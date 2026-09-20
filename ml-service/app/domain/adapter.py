"""
app/domain/adapter.py
─────────────────────
Central domain adapter router.

Responsibilities:
  1. Parse and validate the X-Domain header value into a ``Domain`` enum.
  2. Lazily instantiate domain-specific embedders and chunkers (one instance
     per domain; never re-created after first use).
  3. Expose ``DomainAdapter`` — the single object that route handlers import
     to get the correct embedder, chunker, and prompt template for a request.
  4. Register each domain's components in a typed registry so adding a new
     domain requires only one registration call, not changes scattered across
     the codebase.

Threading / concurrency model:
  • Component instances are created at first access and cached in a module-level
    registry.  Guarded by ``asyncio.Lock`` per domain so that concurrent
    startup requests don't race to load the same model twice.
  • Model loading is CPU-bound and blocking; it is dispatched to a thread-pool
    executor so the event loop is not stalled during cold start.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.core.config import Domain, get_settings
from app.core.observability import get_tracer, span

logger = logging.getLogger(__name__)
tracer = get_tracer()


# ── Protocols (structural interfaces) ────────────────────────────────────────
# Using Protocol so domain implementations are duck-typed rather than forced
# into a deep class hierarchy.

@runtime_checkable
class Embedder(Protocol):
    """Any object that can embed a list of strings into float vectors."""
    domain: Domain

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...

    @property
    def vector_size(self) -> int:
        """Dimensionality of the output vectors."""
        ...


@runtime_checkable
class Chunker(Protocol):
    """Any object that can split a document into domain-appropriate chunks."""
    domain: Domain

    def chunk(self, content: str, metadata: dict) -> list[dict]:
        """
        Split ``content`` into chunks.

        Returns a list of dicts, each containing at minimum:
          • ``text``      – chunk content
          • ``chunk_type`` – e.g. "sentence", "table", "entity"
          • ``metadata``  – merged document + chunk-level metadata
        """
        ...


@runtime_checkable
class PromptTemplate(Protocol):
    """Any object that can render a domain-specific prompt."""
    domain: Domain

    def system_prompt(self) -> str:
        """Return the static system prompt for this domain."""
        ...

    def render(self, query: str, context_chunks: list[dict]) -> str:
        """Render the full prompt with retrieved context injected."""
        ...


# ── Per-domain component record ───────────────────────────────────────────────

@dataclass
class DomainComponents:
    """Lazily-loaded trio of (embedder, chunker, prompt_template) for one domain."""
    domain:          Domain
    _embedder:       Embedder | None        = field(default=None, repr=False)
    _chunker:        Chunker | None         = field(default=None, repr=False)
    _prompt_template: PromptTemplate | None = field(default=None, repr=False)
    _lock:           asyncio.Lock           = field(default_factory=asyncio.Lock, repr=False)

    # ── Lazy accessors ────────────────────────────────────────────────────────
    async def embedder(self) -> Embedder:
        if self._embedder is None:
            await self._load_embedder()
        return self._embedder  # type: ignore[return-value]

    async def chunker(self) -> Chunker:
        if self._chunker is None:
            await self._load_chunker()
        return self._chunker  # type: ignore[return-value]

    async def prompt_template(self) -> PromptTemplate:
        if self._prompt_template is None:
            await self._load_prompt_template()
        return self._prompt_template  # type: ignore[return-value]

    # ── Private loaders (dispatch blocking I/O to thread pool) ────────────────
    async def _load_embedder(self) -> None:
        async with self._lock:
            if self._embedder is not None:
                return
            with span("domain.load_embedder", domain=self.domain.value):
                logger.info("Loading embedder", extra={"domain": self.domain.value})
                loop = asyncio.get_event_loop()
                self._embedder = await loop.run_in_executor(
                    None, _build_embedder, self.domain
                )
                logger.info("Embedder ready", extra={"domain": self.domain.value})

    async def _load_chunker(self) -> None:
        async with self._lock:
            if self._chunker is not None:
                return
            with span("domain.load_chunker", domain=self.domain.value):
                self._chunker = _build_chunker(self.domain)

    async def _load_prompt_template(self) -> None:
        async with self._lock:
            if self._prompt_template is not None:
                return
            self._prompt_template = _build_prompt_template(self.domain)


# ── Component factory functions ───────────────────────────────────────────────

def _build_embedder(domain: Domain) -> Embedder:
    """Import and instantiate the domain-specific embedder (blocking)."""
    if domain == Domain.FINANCIAL:
        from app.domain.financial.embedder import FinancialEmbedder
        return FinancialEmbedder()
    if domain == Domain.MEDICAL:
        from app.domain.medical.embedder import MedicalEmbedder
        return MedicalEmbedder()
    if domain == Domain.GENERAL:
        from app.domain.general.embedder import GeneralEmbedder
        return GeneralEmbedder()
    raise ValueError(f"No embedder registered for domain: {domain}")


def _build_chunker(domain: Domain) -> Chunker:
    """Import and instantiate the domain-specific chunker (non-blocking)."""
    if domain == Domain.FINANCIAL:
        from app.domain.financial.chunker import FinancialChunker
        return FinancialChunker()
    if domain == Domain.MEDICAL:
        from app.domain.medical.chunker import MedicalChunker
        return MedicalChunker()
    if domain == Domain.GENERAL:
        from app.domain.general.chunker import GeneralChunker
        return GeneralChunker()
    raise ValueError(f"No chunker registered for domain: {domain}")


def _build_prompt_template(domain: Domain) -> PromptTemplate:
    """Import and instantiate the domain-specific prompt template."""
    if domain == Domain.FINANCIAL:
        from app.domain.financial.prompt_template import FinancialPromptTemplate
        return FinancialPromptTemplate()
    if domain == Domain.MEDICAL:
        from app.domain.medical.prompt_template import MedicalPromptTemplate
        return MedicalPromptTemplate()
    if domain == Domain.GENERAL:
        from app.domain.general.prompt_template import GeneralPromptTemplate
        return GeneralPromptTemplate()
    raise ValueError(f"No prompt template registered for domain: {domain}")


# ── Registry & DomainAdapter ──────────────────────────────────────────────────

# Module-level registry: one DomainComponents instance per domain.
_registry: dict[Domain, DomainComponents] = {
    d: DomainComponents(domain=d) for d in Domain
}


class DomainAdapter:
    """
    Stateless facade used by route handlers.

    Example usage in a FastAPI route::

        adapter = DomainAdapter()
        domain  = adapter.parse_domain(request.headers.get("X-Domain"))
        embedder = await adapter.get_embedder(domain)
        chunker  = await adapter.get_chunker(domain)
        template = await adapter.get_prompt_template(domain)
    """

    # ── Domain parsing ────────────────────────────────────────────────────────

    @staticmethod
    def parse_domain(raw: str | None, default: Domain = Domain.GENERAL) -> Domain:
        """
        Validate the X-Domain header value and return a ``Domain`` enum.

        Falls back to ``default`` when the header is absent or unrecognised
        rather than raising — the gateway validates the header upstream; this
        is a defence-in-depth check.
        """
        if raw is None:
            logger.debug("X-Domain header absent; defaulting to GENERAL")
            return default

        normalised = raw.strip().lower()
        try:
            return Domain(normalised)
        except ValueError:
            logger.warning(
                "Unrecognised X-Domain value; defaulting to GENERAL",
                extra={"raw_value": raw},
            )
            return default

    # ── Component accessors ───────────────────────────────────────────────────

    async def get_embedder(self, domain: Domain) -> Embedder:
        with span("adapter.get_embedder", domain=domain.value):
            return await _registry[domain].embedder()

    async def get_chunker(self, domain: Domain) -> Chunker:
        return await _registry[domain].chunker()

    async def get_prompt_template(self, domain: Domain) -> PromptTemplate:
        return await _registry[domain].prompt_template()

    # ── Warm-up helper ────────────────────────────────────────────────────────

    async def warm_up(self, domains: list[Domain] | None = None) -> None:
        """
        Pre-load all (or specified) domain models at startup so the first
        real request is not slowed by model loading.

        Call inside the FastAPI lifespan after ``setup_observability()``.
        """
        targets = domains or list(Domain)
        logger.info("Warming up domain adapters", extra={"domains": [d.value for d in targets]})

        tasks = [
            asyncio.gather(
                _registry[d].embedder(),
                _registry[d].chunker(),
                _registry[d].prompt_template(),
            )
            for d in targets
        ]
        await asyncio.gather(*tasks)
        logger.info("Domain adapters warm-up complete")


# ── FastAPI dependency ────────────────────────────────────────────────────────

_adapter_singleton = DomainAdapter()


def get_domain_adapter() -> DomainAdapter:
    """FastAPI dependency that returns the shared DomainAdapter instance."""
    return _adapter_singleton
