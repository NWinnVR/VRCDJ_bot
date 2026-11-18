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
      - **__2__** People can sign up for a slot. **__3__** slots per person.
      - Click on the slots you want to sign-up for

      > #1, <t:1788670800:t>
      > #2, <t:1788674400:t>
      > #3, <t:1788678000:t>
      ...
      > #6, <t:1788688800:t>

  (Doors = slot #1 minus the lead, default 15 min.  "60 minutes long" is
  computed from the slot time and switches to hours past 60.  The limits line
  is baked from the host's slash inputs — a blank/`UNLIMITED` field renders
  **__Unlimited__** / **__No__** wording; see `render_limits_line`.)
"""
from __future__ import annotations

import re
from typing import Optional, List

import timeslots  # reuse parse_start_time / parse_day / parse_duration / slots

__all__ = [
    "MAX_SLOTS", "MAX_SLOTS_EDIT", "MAX_TITLE_LEN", "MAX_TEXT_LEN",
    "DEFAULT_DOORS_LEAD", "DEFAULT_SLOT_SECONDS", "UNLIMITED",
    "DEFAULT_PER_SLOT", "DEFAULT_PER_PERSON",
    "build_slots", "format_duration", "render_limits_line",
    "render_template", "render_event", "parse_event_text", "slot_label", "mention",
]

# --- caps (Discord message = 2000 chars; stay safely under) -----------------
MAX_SLOTS = 50          # a gogo-stage / DJ night realistically never needs more
MAX_SLOTS_EDIT = 80     # a host editing an existing event may add a few
MAX_TITLE_LEN = 100
MAX_TEXT_LEN = 1900

# --- defaults ---------------------------------------------------------------
DEFAULT_DOORS_LEAD = 15            # minutes before slot 1 ("" / 0 = omit line)
DEFAULT_SLOT_SECONDS = 3600        # one-hour sets unless told otherwise
UNLIMITED = -1                     # sentinel: no limit (used for per-slot / per-person)
DEFAULT_PER_SLOT = UNLIMITED       # unlimited people per slot unless told otherwise
DEFAULT_PER_PERSON = UNLIMITED     # unlimited slots per person unless told otherwise


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


def render_limits_line(per_slot: int, per_person: int) -> str:
    """The signup-limits bullet, baked from the host's slash inputs.

    `UNLIMITED` (or any value <= 0) means "no limit".  Each half renders its
    own limited / unlimited wording.  A count of 1 is singular ("1 Person",
    "1 Slot"); anything higher stays plural.  (Per the spec: a LIMITED
    per-person cap reads "... N slots per person." while an UNLIMITED one reads
    "... No slots per person limit" — the two halves differ on purpose.)

      per_slot=2  per_person=3  ->  - **__2__** People can sign up for a slot. **__3__** slots per person.
      per_slot=1  per_person=1  ->  - **__1__** Person can sign up for a slot. **__1__** Slot per person.
      per_slot=2  unlimited     ->  - **__2__** People can sign up for a slot. **__No__** slots per person limit
      unlimited  per_person=3  ->  - **__Unlimited__** People can sign up for a slot. **__3__** slots per person.
      unlimited  unlimited     ->  - **__Unlimited__** People can sign up for a slot. **__No__** slots per person limit
    """
    ps = int(per_slot)
    if ps > 0:
        slot_word = f"**__{ps}__**"
        people_word = "Person" if ps == 1 else "People"
    else:  # UNLIMITED
        slot_word = "**__Unlimited__**"
        people_word = "People"

    pp = int(per_person)
    if pp > 1:
        person_clause = f"**__{pp}__** slots per person."
    elif pp == 1:
        person_clause = "**__1__** Slot per person."
    else:  # UNLIMITED
        person_clause = "**__No__** slots per person limit"

    return f"- {slot_word} {people_word} can sign up for a slot. {person_clause}"


def _notes_block(notes: str) -> List[str]:
    """The host's free-form lines as a list (outer blank lines trimmed).
    Empty/whitespace-only notes -> []."""
    if not notes or not notes.strip():
        return []
    out = [l.rstrip() for l in notes.strip("\n").splitlines()]
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def render_template(title: str,
                    slot_timestamps: List[int],
                    slot_seconds: int,
                    doors_ts: Optional[int],
                    per_slot: int = DEFAULT_PER_SLOT,
                    per_person: int = DEFAULT_PER_PERSON,
                    notes: str = "",
                    pre_slot_notes: str = "",
                    slot_extras: Optional[dict] = None,
                    slot_pre_extras: Optional[dict] = None) -> str:
    """The prefill for the "Event Creation" modal (NO names yet).

    Layout = Nadia's golden template: `# title`, [host notes], the info
    bullets (doors / slot length / signup limits / pick instruction), then any
    mid-zone notes, a blank line, and the `> #N, <t:…:t>` slot block.  Slot
    lines carry host text on BOTH sides of the token: pre-text inline before it
    (`> #1, teams dfg <t:…:t>` — e.g. an inline DJ name) and trailing text
    after it.  With no notes/extras the output is byte-identical to before.
    """
    lines = [f"# {title.strip() or 'New Event'}"]
    nb = _notes_block(notes)
    if nb:
        lines.extend(nb)
        lines.append("")
    if doors_ts:
        lines.append(_doors_line(doors_ts))
    lines.append(f"- Each slot is {format_duration(slot_seconds)} long")
    lines.append(render_limits_line(per_slot, per_person))
    lines.append("- Click on the slots you want to sign-up for")
    pn = _notes_block(pre_slot_notes)
    if pn:
        lines.extend(pn)
    lines.append("")
    extras = _norm_extras(slot_extras)
    pres = _norm_extras(slot_pre_extras)
    slot_lines = []
    for i, ts in enumerate(slot_timestamps):
        pre = pres.get(i, "")
        if pre:
            line = f"> {slot_label(i)}, {pre} <t:{ts}:t>"
        else:
            line = f"> {slot_label(i)}, <t:{ts}:t>"
        extra = extras.get(i, "")
        if extra:
            line += "  " + extra
        slot_lines.append(line)
    lines.extend(slot_lines)
    return "\n".join(lines)


def render_event(title: str,
                 slot_timestamps: List[int],
                 slot_seconds: int,
                 doors_ts: Optional[int],
                 assignments: Optional[dict] = None,
                 per_slot: int = DEFAULT_PER_SLOT,
                 per_person: int = DEFAULT_PER_PERSON,
                 notes: str = "",
                 pre_slot_notes: str = "",
                 slot_extras: Optional[dict] = None,
                 slot_pre_extras: Optional[dict] = None) -> str:
    """The LIVE event post (slots + info + a name under each filled slot).

    `assignments` maps slot index (0-based) -> list of user ids.  A filled
    slot gets its names appended to the line; host slot text rides on both
    sides of the token — pre-text inline BEFORE it (e.g. an inline DJ name:
    `> #1, teams dfg <t:…:t>`), trailing text after — with the signed-up names
    following everything ("space-space" separated = hard line breaks).  Empty
    slots keep the bare `> #N, <t:…:t>` (+ host text, if any) so everyone can
    see what's open.  Layout matches the modal: `# title`, [top notes], info
    bullets (incl. the limits line), [mid notes between the click bullet and
    the slots], blank line, slots.  No host text -> identical to before.
    """
    assignments = assignments or {}
    lines = [f"# {title.strip() or 'New Event'}"]
    nb = _notes_block(notes)
    if nb:
        lines.extend(nb)
        lines.append("")
    if doors_ts:
        lines.append(_doors_line(doors_ts))
    lines.append(f"- Each slot is {format_duration(slot_seconds)} long")
    lines.append(render_limits_line(per_slot, per_person))
    lines.append("- Click on the slots you want to sign-up for")
    pn = _notes_block(pre_slot_notes)
    if pn:
        lines.extend(pn)
    lines.append("")
    extras = _norm_extras(slot_extras)
    pres = _norm_extras(slot_pre_extras)
    for i, ts in enumerate(slot_timestamps):
        pre = pres.get(i, "")
        if pre:
            line = f"> {slot_label(i)}, {pre} <t:{ts}:t>"
        else:
            line = f"> {slot_label(i)}, <t:{ts}:t>"
        extra = extras.get(i, "")
        if extra:
            line += "  " + extra
        ids = assignments.get(i) or assignments.get(str(i)) or []
        if ids:
            line += "  " + "  ".join(mention(u) for u in ids)
        lines.append(line)
    return "\n".join(lines)


def _norm_extras(slot_extras: Optional[dict]) -> dict:
    """{str|int index: text} -> {int index: cleaned text}, empties dropped."""
    out = {}
    for k, v in (slot_extras or {}).items():
        if not v:
            continue
        txt = _clean_slot_extra(str(v))
        if txt:
            try:
                out[int(k)] = txt
            except (TypeError, ValueError):
                pass
    return out


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
# One `<t:N:t>` token (slot lines only — doors use :F / :R, never :t).
_SLOT_TOKEN_RE = re.compile(r"<t:(\d+):t>", re.I)
# The doors-open line:  - Doors Open: **__<t:…:F>__** (<t:…:R>)
_DOORS_RE = re.compile(r"doors\s*open[^>]*<t:(\d+):[fF]", re.I)
# The slot length:  Each slot is 90 minutes long
_DUR_RE = re.compile(r"each slot is\s+(.+?)\s+long", re.I)
# The standard pick-instruction bullet ("- Click on the slots ...").
_CLICK_RE = re.compile(r"click\s+(?:on\s+|at\s+)?(?:the\s+)?slots?", re.I)
# A user mention like <@1234567890> / <@!123...>.  Mentions that land inside a
# slot line's EXTRA text are live sign-up data (rendered from the store), not
# host-written copy — stripping them at parse time keeps an edit round-trip
# from baking stale mentions into the stored extras.
_MENTION_RE = re.compile(r"<@!?\d+>")


def _clean_slot_extra(raw: str) -> str:
    """Normalize a slot line's trailing text: drop user mentions (live data,
    not host copy), collapse whitespace runs to single spaces."""
    return " ".join(_MENTION_RE.sub("", raw).split())


# A slot line's PRE-token text may carry the quote marker + label as typed
# ("> #1. teams dfg <t:…:t>") — strip both before cleaning, so what remains is
# exactly the host's inline text (e.g. a DJ name).
_SLOT_LABEL_PREFIX_RE = re.compile(
    r"^\s*[>›]+\s*(?:#\s*\d+\s*[,.)]?\s*)?", re.I)


def _slot_pre_text(raw: str) -> str:
    """A slot line's text BEFORE its <t:…:t> token (e.g. an inline DJ name),
    cleaned like the post-token extra.  No label/quote on the line -> ''."""
    return _clean_slot_extra(_SLOT_LABEL_PREFIX_RE.sub("", raw))


def _is_structured_line(s: str) -> bool:
    """True if a stripped line is part of the machine-generated block (title
    heading, doors / duration / limits / click bullets) — i.e. NOT host
    free-form notes.  A `>` slot quote with NO <t:…:t> token (a host's own
    bullet typed as a quote) is their TEXT -> False, so it lands in notes and
    can never be silently dropped."""
    if s.startswith("#"):
        return True
    if "<t:" in s:                      # slots (:t) and doors (:F / :R) alike
        return True
    if _DUR_RE.search(s):
        return True
    if re.search(r"can sign up for a slot", s, re.I):
        return True
    if _CLICK_RE.search(s):
        return True
    return False


def parse_event_text(text: str) -> dict:
    """Parse a host's modal text back into the structured event.

    Returns:
      {
        "title":           str,                 # from the first `# heading`
        "slots":           [int, ...],          # slot timestamps, in listed order
        "slot_seconds":    int,                 # from "each slot is N long"
        "doors_ts":        int | None,          # from the doors-open line
        "notes":           str,                 # host lines BEFORE the click bullet
        "pre_slot_notes":  str,                 # host lines BETWEEN it and the slots
        "slot_extras":     {i0: text},          # per-slot text AFTER its token (e.g. DJ)
        "slot_pre_extras": {i0: text},          # per-slot text BEFORE it (inline DJ name)
      }

    `notes` / `pre_slot_notes` — every line that is NOT part of the structured
    block (title, slot quotes, doors / duration / limits / click bullets), kept
    in order; blanks collapse away.  The SPLIT follows the standard "Click on
    the slots ..." bullet: lines above it are TOP notes (rendered under the
    title); lines between it and the first slot line sit MID (rendered right
    above the slot block) — a host types a note wherever they want and it
    lands exactly there.  `slot_extras` / `slot_pre_extras` — the text on each
    slot line after, respectively before, its `<t:…:t>` token (e.g. an inline
    DJ name `> #1. teams dfg <t:…:t>` -> `slot_pre_extras {0: "teams dfg"}`);
    the typed quote / "#N." label is stripped from the pre-text, user mentions
    are stripped from both (they're live sign-up data, re-added on render), and
    empties dropped ({ } = no extras).

    Tolerant of how the host types it (quote, "#N," prefix, ordering).  If a
    slot block is present it is authoritative for the slot list; the duration
    falls back to the spacing between consecutive slots when not stated.
    """
    text = text or ""
    lines = text.splitlines()

    # --- title --------------------------------------------------------
    tm = _TITLE_RE.search(text)
    title_idx: Optional[int] = None
    if tm:
        title = tm.group(1).strip()
        for i, ln in enumerate(lines):
            if re.match(r"^\s*#{1,3}\s+\S", ln):
                title_idx = i
                break
    else:
        # No `# heading` — tolerate a bare title line (the first line that is
        # not blank, not a `>` slot quote, not a `-` info bullet, and not a
        # `<t:…:t>` slot timestamp).  In the live flow the modal's name field
        # is authoritative; this is just a tolerant fallback.  The matched line
        # is remembered so it doesn't ALSO land in `notes`.
        title = ""
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith(">") or s.startswith("-"):
                continue
            if re.search(r"<t:\d+:t>", s, re.I):
                continue
            title = s
            title_idx = i
            break
        title = title or "New Event"

    # --- slots + per-slot extras (line order == document order) -------
    slots: List[int] = []
    slot_extras: dict = {}      # {slot index: text AFTER the <t:…:t> token}
    slot_pre_extras: dict = {}  # {slot index: text BEFORE it, e.g. a DJ name}
    for ln in lines:
        pos = 0
        while True:
            m = _SLOT_TOKEN_RE.search(ln, pos)
            if not m:
                break
            ts = int(m.group(1))
            if not slots or slots[-1] != ts:
                idx = len(slots)
                slots.append(ts)
            else:  # same timestamp twice on the list -> maps to that slot
                idx = len(slots) - 1
            pre = _slot_pre_text(ln[:m.start()])
            if pre:
                slot_pre_extras.setdefault(idx, pre)
            extra = _clean_slot_extra(ln[m.end():])
            if extra:
                slot_extras.setdefault(idx, extra)
            pos = m.end()

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

    # --- host free-form lines, SPLIT BY POSITION -----------------------
    # Everything before the standard "Click on the slots ..." bullet is the
    # TOP zone (`notes` — clarifying bullets at the top of the post); every
    # other non-structured line sits in the MID zone between that bullet and
    # the slot block (`pre_slot_notes`, rendered right above the slots).  A
    # host types a new note wherever they want and it lands exactly there.
    click_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        if _CLICK_RE.search(ln):
            click_idx = i
            break
    first_slot_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        if re.search(r"<t:\d+:t>", ln, re.I):
            first_slot_idx = i
            break

    notes_lines, pre_notes_lines = [], []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or i == title_idx:
            continue
        if _is_structured_line(s) or re.search(r"<t:\d+:t>", s, re.I):
            continue
        if click_idx is None or i < click_idx:
            notes_lines.append(s)
        elif first_slot_idx is None or (first_slot_idx is not None and i < first_slot_idx):
            pre_notes_lines.append(s)

    return {"title": title, "slots": slots,
            "slot_seconds": slot_seconds, "doors_ts": doors,
            "notes": "\n".join(notes_lines),
            "pre_slot_notes": "\n".join(pre_notes_lines),
            "slot_extras": slot_extras,
            "slot_pre_extras": slot_pre_extras}


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

    print("=== 3b. render_limits_line (all four combos) ===")
    check("both set",
          render_limits_line(2, 3) ==
          "- **__2__** People can sign up for a slot. **__3__** slots per person.",
          render_limits_line(2, 3))
    check("both 1 -> singular (Person / Slot)",
          render_limits_line(1, 1) ==
          "- **__1__** Person can sign up for a slot. **__1__** Slot per person.",
          render_limits_line(1, 1))
    check("slot 1 / person unlimited -> singular Person",
          render_limits_line(1, UNLIMITED) ==
          "- **__1__** Person can sign up for a slot. **__No__** slots per person limit",
          render_limits_line(1, UNLIMITED))
    check("slot set / person unlimited",
          render_limits_line(2, UNLIMITED) ==
          "- **__2__** People can sign up for a slot. **__No__** slots per person limit",
          render_limits_line(2, UNLIMITED))
    check("slot unlimited / person set",
          render_limits_line(UNLIMITED, 3) ==
          "- **__Unlimited__** People can sign up for a slot. **__3__** slots per person.",
          render_limits_line(UNLIMITED, 3))
    check("both unlimited (defaults)",
          render_limits_line(UNLIMITED, UNLIMITED) ==
          "- **__Unlimited__** People can sign up for a slot. **__No__** slots per person limit",
          render_limits_line(UNLIMITED, UNLIMITED))
    check("template carries the limits line (2/3)",
          render_limits_line(2, 3) in
          render_template("New Event", [1788670800], 3600, None, 2, 3))
    check("event carries the limits line (unlimited)",
          render_limits_line(UNLIMITED, UNLIMITED) in
          render_event("New Event", [1788670800], 3600, None, {}))

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

    print("=== 5b. notes + slot extras (host free-form text) ===")
    # No notes/extras -> parse yields empty values and the render is unchanged.
    check("plain template: no notes", back["notes"] == "", repr(back["notes"]))
    check("plain template: no extras", back["slot_extras"] == {},
          back["slot_extras"])

    # A host adds clarifying bullets + a DJ name on slot #2.
    with_notes = (
        "# New Event\n"
        "- **Special guest night** — bring your own drinks!\n"
        "- Doors close at 3am sharp.\n"
        "\n"
        + tpl)
    bn = parse_event_text(with_notes)
    check("notes parsed (2 lines)",
          bn["notes"] == ("- **Special guest night** — bring your own drinks!"
                          "\n- Doors close at 3am sharp."), repr(bn["notes"]))
    check("slots survive notes",
          bn["slots"] == [1788670800, 1788674400, 1788678000], bn["slots"])
    # Render with the parsed pieces: notes appear before doors; extras on slot.
    evn = render_event("New Event", [1788670800, 1788674400, 1788678000],
                       3600, 1788669900, {}, notes=bn["notes"])
    check("render has notes block",
          evn.startswith("# New Event\n- **Special guest night** — "
                         "bring your own drinks!\n- Doors close at 3am sharp.\n"),
          repr(evn[:120]))
    # DJ name extra on slot #2 (index 1).
    evx = render_event("New Event", [1788670800, 1788674400, 1788678000],
                       3600, None, {}, slot_extras={1: "Kiki"})
    check("slot #2 carries the extra", "> #2, <t:1788674400:t>  Kiki" in evx)
    check("other slots stay bare", "> #1, <t:1788670800:t>\n" in evx
          and "> #3, <t:1788678000:t>" in evx.split("\n")[-1])
    # Extra + a signed-up name on the same slot stack (extra first).
    evx2 = render_event("New Event", [1788670800, 1788674400],
                        3600, None, {1: [555]}, slot_extras={1: "Kiki"})
    check("extra then name on filled slot",
          "> #2, <t:1788674400:t>  Kiki  <@555>" in evx2)
    # Live event (back2 = parse of `ev`, names on slot #1): the signed-up
    # mentions are LIVE data — they must NOT land in slot_extras.
    check("mentions stripped from extras (live event)",
          back2["slot_extras"] == {}, back2["slot_extras"])

    print("=== 5c. pre-token extras + positional notes (real-world modal) ===")
    # Nadia's VERBATIM test modal: inline DJ names BEFORE the token, and a
    # bullet typed BETWEEN the click line and the slot block — it must stay
    # in place (mid zone), NOT jump to the top.
    real_modal = (
        "- Doors Open: **__<t:1792802100:F>__** (<t:1792802100:R>)\n"
        "- Each slot is 24 minutes long\n"
        "- **__2__** People can sign up for a slot. **__2__** slots per person.\n"
        "- Click on the slots you want to sign-up for\n"
        "- Yui, this is a test input where I would want to append one.\n"
        "\n"
        "> #1. teams dfg <t:1792803600:t>\n"
        "> #2. fdsa <t:1792805040:t>\n"
        "> #3. These are the areas where I need to put dj's fades "
        "<t:1792806480:t>\n"
        "> #4. <t:1792807920:t>\n"
        "> #5. <t:1792809360:t>\n"
        "> #6. <t:1792810800:t>")
    pr = parse_event_text(real_modal)
    check("real: 6 slots", len(pr["slots"]) == 6, str(pr["slots"]))
    check("real: pre-extras {teams dfg, fdsa, sentence}",
          pr["slot_pre_extras"] == {
              0: "teams dfg", 1: "fdsa",
              2: "These are the areas where I need to put dj's fades"},
          str(pr["slot_pre_extras"]))
    check("real: no post-token extras (all text was pre)",
          pr["slot_extras"] == {}, str(pr["slot_extras"]))
    check("real: TOP notes empty (nothing above the click bullet)",
          pr["notes"] == "", repr(pr["notes"]))
    check("real: mid note stays in place",
          pr["pre_slot_notes"] == "- Yui, this is a test input where "
                                  "I would want to append one.",
          repr(pr["pre_slot_notes"]))

    # Render the live post back from those parsed pieces — it must show the
    # inline names BEFORE their tokens and the mid bullet right above slots.
    real_ev = render_event(
        "Yui this is the test modal", pr["slots"], pr["slot_seconds"],
        pr["doors_ts"], {}, per_slot=2, per_person=2,
        notes=pr["notes"], pre_slot_notes=pr["pre_slot_notes"],
        slot_extras={str(k): v for k, v in pr["slot_extras"].items()},
        slot_pre_extras={str(k): v for k, v in pr["slot_pre_extras"].items()})
    check("real render: #1 inline name before token",
          "> #1, teams dfg <t:1792803600:t>" in real_ev, real_ev[:400])
    check("real render: #2 inline name before token",
          "> #2, fdsa <t:1792805040:t>" in real_ev, real_ev[:500])
    check("real render: mid bullet sits between click line and slots",
          "- Yui, this is a test input where I would want to append one.\n"
          "\n> #1," in real_ev, real_ev[-400:])
    check("real render: NO stray top-zone bullet after the title",
          not real_ev.startswith("# \n-Yui") and
          "- Yui" not in real_ev.split("- Doors Open")[0],
          repr(real_ev[:80]))

    # Round-trip: parse the rendered post again — everything survives.
    pr2 = parse_event_text(real_ev)
    check("real round-trip: pre-extras preserved",
          pr2["slot_pre_extras"] == {0: "teams dfg", 1: "fdsa",
                                     2: "These are the areas where I need to put dj's fades"},
          str(pr2["slot_pre_extras"]))
    check("real round-trip: mid note preserved + in place",
          pr2["pre_slot_notes"] == "- Yui, this is a test input where "
                                   "I would want to append one.",
          repr(pr2["pre_slot_notes"]))
    check("real round-trip: top notes still empty", pr2["notes"] == "",
          repr(pr2["notes"]))

    # A signed-up name on slot #1 must stack AFTER the inline DJ name.
    real_ev2 = render_event(
        "Yui this is the test modal", pr["slots"], pr["slot_seconds"],
        pr["doors_ts"], {0: [777]}, per_slot=2, per_person=2,
        notes=pr["notes"], pre_slot_notes=pr["pre_slot_notes"],
        slot_extras={str(k): v for k, v in pr["slot_extras"].items()},
        slot_pre_extras={str(k): v for k, v in pr["slot_pre_extras"].items()})
    check("real render: DJ name then signed-up mention on #1",
          "> #1, teams dfg <t:1792803600:t>  <@777>" in real_ev2,
          real_ev2[:220])

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
