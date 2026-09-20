# Domain-Adaptive RAG System

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg"/>
  <img src="https://img.shields.io/badge/FastAPI-Backend-green"/>
  <img src="https://img.shields.io/badge/Next.js-Frontend-black"/>
  <img src="https://img.shields.io/badge/Qdrant-VectorDB-orange"/>
  <img src="https://img.shields.io/badge/Redis-Async-red"/>
  <img src="https://img.shields.io/badge/Groq-LLM-purple"/>
  <img src="https://img.shields.io/badge/License-MIT-yellow"/>
</p>

<p align="center">
Enterprise-grade Retrieval-Augmented Generation (RAG) system with dynamic domain adaptation, hybrid retrieval, strict grounding, and compliance-aware AI responses.
</p>

---

## Overview

Traditional RAG systems treat every document identically, which often results in:

- Broken financial tables
- Weak understanding of medical terminology
- Cross-domain contamination
- Hallucinated responses
- Generic non-compliant outputs

This project solves these problems by introducing **Domain-Adaptive Retrieval-Augmented Generation**, where the system dynamically changes its behavior depending on document type:

- General
- Financial
- Medical

The application physically isolates vector spaces, retrieval pipelines, embedding models, and prompt templates for each domain.

The system is designed with a strong emphasis on:

- Accuracy
- Security
- Compliance
- Scalability
- Low-latency streaming

---

# Demo

Add screenshots/GIFs here

```md
<img width="100%" src="assets/home.png">

<img width="100%" src="assets/chat.png">

<img width="100%" src="assets/upload.png">
```

---

# Features

## Core Features

### Domain-Adaptive Routing

Dynamically routes uploaded documents and user queries into separate pipelines:

- General → MiniLM embeddings
- Financial → FinBERT embeddings
- Medical → Domain-specific processing

---

### Hybrid Retrieval

Combines:

- Dense Semantic Search
- Sparse Keyword Search (SPLADE)

Benefits:

- Better recall
- Better precision
- Reduced hallucination

---

### Table-Aware Financial Chunking

Traditional chunkers destroy financial tables.

Custom logic:

- Preserves markdown table structure
- Maintains header relationships
- Prevents data loss

---

### Strict Grounding

LLM is forbidden from using external knowledge.

If context is unavailable:

```text
"I do not have sufficient information."
```

instead of hallucinating answers.

---

### Domain-Specific Guardrails

#### Financial

- SEC-compliant response formatting
- Controlled advisory outputs

#### Medical

- HIPAA-lite safety handling
- Localized emergency guidance

---

### Real-time Streaming

Uses:

- Server Sent Events (SSE)
- Groq API token streaming

Provides ChatGPT-like real-time response generation.

---

### Background Ingestion

Heavy tasks run asynchronously:

- OCR
- Chunking
- Embedding generation
- Vector insertion

UI remains responsive.

---

### UUID Redaction Layer

Internal database IDs are physically removed before prompt generation.

Prevents:

- Internal metadata leakage
- Unnecessary LLM exposure

---

### Post-processing Guardrails

Python intercepts generated output and appends:

- Compliance disclaimers
- Safety instructions
- Domain-specific messaging

without depending on the LLM.

---

# System Architecture

```text

                        ┌─────────────────┐
                        │     Next.js     │
                        │    Frontend     │
                        └────────┬────────┘
                                 │
                                 │ X-Domain Header
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │     FastAPI API      │
                     │      Gateway         │
                     └─────────┬────────────┘
                               │
                ┌──────────────┴───────────────┐
                │                              │
                ▼                              ▼

       ┌────────────────┐          ┌────────────────┐
       │ Background     │          │ Query Pipeline │
       │ Ingestion      │          │                │
       └───────┬────────┘          └────────┬───────┘
               │                            │
               ▼                            ▼

      ┌─────────────────┐        ┌─────────────────┐
      │ Domain Adapter  │        │ Hybrid Search   │
      │                 │        │ Dense + Sparse  │
      └────────┬────────┘        └────────┬────────┘
               │                          │
               ▼                          ▼

      ┌─────────────────┐       ┌─────────────────┐
      │ Embeddings      │       │ Qdrant          │
      │ MiniLM/FinBERT  │       │ Vector Database │
      └─────────────────┘       └─────────────────┘
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │ Groq LLM API   │
                                     └────────────────┘


```

---

# Tech Stack

## Frontend

- Next.js
- React
- TailwindCSS
- Lucide React
- JavaScript Fetch API

---

## Backend

- FastAPI
- Python 3.11+
- Uvicorn
- BackgroundTasks
- Pydantic

---

## AI/ML Stack

### Dense Embeddings

| Domain | Model |
|----------|--------|
| General | all-MiniLM-L6-v2 |
| Financial | FinBERT-tone |
| Medical | Domain-specific pipeline |

### Sparse Retrieval

- SPLADE

### LLM

- Groq API
- Llama-3 / Mixtral

---

## Databases

### Vector Database

- Qdrant

### State & Queue Management

- Redis

---

# Folder Structure

```bash

ml-service/
├── app/
│
├── api/v1/
│ ├── ingest.py
│ └── query.py
│
├── core/
│ ├── config.py
│ ├── qdrant_client.py
│
├── domain/
│ ├── adapter.py
│ ├── general/
│ ├── financial/
│ └── medical/
│
├── ingestion/
│
├── retrieval/
│
├── llm/
│
└── main.py


```

---

# Request Flow

## Upload Pipeline

### Step 1

User uploads PDF and selects domain.

↓

### Step 2

Frontend sends:

```http
POST /api/v1/ingest
X-Domain: financial
```

↓

### Step 3

Background task starts:

- File parsing
- Chunking
- Metadata tagging
- Embedding generation

↓

### Step 4

Vectors inserted into:

```text
rag_general
rag_financial
rag_medical
```

---

## Query Pipeline

### Step 1

User asks question

↓

### Step 2

Domain adapter activates:

- FinBERT
- MiniLM
- Medical pipeline

↓

### Step 3

Hybrid retrieval executes:

- Dense search
- Sparse search

↓

### Step 4

Token budget manager trims context

↓

### Step 5

Prompt rendering

↓

### Step 6

Groq streams final answer

---

# Major Challenges Solved

## Vector Dimension Mismatch

### Problem

```text
Expected: 384
Received: 768
```

Qdrant crashed because FinBERT dimensions differed from MiniLM.

### Solution

Implemented dynamic collection provisioning based on selected domain.

---

## Regex Lookbehind Failure

### Problem

Python regex:

```python
(?<!Mr|Corp)
```

caused:

```python
re.error:
look-behind requires fixed width pattern
```

### Solution

Generated dynamic fixed-width regex chains.

---

## Next.js Proxy Failure

### Problem

FormData values were silently stripped.

### Solution

Moved domain data into:

```http
X-Domain
```

custom header.

---

## LLM Metadata Leakage

### Problem

LLM leaked internal UUIDs.

### Solution

Physically scrubbed IDs before prompt creation.

---

# Security Features

- Strict grounding
- Internal metadata redaction
- Domain isolation
- Controlled LLM outputs
- Compliance guardrails
- CORS protection
- Redis task monitoring

---

# Performance Optimizations

- Async FastAPI architecture
- Background ingestion tasks
- Redis state caching
- Hybrid retrieval
- Token budgeting
- Groq ultra-fast streaming

---

# Installation

## Clone repository

```bash
git clone https://github.com/yourusername/domain-adaptive-rag.git

cd domain-adaptive-rag
```

---

## Backend Setup

```bash
pip install -r requirements.txt

uvicorn app.main:app --reload
```

---

## Frontend Setup

```bash
npm install

npm run dev
```

---

## Docker

```bash
docker-compose up --build
```

---

# Environment Variables

Create:

```bash
.env
```

Add:

```env

GROQ_API_KEY=

QDRANT_URL=

QDRANT_API_KEY=

REDIS_URL=

```

---

# Future Improvements

- Authentication & RBAC
- Multi-user document spaces
- Legal domain support
- Citation visualization
- Document summarization
- Fine-tuned domain models
- Kubernetes deployment
- Monitoring dashboard

---

# Resume Highlights

- Built a Domain-Adaptive RAG architecture with dynamic vector routing
- Implemented Hybrid Dense + Sparse Retrieval
- Created custom table-aware chunking logic
- Reduced hallucinations through deterministic grounding
- Built asynchronous ingestion pipelines
- Secured LLM outputs with metadata redaction and compliance guardrails

---

# Author

**Banshikha kumari**

GitHub: https://github.com/banshikha

LinkedIn:www.linkedin.com/in/banshikha-kumari-970a6b303

---

⭐ If you found this project useful, consider giving it a star.
