"""
app/ingestion/processor.py
───────────────────────────
Document ingestion pipeline: parse → chunk → embed (dense + sparse) → upsert.

Pipeline stages:
  1. Parse   — PyMuPDF extracts text instantly, avoiding OCR freezes.
  2. Chunk   — Domain-specific chunker splits elements into retrieval-sized pieces.
  3. Embed   — Dense embedding via domain embedder (e.g., all-MiniLM-L6-v2).
  4. Sparse  — Sparse embedding via SPLADE (Bypassed for large docs on CPU).
  5. Upsert  — Batch upsert to the domain-specific Qdrant collection with:
                 • dense vector  (field: default unnamed vector)
                 • sparse vector (field: "sparse" — must match SPARSE_VECTOR_FIELD)
                 • full payload metadata

Idempotency:
  Each chunk is assigned a deterministic UUID v5 ``chunk_id`` derived from
  ``(document_id, chunk_index)``.  Qdrant's upsert semantics overwrite existing
  points with the same ID, so re-ingesting the same document is safe.

Batching:
  Embedding and upsert are performed in configurable batches (default 32) to
  avoid OOM on large documents and to keep Qdrant round-trips efficient.

S3 archival:
  After successful upsert the original file is archived to S3.  This is a
  best-effort fire-and-forget step; ingestion is not rolled back on S3 failure.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Domain, get_settings
from app.core.models import DocumentChunk
from app.core.observability import get_tracer, span
from app.core.qdrant_client import get_qdrant_client
from app.domain.adapter import get_domain_adapter
from app.retrieval.sparse_retriever import SPARSE_VECTOR_FIELD, get_splade_encoder

logger = logging.getLogger(__name__)
tracer = get_tracer()

# Qdrant batch size for upsert calls
_UPSERT_BATCH = 32

# Unstructured.io element types mapped to chunk-friendly plain text
_SKIP_TYPES = {"PageBreak", "Image", "FigureCaption"}


# ══════════════════════════════════════════════════════════════════════════════
# Result dataclass
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class IngestionResult:
    document_id:    str
    domain:         str
    total_chunks:   int   = 0
    upserted:       int   = 0
    failed:         int   = 0
    duration_s:     float = 0.0
    s3_archived:    bool  = False
    error:          str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# Ingestion Processor
# ══════════════════════════════════════════════════════════════════════════════

class IngestionProcessor:
    """
    Orchestrates the full document ingestion pipeline.
    """

    def __init__(self) -> None:
        self._settings      = get_settings()
        self._domain_adapter = get_domain_adapter()
        self._splade        = get_splade_encoder()

    # ── Public API ────────────────────────────────────────────────────────────

    async def process(
        self,
        file_path:   str,
        document_id: str,
        domain:      Domain,
        acl:         str = "public",
        metadata:    dict | None = None,
    ) -> IngestionResult:
        t_start = time.perf_counter()
        result  = IngestionResult(document_id=document_id, domain=domain.value)
        base_meta = {
            "document_id": document_id,
            "domain":      domain.value,
            "acl":         acl,
            "source":      Path(file_path).name,
            **(metadata or {}),
        }

        with span(
            "ingestion.process",
            document_id=document_id,
            domain=domain.value,
        ):
            try:
                # ── Stage 1: Parse ────────────────────────────────────────────
                raw_content = await self._parse(file_path)

                # ── Stage 2: Chunk ────────────────────────────────────────────
                chunker  = await self._domain_adapter.get_chunker(domain)
                raw_chunks: list[dict] = chunker.chunk(raw_content, base_meta)
                
                # 🚀 SAFETY CAP: Prevent Chunk Explosion on CPU
                MAX_CHUNKS = 100
                if len(raw_chunks) > MAX_CHUNKS:
                    logger.warning(
                        "Chunk explosion detected — truncating to save CPU",
                        extra={"document_id": document_id, "original": len(raw_chunks)}
                    )
                    raw_chunks = raw_chunks[:MAX_CHUNKS]

                result.total_chunks = len(raw_chunks)

                if not raw_chunks:
                    logger.warning(
                        "Ingestion produced zero chunks",
                        extra={"document_id": document_id, "domain": domain.value},
                    )
                    result.duration_s = time.perf_counter() - t_start
                    return result

                # Assign deterministic chunk_id to every chunk
                _assign_chunk_ids(raw_chunks, document_id)

                # ── Stages 3–5: Embed + Upsert in batches ─────────────────────
                embedder = await self._domain_adapter.get_embedder(domain)
                collection = self._settings.qdrant_collection_name(domain)
                client     = get_qdrant_client()

                n_batches = math.ceil(len(raw_chunks) / _UPSERT_BATCH)
                for batch_idx in range(n_batches):
                    batch = raw_chunks[
                        batch_idx * _UPSERT_BATCH :
                        (batch_idx + 1) * _UPSERT_BATCH
                    ]
                    texts = [c["text"] for c in batch]

                    # Dense embeddings
                    with span("ingestion.embed_dense", batch=batch_idx):
                        dense_vectors = await embedder.encode(texts)

                    # 🚀 SPLADE CPU BYPASS: Skip sparse encoding if document is too large
                    with span("ingestion.embed_sparse", batch=batch_idx):
                        if result.total_chunks <= 30:
                            sparse_vectors = await self._encode_sparse_batch(texts)
                        else:
                            from qdrant_client.models import SparseVector
                            # Document is too big. Generate empty sparse vectors to fall back to Pure Dense.
                            sparse_vectors = [SparseVector(indices=[], values=[]) for _ in texts]

                    # Build Qdrant PointStruct list
                    points = _build_points(batch, dense_vectors, sparse_vectors)

                    # Upsert
                    with span("ingestion.upsert", batch=batch_idx, n=len(points)):
                        await client.upsert(
                            collection_name=collection,
                            points=points,
                            wait=True,   # wait=True ensures durability before ack
                        )

                    result.upserted += len(points)
                    logger.debug(
                        "Upserted batch",
                        extra={
                            "document_id": document_id,
                            "batch":       batch_idx + 1,
                            "of":          n_batches,
                            "upserted":    result.upserted,
                        },
                    )

                # ── Stage 6: S3 archival (best-effort) ────────────────────────
                result.s3_archived = await self._archive_to_s3(
                    file_path, document_id, domain
                )

            except Exception as exc:
                logger.error(
                    "Ingestion failed",
                    extra={
                        "document_id": document_id,
                        "domain":      domain.value,
                        "error":       str(exc),
                    },
                    exc_info=True,
                )
                result.error  = str(exc)
                result.failed = result.total_chunks - result.upserted
            finally:
                # Always clean up the temp file
                _safe_remove(file_path)

        result.duration_s = time.perf_counter() - t_start
        logger.info(
            "Ingestion complete",
            extra={
                "document_id":   document_id,
                "domain":        domain.value,
                "total_chunks":  result.total_chunks,
                "upserted":      result.upserted,
                "duration_s":    round(result.duration_s, 2),
                "s3_archived":   result.s3_archived,
            },
        )
        return result

    # ── Parse stage ───────────────────────────────────────────────────────────

    async def _parse(self, file_path: str) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._parse_blocking, file_path)

    @staticmethod
    def _parse_blocking(file_path: str) -> str:
        """Blocking parse. Uses PyMuPDF for lightning-fast PDF extraction."""
        suffix = Path(file_path).suffix.lower()

        # 🚀 THE CRASH FIX: Lightning-fast PDF parsing
        if suffix == ".pdf":
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(file_path)
                text = "\n\n".join([page.get_text("text") for page in doc])
                return text
            except ImportError:
                logger.error("PyMuPDF (fitz) not installed. Run `pip install pymupdf`")
                return ""
            except Exception as e:
                logger.error(f"PDF Parsing failed: {str(e)}")
                return ""

        # Plain text
        if suffix in (".txt", ".md"):
            return Path(file_path).read_text(encoding="utf-8", errors="replace")
            
        # Fallback for docx, HTML, etc., bypassing unstructured
        try:
            return Path(file_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    # ── Sparse encoding ───────────────────────────────────────────────────────

    async def _encode_sparse_batch(self, texts: list[str]):
        import asyncio
        # Encode concurrently but cap parallelism to avoid OOM on large batches
        sem = asyncio.Semaphore(4)

        async def _encode_one(text: str):
            async with sem:
                return await self._splade.encode(text)

        return await asyncio.gather(*[_encode_one(t) for t in texts])

    # ── S3 archival ───────────────────────────────────────────────────────────

    async def _archive_to_s3(
        self, file_path: str, document_id: str, domain: Domain
    ) -> bool:
        if not self._settings.aws_access_key_id:
            # No AWS credentials configured — skip silently in local dev
            return False
        try:
            import asyncio, boto3  # noqa: E401
            loop   = asyncio.get_event_loop()
            key    = f"{domain.value}/{document_id}/{Path(file_path).name}"
            await loop.run_in_executor(
                None,
                lambda: boto3.client(
                    "s3",
                    region_name          = self._settings.s3_region,
                    aws_access_key_id    = self._settings.aws_access_key_id,
                    aws_secret_access_key= self._settings.aws_secret_access_key,
                ).upload_file(file_path, self._settings.s3_bucket, key),
            )
            logger.info(
                "Archived to S3",
                extra={"bucket": self._settings.s3_bucket, "key": key},
            )
            return True
        except Exception as exc:
            logger.warning(
                "S3 archival failed (non-fatal)",
                extra={"document_id": document_id, "error": str(exc)},
            )
            return False


# ══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ══════════════════════════════════════════════════════════════════════════════

def _assign_chunk_ids(chunks: list[dict], document_id: str) -> None:
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # URL namespace
    for chunk in chunks:
        idx      = chunk.get("chunk_index", 0)
        seed     = f"{document_id}:{idx}"
        chunk_id = str(uuid.uuid5(namespace, seed))
        chunk["chunk_id"]            = chunk_id
        chunk["metadata"]["chunk_id"] = chunk_id


def _build_points(
    chunks:          list[dict],
    dense_vectors:   list[list[float]],
    sparse_vectors:  list[Any],
) -> list[Any]:
    from qdrant_client.models import PointStruct, SparseVector

    points = []
    for chunk, dense, sparse_sv in zip(chunks, dense_vectors, sparse_vectors):
        # Payload: everything except the raw text (stored separately)
        payload = {
            "text":        chunk["text"],
            "chunk_id":    chunk["chunk_id"],
            "chunk_type":  chunk.get("chunk_type", "narrative"),
            "chunk_index": chunk.get("chunk_index", 0),
            "section":     chunk.get("section"),
            "page_number": chunk.get("page_number"),
            "table_id":    chunk.get("table_id"),
            "row_range":   chunk.get("row_range"),
            "entities":    chunk.get("entities", []),
            **chunk.get("metadata", {}),
        }

        vectors = {
            "":                   dense,
            SPARSE_VECTOR_FIELD:  SparseVector(
                indices=sparse_sv.indices,
                values=sparse_sv.values,
            ),
        }

        points.append(
            PointStruct(
                id      = chunk["chunk_id"],
                vector  = vectors,
                payload = payload,
            )
        )
    return points


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError as exc:
        logger.debug(
            "Could not remove temp file",
            extra={"path": path, "error": str(exc)},
        )