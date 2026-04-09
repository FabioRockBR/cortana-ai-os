# Cortana AI OS

**Self-hosted AI agent platform for airline maintenance operations.**

Three independent deployment tiers — each a complete Docker stack with its own AI agent, vector database, and document ingestion service. Configure a Telegram bot, deploy, and your team has an AI assistant scoped exactly to their role.

---

## Tiers

| Tier | Agent | Users | Knowledge |
|---|---|---|---|
| **Technician** | Cortana | Line & hangar technicians | Fleet manuals — AMM, CMM, WDM, IPC, SRM |
| **Operations** | MORPHEUS | MCC, dispatch, supervisors | Institutional — MEL, MOE, SMS, quality |
| **Master Chief** | Master Chief | DOM, CEO, quality managers | All knowledge bases combined |

Each tier is isolated — separate Qdrant DB, separate n8n instance, separate Telegram bot. No shared infrastructure between customers.

---

## Architecture

```
Telegram Bot
     │
     ▼
 n8n (workflow engine)
     │
     ├── GPT-4.1 AI Agent
     │         │
     │    Qdrant (vector DB)
     │    scoped per tier
     │
     └── split-pdf (ingestion service)
               │
          Gemini Embedding 2
          3072-dim vectors
```

Full architecture: [docs/architecture.md](docs/architecture.md)  
Tier comparison: [docs/tier-comparison.md](docs/tier-comparison.md)  
Agent system prompts: [docs/agents/](docs/agents/)

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/FabioRockBR/cortana-saas.git
cd cortana-saas
```

### 2. Pick a tier and configure

```bash
cd tiers/technician   # or: operations / master-chief
cp .env.template .env
# Edit .env — fill in TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, GOOGLE_API_KEY
```

### 3. Start

```bash
docker compose up -d
docker compose run --rm n8n-init   # imports workflow on first boot
```

### 4. Open n8n and add credentials

Visit [http://localhost:5678](http://localhost:5678) → create admin account → add API credentials → activate the workflow.

Full setup guide: [docs/setup.md](docs/setup.md)

---

## Stack

| Component | Version | Purpose |
|---|---|---|
| n8n | 1.93.0 | Workflow engine, Telegram I/O, AI agent orchestration |
| Qdrant | 1.13.4 | Vector database for document retrieval |
| PostgreSQL | 16 (Alpine) | n8n backend database |
| GPT-4.1 | OpenAI | Primary AI model |
| Gemini Embedding 2 | Google | Document embedding (3072 dims) |
| Whisper | OpenAI | Speech-to-text for voice messages |
| TTS-1-HD | OpenAI (nova) | Text-to-speech response delivery |

---

## Document Ingestion

Load your maintenance manuals into the vector database:

```bash
python tools/batch_ingest.py \
  --dir /path/to/manuals \
  --url http://localhost:8099 \
  --token YOUR_CF_AUTH_TOKEN \
  --collection b737_ng_kb
```

Supports PDF, images (JPG/PNG), and video files. Large PDFs are auto-split.

Collections: `b737_classic_kb` · `b737_ng_kb` · `b757_kb` · `aircraft_maintenance_kb_v2`

---

## Supported Capabilities

- **Voice I/O** — send voice messages, receive voice replies (pt-BR + English)
- **Photo analysis** — send maintenance photos for damage/fault assessment
- **Document ingestion** — send PDF directly to the bot to ingest into the KB
- **TLB entry generation** — `/tsc tlb` generates CAPS-format technical log entries
- **MEL dispatch** — Operations tier resolves MEL items with category and conditions
- **Bilingual** — auto-detects Brazilian Portuguese and English per message

---

## License

Proprietary — contact [FabioRockBR](https://github.com/FabioRockBR) for licensing.
