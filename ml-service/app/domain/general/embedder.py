"""
app/domain/general/embedder.py
───────────────────────────────
all-MiniLM-L6-v2 embedding model for the general domain (Memory Save Mode).

Model: ``sentence-transformers/all-MiniLM-L6-v2``
  • Extremely fast and lightweight (~80MB), ideal for local development.
  • 384-dim output, matching the updated Qdrant general collection vector size.
  • Max sequence length: 256 tokens.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import Domain
from app.core.observability import get_tracer, span

logger = logging.getLogger(__name__)
tracer  = get_tracer()

# ── FORCED LOCAL DEV OVERRIDE ───────────────────────────────────────────────
# Bypassing DOMAIN_EMBEDDING_MODELS[Domain.GENERAL] to force the lightweight model
_MODEL_NAME  = "sentence-transformers/all-MiniLM-L6-v2"
_VECTOR_SIZE = 384


class GeneralEmbedder:
    """
    Wraps ``sentence-transformers/all-MiniLM-L6-v2`` for the general domain.

    Implements the ``Embedder`` protocol defined in ``adapter.py``.
    """

    domain = Domain.GENERAL

    def __init__(self) -> None:
        self._batch_size = 32
        self._model_name = _MODEL_NAME
        self._device     = "cuda" if _cuda_available() else "cpu"
        self._model: SentenceTransformer | None = None
        self._cache: dict[str, list[float]]     = {}
        logger.info(
            "GeneralEmbedder initialising",
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

        Single texts go through the LRU cache (query path).
        Batches bypass the cache and encode directly (ingestion path).
        """
        if not texts:
            return []

        with span("general.embedder.encode", domain="general", batch_size=len(texts)):
            if len(texts) == 1:
                return [await self._encode_single_cached(texts[0])]

            loop    = asyncio.get_event_loop()
            vectors: np.ndarray = await loop.run_in_executor(
                None, self._encode_batch, texts
            )
            return vectors.tolist()

    # ── Model loading ─────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Blocking load — called from __init__; adapter dispatches via executor."""
        try:
            self._model = SentenceTransformer(
                self._model_name,
                device=self._device,
            )
            self._model.encode(
                ["warm-up"],
                batch_size=1,
                normalize_embeddings=True,
            )
            logger.info(
                "GeneralEmbedder ready",
                extra={"model": self._model_name, "device": self._device},
            )
        except Exception as exc:
            logger.error(
                "GeneralEmbedder failed to load",
                extra={"model": self._model_name, "error": str(exc)},
            )
            raise

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _encode_single_cached(self, text: str) -> list[float]:
        key    = _text_hash(text)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        loop    = asyncio.get_event_loop()
        vectors: np.ndarray = await loop.run_in_executor(
            None, self._encode_batch, [text]
        )
        result = vectors[0].tolist()
        self._cache_store(key, result)
        return result

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        """Synchronous batch encode.  Runs in thread-pool executor."""
        assert self._model is not None, "Model not loaded"
        return self._model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype(np.float32)

    # ── Instance-scoped 512-entry FIFO cache ──────────────────────────────────

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