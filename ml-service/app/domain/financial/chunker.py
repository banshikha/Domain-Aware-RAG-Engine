"""
app/domain/financial/chunker.py
────────────────────────────────
Table-aware document chunker for the financial domain.
"""

from __future__ import annotations

import hashlib
import logging
import re
import textwrap
from dataclasses import dataclass, field
from typing import Iterator

from app.core.config import Domain, get_settings
from app.core.observability import span

logger = logging.getLogger(__name__)

# ── Element type constants ────────────────────────────────────────────────────
_T_TABLE   = "table"
_T_NARR    = "narrative"
_T_HEADER  = "header"
_T_FOOT    = "footnote"
_T_LIST    = "list"

# ── Sentence boundary regex ───────────────────────────────────────────────────
# 🚀 THE FIX: Python `re` requires fixed-width negative lookbehinds. 
# We dynamically build a chain of fixed-width rules to bypass the limit.
_ABBREVS = [
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "vs", "etc",
    "Corp", "Ltd", "Inc", "LLC", "Co", "No", "pp", "Fig", "Eq", "Sec"
]
_ABBREV_LOOKBEHINDS = "".join(rf"(?<!\b{a}[.!?])" for a in _ABBREVS)

_SENT_RE = re.compile(
    rf"{_ABBREV_LOOKBEHINDS}(?<=[.!?])\s+(?=[A-Z\"\'\(])"
)

# ── Footnote detector ─────────────────────────────────────────────────────────
_FOOTNOTE_RE = re.compile(
    r"^\s*(?:\(\d+\)|\d+\.|\*{1,3}|†|‡)\s+",
    re.MULTILINE,
)

# ── Section header detector ───────────────────────────────────────────────────
_HEADER_RE = re.compile(
    r"^(?:PART\s+[IVXLC]+|Item\s+\d+[A-Z]?\.|Note\s+\d+|SECTION\s+\d+)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class _Element:
    """Intermediate representation of a document element before chunking."""
    kind:        str
    text:        str
    page_number: int | None = None
    table_id:    str | None = None
    raw_rows:    list[list[str]] = field(default_factory=list)  # for TABLE elements


class FinancialChunker:
    """
    Table-aware chunker for financial documents.
    """

    domain = Domain.FINANCIAL

    def __init__(self) -> None:
        settings = get_settings()
        self._chunk_size    = settings.chunk_size_financial    # default 512
        self._chunk_overlap = settings.chunk_overlap           # default 32
        self._max_table_chars = self._chunk_size * 4           # ~2 048 chars
        logger.debug(
            "FinancialChunker initialised",
            extra={
                "chunk_size":    self._chunk_size,
                "chunk_overlap": self._chunk_overlap,
            },
        )

    # ── Protocol: chunk ───────────────────────────────────────────────────────

    def chunk(self, content: str, metadata: dict) -> list[dict]:
        """
        Split ``content`` into financial-aware chunks.
        """
        with span("financial.chunker.chunk", domain="financial"):
            elements = self._parse_elements(content)
            chunks   = list(self._build_chunks(elements, metadata))
            logger.debug(
                "FinancialChunker produced chunks",
                extra={"n_chunks": len(chunks), "doc_id": metadata.get("document_id")},
            )
            return chunks

    # ── Element parser ────────────────────────────────────────────────────────

    def _parse_elements(self, content: str) -> list[_Element]:
        """
        Convert raw content into a list of typed ``_Element`` objects.
        """
        # ── Format 1: Unstructured.io JSON ────────────────────────────────────
        if content.lstrip().startswith("["):
            try:
                import json
                raw = json.loads(content)
                return [self._unstructured_to_element(e) for e in raw]
            except (json.JSONDecodeError, KeyError):
                pass  # fall through to text parsing

        # ── Format 2: Markdown / pipe-delimited table ─────────────────────────
        if "|" in content and content.count("|") > 4:
            rows = self._parse_markdown_table(content)
            if rows:
                table_id = _short_hash(content[:64])
                return [_Element(kind=_T_TABLE, text=content, table_id=table_id, raw_rows=rows)]

        # ── Format 3: Plain text ──────────────────────────────────────────────
        return self._parse_plain_text(content)

    def _unstructured_to_element(self, raw: dict) -> _Element:
        """Map one Unstructured.io element dict to our internal ``_Element``."""
        kind_map = {
            "Table":         _T_TABLE,
            "Title":         _T_HEADER,
            "NarrativeText": _T_NARR,
            "ListItem":      _T_LIST,
            "FigureCaption": _T_NARR,
            "Header":        _T_HEADER,
            "Footer":        _T_FOOT,
            "UncategorizedText": _T_NARR,
        }
        raw_type  = raw.get("type", "UncategorizedText")
        kind      = kind_map.get(raw_type, _T_NARR)
        text      = raw.get("text", "")
        page      = raw.get("metadata", {}).get("page_number")
        table_id  = _short_hash(text[:64]) if kind == _T_TABLE else None

        # Unstructured provides HTML for tables — convert to pipe-delimited
        if kind == _T_TABLE and raw.get("metadata", {}).get("text_as_html"):
            text = self._html_table_to_pipe(raw["metadata"]["text_as_html"]) or text

        rows: list[list[str]] = []
        if kind == _T_TABLE:
            rows = self._parse_markdown_table(text) or []

        return _Element(kind=kind, text=text, page_number=page, table_id=table_id, raw_rows=rows)

    def _parse_plain_text(self, content: str) -> list[_Element]:
        """Heuristically classify paragraphs in plain text."""
        elements: list[_Element] = []
        paragraphs = re.split(r"\n{2,}", content.strip())

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if _HEADER_RE.match(para):
                elements.append(_Element(kind=_T_HEADER, text=para))
            elif _FOOTNOTE_RE.match(para):
                elements.append(_Element(kind=_T_FOOT, text=para))
            elif "|" in para and para.count("|") > 4:
                rows = self._parse_markdown_table(para)
                tid  = _short_hash(para[:64])
                elements.append(_Element(kind=_T_TABLE, text=para, table_id=tid, raw_rows=rows or []))
            elif para.startswith(("•", "-", "*", "·")) or re.match(r"^\d+\.", para):
                elements.append(_Element(kind=_T_LIST, text=para))
            else:
                elements.append(_Element(kind=_T_NARR, text=para))

        return elements

    # ── Chunk builder ─────────────────────────────────────────────────────────

    def _build_chunks(
        self, elements: list[_Element], base_meta: dict
    ) -> Iterator[dict]:
        """Iterate over elements and yield chunk dicts."""
        chunk_index   = 0
        current_section: str | None = None
        narrative_buf: list[str]    = []
        last_table_chunk: dict | None = None

        def _flush_narrative() -> Iterator[dict]:
            nonlocal chunk_index, narrative_buf
            if not narrative_buf:
                return
            combined = " ".join(narrative_buf)
            for window in _sliding_window(combined, self._chunk_size, self._chunk_overlap):
                yield _make_chunk(
                    text=window,
                    chunk_type=_T_NARR,
                    chunk_index=chunk_index,
                    section=current_section,
                    metadata=base_meta,
                )
                chunk_index += 1
            narrative_buf.clear()

        for elem in elements:

            if elem.kind == _T_HEADER:
                yield from _flush_narrative()
                current_section = elem.text.strip()
                yield _make_chunk(
                    text=elem.text,
                    chunk_type=_T_HEADER,
                    chunk_index=chunk_index,
                    section=current_section,
                    metadata=base_meta,
                    page_number=elem.page_number,
                )
                chunk_index += 1

            elif elem.kind == _T_TABLE:
                yield from _flush_narrative()
                for table_chunk in self._split_table(
                    elem, chunk_index, current_section, base_meta
                ):
                    last_table_chunk = table_chunk
                    yield table_chunk
                    chunk_index += 1

            elif elem.kind == _T_FOOT:
                if last_table_chunk is not None:
                    last_table_chunk["text"] += "\n" + elem.text
                else:
                    yield from _flush_narrative()
                    yield _make_chunk(
                        text=elem.text,
                        chunk_type=_T_FOOT,
                        chunk_index=chunk_index,
                        section=current_section,
                        metadata=base_meta,
                        page_number=elem.page_number,
                    )
                    chunk_index += 1

            else:
                narrative_buf.append(elem.text)
                if sum(len(t) for t in narrative_buf) >= self._chunk_size:
                    yield from _flush_narrative()

        yield from _flush_narrative()

    # ── Table splitting ───────────────────────────────────────────────────────

    def _split_table(
        self,
        elem: _Element,
        start_index: int,
        section: str | None,
        base_meta: dict,
    ) -> Iterator[dict]:
        rows = elem.raw_rows
        text = elem.text

        if len(text) <= self._max_table_chars or not rows:
            yield _make_chunk(
                text=text,
                chunk_type=_T_TABLE,
                chunk_index=start_index,
                section=section,
                metadata=base_meta,
                page_number=elem.page_number,
                table_id=elem.table_id,
                row_range=f"0-{len(rows) - 1}" if rows else None,
            )
            return

        header     = rows[0]
        data_rows  = rows[1:]
        avg_row_chars = max(1, sum(len(" | ".join(r)) for r in data_rows) // max(len(data_rows), 1))
        rows_per_chunk = max(1, self._max_table_chars // avg_row_chars)

        chunk_offset = 0
        for i in range(0, len(data_rows), rows_per_chunk):
            window     = data_rows[i : i + rows_per_chunk]
            chunk_rows = [header] + window
            chunk_text = _rows_to_pipe(chunk_rows)
            yield _make_chunk(
                text=chunk_text,
                chunk_type=_T_TABLE,
                chunk_index=start_index + chunk_offset,
                section=section,
                metadata=base_meta,
                page_number=elem.page_number,
                table_id=elem.table_id,
                row_range=f"{i + 1}-{i + len(window)}",
            )
            chunk_offset += 1

    # ── HTML table converter ──────────────────────────────────────────────────

    @staticmethod
    def _html_table_to_pipe(html: str) -> str | None:
        try:
            from html.parser import HTMLParser

            class _TableParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.rows: list[list[str]] = []
                    self._current_row: list[str] = []
                    self._current_cell: list[str] = []
                    self._in_cell = False

                def handle_starttag(self, tag, attrs):
                    if tag in ("tr",):
                        self._current_row = []
                    elif tag in ("td", "th"):
                        self._current_cell = []
                        self._in_cell = True

                def handle_endtag(self, tag):
                    if tag in ("td", "th"):
                        self._current_row.append(" ".join(self._current_cell).strip())
                        self._in_cell = False
                    elif tag == "tr":
                        if self._current_row:
                            self.rows.append(self._current_row)

                def handle_data(self, data):
                    if self._in_cell:
                        self._current_cell.append(data.strip())

            parser = _TableParser()
            parser.feed(html)
            if not parser.rows:
                return None
            return _rows_to_pipe(parser.rows)
        except Exception as exc:
            logger.debug("HTML table parse failed", extra={"error": str(exc)})
            return None

    # ── Markdown table parser ─────────────────────────────────────────────────

    @staticmethod
    def _parse_markdown_table(text: str) -> list[list[str]] | None:
        lines = [l.strip() for l in text.splitlines() if l.strip().startswith("|")]
        if len(lines) < 2:
            return None

        rows: list[list[str]] = []
        for line in lines:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r"-+:?|-+", c) for c in cells if c):
                continue
            rows.append(cells)

        return rows if len(rows) >= 1 else None


# ── Standalone helpers ────────────────────────────────────────────────────────

def _make_chunk(
    *,
    text: str,
    chunk_type: str,
    chunk_index: int,
    section: str | None,
    metadata: dict,
    page_number: int | None = None,
    table_id: str | None = None,
    row_range: str | None = None,
) -> dict:
    return {
        "text":        text.strip(),
        "chunk_type":  chunk_type,
        "chunk_index": chunk_index,
        "page_number": page_number,
        "section":     section,
        "table_id":    table_id,
        "row_range":   row_range,
        "metadata": {
            **metadata,
            "chunk_type":  chunk_type,
            "chunk_index": chunk_index,
            "section":     section,
            "page_number": page_number,
        },
    }


def _sliding_window(text: str, size: int, overlap: int) -> Iterator[str]:
    sentences = _SENT_RE.split(text)
    buf   = ""
    saved = ""

    for sent in sentences:
        candidate = (saved + " " + sent).strip() if saved else sent
        if len(buf) + len(candidate) + 1 <= size:
            buf = (buf + " " + candidate).strip()
        else:
            if buf:
                yield buf
                saved = buf[-overlap:] if overlap else ""
            buf = (saved + " " + candidate).strip() if saved else candidate

    if buf:
        yield buf


def _rows_to_pipe(rows: list[list[str]]) -> str:
    lines = []
    for i, row in enumerate(rows):
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    return "\n".join(lines)


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]