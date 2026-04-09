#!/usr/bin/env python3
"""
Cortana AI OS — Automated Maintenance
Runs daily. Performs cleanup, log rotation, execution pruning, and system prompt sync.

Usage:
    python3 tools/maintenance.py [--tier technician] [--dry-run]

Cron (daily at 03:17):
    17 3 * * * /usr/bin/python3 /path/to/cortana-ai-os/tools/maintenance.py >> /tmp/cortana-maintenance.log 2>&1
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import urllib.request
import urllib.error

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
LOG_FILE = Path("/tmp/cortana-maintenance.log")

TIER_CONFIG = {
    "technician":   {"n8n": 5678, "qdrant": 6333},
    "operations":   {"n8n": 5679, "qdrant": 6334},
    "master-chief": {"n8n": 5680, "qdrant": 6335},
}

AGENT_DOCS = {
    "technician":   PROJECT_DIR / "docs" / "agents" / "tier-1-technician.md",
    "operations":   PROJECT_DIR / "docs" / "agents" / "tier-2-operations.md",
    "master-chief": PROJECT_DIR / "docs" / "agents" / "tier-3-master-chief.md",
}


def load_env():
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
    try:
        lines = LOG_FILE.read_text().splitlines()
        if len(lines) > 3000:
            LOG_FILE.write_text("\n".join(lines[-3000:]) + "\n")
    except Exception:
        pass


def tg_send(message: str) -> bool:
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
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log(f"Telegram failed: {e}")
        return False


def run_in_compose(tier: str, service: str, cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a command inside a Docker Compose service using execFile-style (no shell)."""
    tier_dir = PROJECT_DIR / "tiers" / tier
    full_cmd = [
        "docker", "compose",
        "-f", str(tier_dir / "docker-compose.yml"),
        "exec", "-T", service
    ] + cmd
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, (result.stdout + result.stderr).strip()


def prune_executions(tier: str, dry_run: bool) -> dict:
    """Delete n8n executions older than 7 days."""
    log(f"  [{tier}] Pruning executions older than 7 days...")
    try:
        rc, out = run_in_compose(tier, "postgres",
            ["psql", "-U", "n8n", "-d", "n8n", "-t", "-c",
             "SELECT COUNT(*) FROM execution_entity WHERE \"startedAt\" < NOW() - INTERVAL '7 days';"])
        count = int(out.strip()) if out.strip().isdigit() else 0
        log(f"  [{tier}] Executions to prune: {count}")

        if count == 0:
            return {"pruned": 0}

        if not dry_run:
            rc2, _ = run_in_compose(tier, "postgres",
                ["psql", "-U", "n8n", "-d", "n8n", "-t", "-c",
                 "DELETE FROM execution_entity WHERE \"startedAt\" < NOW() - INTERVAL '7 days';"])
            if rc2 == 0:
                log(f"  [{tier}] Pruned {count} executions ✓")
                return {"pruned": count}
            return {"pruned": 0, "error": "DELETE failed"}
        else:
            log(f"  [{tier}] DRY RUN — would prune {count} executions")
            return {"pruned": 0, "would_prune": count}
    except Exception as e:
        log(f"  [{tier}] Prune exception: {e}")
        return {"pruned": 0, "error": str(e)}


def vacuum_postgres(tier: str, dry_run: bool) -> bool:
    """Run VACUUM ANALYZE."""
    if dry_run:
        log(f"  [{tier}] DRY RUN — would VACUUM ANALYZE")
        return True
    log(f"  [{tier}] Running VACUUM ANALYZE...")
    try:
        rc, out = run_in_compose(tier, "postgres",
            ["psql", "-U", "n8n", "-d", "n8n", "-c", "VACUUM ANALYZE;"], timeout=60)
        log(f"  [{tier}] VACUUM: {'OK' if rc == 0 else 'FAILED'}")
        return rc == 0
    except Exception as e:
        log(f"  [{tier}] VACUUM exception: {e}")
        return False


def qdrant_snapshot(tier: str, qdrant_port: int, dry_run: bool) -> dict:
    """Create Qdrant snapshots for backup."""
    snapshots = {}
    try:
        req = urllib.request.Request(f"http://localhost:{qdrant_port}/collections")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        collections = [c["name"] for c in data.get("result", {}).get("collections", [])]
    except Exception as e:
        log(f"  [{tier}] Could not list Qdrant collections: {e}")
        return {}

    for coll in collections:
        if dry_run:
            log(f"  [{tier}] DRY RUN — would snapshot: {coll}")
            snapshots[coll] = "dry_run"
            continue
        try:
            snap_req = urllib.request.Request(
                f"http://localhost:{qdrant_port}/collections/{coll}/snapshots",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=b"{}"
            )
            with urllib.request.urlopen(snap_req, timeout=60) as resp:
                snap_data = json.loads(resp.read())
            snap_name = snap_data.get("result", {}).get("name", "unknown")
            snapshots[coll] = snap_name
            log(f"  [{tier}] Snapshot '{coll}': {snap_name} ✓")
        except Exception as e:
            log(f"  [{tier}] Snapshot '{coll}' failed: {e}")
            snapshots[coll] = f"ERROR: {e}"

    return snapshots


def rotate_logs(dry_run: bool) -> list[str]:
    """Rotate logs larger than 5MB."""
    log_files = [
        Path("/tmp/cortana-watchdog.log"),
        Path("/tmp/cortana-integrity.log"),
        Path("/tmp/cortana-maintenance.log"),
    ]
    rotated = []
    for lf in log_files:
        if lf.exists() and lf.stat().st_size > 5 * 1024 * 1024:
            archive = lf.with_suffix(f".{datetime.now().strftime('%Y%m%d')}.log")
            if not dry_run:
                lf.rename(archive)
                rotated.append(archive.name)
                log(f"  Rotated: {lf.name} → {archive.name}")
            else:
                log(f"  DRY RUN — would rotate: {lf.name} ({lf.stat().st_size // 1024}KB)")
    return rotated


def check_prompt_sync(tier: str) -> dict:
    """Check if system prompt doc was recently modified (needs manual sync)."""
    doc_file = AGENT_DOCS.get(tier)
    if not doc_file or not doc_file.exists():
        return {"status": "no_doc"}
    age_hours = (datetime.now() - datetime.fromtimestamp(doc_file.stat().st_mtime)).total_seconds() / 3600
    if age_hours < 24:
        log(f"  [{tier}] Agent doc modified {age_hours:.1f}h ago — may need n8n prompt sync")
        return {"status": "updated_recently", "age_hours": round(age_hours, 1)}
    return {"status": "current"}


def maintenance_tier(tier: str, dry_run: bool) -> dict:
    cfg = TIER_CONFIG.get(tier)
    if not cfg:
        return {"tier": tier, "error": "unknown tier"}

    log(f"── Maintenance: {tier} ──")
    tasks = {}
    tasks["prune"] = prune_executions(tier, dry_run)
    tasks["vacuum"] = "ok" if vacuum_postgres(tier, dry_run) else "failed"
    tasks["snapshots"] = qdrant_snapshot(tier, cfg["qdrant"], dry_run)
    tasks["prompt_sync"] = check_prompt_sync(tier)
    return {"tier": tier, "tasks": tasks}


def format_daily_report(results: list[dict], log_count: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"📋 <b>Cortana AI OS — Daily Maintenance [{now}]</b>\n"]
    for r in results:
        if "error" in r:
            lines.append(f"❌ <b>{r['tier']}</b>: {r['error']}")
            continue
        lines.append(f"🔧 <b>{r['tier']}</b>")
        t = r.get("tasks", {})

        prune = t.get("prune", {})
        if prune.get("pruned"):
            lines.append(f"   🗑️ Pruned {prune['pruned']:,} executions")
        elif prune.get("would_prune"):
            lines.append(f"   🗑️ Would prune {prune['would_prune']:,} (dry run)")
        else:
            lines.append("   🗑️ No executions to prune")

        lines.append(f"   🗄️ Postgres VACUUM: {t.get('vacuum', '?')}")

        snaps = t.get("snapshots", {})
        ok_snaps = sum(1 for v in snaps.values() if "ERROR" not in str(v) and v != "dry_run")
        if snaps:
            lines.append(f"   💾 Qdrant snapshots: {ok_snaps}/{len(snaps)} collections backed up")

        sync = t.get("prompt_sync", {})
        if sync.get("status") == "updated_recently":
            lines.append(f"   📝 Agent doc updated {sync['age_hours']}h ago — verify n8n prompt sync")

    lines.append(f"\n📊 Log entries today: {log_count}")
    lines.append("🤖 <i>Automated by Cortana AI OS maintenance cron</i>")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    log("════════ Maintenance START ════════")
    if args.dry_run:
        log("DRY RUN MODE")

    tiers = [args.tier] if args.tier else os.environ.get("CORTANA_ACTIVE_TIERS", "technician").split()
    results = [maintenance_tier(t, args.dry_run) for t in tiers]
    rotate_logs(args.dry_run)

    log_count = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for lf in [Path("/tmp/cortana-watchdog.log"), Path("/tmp/cortana-integrity.log")]:
        if lf.exists():
            log_count += sum(1 for l in lf.read_text().splitlines() if today in l)

    msg = format_daily_report(results, log_count)
    sent = tg_send(msg)
    log(f"Daily report {'sent' if sent else 'FAILED (no Telegram config?)'}")
    log("════════ Maintenance DONE ════════")


if __name__ == "__main__":
    main()
