"""
CHUNKER PATCHES
═══════════════
Drop-in replacements for the specific methods that enforce the hard
MAX_CHUNK_CHARS / overlap ceiling in the Financial and Medical chunkers.

Apply by replacing the named methods in-place.  No other logic changes.
All other methods in both files remain identical.
"""

# ══════════════════════════════════════════════════════════════════════════════
# FILE: app/domain/financial/chunker.py
# ══════════════════════════════════════════════════════════════════════════════
#
# 1. ADD this import at the top of the file (after existing imports):
#
#   from app.domain._chunking_utils import (
#       token_safe_windows,
#       MAX_CHUNK_CHARS,
#       DEFAULT_OVERLAP_CHARS,
#   )
#
# 2. REPLACE __init__ with the version below.
# 3. REPLACE _split_table with the version below.
# 4. REPLACE the _sliding_window module-level function with the stub below.
# ──────────────────────────────────────────────────────────────────────────────

class FinancialChunker_PATCH:
    """Patch methods only — not a standalone class."""

    # ── REPLACE: __init__ ────────────────────────────────────────────────────
    def __init__(self) -> None:
        import logging
        from app.core.config import get_settings
        from app.domain._chunking_utils import MAX_CHUNK_CHARS, DEFAULT_OVERLAP_CHARS

        logger = logging.getLogger(__name__)
        settings = get_settings()

        # Honour operator-configured chunk_size but never exceed the hard cap.
        self._chunk_size    = min(settings.chunk_size_financial, MAX_CHUNK_CHARS)
        self._chunk_overlap = min(
            settings.chunk_overlap,
            int(self._chunk_size * 0.20),   # clamp overlap to 20 % of window
        )
        # Tables: allow up to 4× the narrative window, still capped at hard limit.
        self._max_table_chars = min(self._chunk_size * 4, MAX_CHUNK_CHARS)

        logger.debug(
            "FinancialChunker initialised",
            extra={
                "chunk_size":      self._chunk_size,
                "chunk_overlap":   self._chunk_overlap,
                "max_table_chars": self._max_table_chars,
            },
        )

    # ── REPLACE: _split_table ────────────────────────────────────────────────
    def _split_table(self, elem, start_index, section, base_meta):
        """
        Yield one or more chunks from a TABLE element, guaranteeing that no
        chunk exceeds MAX_CHUNK_CHARS.

        Strategy:
          • If the full table fits within ``max_table_chars`` (≤ MAX_CHUNK_CHARS),
            yield it as one atomic chunk.
          • Otherwise split by row windows; the header row is prepended to
            every split so the model always knows column semantics.
          • After row-splitting, each row window is passed through
            ``token_safe_windows()`` as a final hard-cap guard — handles the
            edge case of a single row being extremely wide (e.g., a table with
            many long text columns).
        """
        from app.domain._chunking_utils import token_safe_windows, MAX_CHUNK_CHARS

        rows = elem.raw_rows
        text = elem.text

        # ── Atomic path ───────────────────────────────────────────────────────
        if len(text) <= self._max_table_chars or not rows:
            # Final safety pass through hard-cap windowing
            windows = list(token_safe_windows(
                text,
                max_chars=MAX_CHUNK_CHARS,
                overlap_chars=self._chunk_overlap,
            ))
            for offset, window in enumerate(windows):
                row_range = f"0-{len(rows) - 1}" if rows and offset == 0 else None
                yield _make_chunk(
                    text=window,
                    chunk_type="table",
                    chunk_index=start_index + offset,
                    section=section,
                    metadata=base_meta,
                    page_number=elem.page_number,
                    table_id=elem.table_id,
                    row_range=row_range,
                )
            return

        # ── Row-split path ────────────────────────────────────────────────────
        header    = rows[0]
        data_rows = rows[1:]
        avg_row_chars = max(
            1,
            sum(len(" | ".join(r)) for r in data_rows) // max(len(data_rows), 1),
        )
        rows_per_chunk = max(1, self._max_table_chars // avg_row_chars)

        chunk_offset = 0
        for i in range(0, len(data_rows), rows_per_chunk):
            window      = data_rows[i : i + rows_per_chunk]
            chunk_rows  = [header] + window
            chunk_text  = _rows_to_pipe(chunk_rows)   # _rows_to_pipe is unchanged

            # Hard-cap guard: if even a single row window is too large, re-slice.
            sub_windows = list(token_safe_windows(
                chunk_text,
                max_chars=MAX_CHUNK_CHARS,
                overlap_chars=self._chunk_overlap,
            ))
            for sub_offset, sub_window in enumerate(sub_windows):
                yield _make_chunk(
                    text=sub_window,
                    chunk_type="table",
                    chunk_index=start_index + chunk_offset,
                    section=section,
                    metadata=base_meta,
                    page_number=elem.page_number,
                    table_id=elem.table_id,
                    row_range=f"{i + 1}-{i + len(window)}",
                )
                chunk_offset += 1


# Module-level _sliding_window in financial/chunker.py
# ── REPLACE with this stub that delegates to the shared utility ───────────────
def _sliding_window_FINANCIAL(text, size, overlap):
    """
    Drop-in replacement for the module-level ``_sliding_window`` function.

    Delegates to ``token_safe_windows`` so the hard MAX_CHUNK_CHARS cap is
    enforced even when callers pass a ``size`` larger than the safe limit.

    Replace the existing ``_sliding_window`` function body with:

        from app.domain._chunking_utils import token_safe_windows, MAX_CHUNK_CHARS
        yield from token_safe_windows(
            text,
            max_chars=min(size, MAX_CHUNK_CHARS),
            overlap_chars=overlap,
        )
    """
    from app.domain._chunking_utils import token_safe_windows, MAX_CHUNK_CHARS
    yield from token_safe_windows(
        text,
        max_chars=min(size, MAX_CHUNK_CHARS),
        overlap_chars=overlap,
    )


# ══════════════════════════════════════════════════════════════════════════════
# FILE: app/domain/medical/chunker.py
# ══════════════════════════════════════════════════════════════════════════════
#
# 1. ADD this import at the top of the file (after existing imports):
#
#   from app.domain._chunking_utils import (
#       token_safe_windows,
#       MAX_CHUNK_CHARS,
#       DEFAULT_OVERLAP_CHARS,
#   )
#
# 2. REPLACE __init__ with the version below.
# 3. REPLACE _window_text with the version below.
# ──────────────────────────────────────────────────────────────────────────────

class MedicalChunker_PATCH:
    """Patch methods only — not a standalone class."""

    # ── REPLACE: __init__ ────────────────────────────────────────────────────
    def __init__(self) -> None:
        import logging
        from app.core.config import get_settings
        from app.domain._chunking_utils import MAX_CHUNK_CHARS, DEFAULT_OVERLAP_CHARS

        logger = logging.getLogger(__name__)
        settings = get_settings()

        # Honour operator-configured size but clamp to hard cap.
        self._chunk_size    = min(settings.chunk_size_medical, MAX_CHUNK_CHARS)
        self._chunk_overlap = min(
            settings.chunk_overlap,
            int(self._chunk_size * 0.20),
        )
        logger.debug(
            "MedicalChunker initialised",
            extra={
                "chunk_size":    self._chunk_size,
                "chunk_overlap": self._chunk_overlap,
            },
        )

    # ── REPLACE: _window_text ─────────────────────────────────────────────────
    def _window_text(self, text: str, section: str):
        """
        Yield overlapping windows of at most MAX_CHUNK_CHARS characters,
        never splitting inside a detected medical entity span.

        Delegates the hard-cap + overlap logic to ``token_safe_windows``
        from ``_chunking_utils``, passing in the entity-protected spans so
        the shared utility respects medical entity boundaries.
        """
        from app.domain._chunking_utils import token_safe_windows, MAX_CHUNK_CHARS

        # Compute entity spans for this specific text block so the windowing
        # function knows which character ranges must not be crossed.
        protected = _entity_spans(text)   # _entity_spans is unchanged

        yield from token_safe_windows(
            text,
            max_chars=min(self._chunk_size, MAX_CHUNK_CHARS),
            overlap_chars=self._chunk_overlap,
            protected_spans=protected,
        )
