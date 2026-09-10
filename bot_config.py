"""bot_config.py — VRCDJ_bot configuration (DJ-lookup only, no LLM).

Loads bot_config.json from the project root. Every getter reads through
bot_config.load_config() so the dashboard can change values at runtime —
no restart needed.

STRIPPED from the WyBot version (no LLM / no Wyvern-post / no host):
  • model / ask_channel / schedule_window / convo_idle_minutes
  • social_* settings
  • event_lineup_channels (the bot auto-detects event posts in ANY channel
    where it can read messages — simpler, more portable)

KEPT (DJ + general):
  • sheet_url  — the public Google Sheet CSV endpoint
  • dj_refresh_days — auto-refresh interval
  • enabled — master kill-switch (mirrored in bot_state)
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "bot_config.json"

# ---------------------------------------------------------------------------
# Defaults — safe, portable, no secrets
# ---------------------------------------------------------------------------
DEFAULTS: dict = {
    # The public DJ master-list (Google Sheet → CSV).
    # Uses the "publish to web" CSV link (…/d/e/<PUB_ID>/pub?output=csv) —
    # the one that actually serves CSV without a 400. Anyone with the link
    # can view; no API key needed.
    "sheet_url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vSDmzW0zDXiGPDGDCCBdNYcna-zvd7EJwWLMRdzOobNmpdHQs5v6ayZG2AOuH8gWICjwMpc5iwabssq/pub?output=csv",
    # Re-pull the sheet every N days (the sheet changes ~weekly).
    "dj_refresh_days": 3,
    # Master kill-switch. True = bot answers; False = bot says "switched off".
    # (Also mirrored in bot_state.json so the dashboard and bot stay in sync.)
    "enabled": True,
}

_lock = threading.RLock()


def load_config() -> dict:
    """Return the merged config (defaults ← file). Thread-safe."""
    with _lock:
        cfg = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                file_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(file_cfg, dict):
                    cfg.update(file_cfg)
            except (json.JSONDecodeError, OSError):
                pass  # corrupt file → fall back to defaults
        return cfg


def save_config(cfg: dict) -> None:
    """Write the config to disk atomically. Thread-safe."""
    with _lock:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_PATH)


# ---------------------------------------------------------------------------
# Convenience getters (the bot + dashboard call these, not load_config())
# ---------------------------------------------------------------------------

def get_sheet_url() -> str:
    # env override wins (portable, no JSON editing), then the config default.
    env = os.environ.get("DJ_SHEET_URL", "").strip()
    if env:
        return env
    return str(load_config().get("sheet_url", DEFAULTS["sheet_url"])).strip()


def get_dj_refresh_days() -> int:
    try:
        return max(0, int(load_config().get("dj_refresh_days", 3)))
    except (TypeError, ValueError):
        return 3


def is_enabled() -> bool:
    return bool(load_config().get("enabled", True))


def set_enabled(value: bool) -> None:
    cfg = load_config()
    cfg["enabled"] = bool(value)
    save_config(cfg)
