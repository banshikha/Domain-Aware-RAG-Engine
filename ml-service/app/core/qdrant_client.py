"""
app/core/qdrant_client.py
─────────────────────────
Manages the single shared QdrantClient instance and bootstraps per-domain
collections on startup.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client import models
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    OptimizersConfigDiff,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    VectorParams,
)

from app.core.config import Domain, Settings, get_settings

logger = logging.getLogger(__name__)


# ── HNSW / quantisation presets per domain ───────────────────────────────────

_HNSW_PRESETS: dict[Domain, HnswConfigDiff] = {
    Domain.FINANCIAL: HnswConfigDiff(m=16, ef_construct=200, full_scan_threshold=10_000),
    Domain.MEDICAL:   HnswConfigDiff(m=16, ef_construct=200, full_scan_threshold=10_000),
    Domain.GENERAL:   HnswConfigDiff(m=16, ef_construct=100, full_scan_threshold=20_000),
}

_SCALAR_QUANTIZATION = models.ScalarQuantization(
    scalar=models.ScalarQuantizationConfig(
        type=models.ScalarType.INT8,
        quantile=0.99,
        always_ram=True
    )
)

_OPTIMIZERS_CONFIG = OptimizersConfigDiff(
    indexing_threshold=20_000,   # build HNSW index after 20k vectors
    memmap_threshold=100_000,    # memmap segments beyond 100k vectors
)


# ── Client factory ────────────────────────────────────────────────────────────

def _build_sync_client(settings: Settings) -> QdrantClient:
    """Build a synchronous QdrantClient (used only for collection bootstrap)."""
    kwargs: dict = dict(
        host=settings.qdrant_host,
        api_key=settings.qdrant_api_key,
    )
    if settings.qdrant_use_grpc:
        kwargs["grpc_port"] = settings.qdrant_grpc_port
        kwargs["prefer_grpc"] = True
    else:
        kwargs["port"] = settings.qdrant_port
    return QdrantClient(**kwargs)


def _build_async_client(settings: Settings) -> AsyncQdrantClient:
    """Build the async client used by all inference/ingestion paths."""
    kwargs: dict = dict(
        host=settings.qdrant_host,
        api_key=settings.qdrant_api_key,
    )
    if settings.qdrant_use_grpc:
        kwargs["grpc_port"] = settings.qdrant_grpc_port
        kwargs["prefer_grpc"] = True
    else:
        kwargs["port"] = settings.qdrant_port
    return AsyncQdrantClient(**kwargs)


# ── Collection bootstrap ──────────────────────────────────────────────────────

def bootstrap_collections(settings: Optional[Settings] = None) -> None:
    """
    Idempotently create one Qdrant collection per domain.
    """
    settings = settings or get_settings()
    client = _build_sync_client(settings)

    for domain in Domain:
        collection_name = settings.qdrant_collection_name(domain)
        hnsw_config     = _HNSW_PRESETS[domain]
        
        # ── FORCED OVERRIDE ──
        # Guarantee 384 dimensions for all-MiniLM-L6-v2 compatibility
        forced_vector_size = 384 if domain.value == "general" else 768

        try:
            info = client.get_collection(collection_name)
            logger.info(
                "Qdrant collection already exists",
                extra={"collection": collection_name, "status": info.status},
            )
        except (UnexpectedResponse, Exception) as exc:
            if _is_not_found(exc):
                logger.info(
                    "Creating Qdrant collection",
                    extra={"collection": collection_name, "vector_size": forced_vector_size},
                )
                
                # Create the collection with BOTH Dense (384) and Sparse (SPLADE) configs
                client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=forced_vector_size,
                        distance=Distance.COSINE,
                        on_disk=False,
                    ),
                    sparse_vectors_config={
                        "sparse": models.SparseVectorParams()
                    },
                    hnsw_config=hnsw_config,
                    quantization_config=_SCALAR_QUANTIZATION,
                    optimizers_config=_OPTIMIZERS_CONFIG,
                )
                
                _create_payload_indexes(client, collection_name)
                logger.info(
                    "Qdrant collection created successfully",
                    extra={"collection": collection_name},
                )
            else:
                logger.error(
                    "Unexpected error checking Qdrant collection",
                    extra={"collection": collection_name, "error": str(exc)},
                )
                raise

    client.close()


def _create_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    """Create payload (metadata) indexes to support fast filtered search."""
    from qdrant_client.models import (
        PayloadSchemaType,
        TextIndexParams,
        TokenizerType,
    )

    keyword_fields = ["document_id", "domain", "acl", "chunk_type"]
    for field in keyword_fields:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )

    client.create_payload_index(
        collection_name=collection_name,
        field_name="content",
        field_schema=TextIndexParams(
            type="text",
            tokenizer=TokenizerType.WORD,
            min_token_len=2,
            max_token_len=15,
            lowercase=True,
        ),
    )

    client.create_payload_index(
        collection_name=collection_name,
        field_name="ingested_at",
        field_schema=PayloadSchemaType.INTEGER,
    )


# ── Singleton async client ────────────────────────────────────────────────────

_async_client: Optional[AsyncQdrantClient] = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Return the application-scoped async Qdrant client."""
    if _async_client is None:
        raise RuntimeError(
            "Qdrant client has not been initialised. "
            "Call init_qdrant_client() during application startup."
        )
    return _async_client


def init_qdrant_client(settings: Optional[Settings] = None) -> AsyncQdrantClient:
    """Initialise (or replace) the module-level async client singleton."""
    global _async_client
    settings = settings or get_settings()
    _async_client = _build_async_client(settings)
    logger.info(
        "AsyncQdrantClient initialised",
        extra={
            "host": settings.qdrant_host,
            "grpc": settings.qdrant_use_grpc,
        },
    )
    return _async_client


async def close_qdrant_client() -> None:
    """Gracefully close the async client.  Call during FastAPI shutdown."""
    global _async_client
    if _async_client is not None:
        await _async_client.close()
        _async_client = None
        logger.info("AsyncQdrantClient closed")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_not_found(exc: Exception) -> bool:
    """Return True when the exception indicates a missing collection."""
    msg = str(exc).lower()
    return (
        "not found" in msg
        or "doesn't exist" in msg
        or "status_code=404" in msg
        or (isinstance(exc, UnexpectedResponse) and exc.status_code == 404)
    )