#!/usr/bin/env python
"""dj_add.py — logic for the `/adddj` command: format a DJ suggestion and
package it for the master-list owner to review.

WHAT IT DOES
  Takes a DJ name + ONE link (Twitch, or any of the three VRCDN URLs, or a
  bare streamer name) and produces:
    - a clean, copy-paste-ready block that matches the master-list column
      layout (Twitch / VRCDN RTSP / VRCDN MPEG-TS / VRCDN preview), and
    - the exact title + body for a GitHub issue in the owner's repo — that
      issue IS the drop-box. The community member's suggestion lands in a
      place the owner checks on the dashboard; nothing is ever written to
      the Google Sheet or the repo files directly.

LINK CLASSIFICATION (the whole point — "use the /vrcdn logic")
  - Twitch link (twitch.tv / live.twitch.tv / any URL whose host ends in
    twitch.tv) → kept VERBATIM as the Twitch column.
  - A VRCDN URL (RTSP / MPEG-TS / preview) OR a bare streamer name →
    expanded by the SAME vrcdn module the /vrcdn command uses, so all three
    VRCDN columns are filled.
  - Any other URL → kept as-is, flagged "custom" (still submitted, but the
    owner should double-check it).

SECURITY
  - Pure string logic. No shell, no file writes, no network. Every field is
    treated as opaque text and trimmed; URLs are NOT executed.
  - The GitHub token (if present) is read from the process environment by
    the caller (bot.py / dashboard.py) — it never lives in this module, and
    it is never echoed into an issue body or a log line.

PURE + TESTABLE
  No discord, no bot_state, no flask. Headless testing is trivial:
    import dj_add
    dj_add.classify_link("https://twitch.tv/hyndal")
    dj_add.classify_link("rtspt://stream.vrcdn.live/live/NwinnVR")
"""
import re
from typing import Optional

import vrcdn  # same extraction engine the /vrcdn command uses

__all__ = ["classify_link", "render_block", "issue_payload"]

_TWITCH_HOST = re.compile(
    r"(^|\.)twitch\.tv$", re.IGNORECASE)


def _host(url: str) -> str:
    """Lower-cased host of a URL ('https://live.twitch.tv/x' -> 'live.twitch.tv').
    For scheme-less input (rare) the first dot-segment is used."""
    u = (url or "").strip()
    if "://" in u:
        u = u.split("://", 1)[1]
    # drop path/query/fragment
    u = u.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    # strip userinfo (user:pass@) if somehow present
    if "@" in u:
        u = u.split("@", 1)[1]
    return u.lower()


def is_twitch(url: str) -> bool:
    """True if the link is a Twitch channel/stream URL (any twitch.tv host)."""
    host = _host(url)
    return bool(host) and _TWITCH_HOST.search(host) is not None


def classify_link(link: str) -> dict:
    """Classify a submitted link and expand it into master-list columns.

    Returns:
      {"kind": "twitch", "link": <verbatim>, "vr": None}
      {"kind": "vrcdn",  "link": <verbatim>, "vr": {"rtsp","mpeg_ts","preview"}}
      {"kind": "custom", "link": <verbatim>, "vr": None, "note": "..."}
      {"kind": "bad",    "message": "..."}
    """
    raw = (link or "").strip()
    if not raw:
        return {"kind": "bad", "message": "The link is empty."}

    if is_twitch(raw):
        return {"kind": "twitch", "link": raw, "vr": None}

    # A scheme URL on a NON-vrcdn host (and not twitch) is NOT a VRCDN link —
    # keep it as a custom link rather than mis-parsing its last path segment
    # as a streamer name.
    if "://" in raw and "vrcdn" not in raw.lower():
        return {"kind": "custom", "link": raw, "vr": None,
                "note": "not a Twitch or VRCDN link — kept as-is, please verify"}

    # VRCDN URL? Use the exact /vrcdn parser (handles all three shapes and
    # strips .live.ts etc.), then build all three columns.
    if "vrcdn" in raw.lower() or vrcdn._looks_like_url(raw):
        res = vrcdn.parse(raw)
        if res.get("status") == "ok":
            return {
                "kind": "vrcdn",
                "link": raw,
                "vr": vrcdn.build_urls(res["username"]),
                "streamer": res["username"],
            }
        # Looked VRCDN-ish but the parser couldn't isolate a name —
        # fall through to 'custom' rather than dropping the suggestion.
        return {"kind": "custom", "link": raw, "vr": None,
                "note": "couldn't expand this into the three VRCDN links — please verify"}

    # A bare streamer name (no scheme, not a twitch host) → treat as VRCDN.
    res = vrcdn.parse(raw)
    if res.get("status") == "ok":
        return {
            "kind": "vrcdn",
            "link": raw,
            "vr": vrcdn.build_urls(res["username"]),
            "streamer": res["username"],
        }

    return {"kind": "custom", "link": raw, "vr": None,
            "note": "not a Twitch or VRCDN link — kept as-is, please verify"}


def render_block(name: str, link: str,
                 genres: Optional[str] = None,
                 availability: Optional[str] = None) -> tuple[str, dict]:
    """Produce the human-readable suggestion block + the classified dict.

    Returns (text, classified). The text is what the submitting host sees in
    Discord AND what lands in the GitHub issue body — one source of truth.
    """
    name = (name or "").strip()
    genres = (genres or "").strip()
    availability = (availability or "").strip()
    cls = classify_link(link)
    if cls["kind"] == "bad":
        return cls["message"], cls

    lines = [f"🎧 **{name}**"]
    if cls["kind"] == "twitch":
        lines.append(f"## Twitch: ```{cls['link']}```")
    elif cls["kind"] == "vrcdn":
        vr = cls["vr"]
        lines.append(f"## VRCDN (RTSP): ```{vr['rtsp']}```")
        lines.append(f"## VRCDN (MPEG-TS): ```{vr['mpeg_ts']}```")
        lines.append(f"## VRCDN (host preview): ```{vr['preview']}```")
    else:  # custom
        lines.append(f"## Link: ```{cls['link']}```")

    if genres:
        lines.append(f"Genres: {genres}")
    if availability:
        lines.append(f"Availability: {availability}")
    if cls["kind"] == "custom":
        lines.append(f"⚠️ {cls.get('note', 'non-standard link')}")

    return "\n".join(lines), cls


def issue_payload(name: str, link: str,
                  genres: Optional[str] = None,
                  availability: Optional[str] = None,
                  submitter: str = "", guild: str = "") -> dict:
    """Build the exact GitHub issue title + body for a DJ suggestion.

    The body is a tidy, copy-paste-ready table so the master-list owner can
    lift each field straight into the Google Sheet without re-formatting.
    """
    name = (name or "").strip()
    link = (link or "").strip()
    genres = (genres or "").strip()
    availability = (availability or "").strip()
    cls = classify_link(link)

    rows = [
        ("**DJ name**", name),
        ("**Submitted link**", link),
        ("**Link type**", cls["kind"].upper()),
    ]
    if cls["kind"] == "vrcdn" and cls.get("vr"):
        vr = cls["vr"]
        rows += [
            ("**Twitch**", "—"),
            ("**VRCDN RTSP**", vr["rtsp"]),
            ("**VRCDN MPEG-TS**", vr["mpeg_ts"]),
            ("**VRCDN preview**", vr["preview"]),
        ]
    elif cls["kind"] == "twitch":
        rows += [
            ("**Twitch**", link),
            ("**VRCDN RTSP**", "—"),
            ("**VRCDN MPEG-TS**", "—"),
            ("**VRCDN preview**", "—"),
        ]
    else:
        rows += [
            ("**Twitch**", "—"),
            ("**VRCDN RTSP**", "—"),
            ("**VRCDN MPEG-TS**", "—"),
            ("**VRCDN preview**", "—"),
        ]
    rows += [
        ("**Genres**", genres or "—"),
        ("**Availability**", availability or "—"),
    ]
    if submitter:
        rows.append(("**Submitted by**", f"{submitter}" + (f" ({guild})" if guild else "")))

    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    body = (
        "### 🎧 New DJ suggestion\n\n"
        f"{table}\n\n"
        "---\n"
        "_Submitted via `/adddj` by a community member. Please review, then add to the "
        "master list (Google Sheet) and close this issue. The sheet itself is never "
        "modified by the bot._\n"
    )
    return {
        "title": f"🎧 DJ suggestion: {name}",
        "body": body,
        "labels": ["dj-addition"],
    }


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    print("── classify_link ──")
    for case in [
        "https://twitch.tv/hyndal",
        "https://live.twitch.tv/channel/nwinnvr",
        "rtspt://stream.vrcdn.live/live/SuharpyVR",
        "https://stream.vrcdn.live/live/NwinnVR.live.ts",
        "https://panel.vrcdn.live/preview/Mous",
        "SuharpyVR",
        "https://example.com/stream",
        "",
    ]:
        c = classify_link(case)
        print(f"  {case!r:55} → {c['kind']}"
              + (f"  streamer={c.get('streamer')}" if c.get("streamer") else ""))

    print("\n── render_block (twitch) ──")
    txt, _ = render_block("Hyndal", "https://twitch.tv/hyndal",
                          genres="House, Trance", availability="Fri & Sat nights")
    print(txt)

    print("\n── render_block (vrcdn mpeg) ──")
    txt, _ = render_block("NwinnVR",
                          "https://stream.vrcdn.live/live/NwinnVR.live.ts",
                          genres="Techno")
    print(txt)

    print("\n── issue_payload (vrcdn) ──")
    p = issue_payload("Mous", "SuharpyVR",
                      genres="DnB", availability="weekends",
                      submitter="somehost", guild="SomeCommunity")
    print(p["title"])
    print(p["body"])
