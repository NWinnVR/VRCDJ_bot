"""scrub.py — output sanitizer for anything about to reach Discord.

SECURITY: nothing posted to Discord may carry ANSI escape sequences, local
filesystem paths, or control bytes. This module is a standalone, dependency-
free scrubber (lifted out of the private WyBot LLM bridge, which is NOT part
of this public bot). It is defense-in-depth: the DJ-lookup path only ever
renders data from the operator's own public Google Sheet, but we still strip
any path-like or escape-sequence token that could slip through so an internal
detail can never leak into a public Discord channel.

Preserves URLs (https://… and the stream link shapes the DJ list carries).
Idempotent — running it twice changes nothing.
"""
from __future__ import annotations

import re

# ANSI / VT escape sequences (terminal artifacts).
_ANSI_RE = re.compile(chr(27) + re.escape("[") + "[0-9;?]*[ -/]*[@-~]")
# Windows drive paths: C:\…  D:/…  (a drive letter, colon, then a separator).
_WS_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:(?:[\\/]+)[^\s:;,<>]+")
# Tilde paths: ~/\…  ~/…
_TILDE_RE = re.compile(r"(?<!\w)~(?:[\\/])[^\s:;,<>]+")
# POSIX absolute paths: /a/b/c  (two or more path segments, not a URL — the
# lookbehind excludes the "://" scheme part of an http(s) URL).
_POSIX_RE = re.compile(r"(?<![A-Za-z:/\w])/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")


def strip_ansi(s: str) -> str:
    """Remove ANSI/VT escape sequences safely."""
    if not s:
        return ""
    return _ANSI_RE.sub("", s)


def scrub(s: str) -> str:
    """Strip ANSI escapes + local filesystem paths. Preserves URLs.

    Returns '' for empty input. Never raises.
    """
    if not s:
        return ""
    s = strip_ansi(s)
    s = _WS_RE.sub("<path redacted>", s)
    s = _TILDE_RE.sub("<path redacted>", s)
    s = _POSIX_RE.sub("<path redacted>", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s
