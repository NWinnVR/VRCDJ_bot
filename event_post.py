"""event_post.py — detect Wyvern event posts and extract the DJ lineup.

Pure local logic (NO LLM): a message counts as an EVENT POST when it carries
at least `min_dj_lines` lines that each start with a Discord *time-slot*
timestamp (`<t:NNNNNNNNNNN:t>`) followed by a bold DJ name. That multi-line
time-slot signature is what separates an event lineup from ordinary chat —
a casual message essentially never has 3+ time-slot lines with names.

This is the "runs on its own" tier: same deterministic local logic as the
/dj and /host lookups, so it works even while prompting is paused and never
touches the model.

The DJ-line format varies by author. Every real variant we've seen:

    <t:1780711200:t> - **DJ 1**                 (template)
    <t:1780711200:t> **__SynHaptX Fire__**      (bold + underline)
    <t:1779843600:t> __**Mikeythemaniac**__     (underline outside bold)
    <t:1779505200:t> - :logo_emoji: - **__Moxel__**   (collab: community
                                                       logo before the name)

In every case the DJ name is the LAST bold (`**...**`) segment on the line;
everything before it (dashes, community-logo emojis, extra spaces) is
decoration and is ignored. We therefore take the last bold match per line
and peel the markdown wrappers around it.

Note: the "Event Date" line uses the FULL-DATE formats (`:F>`, `:f>`, `:R>`),
never the `:t>` time format, so it is correctly NOT mistaken for a DJ slot.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# A time-slot timestamp. The value may be empty in the blank template
# (`<t::t>`), hence `\d*` (zero or more digits) before the `:t>` format code.
_TS_RE = re.compile(r"<t:\d*:t>")

# The DJ name is whatever sits AFTER the time-slot timestamp, with the
# decoration in front of it (dashes, community-logo emoji, extra spaces)
# stripped and the markdown emphasis wrappers (``**``, ``__``) peeled off the
# ends. Hosts do NOT always bold/underline the name — every real style we've
# seen is handled in extract_name_from_line().

# Custom / animated emoji tokens. Two forms:
#   static:   <:00_Wyvern_Logo:1280009420062986393>   ->  <  :  name  :  id  >
#   animated: <a:HYPERHEADBANG:1391557208881234003>   ->  <  a  :  name  :  id  >
# So it's  <  (optional a)  :  name  :  digits  >.  Collab-community logos that
# some posts put on the same line as the DJ name — pure decoration, ignore them.
_CUSTOM_EMOJI_RE = re.compile(r"<a?:[\w]+:\d+>")

# Static unicode emoji tokens:  :uThoughtiWouldnt:  :Discord:  :00_Wyvern_Logo:
_STATIC_EMOJI_RE = re.compile(r":[\w]+:")

# Separators that can sit between the timestamp / emoji and the name: dashes,
# pipes, middle-dots, and runs of whitespace. (strip() removes these from the
# ends only — a dash INSIDE a name like "Dj-DeadZed" is preserved.)
_SEP_CHARS = " \t\r-—–·•|"

# Non-DJ filler slots that show up in the template / real posts. Compared
# case-insensitively with all whitespace removed, so "OPEN DECKS" and
# "open decks" both match.
_FILLER = {
    "OPENDECKS", "OPENDECK", "DECKS", "OPEN",
    "TBA", "TBD", "TAN", "TBADecks", "TBAD",
    "TBADECKS", "TBADECK", "TANDECKS", "TANDECK",
    "ANYNAME", "DJNAME", "DJNAMEHERE", "NAMEHERE", "DJHERE",
}


def _clean_name(raw: str) -> str:
    """Peel markdown emphasis wrappers off a captured name.

    Handles the nesting styles we actually see: ``__Moxel__``,
    ``**Moxel**``, ``__*Moxel*__``, etc., WITHOUT touching underscores that
    are part of the real name (a DJ called ``some_name`` keeps its underscore).
    """
    t = (raw or "").strip()
    changed = True
    while changed and len(t) >= 2:
        changed = False
        if t.startswith("**") and t.endswith("**") and len(t) > 4:
            t = t[2:-2]; changed = True
        elif t.startswith("__") and t.endswith("__") and len(t) > 4:
            t = t[2:-2]; changed = True
        elif t.startswith("*") and t.endswith("*") and len(t) > 2:
            t = t[1:-1]; changed = True
        elif t.startswith("_") and t.endswith("_") and len(t) > 2:
            t = t[1:-1]; changed = True
    # Collapse internal whitespace runs to a single space.
    t = " ".join(t.split())
    return t.strip()


def _is_filler(name: str) -> bool:
    n = name.upper().replace(" ", "")
    return n in _FILLER


def _has_time_slot(line: str) -> bool:
    return bool(_TS_RE.search(line))


def count_dj_lines(text: str) -> int:
    """How many lines carry a time-slot timestamp (the event-post signature)."""
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if _has_time_slot(line))


def extract_name_from_line(line: str) -> str:
    """Pull the DJ name out of one time-slot line, in whatever style it's
    written.

    The name always sits AFTER the time-slot timestamp. Everything before it
    (dashes, community-logo / static emoji, extra spaces) is decoration and
    stripped, and the markdown emphasis wrappers (``**``, ``__``) are peeled
    off the ends. Works whether the name is bold, underlined, plain, has a
    leading dash, or carries a collab-community emoji on the same line.
    Returns "" if nothing readable is left.
    """
    s = line
    m = _TS_RE.search(s)
    if m:
        s = s[m.end():]
    s = _CUSTOM_EMOJI_RE.sub(" ", s)
    s = _STATIC_EMOJI_RE.sub(" ", s)
    s = s.strip(_SEP_CHARS)
    return _clean_name(s)


def parse_dj_names(text: str) -> List[str]:
    """Extract DJ names from an event post body, in post order.

    Handles every author style we've seen — bold, underline, plain, a leading
    dash, or a collab-community emoji on the same line. Each name is cleaned
    of markdown/emoji. Template filler slots (OPEN DECKS, TBA, ...) are
    skipped so they don't generate spurious "not found" lines.
    """
    names: List[str] = []
    if not text:
        return names
    for line in text.splitlines():
        if not _has_time_slot(line):
            continue
        name = extract_name_from_line(line)
        if not name or _is_filler(name):
            continue
        names.append(name)
    return names


def is_event_post(text: str, min_dj_lines: int = 3) -> bool:
    """True if `text` looks like a Wyvern event post (>= min_dj_lines slots)."""
    return count_dj_lines(text) >= max(1, int(min_dj_lines))


def extract_lineup(text: str, min_dj_lines: int = 3) -> Dict[str, Any]:
    """One-stop detect + extract.

    Returns ``{"is_event": bool, "names": [str, ...]}``. When ``is_event``
    is False the names list is empty and callers should ignore the message.
    """
    if not is_event_post(text, min_dj_lines):
        return {"is_event": False, "names": []}
    return {"is_event": True, "names": parse_dj_names(text)}


if __name__ == "__main__":
    # Tiny smoke test: a minimal event post must be detected and parsed.
    sample = (
        "# Wyvern: Test Night\n"
        "- **Location**: BeachVern\n"
        "## Event Date: <t:1780711200:F> (<t:1780711200:R>)\n"
        "<t:1780711200:t> **__Alpha__**\n"
        "<t:1780714800:t> __**Beta**__\n"
        "<t:1780718400:t> - :logo: - **__Gamma__**\n"
    )
    res = extract_lineup(sample)
    assert res["is_event"], "smoke: should be an event post"
    assert res["names"] == ["Alpha", "Beta", "Gamma"], res["names"]

    # Every real author style (with / without bold, underline, a leading dash,
    # and a collab-community emoji on the line) must yield the bare name.
    variants = [
        "<t:1780711200:t> __SynHaptX Fire__",
        "<t:1780711200:t> **SynHaptX Fire**",
        "<t:1780711200:t> SynHaptX Fire",
        "<t:1780711200:t> - SynHaptX Fire",
        "<t:1780711200:t> - **__SynHaptX Fire__**",
        "<t:1780711200:t> <:00_Wyvern_Logo:1280009420062986393> __SynHaptX Fire__",
        "<t:1780711200:t> <:00_Wyvern_Logo:1280009420062986393> **SynHaptX Fire**",
        "<t:1780711200:t> <:00_Wyvern_Logo:1280009420062986393> SynHaptX Fire",
        "<t:1780711200:t> <:00_Wyvern_Logo:1280009420062986393> - SynHaptX Fire",
        "<t:1780711200:t> <:00_Wyvern_Logo:1280009420062986393> - **__SynHaptX Fire__**",
    ]
    for v in variants:
        got = extract_name_from_line(v)
        assert got == "SynHaptX Fire", f"variant failed: {v!r} -> {got!r}"

    # A casual single-slot message must NOT be detected.
    casual = "hey anyone up? <t:1780711200:t> **one line** only"
    assert not extract_lineup(casual)["is_event"], "smoke: casual false-positive"
    print("OK: event_post self-test passed ->", res["names"])
