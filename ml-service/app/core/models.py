"""
app/core/models.py
───────────────────
Shared Pydantic models that form the data contract between every layer of
the ML service: retrieval → reranking → LLM orchestration → API response.

Design principles:
  • All inter-module data exchange uses these models — no raw dicts crossing
    module boundaries after the retrieval layer.
  • ``DocumentChunk`` is the single canonical type for a retrieved piece of
    text throughout the entire pipeline.
  • Models are intentionally lean: they carry only what every consumer needs.
    Domain-specific metadata lives inside the opaque ``metadata`` dict and
    is accessed by key, keeping the core model stable as domains evolve.
  • ``model_config = ConfigDict(frozen=False)`` — scores are mutated in-place
    by the reranker to avoid unnecessary object copies in the hot path.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ══════════════════════════════════════════════════════════════════════════════
# Core retrieval type
# ══════════════════════════════════════════════════════════════════════════════

class DocumentChunk(BaseModel):
    """
    A single retrieved document chunk, normalised from any retrieval backend.

    Produced by: DenseRetriever, SparseRetriever
    Consumed by: HybridMerger, Reranker, LLM Orchestrator, API response layer

    Fields:
        chunk_id    — Stable unique identifier for deduplication in RRF.
                      Set to ``{document_id}:{chunk_index}`` by retrievers.
                      Falls back to a hash of ``text`` when not available.
        text        — Raw chunk content sent to the embedder and the LLM.
        score       — Retrieval score (cosine similarity, BM25, RRF, or
                      cross-encoder logit depending on pipeline stage).
                      Mutated in-place by the reranker.
        metadata    — Opaque dict carrying all payload fields stored in Qdrant:
                      document_id, domain, acl, chunk_type, section,
                      page_number, source, filing_date, entities, etc.
        chunk_type  — Convenience accessor mirroring metadata["chunk_type"].
                      Kept top-level because routing logic (e.g. table hint
                      injection in prompt templates) reads it frequently.
        rank        — Position in the ranked list after a retrieval or
                      reranking step.  Set by HybridMerger / Reranker.
    """

    model_config = ConfigDict(frozen=False)   # score/rank are mutated by reranker

    chunk_id:   str   = Field(...,   description="Stable deduplication key")
    text:       str   = Field(...,   description="Chunk content")
    score:      float = Field(0.0,   description="Retrieval or rerank score")
    metadata:   dict[str, Any] = Field(default_factory=dict)
    chunk_type: str   = Field("narrative", description="table|narrative|header|…")
    rank:       int   = Field(0,     description="1-based rank within result list")

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def document_id(self) -> str:
        return self.metadata.get("document_id", "")

    @property
    def domain(self) -> str:
        return self.metadata.get("domain", "general")

    @property
    def acl(self) -> str:
        return self.metadata.get("acl", "public")

    @property
    def section(self) -> str | None:
        return self.metadata.get("section")

    # ── Factory helpers ───────────────────────────────────────────────────────

    @classmethod
    def from_qdrant_hit(cls, hit: Any) -> "DocumentChunk":
        """
        Construct a DocumentChunk from a Qdrant ``ScoredPoint``.

        Handles both the REST model (``hit.payload``) and the gRPC model
        (``hit.payload`` is the same dict in qdrant-client ≥ 1.9).
        """
        payload    = hit.payload or {}
        chunk_idx  = payload.get("chunk_index", 0)
        doc_id     = payload.get("document_id", "")
        chunk_id   = payload.get("chunk_id") or f"{doc_id}:{chunk_idx}"
        chunk_type = payload.get("chunk_type", "narrative")

        return cls(
            chunk_id   = chunk_id,
            text       = payload.get("text", ""),
            score      = float(hit.score),
            metadata   = payload,
            chunk_type = chunk_type,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Retrieval request / response wrappers
# ══════════════════════════════════════════════════════════════════════════════

class RetrievalFilter(BaseModel):
    """
    Optional payload filter applied by both dense and sparse retrievers.

    All fields are optional — only non-None values are added to the Qdrant
    filter condition.
    """
    domain:      str | None        = None
    document_id: str | None        = None
    acl:         str | list[str] | None = None
    chunk_types: list[str] | None  = None   # restrict to e.g. ["table"]

    def is_empty(self) -> bool:
        return all(
            v is None for v in (self.domain, self.document_id, self.acl, self.chunk_types)
        )


class RetrievalResult(BaseModel):
    """Wraps a ranked list of DocumentChunk objects with pipeline provenance."""

    chunks:   list[DocumentChunk] = Field(default_factory=list)
    stage:    str                  = "retrieval"   # dense|sparse|rrf|reranked
    total:    int                  = 0             # total hits before top-k trim
    latency_ms: float              = 0.0

    model_config = ConfigDict(frozen=False)

    def __len__(self) -> int:
        return len(self.chunks)

    def __iter__(self):
        return iter(self.chunks)
