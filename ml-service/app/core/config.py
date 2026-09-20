"""
app/core/config.py
──────────────────
Central Pydantic-Settings configuration for the ML microservice.
All values are read from environment variables (or .env file).
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Domain enum ──────────────────────────────────────────────────────────────

class Domain(str, Enum):
    FINANCIAL = "financial"
    MEDICAL   = "medical"
    GENERAL   = "general"


# ── Embedding model registry ─────────────────────────────────────────────────
# Kept here so adapter.py can import without circular deps.

DOMAIN_EMBEDDING_MODELS: dict[Domain, str] = {
    Domain.FINANCIAL: "yiyanghkust/finbert-tone",          # FinBERT-large proxy
    Domain.MEDICAL:   "microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext",
    Domain.GENERAL:   "sentence-transformers/all-mpnet-base-v2",
}

CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


# ── Main settings class ───────────────────────────────────────────────────────

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Service identity ──────────────────────────────────────────────────────
    app_name: str        = "rag-ml-service"
    app_version: str     = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool          = False
    log_level: str       = "INFO"

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = Field(default=8001, ge=1024, le=65535)
    workers: int = Field(default=1, ge=1)       # set > 1 only without reload

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_host: str     = "localhost"
    qdrant_port: int     = Field(default=6333, ge=1, le=65535)
    qdrant_grpc_port: int = Field(default=6334, ge=1, le=65535)
    qdrant_api_key: str | None = None           # None → no auth (local dev)
    qdrant_use_grpc: bool = True                # prefer gRPC for vector upserts
    # One collection per domain — names are derived at runtime via helper below.
    qdrant_collection_prefix: str = "rag"       # e.g. rag_financial, rag_medical
    qdrant_vector_size_financial: int = 384
    qdrant_vector_size_medical: int   = 384
    qdrant_vector_size_general: int   = 384

    # ── Redis (semantic cache + rate-limit mirror) ─────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600               # 1 h semantic-cache TTL
    cache_similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)

    # ── LLM (llama-cpp-python) ────────────────────────────────────────────────
    llm_model_path: str = "D:/Documents/Code/ML-Proj/rag-system/ml-service/models/tinyllama-1.1b-chat-v1.0-q4_k_m.gguf"
    llm_n_ctx: int      = 8192                  # context window
    llm_n_gpu_layers: int = 0                   # set > 0 for GPU offload
    llm_max_tokens: int  = 1024
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_top_p: float       = Field(default=0.9, ge=0.0, le=1.0)

    # ── Retrieval ─────────────────────────────────────────────────────────────
    retrieval_top_k_dense: int  = 20            # candidates from Qdrant ANN
    retrieval_top_k_sparse: int = 20            # candidates from BM25
    retrieval_top_k_rerank: int = 5             # final docs after cross-encoder
    rrf_k: int = 60                             # RRF constant (60 is standard)

    # ── Ingestion ─────────────────────────────────────────────────────────────
    max_upload_size_mb: int      = 50
    unstructured_api_key: str | None = None     # None → local OSS mode
    chunk_size_financial: int    = 512
    chunk_size_medical: int      = 384
    chunk_size_general: int      = 800
    chunk_overlap: int           = 32

    # ── S3 (document archival) ────────────────────────────────────────────────
    s3_bucket: str                = "rag-documents"
    s3_region: str                = "us-east-1"
    aws_access_key_id: str | None = None        # fallback to IAM role
    aws_secret_access_key: str | None = None

    # ── PostgreSQL (metadata) ─────────────────────────────────────────────────
    postgres_dsn: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag"

    # ── JWT (mirror of gateway secret for internal validation) ────────────────
    jwt_secret: str      = "change-me-in-production"
    jwt_algorithm: str   = "HS256"

    # ── OpenTelemetry ─────────────────────────────────────────────────────────
    otel_enabled: bool          = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str      = "rag-ml-service"

    # ── NeMo Guardrails ───────────────────────────────────────────────────────
    guardrails_config_path: str = "app/llm/guardrails_config"

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    # ── Helpers ───────────────────────────────────────────────────────────────
    def qdrant_collection_name(self, domain: Domain) -> str:
        """Return the Qdrant collection name for a given domain."""
        return f"{self.qdrant_collection_prefix}_{domain.value}"

    def vector_size_for(self, domain: Domain) -> int:
        """Return the embedding dimension for a given domain."""
        mapping = {
            Domain.FINANCIAL: self.qdrant_vector_size_financial,
            Domain.MEDICAL:   self.qdrant_vector_size_medical,
            Domain.GENERAL:   self.qdrant_vector_size_general,
        }
        return mapping[domain]


# ── Singleton accessor ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance.  Use as a FastAPI dependency."""
    return Settings()
