"""
app/api/v1/query.py
────────────────────
POST /query — streaming RAG query endpoint.

SSE wire format (must match Node.js gateway expectation exactly):

  Event 1 — metadata (emitted before first token):
    data: {"type":"metadata","request_id":"...","domain":"...","sources":[...],"chunks_used":N,"chunks_dropped":N}\n\n

  Events 2..N — token stream:
    data: {"type":"token","content":"<token_text>"}\n\n

  Final event:
    data: {"type":"done"}\n\n

Notes:
  • Every event is a single SSE ``data:`` line followed by ``\n\n``.
  • The metadata event carries the source-citation list so the Node.js
    gateway can attach it to the response envelope before forwarding tokens
    to the React client — citations are available without buffering the
    full response.
  • ``media_type="text/event-stream"`` and ``X-Accel-Buffering: no`` are set
    to disable nginx proxy buffering on the ML service side.
  • JWT validation and rate-limiting are handled upstream by the Node.js
    gateway.  The ML service validates only the ``X-Domain`` header here.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.core.config import Domain
from app.core.observability import bind_request_context, clear_request_context
from app.llm.orchestrator import RAGOrchestrator, get_orchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["query"])


# ── Request / Response models ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query:       str   = Field(..., min_length=1, max_length=2000)
    domain:      str   = Field(default="general")
    document_id: str | None = Field(default=None)
    acl:         str | list[str] = Field(default="public")

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        try:
            Domain(v.lower())
        except ValueError:
            raise ValueError(
                f"domain must be one of: {[d.value for d in Domain]}"
            )
        return v.lower()


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    """Encode a dict as a single SSE data line (``data: <json>\\n\\n``)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _build_sse_stream(
    orchestrator: RAGOrchestrator,
    request_body: QueryRequest,
    request_id:   str,
) -> AsyncGenerator[str, None]:
    """
    Async generator that drives the full SSE event sequence.

    Sequence:
      1. Call ``orchestrator.query_stream()`` → (metadata, token_stream)
      2. Yield metadata event.
      3. Yield one ``token`` event per generated token.
      4. Yield ``done`` event.

    Exceptions during streaming are caught and emitted as an ``error`` event
    rather than crashing the connection mid-stream.
    """
    domain = Domain(request_body.domain)

    try:
        meta, token_stream = await orchestrator.query_stream(
            query       = request_body.query,
            domain      = domain,
            request_id  = request_id,
            acl         = request_body.acl,
            document_id = request_body.document_id,
        )

        # ── Event 1: metadata ─────────────────────────────────────────────────
        yield _sse({
            "type":           "metadata",
            "request_id":     meta.request_id,
            "domain":         meta.domain,
            "sources":        meta.sources,
            "chunks_used":    meta.chunks_used,
            "chunks_dropped": meta.chunks_dropped,
            "latency_ms": {
                "dense":   round(meta.dense_latency_ms, 2),
                "sparse":  round(meta.sparse_latency_ms, 2),
                "rerank":  round(meta.rerank_latency_ms, 2),
                "total":   round(meta.total_latency_ms, 2),
            },
            "warnings":       meta.guardrail_warnings,
        })

        # ── Events 2..N: tokens ───────────────────────────────────────────────
        async for token in token_stream:
            if token:   # skip empty strings from guardrail suffix calculation
                yield _sse({"type": "token", "content": token})

        # ── Final event ───────────────────────────────────────────────────────
        yield _sse({"type": "done"})

    except Exception as exc:
        logger.error(
            "Streaming query error",
            extra={"request_id": request_id, "error": str(exc)},
            exc_info=True,
        )
        yield _sse({"type": "error", "message": "An internal error occurred."})

    finally:
        clear_request_context()


# ── Route handler ─────────────────────────────────────────────────────────────

@router.post(
    "/query",
    summary="Streaming RAG query",
    response_description="Server-Sent Events stream (text/event-stream)",
)
async def query(
    body:         QueryRequest,
    request:      Request,
    orchestrator: RAGOrchestrator = Depends(get_orchestrator),
    x_request_id: str | None      = Header(default=None, alias="X-Request-ID"),
    x_domain:     str | None      = Header(default=None, alias="X-Domain"),
) -> StreamingResponse:
    """
    Submit a RAG query and receive a streamed SSE response.

    The ``X-Domain`` header (set by the Node.js gateway) takes precedence
    over the ``domain`` field in the request body when both are present.
    """
    # X-Domain header from gateway takes precedence
    if x_domain:
        try:
            body.domain = Domain(x_domain.lower()).value
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid X-Domain header value: {x_domain}",
            )

    request_id = x_request_id or str(uuid.uuid4())
    bind_request_context(
        request_id=request_id,
        domain=body.domain,
    )

    logger.info(
        "Query received",
        extra={
            "request_id": request_id,
            "domain":     body.domain,
            "query_len":  len(body.query),
        },
    )

    return StreamingResponse(
        content=_build_sse_stream(orchestrator, body, request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx proxy buffering
            "X-Request-ID":     request_id,
            "Connection":       "keep-alive",
        },
    )
