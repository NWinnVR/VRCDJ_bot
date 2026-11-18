"""timeslots.py — the WyBot `/timeslots` command engine (ham-time slot generator).

WHAT IT DOES
  Staff lay out a DJ lineup as a block of Discord *ham-time* slots, one per
  line:

      <t:1789178400:t>
      <t:1789182000:t>
      <t:1789185600:t>
      ...

  Previously that meant hand-computing each Unix timestamp (the "EST vs EDT"
  trap — the 2026-09-10 example took Nadia FOUR attempts to land on the right
  number).  /timeslots does that for us: given a START TIME, a DAY, and a
  slot COUNT, it returns the whole block of equally-spaced `<t:…:t>` lines,
  ready to paste into an event post.  It can prepend a "doors open" line and
  label the slots with letter emotes (A, B, C …) the same way /djlineup does.

Fuzzy, forgiving inputs (the whole point — no one wants to compute unix):
  * start time : "10pm ET" · "10 PM EST" · "10:30pm CT" · "22:00" ·
                 "noon" · "1789178400" (unix) · "<t:1789178400:t>"
                 — OR a whole natural sentence, day included, in any order:
                 "10 PM est, friday" · "Saturday the 12th 11pm EST" ·
                 "the 12th 9pm" · "tuesday 9pm" · "october 5th 8pm"
  * day        : "friday" · "mon" · "June 5th" · "june 5, 2026" ·
                 "2026-09-10" · "the 5th"   (a bare weekday = the closest
                 UPCOMING occurrence of that day, counting from today)
                 — optional when start_time already names a day
  * slot count : "6" · "twelve"
  * slot dur.  : "1 hour" (default) · "2 hour" · "1.5 hours" · "90 min"
  * doors lead : "15" (default, minutes before slot 1) · "30 min" · "1 hour"

TIMEZONES — DST handled by the standard library, NOT by hardcoding offsets.
  An abbreviation maps to an IANA zone and the OS tz database decides the
  real offset for the chosen date:

      ET  / EST / EDT   -> America/New_York
      CT  / CDT / CST   -> America/Chicago
      MT / MDT / MST    -> America/Denver
      PT / PDT / PST    -> America/Los_Angeles
      UTC / GMT         -> UTC

  So "10pm ET on Friday Sep 11 2026" correctly resolves to **EDT (UTC-4) =
  1789178400** — the number Nadia landed on after four attempts.  "EST" is a
  *label*, not a fixed offset; in September Eastern is on DST, and the tz
  database knows that.  This is the class of bug this command exists to
  remove (2026-09-10).

OUTPUT (your golden example, "10pm ET" + 6 slots + "friday"):

      __<t:1789177500:F>__ (<t:1789177500:R>)      <- doors open, 15 min early
      <t:1789178400:t>
      <t:1789182000:t>
      <t:1789185600:t>
      <t:1789189200:t>
      <t:1789192800:t>
      <t:1789196400:t>

  With letter emotes (the /djlineup option) the slot lines become:

      :regional_indicator_a: <t:1789178400:t>
      :regional_indicator_b: <t:1789182000:t>
      ...

PURE + TESTABLE
  No discord, no bot_state, no file I/O.  bot.py's `cmd_timeslots` runs a
  small `wait_for` wizard and then calls `build()`.  Headless testing is
  trivial: import this module, call the functions, assert on the strings.
  Run `python timeslots.py` for the self-test (includes the golden example).
"""
from __future__ import annotations

import re
import time as _time_mod
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover — zoneinfo is stdlib since 3.9
    ZoneInfo = None  # type: ignore

import djlineup  # reuse the A–Z letter emote engine (/djlineup)

__all__ = [
    "build", "parse_start_time", "parse_day", "parse_duration",
    "parse_when", "start_has_date",
    "resolve_candidates", "WhenCandidate", "slot_block",
    "slots", "door_line", "letter_enabled", "start_is_instant",
    "DEFAULT_SLOT_SECONDS",
    "DEFAULT_DOORS_SECONDS",
]

# --- Defaults ---------------------------------------------------------------
DEFAULT_SLOT_SECONDS = 3600     # one-hour sets unless told otherwise
DEFAULT_DOORS_SECONDS = 15 * 60  # doors open 15 min before slot 1

# --- Timezone abbreviations -> IANA zones (DST resolved by the tz database) --
_TZ_MAP = {
    "et": "America/New_York",
    "est": "America/New_York", "edt": "America/New_York",
    "eastern": "America/New_York",
    "ct": "America/Chicago",
    "cdt": "America/Chicago", "cst": "America/Chicago",
    "central": "America/Chicago",
    "mt": "America/Denver",
    "mdt": "America/Denver", "mst": "America/Denver",
    "pt": "America/Los_Angeles",
    "pdt": "America/Los_Angeles", "pst": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "utc": "UTC", "gmt": "UTC", "z": "UTC",
    # UK time for Hyndal: BST = summer (UTC+1), GMT = winter (UTC+0).  Both map
    # to Europe/London so the tz database picks the right offset by date — the
    # same DST-aware approach as ET/CT/MT/PT, so "10pm BST" in September is
    # correctly +1, not a hardcoded offset.
    "bst": "Europe/London", "london": "Europe/London", "uk": "Europe/London",
}
# Word-boundary match so "et" doesn't grab "et" out of another word, and so
# the longer "est"/"edt" are tried before the bare "et" (alternation order).
_TZ_RE = re.compile(
    r"\b(?:est|edt|et|cst|cdt|ct|mdt|mst|mt|pst|pdt|pt|utc|gmt|bst|z)\b", re.I)

# --- Time-of-day patterns ----------------------------------------------------
_NOON = re.compile(r"\bnoon\b", re.I)
_MIDNIGHT = re.compile(r"\bmidnight\b", re.I)
_HOUR_RE = re.compile(r"\b(\d{1,2})(?::(\d{1,2}))?\s*(am|pm)?", re.I)
# "at"/"@" pins the following number as THE clock time ("friday at 22").
_AT_PREFIX = re.compile(
    r"\b(?:at|@)\s+(\d{1,2})(?::(\d{1,2}))?(am|pm)?\b", re.I)
# Morning/evening hint words settle a bare hour ("saturday evening 10" -> PM).
_HINT_AM = re.compile(r"\bmorning\b", re.I)
_HINT_PM = re.compile(r"\b(evening|night|tonight)\b", re.I)
# Relative day words: "today"/"tonight" (this date), "tomorrow" & friends.
_RE_REL = re.compile(r"\b(today|tonight|tomorrow|tmrw?|to-morrow)\b", re.I)


def _parse_hour(text: str) -> Optional[tuple[int, int]]:
    """Extract (hour, minute) from a time string.  Returns None if absent."""
    if _MIDNIGHT.search(text):
        return (0, 0)
    if _NOON.search(text):
        return (12, 0)
    m = _HOUR_RE.search(text)
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    mer = (m.group(3) or "").lower()
    if h > 24:
        return None
    if mer:  # 12-hour
        if h not in range(1, 13):
            return None
        if mer == "pm" and h != 12:
            h += 12
        if mer == "am" and h == 12:
            h = 0
    else:    # no am/pm -> assume 24-hour (h may be 0..24)
        if h == 24:
            h = 0
    if minute > 59:
        return None
    return (h, minute)


def parse_start_time(text: str, ref: Optional[date] = None) -> int:


    """Parse a start-time spec into a UNIX timestamp.

    Accepts:
      * a Unix timestamp:  "1789178400"  or  "<t:1789178400:t>"
      * a time-of-day + zone:  "10pm ET" · "10:30 PM EST" · "22:00" ·
        "noon ET" · "10 pm CT"
      * a natural-language time+day:  "10 PM est, friday" ·
        "Saturday the 12th 11pm EST" · "the 12th 9pm" · "tuesday 9pm"
        (the day is taken FROM the string itself, with the defaults in
        `parse_when` — current month / nearest upcoming weekday)

    A time-of-day still needs a date: if the string carries its own day it
    is used; otherwise `ref` (the day being planned) supplies it.  `ref`
    defaults to TODAY, which is almost never what a staff member wants —
    callers should pass the already-parsed day when they have one.

    Raises ValueError with a friendly message when it can't parse.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("No start time given.")

    # 1) Already a Unix timestamp (bare, or wrapped in a ham-time token).
    m = re.search(r"<t:(\d{8,12}):[tTfF]\s*>", raw)
    if m:
        return int(m.group(1))
    if re.fullmatch(r"\d{8,12}", raw):
        return int(raw)

    # 2) Natural language: the string may carry its own day+time,
    #    ("Saturday the 12th 11pm EST") or just a time-of-day ("10pm ET"
    #    -> resolved against `ref`).  `parse_when` handles both orders.
    d, (h, minute) = parse_when(raw, ref=ref)

    zone = "America/New_York"
    tz_match = _TZ_RE.search(raw)
    if tz_match:
        zone = _TZ_MAP.get(tz_match.group(0).lower(), zone)
    if ZoneInfo is None:
        raise ValueError("Timezone data is unavailable on this system.")

    try:
        dt = datetime(d.year, d.month, d.day, h, minute, tzinfo=ZoneInfo(zone))
    except Exception as exc:
        raise ValueError(f"That time isn't valid: {exc}")
    return int(dt.timestamp())


def start_is_instant(text: str) -> bool:
    """True if `text` is already a full date-encoded instant (a bare Unix
    timestamp or a `<t:…:t>` ham-time token).  Such an input carries its own
    date, so the separate "day" field is not required — the command can use
    the date baked into the timestamp directly.  A time-of-day like "10pm ET"
    is NOT an instant: it still needs a day to become a real moment."""
    raw = (text or "").strip()
    if re.search(r"<t:\d{8,12}:t\s*>", raw, re.I):
        return True
    return bool(re.fullmatch(r"\d{8,12}", raw))


def _closest_upcoming_weekday(ref: date, weekday: int) -> date:
    """The nearest date at/after `ref` that is the given weekday (0=Mon..6=Sun)."""
    delta = (weekday - ref.weekday()) % 7
    if delta == 0:
        return ref
    return date.fromordinal(ref.toordinal() + delta)


# weekday name -> 0..6  (Monday = 0, matching date.weekday())
_WEEKDAY = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_MONTH = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_MD_RE = re.compile(r"(?:^|\b)(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
                    r"(?:uary|ruary|ch|il|e|ly|ust|tember|ober|ember|ember)?\s+"
                    r"(\d{1,2})(?:st|nd|rd|th)?", re.I)
_DM_RE = re.compile(r"(?:^|\b)(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
                    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
                    r"(?:uary|ruary|ch|il|e|ly|ust|tember|ober|ember|ember)?", re.I)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_WEEKDAY_TOKEN = re.compile(r"\b(monday|mon|tuesday|tue|tues|wednesday|wed|"
                            r"thursday|thu|thur|thurs|friday|fri|saturday|sat|"
                            r"sunday|sun)\b", re.I)
# ---------------------------------------------------------------------------
# Natural-language start-time resolver  (added 2026-10-04)
#
# The start_time field can carry BOTH the time and the day in one free-form
# string — "10 PM est, friday", "Saturday the 12th 11pm EST", "tuesday 9pm",
# "the 12th 9pm" — just like a person would say it.  `parse_when` is the
# single entry point: it extracts the date and the time-of-day from ANY
# order and returns (date, (hour, minute)).  `parse_start_time` delegates
# to it, so both /timeslots and /signup get the upgrade for free.
#
# Default rules (Nadia's spec):
#   * no month given  -> assume the current month ("the 12th" = 12th of
#     this month; if that has already passed, the 12th of next month)
#   * bare weekday    -> the next closest occurrence ("tuesday" = next Tue)
#   * "next <weekday>"-> the one AFTER the next closest
#   * weekday + Nth   -> the nearest Nth that actually IS that weekday
#   * month + Nth     -> that month-day this year (next year if already past)
# ---------------------------------------------------------------------------

# Weekday alternatives — long names FIRST so "saturday" is never matched as
# "sat" + junk, and "mon" never eats "monday".
_WD = (r"monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|"
       r"friday|fri|saturday|sat|sunday|sun")
_MONTH_ALT = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
              r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
              r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_NTH = r"(?:st|nd|rd|th)?"
_NOT_DAY = r"(?!\s*\d{1,2}(?:st|nd|rd|th)\b)"

_RE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_MD = re.compile(
    r"\b(" + _MONTH_ALT + r")\s+(?:the\s+)?(\d{1,2})" + _NTH +
    r"\b(,\s*(\d{4}))?" + _NOT_DAY, re.I)
_RE_DM = re.compile(
    r"\b(?:the\s+)?(\d{1,2})" + _NTH + r"\s+(?:of\s+)?(" + _MONTH_ALT +
    r")\b(,\s*(\d{4}))?", re.I)
_RE_WD_NTH = re.compile(
    # (?!:) — "saturday 22:00" is a TIME, not "Saturday the 22nd": never let a
    # weekday+number match when a colon (clock time) follows the number.
    r"\b(" + _WD + r")\b\s+(?:the\s+)?(\d{1,2})" + _NTH +
    r"\b(?!:)" + _NOT_DAY, re.I)
_RE_WD = re.compile(r"\b(" + _WD + r")\b", re.I)
_RE_MONTH = re.compile(r"\b(" + _MONTH_ALT + r")\b", re.I)
_RE_BARE_NTH = re.compile(
    r"\b(?:the\s+\d{1,2}" + _NTH + r"|\d{1,2}(?:st|nd|rd|th)\b)" + _NOT_DAY,
    re.I)
_RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_RE_NEXT = re.compile(r"\bnext\b", re.I)


def _month_num(name: str) -> int:
    key = (name or "").lower()
    if key in ("sept", "septm"):
        key = "september"
    if key not in _MONTH:
        raise ValueError(f"I don't know the month {name!r}.  Try “october” or “oct”.")
    return _MONTH[key]


def _roll_forward(d: date, ref: date) -> date:
    """If `d` is already in the past, move it forward: same day next month,
    else the 28th next month, else same day next year.  (Events are future.)"""
    if d >= ref:
        return d
    try:
        nd = d.replace(month=d.month % 12 + 1)
    except ValueError:
        nd = d.replace(day=28, month=d.month % 12 + 1)
    if nd >= ref:
        return nd
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def _nearest_weekday_day(ref: date, weekday: int, dnum: int) -> date:
    """Nearest date >= ref that is BOTH the given weekday and that day-of-month.
    Searches up to 15 months ahead (a weekday+day combo always recurs within)."""
    for offset in range(15):
        m = ref.month - 1 + offset
        y = ref.year + m // 12
        mo = m % 12 + 1
        try:
            d = date(y, mo, dnum)
        except ValueError:
            continue
        if d >= ref and d.weekday() == weekday:
            return d
    raise ValueError("I couldn't land that weekday + day combination — try again.")


def _extract_time_info(text: str) -> Optional[dict]:
    """Robustly extract the time-of-day from a mixed "when" string.

    Returns {"h": 0..23, "minute": 0..59, "certain": bool}.  `certain` is
    False ONLY when the hour was given bare (1-12, no am/pm, no hint word) —
    i.e. both AM and PM are possible readings and the caller should ASK.

    Ranks every numeric candidate so the REAL time wins over day numbers:
    priority 0 = has am/pm ("11pm"), 1 = a colon 24h time ("22:00") or an
    hour pinned by "at"/"@" (there is no such thing as 14am), 2 = a bare
    1-12 number.  Date tokens are masked first, so the "12th" in
    "Saturday the 12th 9pm" can never be read as a clock hour — which also
    fixes the old silent corruption of inputs like "the 12th 9".

    Hint words settle an ambiguous bare hour: morning -> AM,
    evening/night/tonight -> PM.  Returns None when no valid time exists.
    """
    if _MIDNIGHT.search(text):
        return {"h": 0, "minute": 0, "certain": True}
    if _NOON.search(text):
        return {"h": 12, "minute": 0, "certain": True}

    hint_am = bool(_HINT_AM.search(text))
    hint_pm = bool(_HINT_PM.search(text))

    # Mask date-token spans (same length!) so their numbers are not hours.
    masked = text
    for rx in (_RE_ISO, _RE_WD_NTH, _RE_MD, _RE_DM, _RE_BARE_NTH):
        for m in rx.finditer(masked):
            masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]

    at_spans = [m.span(1) for m in _AT_PREFIX.finditer(text)]

    def _at(pos: int) -> bool:
        return any(s <= pos < e for s, e in at_spans)

    candidates = []  # (pri, h24, minute, kind): kind is "exact" or "bare"
    for m in _HOUR_RE.finditer(masked):
        h = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        mer = (m.group(3) or "").lower()
        if minute > 59:
            continue
        if mer:
            if h not in range(1, 13):
                continue
            h24 = 0 if (h == 12 and mer == "am") else ((h + 12) % 24 if mer == "pm" else h)
            candidates.append((0, h24, minute, "exact"))
        elif m.group(2):                    # colon time ("22:00") -> certain
            if h <= 23:
                candidates.append((1, h % 24, minute, "exact"))
        elif h >= 13:                       # there is no such thing as 14am — a bare
            candidates.append((1, h % 24, minute, "exact"))   # number >12 can only be 24h
        elif h <= 12:                       # bare number -> AM/PM unknown (maybe)
            candidates.append((2, h, minute, "bare"))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], -c[2]))
    _, h, minute, kind = candidates[0]
    if kind != "bare":
        return {"h": h, "minute": minute, "certain": True}
    # Bare hour: hint words decide; with no hint it stays AM/PM-ambiguous.
    if hint_pm and not hint_am:
        h = 12 if h == 12 else (h % 12) + 12
        return {"h": h, "minute": minute, "certain": True}
    if hint_am and not hint_pm:
        h = 0 if h == 12 else h % 12
        return {"h": h, "minute": minute, "certain": True}
    return {"h": h, "minute": minute, "certain": False}


def _extract_hour(text: str) -> Optional[tuple[int, int]]:
    """(hour, minute) for deterministic silent parsing — the legacy contract.

    Thin wrapper over `_extract_time_info`: an AM/PM-ambiguous bare hour keeps
    its historical default (the number as typed = morning), so existing one-shot
    parses never change.  The ambiguity itself is surfaced separately by
    `resolve_candidates` (the /timeslots clarifying question)."""
    info = _extract_time_info(text)
    if info is None:
        return None
    return (info["h"], info["minute"])

def _nearest_weekday(ref: date, weekday: int, *, skip_today: bool = False) -> date:
    """Nearest date at/after `ref` (strictly after if `skip_today`) that is the
    given weekday (0=Mon..6=Sun)."""
    delta = (weekday - ref.weekday()) % 7
    if delta == 0 and skip_today:
        delta = 7
    return date.fromordinal(ref.toordinal() + delta)


def _resolve_date_tokens(raw: str, ref: date, next_flag: bool) -> Optional[date]:
    """Find the date hidden anywhere in a free-form "when" string.

    Returns None when the string carries NO date (a bare time-of-day like
    "10pm ET") — in that case the caller supplies one from the `day` field or
    today.  Recognition order: ISO > weekday+Nth ("saturday the 12th") >
    month/day ("october 5th", "5th of october") > relative day word (today /
    tonight = ref, tomorrow & friends = ref+1) > bare weekday ("friday",
    "next tuesday" — always at/after ref, so a past time today yields an honest
    error instead of silently jumping a week) > bare month ("october") > bare
    ordinal ("the 12th" → current month, rolled forward if past).

    Raises ValueError with a friendly message when the tokens can't form a real
    date.
    """
    # 1) Full ISO date wins outright.
    iso = _RE_ISO.search(raw)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            raise ValueError(f"That's not a real date: {raw!r}")

    # 2) Weekday + day-of-month  ("saturday the 12th")
    wdn = _RE_WD_NTH.search(raw)
    if wdn:
        wd = _WEEKDAY[wdn.group(1).lower()]
        dnum = int(wdn.group(2))
        if not (1 <= dnum <= 31):
            raise ValueError(f"That's not a real day: {raw!r}")
        return _nearest_weekday_day(ref, wd, dnum)

    # 3) Month + day  ("october 5th", "5th of october", "june 5, 2026")
    md = _RE_MD.search(raw) or _RE_DM.search(raw)
    if md:
        if md.re is _RE_MD:
            month, dnum, ytxt = _month_num(md.group(1)), int(md.group(2)), md.group(3)
        else:
            dnum, month, ytxt = int(md.group(1)), _month_num(md.group(2)), md.group(3)
        if not (1 <= dnum <= 31):
            raise ValueError(f"That's not a real day: {raw!r}")
        year = int(ytxt) if ytxt else ref.year
        try:
            d = date(year, month, dnum)
        except ValueError:
            raise ValueError(f"That's not a real date: {raw!r}")
        return d if ytxt else _roll_forward(d, ref)

    # 3b) Relative day words  ("today", "tonight" = ref; tomorrow / tmrw = +1)
    rel = _RE_REL.search(raw)
    if rel:
        word = rel.group(1).lower()
        if word in ("tomorrow", "tmr", "tmrw", "to-morrow"):
            return date.fromordinal(ref.toordinal() + 1)
        return ref

    # 4) Bare weekday  ("friday", "next tuesday") — at/after `ref` (this week's
    #    Friday when earlier in the week, next week's if it already passed).
    w = _RE_WD.search(raw)
    if w:
        wd = _WEEKDAY[w.group(1).lower()]
        d = _nearest_weekday(ref, wd)
        if next_flag:
            d = date.fromordinal(d.toordinal() + 7)
        return d

    # 5) Bare month  ("october") -> 1st of that month (next year if past)
    mo = _RE_MONTH.search(raw)
    if mo:
        d = date(ref.year, _month_num(mo.group(1)), 1)
        return _roll_forward(d, ref)

    # 6) Bare ordinal  ("the 12th") -> 12th of the current month, else next month
    bn = _RE_BARE_NTH.search(raw)
    if bn:
        dnum = int(re.search(r"\d{1,2}", bn.group(0)).group(0))
        if not (1 <= dnum <= 31):
            raise ValueError(f"That's not a real day: {raw!r}")
        return _roll_forward(date(ref.year, ref.month, dnum), ref)

    # No date in the string.
    return None


def parse_when(text: str, ref: Optional[date] = None) -> tuple[date, tuple[int, int]]:
    """Parse a free-form "when" string into (date, (hour, minute)).

    The day and the time can appear in ANY order:
      "10 PM est, friday"            -> nearest Friday, 22:00
      "Saturday the 12th 11pm EST"   -> nearest Saturday that is a 12th, 23:00
      "tuesday 9pm"                  -> next Tuesday, 21:00
      "the 12th 9pm"                 -> 12th of this month, 21:00
      "october 5th 8pm"              -> Oct 5 (this year, or next if past), 08:00
      "2026-11-01 9am"               -> Nov 1 2026, 09:00

    Defaults (Nadia's spec): no month -> current month; bare weekday -> the
    next closest one; "next <weekday>" -> the one after that.

    Raises ValueError when no time (or no sensible date) can be found.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("No start time given.")
    ref = ref or date.today()
    next_flag = bool(_RE_NEXT.search(raw))

    hour = _extract_hour(raw)
    if hour is None:
        raise ValueError(
            f"I couldn't read a time in {raw!r}.  Try “10pm ET” or “22:00”.")

    # The date may be anywhere in the string (or nowhere — then `ref` wins).
    d = _resolve_date_tokens(raw, ref=ref, next_flag=next_flag)
    if d is None:
        d = ref
    return d, hour



def start_has_date(text: str) -> bool:
    """True if `text` carries recognisable date info (weekday, month, ordinal,
    ISO date, or a 4-digit year) — meaning the separate "day" field is
    optional and start_time supplies its own date.  A bare time like "10pm ET"
    is False; a full timestamp ("<t:…:t>" / bare unix) is True."""
    raw = (text or "").strip()
    if not raw:
        return False
    if re.search(r"<t:\d{8,12}:t\s*>", raw, re.I) or re.fullmatch(r"\d{8,12}", raw):
        return True
    return bool(_RE_WD.search(raw) or _RE_MONTH.search(raw)
                or _RE_BARE_NTH.search(raw) or _RE_ISO.search(raw)
                or _RE_REL.search(raw) or _RE_YEAR.search(raw))


@dataclass(frozen=True)
class WhenCandidate:
    """One concrete "when" the bot is asking about.

    `ts`     — the fully-resolved UNIX timestamp (date + time-of-day).
    `label`  — a short human label for a Discord button ("Fri Oct 23 ·
               9:00 PM ET"), kept under Discord's 32-char button limit by
               construction in `_when_label`.
    """
    ts: int
    label: str


def _zone_from_raw(raw: str) -> str:
    """The IANA zone named in `raw` (ET/EST/CT/… / BST …), default ET."""
    m = _TZ_RE.search(raw or "")
    return _TZ_MAP.get(m.group(0).lower(), "America/New_York") if m else "America/New_York"


def _when_label(d: date, h: int, minute: int, zone: str) -> str:
    """A ≤32-char button label for a candidate moment, e.g. “Fri Sep 11 ·
    10:00 PM ET”.

    Built without %e/%-I-style strftime flags (Windows' C library leaves them
    as literal junk): weekday/month abbreviations are standard %-a/%-b-free —
    just %a and %b, the day number is plain text, and the hour is converted to
    12-hour form by hand.  The zone comes from a real DST-aware datetime so
    ET in September correctly renders as EDT."""
    if ZoneInfo is None:
        raise ValueError("Timezone data is unavailable on this system.")
    dt = datetime(d.year, d.month, d.day, h, minute, tzinfo=ZoneInfo(zone))
    zname = dt.strftime("%Z")                    # EDT / EST / BST — portable %Z
    ampm = "PM" if 12 <= h <= 23 else "AM"
    h12 = (h % 12) or 12
    label = f"{d.strftime('%a')} {d.strftime('%b')} {d.day} · {h12}:{minute:02d} {ampm} {zname}"
    # Discord caps button labels at 32 chars.  The date part is exactly the
    # information this question is about, so it's kept whole; only the tail may
    # give (worst case: "Fri Sep 12 · 9:00 AM EST" → drops the zone name).
    if len(label) > 32:
        label = label[:32].rstrip()
    return label


def _hour_variants(info: dict, d: date, zone: str) -> List[WhenCandidate]:
    """The candidate instant(s) for a time-of-day on date `d`.

    A certain hour yields exactly one; an AM/PM-ambiguous bare hour (e.g.
    "the 12th 9") yields both readings — the ambiguous pair IS the question."""
    if info["certain"]:
        return [WhenCandidate(
            int(datetime(d.year, d.month, d.day, info["h"], info["minute"],
                         tzinfo=ZoneInfo(zone)).timestamp()),
            _when_label(d, info["h"], info["minute"], zone))]
    h_am = 0 if info["h"] == 12 else info["h"] % 12
    h_pm = 12 if info["h"] == 12 else (info["h"] % 12) + 12
    return [WhenCandidate(
        int(datetime(d.year, d.month, d.day, hh, info["minute"],
                     tzinfo=ZoneInfo(zone)).timestamp()),
        _when_label(d, hh, info["minute"], zone)) for hh in (h_am, h_pm)]


def resolve_candidates(start_time: str,
                       day: Optional[str] = None,
                       ref: Optional[date] = None,
                       now: Optional[float] = None) -> tuple[str, List[WhenCandidate]]:
    """Decide what a free-form start-time string means — and whether it needs
    a clarifying question.

    Returns (kind, candidates):
      kind "ok"     — unambiguous; exactly one candidate.  Callers may just use
                      it (or keep using `build()`, which is equivalent).
      kind "am_pm"  — the DATE is known but AM/PM isn't ("the 12th 9"); two
                      candidates, ask which.
      kind "date"   — no date at all and today's reading has already passed /
                      none fits; up to three candidates (Thu/Fri/Sat style),
                      ask which day.

    Past instants are dropped from the candidate list entirely; when EVERY
    reading is in the past, ValueError is raised with a friendly message (the
    honest "that time has already passed" error — never a silent jump).

    `ref` is today's date for relative words / bare weekdays; `day` (the
    separate slash field) supplies the date when start_time has none.  Pass
    fixed values in tests for determinism."""
    if ZoneInfo is None:
        raise ValueError("Timezone data is unavailable on this system.")
    raw = (start_time or "").strip()
    if not raw:
        raise ValueError("No start time given.")
    ref = ref or date.today()
    now = float(now if now is not None else _time_mod.time())
    zone = _zone_from_raw(raw)

    # A full timestamp already knows its exact moment.
    if start_is_instant(raw):
        ts = parse_start_time(raw)
        dti = datetime.fromtimestamp(ts, ZoneInfo(zone))
        return "ok", [WhenCandidate(ts, _when_label(dti.date(), dti.hour, dti.minute, zone))]

    info = _extract_time_info(raw)
    if info is None:
        raise ValueError(
            f"I couldn't read a time in {raw!r}.  Try “10pm ET” or “22:00”.")

    d = _resolve_date_tokens(raw, ref=ref, next_flag=bool(_RE_NEXT.search(raw)))
    source = "tokens"                          # the string names its own day…
    if d is None and (day or "").strip():      # …or the separate field does…
        d, source = parse_day(day, ref=ref), "day_field"
    if d is None:                              # …and otherwise it's just today.
        d, source = ref, "default"

    cands = _hour_variants(info, d, zone)
    future = [c for c in cands if c.ts > now]

    if not future and source == "default":
        # Every reading is in the past → try the next up to three days with
        # the same time-of-day; a "9am" said at 6pm today becomes Thu/Fri/Sat.
        cands, day_d = [], d
        for _ in range(3):
            day_d = date.fromordinal(day_d.toordinal() + 1)
            cand = WhenCandidate(
                int(datetime(day_d.year, day_d.month, day_d.day, info["h"],
                             info["minute"], tzinfo=ZoneInfo(zone)).timestamp()),
                _when_label(day_d, info["h"], info["minute"], zone))
            cands.append(cand)
        future = [c for c in cands if c.ts > now] or cands

    if not future:
        raise ValueError("That time has already passed — try a later one.")

    # Pick the question kind.  Exactly ONE surviving reading needs no
    # question at all (a bare hour whose AM side already passed must mean PM).
    if len(future) == 1:
        return "ok", future
    if (not info["certain"]) and len(cands) == 2:
        return "am_pm", future          # both readings alive, one known date
    return "date", future               # several days in play


def question_text(kind: str, n_cands: int) -> str:
    """The clarifying-question line shown above the answer buttons."""
    if kind == "am_pm":
        return ("I've got the date — which time did you mean?  Tap one:")
    # kind == "date"
    return ("I'm not sure which day you meant — pick the right one:")


def parse_day(text: str, ref: Optional[date] = None) -> date:
    """Parse a day spec into a `date`.

    * "friday" / "mon"  -> the closest UPCOMING occurrence (today if it is).
    * "June 5th" / "5th June" / "6/5" style month-day -> this year; if that
      date has already passed this year, roll to next year.
    * "2026-09-10" -> that exact date.
    * a bare weekday with no month/day also works.

    Raises ValueError when it can't resolve a day.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("No day given.")
    ref = ref or date.today()

    # ISO / explicit year.
    iso = _ISO_RE.search(raw)
    if iso:
        y, mo, d = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            raise ValueError(f"That's not a real date: {raw!r}")

    year = None
    ym = _YEAR_RE.search(raw)
    if ym:
        year = int(ym.group(0))

    md = _MD_RE.search(raw) or _DM_RE.search(raw)
    if md:
        if md.re.pattern and "jan" in md.re.pattern:  # month-first
            month = _MONTH[md.group(1).lower()]
            day = int(md.group(2))
        else:                                          # day-first
            day = int(md.group(1))
            month = _MONTH[md.group(2).lower()]
        if not (1 <= month <= 12 and 1 <= day <= 31):
            raise ValueError(f"That's not a real date: {raw!r}")
        explicit_year = year is not None
        if year is None:
            year = ref.year
        try:
            d = date(year, month, day)
        except ValueError:
            raise ValueError(f"That's not a real date: {raw!r}")
        # No explicit year AND the date already passed this year -> assume the
        # staff member is planning a FUTURE event, so roll to next year.
        # An explicit year (e.g. "june 5, 2026") is always respected verbatim.
        if not explicit_year and d < ref:
            try:
                d = date(ref.year + 1, month, day)
            except ValueError:
                pass
        return d

    wd = _WEEKDAY_TOKEN.search(raw)
    if wd:
        key = wd.group(1).lower()
        if key in ("sept",):
            key = "september"
        if key not in _WEEKDAY:
            raise ValueError(f"I don't know that weekday: {wd.group(1)!r}")
        return _closest_upcoming_weekday(ref, _WEEKDAY[key])

    raise ValueError(
        "I couldn't read that as a day.  Try “friday”, “June 5th”, or "
        "“2026-09-10”.")


def parse_duration(text: str) -> int:
    """Parse a duration like "1 hour", "2 hour", "1.5 hours", "90 min" -> seconds.

    Also accepts a bare number of minutes ("15").  Raises ValueError when it
    can't parse.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("No duration given.")
    if re.fullmatch(r"\d{1,3}", raw):
        return int(raw) * 60
    low = raw.lower()
    hours = 0.0
    minutes = 0.0
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours|o'clock)\b", low)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b", low)
    if h:
        hours = float(h.group(1))
    if m:
        minutes = float(m.group(1))
    if hours == 0 and minutes == 0:
        # Last resort: a bare number = minutes.
        num = re.search(r"(\d+(?:\.\d+)?)", low)
        if not num:
            raise ValueError(
                "I couldn't read that as a length.  Try “1 hour”, “1.5 hours”, "
                "or “90 min”.")
        return int(round(float(num.group(1)) * 60))
    seconds = int(round((hours * 60 + minutes) * 60))
    if seconds <= 0:
        raise ValueError("That duration is zero or negative.")
    return seconds


def letter_enabled(text: str) -> bool:
    """Does the user want A–Z letter emotes?  "yes"/"y"/"yeah" -> True."""
    low = (text or "").strip().lower()
    if not low:
        return False
    return low in {"yes", "y", "yep", "yeah", "sure", "true", "1", "on", "please"}
def door_line(ts: int) -> str:
    """The doors-open line:  __<t:TS:F>__ (<t:TS:R>)"""
    return f"__<t:{ts}:F>__ (<t:{ts}:R>)"


def slots(start_ts: int, n: int, step_seconds: int) -> list:
    """The list of `n` slot timestamps starting at `start_ts`, `step_seconds` apart."""
    if n < 1:
        raise ValueError("Need at least one slot.")
    if step_seconds <= 0:
        raise ValueError("Slot length must be positive.")
    return [start_ts + i * step_seconds for i in range(n)]


def build(start_time: str,
          slot_count: str,
          day: Optional[str] = None,
          doors_lead: Optional[str] = "15",
          add_letters: Optional[str] = None,
          slot_duration: Optional[str] = "1 hour",
          ref: Optional[date] = None) -> str:
    """Build the full /timeslots reply.

    Args:
      start_time    : "10pm ET" / "22:00" / "1789178400" / "<t:…:t>"
      slot_count    : "6" / "twelve"
      day           : "friday" / "June 5th" / "2026-09-10"  — OPTIONAL when
                      `start_time` carries its own date (a natural-language
                      string like "10 PM est, friday" or a full timestamp like
                      1789178400); otherwise it is the day a bare time-of-day
                      ("10pm ET") is resolved against.
      doors_lead    : minutes (or "30 min" / "1 hour") before slot 1,
                      default "15".  Pass "" / None to omit the doors line.
      add_letters   : "yes" to prepend A–Z letter emotes (else omitted).
      slot_duration : "1 hour" (default) / "2 hour" / "1.5 hours" / "90 min".
      ref           : the "today" a bare weekday / month-day is resolved
                      against.  Defaults to the real current date; tests pass
                      a fixed date so the golden example is deterministic.

    Returns the reply text: optional doors-open line, then one `<t:…:t>`
    line per slot (each optionally prefixed with its letter emote).
    """
    day_text = (day or "").strip()

    # A full timestamp already knows its own date — use it, day not required.
    if start_is_instant(start_time):
        start_ts = parse_start_time(start_time)
        d = date.fromtimestamp(start_ts)  # only used for any derived date logic
    else:
        # Natural-language start_time may carry its OWN day
        # ("10 PM est, friday", "Saturday the 12th 11pm EST") — use that.
        # Otherwise fall back to the separate `day` field; if there's no
        # day at all, the time-of-day resolves against `ref` (today).
        if start_has_date(start_time):
            d = parse_when(start_time, ref=ref)[0]
        elif day_text:
            d = parse_day(day_text, ref=ref)
        else:
            d = ref or date.today()

    # Slot count.
    n_text = (slot_count or "").strip()
    try:
        n = int(n_text)
    except ValueError:
        # "six", "twelve" — a small word map is plenty for a slot count.
        _WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                  "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                  "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14}
        word = n_text.lower().strip().rstrip("s")
        if word in _WORDS:
            n = _WORDS[word]
        else:
            raise ValueError(
                f"How many slots?  Give me a number — e.g. “6”. (got {n_text!r})")
    if n < 1:
        raise ValueError("Need at least one slot.")

    # Step between slots.
    step = parse_duration(slot_duration or "1 hour")

    # Doors-open lead (optional).
    doors_text = (doors_lead or "").strip()
    doors_seconds = 0
    if doors_text:
        doors_seconds = parse_duration(doors_text)

    start_ts = parse_start_time(start_time, ref=d)
    return slot_block(start_ts, n, step, doors_seconds=doors_seconds,
                      letters=letter_enabled(add_letters))


def slot_block(start_ts: int,
               n: int,
               step_seconds: int,
               doors_seconds: int = 0,
               letters: bool = False) -> str:
    """Render the reply block for an ALREADY-RESOLVED start timestamp.

    Optional doors-open line (doors lead before slot 1), then one `<t:…:t>` per
    slot `step_seconds` apart, each optionally prefixed with its A–Z letter
    emote.  This is build()'s tail — the /timeslots clarifying buttons render
    each answer candidate through here without re-parsing anything."""
    ts_list = slots(start_ts, n, step_seconds)
    lines = []
    if doors_seconds > 0:
        lines.append(door_line(start_ts - doors_seconds))
    for i, ts in enumerate(ts_list):
        tok = f"<t:{ts}:t>"
        lines.append(f"{djlineup.letter_for_index(i)} {tok}" if letters else tok)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test  (run: python timeslots.py)
# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(label: str, ok: bool, got=None, want=None):
        nonlocal failures
        print(f"  {'PASS ✅' if ok else 'FAIL ❌'} — {label}")
        if not ok:
            if got is not None:
                print("    GOT : " + repr(got))
            if want is not None:
                print("    WANT: " + repr(want))
            failures += 1

    # Anchor: today is Wed 2026-09-09, so "friday" -> Fri 2026-09-11.
    ref = date(2026, 9, 9)
    GOLDEN = 1789178400  # Fri 2026-09-11 22:00 EDT  (the number Nadia landed on)

    # 1) parse_start_time: "10pm ET" on that Friday == the golden timestamp.
    print("=== 1. golden start time (10pm ET, Fri 2026-09-11) ===")
    check("10pm ET -> golden", parse_start_time("10pm ET", ref=date(2026, 9, 11)) == GOLDEN,
          parse_start_time("10pm ET", ref=date(2026, 9, 11)), GOLDEN)
    check("10 pm EST (DST-aware) -> golden",
          parse_start_time("10 pm EST", ref=date(2026, 9, 11)) == GOLDEN,
          parse_start_time("10 pm EST", ref=date(2026, 9, 11)), GOLDEN)
    check("22:00 ET -> golden",
          parse_start_time("22:00 ET", ref=date(2026, 9, 11)) == GOLDEN,
          parse_start_time("22:00 ET", ref=date(2026, 9, 11)), GOLDEN)
    check("bare unix -> itself", parse_start_time("1789178400") == 1789178400)
    check("<t:unix:t> -> unix", parse_start_time("<t:1789178400:t>") == 1789178400)

    # 2) parse_start_time: 12-hour noon/midnight + a winter date (EST, UTC-5).
    print("=== 2. noon/midnight + EST winter (UTC-5) ===")
    # noon (12:00) EST = 17:00 UTC.
    noon_est = datetime(2026, 1, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    check("noon EST winter", parse_start_time("noon", ref=date(2026, 1, 9))
          == int(noon_est.timestamp()),
          parse_start_time("noon", ref=date(2026, 1, 9)), int(noon_est.timestamp()))

    # 3) parse_day: bare weekday = closest upcoming (from Wed -> Fri).
    print("=== 3. bare weekday -> closest upcoming ===")
    check("'friday' from Wed", parse_day("friday", ref) == date(2026, 9, 11),
          parse_day("friday", ref), date(2026, 9, 11))
    check("'mon' from Wed -> next Mon", parse_day("mon", ref) == date(2026, 9, 14),
          parse_day("mon", ref), date(2026, 9, 14))
    check("'wed' from Wed -> today", parse_day("wed", ref) == date(2026, 9, 9),
          parse_day("wed", ref), date(2026, 9, 9))

    # 4) parse_day: month-day and ISO.
    print("=== 4. month-day and ISO dates ===")
    # "June 5th" with no year and the date already PAST (today is Sep 9) ->
    # staff are planning a future event, so it rolls to next year (2027-06-05).
    check("'June 5th' past-date rolls to next year",
          parse_day("June 5th", ref) == date(2027, 6, 5),
          parse_day("June 5th", ref), date(2027, 6, 5))
    check("'2026-09-10' ISO", parse_day("2026-09-10", ref) == date(2026, 9, 10),
          parse_day("2026-09-10", ref), date(2026, 9, 10))
    # Explicit year is respected verbatim even though the date is past.
    check("'june 5, 2026' explicit year respected",
          parse_day("june 5, 2026", ref) == date(2026, 6, 5),
          parse_day("june 5, 2026", ref), date(2026, 6, 5))
    # A FUTURE month-day this year is NOT rolled (Oct 20 is ahead of Sep 9).
    check("'October 20' this year (future, no roll)",
          parse_day("October 20", ref) == date(2026, 10, 20),
          parse_day("October 20", ref), date(2026, 10, 20))

    # 5) parse_duration.
    print("=== 5. duration parsing ===")
    check("'1 hour' -> 3600", parse_duration("1 hour") == 3600)
    check("'2 hour' -> 7200", parse_duration("2 hour") == 7200)
    check("'1.5 hours' -> 5400", parse_duration("1.5 hours") == 5400)
    check("'90 min' -> 5400", parse_duration("90 min") == 5400)
    check("'15' -> 900", parse_duration("15") == 900)

    # 6) letter_enabled.
    print("=== 6. letter emote toggle ===")
    check("'yes' -> True", letter_enabled("yes") is True)
    check("'no' -> False", letter_enabled("no") is False)
    check("'' -> False", letter_enabled("") is False)

    # 7) THE GOLDEN EXAMPLE — your exact input, your exact expected output.
    #    ref fixed to a non-Friday so "friday" deterministically -> next Fri (9/11).
    print("=== 7. GOLDEN EXAMPLE (10pm ET, friday, 6 slots) ===")
    out = build("10pm ET", "6", "friday",
                doors_lead="15", add_letters=None, slot_duration="1 hour",
                ref=date(2026, 9, 9))
    lines = out.splitlines()
    check("7 lines (1 doors + 6 slots)", len(lines) == 7, out)
    check("first slot is the golden token",
          lines[1] == f"<t:{GOLDEN}:t>", lines[1], f"<t:{GOLDEN}:t>")
    # Slot i (0-based) sits at line i+1 and is GOLDEN + i*3600.
    check("all 6 slots step by 3600 from the golden start",
          all(lines[1 + i] == f"<t:{GOLDEN + i * 3600}:t>" for i in range(6)),
          out)
    # Doors line is exactly 900s (15 min) before slot 1, in the F+R format.
    want_doors = f"__<t:{GOLDEN - 900}:F>__ (<t:{GOLDEN - 900}:R>)"
    check("doors-open line (15 min early)", lines[0] == want_doors, lines[0], want_doors)

    # 8) Golden example WITH letter emotes.
    print("=== 8. golden example + letter emotes ===")
    outl = build("10pm ET", "6", "friday", doors_lead="15",
                 add_letters="yes", slot_duration="1 hour", ref=date(2026, 9, 9))
    linesl = outl.splitlines()
    check("6 labelled slots",
          linesl[1] == f":regional_indicator_a: <t:{GOLDEN}:t>",
          linesl[1], f":regional_indicator_a: <t:{GOLDEN}:t>")
    check("slot B labelled",
          linesl[2] == f":regional_indicator_b: <t:{GOLDEN + 3600}:t>",
          linesl[2])

    # 9) Custom slot duration (1.5h) changes the step.
    print("=== 9. 1.5-hour slots ===")
    out15 = build("10pm ET", "3", "friday", doors_lead="", slot_duration="1.5 hours",
                  ref=date(2026, 9, 9))
    lines15 = out15.splitlines()
    check("no doors line when doors_lead empty", len(lines15) == 3, out15)
    check("step is 5400",
          lines15[1] == f"<t:{GOLDEN + 5400}:t>", lines15[1], f"<t:{GOLDEN + 5400}:t>")

    # 10) Bad inputs raise friendly ValueErrors.
    print("=== 10. bad inputs raise ===")
    for bad in (lambda: build("", "6", "friday"),
                lambda: build("10pm ET", "", "friday"),
                lambda: build("10pm ET", "6", "notaday")):
        try:
            bad()
            check("should have raised ValueError", False, "no exception")
        except ValueError:
            check("raises ValueError", True)

    # 11) UK timezones (for Hyndal): BST is DST-aware like the US zones.
    print("=== 11. BST / GMT (Europe/London) ===")
    from zoneinfo import ZoneInfo as _ZI
    utc = _ZI("UTC")
    # DST-awareness, compared against UTC on EACH date (not to each other —
    # different dates differ by months, not a DST hour).
    #   Summer (Sep 11): London on BST = UTC+1  ->  "10pm" = 21:00 UTC.
    #   Winter (Jan 9) : London on GMT = UTC+0  ->  "10pm" = 22:00 UTC.
    bst_summer = parse_start_time("10pm BST", ref=date(2026, 9, 11))
    bst_winter = parse_start_time("10pm BST", ref=date(2026, 1, 9))
    check("10pm BST (summer) == 21:00 UTC (BST=UTC+1)",
          bst_summer == int(datetime(2026, 9, 11, 21, 0, tzinfo=utc).timestamp()),
          bst_summer)
    check("10pm BST (winter) == 22:00 UTC (GMT=UTC+0)",
          bst_winter == int(datetime(2026, 1, 9, 22, 0, tzinfo=utc).timestamp()),
          bst_winter)
    # GMT is always UTC+0.
    gmt_val = parse_start_time("10pm GMT", ref=date(2026, 1, 9))
    check("10pm GMT == 22:00 UTC",
          gmt_val == int(datetime(2026, 1, 9, 22, 0, tzinfo=utc).timestamp()),
          gmt_val)
    # BST and GMT are identical in winter (London on GMT).
    check("10pm BST == 10pm GMT in winter (London on GMT)",
          bst_winter == gmt_val, f"{bst_winter} vs {gmt_val}")
    # And they DIFFER in summer (BST is +1, GMT stays +0).
    check("10pm BST != 10pm GMT in summer (DST active)",
          parse_start_time("10pm BST", ref=date(2026, 9, 11)) !=
          parse_start_time("10pm GMT", ref=date(2026, 9, 11)),
          "should differ by 1h in summer")

    # 12) A full timestamp carries its own date -> the day field is optional.
    print("=== 12. timestamp start needs no day ===")
    check("start_is_instant: bare unix", start_is_instant("1789178400"))
    check("start_is_instant: <t:…:t> token", start_is_instant("<t:1789178400:t>"))
    check("start_is_instant: NOT a time-of-day", not start_is_instant("10pm ET"))
    # Bare unix, NO day -> works, uses the timestamp's own date (the golden).
    out_ts = build("1789178400", "6")
    lines_ts = out_ts.splitlines()
    check("unix ts, no day -> 7 lines (doors + 6)", len(lines_ts) == 7, out_ts)
    check("unix ts, no day -> slot 1 is golden",


          lines_ts[1] == f"<t:{GOLDEN}:t>", lines_ts[1])
    # Ham-time token, NO day -> works too.
    out_tok = build("<t:1789178400:t>", "3", doors_lead="")
    check("<t:…:t> token, no day -> 3 slots", len(out_tok.splitlines()) == 3, out_tok)
    # Time-of-day with NO day -> resolves against TODAY (the day field is
    # optional; a bare time means "today at that time").
    import time as _time
    out_today = build("10pm ET", "3", doors_lead="")  # no doors line -> slot 1 is line 1
    today = date.fromtimestamp(int(_time.time()))
    from zoneinfo import ZoneInfo as _ZI
    want_today = int(datetime(today.year, today.month, today.day, 22, 0,
                              tzinfo=_ZI("America/New_York")).timestamp())
    check("time-of-day with no day -> today 22:00",
          out_today.splitlines()[0] == "<t:" + str(want_today) + ":t>",
          out_today.splitlines()[0], "<t:" + str(want_today) + ":t>")

    # 13) Natural-language start_time: day + time in ONE free-form string.
    #     ref = Wed 2026-09-09.  Expected values computed with zoneinfo.
    print("=== 13. natural-language start_time (day + time in one string) ===")
    NL = [
        ("10 PM est, friday",          1789178400),  # nearest Fri (9/11) 22:00
        ("10 PM est, fri",             1789178400),  # abbrev weekday
        ("Saturday the 12th 11pm EST", 1789268400),  # nearest Sat-12th (2026-09-12) 23:00
        ("the 12th 9pm",               1789261200),  # 12th this month (9/12) 21:00
        ("tuesday 9pm",                1789520400),  # next Tuesday (9/15) 21:00
        ("next tuesday 10pm",          1790128800),  # the Tuesday AFTER (9/22) 22:00
        ("october 5th 8pm",            1791244800),  # month + day (10/5) 20:00
        ("2026-11-01 9am",             1793541600),  # ISO date 09:00
        ("10pm ET",                    1789005600),  # no date -> ref (today) 22:00
    ]
    for s, want in NL:
        check(f"parse_start_time({s!r})",
              parse_start_time(s, ref=ref) == want,
              parse_start_time(s, ref=ref), want)
    # build() with the day INSIDE start_time — no separate day field needed.
    out_nl = build("Saturday the 12th 11pm EST", "4", doors_lead="15", ref=ref)
    lines_nl = out_nl.splitlines()
    check("NL build: 5 lines (doors + 4)", len(lines_nl) == 5, out_nl)
    check("NL build: slot 1 = Sat 2026-09-12 23:00",
          lines_nl[1] == "<t:1789268400:t>", lines_nl[1])
    check("NL build: slots step by 3600",
          all(lines_nl[1 + k] == "<t:" + str(1789268400 + k * 3600) + ":t>" for k in range(4)),
          out_nl)
    # 14) _extract_time_info — the v1.7.0 fixes + ambiguity flag.
    print("=== 14. time extraction: at-pin, hints, day-number masking ===")
    ti = lambda s: _extract_time_info(s)
    check("'friday at 22' -> 22:00 (old code crashed)",
          ti("friday at 22") == {"h": 22, "minute": 0, "certain": True}, ti("friday at 22"))
    check("'the 12th 9pm' -> 21:00 certain",
          ti("Saturday the 12th 9pm EST") == {"h": 21, "minute": 0, "certain": True},
          ti("Saturday the 12th 9pm EST"))
    check("'the 12th 9' -> 9:xx AM/PM ambiguous (old code silently 12:00)",
          ti("the 12th 9") == {"h": 9, "minute": 0, "certain": False}, ti("the 12th 9"))
    check("'saturday evening 10' -> PM via hint",
          ti("saturday evening 10") == {"h": 22, "minute": 0, "certain": True},
          ti("saturday evening 10"))
    check("'tuesday morning 8' -> AM via hint",
          ti("tuesday morning 8") == {"h": 8, "minute": 0, "certain": True},
          ti("tuesday morning 8"))
    check("'friday at 9am' -> 9:00 (at-pin + meridiem)",
          ti("friday at 9am") == {"h": 9, "minute": 0, "certain": True}, ti("friday at 9am"))
    check("'22:00' colon time stays certain",
          ti("saturday 22:00") == {"h": 22, "minute": 0, "certain": True}, ti("saturday 22:00"))

    # 15) Relative day words + past-filter in resolve_candidates.
    print("=== 15. relative days & the clarifying question API ===")
    now = datetime(2026, 9, 9, 18, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    ref = date(2026, 9, 9)
    k, cs = resolve_candidates("tonight at 9pm", ref=ref, now=now)
    check("'tonight at 9pm' -> ok, Wed 17:00 is past so… future?  (18:00 > 21:00?) NO — 21:00 today IS future of 18:00",
          k == "ok" and len(cs) == 1, (k, cs))
    _NY = ZoneInfo("America/New_York")
    check("tonight ts = Wed Sep 9 21:00 ET",
          datetime.fromtimestamp(cs[0].ts, _NY) ==
          datetime(2026, 9, 9, 21, 0, tzinfo=_NY),
          datetime.fromtimestamp(cs[0].ts, _NY))
    k2, c2 = resolve_candidates("tomorrow at 8am", ref=ref, now=now)
    check("'tomorrow at 8am' -> ok, Thu Sep 10 08:00 ET",
          k2 == "ok" and datetime.fromtimestamp(c2[0].ts, _NY) ==
          datetime(2026, 9, 10, 8, 0, tzinfo=_NY), (k2, c2))

    # 16) The three question kinds + slot_block parity with build().
    print("=== 16. ambiguity: am_pm / date kinds, labels ≤32, block parity ===")
    k3, c3 = resolve_candidates("the 12th 9", ref=ref, now=now)   # the 12th this month = Sat Sep 12 (future)
    check("'the 12th 9' -> am_pm with AM+PM pair on Sep 12",
          k3 == "am_pm" and len(c3) == 2, (k3, c3))
    check("am/pm labels fit Discord buttons (≤32 chars)",
          all(len(c.label) <= 32 for c in c3), [c.label for c in c3])
    k4, c4 = resolve_candidates("9am", ref=ref, now=now)          # Wed 09:00 already passed (now=18:00)
    check("'9am' with today's reading past -> date kind, 3 future days",
          k4 == "date" and len(c4) == 3, (k4, c4))
    try:
        resolve_candidates("wednesday at 5pm", ref=ref, now=now)   # Wed 17:00 past & no other reading
        got = ("no error raised", None)
    except ValueError as exc:
        got = ("ValueError", str(exc))
    check("'wednesday at 5pm' (already past) -> friendly ValueError",
          got[0] == "ValueError" and "passed" in got[1].lower(), got)
    k5, c5 = resolve_candidates("friday at 10pm EST", ref=ref, now=now)   # THE headline case
    check("'friday at 10pm EST' -> ok, Fri Sep 11 22:00 (golden)",
          k5 == "ok" and c5[0].ts == GOLDEN, (k5, [c.ts for c in c5]))
    # slot_block is build()'s tail — identical output for the same inputs.
    blk = slot_block(GOLDEN, 4, 3600, doors_seconds=15 * 60)
    bld = build("10pm ET", "4", day="2026-09-11", doors_lead="15", ref=ref)
    check("slot_block == build() output (same inputs)", blk == bld, (blk, bld))
    # Golden parity: resolve_candidates' ok kind agrees with parse_start_time.
    for s in ("tuesday 9pm", "october 5th 8pm", "2026-11-01 9am", "Saturday the 12th 11pm EST"):
        kx, cx = resolve_candidates(s, ref=ref, now=now)
        check(f"resolve parity {s!r} == parse_start_time",
              kx == "ok" and cx[0].ts == parse_start_time(s, ref=ref),
              (kx, [c.ts for c in cx]), parse_start_time(s, ref=ref))

    # start_has_date drives the day-field optionality.
    check("start_has_date('10 PM est, friday')", start_has_date("10 PM est, friday"))
    # start_has_date drives the day-field optionality.
    check("start_has_date('10 PM est, friday')", start_has_date("10 PM est, friday"))
    check("start_has_date('the 12th 9pm')", start_has_date("the 12th 9pm"))
    check("start_has_date('10pm ET') is False", not start_has_date("10pm ET"))
    check("start_has_date('<t:…:t>')", start_has_date("<t:1789178400:t>"))

    print(f"\n{'ALL PASS ✅' if failures == 0 else f'{failures} FAILURE(S) ❌'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
