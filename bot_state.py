"""bot_state.py — VRCDJ_bot runtime state (DJ-lookup only, no LLM).

Persists to bot_state.json + bot_lifecycle.json in the project root.
Thread-safe via RLock; atomic writes via tempfile + os.replace.

STRIPPED from the WyBot version:
  • prompts_enabled / set_prompts_enabled / is_prompts_enabled  (no LLM)
  • get_model / set_model  (no LLM)
  • SOCIAL_KINDS constant (no social posting)

KEPT:
  • enabled / set_enabled / is_enabled  (master kill-switch)
  • mark_ready / is_ready  (lifecycle for the dashboard)
  • duplicate-instance guard  (parallel-run safety)
  • log / get_log  (recent events for the dashboard)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
STATE_PATH = _HERE / "bot_state.json"
LIFECYCLE_PATH = _HERE / "bot_lifecycle.json"

# ---------------------------------------------------------------------------
# Duplicate-instance guard (same as WyBot)
# ---------------------------------------------------------------------------
# A "live" instance is one that beat its heartbeat in the last 12 seconds.
# If we find one (and it's not our own PID), we refuse to start.
LIVE_STALE_SECONDS = 12.0


def _atomic_write(path: Path, data: str) -> None:
    """Write `data` to `path` atomically (tempfile + os.replace)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(default)


_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_STATE_DEFAULTS = {
    "enabled": True,
}

_LIFECYCLE_DEFAULTS = {
    "state": "starting",   # starting → ready → stopped
    "started_at": None,
    "ready_at": None,
    "stopped_at": None,
    "pid": None,
    "last_beat": None,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    with _lock:
        return bool(_read_json(STATE_PATH, _STATE_DEFAULTS).get("enabled", True))


def set_enabled(value: bool, *, by: str = "") -> None:
    with _lock:
        data = _read_json(STATE_PATH, _STATE_DEFAULTS)
        data["enabled"] = bool(value)
        if by:
            data["enabled_by"] = by
        data["enabled_at"] = time.time()
        _atomic_write(STATE_PATH, json.dumps(data, indent=2))


def mark_ready() -> None:
    """Flip the lifecycle to 'ready'. Called once from on_ready."""
    with _lock:
        data = _read_json(LIFECYCLE_PATH, _LIFECYCLE_DEFAULTS)
        data["state"] = "ready"
        data["ready_at"] = time.time()
        data["pid"] = os.getpid()
        data["last_beat"] = time.time()
        _atomic_write(LIFECYCLE_PATH, json.dumps(data, indent=2))


def is_ready() -> bool:
    with _lock:
        data = _read_json(LIFECYCLE_PATH, _LIFECYCLE_DEFAULTS)
        return data.get("state") == "ready"


def mark_stopped() -> None:
    with _lock:
        data = _read_json(LIFECYCLE_PATH, _LIFECYCLE_DEFAULTS)
        data["state"] = "stopped"
        data["stopped_at"] = time.time()
        _atomic_write(LIFECYCLE_PATH, json.dumps(data, indent=2))


def heartbeat() -> None:
    """Refresh last_beat. Called every 4 s from the bot's background loop."""
    with _lock:
        data = _read_json(LIFECYCLE_PATH, _LIFECYCLE_DEFAULTS)
        data["last_beat"] = time.time()
        data["pid"] = os.getpid()
        _atomic_write(LIFECYCLE_PATH, json.dumps(data, indent=2))


def set_guild_count(n: int) -> None:
    """Record how many servers the bot is currently in (from discord.guilds).
    The dashboard reads this from the lifecycle file for its Servers counter.
    Called from on_ready and on_guild_join / on_guild_remove."""
    with _lock:
        data = _read_json(LIFECYCLE_PATH, _LIFECYCLE_DEFAULTS)
        data["guild_count"] = int(n)
        data["guild_at"] = time.time()
        _atomic_write(LIFECYCLE_PATH, json.dumps(data, indent=2))


def duplicate_info() -> dict | None:
    """Check whether another live instance is running (heartbeat < 12 s old,
    different PID). Returns {pid, last_beat, age_s} or None."""
    with _lock:
        data = _read_json(LIFECYCLE_PATH, _LIFECYCLE_DEFAULTS)
        last_beat = data.get("last_beat")
        if not last_beat:
            return None
        age = time.time() - last_beat
        if age > LIVE_STALE_SECONDS:
            return None
        own_pid = os.getpid()
        if data.get("pid") == own_pid:
            return None  # it's us
        return {
            "pid": data.get("pid"),
            "last_beat": last_beat,
            "age_s": round(age, 1),
            "state": data.get("state"),
        }


# ---------------------------------------------------------------------------
# Usage counters
# ---------------------------------------------------------------------------
# Two counters, both in bot_state.json (so they survive dashboard restarts and
# are visible to the web dashboard):
#
#   session_replies  — replies since the CURRENT bot start. Reset to 0 on each
#                      on_ready (a fresh bot process), so it tracks THIS session.
#   total_replies    — every reply across the bot's whole life. Never reset by
#                      a restart; only the operator can zero it (Reset button).
#
# Both are bumped by exactly one call site (on_app_command_completion in
# bot.py) — i.e. once per command that the user actually got a response to.


def _stats_default() -> dict:
    return {"session_replies": 0, "total_replies": 0}


def _read_stats() -> dict:
    data = _read_json(STATE_PATH, _STATE_DEFAULTS)
    return {
        "session_replies": int(data.get("session_replies", 0)),
        "total_replies": int(data.get("total_replies", 0)),
    }


def bump_reply() -> dict:
    """Record one successful command reply. Returns the new counter values."""
    with _lock:
        data = _read_json(STATE_PATH, _STATE_DEFAULTS)
        data["session_replies"] = int(data.get("session_replies", 0)) + 1
        data["total_replies"] = int(data.get("total_replies", 0)) + 1
        _atomic_write(STATE_PATH, json.dumps(data, indent=2))
        return {
            "session_replies": data["session_replies"],
            "total_replies": data["total_replies"],
        }


def reset_session() -> dict:
    """Start a fresh session: zero the session counter (keep the lifetime
    total). Called once from on_ready."""
    with _lock:
        data = _read_json(STATE_PATH, _STATE_DEFAULTS)
        data["session_replies"] = 0
        data["total_replies"] = int(data.get("total_replies", 0))
        _atomic_write(STATE_PATH, json.dumps(data, indent=2))
        return {"session_replies": 0, "total_replies": data["total_replies"]}


def get_stats() -> dict:
    with _lock:
        return _read_stats()


def reset_total() -> dict:
    """Zero the lifetime total (and the session counter). Operator-only, via
    the dashboard's Reset button."""
    with _lock:
        data = _read_json(STATE_PATH, _STATE_DEFAULTS)
        data["session_replies"] = 0
        data["total_replies"] = 0
        _atomic_write(STATE_PATH, json.dumps(data, indent=2))
        return {"session_replies": 0, "total_replies": 0}


# ---------------------------------------------------------------------------
# Log (for the dashboard's log-tail view)
# ---------------------------------------------------------------------------
# We piggyback on botlog.log() which writes to bot_log.jsonl.
# The dashboard just reads the tail of that file — no extra state here.
