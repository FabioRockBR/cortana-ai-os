#!/usr/bin/env python3
"""
Cortana AI OS — Integrity Audit
Runs hourly. Checks vector DB health, workflow status, and execution quality.

Usage:
    python3 tools/integrity_audit.py [--tier technician] [--quiet]

Cron (hourly at :23):
    23 * * * * /usr/bin/python3 /path/to/cortana-ai-os/tools/integrity_audit.py
"""

import argparse
import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
LOG_FILE = Path("/tmp/cortana-integrity.log")
BASELINE_FILE = Path("/tmp/cortana-qdrant-baseline.json")

# Port maps
TIER_CONFIG = {
    "technician":  {"n8n": 5678, "qdrant": 6333, "splitpdf": 8099},
    "operations":  {"n8n": 5679, "qdrant": 6334, "splitpdf": 8099},
    "master-chief":{"n8n": 5680, "qdrant": 6335, "splitpdf": 8099},
}

# Expected Qdrant collections per tier
TIER_COLLECTIONS = {
    "technician":   ["b737_classic_kb", "b737_ng_kb", "b757_kb"],
    "operations":   ["aircraft_maintenance_kb_v2"],
    "master-chief": ["aircraft_maintenance_kb_v2", "b737_classic_kb", "b737_ng_kb", "b757_kb"],
}

# Alert thresholds
VECTOR_DROP_THRESHOLD = 0.05   # Alert if collection drops >5% vectors
MIN_VECTORS_PER_COLLECTION = 100  # Alert if any collection has fewer


def load_env():
    """Load .env from project root."""
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    print(entry)
    with open(LOG_FILE, "a") as f:
        f.write(entry + "\n")
    # Keep log bounded (last 2000 lines)
    try:
        lines = LOG_FILE.read_text().splitlines()
        if len(lines) > 2000:
            LOG_FILE.write_text("\n".join(lines[-2000:]) + "\n")
    except Exception:
        pass


def tg_send(message: str) -> bool:
    """Send a Telegram message. Returns True if sent."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log(f"Telegram send failed: {e}")
        return False


def http_get(url: str, timeout: int = 8) -> tuple[int, dict | None]:
    """Returns (status_code, json_body_or_None)."""
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def check_qdrant(tier: str, port: int) -> dict:
    """Check all collections for a tier. Returns findings dict."""
    findings = {"ok": True, "collections": {}, "alerts": []}

    expected = TIER_COLLECTIONS.get(tier, [])
    if not expected:
        return findings

    # Load baseline counts
    baseline = {}
    if BASELINE_FILE.exists():
        try:
            baseline = json.loads(BASELINE_FILE.read_text()).get(tier, {})
        except Exception:
            pass

    new_baseline = {}

    for collection in expected:
        url = f"http://localhost:{port}/collections/{collection}"
        status, body = http_get(url)

        if status != 200 or not body:
            findings["ok"] = False
            findings["alerts"].append(f"Collection '{collection}' not accessible (HTTP {status})")
            findings["collections"][collection] = {"status": "unreachable"}
            continue

        count = body.get("result", {}).get("vectors_count", 0)
        indexed = body.get("result", {}).get("indexed_vectors_count", 0)
        new_baseline[collection] = count

        # Check minimum threshold
        if count < MIN_VECTORS_PER_COLLECTION:
            findings["ok"] = False
            findings["alerts"].append(
                f"Collection '{collection}' has only {count} vectors (minimum: {MIN_VECTORS_PER_COLLECTION})"
            )

        # Check for unexpected vector drop
        prev = baseline.get(collection, 0)
        if prev > 0 and count < prev * (1 - VECTOR_DROP_THRESHOLD):
            drop_pct = round((prev - count) / prev * 100, 1)
            findings["ok"] = False
            findings["alerts"].append(
                f"Collection '{collection}' dropped {drop_pct}% vectors ({prev} → {count})"
            )

        findings["collections"][collection] = {
            "vectors": count,
            "indexed": indexed,
            "prev": prev,
            "status": "ok" if count >= MIN_VECTORS_PER_COLLECTION else "low"
        }
        log(f"  [{tier}] {collection}: {count:,} vectors (prev: {prev:,})")

    # Persist new baseline
    try:
        all_baseline = {}
        if BASELINE_FILE.exists():
            all_baseline = json.loads(BASELINE_FILE.read_text())
        all_baseline[tier] = new_baseline
        BASELINE_FILE.write_text(json.dumps(all_baseline, indent=2))
    except Exception as e:
        log(f"  Baseline write failed: {e}")

    return findings


def check_n8n_workflow(tier: str, port: int) -> dict:
    """Check n8n is healthy and the main workflow is active."""
    findings = {"ok": True, "alerts": []}

    # Health check
    status, _ = http_get(f"http://localhost:{port}/healthz")
    if status != 200:
        findings["ok"] = False
        findings["alerts"].append(f"n8n not responding (HTTP {status})")
        return findings

    log(f"  [{tier}] n8n health: OK")

    # Check for failed executions in the last hour via Postgres
    # (Postgres is only accessible within Docker network, so we exec into container)
    try:
        tier_dir = PROJECT_DIR / "tiers" / tier
        result = subprocess.run(
            ["docker", "compose", "-f", str(tier_dir / "docker-compose.yml"),
             "exec", "-T", "postgres",
             "psql", "-U", "n8n", "-d", "n8n", "-t", "-c",
             """SELECT COUNT(*) FROM execution_entity
                WHERE status = 'error'
                AND "startedAt" > NOW() - INTERVAL '1 hour';"""],
            capture_output=True, text=True, timeout=15
        )
        error_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else -1
        if error_count > 5:
            findings["ok"] = False
            findings["alerts"].append(f"n8n: {error_count} failed executions in last hour")
            log(f"  [{tier}] n8n execution errors (last 1h): {error_count} ⚠️")
        else:
            log(f"  [{tier}] n8n execution errors (last 1h): {max(error_count, 0)}")
    except Exception as e:
        log(f"  [{tier}] Could not check execution errors: {e}")

    return findings


def check_postgres(tier: str) -> dict:
    """Verify Postgres is healthy and DB is accessible."""
    findings = {"ok": True, "alerts": []}
    tier_dir = PROJECT_DIR / "tiers" / tier
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(tier_dir / "docker-compose.yml"),
             "exec", "-T", "postgres", "pg_isready", "-U", "n8n"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            log(f"  [{tier}] Postgres: OK")
        else:
            findings["ok"] = False
            findings["alerts"].append("Postgres pg_isready check failed")
    except Exception as e:
        findings["ok"] = False
        findings["alerts"].append(f"Postgres check error: {e}")
    return findings


def audit_tier(tier: str) -> dict:
    """Run full integrity audit for one tier. Returns summary."""
    cfg = TIER_CONFIG.get(tier)
    if not cfg:
        return {"tier": tier, "ok": False, "alerts": ["Unknown tier"]}

    log(f"── Auditing tier: {tier} ──")
    all_alerts = []
    all_ok = True

    # Qdrant collections
    qdrant = check_qdrant(tier, cfg["qdrant"])
    if not qdrant["ok"]:
        all_ok = False
        all_alerts.extend(qdrant["alerts"])

    # n8n
    n8n = check_n8n_workflow(tier, cfg["n8n"])
    if not n8n["ok"]:
        all_ok = False
        all_alerts.extend(n8n["alerts"])

    # Postgres
    pg = check_postgres(tier)
    if not pg["ok"]:
        all_ok = False
        all_alerts.extend(pg["alerts"])

    return {
        "tier": tier,
        "ok": all_ok,
        "alerts": all_alerts,
        "qdrant": qdrant.get("collections", {}),
    }


def format_telegram_report(results: list[dict], quiet: bool) -> str | None:
    """Format Telegram message. Returns None if quiet and all OK."""
    all_ok = all(r["ok"] for r in results)
    now = datetime.now().strftime("%H:%M")

    if all_ok and quiet:
        return None  # No hourly noise when everything is fine

    lines = [f"{'✅' if all_ok else '🔴'} <b>Cortana AI OS — Integrity [{now}]</b>\n"]

    for r in results:
        icon = "✅" if r["ok"] else "❌"
        lines.append(f"{icon} <b>{r['tier']}</b>")
        if r["alerts"]:
            for a in r["alerts"]:
                lines.append(f"   ⚠️ {a}")
        # Vector counts
        for coll, data in r.get("qdrant", {}).items():
            vcount = data.get("vectors", 0)
            prev = data.get("prev", 0)
            delta = ""
            if prev and vcount != prev:
                diff = vcount - prev
                delta = f" ({'+'if diff>0 else ''}{diff:,})"
            lines.append(f"   📚 {coll}: {vcount:,}{delta}")

    if not all_ok:
        lines.append("\n🔧 Run: <code>python3 tools/maintenance.py --tier [tier]</code>")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default=None, help="Audit specific tier only")
    parser.add_argument("--quiet", action="store_true", help="Only send Telegram alert on failure")
    args = parser.parse_args()

    load_env()
    log("════════ Integrity Audit START ════════")

    # Determine which tiers to audit
    if args.tier:
        tiers = [args.tier]
    else:
        active = os.environ.get("CORTANA_ACTIVE_TIERS", "technician")
        tiers = active.split()

    results = []
    for tier in tiers:
        result = audit_tier(tier)
        results.append(result)

    # Telegram report
    msg = format_telegram_report(results, quiet=args.quiet)
    if msg:
        sent = tg_send(msg)
        log(f"Telegram report {'sent' if sent else 'FAILED (no token?)'}")

    # Exit code: 1 if any failures (for cron monitoring)
    all_ok = all(r["ok"] for r in results)
    log(f"════════ Integrity Audit DONE — {'OK' if all_ok else 'FAILURES DETECTED'} ════════")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
