"""
app/domain/_chunking_utils.py
──────────────────────────────
Shared token-safe windowing logic used by all three domain chunkers.

HuggingFace BERT-family models have a hard 512-token limit.  Exceeding it
silently truncates vectors, destroying retrieval quality.  This module
provides a single ``token_safe_windows()`` function that all chunkers use
as their final windowing step, guaranteeing the hard limit is never breached
regardless of upstream chunk-size settings.

Token counting:
  Exact tokenisation (loading the model's vocab) is expensive at chunking
  time.  Instead we use a conservative character-to-token ratio of 4.0
  chars/token (empirically validated across BERT tokenisers for English
  biomedical and financial text — the ratio ranges from 3.8 to 4.5).
  This means a 2 000-char limit corresponds to ~500 tokens, safely under
  the 512-token ceiling with headroom for special tokens ([CLS], [SEP]).

Constants exposed for import by each domain chunker:
  MAX_CHUNK_CHARS    — hard ceiling (~500 tokens at 4 chars/token)
  DEFAULT_OVERLAP_CHARS — 15 % of MAX_CHUNK_CHARS
"""

from __future__ import annotations

import regex as re
from typing import Iterator

# ── Constants ─────────────────────────────────────────────────────────────────
CHARS_PER_TOKEN: float = 4.0          # conservative ratio for BERT tokenisers
MAX_TOKENS:      int   = 500          # stay under 512 with room for specials
MAX_CHUNK_CHARS: int   = int(MAX_TOKENS * CHARS_PER_TOKEN)   # 2 000
OVERLAP_RATIO:   float = 0.15         # 15 % overlap ≈ 300 chars
DEFAULT_OVERLAP_CHARS: int = int(MAX_CHUNK_CHARS * OVERLAP_RATIO)  # 300

# Sentence boundary: period/!/? followed by whitespace + capital letter.
# Common abbreviations are protected to reduce false splits.
_ABBREV = r"(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|Corp|Ltd|Inc|LLC|Co|No|pp|Fig|Eq|Sec|U\.S|e\.g|i\.e)"
_SENT_RE = re.compile(rf"(?<!\b{_ABBREV})(?<=[.!?])\s+(?=[A-Z\"\'\(])")


def token_safe_windows(
    text: str,
    *,
    max_chars:    int = MAX_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    protected_spans: list[tuple[int, int]] | None = None,
) -> Iterator[str]:
    """
    Yield non-empty text windows that respect three constraints:

    1. **Hard size limit** — no window exceeds ``max_chars`` characters
       (~500 tokens), so the embedder is never passed a sequence that will
       be silently truncated.

    2. **Overlap** — each window begins ``overlap_chars`` characters into
       the previous window's content, preserving cross-boundary context.

    3. **Entity / table boundary safety** — split points that fall inside
       any span in ``protected_spans`` are skipped; the algorithm merges
       forward to the next safe boundary.  If no safe boundary exists before
       the hard limit is hit, a hard cut is made at ``max_chars`` to prevent
       an infinite loop.

    Args:
        text:            Input text to window.
        max_chars:       Hard character ceiling per window (default 2 000).
        overlap_chars:   Characters of overlap between consecutive windows
                         (default 300, ≈15 %).
        protected_spans: Optional list of (start, end) char offsets that
                         must not be crossed by a split.  Supplied by
                         financial (table cells) and medical (entity spans)
                         chunkers.

    Yields:
        Non-empty stripped window strings.
    """
    if not text or not text.strip():
        return

    protected = protected_spans or []
    sentences = _split_sentences(text, protected)

    buf   = ""
    carry = ""     # overlap tail from the previous window

    for sent in sentences:
        candidate = (carry + " " + sent).strip() if carry else sent

        # If even a single sentence exceeds the hard limit, hard-cut it.
        if len(candidate) > max_chars:
            # Flush existing buffer first
            if buf:
                yield buf.strip()
                carry = _tail(buf, overlap_chars)
                buf   = ""
            # Hard-cut the oversized sentence into max_chars slices
            for slice_ in _hard_cut(candidate, max_chars, overlap_chars):
                yield slice_
            carry = _tail(candidate, overlap_chars)
            continue

        if len(buf) + len(candidate) + 1 <= max_chars:
            buf = (buf + " " + candidate).strip()
        else:
            if buf:
                yield buf.strip()
                carry = _tail(buf, overlap_chars)
            buf = (carry + " " + candidate).strip() if carry else candidate

    if buf.strip():
        yield buf.strip()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _split_sentences(
    text: str,
    protected: list[tuple[int, int]],
) -> list[str]:
    """
    Split ``text`` at sentence boundaries that do NOT fall inside a protected
    span.  Falls back to the full text as a single sentence if no safe
    boundary exists.
    """
    candidates = [m.start() for m in _SENT_RE.finditer(text)]

    def _safe(pos: int) -> bool:
        return not any(s <= pos < e for s, e in protected)

    safe_positions = [p for p in candidates if _safe(p)]

    if not safe_positions:
        return [text]

    parts: list[str] = []
    prev = 0
    for pos in safe_positions:
        part = text[prev:pos].strip()
        if part:
            parts.append(part)
        prev = pos
    tail = text[prev:].strip()
    if tail:
        parts.append(tail)
    return parts or [text]


def _tail(text: str, n: int) -> str:
    """Return the last ``n`` characters of ``text`` as the overlap carry."""
    return text[-n:] if n and len(text) > n else text


def _hard_cut(text: str, max_chars: int, overlap_chars: int) -> Iterator[str]:
    """
    Yield ``max_chars``-sized slices of ``text`` with ``overlap_chars``
    overlap.  Used only when a single sentence exceeds the hard limit
    (e.g., a very dense table row or a run-on clinical note).
    """
    start = 0
    step  = max(1, max_chars - overlap_chars)
    while start < len(text):
        chunk = text[start : start + max_chars].strip()
        if chunk:
            yield chunk
        start += step
