# Deployment Guide

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- A Telegram bot token — create one at [@BotFather](https://t.me/BotFather)
- OpenAI API key — [platform.openai.com](https://platform.openai.com)
- Google AI Studio API key — [aistudio.google.com](https://aistudio.google.com/apikey)
- `ngrok` or a public domain for Telegram webhooks (local) — or reverse proxy for cloud deploy

---

## Quick Start (Single Tier)

### 1. Clone and configure

```bash
git clone https://github.com/FabioRockBR/cortana-saas.git
cd cortana-saas/tiers/technician   # or operations / master-chief
cp .env.template .env
```

Edit `.env` and fill in all values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
CF_AUTH_TOKEN=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
N8N_ENCRYPTION_KEY=$(openssl rand -hex 24)
WEBHOOK_URL=https://your-ngrok-url.ngrok-free.app
```

### 2. Start the stack

```bash
docker compose up -d
```

Wait ~30 seconds for all services to become healthy:

```bash
docker compose ps
# All 4 services should show (healthy)
```

### 3. Import the workflow

```bash
docker compose run --rm n8n-init
```

This runs once, imports the Cortana workflow directly into the n8n database, then exits.

### 4. Complete n8n setup

1. Open [http://localhost:5678](http://localhost:5678)
2. Create your admin account (email + password)
3. Add credentials in **Settings → Credentials**:
   - **OpenAI API** → paste your API key
   - **Telegram API** → paste your bot token
   - **Google Gemini (PaLM) API** → paste your Google AI Studio key
   - **HTTP Header Auth** (for split-pdf) → Header: `X-CF-Token`, Value: your `CF_AUTH_TOKEN`
4. Open the imported workflow, update nodes to use the new credentials
5. Activate the workflow (toggle at top right)

### 5. Register the bot token in Telegram

Send this message to your bot:
```
/settoken YOUR_BOT_TOKEN
```

This stores the token in n8n's static data for voice I/O (required once per deployment).

### 6. Test

Send any message to your Telegram bot. You should receive a response from the AI agent.

---

## Ingesting Documents

Use the batch ingest tool to load documents into the vector database:

```bash
# Install dependencies
pip install requests python-dotenv pymupdf

# Ingest a folder of PDFs
python tools/batch_ingest.py \
  --dir /path/to/your/manuals \
  --url http://localhost:8099 \
  --token YOUR_CF_AUTH_TOKEN \
  --collection b737_ng_kb
```

Available collections by tier:

| Tier | Collections |
|---|---|
| Technician | `b737_classic_kb`, `b737_ng_kb`, `b757_kb` |
| Operations | `aircraft_maintenance_kb_v2` |
| Master Chief | All 4 collections |

For PDFs >200MB, the tool automatically splits them into 10-page chunks before ingestion.

---

## Running Multiple Tiers Locally

Each tier uses different host ports — you can run all 3 simultaneously:

```bash
# Terminal 1 — Technician
cd tiers/technician && docker compose up -d

# Terminal 2 — Operations (different ports: n8n:5679, Qdrant:6334)
cd tiers/operations && docker compose up -d

# Terminal 3 — Master Chief (different ports: n8n:5680, Qdrant:6335)
cd tiers/master-chief && docker compose up -d
```

---

## Cloud Deployment

For production, each tier should have:

1. A dedicated VPS or cloud instance (min 4GB RAM, 2 vCPU)
2. A reverse proxy (nginx/Caddy) with TLS termination
3. `WEBHOOK_URL` set to the public HTTPS domain

Example nginx configuration:

```nginx
server {
    server_name cortana-tech.yourdomain.com;
    location / {
        proxy_pass http://localhost:5678;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 50m;
        proxy_read_timeout 600s;
    }
}
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Telegram bot doesn't respond | Webhook not registered | Check `WEBHOOK_URL` is public HTTPS; restart n8n |
| split-pdf returns 401 | Wrong `CF_AUTH_TOKEN` | Verify `.env` matches token used in n8n credential |
| split-pdf returns 500 | Missing `GOOGLE_API_KEY` | Check `.env` and restart split-pdf container |
| n8n workflow inactive | Not activated | Toggle workflow ON in n8n editor |
| Qdrant shows unhealthy | Port conflict | Change `6333:6333` to unused port in docker-compose |
| Voice not working | Bot token not set | Send `/settoken YOUR_BOT_TOKEN` to the bot |

---

## Stopping and Cleaning Up

```bash
# Stop without removing data
docker compose down

# Stop and remove all data (volumes)
docker compose down -v
```
