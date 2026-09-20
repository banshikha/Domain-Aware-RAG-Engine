"""
app/domain/medical/prompt_template.py
───────────────────────────────────────
Domain-specific prompt templates for the medical domain.

Template hierarchy:
  1. SYSTEM_PROMPT       — static, injected at the start of every request.
  2. render()            — combines system prompt + context + user query.
  3. Post-processing guards:
       • ensure_disclaimer()      — appends the short medical disclaimer.
       • inject_safety_referral() — detects sensitive topics (localized for India).
       • redact_phi_reminder()    — reminds the model not to echo PHI.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from app.core.config import Domain

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Disclaimer text (Shortened & Localized for India)
# ══════════════════════════════════════════════════════════════════════════════

_DISCLAIMER = (
    "MEDICAL DISCLAIMER: This information is for educational purposes only and is "
    "not medical advice. Consult a healthcare professional before making clinical decisions. "
    "In an emergency, call 108 or 112 immediately."
)

# Crisis / mental-health referral (India)
_MENTAL_HEALTH_REFERRAL = (
    "If you or someone you know is in distress, please reach out for help. "
    "In India, you can call the Kiran Helpline at 1800-599-0019 or emergency services at 112."
)

# Drug prescribing-information reference requirement
_PRESCRIBING_INFO_NOTE = (
    "For complete prescribing information, dosage adjustments, contraindications, "
    "and drug interactions, refer to the officially approved prescribing information "
    "or consult a licensed pharmacist or physician."
)

# ══════════════════════════════════════════════════════════════════════════════
# System prompt
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """You are a highly accurate, professional medical information assistant with \
expertise in clinical guidelines, biomedical research literature, and pharmacology.

## Core Behavioural Rules

1. **Informational only — never diagnostic**:
   You provide information as described in the retrieved documents ONLY. You DO NOT diagnose, \
prescribe, or recommend treatments. If asked for a personal diagnosis, respond concisely: \
"I cannot provide a personal medical diagnosis or treatment recommendation. Please consult a healthcare provider."

2. **Strict Grounding & Source Fidelity**:
   Base your answer SOLELY on the retrieved context. Reproduce numeric values (dosages, lab ranges) verbatim. \
If the context does not contain the answer, state that explicitly without guessing. NEVER use external knowledge.

3. **Clean, Cohesive Output (No Source Tags)**:
   Synthesize the information into clear, professional paragraphs. NEVER output raw citation tags \
like `[Source: ...]`, UUIDs, or internal document IDs. Integrate source names (e.g., specific guidelines) \
naturally only if helpful for context.

4. **Confident Tone & No Repetition**:
   Answer directly and confidently. DO NOT use weak, defensive meta-commentary (e.g., "The retrieved documents do not explain..." \
or "Based on the provided context..."). NEVER repeat the same sentence or concept twice. Keep answers concise and focused.

5. **Drug information protocol**:
   When describing a medication based on context, include the indication, dosage range, and major contraindications/interactions \
IF they are in the source. NEVER recommend a specific dose for an individual patient.

6. **PHI protection**:
   Do NOT echo, reference, or elaborate on any patient identifiers, case details, or demographics present in the context.

7. **Temporal precision**:
   State the guideline version or publication year if present in the text to ensure clinical relevance.

8. **No Disclaimers**:
   You are an internal analysis tool. DO NOT output ANY legal, medical, or advisory disclaimers natively. \
(The system will automatically append them later).
"""

# ══════════════════════════════════════════════════════════════════════════════
# Context block template
# ══════════════════════════════════════════════════════════════════════════════

# 🚀 THE FIX: Removed {document_id} completely to prevent UUID leaks.
_CONTEXT_BLOCK_TEMPLATE = """\
### Retrieved Document [{index}]
- **Source**: {source}
- **Clinical Section**: {section}
- **Date / Version**: {date}
- **Chunk type**: {chunk_type}
- **Detected entities**: {entities}
- **Relevance score**: {score:.4f}

{text}
"""

_CITATION_INSTRUCTION = (
    "Base your entire answer strictly on the provided context. "
    "Do NOT use bracketed citation tags (e.g., [Source: ...]). "
    "Synthesize the information into a cohesive, professional, concise, and readable clinical response. "
    "If the context does not contain the answer, state that explicitly without guessing."
)

# ══════════════════════════════════════════════════════════════════════════════
# Sensitive topic detectors
# ══════════════════════════════════════════════════════════════════════════════

_MENTAL_HEALTH_RE = re.compile(
    r"\b(suicid|self[- ]?harm|self[- ]?injur|eating\s+disorder|anorexia|bulimia|"
    r"overdos|substance\s+(use|abuse)|opioid|addiction|depression|anxiety|"
    r"psychosis|schizophrenia|bipolar)\b",
    re.I,
)

_DRUG_DOSAGE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|mL|IU|units?)\b",
    re.I,
)

_DIAGNOSIS_REQUEST_RE = re.compile(
    r"\b(do\s+i\s+have|am\s+i\s+(?:sick|ill|infected)|"
    r"is\s+this\s+(?:cancer|diabetes|covid)|"
    r"what(?:'s|\s+is)\s+wrong\s+with\s+me|"
    r"should\s+i\s+take|can\s+i\s+take)\b",
    re.I,
)


# ══════════════════════════════════════════════════════════════════════════════
# PromptTemplate implementation
# ══════════════════════════════════════════════════════════════════════════════

class MedicalPromptTemplate:
    """
    Builds prompts for the medical domain.
    """

    domain = Domain.MEDICAL

    # ── Protocol: system_prompt ───────────────────────────────────────────────

    def system_prompt(self) -> str:
        """Return the static medical system prompt."""
        return _SYSTEM_PROMPT

    # ── Protocol: render ──────────────────────────────────────────────────────

    def render(self, query: str, context_chunks: list[dict]) -> str:
        """Render the full prompt to send to the LLM."""
        context_section   = self._build_context_section(context_chunks)
        today             = date.today().isoformat()
        diagnosis_warning = self._diagnosis_guard(query)

        return f"""{_SYSTEM_PROMPT}

---

## Retrieved Clinical Context
The following {len(context_chunks)} document excerpt(s) were retrieved as \
relevant. Today's date is {today}.

{context_section}

---

## User Query

{query.strip()}
{diagnosis_warning}
{_CITATION_INSTRUCTION}
"""

    # ── Context section builder ───────────────────────────────────────────────

    def _build_context_section(self, chunks: list[dict]) -> str:
        if not chunks:
            return "_No relevant clinical documents were retrieved for this query._"

        blocks: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            meta       = chunk.get("metadata", {})
            source     = meta.get("source", meta.get("filename", "unknown"))
            section    = chunk.get("section") or meta.get("section", "N/A")
            doc_date   = meta.get("publication_date") or meta.get("date", "N/A")
            chunk_type = chunk.get("chunk_type", "narrative")
            score      = chunk.get("score", 0.0)
            text       = chunk.get("text", "").strip()
            entities   = ", ".join(chunk.get("entities", [])) or "none detected"

            # Prepend medication-precision note for drug chunks
            if chunk_type == "medication":
                text = (
                    "[MEDICATION ENTRY — dosage values are from the source document; "
                    "do not personalise for individual patients]\n\n" + text
                )

            block = _CONTEXT_BLOCK_TEMPLATE.format(
                index=i,
                source=source,
                section=section,
                date=doc_date,
                chunk_type=chunk_type,
                entities=entities,
                score=score,
                text=text,
            )
            blocks.append(block)

        return "\n".join(blocks)

    # ── Query-level diagnosis guard ───────────────────────────────────────────

    @staticmethod
    def _diagnosis_guard(query: str) -> str:
        if _DIAGNOSIS_REQUEST_RE.search(query):
            return (
                "\n⚠️ **Note to model**: This query appears to request a personal "
                "medical diagnosis or treatment recommendation. Respond according "
                "to Rule 1: provide general clinical information from the retrieved "
                "documents only, and direct the user to a licensed healthcare "
                "professional for personal medical decisions.\n"
            )
        return ""

    # ── Post-processing guards ────────────────────────────────────────────────

    @staticmethod
    def ensure_disclaimer(text: str) -> str:
        """Append the medical disclaimer if not already present."""
        if _DISCLAIMER[:40] not in text:
            return text.rstrip() + f"\n\n---\n{_DISCLAIMER}"
        return text

    @staticmethod
    def inject_safety_referral(text: str) -> str:
        """Append the mental-health crisis referral if sensitive content is detected."""
        if _MENTAL_HEALTH_RE.search(text):
            if _MENTAL_HEALTH_REFERRAL[:30] not in text:
                return text.rstrip() + f"\n\n{_MENTAL_HEALTH_REFERRAL}"
        return text

    @staticmethod
    def inject_prescribing_note(text: str) -> str:
        """Append the prescribing-information note if drug dosage values are detected."""
        if _DRUG_DOSAGE_RE.search(text):
            if _PRESCRIBING_INFO_NOTE[:30] not in text:
                return text.rstrip() + f"\n\n{_PRESCRIBING_INFO_NOTE}"
        return text

    @staticmethod
    def disclaimer() -> str:
        return _DISCLAIMER

    @staticmethod
    def mental_health_referral() -> str:
        return _MENTAL_HEALTH_REFERRAL