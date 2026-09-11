"""signup.py — the `/signup` command engine (DJ / dancer slot auto-signup).

WHAT IT DOES
  A host wants people to sign up for their OWN slots on a gogo stage or DJ
  rotation WITHOUT the host babysitting the channel, and WITHOUT anyone
  double-booking.  `/signup` builds the event post, then everyone clicks their
  slot on a live board — the moment a name lands, it's visible to everyone,
  and the per-slot / per-person limits are enforced.

  Flow (see bot.py cmd_signup + signup_ui.py):
      /signup <start> <slots> <slot_time> [doors] [per_slot] [per_person]
        → bot opens a MODAL ("Event Creation") prefilled with the template
        → host reviews / edits the text, hits Submit
        → bot posts the event + a "Pick Slot" + "Edit Event" button row
        → "Pick Slot" → an EPHEMERAL (you-only) prompt with a `#1…#N` button
          grid; click a slot to place your name, click it again to cancel
        → "Edit Event" → re-opens the modal; saving edits the SAME post and
          PRESERVES any already-filled slots (matched by timestamp)

  This module is PURE — no discord, no bot_state, no file I/O.  It renders the
  text the bot will post, parses a host's free-form slot list back out of the
  template, and formats durations.  `bot.py`'s `cmd_signup` + `signup_ui.py`
  call these; `signup_store.py` owns the state.  Headless testing is trivial:
  import this module, call the functions, assert on the strings
  (`python signup.py`).

Fuzzy, forgiving inputs (reuses the proven /timeslots parsers where it can):
  * start time   : "10pm ET" · "22:00" · "1788670800" (unix) · "<t:1788670800:t>"
  * slot count   : "6" · "eight"
  * slot time    : "1 hour" · "1.5 hours" · "90 min" · "45"  (bare number = min)
  * doors lead   : "15" (default, minutes before slot 1) · "30 min" · "1 hour"
  * day          : "friday" · "June 5th" · "2026-09-11"   (only needed for a
                   time-of-day start; a unix timestamp carries its own date)

OUTPUT (the golden template — exactly the format Nadia wants):

      # New Event
      - Doors Open: **__<t:1788669000:F>__** (<t:1788669000:R>)
      - Each slot is 60 minutes long
      - Click on the slots you want to sign-up for

      > #1, <t:1788670800:t>
      > #2, <t:1788674400:t>
      > #3, <t:1788678000:t>
      ...
      > #6, <t:1788688800:t>

  (Doors = slot #1 minus the lead, default 15 min.  "60 minutes long" is
  computed from the slot time and switches to hours past 60.)
"""
from __future__ import annotations

import re
from typing import Optional, List

import timeslots  # reuse parse_start_time / parse_day / parse_duration / slots

__all__ = [
    "MAX_SLOTS", "MAX_SLOTS_EDIT", "MAX_TITLE_LEN", "MAX_TEXT_LEN",
    "DEFAULT_DOORS_LEAD", "DEFAULT_SLOT_SECONDS", "DEFAULT_PER_SLOT",
    "DEFAULT_PER_PERSON",
    "build_slots", "format_duration", "render_template",
    "render_event", "parse_event_text", "slot_label", "mention",
]

# --- caps (Discord message = 2000 chars; stay safely under) -----------------
MAX_SLOTS = 50          # a gogo-stage / DJ night realistically never needs more
MAX_SLOTS_EDIT = 80     # a host editing an existing event may add a few
MAX_TITLE_LEN = 100
MAX_TEXT_LEN = 1900

# --- defaults ---------------------------------------------------------------
DEFAULT_DOORS_LEAD = 15            # minutes before slot 1 ("" / 0 = omit line)
DEFAULT_SLOT_SECONDS = 3600        # one-hour sets unless told otherwise
DEFAULT_PER_SLOT = 1               # one name per slot unless told otherwise
DEFAULT_PER_PERSON = 1             # one slot per person unless told otherwise


# ---------------------------------------------------------------------------
# duration formatting  (minutes ↔ hours)
# ---------------------------------------------------------------------------
def format_duration(seconds: int) -> str:
    """Human label for a slot length: 3600 -> "1 hour", 900 -> "15 minutes".

    Whole hours read as hours; whole minutes as minutes; otherwise fall back
    to minutes.  (Discord can't show "1.5 hours" cleanly, so 1800 -> "30
    minutes".)  Used for the "Each slot is N long" line and any UI copy.
    """
    if seconds <= 0:
        return "0 minutes"
    if seconds % 3600 == 0:
        h = seconds // 3600
        return f"{h} hour" if h == 1 else f"{h} hours"
    if seconds % 60 == 0:
        m = seconds // 60
        return f"{m} minute" if m == 1 else f"{m} minutes"
    return f"{seconds} seconds"


# ---------------------------------------------------------------------------
# slot construction
# ---------------------------------------------------------------------------
def build_slots(start_time: str, slot_count: str, slot_time: str,
                day: Optional[str] = None) -> List[int]:
    """Return the list of slot UNIX timestamps (first = the host's start time).

    `start_time` may be a time-of-day (needs `day`) or a full timestamp (its
    own date wins, `day` ignored).  `slot_time` is the spacing between slots.
    Reuses the exact /timeslots parsers so the DST math is identical.
    """
    start_ts = timeslots.parse_start_time(
        start_time, ref=timeslots.parse_day(day) if day else None)
    n = _parse_count(slot_count)
    step = timeslots.parse_duration(slot_time or "1 hour")
    return timeslots.slots(start_ts, n, step)


def _parse_count(slot_count: str) -> int:
    """Slot count: "6" or a small word ("eight").  Reuses /timeslots' map."""
    text = (slot_count or "").strip()
    if not text:
        raise ValueError("How many slots?  Give me a number — e.g. “6”.")
    try:
        n = int(text)
    except ValueError:
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14}
        w = text.lower().strip().rstrip("s")
        if w in words:
            n = words[w]
        else:
            raise ValueError(f"How many slots?  Give me a number — e.g. “6”. (got {text!r})")
    if n < 1:
        raise ValueError("Need at least one slot.")
    return n


def slot_label(i: int) -> str:
    """The `#N` label for slot index `i` (0-based) -> "#1", "#2", …"""
    return f"#{i + 1}"


def mention(user_id: int) -> str:
    """A clickable @-mention markdown token for a user id."""
    return f"<@{int(user_id)}>"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _doors_line(doors_ts: int) -> str:
    """The doors-open line (Nadia's exact format)."""
    return (f"- Doors Open: **__<t:{doors_ts}:F>__** (<t:{doors_ts}:R>)")


def render_template(title: str,
                    slot_timestamps: List[int],
                    slot_seconds: int,
                    doors_ts: Optional[int]) -> str:
    """The prefill for the "Event Creation" modal (NO names yet).

    Layout = Nadia's golden template: `# title`, the info bullets (doors /
    slot length / pick instruction), a blank line, then the `> #N, <t:…:t>`
    slot block.
    """
    lines = [f"# {title.strip() or 'New Event'}"]
    if doors_ts:
        lines.append(_doors_line(doors_ts))
    lines.append(f"- Each slot is {format_duration(slot_seconds)} long")
    lines.append("- Click on the slots you want to sign-up for")
    lines.append("")
    lines.append("\n".join(f"> {slot_label(i)}, <t:{ts}:t>"
                           for i, ts in enumerate(slot_timestamps)))
    return "\n".join(lines)


def render_event(title: str,
                 slot_timestamps: List[int],
                 slot_seconds: int,
                 doors_ts: Optional[int],
                 assignments: Optional[dict] = None) -> str:
    """The LIVE event post (slots + info + a name under each filled slot).

    `assignments` maps slot index (0-based) -> list of user ids.  A filled
    slot gets its names appended to the line:  `> #1, <t:…:t>  <@id> <@id>`.
    Names are "space-space" separated (two spaces -> a hard line break in
    Discord) so multiple people on one slot stack cleanly.  Empty slots keep
    the bare `> #N, <t:…:t>` so everyone can see what's open.
    Layout matches the modal: `# title`, info bullets, blank line, slots.
    """
    assignments = assignments or {}
    lines = [f"# {title.strip() or 'New Event'}"]
    if doors_ts:
        lines.append(_doors_line(doors_ts))
    lines.append(f"- Each slot is {format_duration(slot_seconds)} long")
    lines.append("- Click on the slots you want to sign-up for")
    lines.append("")
    for i, ts in enumerate(slot_timestamps):
        line = f"> {slot_label(i)}, <t:{ts}:t>"
        ids = assignments.get(i) or assignments.get(str(i)) or []
        if ids:
            line += "  " + "  ".join(mention(u) for u in ids)
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# parsing (read the host's edited modal text back into structured data)
# ---------------------------------------------------------------------------
_TITLE_RE = re.compile(r"^\s*(?:#|##|###)\s+(.*\S)\s*$", re.M)
# A slot line:  > #2, <t:1788674400:t>   (the "> " quote and "#N," are both
# optional — a host may type the slot however; what matters is the timestamp).
# Match the <t:…:t> token anywhere on a line (a host may prefix it with a
# label like "10:00" or "slot A").  The doors line uses :F / :R (never :t),
# so it will not be mistaken for a slot.
_SLOT_LINE_RE = re.compile(
    r"(?:#\s*(\d+)\s*[,.)]?\s*)?(<t:(\d+):t>)", re.I)
# The doors-open line:  - Doors Open: **__<t:…:F>__** (<t:…:R>)
_DOORS_RE = re.compile(r"doors\s*open[^>]*<t:(\d+):[fF]", re.I)
# The slot length:  Each slot is 90 minutes long
_DUR_RE = re.compile(r"each slot is\s+(.+?)\s+long", re.I)


def parse_event_text(text: str) -> dict:
    """Parse a host's modal text back into the structured event.

    Returns:
      {
        "title":        str,                 # from the first `# heading`
        "slots":        [int, ...],          # slot timestamps, in listed order
        "slot_seconds": int,                 # from "each slot is N long"
        "doors_ts":     int | None,          # from the doors-open line
      }

    Tolerant of how the host types it (quote, "#N," prefix, ordering).  If a
    slot block is present it is authoritative for the slot list; the duration
    falls back to the spacing between consecutive slots when not stated.
    """
    text = text or ""

    tm = _TITLE_RE.search(text)
    if tm:
        title = tm.group(1).strip()
    else:
        # No `# heading` — tolerate a bare title line (the first line that is
        # not blank, not a `>` slot quote, not a `-` info bullet, and not a
        # `<t:…:t>` slot timestamp).  In the live flow the modal's name field
        # is authoritative; this is just a tolerant fallback.
        title = ""
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith(">") or s.startswith("-"):
                continue
            if re.search(r"<t:\d+:t>", s, re.I):
                continue
            title = s
            break
        title = title or "New Event"

    slots: List[int] = []
    for m in _SLOT_LINE_RE.finditer(text):
        ts = int(m.group(3))
        if not slots or slots[-1] != ts:
            slots.append(ts)

    doors = None
    dm = _DOORS_RE.search(text)
    if dm:
        doors = int(dm.group(1))

    dur_match = _DUR_RE.search(text)
    slot_seconds = DEFAULT_SLOT_SECONDS
    if dur_match:
        try:
            slot_seconds = max(1, timeslots.parse_duration(dur_match.group(1)))
        except ValueError:
            slot_seconds = DEFAULT_SLOT_SECONDS
    elif len(slots) >= 2:
        # Derive the spacing from the first gap (most events are evenly spaced).
        slot_seconds = max(1, int(slots[1]) - int(slots[0]))

    return {"title": title, "slots": slots,
            "slot_seconds": slot_seconds, "doors_ts": doors}


# ---------------------------------------------------------------------------
# Self-test  (run: python signup.py)
# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(label, ok, got=None, want=None):
        nonlocal failures
        print(f"  {'PASS' if ok else 'FAIL'} — {label}")
        if not ok:
            if got is not None:
                print("    GOT : " + repr(got))
            if want is not None:
                print("    WANT: " + repr(want))
            failures += 1

    print("=== 1. format_duration ===")
    check("3600 -> 1 hour", format_duration(3600) == "1 hour")
    check("7200 -> 2 hours", format_duration(7200) == "2 hours")
    check("900 -> 15 minutes", format_duration(900) == "15 minutes")
    check("5400 -> 90 minutes", format_duration(5400) == "90 minutes")
    check("300 -> 5 minutes", format_duration(300) == "5 minutes")
    check("60 -> 1 minute", format_duration(60) == "1 minute")

    print("=== 2. build_slots (golden start) ===")
    # start 1788670800 (from Nadia's screenshot), 8 slots, 1 hour apart.
    ts = build_slots("1788670800", "8", "1 hour")
    check("8 slots", len(ts) == 8, len(ts))
    check("first == start", ts[0] == 1788670800, ts[0])
    check("second == +3600", ts[1] == 1788674400, ts[1])
    check("eighth == start+7h", ts[7] == 1788670800 + 7 * 3600, ts[7])

    # time-of-day start with an explicit day (DST-aware via /timeslots).
    ts2 = build_slots("10pm ET", "3", "90 min", day="2026-09-11")
    check("3 slots (tod)", len(ts2) == 3, len(ts2))
    check("spacing 90 min", (ts2[1] - ts2[0]) == 5400, ts2[1] - ts2[0])

    print("=== 3. render_template (golden layout) ===")
    tpl = render_template(
        "New Event",
        [1788670800, 1788674400, 1788678000],
        3600,
        1788670800 - 15 * 60,
    )
    print("---- template ----\n" + tpl + "\n------------------")
    check("has # title", tpl.startswith("# New Event"))
    check("has > #1 line", "> #1, <t:1788670800:t>" in tpl)
    check("has > #3 line", "> #3, <t:1788678000:t>" in tpl)
    check("doors line exact",
          "- Doors Open: **__<t:1788669900:F>__** (<t:1788669900:R>)" in tpl)
    check("slot-length line", "- Each slot is 1 hour long" in tpl)
    check("pick instruction", "- Click on the slots you want to sign-up for" in tpl)
    check("under 1900 chars", len(tpl) < MAX_TEXT_LEN, len(tpl))

    print("=== 4. render_event with assignments ===")
    ev = render_event(
        "New Event",
        [1788670800, 1788674400, 1788678000],
        3600,
        1788669900,
        assignments={0: [123, 456]},   # two people on slot #1
    )
    check("slot #1 has both names",
          "> #1, <t:1788670800:t>  <@123>  <@456>" in ev)
    check("slot #2 empty", "> #2, <t:1788674400:t>" in ev
          and "<@" not in ev.split("> #2,")[1].split("\n")[0])
    check("names space-space separated", "  <@123>  <@456>" in ev)

    print("=== 5. parse_event_text (round-trips the template) ===")
    back = parse_event_text(tpl)
    check("title back", back["title"] == "New Event", back["title"])
    check("3 slots back", back["slots"] == [1788670800, 1788674400, 1788678000],
          back["slots"])
    check("duration back", back["slot_seconds"] == 3600, back["slot_seconds"])
    check("doors back", back["doors_ts"] == 1788669900, back["doors_ts"])

    # parse the live event (with names) — names are ignored, slots preserved.
    back2 = parse_event_text(ev)
    check("slots survive names", back2["slots"] == [1788670800, 1788674400, 1788678000],
          back2["slots"])

    # a host re-types it loosely (no quotes, no #, different title).
    loose = ("my friday show\n"
             "10:00 <t:1788670800:t>\n"
             "11:00 <t:1788674400:t>\n"
             "- Doors Open: **__<t:1788669900:F>__** (<t:1788669900:R>)\n"
             "- Each slot is 60 minutes long")
    back3 = parse_event_text(loose)
    check("loose title", back3["title"] == "my friday show", back3["title"])
    check("loose slots", back3["slots"] == [1788670800, 1788674400], back3["slots"])
    check("loose duration", back3["slot_seconds"] == 3600, back3["slot_seconds"])

    print("=== 6. label / mention helpers ===")
    check("slot_label 0 -> #1", slot_label(0) == "#1")
    check("slot_label 7 -> #8", slot_label(7) == "#8")
    check("mention", mention(42) == "<@42>")

    if failures:
        print(f"\n{failures} CHECK(S) FAILED")
        return 1
    print("\nOK: signup self-test passed — template/event render + parse round-trip")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
