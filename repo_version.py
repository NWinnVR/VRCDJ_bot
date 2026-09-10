"""repo_version.py — cheap, cached git-version introspection for the dashboard.

Used to show, at the top of the window and under the BOT PROCESS status:
  • the commit (7-char short id) the running code is on, and
  • how many commits it sits behind the local repo HEAD.

Cost model (matters — this runs while the dashboard is open):
  • Every result is cached with a TTL (default 60 s). A `git` subprocess is
    spawned ONLY on a cache miss, so at most ~1-2 spawns per minute, and
    usually zero after the first (the dashboard pre-warms the cache on a
    background thread at startup).
  • `git rev-parse` and `git rev-list --count` are local-only operations —
    no network, no remote access, and on a repo this small they take
    single-digit milliseconds.

All functions are total: any failure (no git, not a repo, bad sha) yields
None / False / (0, "unknown") rather than raising, so a display hiccup can
never break the dashboard.
"""
from __future__ import annotations

import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# How long a cached answer stays "good enough" before re-checking git.
_TTL_S = 60.0
# Hard cap on any single git call — a wedded git must never hang the UI.
_TIMEOUT_S = 5.0

# Windows: spawn git WITHOUT a console window. Without this flag every cache
# miss pops a cmd.exe window for a fraction of a second (visible flash once a
# minute). On POSIX this attribute is 0 / ignored, so it's safe everywhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_cache: dict[str, tuple[float, object]] = {}


def _git(*args: str) -> str | None:
    """Run a git command in this directory; return stripped stdout or None.

    Spawned with CREATE_NO_WINDOW on Windows so the child never grabs a
    visible console.  stdin is DEVNULL so git can never block reading
    stdin.  On timeout the child is explicitly killed AND its pipes are
    drained, so a wedged git.exe cannot leave a dangling pipe that keeps
    the calling thread alive forever.
    """
    try:
        p = subprocess.Popen(
            ["git", "-C", HERE, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        try:
            out, _err = p.communicate(timeout=_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                p.communicate(timeout=2)
            except Exception:
                pass
            return None
        if p.returncode != 0:
            return None
        return (out or b"").decode("utf-8", "replace").strip()
    except Exception:
        return None


def _cached(key: str, fn) -> object:
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _TTL_S:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def clear_cache() -> None:
    """Force the next reads to re-check git (used by the ⟳ refresh button)."""
    _cache.clear()


def get_local_head() -> str | None:
    """7-char short id of the local repo HEAD (this working tree)."""
    full = _cached("head", lambda: _git("rev-parse", "HEAD"))
    return (full or "")[:7] or None


def commit_known(sha: str) -> bool:
    """True if `sha` (full or abbreviated) resolves in the local history."""
    def check():
        r = _git("cat-file", "-e", f"{sha}^{{commit}}")
        return r is not None
    return bool(_cached(f"known:{sha}", check))


def commits_between(ancestor: str, descendant: str) -> int | None:
    """Commit count on `descendant`'s history that is NOT on `ancestor`'s.

    This is 'how far `descendant` is ahead of `ancestor`'. Callers pass the
    pair in the order that makes the answer meaningful:
      commits_between(bot_commit, local_head) → bot is N commits BEHIND local
    None if either sha can't be resolved locally (e.g. the bot was started
    from a clone that never fetched this commit).
    """
    def count():
        if not commit_known(ancestor) or not commit_known(descendant):
            return None
        r = _git("rev-list", "--count", f"{ancestor}..{descendant}")
        return int(r) if r is not None else None
    return _cached(f"between:{ancestor}:{descendant}", count)
