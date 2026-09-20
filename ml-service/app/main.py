"""
app/main.py
────────────
FastAPI application factory and lifespan manager.

Startup sequence (inside ``lifespan``):
  1. ``setup_observability()``   — configure OTEL tracing + structlog
  2. ``bootstrap_collections()`` — idempotent Qdrant collection creation
  3. ``init_qdrant_client()``    — initialise the async Qdrant client singleton
  4. ``orchestrator.warm_up()``  — pre-load all ML models:
       • Domain embedders (FinBERT, BioMedBERT, all-mpnet-base-v2)
       • SPLADE sparse encoder
       • Cross-encoder reranker (ms-marco-MiniLM-L-6-v2)
       • LLM (Llama 3 / Gemma GGUF)
  5. ``instrument_fastapi(app)`` — attach OTEL auto-instrumentation

Shutdown sequence:
  1. ``close_qdrant_client()``   — graceful gRPC connection teardown

Middleware (applied in registration order):
  • CORS            — allow Node.js gateway origin
  • RequestID       — stamp X-Request-ID on every response
  • RequestLogging  — structured log per request (method, path, status, ms)

Routers:
  • /health          (liveness + readiness probes)
  • /api/v1/query    (streaming RAG)
  • /api/v1/ingest   (document upload)
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.observability import instrument_fastapi, setup_observability
from app.core.qdrant_client import (
    bootstrap_collections,
    close_qdrant_client,
    init_qdrant_client,
)
from app.llm.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Lifespan
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown within a single async context.

    Using the lifespan pattern (FastAPI ≥ 0.93) rather than the deprecated
    ``on_event`` decorators ensures startup failures abort the process cleanly
    rather than leaving a partially-initialised server accepting traffic.
    """
    settings = get_settings()

    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info(
        "Starting ML service",
        extra={
            "service":     settings.app_name,
            "version":     settings.app_version,
            "environment": settings.environment,
        },
    )

    # 1. Observability first — every subsequent log/trace benefits from it
    setup_observability(settings)

    # 2. Qdrant collections (sync bootstrap, then async client)
    bootstrap_collections(settings)         # idempotent, uses sync client once
    init_qdrant_client(settings)            # initialises async singleton

    # 3. Warm up all ML models (blocks until every model is loaded)
    orchestrator = get_orchestrator()
    await orchestrator.warm_up()

    # 4. Attach OTEL FastAPI auto-instrumentation AFTER app is fully built
    # instrument_fastapi(app)

    logger.info("ML service startup complete — ready to serve requests")

    yield   # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("ML service shutting down")
    await close_qdrant_client()
    logger.info("ML service shutdown complete")


# ══════════════════════════════════════════════════════════════════════════════
# Application factory
# ══════════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title       = settings.app_name,
        version     = settings.app_version,
        description = (
            "Domain-Adaptive RAG ML Microservice — "
            "embedding, retrieval, reranking, and LLM generation."
        ),
        docs_url    = "/docs"    if settings.environment != "production" else None,
        redoc_url   = "/redoc"   if settings.environment != "production" else None,
        openapi_url = "/openapi.json" if settings.environment != "production" else None,
        lifespan    = lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    # The ML service is internal and only called by the Node.js gateway.
    # In production, restrict to the gateway's internal DNS name.
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"] if settings.environment == "development" else [
            "http://api-gateway",
            "http://api-gateway:3000",
        ],
        allow_methods     = ["GET", "POST"],
        allow_headers     = ["*"],
        allow_credentials = False,
    )

    # ── Request ID middleware ─────────────────────────────────────────────────
    @app.middleware("http")
    async def _stamp_request_id(request: Request, call_next) -> Response:
        """
        Propagate X-Request-ID from the gateway (or generate a new one).
        Stamp it on the response so the gateway can correlate logs.
        """
        request_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Correlation-ID")
            or str(uuid.uuid4())
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # ── Structured request logging middleware ─────────────────────────────────
    @app.middleware("http")
    async def _log_requests(request: Request, call_next) -> Response:
        t0       = time.perf_counter()
        response = await call_next(request)
        latency  = (time.perf_counter() - t0) * 1000

        # Skip health-check noise in logs
        if request.url.path not in ("/health", "/health/ready"):
            logger.info(
                "http_request",
                extra={
                    "method":     request.method,
                    "path":       request.url.path,
                    "status":     response.status_code,
                    "latency_ms": round(latency, 2),
                    "request_id": getattr(request.state, "request_id", "-"),
                    "client_ip":  request.client.host if request.client else "-",
                },
            )
        return response

    # ── Routers ───────────────────────────────────────────────────────────────
    from app.api.health      import router as health_router
    from app.api.v1.query    import router as query_router
    from app.api.v1.ingest   import router as ingest_router

    app.include_router(health_router)
    app.include_router(query_router,  prefix="/api")
    app.include_router(ingest_router, prefix="/api")

    instrument_fastapi(app)

    return app


# ══════════════════════════════════════════════════════════════════════════════
# Application instance (module-level, imported by uvicorn)
# ══════════════════════════════════════════════════════════════════════════════

app = create_app()


# ── Entry point (direct execution) ───────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host    = settings.host,
        port    = settings.port,
        workers = settings.workers,
        reload  = settings.debug,
        log_config = None,   # structlog handles all logging
    )
