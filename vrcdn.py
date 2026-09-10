"""vrcdn.py — VRCDN stream URL helpers for the WyBot `/vrcdn` command.

WHAT IT DOES
  A DJ who shares only ONE of their three VRCDN URLs (RTSP / MPEG-TS /
  preview) — or even just their bare streamer name — gets ALL three back,
  with the username in the right spot for each format:

      ## RTSP:       `rtspt://stream.vrcdn.live/live/<name>`
      ## MPEG-TS:    `https://stream.vrcdn.live/live/<name>.live.ts`
      ## Preview:    `https://panel.vrcdn.live/preview/<name>`

  The username is extracted EXACTLY as it appears (case preserved, no
  normalisation) and is NEVER shown as a bare label — only inside the three
  URLs. This is the deterministic "lookup tier" (no LLM, no model call):
  the bot parses one string and builds three.

THREE URL SHAPES (VRCDN, confirmed via the official wiki):
  - RTSP (PC, low-latency):  rtspt://stream.vrcdn.live/live/<name>
  - MPEG-TS (Quest, etc.):   https://stream.vrcdn.live/live/<name>.live.ts
  - Preview (host panel):    https://panel.vrcdn.live/preview/<name>

The username sits in a different position per format — that's the whole
point of this module.

PURE + TESTABLE
  No discord, no bot_state, no file I/O. bot.py's `cmd_vrcdn` calls
  `parse()` + `render()` and posts the result. Headless testing is
  trivial: import this module, call parse/render, assert on the strings.
"""
import re
from typing import Optional

__all__ = ["parse", "render", "build_urls"]

# The three canonical URL shapes, with the username in a {name} placeholder.
# (The MPEG-TS `.live.ts` suffix is the real VRCDN format — the streamer
#  name is followed by `.live.ts`, NOT the bare name alone.)
_URL_RTSP = "rtspt://stream.vrcdn.live/live/{name}"
_URL_MPEG = "https://stream.vrcdn.live/live/{name}.live.ts"
_URL_PREVIEW = "https://panel.vrcdn.live/preview/{name}"

# A username we will happily build URLs for. VRCDN usernames are short
# alphanumeric + underscore/hyphen/dot. We are deliberately LENIENT here
# (no length cap, no charset whitelist) so that an unusual-but-real username
# still works; we only REJECT things that are obviously not a username
# (empty, or containing whitespace / path separators / scheme junk).
#
# We do NOT try to be clever about validating VRCDN usernames — we extract
# the EXACT text the DJ gave us and trust that it's their real name. If it's
# wrong, the URLs will be wrong, and that's a DJ-side fix (re-run /vrcdn with
# the correct name), not a bot-side guess.
_BAD_USERNAME = re.compile(r"[\s/\\<>:|?\"'\u0000-\u001f\u007f]")


def _looks_like_url(s: str) -> bool:
    """True if the input is recognisably a URL (scheme:// or a vrcdn host)."""
    s = s.strip()
    if "://" in s:
        return True
    low = s.lower()
    return ("vrcdn.live" in low) or ("stream.vrcdn" in low) or ("panel.vrcdn" in low)


def _extract_from_url(s: str) -> Optional[str]:
    """Pull the streamer username out of ANY of the three VRCDN URL forms.

    Strategy: take the path portion after the host, then apply the shape we
    recognise:
      - .../live/<name>            (RTSP)          → <name>
      - .../live/<name>.live.ts    (MPEG-TS)       → <name> (strip .live.ts)
      - .../preview/<name>         (preview)       → <name>
    We key off the LAST path segment and strip known suffixes (.ts, .live.ts).
    Returns None if no username can be isolated.
    """
    s = s.strip()
    # Strip a trailing slash (harmless) and any query string (#… or ?…).
    s = s.split("#", 1)[0].split("?", 1)[0]

    # Normalise scheme to nothing for matching — keep the path.
    # Take everything after the last '/'-terminated host boundary.
    # We want the final path segment (the username position).
    if "/" not in s:
        return None
    # Get the path (drop scheme + host).
    # A URL looks like scheme://host/path…  →  find the first '/' after host.
    # Simpler: split on '//' then on '/' to get segments.
    rest = s.split("://", 1)
    pathpart = rest[1] if len(rest) == 2 else s
    # pathpart is "host/seg1/seg2…". Drop the host (first segment).
    segs = [seg for seg in pathpart.split("/") if seg != ""]
    if not segs:
        return None
    last = segs[-1]
    # Strip the known MPEG-TS suffixes (most-specific first).
    for suffix in (".live.ts", ".ts"):
        if last.lower().endswith(suffix):
            last = last[: -len(suffix)]
            break
    last = last.strip()
    if not last:
        return None
    if _BAD_USERNAME.search(last):
        return None
    return last


def parse(raw: str) -> dict:
    """Parse `/vrcdn` input into a streamer username.

    Returns a dict:
      {"status": "ok",  "username": "..."}
      {"status": "bad", "message": "..."}
    `status` is "ok" when we isolated a plausible username; "bad" otherwise.
    """
    raw = (raw or "").strip()
    if not raw:
        return {"status": "bad",
                "message": "Give me one of the three VRCDN URLs — or just the streamer name."}
    if _looks_like_url(raw):
        name = _extract_from_url(raw)
        if name:
            return {"status": "ok", "username": name}
        return {"status": "bad",
                "message": "That looked like a URL, but I couldn't pull a streamer name out of it. "
                           "Try the plain streamer name (e.g. `/vrcdn SuharpyVR`)."}
    # Not a URL — treat the whole input as a bare username.
    name = raw
    if _BAD_USERNAME.search(name):
        return {"status": "bad",
                "message": "I can't use that as a streamer name (it looks like more than one thing). "
                           "Paste ONE VRCDN URL, or just the bare name."}
    return {"status": "ok", "username": name}


def build_urls(username: str) -> dict:
    """Build all three canonical VRCDN URLs for a streamer username."""
    return {
        "rtsp":    _URL_RTSP.format(name=username),
        "mpeg_ts": _URL_MPEG.format(name=username),
        "preview": _URL_PREVIEW.format(name=username),
    }


def render(username: str) -> str:
    """Render the three URLs as a Discord block in the same `## Label: ```url```
    shape the `/dj` command uses — a big label + a single-click-copy code span,
    which is what matters when reaching for the copy button in a headset.
    """
    urls = build_urls(username)
    lines = [
        f"## RTSP: ```{urls['rtsp']}```",
        f"## MPEG-TS: ```{urls['mpeg_ts']}```",
        f"## Preview: ```{urls['preview']}```",
    ]
    return "\n".join(lines)
