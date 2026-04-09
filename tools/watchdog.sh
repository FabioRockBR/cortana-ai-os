#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Cortana AI OS — System Watchdog
# Run every 5 minutes via cron:
#   */5 * * * * /bin/bash /path/to/cortana-ai-os/tools/watchdog.sh >> /tmp/cortana-watchdog.log 2>&1
#
# Checks: Docker health, n8n, split-pdf, Qdrant
# Action: restart unhealthy containers automatically
# Alerts: Telegram on failure + restore; silent hourly OK ping
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG="/tmp/cortana-watchdog.log"
MAX_LOG_LINES=1000

# Load env from project root if present, else from shell environment
ENV_FILE="${CORTANA_ENV_FILE:-$PROJECT_DIR/.env}"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

TG_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TG_CHAT_ID="${TELEGRAM_CHAT_ID:-}"      # owner's chat ID (set in .env)
CF_TOKEN="${CF_AUTH_TOKEN:-}"

# Active tiers to monitor — set CORTANA_TIERS in .env or override here
ACTIVE_TIERS="${CORTANA_ACTIVE_TIERS:-technician}"  # space-separated, e.g. "technician operations"

# Port map: tier → n8n_port:qdrant_port:splitpdf_port
declare -A N8N_PORT=([technician]=5678 [operations]=5679 [master-chief]=5680)
declare -A QDRANT_PORT=([technician]=6333 [operations]=6334 [master-chief]=6335)
declare -A SPLITPDF_PORT=([technician]=8099 [operations]=8099 [master-chief]=8099)

# ── Helpers ───────────────────────────────────────────────────────────────────
ts()      { date '+%Y-%m-%d %H:%M:%S'; }
log()     { echo "[$(ts)] $*" | tee -a "$LOG"; }
tg_send() {
    [[ -z "$TG_BOT_TOKEN" || -z "$TG_CHAT_ID" ]] && return 0
    curl -sf --max-time 10 \
        "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT_ID}" \
        --data-urlencode "text=$1" \
        -d "parse_mode=HTML" > /dev/null 2>&1 || true
}

# Rotate log
if [[ -f "$LOG" ]]; then
    tail -n $MAX_LOG_LINES "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi

log "════════ Watchdog tick ════════"

GLOBAL_FAILURES=0
REPORT=""

# ── Check each active tier ────────────────────────────────────────────────────
for TIER in $ACTIVE_TIERS; do
    TIER_DIR="$PROJECT_DIR/tiers/$TIER"
    [[ -d "$TIER_DIR" ]] || { log "WARN: tier directory not found: $TIER_DIR"; continue; }

    log "── Tier: $TIER ──"
    TIER_FAIL=0
    TIER_MSGS=""

    N8N_P="${N8N_PORT[$TIER]:-5678}"
    QD_P="${QDRANT_PORT[$TIER]:-6333}"
    SP_P="${SPLITPDF_PORT[$TIER]:-8099}"

    # 1. Docker compose health
    log "  [docker] checking compose services..."
    UNHEALTHY=$(docker compose -f "$TIER_DIR/docker-compose.yml" ps --format json 2>/dev/null \
        | python3 -c "
import json,sys
lines = sys.stdin.read().strip().split('\n')
bad = []
for l in lines:
    try:
        s = json.loads(l)
        state = s.get('Health','').lower()
        if state and state not in ('healthy',''):
            bad.append(f\"{s['Service']}({state})\")
    except: pass
print(' '.join(bad))
" 2>/dev/null || echo "")

    if [[ -n "$UNHEALTHY" ]]; then
        log "  [docker] UNHEALTHY: $UNHEALTHY — restarting..."
        docker compose -f "$TIER_DIR/docker-compose.yml" restart $UNHEALTHY >> "$LOG" 2>&1 || true
        TIER_MSGS="${TIER_MSGS}\n  • Docker unhealthy: $UNHEALTHY (restarted)"
        TIER_FAIL=$((TIER_FAIL + 1))
    else
        log "  [docker] all containers healthy"
    fi

    # 2. n8n health endpoint
    N8N_STATUS=$(curl -sf --max-time 8 -o /dev/null -w "%{http_code}" \
        "http://localhost:${N8N_P}/healthz" 2>/dev/null || echo "ERR")
    if [[ "$N8N_STATUS" == "200" ]]; then
        log "  [n8n] OK (port $N8N_P)"
    else
        log "  [n8n] FAIL — HTTP $N8N_STATUS on port $N8N_P"
        TIER_MSGS="${TIER_MSGS}\n  • n8n not responding (HTTP $N8N_STATUS)"
        TIER_FAIL=$((TIER_FAIL + 1))
    fi

    # 3. Qdrant health
    QD_STATUS=$(curl -sf --max-time 8 -o /dev/null -w "%{http_code}" \
        "http://localhost:${QD_P}/healthz" 2>/dev/null || echo "ERR")
    if [[ "$QD_STATUS" == "200" ]]; then
        log "  [qdrant] OK (port $QD_P)"
    else
        log "  [qdrant] FAIL — HTTP $QD_STATUS on port $QD_P"
        TIER_MSGS="${TIER_MSGS}\n  • Qdrant not responding (HTTP $QD_STATUS)"
        TIER_FAIL=$((TIER_FAIL + 1))
    fi

    # 4. split-pdf auth check (expects 401 = service running, auth works)
    if [[ -n "$CF_TOKEN" ]]; then
        SP_AUTH=$(curl -sf --max-time 8 -o /dev/null -w "%{http_code}" \
            -X POST "http://localhost:${SP_P}" \
            -H "Content-Type: application/json" \
            -H "X-CF-Token: WRONG_TOKEN_CHECK" \
            -d '{}' 2>/dev/null || echo "ERR")
        if [[ "$SP_AUTH" == "401" ]]; then
            log "  [split-pdf] OK — auth working (port $SP_P)"
        else
            log "  [split-pdf] WARN — unexpected response: HTTP $SP_AUTH (expected 401)"
            TIER_MSGS="${TIER_MSGS}\n  • split-pdf unexpected response (HTTP $SP_AUTH)"
        fi
    fi

    # 5. Summarize tier
    if [[ $TIER_FAIL -gt 0 ]]; then
        GLOBAL_FAILURES=$((GLOBAL_FAILURES + TIER_FAIL))
        REPORT="${REPORT}\n🔴 <b>$TIER</b>: $TIER_FAIL issue(s)${TIER_MSGS}"
        log "  TIER STATUS: DEGRADED ($TIER_FAIL failures)"
    else
        REPORT="${REPORT}\n✅ <b>$TIER</b>: all systems nominal"
        log "  TIER STATUS: OK"
    fi
done

# ── Disk space check ──────────────────────────────────────────────────────────
DISK_PCT=$(df "$PROJECT_DIR" | awk 'NR==2 {gsub(/%/,""); print $5}' 2>/dev/null || echo "0")
log "Disk usage: ${DISK_PCT}%"
if [[ "$DISK_PCT" -gt 85 ]]; then
    REPORT="${REPORT}\n⚠️ <b>Disk</b>: ${DISK_PCT}% used — cleanup needed"
    GLOBAL_FAILURES=$((GLOBAL_FAILURES + 1))
    log "WARN: disk at ${DISK_PCT}% — approaching limit"
fi

# ── Telegram reporting ────────────────────────────────────────────────────────
NOW=$(date '+%H:%M')
MINUTE=$(date '+%M')

if [[ $GLOBAL_FAILURES -gt 0 ]]; then
    tg_send "🚨 <b>Cortana AI OS — Alert</b> [${NOW}]\n\n${REPORT}\n\n⚡ Auto-recovery attempted. Check /tmp/cortana-watchdog.log for details."
    log "ALERT sent to Telegram ($GLOBAL_FAILURES failures)"
else
    # Hourly OK ping (on the :07 minute to spread load)
    if [[ "$MINUTE" == "07" ]]; then
        TIER_COUNT=$(echo $ACTIVE_TIERS | wc -w | tr -d ' ')
        tg_send "✅ <b>Cortana AI OS — ${NOW}</b>\n${REPORT}\n\n📊 Disk: ${DISK_PCT}% | Tiers: ${TIER_COUNT} | Status: All nominal"
        log "Hourly OK ping sent"
    fi
fi

log "════════ Watchdog done — failures: $GLOBAL_FAILURES ════════"
