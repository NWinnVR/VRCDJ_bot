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

echo
echo "[i] Starting VRCDJ_bot (dashboard + bot)..."
echo
exec venv/bin/python dashboard.py
