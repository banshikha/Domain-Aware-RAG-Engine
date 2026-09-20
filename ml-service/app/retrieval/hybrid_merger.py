"""
app/retrieval/hybrid_merger.py
───────────────────────────────
Reciprocal Rank Fusion (RRF) merger for dense and sparse retrieval results.

RRF formula (Cormack et al., 2009):
    score(d) = Σ_r  1 / (k + rank_r(d))

where:
  • d      — a document chunk
  • r      — a retrieval system (dense, sparse, or any additional ranker)
  • rank_r — the 1-based rank of d in system r's result list (∞ if absent)
  • k      — a smoothing constant (default 60, per the original paper)

Why k = 60?
  The original paper shows k = 60 is empirically optimal across a wide range
  of retrieval tasks.  It prevents very high-ranked documents in a single
  system from dominating the fused score, which is exactly the behaviour we
  want: a chunk that ranks #1 dense but does not appear in sparse results
  should not outscore a chunk that ranks #3 in both.

Deduplication:
  Chunks from dense and sparse may overlap (same chunk retrieved by both).
  Deduplication is keyed on ``chunk_id`` (``{document_id}:{chunk_index}``),
  which is stable across both retrievers.  When a chunk appears in both
  result lists its RRF contributions are summed.

Design decisions:
  • Pure in-memory merge — inputs are already small (top-20 from each
    retriever).  No I/O, no async.  The function is synchronous and fast.
  • Accepts any number of ``RetrievalResult`` objects, not just two, so a
    third ranker (e.g. a title-boosted BM25 pass) can be added without
    changing the function signature.
  • Returns a new ``RetrievalResult`` with ``stage="rrf"`` and freshly
    assigned ranks.  The original ``RetrievalResult`` objects are not mutated.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from app.core.models import DocumentChunk, RetrievalResult
from app.core.observability import span

logger = logging.getLogger(__name__)

# Default RRF constant — recommended by Cormack et al., 2009
DEFAULT_RRF_K: int = 60


def reciprocal_rank_fusion(
    *result_lists: RetrievalResult,
    k: int = DEFAULT_RRF_K,
    top_k: int | None = None,
) -> RetrievalResult:
    """
    Fuse an arbitrary number of ``RetrievalResult`` objects using RRF.

    Args:
        *result_lists: One or more retrieval result sets (dense, sparse, …).
                       Each must have ``chunks`` sorted by descending score
                       with ``rank`` assigned (done by the retrievers).
        k:             RRF smoothing constant.  Default 60.
        top_k:         Optional cap on the number of returned chunks.
                       When None, all fused chunks are returned.

    Returns:
        A new ``RetrievalResult`` with ``stage="rrf"`` containing deduplicated,
        RRF-scored, and re-ranked chunks.
    """
    with span("retrieval.rrf_fusion", n_lists=len(result_lists), k=k):
        t0 = time.perf_counter()

        # ── Accumulate RRF scores ─────────────────────────────────────────────
        # chunk_id → accumulated RRF score
        rrf_scores: dict[str, float]        = defaultdict(float)
        # chunk_id → DocumentChunk (last seen copy; all copies are identical)
        chunk_registry: dict[str, DocumentChunk] = {}

        for result in result_lists:
            if not result.chunks:
                continue
            for chunk in result.chunks:
                cid = chunk.chunk_id
                # rank is 1-based; if somehow 0, treat as last position
                rank = chunk.rank if chunk.rank > 0 else len(result.chunks) + 1
                rrf_scores[cid]    += 1.0 / (k + rank)
                chunk_registry[cid] = chunk

        if not chunk_registry:
            return RetrievalResult(stage="rrf", chunks=[], total=0)

        # ── Sort by RRF score descending ──────────────────────────────────────
        sorted_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)

        if top_k is not None:
            sorted_ids = sorted_ids[:top_k]

        # ── Build output chunks with updated score and rank ───────────────────
        fused: list[DocumentChunk] = []
        for rank, cid in enumerate(sorted_ids, start=1):
            chunk = chunk_registry[cid].model_copy()
            chunk.score = round(rrf_scores[cid], 6)
            chunk.rank  = rank
            fused.append(chunk)

        latency_ms = (time.perf_counter() - t0) * 1000

        logger.debug(
            "RRF fusion complete",
            extra={
                "n_input_lists": len(result_lists),
                "n_unique":      len(chunk_registry),
                "n_output":      len(fused),
                "latency_ms":    round(latency_ms, 2),
                "k":             k,
            },
        )

        return RetrievalResult(
            chunks=fused,
            stage="rrf",
            total=len(chunk_registry),
            latency_ms=latency_ms,
        )


# ── Convenience wrapper class (for dependency injection) ──────────────────────

class HybridMerger:
    """
    Thin wrapper around ``reciprocal_rank_fusion`` for FastAPI DI.

    Holds the configured k value so callers don't need to pass it explicitly.

    Usage via dependency injection::

        merger = HybridMerger()
        fused  = merger.merge(dense_result, sparse_result, top_k=10)
    """

    def __init__(self, k: int | None = None) -> None:
        from app.core.config import get_settings
        self._k = k or get_settings().rrf_k

    def merge(
        self,
        *result_lists: RetrievalResult,
        top_k: int | None = None,
    ) -> RetrievalResult:
        """Fuse result lists using the configured k value."""
        return reciprocal_rank_fusion(*result_lists, k=self._k, top_k=top_k)


# ── FastAPI dependency ────────────────────────────────────────────────────────

_hybrid_merger_singleton: HybridMerger | None = None


def get_hybrid_merger() -> HybridMerger:
    """Return the application-scoped HybridMerger singleton."""
    global _hybrid_merger_singleton
    if _hybrid_merger_singleton is None:
        _hybrid_merger_singleton = HybridMerger()
    return _hybrid_merger_singleton
