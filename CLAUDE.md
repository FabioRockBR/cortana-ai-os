# CLAUDE.md — Cortana AI OS

AI agent instructions for working with this repository.

## Project Overview

This is the **Cortana AI OS** — a self-hosted, multi-tier AI agent platform for airline maintenance operations. Three independent Docker stacks, each with a different AI persona scoped to a specific user group.

## Repository Structure

```
cortana-ai-os/
├── services/
│   └── split-pdf/        ← Document ingestion microservice (Flask + pymupdf + Gemini)
├── tiers/
│   ├── technician/       ← Tier 1: Line/hangar technicians, fleet manual access
│   ├── operations/       ← Tier 2: MCC/dispatch, institutional KB (MEL/MOE/SMS)
│   └── master-chief/     ← Tier 3: Executive/DOM, all KBs
│       ├── docker-compose.yml
│       ├── .env.template
│       ├── init/setup.sh
│       └── workflows/    ← n8n workflow JSON (imported on first boot)
├── tools/
│   └── batch_ingest.py   ← Bulk document loader
└── docs/
    ├── agents/           ← System prompts for each tier
    ├── architecture.md
    ├── tier-comparison.md
    └── setup.md
```

## Key Technical Decisions

### Embedding Model
All tiers use **Gemini Embedding 2** (`gemini-embedding-2-preview`, 3072 dims, Cosine).
- NEVER change this after documents have been ingested — dimension mismatch breaks retrieval
- Requires `GOOGLE_API_KEY` (Google AI Studio, not Google Cloud)

### AI Model
**GPT-4.1** (`gpt-4.1`) — chosen over o4-mini to avoid OOM from reasoning token accumulation in n8n's LangChain message buffer.

### n8n Workflow Import
Workflows are imported via `n8n import:workflow` CLI (not REST API) — writes directly to DB, no auth needed. This runs in the `n8n-init` one-shot container.

### split-pdf Service
- Entry point: `process_file` via `functions-framework`
- Auth: `X-CF-Token` header checked against `CF_AUTH_TOKEN` env var
- `QDRANT_API_KEY` is optional — empty string for local unauthenticated Qdrant
- Default collection: `aircraft_maintenance_kb_v2`

### Qdrant Healthcheck
The qdrant image has no curl/wget. Use TCP check:
```yaml
test: ["CMD-SHELL", "timeout 1 bash -c 'echo > /dev/tcp/localhost/6333' 2>/dev/null && echo ok || exit 1"]
```

### n8n localhost vs 127.0.0.1
The n8n Docker image does NOT resolve `localhost` in healthchecks. Use `127.0.0.1` explicitly.

## Tier Port Assignments (Local Multi-Tier)

| Tier | n8n | Qdrant |
|---|---|---|
| technician | 5678 | 6333 |
| operations | 5679 | 6334 |
| master-chief | 5680 | 6335 |

## Workflow JSON Conventions

Workflow JSONs in `tiers/*/workflows/` use placeholder tokens replaced by `init/setup.sh` on first boot:

| Placeholder | Replaced With |
|---|---|
| `__TELEGRAM_BOT_TOKEN__` | `$TELEGRAM_BOT_TOKEN` from `.env` |
| `__OPENAI_API_KEY__` | `$OPENAI_API_KEY` |
| `__GOOGLE_API_KEY__` | `$GOOGLE_API_KEY` |
| `__CF_AUTH_TOKEN__` | `$CF_AUTH_TOKEN` |
| `__SPLIT_PDF_URL__` | `http://split-pdf:8080` |
| `__QDRANT_URL__` | `http://qdrant:6333` |

## Common Operations

### Start a tier
```bash
cd tiers/technician
docker compose up -d
docker compose run --rm n8n-init
```

### Check all services healthy
```bash
docker compose ps
```

### View n8n logs
```bash
docker compose logs n8n -f
```

### Ingest documents
```bash
python tools/batch_ingest.py --dir /path/to/pdfs --url http://localhost:8099 --token $CF_AUTH_TOKEN --collection b737_ng_kb
```

### Update a system prompt
Edit the appropriate `docs/agents/tier-N-*.md`, then update the AI Agent node's `systemMessage` field in the n8n workflow canvas.

## Known Issues

| Issue | Workaround |
|---|---|
| n8n workflow shows missing credentials after import | Add credentials manually in n8n UI, then update workflow nodes |
| Qdrant free tier suspends after inactivity | Reactivate at cloud.qdrant.io if using cloud Qdrant |
| `functions-framework` not found | Use `CMD ["functions-framework", "--target=process_file"]` not `python main.py` |
