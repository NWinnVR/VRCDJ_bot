"""djlineup.py — label event-post time-slots with letter emotes.

WHAT IT DOES
  Staff lay a new event out as a block of Discord *ham-time* slots, e.g.

      <t:1788667200:t>
      <t:1788670800:t>
      <t:1788674400:t>

  so a DJ can sign up for "slot B" instead of "10 pm" (which can mean a
  different hour for each person, depending on their time-zone).  /djlineup
  takes that block and labels each time slot with a Discord *regional
  indicator* letter, in the EXACT order the slots appear:

      :regional_indicator_a: <t:1788667200:t>
      :regional_indicator_b: <t:1788670800:t>
      :regional_indicator_c: <t:1788674400:t>

  Past 26 slots the labels continue in **Excel-column style**:
  a, b, …, z, aa, ab, …, az, ba, …, zz, aaa, …
  A two-letter code renders as two adjacent regional-indicator emotes
  (e.g. `:regional_indicator_a::regional_indicator_a:` → **AA**), so a DJ
  can still refer to "slot AA" unambiguously.  There is no cap — the labels
  keep growing (aa, ab, …, zz, aaa, …) until Discord's 2000-char message
  limit is hit, at which point `discord_send` chunks the rest into
  follow-up messages.

TOKEN-BASED, NOT LINE-BASED (why — learned the hard way, 2026-09-09)
  We find every `<t:…:t>` token in the pasted text and label each one, in
  order — we do NOT rely on the text being one-slot-per-line.  That matters
  because Discord's slash-command text field FLATTENS a pasted block: the
  newlines you see while typing become spaces by the time the string reaches
  the bot.  A line-based parser would see one line holding six slots and hand
  it a single label (the real "A 23:00 00:00 01:00…" miss-fire).  The token
  boundaries are unambiguous either way, so token extraction is immune to how
  the paste was flattened.

  Only *time* slots (`<t:…:t>`) are labelled — the event *date* line, which
  uses `:F>`/`:f>`/`:R>` (never `:t>`), is correctly ignored, matching
  `event_post.py`'s definition of a "DJ slot".

PURE + TESTABLE
  No discord, no bot_state, no file I/O.  `bot.py`'s `cmd_djlineup` calls
  `label_slots()` and posts the result.  Headless testing is trivial:
  import this module, call the functions, assert on the strings
  (`python djlineup.py`).
"""
from __future__ import annotations

import re
from typing import List

__all__ = ["label_slots", "count_slots", "letter_for_index", "slot_code"]

# A time slot: a Discord *time* timestamp `<t:<unix>:t>`.  The value may be
# empty in a blank template (`<t::t>`), hence `\d*` before the `:t>` format
# code.  The event DATE uses `:F>`/`:f>`/`:R>`, never `:t>`, so it is
# correctly NOT matched here and never receives a slot letter.
_SLOT_RE = re.compile(r"<t:\d*:t>")

# 26 letters for the base alphabet.
_BASE = "abcdefghijklmnopqrstuvwxyz"


def _column_chars(i: int) -> List[str]:
    """Slot number `i` (0-based) as a list of base-26 letters.

    Uses the same column-coding as Excel spreadsheet columns:
    A, B, …, Z, AA, AB, …, AZ, BA, …, ZZ, AAA, …  (0 → ['a'], 25 → ['z'],
    26 → ['a', 'a'], 27 → ['a', 'b'], …).  No cap — grows without bound.
    """
    if i < 0:
        raise ValueError(f"slot index must be >= 0, got {i}")
    n = i + 1
    chars: List[str] = []
    while n > 0:
        n -= 1
        chars.append(_BASE[n % 26])
        n //= 26
    chars.reverse()
    return chars


def slot_code(i: int) -> str:
    """The compact letter code for slot `i` (0-based), e.g. 'a', 'b', …, 'z',
    'aa', 'ab', …, 'zz', 'aaa', ….  For plain-text display (logs) — no emote
    markup.  No cap.
    """
    return "".join(_column_chars(i))


def letter_for_index(i: int) -> str:
    """The regional-indicator emote token for slot number `i` (0-based).

    0 → `:regional_indicator_a:` (A)
    1 → `:regional_indicator_b:` (B)
    …
    25 → `:regional_indicator_z:` (Z)
    26 → `:regional_indicator_a::regional_indicator_a:` (AA)
    27 → `:regional_indicator_a::regional_indicator_b:` (AB)
    …
    51 → `:regional_indicator_a::regional_indicator_z:` (AZ)
    52 → `:regional_indicator_b::regional_indicator_a:` (BA)
    …

    Each letter is a separate regional-indicator emote; multi-letter codes
    render as adjacent emotes in Discord (e.g. AA → 🅰️🅰️).  No cap — the
    labels keep growing (AA, AB, …, ZZ, AAA, …) without bound.
    """
    return "".join(f":regional_indicator_{c}:" for c in _column_chars(i))


def count_slots(text: str) -> int:
    """How many time slots (`<t:…:t>`) are in `text`, regardless of layout."""
    return len(_SLOT_RE.findall(text or ""))


def label_slots(text: str) -> str:
    """Label every time slot in `text` with letter emotes, in order.

    Token-based (see the module docstring): each `<t:…:t>` becomes its own
    labelled line, `:regional_indicator_X: <token>`, in the order it appears.
    Works whether the paste arrived clean (one per line) or flattened onto a
    single line by Discord's text field.  Non-slot text (blanks, the
    event-date line, a DJ name) is not part of the lineup output.  Labels
    continue past Z (aa, ab, …, zz, aaa, …) without a cap.
    """
    tokens = _SLOT_RE.findall(text or "")
    out: List[str] = []
    for i, tok in enumerate(tokens):
        out.append(f"{letter_for_index(i)} {tok}")
    return "\n".join(out)


if __name__ == "__main__":
    # 1) The exact example from the request: 6 slots -> A–F.
    example = (
        "<t:1788667200:t>\n"
        "<t:1788670800:t>\n"
        "<t:1788674400:t>\n"
        "<t:1788678000:t>\n"
        "<t:1788681600:t>\n"
        "<t:1788685200:t>"
    )
    got = label_slots(example)
    want = (
        ":regional_indicator_a: <t:1788667200:t>\n"
        ":regional_indicator_b: <t:1788670800:t>\n"
        ":regional_indicator_c: <t:1788674400:t>\n"
        ":regional_indicator_d: <t:1788678000:t>\n"
        ":regional_indicator_e: <t:1788681600:t>\n"
        ":regional_indicator_f: <t:1788685200:t>"
    )
    assert got == want, f"example mismatch:\n--- got ---\n{got}\n--- want ---\n{want}"
    assert count_slots(example) == 6, count_slots(example)

    # 2) THE BUG (2026-09-09 miss-fire): all six slots FLATTENED onto one
    #    line by Discord's text field (newlines -> spaces).  Must STILL come
    #    out A–F, one per line.
    flattened = (" <t:1788667200:t> <t:1788670800:t> <t:1788674400:t> "
                 "<t:1788678000:t> <t:1788681600:t> <t:1788685200:t> ")
    assert label_slots(flattened) == want, \
        f"flattened input must still yield A–F:\n{label_slots(flattened)}"
    assert count_slots(flattened) == 6, count_slots(flattened)

    # 3) A realistic post: the event-date line (:F>/:R>) must NOT get a
    #    letter; only the three <t:…:t> slots are labelled, in order.
    post = (
        "## Event Date: <t:1780711200:F> (<t:1780711200:R>)\n"
        "<t:1780711200:t> **__Alpha__**\n"
        "\n"
        "<t:1780714800:t> **Beta**\n"
        "<t:1780718400:t> - **Gamma**"
    )
    got2 = label_slots(post)
    want2 = (
        ":regional_indicator_a: <t:1780711200:t>\n"
        ":regional_indicator_b: <t:1780714800:t>\n"
        ":regional_indicator_c: <t:1780718400:t>"
    )
    assert got2 == want2, f"post mismatch:\n--- got ---\n{got2}\n--- want ---\n{want2}"
    assert count_slots(post) == 3, count_slots(post)

    # 4) letter_for_index: single-letter a→z.
    assert letter_for_index(0) == ":regional_indicator_a:"
    assert letter_for_index(25) == ":regional_indicator_z:"

    # 5) letter_for_index: two-letter aa→az→ba (Excel-column style, no cap).
    assert letter_for_index(26) == \
        ":regional_indicator_a::regional_indicator_a:", letter_for_index(26)
    assert letter_for_index(27) == \
        ":regional_indicator_a::regional_indicator_b:", letter_for_index(27)
    assert letter_for_index(51) == \
        ":regional_indicator_a::regional_indicator_z:", letter_for_index(51)
    assert letter_for_index(52) == \
        ":regional_indicator_b::regional_indicator_a:", letter_for_index(52)

    # 6) letter_for_index: three-letter (past ZZ = 676; AAA is index 702).
    assert letter_for_index(702) == \
        ":regional_indicator_a::regional_indicator_a::regional_indicator_a:", \
        letter_for_index(702)

    # 7) Negative index raises ValueError.
    try:
        letter_for_index(-1)
        assert False, "negative index should raise ValueError"
    except ValueError:
        pass

    # 8) 27 slots: first 26 get a–z, the 27th gets AA (not bare).
    many = " ".join(f"<t:{1788667200 + i * 3600}:t>" for i in range(27))
    ml = label_slots(many).split("\n")
    assert ml[0] == ":regional_indicator_a: <t:1788667200:t>", ml[0]
    assert ml[25] == ":regional_indicator_z: <t:1788757200:t>", ml[25]
    assert ml[26] == (
        ":regional_indicator_a::regional_indicator_a: <t:1788760800:t>"
    ), ml[26]
    assert count_slots(many) == 27

    # 9) 30 slots: verify aa, ab, ac, ad are correct.
    many30 = " ".join(f"<t:{1788667200 + i * 3600}:t>" for i in range(30))
    ml30 = label_slots(many30).split("\n")
    assert ml30[26] == (
        ":regional_indicator_a::regional_indicator_a: <t:1788760800:t>"
    ), ml30[26]
    assert ml30[27] == (
        ":regional_indicator_a::regional_indicator_b: <t:1788764400:t>"
    ), ml30[27]
    assert ml30[28] == (
        ":regional_indicator_a::regional_indicator_c: <t:1788768000:t>"
    ), ml30[28]
    assert ml30[29] == (
        ":regional_indicator_a::regional_indicator_d: <t:1788771600:t>"
    ), ml30[29]

    # 10) Empty / no-slot input -> empty output, zero slots.
    assert label_slots("") == ""
    assert count_slots("") == 0
    assert count_slots("just some words, no slots") == 0
    assert label_slots("just some words, no slots") == ""

    print("OK: djlineup self-test passed — 6 slots -> A–F; flattened one-line "
          "still -> A–F; date line skipped; aa/ab/ac/… past Z; empty handled")
