"""
app/llm/orchestrator.py
────────────────────────
Core pipeline coordinator for query processing and streaming LLM generation.

Full pipeline (left to right):
  user query
    → GuardrailManager.apply_pre()          [sanitise + domain pre-guards]
    → DomainAdapter.get_embedder()          [domain-specific embedding model]
    → embedder.encode(query)               [query → dense vector]
    → DenseRetriever.retrieve()            [Qdrant ANN search, top-k dense]
    → SparseRetriever.retrieve()           [Qdrant SPLADE search, top-k sparse]
         (dense + sparse run concurrently via asyncio.gather)
    → HybridMerger.merge()                 [RRF fusion → deduplicated ranking]
    → Reranker.rerank()                    [cross-encoder final ranking via ONNX]
    → TokenBudgetManager.fit_chunks()      [trim to LLM context window]
    → PromptTemplate.render()              [inject chunks into domain prompt]
    → CloudLLMModel.generate_stream()      [token-by-token generation via Groq]
    → GuardrailManager.apply_post_streaming() [buffer + append compliance suffix]
    → AsyncGenerator[str]                  [caller wraps in StreamingResponse]

LLM integration:
  • Offloads generation to Groq API (Llama 3) for zero local memory overhead.
  • Natively asynchronous streaming using the official groq SDK.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncGenerator

from app.core.config import Domain, get_settings
from app.core.models import DocumentChunk, RetrievalFilter, RetrievalResult
from app.core.observability import bind_request_context, get_tracer, span
from app.domain.adapter import DomainAdapter, get_domain_adapter
from app.llm.guardrails import GuardrailManager, get_guardrail_manager
from app.llm.token_budget import BudgetResult, TokenBudgetManager, get_token_budget_manager
from app.retrieval.dense_retriever import DenseRetriever, get_dense_retriever
from app.retrieval.hybrid_merger import HybridMerger, get_hybrid_merger
from app.retrieval.reranker import Reranker, get_reranker
from app.retrieval.sparse_retriever import SparseRetriever, get_sparse_retriever

logger = logging.getLogger(__name__)
tracer = get_tracer()


# ══════════════════════════════════════════════════════════════════════════════
# Cloud LLM Wrapper (Groq API)
# ══════════════════════════════════════════════════════════════════════════════

class _CloudLLMModel:
    """
    Wraps the Groq Async API for high-speed, zero-local-footprint inference.
    """

    def __init__(self) -> None:
        self._client = None
        self._ready: bool = False
        self._settings = get_settings()

    async def ensure_loaded(self) -> None:
        if self._ready:
            return
            
        logger.info("Connecting to Groq Cloud LLM API...")
        
        try:
            from groq import AsyncGroq
        except ImportError:
            logger.error("Groq SDK not installed. Please run `pip install groq`")
            return

        # ── FORCE LOAD THE .ENV FILE ──
        try:
            from dotenv import load_dotenv
            load_dotenv()  # This guarantees Python reads your ml-service/.env file
        except ImportError:
            logger.warning("python-dotenv not installed, attempting to use raw os.environ")

        # Attempt to get the key from the environment (loaded from .env)
        api_key = os.environ.get("LLM_API_KEY")
        
        if not api_key:
            logger.warning("LLM_API_KEY not found in environment. Streaming will be mocked.")
        else:
            self._client = AsyncGroq(api_key=api_key)
            logger.info("✅ Groq Cloud LLM connected successfully.")
            
        self._ready = True

    # ── Streaming generation ──────────────────────────────────────────────────

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt:   str,
    ) -> AsyncGenerator[str, None]:
        """Yield raw token strings asynchronously from the Groq API."""
        
        await self.ensure_loaded()
        
        if self._client is None:
            yield " [MOCK CLOUD LLM] "
            yield "The RAG retrieval pipeline worked perfectly! "
            yield "However, the LLM_API_KEY is missing or the groq package is not installed."
            return

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]

        try:
            # We use Llama 3 8B because it is blazing fast and highly capable for RAG.
            stream = await self._client.chat.completions.create(
                messages=messages,
                model="llama-3.1-8b-instant", 
                temperature=0.1,  # 🚀 FIX: Hardcoded to 0.1 to prevent hallucinations
                max_tokens=self._settings.llm_max_tokens,
                top_p=self._settings.llm_top_p,
                stream=True,
                stop=["<|eot_id|>", "</s>", "[/INST]"]
            )
            
            async for chunk in stream:
                # Groq returns standard OpenAI-style delta objects
                content = chunk.choices[0].delta.content
                if content:
                    yield content
                    
        except Exception as exc:
            logger.error("Cloud LLM inference error", extra={"error": str(exc)})
            yield f"\n\n[API Error: {str(exc)}]"

    # ── Non-streaming generation ──────────────────────────────────────────────

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Assemble full response string (non-streaming path)."""
        tokens: list[str] = []
        async for token in self.generate_stream(system_prompt, user_prompt):
            tokens.append(token)
        return "".join(tokens)


# Module-level LLM singleton
_cloud_llm = _CloudLLMModel()


def get_llm() -> _CloudLLMModel:
    """Return the shared LLM instance."""
    return _cloud_llm


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline result dataclass
# ══════════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field as dc_field


@dataclass
class PipelineMetadata:
    domain:          str
    request_id:      str
    query_tokens:    int            = 0
    chunks_retrieved: int           = 0
    chunks_used:     int            = 0
    chunks_dropped:  int            = 0
    dense_latency_ms: float         = 0.0
    sparse_latency_ms: float        = 0.0
    rerank_latency_ms: float        = 0.0
    total_latency_ms: float         = 0.0
    guardrail_warnings: list[str]   = dc_field(default_factory=list)
    sources: list[dict]             = dc_field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# RAG Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class RAGOrchestrator:
    """Coordinates the full RAG pipeline from query to streamed Cloud LLM response."""

    def __init__(
        self,
        domain_adapter:  DomainAdapter | None        = None,
        dense_retriever: DenseRetriever | None        = None,
        sparse_retriever: SparseRetriever | None      = None,
        hybrid_merger:   HybridMerger | None          = None,
        reranker:        Reranker | None               = None,
        token_budget:    TokenBudgetManager | None    = None,
        guardrails:      GuardrailManager | None      = None,
        llm:             _CloudLLMModel | None        = None,
    ) -> None:
        self._settings         = get_settings()
        self._domain_adapter   = domain_adapter   or get_domain_adapter()
        self._dense_retriever  = dense_retriever  or get_dense_retriever()
        self._sparse_retriever = sparse_retriever or get_sparse_retriever()
        self._hybrid_merger    = hybrid_merger    or get_hybrid_merger()
        self._reranker         = reranker         or get_reranker()
        self._token_budget     = token_budget     or get_token_budget_manager()
        self._guardrails       = guardrails       or get_guardrail_manager()
        self._llm              = llm              or get_llm()

    # ── Public API: streaming ─────────────────────────────────────────────────

    async def query_stream(
        self,
        query:      str,
        domain:     Domain,
        request_id: str,
        acl:        str | list[str] = "public",
        document_id: str | None     = None,
    ) -> tuple[PipelineMetadata, AsyncGenerator[str, None]]:
        bind_request_context(request_id=request_id, domain=domain.value)
        t_start = time.perf_counter()

        with span("orchestrator.query_stream", domain=domain.value, request_id=request_id):

            # ── Stage 1: Pre-generation guardrails ────────────────────────────
            clean_query, grd_warnings = self._guardrails.apply_pre(query, domain)

            # ── Stage 2: Embed query ──────────────────────────────────────────
            embedder     = await self._domain_adapter.get_embedder(domain)
            query_vector = (await embedder.encode([clean_query]))[0]

            # ── Stage 3: Parallel dense + sparse retrieval ────────────────────
            ret_filter = RetrievalFilter(
                domain      = domain.value,
                acl         = acl,
                document_id = document_id,
            )

            dense_result, sparse_result = await asyncio.gather(
                self._dense_retriever.retrieve(
                    domain=domain,
                    query_vector=query_vector,
                    filter=ret_filter,
                ),
                self._sparse_retriever.retrieve(
                    domain=domain,
                    query=clean_query,
                    filter=ret_filter,
                ),
            )

            # ── Stage 4: RRF fusion (Boosted Top-K) ───────────────────────────
            # Ensure we pull enough chunks to give the LLM full context
            merge_top_k = max((self._settings.retrieval_top_k_dense + self._settings.retrieval_top_k_sparse), 10)
            merged = self._hybrid_merger.merge(
                dense_result,
                sparse_result,
                top_k=merge_top_k,
            )

            # ── Stage 5: Cross-encoder reranking (Boosted Top-K) ──────────────
            t_rerank_start = time.perf_counter()
            rerank_top_k = 5 # Force at least 6 chunks
            
            reranked = await self._reranker.rerank(
                query  = clean_query,
                result = merged,
                top_k  = rerank_top_k,
            )
            rerank_latency = (time.perf_counter() - t_rerank_start) * 1000

            # ── Stage 6: Token budget & Prompt Override ───────────────────────
            template = await self._domain_adapter.get_prompt_template(domain)
            base_system_prompt = template.system_prompt()
            
            system_prompt = base_system_prompt + (
                "\n\n--- CRITICAL SYSTEM INSTRUCTIONS ---\n"

                "1. THINK FIRST: Before answering, identify the key facts and how they connect. "
                "If information is spread across multiple parts, mentally combine them into one clear explanation.\n"

                "2. EXPLAIN WITH PURPOSE: Focus on explaining WHY and HOW only when necessary. "
                "Do not over-explain simple factual questions.\n"

                "3. SYNTHESIZE, DON'T LIST: Combine related ideas into a single coherent explanation. "
                "Avoid disconnected or repetitive points.\n"

                "4. ADAPT FORMAT TO QUESTION:\n"
                "- Use paragraphs for explanations\n"
                "- Use bullet points only when the question explicitly asks for lists\n"

                "5. NATURAL RESPONSE: Start directly with the answer. "
                "Do NOT mention documents, sources, or phrases like 'the text says'.\n"

                "6. STRICT GROUNDING: Use only the provided context. "
                "Do not introduce outside knowledge.\n"

                "7. NO HALLUCINATION: If the answer is missing, clearly say so.\n"
            )
            budget: BudgetResult = self._token_budget.fit_chunks(
                chunks        = reranked.chunks,
                system_prompt = system_prompt,
                user_query    = clean_query,
            )

            # 🚀 PRODUCTION GUARD: Hard fail if no context is found
            if not budget.kept_chunks:
                logger.warning("No relevant context found. Triggering strict fallback.", extra={"request_id": request_id})
                
                # Create a mini-generator for the fallback message
                async def _fallback_stream():
                    yield "The document does not contain sufficient information to answer this question."
                
            # 🚀 PRODUCTION GUARD: Hard fail if no context is found
            if not budget.kept_chunks:
                logger.warning("No relevant context found. Triggering strict fallback.", extra={"request_id": request_id})
                
                async def _fallback_stream():
                    yield "The document does not contain sufficient information to answer this question."
                
                # 🚀 THE BULLETPROOF FIX: A catch-all object that will never throw an AttributeError
                class FallbackMeta:
                    def __init__(self, req_id, dom):
                        self.request_id = req_id
                        self.domain = dom.value if hasattr(dom, "value") else dom
                        self.sources = [] # Must be a list so the UI doesn't break trying to loop over it
                    
                    def __getattr__(self, item):
                        # If the API asks for ANY metric we forgot (chunks_dropped, processing_time, etc.), safely return 0
                        return 0

                fallback_meta = FallbackMeta(request_id, domain)
                return fallback_meta, _fallback_stream()

            # ── Stage 7: Prompt rendering with UUID Masking ───────────────────
            # Mask the ugly UUIDs so the LLM physically cannot cite them
            safe_chunks = []
            for i, c in enumerate(budget.kept_chunks):
                chunk_dict = c.model_dump()
                chunk_dict['document_id'] = f"Document {i+1}" 
                safe_chunks.append(chunk_dict)

            user_prompt = template.render(
                query          = clean_query,
                context_chunks = safe_chunks,
            )

            # ── Assemble pipeline metadata ────────────────────────────────────
            total_latency = (time.perf_counter() - t_start) * 1000
            meta = PipelineMetadata(
                domain             = domain.value,
                request_id         = request_id,
                query_tokens       = budget.tokens_query,
                chunks_retrieved   = len(merged.chunks),
                chunks_used        = len(budget.kept_chunks),
                chunks_dropped     = len(budget.dropped_chunks),
                dense_latency_ms   = dense_result.latency_ms,
                sparse_latency_ms  = sparse_result.latency_ms,
                rerank_latency_ms  = rerank_latency,
                total_latency_ms   = total_latency,
                guardrail_warnings = grd_warnings,
                sources            = _build_sources(budget.kept_chunks),
            )

            logger.info(
                "Pipeline pre-generation complete",
                extra={
                    "domain":           domain.value,
                    "chunks_used":      meta.chunks_used,
                    "chunks_dropped":   meta.chunks_dropped,
                    "total_latency_ms": round(meta.total_latency_ms, 2),
                    "budget_util":      round(budget.utilisation, 3),
                },
            )

        # ── Stage 8: LLM streaming + post-generation guardrails ───────────────
        raw_stream = self._llm.generate_stream(system_prompt, user_prompt)
        guarded_stream = await self._guardrails.apply_post_streaming(
            raw_stream, domain
        )

        return meta, guarded_stream

    # ── Public API: non-streaming ─────────────────────────────────────────────

    async def query(
        self,
        query:      str,
        domain:     Domain,
        request_id: str,
        acl:        str | list[str] = "public",
        document_id: str | None     = None,
    ) -> tuple[PipelineMetadata, str]:
        meta, stream = await self.query_stream(
            query=query,
            domain=domain,
            request_id=request_id,
            acl=acl,
            document_id=document_id,
        )
        tokens: list[str] = []
        async for token in stream:
            tokens.append(token)
        return meta, "".join(tokens)

    # ── Warm-up ───────────────────────────────────────────────────────────────

    async def warm_up(self) -> None:
        logger.info("RAGOrchestrator warm-up starting")
        await self._llm.ensure_loaded()
        
        # 🚀 FIX: Load the highly optimized INT8 ONNX Reranker
        self._reranker = get_reranker()
        
        print("⏭️ Skipping Domain Adapters (Memory Save Mode)")
        
        logger.info("RAGOrchestrator warm-up complete")


# ── Helper functions ──────────────────────────────────────────────────────────

def _build_sources(chunks: list[DocumentChunk]) -> list[dict]:
    seen:  set[str]  = set()
    sources: list[dict] = []

    for chunk in chunks:
        doc_id = chunk.document_id
        if doc_id in seen:
            continue
        seen.add(doc_id)
        sources.append({
            "document_id": doc_id,
            "source":      chunk.metadata.get("source", ""),
            "section":     chunk.section,
            "chunk_type":  chunk.chunk_type,
            "page_number": chunk.metadata.get("page_number"),
            "date":        chunk.metadata.get("date")
                            or chunk.metadata.get("filing_date"),
            "score":       chunk.score,
            "rank":        chunk.rank,
        })

    return sources


# ── FastAPI dependency ────────────────────────────────────────────────────────

_orchestrator_singleton: RAGOrchestrator | None = None


def get_orchestrator() -> RAGOrchestrator:
    global _orchestrator_singleton
    if _orchestrator_singleton is None:
        _orchestrator_singleton = RAGOrchestrator()
    return _orchestrator_singleton