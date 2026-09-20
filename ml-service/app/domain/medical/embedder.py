"""
app/domain/medical/embedder.py
───────────────────────────────
BioMedBERT-based embedding model for the medical domain.

Model: ``pritamdeka/S-PubMedBert-MS-MARCO``
  • Built on PubMedBERT — pre-trained exclusively on PubMed abstracts and
    full-text biomedical articles (3.1 B tokens, zero out-of-domain data).
  • Fine-tuned with MS-MARCO passage ranking via sentence-transformers, so it
    understands question-to-passage relevance in addition to semantic similarity.
  • 768-dim output — matches the Qdrant medical collection vector size.
  • Outperforms general-purpose models on BEIR BioASQ, MedQA, and NFCorpus
    benchmarks for biomedical retrieval.

Why not ``microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext``?
  The Microsoft model is a masked-LM base, not a sentence encoder.  Using it
  directly via sentence-transformers would apply generic mean-pooling without
  the bi-encoder fine-tuning needed for retrieval quality.
  ``pritamdeka/S-PubMedBert-MS-MARCO`` IS that fine-tuned retrieval model —
  it wraps the same PubMedBERT backbone with proper sentence-embedding training.
  The config.py entry for MEDICAL is updated by comment; the model string here
  is the authoritative source.

Implementation notes (mirrors financial/embedder.py):
  • Blocking ``SentenceTransformer`` load is called from ``__init__`` — the
    adapter dispatches ``_build_embedder()`` inside ``run_in_executor`` so the
    event loop is never stalled.
  • Same instance-scoped LRU cache (512 entries, SHA-256 key) for single-text
    query encoding.
  • ``normalize_embeddings=True`` → unit-norm vectors for cosine similarity.
  • Batch path (ingestion) bypasses cache to prevent memory bloat.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import Domain, get_settings
from app.core.observability import get_tracer, span

logger = logging.getLogger(__name__)
tracer = get_tracer()

# Authoritative model for the medical domain.
# config.py DOMAIN_EMBEDDING_MODELS[Domain.MEDICAL] is used as the fallback
# reference; this constant takes precedence within this module.
_MODEL_NAME  = "pritamdeka/S-PubMedBert-MS-MARCO"
_VECTOR_SIZE = 768


class MedicalEmbedder:
    """
    Wraps ``pritamdeka/S-PubMedBert-MS-MARCO`` for the medical domain.

    Implements the ``Embedder`` protocol defined in ``adapter.py``.
    """

    domain = Domain.MEDICAL

    def __init__(self) -> None:
        settings = get_settings()
        self._batch_size  = 32
        self._model_name  = _MODEL_NAME
        self._device      = "cuda" if _cuda_available() else "cpu"
        self._model: SentenceTransformer | None = None
        self._cache: dict[str, list[float]]     = {}
        logger.info(
            "MedicalEmbedder initialising",
            extra={"model": self._model_name, "device": self._device},
        )
        self._load_model()

    # ── Protocol: vector_size ─────────────────────────────────────────────────

    @property
    def vector_size(self) -> int:
        return _VECTOR_SIZE

    # ── Protocol: encode ──────────────────────────────────────────────────────

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts asynchronously.

        Single-text requests (query-time) pass through the LRU cache.
        Batch requests (ingestion) encode directly in the thread pool.
        """
        if not texts:
            return []

        with span(
            "medical.embedder.encode",
            domain="medical",
            batch_size=len(texts),
        ):
            if len(texts) == 1:
                return [await self._encode_single_cached(texts[0])]

            loop    = asyncio.get_event_loop()
            vectors: np.ndarray = await loop.run_in_executor(
                None, self._encode_batch, texts
            )
            return vectors.tolist()

    # ── Model loading ─────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Blocking model load — safe because adapter calls this via executor."""
        try:
            self._model = SentenceTransformer(
                self._model_name,
                device=self._device,
            )
            # Warm-up pass triggers any JIT / CUDA kernel compilation
            self._model.encode(
                ["warm-up biomedical query"],
                batch_size=1,
                normalize_embeddings=True,
            )
            logger.info(
                "MedicalEmbedder ready",
                extra={"model": self._model_name, "device": self._device},
            )
        except Exception as exc:
            logger.error(
                "MedicalEmbedder failed to load model",
                extra={"model": self._model_name, "error": str(exc)},
            )
            raise

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _encode_single_cached(self, text: str) -> list[float]:
        key    = _text_hash(text)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        loop   = asyncio.get_event_loop()
        vectors: np.ndarray = await loop.run_in_executor(
            None, self._encode_batch, [text]
        )
        result = vectors[0].tolist()
        self._cache_store(key, result)
        return result

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        """
        Synchronous batch encode.  Runs in thread-pool executor.

        Returns float32 array of shape (len(texts), 768).
        """
        assert self._model is not None, "Model not loaded"
        return self._model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype(np.float32)

    # ── Instance-scoped LRU cache (512-entry FIFO) ────────────────────────────

    _MAX_CACHE = 512

    def _cache_store(self, key: str, value: list[float]) -> None:
        if len(self._cache) >= self._MAX_CACHE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = value


# ── Utility helpers ───────────────────────────────────────────────────────────

def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
