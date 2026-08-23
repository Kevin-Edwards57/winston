#!/usr/bin/env python3
"""Diagnose a Winston installation.

Every check runs for real. Nothing is reported healthy because it usually is, and
nothing is skipped silently: a check that cannot run says so and counts as a warning
rather than quietly passing.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.getenv("WINSTON_PORT", "5001"))
URL = f"http://127.0.0.1:{PORT}"
LABEL = "studio.yardlink.winston"

GREEN, RED, AMBER, DIM, RESET = "\033[0;32m", "\033[0;31m", "\033[0;33m", "\033[2m", "\033[0m"
results: list[tuple[str, str, str]] = []


def record(state: str, name: str, detail: str = "") -> None:
    results.append((state, name, detail))
    colour = {"PASS": GREEN, "FAIL": RED, "WARN": AMBER}[state]
    print(f"  {colour}{state:<4}{RESET}  {name}" + (f"\n        {DIM}{detail}{RESET}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}")


# ── Environment ──────────────────────────────────────────────────────────
section("Environment")

version = sys.version_info
if version >= (3, 10):
    record("PASS", f"Python {version.major}.{version.minor}.{version.micro}")
else:
    record("FAIL", "Python version", f"3.10+ required, found {version.major}.{version.minor}")

venv = ROOT / "venv" / "bin" / "python3"
if venv.exists():
    record("PASS", "Virtual environment", str(venv))
else:
    record("WARN", "Virtual environment", "venv/ not found; using the system Python")

missing = [module for module in ("flask", "requests", "dotenv")
           if importlib.util.find_spec(module) is None]
if missing:
    record("FAIL", "Dependencies", f"missing: {', '.join(missing)}. Run pip install -r requirements.txt")
else:
    record("PASS", "Dependencies", "flask, requests, python-dotenv")

env_file = ROOT / ".env"
if env_file.exists():
    keys = [line.split("=")[0] for line in env_file.read_text().splitlines()
            if "=" in line and not line.startswith("#")]
    record("PASS", ".env present", f"{len(keys)} keys configured")
else:
    record("WARN", ".env missing", "copy .env.example to .env")

# ── Database ─────────────────────────────────────────────────────────────
section("Database")

db_path = Path(os.getenv("WINSTON_DATABASE", ROOT / "winston.db"))
if db_path.exists():
    size_mb = db_path.stat().st_size / 1_048_576
    record("PASS", "SQLite database", f"{db_path} ({size_mb:.1f} MB)")
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        integrity = connection.execute("PRAGMA integrity_check;").fetchone()[0]
        record("PASS" if integrity == "ok" else "FAIL", "Integrity check", integrity)
        journal = connection.execute("PRAGMA journal_mode;").fetchone()[0]
        record("PASS" if journal.lower() == "wal" else "WARN", "Journal mode", journal)
        contacts = connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        drafts = connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
        suppressions = connection.execute("SELECT COUNT(*) FROM suppressions").fetchone()[0]
        record("PASS", "Data", f"{contacts} contacts, {drafts} drafts, {suppressions} suppressions")
        connection.close()
    except sqlite3.Error as error:
        record("FAIL", "Database readable", str(error))
else:
    record("WARN", "SQLite database", f"{db_path} does not exist yet; it is created on first run")

backups = sorted((ROOT / "data" / "backups").glob("winston-*.db")) if (ROOT / "data" / "backups").exists() else []
if backups:
    record("PASS", "Backups", f"{len(backups)}, most recent {backups[-1].name}")
else:
    record("WARN", "Backups", "none yet. Run ./winstonctl backup")

# ── Ollama ───────────────────────────────────────────────────────────────
section("Local AI")

if shutil.which("ollama"):
    record("PASS", "Ollama installed", shutil.which("ollama"))
else:
    record("WARN", "Ollama binary", "not on PATH; it may still be running as an app")

base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
try:
    with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as response:
        tags = json.load(response)
    installed = {model["name"] for model in tags.get("models", [])}
    record("PASS", "Ollama reachable", f"{base}, {len(installed)} model(s)")

    for label, env_key, default in (("Primary model", "OLLAMA_MODEL", "qwen3:8b"),
                                    ("Light model", "OLLAMA_LIGHT_MODEL", "llama3.2:3b")):
        wanted = os.getenv(env_key, default)
        if wanted in installed:
            record("PASS", label, wanted)
        else:
            record("FAIL", label, f"{wanted} not installed. Run: ollama pull {wanted}")
except Exception as error:
    record("FAIL", "Ollama reachable", f"{base} unreachable ({type(error).__name__}). "
                                       "Winston cannot generate without it.")

gemini = os.getenv("GEMINI_API_KEY", "").strip()
record("PASS" if gemini else "WARN", "Gemini free tier",
       "configured" if gemini else "GEMINI_API_KEY empty; the free tier is never attempted")

claude_enabled = os.getenv("WINSTON_ENABLE_CLAUDE", "false").casefold() == "true"
record("PASS", "Claude", "enabled by operator" if claude_enabled
       else "disabled (the safe default, keeps AI cost at zero)")

# ── Service ──────────────────────────────────────────────────────────────
section("Service")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    port_open = probe.connect_ex(("127.0.0.1", PORT)) == 0

if PORT == 5000:
    record("WARN", "Port 5000", "macOS ControlCenter (AirPlay Receiver) also binds 5000")

plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
if plist.exists():
    loaded = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
    record("PASS" if loaded.returncode == 0 else "WARN", "Autostart (launchd)",
           "installed and loaded" if loaded.returncode == 0 else "installed but not loaded")
else:
    record("WARN", "Autostart (launchd)", "not installed. Run ./winstonctl install")

try:
    with urllib.request.urlopen(f"{URL}/health", timeout=5) as response:
        health = json.load(response)
    record("PASS", "Winston responding", URL)
    record("PASS" if health.get("database") == "ok" else "FAIL",
           "Application health", f"database {health.get('database')}")
    record("PASS" if health.get("dry_run") else "WARN", "Dry run",
           "ON, no message can reach a real inbox" if health.get("dry_run")
           else "OFF. Winston will send real email.")
    cost = health.get("ai_cost", {}).get("month_to_date_usd", 0)
    record("PASS" if cost == 0 else "WARN", "AI cost this month", f"${cost:.2f}")
    catalogue = health.get("catalogue", {})
    record("PASS" if catalogue.get("can_recommend") else "WARN", "Catalogue",
           f"{catalogue.get('offerable_to_business', 0)} offerable service(s)")
    pricing = health.get("pricing", {})
    record("PASS" if pricing.get("can_quote") else "WARN", "Pricing",
           "; ".join(pricing.get("missing", [])) or "can quote")
except (urllib.error.URLError, TimeoutError, OSError):
    if port_open:
        record("FAIL", "Winston responding", f"something holds port {PORT} but /health did not answer")
    else:
        record("WARN", "Winston responding", f"not running. Start it with ./winstonctl start")

# ── Summary ──────────────────────────────────────────────────────────────
counts = {state: sum(1 for s, _, _ in results if s == state) for state in ("PASS", "WARN", "FAIL")}
print(f"\n{counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failures")

if counts["FAIL"]:
    print(f"{RED}Winston has problems that will stop it working.{RESET}")
    sys.exit(1)
if counts["WARN"]:
    print(f"{AMBER}Winston works, with warnings above.{RESET}")
    sys.exit(0)
print(f"{GREEN}Winston is fully operational.{RESET}")
