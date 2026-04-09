# Cortana AI OS — Architecture

## Overview

Cortana AI OS is a self-hosted, multi-tier AI agent platform for airline maintenance operations. Each tier is an independent Docker stack combining n8n (workflow engine), Qdrant (vector database), and a document ingestion service.

```
┌─────────────────────────────────────────────────────────────────┐
│                        TELEGRAM BOT                             │
│              (one bot per customer deployment)                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │       n8n Cloud      │
                    │  (Workflow Engine)   │
                    │  Webhook Trigger     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐ ┌───────▼──────┐ ┌──────▼────────┐
    │  GPT-4.1 Agent │ │ GPT-4.1 Agent│ │ GPT-4.1 Agent │
    │   Technician   │ │  Operations  │ │ Master Chief  │
    └─────────┬──────┘ └───────┬──────┘ └──────┬────────┘
              │                │                │
    ┌─────────▼──────┐ ┌───────▼──────┐ ┌──────▼────────┐
    │    Qdrant DB   │ │   Qdrant DB  │ │   Qdrant DB   │
    │  Fleet manuals │ │Institutional │ │  All 4 KBs    │
    │ (AMM/CMM/WDM)  │ │ (MOE/SMS/MEL)│ │  Full access  │
    └────────────────┘ └──────────────┘ └───────────────┘
```

## Stack Components

Each tier runs as an isolated Docker Compose stack:

| Service | Image | Purpose |
|---|---|---|
| `n8n` | `n8nio/n8n:1.93.0` | Workflow engine — routes messages, runs AI agent, handles Telegram I/O |
| `qdrant` | `qdrant/qdrant:v1.13.4` | Vector database — stores embedded document chunks |
| `split-pdf` | custom build | Document ingestion service — chunks, embeds, stores in Qdrant |
| `postgres` | `postgres:16-alpine` | n8n backend database |
| `n8n-init` | `n8nio/n8n:1.93.0` | One-shot init container — imports workflow on first boot |

## Document Ingestion Pipeline

```
User uploads PDF/image via Telegram
          │
          ▼
  n8n: Is Document?
          │
          ▼
  Get file from Telegram
          │
          ▼
  POST to split-pdf service
    (with X-CF-Token auth)
          │
          ▼
  split-pdf: pymupdf extract
    → overlapping chunks (2000 chars / 400 overlap)
    → Gemini Embedding 2 (3072 dims)
          │
          ▼
  Qdrant: upsert vectors
    (dedup by source_file_id)
          │
          ▼
  Telegram: confirmation reply
```

## Embedding Model

All tiers use **Gemini Embedding 2** (`gemini-embedding-2-preview`, 3072 dimensions, Cosine distance).

This model must be consistent across:
- Document ingestion (split-pdf service)
- Query time (n8n Qdrant retrieval nodes)

**Do not change the embedding model after ingestion** — dimension mismatch breaks retrieval.

## Voice I/O

The agent supports voice messages via Telegram:

```
Telegram voice → Whisper STT → AI Agent → OpenAI TTS (nova) → Telegram audio
```

- STT: OpenAI Whisper (`whisper-1`) — handles OGG/Opus natively
- TTS: OpenAI TTS (`tts-1-hd`, voice `nova`) — natural female voice, auto-detects pt-BR/en-US

## Security

| Layer | Mechanism |
|---|---|
| Telegram | User ID whitelist in `Check Authorization` node |
| split-pdf service | `X-CF-Token` header (set via `CF_AUTH_TOKEN` env var) |
| n8n API | `N8N_ENCRYPTION_KEY` encrypts credentials at rest |
| Qdrant | No auth for local (Docker network only) — add `QDRANT__SERVICE__API_KEY` for cloud |

## Ports (Local Multi-Tier Testing)

| Tier | n8n | Qdrant |
|---|---|---|
| Technician | 5678 | 6333 |
| Operations | 5679 | 6334 |
| Master Chief | 5680 | 6335 |
