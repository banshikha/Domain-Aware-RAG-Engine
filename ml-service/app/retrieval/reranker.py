"""
app/retrieval/reranker.py
──────────────────────────
Cross-encoder reranker using an INT8 Quantized ONNX model.

Role in the pipeline:
  Dense + sparse retrieval recall broad candidate sets (top-20 each → ~40
  after deduplication).  Bi-encoders encode query and document independently,
  losing fine-grained token-level interaction.  The cross-encoder sees the
  full (query, passage) pair concatenated, enabling much more accurate
  relevance scoring at the cost of higher latency.

Model: ``BAAI/bge-reranker-base`` (INT8 ONNX)
  • Converted and dynamically quantized to 8-bit integers.
  • Executes via `onnxruntime` using the CPUExecutionProvider.
  • Drastically reduces memory footprint (~270MB) and CPU latency compared 
    to standard PyTorch `sentence_transformers`.

Async safety:
  Model loading and tokenization/inference are blocking operations. The same
  ``asyncio.Lock + run_in_executor`` pattern used by the embedders is applied
  here so the event loop is never stalled during inference or cold start.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.config import get_settings
from app.core.models import DocumentChunk, RetrievalResult
from app.core.observability import get_tracer, span

logger = logging.getLogger(__name__)
tracer = get_tracer()


# ══════════════════════════════════════════════════════════════════════════════
# Cross-encoder model wrapper  (ONNX, lazy singleton, async-safe)
# ══════════════════════════════════════════════════════════════════════════════

class _ONNXRerankerModel:
    """
    Wraps the ONNX runtime session with async-safe lazy loading and batching.
    """

    def __init__(self, model_dir: str = "./models/bge-reranker-int8") -> None:
        self._model_dir = model_dir
        self._session: Any = None
        self._tokenizer: Any = None
        self._lock  = asyncio.Lock()
        self._ready = False

    async def ensure_loaded(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            with span("reranker.load_onnx", model=self._model_dir):
                logger.info(
                    "Loading INT8 ONNX cross-encoder",
                    extra={"model_dir": self._model_dir},
                )
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_blocking)
                self._ready = True
                logger.info("ONNX Cross-encoder ready")

    def _load_blocking(self) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer
        
        # Load tokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_dir)
        
        # Load quantized ONNX model enforcing CPU execution
        self._session = ort.InferenceSession(
            f"{self._model_dir}/model_quantized.onnx",
            providers=["CPUExecutionProvider"]
        )
        
        # Warm-up: score one dummy pair to trigger JIT / kernel init
        self._predict_blocking([("warm-up query", "warm-up passage")])
        logger.debug("ONNX Cross-encoder warm-up complete")

    async def predict_batch(self, pairs: list[tuple[str, str]]) -> list[float]:
        """
        Score a batch of (query, passage) pairs asynchronously.
        Returns a list of float logits in the same order as ``pairs``.
        """
        await self.ensure_loaded()
        with span("reranker.predict_batch", n_pairs=len(pairs)):
            loop   = asyncio.get_event_loop()
            scores = await loop.run_in_executor(None, self._predict_blocking, pairs)
        return scores

    def _predict_blocking(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Synchronous ONNX batch inference. Runs in thread-pool executor."""
        import numpy as np
        
        # 1. Tokenize pairs
        inputs = self._tokenizer(
            pairs,
            padding=True,
            truncation=True,
            return_tensors="np",  # Native NumPy arrays for ONNX
            max_length=512
        )

        # 2. Prepare ONNX input dictionary
        onnx_inputs = {
            "input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64),
        }
        if "token_type_ids" in inputs:
            onnx_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

        # 3. Execute inference
        logits = self._session.run(None, onnx_inputs)[0]
        
        # 4. Extract scalar floats
        return [float(s) for s in logits.flatten()]


# Module-level singleton pointing to the ONNX directory we just generated
_cross_encoder = _ONNXRerankerModel("./models/bge-reranker-int8")


def get_cross_encoder() -> _ONNXRerankerModel:
    """Return the shared cross-encoder model instance."""
    return _cross_encoder


# ══════════════════════════════════════════════════════════════════════════════
# Reranker
# ══════════════════════════════════════════════════════════════════════════════

class Reranker:
    """
    Re-ranks a list of ``DocumentChunk`` objects using the ONNX cross-encoder.
    """

    def __init__(self, model: _ONNXRerankerModel | None = None) -> None:
        self._settings = get_settings()
        self._model    = model or _cross_encoder

    # ── Public API ────────────────────────────────────────────────────────────

    async def rerank(
        self,
        query:  str,
        result: RetrievalResult,
        top_k:  int | None = None,
    ) -> RetrievalResult:
        """
        Re-rank chunks in ``result`` using the cross-encoder.
        """
        top_k = top_k or self._settings.retrieval_top_k_rerank

        if not result.chunks:
            return RetrievalResult(stage="reranked", chunks=[], total=0)

        with span(
            "retrieval.rerank",
            n_candidates=len(result.chunks),
            top_k=top_k,
        ):
            t0 = time.perf_counter()

            # Build (query, passage) pairs for batch inference
            pairs: list[tuple[str, str]] = [
                (query, chunk.text) for chunk in result.chunks
            ]

            scores = await self._model.predict_batch(pairs)

            # Pair scores with chunks and sort descending
            scored: list[tuple[float, DocumentChunk]] = list(
                zip(scores, result.chunks)
            )
            scored.sort(key=lambda x: x[0], reverse=True)

            # Slice to top_k and assign updated score + rank
            top_scored = scored[:top_k]
            reranked: list[DocumentChunk] = []
            for rank, (score, chunk) in enumerate(top_scored, start=1):
                updated       = chunk.model_copy()
                updated.score = round(score, 6)
                updated.rank  = rank
                reranked.append(updated)

            latency_ms = (time.perf_counter() - t0) * 1000

        logger.debug(
            "Reranking complete",
            extra={
                "n_input":    len(result.chunks),
                "n_output":   len(reranked),
                "latency_ms": round(latency_ms, 2),
            },
        )

        return RetrievalResult(
            chunks=reranked,
            stage="reranked",
            total=len(result.chunks),
            latency_ms=latency_ms,
        )

    async def warm_up(self) -> None:
        """Pre-load the cross-encoder model.  Call during FastAPI lifespan."""
        await self._model.ensure_loaded()


# ── FastAPI dependency ────────────────────────────────────────────────────────

_reranker_singleton: Reranker | None = None


def get_reranker() -> Reranker:
    """Return the application-scoped Reranker singleton."""
    global _reranker_singleton
    if _reranker_singleton is None:
        _reranker_singleton = Reranker()
    return _reranker_singleton