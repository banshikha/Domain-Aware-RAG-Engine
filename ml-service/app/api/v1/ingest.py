"""
app/api/v1/ingest.py
─────────────────────
POST /ingest — non-blocking document ingestion endpoint.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from fastapi import File, Form
from pydantic import BaseModel

from app.core.config import Domain, get_settings
from app.core.observability import get_tracer, span
from app.ingestion.processor import IngestionProcessor, IngestionResult

logger = logging.getLogger(__name__)
tracer  = get_tracer()

router  = APIRouter(prefix="/v1", tags=["ingest"])

# Allowed upload extensions
_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".html", ".htm", ".txt", ".md"}

# Redis key pattern for task status
_TASK_KEY = "ingest:{task_id}"
_TASK_TTL = 86_400   # 24 hours


# ── Response models ───────────────────────────────────────────────────────────

class IngestAcceptedResponse(BaseModel):
    task_id:     str
    document_id: str
    status:      str = "processing"
    message:     str = "Document ingestion started."


class IngestStatusResponse(BaseModel):
    task_id:     str
    document_id: str
    status:      str          # processing | completed | failed
    domain:      str
    total_chunks: int  = 0
    upserted:    int   = 0
    failed:      int   = 0
    duration_s:  float = 0.0
    s3_archived: bool  = False
    error:       str | None = None


# ── Redis helpers ─────────────────────────────────────────────────────────────

async def _get_redis() -> aioredis.Redis:
    settings = get_settings()
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _set_task_status(task_id: str, data: dict) -> None:
    try:
        r = await _get_redis()
        await r.setex(
            _TASK_KEY.format(task_id=task_id),
            _TASK_TTL,
            json.dumps(data),
        )
        await r.aclose()
    except Exception as exc:
        logger.warning(
            "Could not write task status to Redis",
            extra={"task_id": task_id, "error": str(exc)},
        )


async def _get_task_status(task_id: str) -> dict | None:
    try:
        r   = await _get_redis()
        raw = await r.get(_TASK_KEY.format(task_id=task_id))
        await r.aclose()
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning(
            "Could not read task status from Redis",
            extra={"task_id": task_id, "error": str(exc)},
        )
        return None


# ── Background ingestion task ─────────────────────────────────────────────────

async def _run_ingestion(
    task_id:     str,
    file_path:   str,
    document_id: str,
    domain:      Domain,
    acl:         str,
    extra_meta:  dict,
) -> None:
    # Mark as processing in Redis immediately
    await _set_task_status(task_id, {
        "task_id":     task_id,
        "document_id": document_id,
        "domain":      domain.value,
        "status":      "processing",
    })

    with span(
        "ingest.background_task",
        task_id=task_id,
        document_id=document_id,
        domain=domain.value,
    ):
        processor = IngestionProcessor()
        result: IngestionResult = await processor.process(
            file_path   = file_path,
            document_id = document_id,
            domain      = domain,
            acl         = acl,
            metadata    = extra_meta,
        )

    final_status = "completed" if result.error is None else "failed"
    await _set_task_status(task_id, {
        "task_id":     task_id,
        "document_id": document_id,
        "domain":      domain.value,
        "status":      final_status,
        "total_chunks": result.total_chunks,
        "upserted":    result.upserted,
        "failed":      result.failed,
        "duration_s":  round(result.duration_s, 3),
        "s3_archived": result.s3_archived,
        "error":       result.error,
    })

    logger.info(
        "Background ingestion finished",
        extra={
            "task_id":     task_id,
            "document_id": document_id,
            "status":      final_status,
            "upserted":    result.upserted,
        },
    )


# ── POST /ingest ──────────────────────────────────────────────────────────────

@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestAcceptedResponse,
    summary="Ingest a document (non-blocking)",
)
async def ingest(
    background_tasks: BackgroundTasks,
    # 🚀 FIX 1: Removed aliases so it strictly matches the React formData payload
    file:        UploadFile = File(..., description="Document to ingest"),
    domain:      str        = Form(default="general"),
    acl:         str        = Form(default="public"),
    document_id: str | None = Form(default=None),
    source:      str | None = Form(default=None),
    x_domain:    str | None = Header(default=None, alias="X-Domain"),
) -> IngestAcceptedResponse:
    """
    Upload a document for ingestion into the RAG vector store.
    """
    settings = get_settings()

    # 🚀 FIX 2: Explicitly prioritize the Form field over any proxy headers.
    # If the user selected 'financial', we ignore the default 'general' header entirely.
    raw_domain = domain if domain != "general" else (x_domain or "general")
    
    try:
        resolved_domain = Domain(raw_domain.lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid domain: {raw_domain}. Must be one of {[d.value for d in Domain]}",
        )

    # ── Validate extension ────────────────────────────────────────────────────
    filename  = file.filename or "upload"
    ext       = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {ext}. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    # ── Validate file size ────────────────────────────────────────────────────
    file_bytes = await file.read()
    size_mb    = len(file_bytes) / (1024 * 1024)
    if size_mb > settings.max_upload_size_mb:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size {size_mb:.1f} MB exceeds limit of {settings.max_upload_size_mb} MB.",
        )

    # ── Save to persistent temp path ──────────────────────────────────────────
    task_id     = str(uuid.uuid4())
    doc_id      = document_id or str(uuid.uuid4())
    upload_dir  = Path(tempfile.gettempdir()) / "rag_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path  = str(upload_dir / f"{task_id}{ext}")

    try:
        with open(saved_path, "wb") as fh:
            fh.write(file_bytes)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {exc}",
        )

    # ── Schedule background ingestion ─────────────────────────────────────────
    extra_meta = {
        "source":            source or filename,
        "original_filename": filename,
        "upload_size_mb":    round(size_mb, 3),
    }

    background_tasks.add_task(
        _run_ingestion,
        task_id     = task_id,
        file_path   = saved_path,
        document_id = doc_id,
        domain      = resolved_domain,
        acl         = acl,
        extra_meta  = extra_meta,
    )

    logger.info(
        "Ingest task queued",
        extra={
            "task_id":     task_id,
            "document_id": doc_id,
            "domain":      resolved_domain.value,
            "file":        filename,
            "size_mb":     round(size_mb, 3),
        },
    )

    return IngestAcceptedResponse(
        task_id     = task_id,
        document_id = doc_id,
    )


# ── GET /ingest/{task_id} — status polling ────────────────────────────────────

@router.get(
    "/ingest/{task_id}",
    response_model=IngestStatusResponse,
    summary="Poll ingestion task status",
)
async def ingest_status(task_id: str) -> IngestStatusResponse:
    data = await _get_task_status(task_id)

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id!r} not found or has expired.",
        )

    return IngestStatusResponse(**data)