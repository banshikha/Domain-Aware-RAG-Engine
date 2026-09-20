"""
app/domain/general/chunker.py
──────────────────────────────
Sentence-boundary chunker for the general domain.

Strategy:
  Clean, readable sentence-based windowing using the shared
  ``token_safe_windows`` utility.  No domain-specific heuristics.

  1. Parse content from Unstructured.io JSON or plain text into typed elements
     (header, narrative, list).
  2. Emit headers as single-sentence chunks so header-targeted queries rank
     them highly.
  3. Buffer narrative and list text; flush via ``token_safe_windows`` which
     enforces the MAX_CHUNK_CHARS hard limit (~500 tokens / 2 000 chars)
     with configurable overlap.
  4. Carry section context into each chunk's metadata.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterator

from app.core.config import Domain, get_settings
from app.core.observability import span
from app.domain._chunking_utils import (
    DEFAULT_OVERLAP_CHARS,
    MAX_CHUNK_CHARS,
    token_safe_windows,
)

logger = logging.getLogger(__name__)

# Section header detector: ALL-CAPS lines, markdown headings, or numbered items
_HEADER_RE = re.compile(
    r"^(?:[A-Z][A-Z\s]{3,}:?\s*$|#{1,3}\s+.+|\d+\.\s+[A-Z].+:?\s*$)",
    re.MULTILINE,
)

# List item detector
_LIST_RE = re.compile(r"^[\s]*[-•·*]\s+\S", re.MULTILINE)


@dataclass
class _Element:
    kind:        str   # "header" | "narrative" | "list"
    text:        str
    page_number: int | None = None


class GeneralChunker:
    """
    Sentence-boundary chunker for general-purpose documents.
    """

    domain = Domain.GENERAL

    def __init__(self) -> None:
        settings = get_settings()
        # Clamp to hard token-safe ceiling
        self._chunk_size    = min(settings.chunk_size_general, MAX_CHUNK_CHARS)
        # 🚀 FIX: Increased overlap to 30% to prevent lists from losing their introductory context
        self._chunk_overlap = min(
            settings.chunk_overlap,
            int(self._chunk_size * 0.30),
        )
        logger.debug(
            "GeneralChunker initialised",
            extra={
                "chunk_size":    self._chunk_size,
                "chunk_overlap": self._chunk_overlap,
            },
        )

    # ── Protocol: chunk ───────────────────────────────────────────────────────

    def chunk(self, content: str, metadata: dict) -> list[dict]:
        with span("general.chunker.chunk", domain="general"):
            elements = self._parse_elements(content)
            chunks   = list(self._build_chunks(elements, metadata))
            return chunks

    # ── Element parser ────────────────────────────────────────────────────────

    def _parse_elements(self, content: str) -> list[_Element]:
        if content.lstrip().startswith("["):
            try:
                import json
                raw = json.loads(content)
                return [self._unstructured_to_element(e) for e in raw]
            except (json.JSONDecodeError, KeyError):
                pass
        return self._parse_plain_text(content)

    def _unstructured_to_element(self, raw: dict) -> _Element:
        kind_map = {
            "Title":             "header",
            "Header":            "header",
            "NarrativeText":     "narrative",
            "ListItem":          "list",
            "Table":             "narrative",
            "UncategorizedText": "narrative",
        }
        kind = kind_map.get(raw.get("type", "UncategorizedText"), "narrative")
        text = raw.get("text", "").strip()
        page = raw.get("metadata", {}).get("page_number")
        return _Element(kind=kind, text=text, page_number=page)

    def _parse_plain_text(self, content: str) -> list[_Element]:
        """Classify paragraphs by simple pattern matching."""
        elements: list[_Element] = []
        for para in re.split(r"\n{2,}", content.strip()):
            para = para.strip()
            if not para:
                continue
            if _HEADER_RE.match(para):
                elements.append(_Element(kind="header", text=para))
            elif _LIST_RE.match(para):
                # 🚀 FIX: We no longer violently split bullet points. 
                # Keep the whole list block together so context is preserved!
                elements.append(_Element(kind="list", text=para))
            else:
                elements.append(_Element(kind="narrative", text=para))
        return elements

    # ── Chunk builder ─────────────────────────────────────────────────────────

    def _build_chunks(
        self, elements: list[_Element], base_meta: dict
    ) -> Iterator[dict]:
        chunk_index     = 0
        current_section: str | None = None
        buf: list[str]  = []

        def _flush() -> Iterator[dict]:
            nonlocal chunk_index, buf
            if not buf:
                return
            combined = "\n".join(buf) # Changed to newline to preserve visual lists
            for window in token_safe_windows(
                combined,
                max_chars=self._chunk_size,
                overlap_chars=self._chunk_overlap,
            ):
                yield _make_chunk(
                    text=window,
                    chunk_type="narrative",
                    chunk_index=chunk_index,
                    section=current_section,
                    metadata=base_meta,
                )
                chunk_index += 1
            buf.clear()

        for elem in elements:
            if elem.kind == "header":
                yield from _flush()
                current_section = elem.text.strip()
                yield _make_chunk(
                    text=elem.text,
                    chunk_type="header",
                    chunk_index=chunk_index,
                    section=current_section,
                    metadata=base_meta,
                    page_number=elem.page_number,
                )
                chunk_index += 1

            else:
                buf.append(elem.text)
                if sum(len(t) for t in buf) >= self._chunk_size:
                    yield from _flush()

        yield from _flush()


# ── Chunk dict factory ────────────────────────────────────────────────────────

def _make_chunk(
    *,
    text:        str,
    chunk_type:  str,
    chunk_index: int,
    section:     str | None,
    metadata:    dict,
    page_number: int | None = None,
) -> dict:
    return {
        "text":        text.strip(),
        "chunk_type":  chunk_type,
        "chunk_index": chunk_index,
        "page_number": page_number,
        "section":     section,
        "metadata": {
            **metadata,
            "chunk_type":  chunk_type,
            "chunk_index": chunk_index,
            "section":     section,
            "page_number": page_number,
        },
    }