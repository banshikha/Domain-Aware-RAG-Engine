"""
app/llm/token_budget.py
────────────────────────
Context-window budget manager for the LLM generation step.

Responsibilities:
  • Given a ranked list of ``DocumentChunk`` objects plus the rendered system
    prompt and user query, determine how many chunks fit inside the LLM's
    context window while reserving space for the generated response.
  • Truncate from the lowest-ranked chunks first (highest rank number =
    least relevant = first to go).
  • Return a ``BudgetResult`` that describes exactly what was kept, what was
    dropped, and the estimated token distribution across prompt sections.

Token estimation strategy:
  Exact tokenisation (loading the model's BPE vocabulary) is expensive and
  creates a hard dependency on the specific model's tokenizer.  Instead we
  use a conservative char-to-token ratio of 3.5 chars/token.

Budget partitioning:
  ┌──────────────────────────────────────────────────────┐
  │  Total context window  (llm_n_ctx)                   │
  │  ├─ System prompt tokens      (estimated)            │
  │  ├─ User query tokens         (estimated)            │
  │  ├─ Prompt scaffolding        (_SCAFFOLD_TOKENS = 64)│
  │  ├─ Reserved for response     (llm_max_tokens)       │
  │  └─ Available for chunks      (computed)             │
  └──────────────────────────────────────────────────────┘

  Chunks are added in rank order (rank 1 first) until the chunk budget is
  exhausted. If a chunk cannot fit entirely, it is completely dropped 
  (All-or-Nothing) to preserve semantic integrity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.models import DocumentChunk
from app.core.observability import span

logger = logging.getLogger(__name__)

# Chars per token — conservative estimate for LLM (SentencePiece) tokenisers
_CHARS_PER_TOKEN: float = 3.5

# Overhead tokens for prompt scaffolding (section headers, separators, etc.)
_SCAFFOLD_TOKENS: int = 64


# ── Token counting ────────────────────────────────────────────────────────────

def _count_tokens(text: str) -> int:
    """
    Return an estimated token count for ``text``.

    Tries tiktoken first (exact), falls back to char-ratio (conservative).
    """
    try:
        import tiktoken
        settings = get_settings()
        enc_name = getattr(settings, "llm_tokenizer_encoding", None) or "cl100k_base"
        enc      = tiktoken.get_encoding(enc_name)
        return len(enc.encode(text))
    except (ImportError, Exception):
        return max(1, int(len(text) / _CHARS_PER_TOKEN))


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BudgetResult:
    """
    Output of ``TokenBudgetManager.fit_chunks()``.
    """
    kept_chunks:     list[DocumentChunk] = field(default_factory=list)
    dropped_chunks:  list[DocumentChunk] = field(default_factory=list)
    tokens_system:   int = 0
    tokens_query:    int = 0
    tokens_chunks:   int = 0
    tokens_reserved: int = 0
    tokens_total:    int = 0
    budget_tokens:   int = 0
    truncated:       bool = False

    @property
    def utilisation(self) -> float:
        """Fraction of total context window used (0.0–1.0)."""
        settings = get_settings()
        return self.tokens_total / max(1, settings.llm_n_ctx)


# ══════════════════════════════════════════════════════════════════════════════
# Token Budget Manager
# ══════════════════════════════════════════════════════════════════════════════

class TokenBudgetManager:
    """
    Fits the maximum number of retrieved chunks into the LLM context window.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._n_ctx        = settings.llm_n_ctx        # total context window
        self._max_tokens   = settings.llm_max_tokens   # reserved for response

    # ── Public API ────────────────────────────────────────────────────────────

    def fit_chunks(
        self,
        chunks:        list[DocumentChunk],
        system_prompt: str,
        user_query:    str,
        extra_reserve: int = 0,
    ) -> BudgetResult:
        with span("llm.token_budget.fit_chunks", n_input=len(chunks)):
            return self._fit(chunks, system_prompt, user_query, extra_reserve)

    # ── Core fitting logic ────────────────────────────────────────────────────

    def _fit(
        self,
        chunks:        list[DocumentChunk],
        system_prompt: str,
        user_query:    str,
        extra_reserve: int,
    ) -> BudgetResult:
        # ── Measure fixed costs ───────────────────────────────────────────────
        tokens_system   = _count_tokens(system_prompt)
        tokens_query    = _count_tokens(user_query)
        tokens_reserved = self._max_tokens + extra_reserve
        tokens_fixed    = (
            tokens_system
            + tokens_query
            + tokens_reserved
            + _SCAFFOLD_TOKENS
        )

        chunk_budget = self._n_ctx - tokens_fixed

        if chunk_budget <= 0:
            logger.warning(
                "Token budget exhausted by fixed costs — no chunks can fit",
                extra={
                    "n_ctx":          self._n_ctx,
                    "tokens_fixed":   tokens_fixed,
                    "chunk_budget":   chunk_budget,
                },
            )
            return BudgetResult(
                dropped_chunks   = list(chunks),
                tokens_system    = tokens_system,
                tokens_query     = tokens_query,
                tokens_reserved  = tokens_reserved,
                tokens_total     = tokens_fixed,
                budget_tokens    = 0,
                truncated        = bool(chunks),
            )

        # ── Greedily add chunks in rank order (All or Nothing) ────────────────
        kept:    list[DocumentChunk] = []
        dropped: list[DocumentChunk] = []
        running_chunk_tokens = 0
        truncated = False

        for chunk in chunks:
            chunk_tokens = _count_tokens(chunk.text)

            # ALL OR NOTHING FIT - Prevents slicing lists in half
            if running_chunk_tokens + chunk_tokens <= chunk_budget:
                kept.append(chunk)
                running_chunk_tokens += chunk_tokens
            else:
                # If it doesn't fit, drop it and all remaining lower-ranked chunks
                dropped.append(chunk)
                truncated = True
                
                # Grab the rest of the chunks and dump them in dropped
                if len(kept) + len(dropped) < len(chunks):
                    remaining = chunks[len(kept) + len(dropped):]
                    dropped.extend(remaining)
                break 

        tokens_total = tokens_fixed + running_chunk_tokens

        logger.debug(
            "Token budget computed",
            extra={
                "n_ctx":            self._n_ctx,
                "chunk_budget":     chunk_budget,
                "tokens_system":    tokens_system,
                "tokens_query":     tokens_query,
                "tokens_reserved":  tokens_reserved,
                "tokens_chunks":    running_chunk_tokens,
                "tokens_total":     tokens_total,
                "kept":             len(kept),
                "dropped":          len(dropped),
                "truncated":        truncated,
            },
        )

        return BudgetResult(
            kept_chunks      = kept,
            dropped_chunks   = dropped,
            tokens_system    = tokens_system,
            tokens_query     = tokens_query,
            tokens_chunks    = running_chunk_tokens,
            tokens_reserved  = tokens_reserved,
            tokens_total     = tokens_total,
            budget_tokens    = chunk_budget,
            truncated        = truncated,
        )

    # ── Convenience: estimate prompt tokens without fitting ───────────────────

    def estimate(self, system_prompt: str, user_query: str, chunks: list[DocumentChunk]) -> int:
        return (
            _count_tokens(system_prompt)
            + _count_tokens(user_query)
            + sum(_count_tokens(c.text) for c in chunks)
            + _SCAFFOLD_TOKENS
            + self._max_tokens
        )


# ── FastAPI dependency ────────────────────────────────────────────────────────

_token_budget_singleton: TokenBudgetManager | None = None


def get_token_budget_manager() -> TokenBudgetManager:
    """Return the application-scoped TokenBudgetManager singleton."""
    global _token_budget_singleton
    if _token_budget_singleton is None:
        _token_budget_singleton = TokenBudgetManager()
    return _token_budget_singleton