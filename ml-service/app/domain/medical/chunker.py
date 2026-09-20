"""
app/domain/medical/chunker.py
──────────────────────────────
Entity-aware document chunker for the medical domain.

Why medical documents need specialised chunking:
  Clinical text has two properties that break generic sentence splitters:

  1. **Structured clinical sections** — "Chief Complaint", "Assessment &
     Plan", "Medications", "Adverse Reactions".  Splitting across a section
     boundary produces chunks that lose the section context essential for
     answering questions like "what is the recommended dosage for X".

  2. **Critical multi-token medical entities** — "Type II Diabetes Mellitus",
     "HbA1c ≥ 6.5%", "metformin 500 mg twice daily", "COVID-19 mRNA vaccine
     BNT162b2".  A sentence splitter that breaks at periods or commas inside
     these phrases destroys the semantic unit.

Design — heuristic-only (no scispaCy / medspaCy):
  • Zero heavy-NLP dependencies; runs on any CPU instance at startup.
  • All patterns are compiled once at import time.
  • Accuracy sufficient for retrieval:  the goal is not NER annotation but
    *preventing splits inside entities*, which regex patterns handle well.

Pipeline:
  1. ``_parse_elements()``  — convert raw content to typed ``_Element`` list.
       Supports Unstructured.io JSON, plain text, and structured markup.
  2. ``_classify_section()`` — map element text to one of the known clinical
       section categories (SYMPTOMS, DIAGNOSIS, TREATMENT, MEDICATIONS,
       ADVERSE_REACTIONS, LAB_RESULTS, HISTORY, PROCEDURES, GENERAL).
  3. ``_build_chunks()``    — state machine that tracks current section,
       buffers narrative text, protects entity spans from being split, and
       flushes windows of ``chunk_size`` with ``chunk_overlap``.

Chunk metadata schema (each chunk dict):
  {
    "text":            str,
    "chunk_type":      str,   # "narrative" | "header" | "entity_block"
                              # | "medication" | "lab_result" | "list"
    "chunk_index":     int,
    "page_number":     int | None,
    "section":         str,   # clinical section label
    "section_raw":     str | None,  # verbatim header text from document
    "entities":        list[str],   # detected medical entities in this chunk
    "metadata":        dict,
  }
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterator

from app.core.config import Domain, get_settings
from app.core.observability import span

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Clinical section taxonomy
# ══════════════════════════════════════════════════════════════════════════════

class ClinicalSection:
    SYMPTOMS          = "Symptoms & Chief Complaint"
    DIAGNOSIS         = "Diagnosis & Assessment"
    TREATMENT         = "Treatment & Plan"
    MEDICATIONS       = "Medications & Dosage"
    ADVERSE_REACTIONS = "Adverse Reactions & Warnings"
    LAB_RESULTS       = "Lab Results & Diagnostics"
    HISTORY           = "Medical History"
    PROCEDURES        = "Procedures & Interventions"
    GENERAL           = "General"

# Maps regex patterns to section labels.
# Patterns are tried in order; first match wins.
_SECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(chief\s+complaint|presenting\s+(symptom|complaint)|symptoms?|signs?\s+and\s+symptoms?)\b", re.I), ClinicalSection.SYMPTOMS),
    (re.compile(r"\b(assessment|diagnos[ie]s|impression|differential|icd[-\s]?\d+)\b", re.I), ClinicalSection.DIAGNOSIS),
    (re.compile(r"\b(treatment\s+plan|plan|management|therapeutic|intervention|therapy)\b", re.I), ClinicalSection.TREATMENT),
    (re.compile(r"\b(medication|drug|prescription|dosage|dose|formulary|pharmacolog|NDC\b)\b", re.I), ClinicalSection.MEDICATIONS),
    (re.compile(r"\b(adverse\s+(reaction|event|effect)|side\s+effect|contraindication|warning|black\s+box|REMS\b)\b", re.I), ClinicalSection.ADVERSE_REACTIONS),
    (re.compile(r"\b(lab(oratory)?|result|finding|CBC|CMP|HbA1c|troponin|creatinine|eGFR|INR|TSH|culture|biopsy|patholog)\b", re.I), ClinicalSection.LAB_RESULTS),
    (re.compile(r"\b(history|past\s+medical|PMH|HPI|review\s+of\s+systems|ROS|family\s+history|social\s+history)\b", re.I), ClinicalSection.HISTORY),
    (re.compile(r"\b(procedure|surgery|operation|intervention|catheter|endoscop|radiolog|imaging|MRI|CT\s+scan|X-ray)\b", re.I), ClinicalSection.PROCEDURES),
]


def _classify_section(text: str) -> str:
    """Return the best-matching clinical section label for a header or paragraph."""
    for pattern, label in _SECTION_PATTERNS:
        if pattern.search(text):
            return label
    return ClinicalSection.GENERAL


# ══════════════════════════════════════════════════════════════════════════════
# Medical entity patterns
# ══════════════════════════════════════════════════════════════════════════════

# These patterns are used to (a) detect entity spans and (b) prevent splits
# inside them.  They are NOT intended as exhaustive NER — just enough to
# protect the most common multi-token clinical entities.

_ENTITY_PATTERNS: list[re.Pattern] = [
    # Drug + dosage:  "metformin 500 mg twice daily", "aspirin 81 mg"
    re.compile(
        r"\b[a-zA-Z][a-zA-Z\-]+\s+\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|mL|IU|units?|mmol)"
        r"(?:/\w+)?"                           # "/kg", "/day"
        r"(?:\s+(?:once|twice|three\s+times?|q\d+h|QD|BID|TID|QID|PRN|daily|weekly))?"
        , re.I
    ),
    # Lab values with units: "HbA1c 7.2%", "eGFR 45 mL/min/1.73m²"
    re.compile(
        r"\b(?:HbA1c|eGFR|INR|TSH|CRP|ESR|PSA|BMI|BP|HR|SpO2|PaO2|pH)"
        r"\s*[<>=≤≥]?\s*\d+(?:\.\d+)?\s*(?:%|mmHg|mL/min(?:/\S+)?|ng/[mµ]L|IU/L|mg/dL|mmol/L)?",
        re.I
    ),
    # Named diseases / conditions (common multi-token forms)
    re.compile(
        r"\b(?:Type\s+[12I]{1,3}\s+Diabetes(?:\s+Mellitus)?|"
        r"COVID-19|SARS-CoV-2|"
        r"Non-?Hodgkin(?:'s)?\s+Lymphoma|"
        r"Chronic\s+Kidney\s+Disease|"
        r"Congestive\s+Heart\s+Failure|"
        r"Acute\s+Myocardial\s+Infarction|"
        r"Atrial\s+Fibrillation|"
        r"Crohn(?:'s)?\s+Disease|"
        r"Multiple\s+Sclerosis|"
        r"Parkinson(?:'s)?\s+Disease|"
        r"Alzheimer(?:'s)?\s+Disease|"
        r"Systemic\s+Lupus\s+Erythematosus|"
        r"Rheumatoid\s+Arthritis)",
        re.I
    ),
    # ICD-10 codes: "E11.9", "J18.9"
    re.compile(r"\b[A-Z]\d{2}(?:\.\d{1,4})?\b"),
    # Drug brand names with formulation: "Jardiance 10 mg tablet"
    re.compile(
        r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)?\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|mL)"
        r"(?:\s+(?:tablet|capsule|injection|inhaler|patch|solution|suspension))?",
        re.I
    ),
    # Vaccine names: "BNT162b2", "mRNA-1273"
    re.compile(r"\b(?:BNT162b2|mRNA-1273|ChAdOx1|Ad26\.COV2\.S|JNJ-78436735)\b", re.I),
    # Gene / biomarker identifiers: "BRCA1", "HER2", "EGFR"
    re.compile(r"\b(?:BRCA[12]|HER[23]|EGFR|ALK|KRAS|BRAF|PD-L1|PD-1|TP53|MLH1)\b"),
]

# Sentence boundary: split on period/?/! followed by whitespace + capital,
# but NOT inside an entity span.
_SENT_BOUNDARY = re.compile(r"(?<=[.?!])\s+(?=[A-Z\"\(])")

# Section header detector: common clinical document header patterns
_HEADER_RE = re.compile(
    r"^(?:"
    r"(?:[A-Z][A-Z\s&/]{3,}:?\s*$)"           # ALL-CAPS header: "ASSESSMENT:"
    r"|(?:\d+\.\s+[A-Z][a-zA-Z\s]+:?\s*$)"    # "1. Chief Complaint:"
    r"|(?:#{1,3}\s+.+)"                        # Markdown heading
    r")",
    re.MULTILINE
)

# Medication list item: "• Metformin 500mg BID" or "- aspirin 81mg daily"
_MED_LIST_RE = re.compile(
    r"^[\s]*[-•·*]\s+[A-Za-z]",
    re.MULTILINE
)


# ══════════════════════════════════════════════════════════════════════════════
# Internal element representation
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _Element:
    kind:        str            # "header" | "narrative" | "list" | "lab"
    text:        str
    page_number: int | None = None
    section_raw: str | None = None   # verbatim text if kind == "header"


# ══════════════════════════════════════════════════════════════════════════════
# Chunker implementation
# ══════════════════════════════════════════════════════════════════════════════

class MedicalChunker:
    """
    Entity-aware chunker for medical / clinical documents.

    Implements the ``Chunker`` protocol defined in ``adapter.py``.

    Key behaviours:
      • Clinical sections are detected and propagated into chunk metadata.
      • Multi-token medical entities are never split across chunk boundaries.
      • Drug+dosage, condition, and lab-value spans are preserved atomically.
      • Medication list items form their own ``medication`` chunks for
        precision retrieval (dosage queries).
      • Narrative text is windowed with overlap to maintain context continuity.
    """

    domain = Domain.MEDICAL

    def __init__(self) -> None:
        settings            = get_settings()
        self._chunk_size    = settings.chunk_size_medical    # default 384
        self._chunk_overlap = settings.chunk_overlap         # default 32
        logger.debug(
            "MedicalChunker initialised",
            extra={
                "chunk_size":    self._chunk_size,
                "chunk_overlap": self._chunk_overlap,
            },
        )

    # ── Protocol: chunk ───────────────────────────────────────────────────────

    def chunk(self, content: str, metadata: dict) -> list[dict]:
        """
        Split ``content`` into clinically coherent chunks.

        Returns a list of chunk dicts (schema documented at module level).
        """
        with span("medical.chunker.chunk", domain="medical"):
            elements = self._parse_elements(content)
            chunks   = list(self._build_chunks(elements, metadata))
            logger.debug(
                "MedicalChunker produced chunks",
                extra={
                    "n_chunks": len(chunks),
                    "doc_id":   metadata.get("document_id"),
                },
            )
            return chunks

    # ── Element parser ────────────────────────────────────────────────────────

    def _parse_elements(self, content: str) -> list[_Element]:
        """Dispatch to the correct parser based on detected content format."""
        stripped = content.lstrip()

        # ── Unstructured.io JSON ───────────────────────────────────────────────
        if stripped.startswith("["):
            try:
                import json
                raw = json.loads(content)
                return [self._unstructured_to_element(e) for e in raw]
            except (json.JSONDecodeError, KeyError):
                pass

        # ── Plain text fallback ────────────────────────────────────────────────
        return self._parse_plain_text(content)

    def _unstructured_to_element(self, raw: dict) -> _Element:
        kind_map = {
            "Title":         "header",
            "Header":        "header",
            "NarrativeText": "narrative",
            "ListItem":      "list",
            "Table":         "narrative",   # tables in medical → treat as narrative
            "UncategorizedText": "narrative",
        }
        raw_type = raw.get("type", "UncategorizedText")
        kind     = kind_map.get(raw_type, "narrative")
        text     = raw.get("text", "").strip()
        page     = raw.get("metadata", {}).get("page_number")
        return _Element(
            kind=kind,
            text=text,
            page_number=page,
            section_raw=text if kind == "header" else None,
        )

    def _parse_plain_text(self, content: str) -> list[_Element]:
        """Heuristically classify paragraphs into element types."""
        elements: list[_Element] = []
        paragraphs = re.split(r"\n{2,}", content.strip())

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if _HEADER_RE.match(para):
                elements.append(_Element(kind="header", text=para, section_raw=para))
            elif _MED_LIST_RE.match(para):
                # Split medication list into individual items
                items = re.split(r"\n(?=\s*[-•·*])", para)
                for item in items:
                    item = item.strip()
                    if item:
                        elements.append(_Element(kind="list", text=item))
            else:
                elements.append(_Element(kind="narrative", text=para))

        return elements

    # ── Chunk builder ─────────────────────────────────────────────────────────

    def _build_chunks(
        self, elements: list[_Element], base_meta: dict
    ) -> Iterator[dict]:
        """
        State machine that converts elements to chunks.

        State tracked:
          • ``current_section`` — clinical section label (propagated to metadata)
          • ``section_raw``     — verbatim header text
          • ``narrative_buf``   — accumulates narrative sentences
          • ``chunk_index``     — monotonically increasing per document
        """
        chunk_index     = 0
        current_section = ClinicalSection.GENERAL
        section_raw: str | None = None
        narrative_buf: list[str] = []

        def _flush() -> Iterator[dict]:
            nonlocal chunk_index, narrative_buf
            if not narrative_buf:
                return
            combined = " ".join(narrative_buf)
            for window in self._window_text(combined, current_section):
                entities = _extract_entities(window)
                yield _make_chunk(
                    text=window,
                    chunk_type="narrative",
                    chunk_index=chunk_index,
                    section=current_section,
                    section_raw=section_raw,
                    entities=entities,
                    metadata=base_meta,
                )
                chunk_index += 1
            narrative_buf.clear()

        for elem in elements:

            if elem.kind == "header":
                yield from _flush()
                current_section = _classify_section(elem.text)
                section_raw     = elem.section_raw or elem.text
                # Emit the header as its own chunk for section-level queries
                yield _make_chunk(
                    text=elem.text,
                    chunk_type="header",
                    chunk_index=chunk_index,
                    section=current_section,
                    section_raw=section_raw,
                    entities=[],
                    metadata=base_meta,
                    page_number=elem.page_number,
                )
                chunk_index += 1

            elif elem.kind == "list":
                # Each list item (medication line) becomes its own chunk to
                # maximise precision on dosage-lookup queries.
                yield from _flush()
                entities = _extract_entities(elem.text)
                # Determine if this is a medication line
                chunk_type = (
                    "medication"
                    if current_section == ClinicalSection.MEDICATIONS
                    or any(_ENTITY_PATTERNS[0].search(elem.text) for _ in [1])
                    else "list"
                )
                yield _make_chunk(
                    text=elem.text,
                    chunk_type=chunk_type,
                    chunk_index=chunk_index,
                    section=current_section,
                    section_raw=section_raw,
                    entities=entities,
                    metadata=base_meta,
                    page_number=elem.page_number,
                )
                chunk_index += 1

            else:
                # Narrative: accumulate into buffer; flush when full
                narrative_buf.append(elem.text)
                buf_len = sum(len(t) for t in narrative_buf)
                if buf_len >= self._chunk_size:
                    yield from _flush()

        yield from _flush()

    # ── Entity-safe text windowing ────────────────────────────────────────────

    def _window_text(self, text: str, section: str) -> Iterator[str]:
        """
        Yield overlapping windows of ``chunk_size`` characters, split on
        sentence boundaries — but never inside a detected entity span.

        Algorithm:
          1. Tokenise into sentences using ``_SENT_BOUNDARY``.
          2. Before accepting a split point, check whether it falls inside
             any entity span (via ``_entity_spans``).  If it does, merge
             the next sentence into the current buffer instead.
          3. When the buffer exceeds ``chunk_size``, yield it and carry
             the last ``chunk_overlap`` characters forward.
        """
        sentences  = _safe_sentence_split(text, _entity_spans(text))
        buf        = ""
        carry      = ""

        for sent in sentences:
            candidate = (carry + " " + sent).strip() if carry else sent
            if len(buf) + len(candidate) + 1 <= self._chunk_size:
                buf = (buf + " " + candidate).strip()
            else:
                if buf:
                    yield buf
                    carry = buf[-self._chunk_overlap:] if self._chunk_overlap else ""
                buf = (carry + " " + candidate).strip() if carry else candidate

        if buf:
            yield buf


# ══════════════════════════════════════════════════════════════════════════════
# Entity extraction and span-safe sentence splitting
# ══════════════════════════════════════════════════════════════════════════════

def _entity_spans(text: str) -> list[tuple[int, int]]:
    """
    Return a list of (start, end) character offsets for all detected entity
    spans in ``text``.  These spans must not be crossed by sentence splits.
    """
    spans: list[tuple[int, int]] = []
    for pattern in _ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end()))
    # Sort and merge overlapping spans
    if not spans:
        return []
    spans.sort()
    merged: list[tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _safe_sentence_split(text: str, protected: list[tuple[int, int]]) -> list[str]:
    """
    Split ``text`` into sentences at ``_SENT_BOUNDARY`` positions that do
    NOT fall inside any of the ``protected`` spans.
    """
    candidate_splits = [m.start() for m in _SENT_BOUNDARY.finditer(text)]

    def _is_protected(pos: int) -> bool:
        return any(s <= pos < e for s, e in protected)

    safe_splits = [p for p in candidate_splits if not _is_protected(p)]

    if not safe_splits:
        return [text]

    sentences: list[str] = []
    prev = 0
    for split in safe_splits:
        part = text[prev:split].strip()
        if part:
            sentences.append(part)
        prev = split
    tail = text[prev:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _extract_entities(text: str) -> list[str]:
    """
    Return a deduplicated list of matched entity strings from ``text``.

    Used to populate the ``entities`` field in chunk metadata, enabling
    entity-based payload filtering in Qdrant without a full NER pipeline.
    """
    found: list[str] = []
    seen:  set[str]  = set()
    for pattern in _ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            entity = m.group(0).strip()
            lower  = entity.lower()
            if lower not in seen and len(entity) > 2:
                seen.add(lower)
                found.append(entity)
    return found


# ══════════════════════════════════════════════════════════════════════════════
# Chunk dict factory
# ══════════════════════════════════════════════════════════════════════════════

def _make_chunk(
    *,
    text:        str,
    chunk_type:  str,
    chunk_index: int,
    section:     str,
    section_raw: str | None,
    entities:    list[str],
    metadata:    dict,
    page_number: int | None = None,
) -> dict:
    """Construct a normalised medical chunk dict."""
    return {
        "text":        text.strip(),
        "chunk_type":  chunk_type,
        "chunk_index": chunk_index,
        "page_number": page_number,
        "section":     section,
        "section_raw": section_raw,
        "entities":    entities,
        "metadata": {
            **metadata,
            "chunk_type":  chunk_type,
            "chunk_index": chunk_index,
            "section":     section,
            "section_raw": section_raw,
            "entities":    entities,
            "page_number": page_number,
        },
    }
