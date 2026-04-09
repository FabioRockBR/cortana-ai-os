#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Cortana n8n init script — one-shot, runs as separate init container
# Imports workflows using n8n CLI (no REST auth needed, writes directly to DB)
# ─────────────────────────────────────────────────────────────────────────────
set -e

INIT_FLAG="/home/node/.n8n/.cortana_initialized"
N8N_BASE="http://n8n:5678"

echo "[init] Waiting for n8n to be ready at $N8N_BASE..."
for i in $(seq 1 60); do
  if wget -qO- --timeout=3 http://n8n:5678/healthz >/dev/null 2>&1; then
    echo "[init] n8n ready after ${i}s"
    break
  fi
  sleep 2
done

if [ -f "$INIT_FLAG" ]; then
  echo "[init] Already initialized — skipping"
  exit 0
fi

echo "[init] First boot — importing workflows for tier: $CORTANA_TIER"

WORKFLOWS_DIR="/workflows"
if [ -d "$WORKFLOWS_DIR" ]; then
  for wf in "$WORKFLOWS_DIR"/*.json; do
    [ -f "$wf" ] || continue
    name=$(basename "$wf" .json)
    tmpfile="/tmp/wf_${name}.json"

    # Substitute placeholder tokens with actual env vars
    sed \
      -e "s|__TELEGRAM_BOT_TOKEN__|${TELEGRAM_BOT_TOKEN}|g" \
      -e "s|__OPENAI_API_KEY__|${OPENAI_API_KEY}|g" \
      -e "s|__GOOGLE_API_KEY__|${GOOGLE_API_KEY}|g" \
      -e "s|__CF_AUTH_TOKEN__|${CF_AUTH_TOKEN}|g" \
      -e "s|__SPLIT_PDF_URL__|${SPLIT_PDF_URL:-http://split-pdf:8080}|g" \
      -e "s|__AIRTABLE_API_KEY__|${AIRTABLE_API_KEY:-}|g" \
      -e "s|__QDRANT_URL__|http://qdrant:6333|g" \
      "$wf" > "$tmpfile"

    echo "[init]   Importing: $name"
    n8n import:workflow --input="$tmpfile" && \
      echo "[init]     ✅ $name" || \
      echo "[init]     ⚠️  $name (import failed — check format)"
    rm -f "$tmpfile"
  done
fi

touch "$INIT_FLAG"
echo "[init] ✅ Cortana ($CORTANA_TIER) workflows imported"
echo "[init] Visit http://localhost:5678 to complete setup (create admin account)"
echo "[init] Then send /start to your Telegram bot"
