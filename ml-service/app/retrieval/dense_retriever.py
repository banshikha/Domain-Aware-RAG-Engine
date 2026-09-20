"""
app/retrieval/dense_retriever.py
─────────────────────────────────
Dense ANN retrieval using Qdrant's HNSW index.

Responsibilities:
  • Accept a pre-computed query embedding (float list) and an optional
    ``RetrievalFilter``, perform Qdrant ANN search, and return a ranked
    ``list[DocumentChunk]``.
  • Build Qdrant ``Filter`` objects from ``RetrievalFilter`` so callers
    never touch Qdrant models directly.
  • Emit an OpenTelemetry span covering the full Qdrant round-trip so
    latency is visible in Jaeger without any instrumentation in route handlers.

Design decisions:
  • Stateless — no instance state beyond the injected settings and client
    reference.  Safe to share a single instance across all concurrent
    requests (FastAPI ``Depends`` singleton pattern).
  • The ``with_vectors=False`` flag is set on every search call — we store
    the raw text and metadata in the payload; there is no need to retrieve
    the embedding vector back over the wire.
  • ``score_threshold`` is intentionally NOT set here.  Threshold filtering
    belongs in the hybrid merger after RRF fusion, not in individual
    retrievers, because RRF scores are not comparable to raw cosine scores.
"""

from __future__ import annotations

import logging
import time
from typing import Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    SearchRequest,
)

from app.core.config import Domain, get_settings
from app.core.models import DocumentChunk, RetrievalFilter, RetrievalResult
from app.core.observability import get_tracer, span
from app.core.qdrant_client import get_qdrant_client

logger = logging.getLogger(__name__)
tracer = get_tracer()


class DenseRetriever:
    """
    Performs ANN vector search against the per-domain Qdrant collection.

    Usage::

        retriever = DenseRetriever()
        result = await retriever.retrieve(
            domain=Domain.FINANCIAL,
            query_vector=[0.12, -0.34, ...],   # 768-dim
            top_k=20,
            filter=RetrievalFilter(domain="financial", acl="public"),
        )
    """

    def __init__(
        self,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self._settings = get_settings()
        self._client   = client   # injected or resolved lazily via get_qdrant_client()

    # ── Public API ────────────────────────────────────────────────────────────

    async def retrieve(
        self,
        domain:       Domain,
        query_vector: list[float],
        top_k:        int | None = None,
        filter:       RetrievalFilter | None = None,
    ) -> RetrievalResult:
        """
        Run ANN search for ``query_vector`` in the domain's Qdrant collection.

        Args:
            domain:       Target domain — determines which collection to query.
            query_vector: Pre-computed, L2-normalised query embedding.
            top_k:        Number of results to return.  Defaults to
                          ``settings.retrieval_top_k_dense``.
            filter:       Optional payload filter (domain, doc_id, ACL).

        Returns:
            ``RetrievalResult`` with ``stage="dense"`` and ranked chunks.
        """
        top_k      = top_k or self._settings.retrieval_top_k_dense
        collection = self._settings.qdrant_collection_name(domain)
        client     = self._client or get_qdrant_client()
        qdrant_filter = _build_filter(filter) if filter and not filter.is_empty() else None

        with span(
            "retrieval.dense_search",
            domain=domain.value,
            collection=collection,
            top_k=top_k,
        ):
            t0 = time.perf_counter()
            try:
                hits = await client.search(
                    collection_name=collection,
                    query_vector=query_vector,
                    query_filter=qdrant_filter,
                    limit=top_k,
                    with_payload=True,
                    with_vectors=False,     # never pull vectors back over the wire
                )
            except Exception as exc:
                logger.error(
                    "Dense retrieval failed",
                    extra={"domain": domain.value, "error": str(exc)},
                )
                raise

            latency_ms = (time.perf_counter() - t0) * 1000

        chunks = [DocumentChunk.from_qdrant_hit(h) for h in hits]
        _assign_ranks(chunks)

        logger.debug(
            "Dense retrieval complete",
            extra={
                "domain":     domain.value,
                "n_hits":     len(chunks),
                "latency_ms": round(latency_ms, 2),
            },
        )

        return RetrievalResult(
            chunks=chunks,
            stage="dense",
            total=len(chunks),
            latency_ms=latency_ms,
        )

    async def retrieve_batch(
        self,
        domain:        Domain,
        query_vectors: list[list[float]],
        top_k:         int | None = None,
        filter:        RetrievalFilter | None = None,
    ) -> list[RetrievalResult]:
        """
        Run ANN search for multiple query vectors concurrently.

        Used by the ingestion pipeline to find near-duplicate chunks before
        upserting, and by evaluation scripts.
        """
        import asyncio
        tasks = [
            self.retrieve(domain, qv, top_k=top_k, filter=filter)
            for qv in query_vectors
        ]
        return await asyncio.gather(*tasks)


# ── Qdrant filter builder ─────────────────────────────────────────────────────

def _build_filter(f: RetrievalFilter) -> Filter:
    """
    Convert a ``RetrievalFilter`` to a Qdrant ``Filter`` (must-match AND logic).

    ACL is handled as MatchAny when a list of roles is provided, or MatchValue
    for a single role string — allows callers to pass user roles directly.
    """
    conditions = []

    if f.domain:
        conditions.append(
            FieldCondition(key="domain", match=MatchValue(value=f.domain))
        )

    if f.document_id:
        conditions.append(
            FieldCondition(key="document_id", match=MatchValue(value=f.document_id))
        )

    if f.acl:
        if isinstance(f.acl, list):
            conditions.append(
                FieldCondition(key="acl", match=MatchAny(any=f.acl))
            )
        else:
            conditions.append(
                FieldCondition(key="acl", match=MatchValue(value=f.acl))
            )

    if f.chunk_types:
        conditions.append(
            FieldCondition(key="chunk_type", match=MatchAny(any=f.chunk_types))
        )

    return Filter(must=conditions)


def _assign_ranks(chunks: list[DocumentChunk]) -> None:
    """Set 1-based rank on each chunk in-place (already sorted by score desc)."""
    for i, chunk in enumerate(chunks, start=1):
        chunk.rank = i


# ── FastAPI dependency ────────────────────────────────────────────────────────

_dense_retriever_singleton: DenseRetriever | None = None


def get_dense_retriever() -> DenseRetriever:
    """Return the application-scoped DenseRetriever singleton."""
    global _dense_retriever_singleton
    if _dense_retriever_singleton is None:
        _dense_retriever_singleton = DenseRetriever()
    return _dense_retriever_singleton
