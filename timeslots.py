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
  * day        : "friday" · "mon" · "June 5th" · "june 5, 2026" ·
                 "2026-09-10" · "the 5th"   (a bare weekday = the closest
                 UPCOMING occurrence of that day, counting from today)
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
from datetime import date, datetime, timezone as _utc
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover — zoneinfo is stdlib since 3.9
    ZoneInfo = None  # type: ignore

import djlineup  # reuse the A–Z letter emote engine (/djlineup)

__all__ = [
    "build", "parse_start_time", "parse_day", "parse_duration",
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
        -> a time-of-day needs a date to become a real instant.  `ref`
           (the day being planned) supplies it; without `ref` it defaults
           to TODAY, which is almost never what a staff member wants — so
           callers pass the already-parsed day.

    Raises ValueError with a friendly message when it can't parse.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("No start time given.")

    # 1) Already a Unix timestamp (bare, or wrapped in a ham-time token).
    t = raw
    m = re.search(r"<t:(\d{8,12}):[tTfF]\s*>", t)
    if m:
        return int(m.group(1))
    if re.fullmatch(r"\d{8,12}", t):
        return int(t)

    # 2) Time-of-day + optional zone.
    hour = _parse_hour(raw)
    if hour is None:
        raise ValueError(
            f"I couldn't read that as a time.  Try “10pm ET”, “10:30 pm CT”, "
            f"“22:00”, “noon”, or a Unix timestamp like 1789178400.")
    h, minute = hour

    zone = "America/New_York"
    tz_match = _TZ_RE.search(raw)
    if tz_match:
        zone = _TZ_MAP.get(tz_match.group(0).lower(), zone)
    if ZoneInfo is None:
        raise ValueError("Timezone data is unavailable on this system.")

    d = ref or date.today()
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
        f"I couldn't read that as a day.  Try “friday”, “June 5th”, or "
        f"“2026-09-10”.")


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
                f"I couldn't read that as a length.  Try “1 hour”, “1.5 hours”, "
                f"or “90 min”.")
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
      day           : "friday" / "June 5th" / "2026-09-10"  — REQUIRED for a
                      time-of-day, but OPTIONAL when `start_time` is already a
                      full timestamp (bare unix or `<t:…:t>`), because that
                      input encodes its own date and the day field is ignored.
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
        # Time-of-day: needs a day to become a real instant.
        if not day_text:
            raise ValueError(
                "That start time is a time-of-day (e.g. “10pm ET”) — I also need "
                "the DAY.  Give me “friday”, “June 5th”, or “2026-09-10”.  "
                "(Or pass a full timestamp like 1789178400, which has its own date.)")
        d = parse_day(day_text, ref=ref)

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
    ts_list = slots(start_ts, n, step)

    lines = []
    if doors_seconds > 0:
        lines.append(door_line(start_ts - doors_seconds))
    letters = letter_enabled(add_letters)
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
    lon = _ZI("Europe/London")
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
    # Time-of-day with NO day -> friendly error (needs a day to become an instant).
    try:
        build("10pm ET", "6")  # no day
        check("time-of-day with no day should raise", False, "no exception")
    except ValueError as e:
        check("time-of-day with no day raises (asks for the day)",
              "DAY" in str(e).upper() or "day" in str(e), str(e))

    print(f"\n{'ALL PASS ✅' if failures == 0 else f'{failures} FAILURE(S) ❌'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
