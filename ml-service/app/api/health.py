"""
app/api/health.py
──────────────────
Health check endpoint.

Provides two levels of health information:
  • GET /health          — liveness probe (always fast, no I/O)
  • GET /health/ready    — readiness probe (checks Qdrant + Redis connectivity)

The liveness probe is used by Kubernetes/Docker to know if the process is
alive.  The readiness probe is used to know if traffic should be routed to
this instance — it returns 503 until all dependencies are reachable.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status:  str
    service: str
    version: str
    uptime_s: float


_start_time = time.time()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
)
async def health() -> HealthResponse:
    """Fast liveness check — returns 200 if the process is running."""
    settings = get_settings()
    return HealthResponse(
        status   = "ok",
        service  = settings.app_name,
        version  = settings.app_version,
        uptime_s = round(time.time() - _start_time, 1),
    )


@router.get(
    "/health/ready",
    summary="Readiness probe",
)
async def ready() -> JSONResponse:
    """
    Readiness check — verifies Qdrant and Redis are reachable.
    Returns 200 when ready, 503 when any dependency is unavailable.
    """
    settings = get_settings()
    checks:  dict[str, str] = {}
    healthy: bool           = True

    # ── Qdrant ────────────────────────────────────────────────────────────────
    try:
        from app.core.qdrant_client import get_qdrant_client
        client = get_qdrant_client()
        await client.get_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:
        checks["qdrant"] = f"error: {exc}"
        healthy = False

    # ── Redis ─────────────────────────────────────────────────────────────────
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        healthy = False

    code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=code,
        content={
            "status":  "ready" if healthy else "not_ready",
            "checks":  checks,
            "service": settings.app_name,
            "version": settings.app_version,
        },
    )
