"""
botlog.py — one shared, append-only activity log for the Wyvern bot + dashboard.

Every interesting event — who asked what, staff toggles, model changes,
DJ refreshes, convo start/end, errors — lands here as a single JSON line.
The dashboard's live log pane just tails this file; the bot writes to it.
No server, no DB, no side effects beyond appending one line.

File: bot_activity.jsonl next to this module.
Each line: {"ts": "2026-08-19T10:30:00.123", "kind": "ask", "user": "nadia#1234",
            "user_id": "123…", "text": "what's up", "detail": "…", "level": "info"}

kinds (what the log pane groups by):
  ask        — /ask or @mention question (user + question text)
  answered   — an ask completed (model + how long it took)
  convo      — /startconvo / /endconvo / in-thread prompt
  dj         — /dj lookup + outcome (exact/didyoumean/ambiguous/none)
  host       — /host lookup + outcome
  toggle     — /on /off
  model      — model changed (dashboard)
  refresh    — DJ list refreshed (n DJs, ok/fail)
  setup      — /setup ran in a server
  error      — anything that failed
  system     — bot start/stop, dashboard actions
  boot       — bot starting up (token/model/schedule pre-flight)
  ready      — connected + commands synced (fully serving users)
  bridge     — model bridge health-check result (background)
  stopped    — bot shutting down gracefully
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "bot_activity.jsonl")
MAX_BYTES = 5 * 1024 * 1024          # rotate at ~5 MB
MAX_BACKUPS = 3                     # bot_activity.jsonl.1 … .3

_LOCK = threading.Lock()


def log(kind: str, *, who: str = "", user: str = "", user_id: str = "",
        text: str = "", detail: str = "", level: str = "info",
        guild: str = "", session: str = "") -> None:
    """Append one structured event. Never raises — logging must not
    break a command, so every failure is swallowed silently.

    ``who`` is a convenience alias for ``user`` (the asker's display name).
    If both are given, ``who`` wins. ``guild`` / ``session`` are optional
    context (server name, hermes session) recorded when present."""
    try:
        _rotate_if_needed()
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "kind": kind,
            "user": who or user,
            "user_id": user_id,
            "text": text[:2000],
            "detail": detail[:2000],
            "level": level,
        }
        if guild:
            entry["guild"] = guild[:200]
        if session:
            entry["session"] = session[:200]
        with _LOCK:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def fmt_duration(seconds: float) -> str:
    """Human duration for log detail: 0.8s → '0.8s', 75.3 → '1m 15s'."""
    try:
        seconds = max(0.0, float(seconds))
    except Exception:
        return "?"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s:02d}s"


def _rotate_if_needed() -> None:
    try:
        if not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) < MAX_BYTES:
            return
        with _LOCK:
            for i in range(MAX_BACKUPS, 0, -1):
                src = LOG_PATH + (f".{i}" if i else "")
                dst = LOG_PATH + f".{i + 1}"
                if i and os.path.exists(src):
                    os.replace(src, dst)
            if os.path.exists(LOG_PATH):
                os.replace(LOG_PATH, LOG_PATH + ".1")
    except Exception:
        pass


def tail(n: int = 200) -> list[dict]:
    """Return the last n log entries as a list of dicts (oldest first).
    Tolerates partial/rotated files — a corrupt line is skipped."""
    if not os.path.exists(LOG_PATH):
        return []
    entries: list[dict] = []
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return entries[-n:]


# Prompt events — the ones that count as "a person actually asked the bot
# something" (the /ask + @mention + in-thread prompts). DJ/host lookups are
# lookups, not prompts, so they're deliberately excluded from this count.
PROMPT_KINDS = {"ask", "ask_mention", "ask_thread"}

# Lookup events — /dj, /host, and /djlineup calls. The bot logs
# exactly ONE entry per command, even when the user names several DJs/hosts at
# once (they're comma-split into individual lookups but still a single
# botlog.log call), so counting these entries = counting single queries, NOT
# individual names. /djlineup is a DJ workflow lookup (staff prep an event's
# slot labels for the DJ sign-up list).
LOOKUP_KINDS = {"dj", "host", "djlineup"}


def _parse_ts(s: str):
    """Parse a log timestamp to a naive datetime, or None if unparseable.
    Tolerates three shapes:
      - legacy local, second-only:  '2026-08-20T14:31:02'
      - legacy local, millis:       '2026-08-20T14:31:02.513'
      - current UTC (Z-suffixed):   '2026-09-10T20:03:05.130Z'
    The fractional seconds and any 'Z' suffix are dropped; the value is
    returned as a naive datetime (wall-clock), so old and new entries are
    comparable against each other and against other naive timestamps."""
    s = (s or "").strip()
    if not s:
        return None
    # drop fractional seconds ('.513') and a trailing 'Z' (UTC marker)
    base = s.split(".", 1)[0]
    if base.endswith("Z"):
        base = base[:-1]
    try:
        return datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def count_prompts(since_ts: str | None = None) -> int:
    """Count prompt events (kinds in PROMPT_KINDS) in the live log.

    ``since_ts`` — optional 'YYYY-MM-DDTHH:MM:SS' local timestamp; when given,
    only events at or after that instant are counted (used for the
    'since bot started' / 'since this dashboard opened' counters).
    Tolerates partial/rotated files — corrupt lines are skipped.
    """
    from datetime import datetime
    since_dt = _parse_ts(since_ts) if since_ts else None
    n = 0
    if not os.path.exists(LOG_PATH):
        return 0
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("kind") not in PROMPT_KINDS:
                    continue
                if since_dt is not None:
                    e_dt = _parse_ts(e.get("ts", ""))
                    if e_dt is None or e_dt < since_dt:
                        continue
                n += 1
    except Exception:
        pass
    return n


# Social-post events — the ones that count as "a social post was MADE". The
# bot logs exactly ONE entry per command invocation (before it even attempts
# to build the draft/post), so counting these = counting times the command
# was run, NOT the individual drafts/approvals it produces.
#   draftsocials — /draftsocials (event → Bluesky/Twitter/vrc.tl drafts)
#   worldopen    — /worldopen   (the "Doors are Open" instance announcement,
#                    posted separately when the instance goes joinable)
SOCIAL_KINDS = {"draftsocials", "worldopen"}


def count_social(since_ts: str | None = None) -> int:
    """Count social-post invocations (/draftsocials + /worldopen) in the log.

    Each of those commands produces exactly ONE log entry per use — so this
    counts single COMMAND USES, not the drafts/approvals it yields.

    ``since_ts`` — optional 'YYYY-MM-DDTHH:MM:SS' local timestamp; when given,
    only events at or after that instant are counted (used for the
    'since this dashboard opened' counter). Tolerates partial/rotated files.
    """
    since_dt = _parse_ts(since_ts) if since_ts else None
    n = 0
    if not os.path.exists(LOG_PATH):
        return 0
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("kind") not in SOCIAL_KINDS:
                    continue
                if since_dt is not None:
                    e_dt = _parse_ts(e.get("ts", ""))
                    if e_dt is None or e_dt < since_dt:
                        continue
                n += 1
    except Exception:
        pass
    return n


def count_lookups(since_ts: str | None = None) -> int:
    """Count DJ/host/lineup lookup events (kinds in LOOKUP_KINDS) in the live log.

    Each /dj, /host, or /djlineup command produces exactly ONE log entry — even
    when the user names several DJs/hosts in one command (comma-separated) — so
    this counts single queries, NOT the individual names within a query.

    ``since_ts`` — optional 'YYYY-MM-DDTHH:MM:SS' local timestamp; when given,
    only events at or after that instant are counted (used for the
    'since dashboard opened' counter). Tolerates partial/rotated files —
    corrupt lines are skipped.
    """
    since_dt = _parse_ts(since_ts) if since_ts else None
    n = 0
    if not os.path.exists(LOG_PATH):
        return 0
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("kind") not in LOOKUP_KINDS:
                    continue
                if since_dt is not None:
                    e_dt = _parse_ts(e.get("ts", ""))
                    if e_dt is None or e_dt < since_dt:
                        continue
                n += 1
    except Exception:
        pass
    return n


def clear() -> None:
    """Clear the live log while preserving the recent history.

    Shifts the backup chain (.3→dropped, .2→.3, .1→.2) and moves the live
    file into slot .1, so the last few generations of events are still
    recoverable on disk — only the on-screen / tailed log is emptied.
    """
    try:
        with _LOCK:
            for i in range(MAX_BACKUPS, 0, -1):
                src = LOG_PATH + (f".{i}" if i else "")
                dst = LOG_PATH + f".{i + 1}"
                if i and os.path.exists(src):
                    os.replace(src, dst)
            if os.path.exists(LOG_PATH):
                os.replace(LOG_PATH, LOG_PATH + ".1")
    except Exception:
        pass


if __name__ == "__main__":
    for e in tail(30):
        print(f"{e.get('ts','?')}  {e.get('kind','?'):<9} "
              f"{e.get('user','')[:18]:<18} {e.get('text','')[:70]}")
