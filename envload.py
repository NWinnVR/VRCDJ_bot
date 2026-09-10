"""envload.py — a tiny, dependency-free .env loader.

Reads KEY=VALUE lines from a .env file and sets them into os.environ
(without overriding variables that are ALREADY set). No third-party
packages, so VRCDJ_bot stays portable: `pip install discord.py Flask`
is all a fresh machine needs.

Rules (same as python-dotenv, minus the extras):
  - blank lines and `#` comments are ignored
  - `export FOO=bar` works (the `export ` prefix is stripped)
  - values may be quoted (single or double) — quotes are removed
  - inline comments are NOT supported (keep it simple & predictable)
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path, *, override: bool = False) -> dict[str, str]:
    """Load `path` (a .env file) into os.environ. Missing file is fine.

    Returns the dict of keys actually loaded (empty if nothing to do)."""
    p = Path(path)
    if not p.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if value and value[0] not in ("'", '"'):
            # unquoted: drop an inline comment (a '#' preceded by whitespace)
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1] in " \t"):
                    value = value[:i]
                    break
            value = value.strip()
        # strip one layer of matching quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
