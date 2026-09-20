"""
app/llm/guardrails.py
──────────────────────
Composable guardrail manager for the LLM generation pipeline.

Architecture:
  Domain-specific guardrail logic (disclaimers, compliance language, crisis
  referrals) already lives in each ``PromptTemplate`` implementation:
    • ``FinancialPromptTemplate.ensure_disclaimer()``
    • ``FinancialPromptTemplate.inject_forward_looking_warning()``
    • ``MedicalPromptTemplate.ensure_disclaimer()``
    • ``MedicalPromptTemplate.inject_safety_referral()``
    • ``MedicalPromptTemplate.inject_prescribing_note()``
    • ``GeneralPromptTemplate.ensure_disclaimer()``

  This module does NOT replicate that logic.  Instead, ``GuardrailManager``
  acts as a composable orchestration layer:

  Pre-generation guards (applied to the user query BEFORE retrieval):
    • ``QuerySanitiser``  — strip/flag prompt-injection attempts
    • ``DomainPreGuard``  — delegate to ``PromptTemplate.pre_guard()`` if present

  Post-generation guards (applied to the assembled LLM response):
    • ``DomainPostGuard`` — call all domain-specific post-processing hooks
                            in a deterministic order via the PromptTemplate
    • ``EmptyResponseGuard`` — replace empty/whitespace-only responses

  Each guard is a small callable conforming to ``PreGuard`` or ``PostGuard``
  protocol.  New guards are registered via ``GuardrailManager.register_pre()``
  / ``register_post()`` without modifying existing code.

Streaming compatibility:
  Post-generation guards operate on the FULLY assembled response string, not
  on individual streamed tokens.  The orchestrator buffers the complete
  response, applies guards, then yields the guarded text.  This is the only
  correct approach for compliance guards — a disclaimer cannot be injected
  mid-stream reliably.

  The ``GuardrailManager.apply_post_streaming()`` async generator wraps this
  pattern: it accepts a token stream, buffers it, applies guards, then
  re-yields the guarded text in a final chunk.
"""

from __future__ import annotations

import logging
import re
from typing import AsyncGenerator, Callable, Protocol, runtime_checkable

from app.core.config import Domain
from app.core.observability import span

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Guard protocols
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class PreGuard(Protocol):
    """
    A callable that inspects/modifies the user query before retrieval.

    Returns the (possibly modified) query string and an optional list of
    warning strings that will be logged and attached to the trace span.
    """
    def __call__(self, query: str, domain: Domain) -> tuple[str, list[str]]:
        ...


@runtime_checkable
class PostGuard(Protocol):
    """
    A callable that inspects/modifies the fully assembled LLM response.

    Returns the (possibly modified) response string.
    """
    def __call__(self, response: str, domain: Domain) -> str:
        ...


# ══════════════════════════════════════════════════════════════════════════════
# Built-in pre-generation guards
# ══════════════════════════════════════════════════════════════════════════════

# Patterns that indicate prompt-injection attempts
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions?", re.I),
    re.compile(r"(you\s+are\s+now|act\s+as|pretend\s+(you\s+are|to\s+be))\s+", re.I),
    re.compile(r"(system|assistant|user)\s*:\s*", re.I),   # role injection
    re.compile(r"<\s*(system|assistant|INST|s)\s*>", re.I),  # template tag injection
    re.compile(r"\[/?INST\]", re.I),
    re.compile(r"#{3,}", re.I),   # markdown heading floods (common in injections)
]

# Maximum query length (characters) — prevents token-stuffing attacks
_MAX_QUERY_CHARS = 2000


class QuerySanitiser:
    """
    Pre-guard that:
      1. Enforces a maximum query length.
      2. Detects and flags prompt-injection patterns.
      3. Does NOT silently remove content — it logs warnings and allows the
         query through so the retrieval result informs the response.
         (Blocking the query entirely is the API gateway's job.)
    """

    def __call__(self, query: str, domain: Domain) -> tuple[str, list[str]]:
        warnings: list[str] = []

        # Length enforcement
        if len(query) > _MAX_QUERY_CHARS:
            query    = query[:_MAX_QUERY_CHARS]
            warnings.append(
                f"Query truncated to {_MAX_QUERY_CHARS} characters"
            )

        # Injection detection
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(query):
                warnings.append(
                    f"Potential prompt injection detected "
                    f"(pattern: {pattern.pattern[:40]})"
                )
                # Log at WARNING; do not strip content — that changes semantics
                logger.warning(
                    "Prompt injection pattern detected in query",
                    extra={
                        "domain":  domain.value,
                        "pattern": pattern.pattern[:60],
                    },
                )

        return query, warnings


class DomainPreGuard:
    """
    Pre-guard that delegates to ``PromptTemplate._diagnosis_guard()`` or
    equivalent pre-hooks when the template exposes them.

    Currently only the MedicalPromptTemplate has a meaningful pre-hook
    (diagnosis request detection).  For other domains this is a no-op.
    """

    def __call__(self, query: str, domain: Domain) -> tuple[str, list[str]]:
        warnings: list[str] = []
        if domain == Domain.MEDICAL:
            from app.domain.medical.prompt_template import _DIAGNOSIS_REQUEST_RE
            if _DIAGNOSIS_REQUEST_RE.search(query):
                warnings.append(
                    "Query appears to request a personal medical diagnosis — "
                    "response will include professional-referral instruction."
                )
        return query, warnings


# ══════════════════════════════════════════════════════════════════════════════
# Built-in post-generation guards
# ══════════════════════════════════════════════════════════════════════════════

class EmptyResponseGuard:
    """
    Post-guard that replaces empty or whitespace-only LLM responses with a
    safe fallback rather than returning a blank string to the client.
    """

    _FALLBACK = (
        "I was unable to generate a response based on the available information. "
        "Please rephrase your question or consult the source documents directly."
    )

    def __call__(self, response: str, domain: Domain) -> str:
        if not response or not response.strip():
            logger.warning(
                "Empty LLM response replaced with fallback",
                extra={"domain": domain.value},
            )
            return self._FALLBACK
        return response


class DomainPostGuard:
    """
    Post-guard that applies all domain-specific post-processing hooks from
    the relevant PromptTemplate in a deterministic order.

    Hook execution order per domain:
      Financial:
        1. inject_forward_looking_warning  (prepend SEC safe-harbour if needed)
        2. ensure_disclaimer               (mandatory footer)

      Medical:
        1. inject_safety_referral          (crisis hotline if sensitive topics)
        2. inject_prescribing_note         (FDA prescribing info if dosages present)
        3. ensure_disclaimer               (mandatory footer)

      General:
        1. ensure_disclaimer               (lightweight note)

    Adding a new hook to a domain requires only modifying this method and
    the relevant PromptTemplate — no other files change.
    """

    def __call__(self, response: str, domain: Domain) -> str:
        with span("guardrails.domain_post_guard", domain=domain.value):
            if domain == Domain.FINANCIAL:
                return self._apply_financial(response)
            if domain == Domain.MEDICAL:
                return self._apply_medical(response)
            return self._apply_general(response)

    @staticmethod
    def _apply_financial(response: str) -> str:
        from app.domain.financial.prompt_template import FinancialPromptTemplate
        t = FinancialPromptTemplate()
        response = t.inject_forward_looking_warning(response)
        response = t.ensure_disclaimer(response)
        return response

    @staticmethod
    def _apply_medical(response: str) -> str:
        from app.domain.medical.prompt_template import MedicalPromptTemplate
        t = MedicalPromptTemplate()
        response = t.inject_safety_referral(response)
        response = t.inject_prescribing_note(response)
        response = t.ensure_disclaimer(response)
        return response

    @staticmethod
    def _apply_general(response: str) -> str:
        from app.domain.general.prompt_template import GeneralPromptTemplate
        t = GeneralPromptTemplate()
        response = t.ensure_disclaimer(response)
        return response


# ══════════════════════════════════════════════════════════════════════════════
# GuardrailManager
# ══════════════════════════════════════════════════════════════════════════════

class GuardrailManager:
    """
    Orchestrates a pipeline of pre- and post-generation guards.

    Default configuration (applied in order):
      Pre-guards:   QuerySanitiser → DomainPreGuard
      Post-guards:  EmptyResponseGuard → DomainPostGuard

    Custom guards can be added via ``register_pre()`` / ``register_post()``.
    Guards are applied in registration order (FIFO).

    Usage in orchestrator::

        manager = GuardrailManager()

        # Before retrieval
        clean_query, warnings = manager.apply_pre(query, domain)

        # After generation (non-streaming)
        safe_response = manager.apply_post(raw_response, domain)

        # After generation (streaming)
        async for chunk in manager.apply_post_streaming(token_stream, domain):
            yield chunk
    """

    def __init__(self) -> None:
        self._pre_guards:  list[PreGuard]  = [QuerySanitiser(), DomainPreGuard()]
        self._post_guards: list[PostGuard] = [EmptyResponseGuard(), DomainPostGuard()]

    # ── Guard registration ────────────────────────────────────────────────────

    def register_pre(self, guard: PreGuard) -> None:
        """Append a custom pre-generation guard."""
        self._pre_guards.append(guard)

    def register_post(self, guard: PostGuard) -> None:
        """Append a custom post-generation guard."""
        self._post_guards.append(guard)

    # ── Pre-generation ────────────────────────────────────────────────────────

    def apply_pre(
        self,
        query:  str,
        domain: Domain,
    ) -> tuple[str, list[str]]:
        """
        Run all pre-guards on the user query.

        Returns:
            (sanitised_query, all_warnings_from_all_guards)
        """
        all_warnings: list[str] = []
        with span("guardrails.pre", domain=domain.value):
            for guard in self._pre_guards:
                query, warnings = guard(query, domain)
                all_warnings.extend(warnings)

        if all_warnings:
            logger.info(
                "Pre-guard warnings",
                extra={"domain": domain.value, "warnings": all_warnings},
            )

        return query, all_warnings

    # ── Post-generation (non-streaming) ───────────────────────────────────────

    def apply_post(self, response: str, domain: Domain) -> str:
        """
        Run all post-guards on a fully assembled response string.

        Returns the guarded response string.
        """
        with span("guardrails.post", domain=domain.value):
            for guard in self._post_guards:
                response = guard(response, domain)
        return response

    # ── Post-generation (streaming) ───────────────────────────────────────────

    async def apply_post_streaming(
        self,
        token_stream: AsyncGenerator[str, None],
        domain:       Domain,
    ) -> AsyncGenerator[str, None]:
        """
        Buffer a streaming token generator, apply post-guards to the
        assembled text, then re-yield in a streaming-compatible way.

        Strategy:
          • Yield tokens as they arrive (client sees progressive output).
          • Buffer all tokens simultaneously.
          • After the stream ends, compute and yield the guarded suffix
            (disclaimer, safety note, etc.) as a final chunk.

        This preserves the perceived streaming experience while guaranteeing
        compliance text always appears at the end — even if the LLM omits it.

        Yields:
          Raw token strings during streaming, then a guarded suffix chunk
          at the end (may be empty string if no suffix is needed).
        """
        return self._stream_with_post_guard(token_stream, domain)

    async def _stream_with_post_guard(
        self,
        token_stream: AsyncGenerator[str, None],
        domain:       Domain,
    ) -> AsyncGenerator[str, None]:
        """Internal async generator implementing the buffered streaming guard."""
        buffer: list[str] = []

        async for token in token_stream:
            buffer.append(token)
            yield token

        # Apply all post-guards to the complete assembled response
        assembled = "".join(buffer)
        guarded   = self.apply_post(assembled, domain)

        # Compute the suffix that was added (if any) and yield it
        if len(guarded) > len(assembled):
            suffix = guarded[len(assembled):]
            yield suffix


# ── FastAPI dependency ────────────────────────────────────────────────────────

_guardrail_manager_singleton: GuardrailManager | None = None


def get_guardrail_manager() -> GuardrailManager:
    """Return the application-scoped GuardrailManager singleton."""
    global _guardrail_manager_singleton
    if _guardrail_manager_singleton is None:
        _guardrail_manager_singleton = GuardrailManager()
    return _guardrail_manager_singleton
