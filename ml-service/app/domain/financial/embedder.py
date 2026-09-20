"""
app/domain/financial/embedder.py
─────────────────────────────────
FinBERT-based embedding model for the financial domain.

Model: ``yiyanghkust/finbert-tone``  (768-dim, BERT architecture)

Why FinBERT over a generic sentence-transformer?
  • Pre-trained on 4.9 B financial tokens (Reuters, Bloomberg, SEC filings).
  • Captures domain vocabulary: "EBITDA", "Q3 guidance", "covenant breach",
    "mark-to-market" — terms where generic models produce noisy embeddings.
  • 768-dim output matches the Qdrant collection vector size configured in
    ``config.py``.

Implementation notes:
  • ``sentence-transformers`` is used as the runtime because it handles mean
    pooling, attention-mask weighting, and L2 normalisation correctly.
    Loading via ``SentenceTransformer`` with ``trust_remote_code=False`` is
    safe for this model.
  • Batched encoding with configurable ``batch_size`` prevents OOM on large
    ingestion payloads.
  • A simple in-process LRU cache on single-text encoding avoids redundant
    computation for repeated queries (e.g. semantic cache misses that reach
    the embedder twice).
  • ``encode`` is async-compatible: the blocking ``model.encode()`` call is
    offloaded to the default thread-pool executor so the event loop is
    not blocked during inference.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import lru_cache
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import Domain, DOMAIN_EMBEDDING_MODELS, get_settings
from app.core.observability import get_tracer, span

logger = logging.getLogger(__name__)
tracer = get_tracer()

_MODEL_NAME = DOMAIN_EMBEDDING_MODELS[Domain.FINANCIAL]
_VECTOR_SIZE = 768


class FinancialEmbedder:
    """
    Wraps FinBERT (yiyanghkust/finbert-tone) for the financial domain.

    Implements the ``Embedder`` protocol defined in ``adapter.py``.
    """

    domain = Domain.FINANCIAL

    def __init__(self) -> None:
        settings = get_settings()
        self._batch_size: int = 32
        self._model: SentenceTransformer | None = None
        self._device: str = "cuda" if _cuda_available() else "cpu"
        self._model_name = _MODEL_NAME
        logger.info(
            "FinancialEmbedder initialising",
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

        Single-text requests (query encoding) pass through an LRU cache to
        avoid re-encoding identical queries.  Batch requests (ingestion) bypass
        the cache and encode directly.
        """
        if not texts:
            return []

        with span(
            "financial.embedder.encode",
            domain="financial",
            batch_size=len(texts),
        ):
            if len(texts) == 1:
                return [await self._encode_single_cached(texts[0])]

            loop = asyncio.get_event_loop()
            vectors: np.ndarray = await loop.run_in_executor(
                None, self._encode_batch, texts
            )
            return vectors.tolist()

    # ── Model loading ─────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Blocking model load — called from __init__ inside a thread executor."""
        try:
            self._model = SentenceTransformer(
                self._model_name,
                device=self._device,
                cache_folder=get_settings().llm_model_path.replace(
                    "llama3-8b-instruct.Q4_K_M.gguf", "sentence_transformers"
                ),
            )
            # Warm up with a dummy sentence to trigger JIT compilation
            self._model.encode(["warm-up"], batch_size=1, normalize_embeddings=True)
            logger.info(
                "FinancialEmbedder ready",
                extra={"model": self._model_name, "device": self._device},
            )
        except Exception as exc:
            logger.error(
                "FinancialEmbedder failed to load model",
                extra={"model": self._model_name, "error": str(exc)},
            )
            raise

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _encode_single_cached(self, text: str) -> list[float]:
        """
        Encode a single text with LRU caching keyed on a SHA-256 hash.
        The LRU is scoped to the *instance*, not the class, so it is
        garbage-collected with the embedder.
        """
        cache_key = _text_hash(text)
        cached = self._cache_lookup(cache_key)
        if cached is not None:
            return cached

        loop = asyncio.get_event_loop()
        vector: np.ndarray = await loop.run_in_executor(
            None, self._encode_batch, [text]
        )
        result = vector[0].tolist()
        self._cache_store(cache_key, result)
        return result

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        """
        Synchronous batch encoding.  Runs in a thread-pool executor.

        Args:
            texts: List of raw strings to embed.

        Returns:
            Float32 numpy array of shape (len(texts), vector_size).
        """
        assert self._model is not None, "Model not loaded"
        vectors = self._model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,    # unit-norm for cosine similarity
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return vectors.astype(np.float32)

    # ── Tiny in-process LRU cache ─────────────────────────────────────────────
    # Implemented as two plain dicts (FIFO eviction, 512-entry limit) rather
    # than functools.lru_cache so it works on instance methods.

    _MAX_CACHE = 512

    def __post_init__(self) -> None:
        self._cache: dict[str, list[float]] = {}

    def _cache_lookup(self, key: str) -> list[float] | None:
        if not hasattr(self, "_cache"):
            self._cache = {}
        return self._cache.get(key)

    def _cache_store(self, key: str, value: list[float]) -> None:
        if not hasattr(self, "_cache"):
            self._cache = {}
        if len(self._cache) >= self._MAX_CACHE:
            # Evict oldest entry (insertion-ordered dict, Python 3.7+)
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
    """Return a short SHA-256 hex digest for use as a cache key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
