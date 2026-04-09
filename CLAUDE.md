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

## Automated Maintenance System

The system runs three layers of autonomous monitoring. All tools live in `tools/` and report to Telegram.

### Required `.env` additions for monitoring

```env
TELEGRAM_CHAT_ID=your_owner_chat_id        # Your personal chat ID (get from @userinfobot)
CORTANA_ACTIVE_TIERS=technician            # Space-separated list of active tiers
CORTANA_ENV_FILE=/path/to/cortana-ai-os/.env
```

---

### Layer 1 — Watchdog (every 5 minutes)
**Script:** `tools/watchdog.sh`  
**What it checks:** Docker container health, n8n `/healthz`, Qdrant `/healthz`, split-pdf auth response, disk usage  
**Action:** Auto-restarts unhealthy containers via `docker compose restart`  
**Telegram:** Immediate alert on failure + restore confirmation; silent hourly OK ping at `:07`  

**Install as system cron:**
```bash
crontab -e
# Add:
*/5 * * * * /bin/bash /path/to/cortana-ai-os/tools/watchdog.sh >> /tmp/cortana-watchdog.log 2>&1
```

---

### Layer 2 — Integrity Audit (every hour)
**Script:** `tools/integrity_audit.py`  
**What it checks:**
- Qdrant vector counts per collection — alerts if any drop >5% or fall below 100 vectors
- Baseline drift detection — compares against last known counts stored in `/tmp/cortana-qdrant-baseline.json`
- n8n execution error rate — alerts if >5 failures in last hour
- Postgres connectivity

**Telegram:** Alert on any anomaly; silent when all OK (use `--quiet` flag, which is the default in cron)

**Install as cron:**
```bash
# Hourly at :23
23 * * * * /usr/bin/python3 /path/to/cortana-ai-os/tools/integrity_audit.py --quiet >> /tmp/cortana-integrity.log 2>&1
```

**Manual run (with output):**
```bash
python3 tools/integrity_audit.py --tier technician
```

---

### Layer 3 — Claude Maintenance Agent (daily)
**What it does:** A Claude Code session runs daily, reads audit logs, performs intelligent analysis, executes maintenance tasks, and sends a full daily report to Telegram.

**Maintenance tasks performed:**
- Prune n8n executions older than 7 days (keeps Postgres lean)
- `VACUUM ANALYZE` on Postgres DB
- Create Qdrant collection snapshots (backup)
- Detect if `docs/agents/` system prompts changed and need n8n sync
- Rotate log files >5MB

**Install as cron:**
```bash
# Daily at 03:17
17 3 * * * /usr/bin/python3 /path/to/cortana-ai-os/tools/maintenance.py >> /tmp/cortana-maintenance.log 2>&1
```

**Install Claude-powered audit as durable cron (run once to register):**
```bash
claude -p "Run the Cortana AI OS integrity audit: check all active tiers in /path/to/cortana-ai-os, run tools/integrity_audit.py, review the output, investigate any anomalies by reading Docker logs, and send a Telegram report via tools/maintenance.py. Fix anything that can be fixed automatically."
```

---

### Claude Agent Maintenance Protocol

When Claude runs a maintenance session, it must follow this sequence:

#### Safety Check
1. Run `docker compose ps` for each active tier — verify all containers healthy
2. Run `python3 tools/integrity_audit.py` — review output for anomalies
3. Check `docker compose logs --tail=50` for each tier — look for recurring errors
4. Verify n8n workflows are active: `curl http://localhost:{port}/healthz`
5. Check Qdrant collection counts haven't dropped unexpectedly

#### Integrity Check
1. Read `/tmp/cortana-watchdog.log` last 100 lines — look for repeated restart loops
2. Read `/tmp/cortana-integrity.log` last 100 lines — look for persistent failures
3. Compare vector counts against baseline: `cat /tmp/cortana-qdrant-baseline.json`
4. Check if any `.env` vars are missing: cross-reference `.env.template` vs `.env`
5. Verify split-pdf service responds to auth: `curl -X POST http://localhost:8099 -H "X-CF-Token: WRONG" -d '{}' → should return 401`

#### Automated Fixes (apply without asking)
- Restart containers that have been restarting repeatedly: `docker compose restart [service]`
- Prune oversized execution logs: `python3 tools/maintenance.py --tier [tier]`
- If a container won't start: read its logs, identify root cause, fix config if possible

#### Escalate to Human (send Telegram alert and stop)
- Qdrant collection vectors dropped >20% (possible data loss)
- n8n workflow has been deactivated (not just unhealthy — deactivated)
- Postgres data directory corruption
- `CF_AUTH_TOKEN` or `OPENAI_API_KEY` missing from running container env
- Same container restarting >10 times in last hour with no fix

#### System Prompt Sync
When `docs/agents/tier-N-*.md` has been modified:
1. Extract the system prompt block from the markdown (text between ` ``` ` fences under `## System Prompt`)
2. Update the AI Agent node's `systemMessage` via n8n REST API:
   ```bash
   # Get workflow ID
   curl http://localhost:5678/api/v1/workflows -H "X-N8N-API-KEY: $N8N_API_KEY"
   # Update agent system message (use n8n UI if API PUT is unreliable for large payloads)
   ```
3. If workflow is >25KB, use n8n canvas UI to update — large PUT requests can timeout

---

### Telegram Report Format

**Watchdog (failure):**
```
🚨 Cortana AI OS — Alert [HH:MM]

🔴 technician: 1 issue(s)
  • Docker unhealthy: n8n(unhealthy) (restarted)
  ⚡ Auto-recovery attempted.
```

**Integrity audit (anomaly):**
```
🔴 Cortana AI OS — Integrity [HH:MM]

❌ technician
   ⚠️ Collection 'b737_ng_kb' dropped 7.2% vectors (16189 → 15020)
   📚 b737_classic_kb: 30,457 (+0)
```

**Daily maintenance:**
```
📋 Cortana AI OS — Daily Maintenance [YYYY-MM-DD HH:MM]

🔧 technician
   🗑️ Pruned 1,247 old executions
   🗄️ Postgres VACUUM: ok
   💾 Qdrant snapshots: 3/3 collections backed up
   📝 Agent doc updated 2.1h ago — verify n8n prompt sync

📊 Log entries today: 1,842
🤖 Automated by Cortana AI OS maintenance cron
```

---

## Known Issues

| Issue | Workaround |
|---|---|
| n8n workflow shows missing credentials after import | Add credentials manually in n8n UI, then update workflow nodes |
| Qdrant free tier suspends after inactivity | Reactivate at cloud.qdrant.io if using cloud Qdrant |
| `functions-framework` not found | Use `CMD ["functions-framework", "--target=process_file"]` not `python main.py` |
