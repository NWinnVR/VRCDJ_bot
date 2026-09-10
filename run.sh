#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  VRCDJ_bot — run (macOS / Linux)
#  One-time setup, then start the dashboard + bot.
# ─────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

# --- one-time setup --------------------------------------------------------
if [ ! -x "venv/bin/python" ]; then
    echo "[i] Creating venv + installing dependencies..."
    python3 -m venv venv
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
fi

# --- make sure bot.env exists ---------------------------------------------
if [ ! -f "bot.env" ]; then
    echo "[i] No bot.env — copying bot.env.example."
    cp bot.env.example bot.env
    echo "[!] EDIT bot.env and set DISCORD_BOT_TOKEN, then re-run this script."
    exit 1
fi

# --- distinct process names (ps shows vrcjd / vrcjb, not generic python) ---
# Copies of the venv's python so VRCDJ_bot is distinguishable from WyBot
# when both run on the same machine.  Safe to re-run.
if [ ! -x "venv/bin/vrcjd" ]; then
    cp venv/bin/python venv/bin/vrcjd
    cp venv/bin/python venv/bin/vrcjb
    echo "[i] Created vrcjd (dashboard) + vrcjb (bot) process names."
fi

PY="venv/bin/vrcjd"
[ -x "$PY" ] || PY="venv/bin/python"

echo
echo "[i] Starting VRCDJ_bot (dashboard: ${PY}  ·  bot: vrcjb)..."
echo
exec "$PY" dashboard.py
