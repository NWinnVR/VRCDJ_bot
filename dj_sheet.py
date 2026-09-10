#!/usr/bin/env python
"""dj_sheet.py — pull the Wyvern DJ master list from a PUBLIC Google Sheet.

No API key, no OAuth. Two supported link forms:

  1) Published CSV (what Nadia uses — the "publish to web" button):
       https://docs.google.com/spreadsheets/d/e/<PUB_ID>/pub?output=csv
     Fetched directly.

  2) "Anyone with the link — can view" sharing:
       https://docs.google.com/spreadsheets/d/<SHEET_ID>/...
     Fetched via /export?format=csv&gid=0.

Column mapping is by header KEYWORDS (case-insensitive), so column order
in the sheet doesn't matter:

    DJ / name           -> name
    TWITCH              -> twitch
    VRCDN RTSPT         -> rtspt
    VRCDN MPEG-TS       -> mpeg_ts
    VRCDN Preview       -> preview
    Genres              -> genres
    Availability        -> availability

Output shape (one dict per DJ):

    {
      "name":         "Logos Drive link",
      "twitch":       "https://twitch.tv/...",
      "rtspt":        "rtspt://stream.vrcdn.live/live/...",
      "mpeg_ts":      "https://stream.vrcdn.live/live/...",
      "preview":      "https://panel.vrcdn.live/preview/...",
      "genres":       "techno, hard",
      "availability": ""
    }

Any field may be "" (missing). The parser is lenient about blank cells,
merged rows, and header misalignment (Google's export is a little sloppy
with that).

CLI:
    python dj_sheet.py <sheet-url>      # refresh cache, print summary
    python dj_sheet.py --list           # print the cached lineup
    python dj_sheet.py --find <name>    # fuzzy-find a DJ in the cache
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.environ.get("NADIA_DJ_CACHE", os.path.join(HERE, "DJ_LIST.json"))
METADATA_PATH = os.path.join(HERE, "dj_meta.json")

PUB_RE = re.compile(r"/spreadsheets/d/e/([A-Za-z0-9_-]+)")
SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)(?!/e/)")
URL_RE = re.compile(r"https?://\S+|rtspt?://\S+")

# Header keywords per column kind (first match wins; a column may carry
# multiple kinds if it contains both, e.g. a "links" column with twitch
# + vrcdn mixed — we classify each CELL by content below anyway).
_HEADER_KINDS = {
    "name":         ("dj", "name", "handle"),
    "twitch":       ("twitch",),
    "rtspt":        ("rtspt", "rtsp"),
    "mpeg_ts":      ("mpeg", "ts", "vrcdn"),
    "preview":      ("preview", "panel"),
    "genres":       ("genre",),
    "availability": ("availab",),
}

# A valid DJ name. Deliberately LENIENT — real handles start with `_`, `-`,
# `[`, or a digit (17, 780Racer, 9TAILED), use bold-Unicode letters
# (𝐓𝐞𝐜𝐡𝐇𝐲𝐬𝐭𝐞𝐫𝐢𝐚), or contain spaces / slashes ("Bobby J",
# "KAOTIC (DJ)/ FIREKRAKER", "NWinn / Wyvern", "A://DDOS"). The OLD
# ^[A-Za-z]... regex silently dropped hundreds of those, and a no-space
# variant dropped ~100 more. We only reject: empty, >60 chars, newlines,
# cells that are a bare pasted URL, and a handful of known leftover labels.
_BARE_URL = re.compile(r"^\s*(https?|rtsp|rtspt|vrcdn|ftp)://", re.I)
_KNOWN_JUNK = {"vrCDN preview", "vrcdn preview", "logos drive link"}


def _valid_name(n: str) -> bool:
    """True if this cell looks like a real DJ name (see note above)."""
    if not n:
        return False
    n = n.strip()
    if not (1 <= len(n) <= 60):
        return False
    if "\n" in n or "\r" in n:
        return False
    if _BARE_URL.match(n):
        return False
    if n.lower() in _KNOWN_JUNK:
        return False
    return True


# ─────────────────────────── URL helpers ──────────────────────────────
def csv_endpoint(sheet_url: str) -> str:
    """Return the direct-CSV URL for either supported link form."""
    pub = PUB_RE.search(sheet_url or "")
    if pub:
        return (
            "https://docs.google.com/spreadsheets/d/e/"
            + pub.group(1)
            + "/pub?output=csv"
        )
    sid = SHEET_ID_RE.search(sheet_url or "")
    if sid:
        return (
            "https://docs.google.com/spreadsheets/d/"
            + sid.group(1)
            + "/export?format=csv&gid=0"
        )
    # maybe it's already a csv URL
    if sheet_url and "output=csv" in sheet_url:
        return sheet_url
    raise ValueError(
        "That doesn't look like a Google Sheet link I can pull. Use the "
        "'publish to web' CSV link (…/d/e/<id>/pub?output=csv) or the "
        "normal /d/<id>/… link with 'anyone with the link can view'."
    )


def fetch_csv(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "WyvernDJ/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    text = raw.decode("utf-8-sig", errors="replace")
    head = text[:400].lower()
    if "<!doctype" in head or "sign in" in head:
        raise PermissionError(
            "Google returned a login page, not CSV. Make sure the sheet is "
            'shared as "Anyone with the link — can view" (or use the '
            "published-CSV link)."
        )
    return text


# ─────────────────────────── parsing ──────────────────────────────────
def _classify_cell(cell: str) -> str | None:
    """What KIND of value is in this cell (used for fallback when the
    header didn't map a column)."""
    c = (cell or "").strip()
    low = c.lower()
    if not c:
        return None
    if low.startswith("rtspt://") or low.startswith("rtsp://"):
        return "rtspt"
    if "vrcdn" in low and ("preview" in low or "panel" in low):
        return "preview"
    if "vrcdn" in low or low.startswith("https://stream."):
        return "mpeg_ts"
    if "twitch.tv" in low:
        return "twitch"
    if URL_RE.match(c):
        return "other_http"
    return None


def _map_columns(header_row: list[str]) -> dict[str, int]:
    """kind -> column index, first match per kind."""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        c = (cell or "").strip().lower()
        if not c:
            continue
        for kind, keys in _HEADER_KINDS.items():
            if kind in mapping:
                continue
            if any(k in c for k in keys):
                mapping[kind] = idx
                break
    return mapping


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def parse_csv(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c and c.strip() for c in r)]
    if not rows:
        return []

    # Find the header row: first row with a "dj" / "name" / "twitch" keyword.
    hdr_i = 0
    for i, row in enumerate(rows[:5]):
        cells = [c.strip().lower() for c in row if c and c.strip()]
        if any(any(k in c for k in ("dj", "name", "twitch")) for c in cells):
            hdr_i = i
            break
    cols = _map_columns(rows[hdr_i])
    if "name" not in cols:
        cols["name"] = 0  # fall back: first column is the name

    djs: list[dict] = []
    seen: set[str] = set()
    for row in rows[hdr_i + 1:]:
        name = _cell(row, cols["name"])
        if not _valid_name(name):
            continue  # skip blank / stray / leftover-label rows

        entry = {
            "name": name,
            "twitch": _cell(row, cols.get("twitch")),
            "rtspt": _cell(row, cols.get("rtspt")),
            "mpeg_ts": _cell(row, cols.get("mpeg_ts")),
            "preview": _cell(row, cols.get("preview")),
            "genres": _cell(row, cols.get("genres")),
            "availability": _cell(row, cols.get("availability")),
        }

        # Fallback: any un-mapped column with a recognizable URL still gets
        # picked up, so a sheet that adds a new column doesn't silently drop
        # links.
        mapped = {v for v in cols.values() if v is not None}
        for idx, cell in enumerate(row):
            if idx in mapped or idx == cols["name"]:
                continue
            kind = _classify_cell(cell)
            if kind in ("twitch", "rtspt", "mpeg_ts", "preview") \
                    and not entry[kind]:
                entry[kind] = cell.strip()

        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        djs.append(entry)
    return djs


# ─────────────────────────── cache ────────────────────────────────────
def _read_meta() -> dict:
    if not os.path.exists(METADATA_PATH):
        return {}
    try:
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_meta(meta: dict) -> None:
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def refresh(sheet_url: str,
            cache_path: str = CACHE_PATH,
            timeout: int = 30) -> dict:
    """Fetch + parse + cache. Returns a summary dict.

    Raises on network / permission problems (caller decides how to surface).
    """
    endpoint = csv_endpoint(sheet_url)
    text = fetch_csv(endpoint, timeout=timeout)
    djs = parse_csv(text)
    if not djs:
        raise ValueError("Sheet downloaded but no DJ rows were parsed.")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(djs, f, indent=2)
    meta = _read_meta()
    meta.update({
        "count": len(djs),
        "with_any_link": sum(1 for d in djs
                             if any(d.get(k) for k in
                                    ("twitch", "rtspt", "mpeg_ts", "preview"))),
        "last_ok": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": sheet_url,
    })
    _write_meta(meta)
    return meta


def load_cached(cache_path: str = CACHE_PATH) -> list[dict]:
    if not os.path.exists(cache_path):
        return []
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        if not isinstance(d, dict) or not d.get("name"):
            continue
        out.append({
            "name": d["name"],
            "twitch": d.get("twitch", ""),
            "rtspt": d.get("rtspt", ""),
            "mpeg_ts": d.get("mpeg_ts", ""),
            "preview": d.get("preview", ""),
            "genres": d.get("genres", ""),
            "availability": d.get("availability", ""),
        })
    return out


# ─────────────────────────── public API (used by bot.py / dashboard) ─
def _cfg_sheet_url() -> str:
    """Read the configured sheet URL from bot_config.json (same folder)."""
    cfg_path = os.path.join(HERE, "bot_config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            url = (cfg.get("sheet_url") or "").strip()
            if url:
                return url
        except Exception:
            pass
    return os.environ.get("WYVERN_SHEET_URL", "")


def refresh_dj_list(sheet_url: str | None = None,
                    cache_path: str = CACHE_PATH) -> int:
    """Refresh the cache from the sheet. Returns the DJ count.

    sheet_url may be omitted → falls back to bot_config.json → env var.
    """
    url = (sheet_url or "").strip() or _cfg_sheet_url()
    if not url:
        raise ValueError(
            "No sheet URL configured. Put one in bot_config.json under "
            '"sheet_url", or pass it explicitly.'
        )
    meta = refresh(url, cache_path=cache_path)
    return meta["count"]


def load_dj_list() -> list[dict]:
    return load_cached()


def count_djs() -> int:
    return len(load_cached())


def get_dj_freshness() -> str:
    meta = _read_meta()
    if not meta:
        return "never refreshed"
    return f"last refreshed {meta.get('last_ok', '?')} — {meta.get('source', '?')}"


def is_dj_stale(interval_days: int | float) -> bool:
    """True if the cached DJ list is older than the refresh interval.

    'Never refreshed' (no metadata, no cache) counts as stale. A parse
    failure of the last_ok timestamp also counts as stale — better to
    re-fetch than to serve a list we can't date.
    """
    import datetime as _dt
    meta = _read_meta()
    last_ok = (meta or {}).get("last_ok")
    if not last_ok:
        return True
    try:
        then = _dt.datetime.strptime(str(last_ok), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True
    age = _dt.datetime.now() - then
    return age.days >= max(1, int(interval_days))


def get_dj_entry(query: str) -> dict | None:
    """Find a DJ by exact name, then best fuzzy match (ratio >= 0.6)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    djs = load_cached()
    for d in djs:
        if d["name"].lower() == q:
            return d
    best, best_ratio = None, 0.0
    for d in djs:
        r = SequenceMatcher(None, q, d["name"].lower()).ratio()
        if r > best_ratio:
            best, best_ratio = d, r
    if best and best_ratio >= 0.6:
        return best
    return None


def list_djs(n: int | None = None) -> list[dict]:
    djs = load_cached()
    return djs[:n] if n else djs


# ─────────────────────── fuzzy "did you mean" lookup ─────────────────────
# Scoring thresholds. Tune these to taste — they're the only knobs that
# change the UX, and they're named so it's obvious what they do.
FUZZY_EXACT      = 1.0   # name matches the query verbatim (after cleanup)
FUZZY_CONFIDENT  = 0.80  # top hit this strong (and clearly ahead) → "Did you mean …?"
FUZZY_MARGIN     = 0.10  # runner-up must be this far behind to avoid "ambiguous"
FUZZY_ASK_MIN    = 0.55  # below this we stop guessing and just list candidates
FUZZY_ASK_MAX    = 5     # at most this many candidates we ever offer
# Floor for a LONE near-miss. Real mistypes (bobby→Bobby J 0.93, electro→
# ElectroMaster 0.88, 780→780Racer 0.86) all score ≥ ~0.84, while false-friends
# (zzz→Neontempzzz 0.69, skoota→ZOOT 0.60, tech→Mister Chop 0.57) stay ≤ ~0.69.
# A confident "did you mean?" below this line is a guess, not a correction —
# better to say "couldn't find, closest is …" than to assert the wrong DJ.
FUZZY_LONE_MIN   = 0.75


def _norm_name(s: str) -> str:
    """Lowercase, strip accents, drop punctuation/whitespace for matching.

    'DJ Electronic'  -> 'djelectronic'
    'NWinn / Wyvern' -> 'nwinnwyvern'
    '𝐓𝐞𝐜𝐡…' (bold)  -> 'tech…'   (best-effort; bold-unicode maps to base)
    """
    s = (s or "").lower().strip()
    try:
        import unicodedata
        s = "".join(
            c for c in unicodedata.normalize("NFKD", s)
            if not unicodedata.combining(c)
        )
    except Exception:
        pass
    # keep only word chars (letters, digits) — this folds spaces, slashes,
    # dots, parens, brackets, apostrophes, underscores, etc.
    return re.sub(r"[^0-9a-z]+", "", s)


def _norm_name_djfree(s: str) -> str:
    """Like _norm_name, but ALSO drops 'dj' and '(dj)' markers.

    The sheet marks some DJs with a ' (DJ)' suffix and others with a
    'DJ ' prefix — and NOT every entry is a DJ at all (e.g. 'A Roomba').
    So a user typing 'DJ Bobby J' must match 'Bobby J (DJ)', and typing
    'bobby' must still match 'Bobby J (DJ)'. Stripping the dj-token from
    BOTH sides makes all four spellings converge to the same key:
        'Bobby J (DJ)'   -> 'bobbyj'
        'DJ Bobby J'     -> 'bobbyj'
        'bobby j'        -> 'bobbyj'
    Display names are never changed — this is match-only.
    """
    s = (s or "").lower().strip()
    s = re.sub(r"\(\s*dj\s*\)", " ", s, flags=re.I)
    s = re.sub(r"\bdj\b", " ", s, flags=re.I)
    try:
        import unicodedata
        s = "".join(
            c for c in unicodedata.normalize("NFKD", s)
            if not unicodedata.combining(c)
        )
    except Exception:
        pass
    return re.sub(r"[^0-9a-z]+", "", s)


def _score(query: str, name: str) -> float:
    """0.0–1.0. Higher = more confident this DJ matches the query.

    Order of preference (strongest first):
      1. verbatim          -> 1.0
      2. query is a prefix of the name  (e.g. "electro" -> "ElectroMaster")
      3. query is a substring of the name (scaled by overlap)
      4. name is a substring of the query (user typed extra words)
      5. query matches a whole word in the name (prefix > substring)
      6. SequenceMatcher similarity as the floor

    The old flat 0.72 word rule returned on the FIRST partial word hit,
    which is why "skoota" could lose to "A Roomba" — now every word is
    scored and the best one wins, with prefix matches outranking inner
    substrings and a length penalty keeping short queries honest.
    """
    q = _norm_name(query)
    n = _norm_name(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0

    best = 0.0

    # name starts with the query (prefix) — strongest partial signal
    if n.startswith(q):
        best = max(best, 0.80 + 0.15 * min(1.0, len(q) / len(n)))
    # query contains the full name (user added words: "dj bobby" -> "Bobby J")
    elif q.startswith(n):
        best = max(best, 0.78 + 0.15 * min(1.0, len(n) / len(q)))
    elif q in n:
        best = max(best, 0.62 + 0.25 * min(1.0, len(q) / len(n)))
    elif n in q:
        best = max(best, 0.60 + 0.22 * min(1.0, len(n) / len(q)))

    # word-level: score EVERY word, keep the best (prefix > substring)
    for w in name.lower().split():
        wn = _norm_name(w)
        if not wn:
            continue
        if wn.startswith(q):
            best = max(best, 0.84)
        elif q.startswith(wn) and len(wn) >= 3:
            best = max(best, 0.76)
        elif len(q) >= 4 and q in wn:
            best = max(best, 0.70)
        elif len(wn) >= 4 and wn in q:
            best = max(best, 0.68)

    # general fuzzy similarity as the floor
    best = max(best, SequenceMatcher(None, q, n).ratio())

    # length honesty: a very short query (<=2 chars) can't be "confident"
    if len(q) <= 2:
        best = min(best, 0.70)

    return round(best, 4)


def fuzzy_find(query: str, entries: list, limit: int = FUZZY_ASK_MAX) -> dict:
    """Generic fuzzy-find over a list of dicts that each have a "name".

    This is the ONE fuzzy engine the whole bot uses — both the DJ list and
    the host list route through it, so `/dj` and `/host` behave identically.
    `entries` may be any dicts as long as each carries a "name" key; the
    matched dict is echoed back as "entry" so the caller renders its own
    fields (stream links for DJs, the profile URL for hosts).

    Shape:
      {"status": "exact"|"didyoumean"|"ambiguous"|"none",
       "matches": [ {name, score, entry}, … ],   # best first
       "top":    entry | None,                    # only when status is
                                                  # "exact" or "didyoumean"
       "closest": {name, score, entry} | None}    # always the single best
                                                  # candidate (or None), so
                                                  # the "none" branch can
                                                  # offer a soft hint

    Branching rules (all driven by the named thresholds above):
      * exact       — a verbatim name match. Use matches[0].
      * didyoumean  — ONE candidate ≥ FUZZY_LONE_MIN, OR 2+ where the top is
                      ≥ FUZZY_CONFIDENT AND ≥ FUZZY_MARGIN ahead of #2.
                      Bot should ask: "Did you mean **<name>**?"
      * ambiguous   — 2+ close candidates, none clearly ahead. Bot lists them.
      * none        — no confident candidate (a lone hit below FUZZY_LONE_MIN
                      lands here). Bot says "couldn't find" + closest hint.

    The caller (bot.py) decides how to render each status. This function
    never returns a silent single guess — that was the old get_dj_entry()
    behaviour that hid ambiguity.
    """
    q = (query or "").strip()
    if not q:
        return {"status": "none", "matches": [], "top": None}

    scored = []
    for d in entries:
        if not isinstance(d, dict) or not d.get("name"):
            continue
        s = _score(q, d["name"])
        if s >= FUZZY_ASK_MIN:
            scored.append({"name": d["name"], "score": round(s, 3),
                           "entry": d})
    scored.sort(key=lambda m: m["score"], reverse=True)
    scored = scored[:limit]

    if not scored:
        return {"status": "none", "matches": [], "top": None, "closest": None}

    top = scored[0]
    runner = scored[1]["score"] if len(scored) > 1 else 0.0

    # exact? — verbatim, OR identical after dropping DJ/'(DJ)' markers
    # (sheet stores 'Bobby J (DJ)'; users type 'dj bobby j' / 'bobby j').
    if (_norm_name(q) == _norm_name(top["name"])
            or _norm_name_djfree(q) == _norm_name_djfree(top["name"])):
        return {"status": "exact", "matches": scored, "top": top["entry"],
                "closest": top}

    # A lone candidate is never "ambiguous" — there's only one answer to
    # confirm. But only *assert* it ("Did you mean X?") when it clears the
    # floor: a lone hit at 0.60 (zzz→Neontempzzz) is a false-friend, so we
    # fall through to "none" and let the caller offer it as a soft hint.
    if len(scored) == 1 and top["score"] >= FUZZY_LONE_MIN:
        return {"status": "didyoumean", "matches": scored,
                "top": top["entry"], "closest": top}

    # 2+ candidates: only call it "didyoumean" when the top is both strong
    # enough AND clearly ahead of the runner-up; otherwise list the options.
    if (top["score"] >= FUZZY_CONFIDENT
            and (top["score"] - runner) >= FUZZY_MARGIN):
        return {"status": "didyoumean", "matches": scored,
                "top": top["entry"], "closest": top}

    # A lone-but-weak hit, or a weak/ambiguous cluster → don't assert.
    # Still surface the single best candidate as "closest" so the bot can
    # say "couldn't find X — closest was Y (60%)" instead of a dead end.
    return {"status": "ambiguous" if len(scored) > 1 else "none",
            "matches": scored, "top": None, "closest": top}


def find_djs(query: str, limit: int = FUZZY_ASK_MAX) -> dict:
    """Fuzzy-find a DJ in the cached master list. Thin wrapper over
    fuzzy_find() so the host list can reuse the exact same engine."""
    return fuzzy_find(query, load_cached(), limit)


# ─────────────────────────── CLI ──────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip())
        sys.exit(0)
    try:
        if args[0] == "--list":
            djs = list_djs()
            print(f"{len(djs)} DJs cached")
            for d in djs[:20]:
                bits = [d["name"]]
                if d.get("twitch"):
                    bits.append("twitch")
                if d.get("rtspt"):
                    bits.append("rtspt")
                if d.get("mpeg_ts"):
                    bits.append("mpeg")
                if d.get("preview"):
                    bits.append("preview")
                print("  " + " | ".join(bits))
            sys.exit(0)
        if args[0] == "--find" and len(args) >= 2:
            e = get_dj_entry(" ".join(args[1:]))
            print(json.dumps(e, indent=2) if e else "no match")
            sys.exit(0)
        if args[0] == "--status":
            print(get_dj_freshness())
            print("count:", count_djs())
            sys.exit(0)
        # positional: refresh
        n = refresh_dj_list(args[0])
        print(f"OK — cached {n} DJs")
        print(get_dj_freshness())
    except Exception as e:
        print("FAILED:", e)
        sys.exit(1)
