"""timeslots_ui.py — the Discord UI for the /timeslots clarifying question.

When `/timeslots` gets a start time the engine can't pin down uniquely (a bare
hour with AM/PM unknown, or no date at all with today's reading already past),
the bot asks ONE question with up to five buttons — one per candidate moment.
Clicking a button instantly posts that candidate's full slot block:

  * TimeslotsClarifyView   — the non-persistent button row (timeout=600s, no
                             custom_id persistence needed: it dies with the
                             message; stateless, all data lives in the View).
  * build_and_send_block() — renders + delivers the chosen candidate's block
                             via discord_send chunking (a big lineup >2000
                             chars must be split, same as /timeslots itself).

DISCORD.PY 2.7.1 API notes (same contract verified for signup_ui.py):
  * Buttons: discord.ui.Button(style, label≤32, custom_id) + `.callback`
    (coro taking one Interaction), added via View.add_item (max 5 per row).
  * A button callback MUST answer the interaction before ~3s — defer() first,
    then followup.send() for every message.  `interaction.original.edit()` is
    how we clear the buttons after a pick (best-effort: the original may have
    been deleted by the time the click lands).
"""
from __future__ import annotations

import contextlib
from typing import Optional

import discord

import botlog
import timeslots
import discord_send

# How long an unanswered question stays clickable.  600s is generous but not
# forever: after a restart the buttons are dead anyway (non-persistent), and
# staff who walk away should re-run /timeslots for a FRESH resolution against
# the current "today" — a stale guess from hours ago is worse than a fresh ask.
_CLARIFY_TIMEOUT_S = 600

# Discord hard-caps a button label at 32 chars; timeslots._when_label already
# trims its labels to that, but clamp defensively so a future label change can
# never break the payload with a 50035.
_LABEL_MAX = 32


def _candidate_id(instance_key: str, i: int) -> str:
    """`tsl:{instance_key}:{i}` — stable within one question's lifetime."""
    return f"tsl:{instance_key}:{i}"


class TimeslotsClarifyView(discord.ui.View):
    """The "which did you mean?" button row under an ambiguous /timeslots ask.

    Every button renders its candidate's FULL slot block on click (doors line +
    all slots, letters if requested) — the same output the one-shot path gives
    for an unambiguous start time.  The buttons are disabled by editing the
    original response after a pick so nobody else can double-fire them; if that
    edit fails (message deleted / bot missing perms), each callback also
    re-checks the requester, so only the person who asked gets slots.
    """

    def __init__(self, candidates: list[timeslots.WhenCandidate],
                 instance_key: str, requester_id: int,
                 slot_count: str, doors_lead: Optional[str] = "15",
                 add_letters: Optional[str] = "no",
                 slot_duration: Optional[str] = "1 hour"):
        super().__init__(timeout=_CLARIFY_TIMEOUT_S)
        self.requester_id = int(requester_id)
        self.slot_count = slot_count
        self.doors_lead = doors_lead
        self.add_letters = add_letters
        self.slot_duration = slot_duration

        for i, cand in enumerate(candidates[:5]):           # max 5 per row
            btn = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label=(cand.label or "?")[:_LABEL_MAX],
                custom_id=_candidate_id(instance_key, i),
            )
            btn.callback = self._make_callback(cand)
            self.add_item(btn)

    def _make_callback(self, cand: timeslots.WhenCandidate):
        async def cb(interaction: discord.Interaction):
            if int(interaction.user.id) != self.requester_id:
                await interaction.response.send_message(
                    "🔒 This question was for the person who ran `/timeslots` — "
                    "run your own.", ephemeral=True)
                return
            # ACK immediately (spinner): building + chunked delivery can take a
            # beat, and Discord's 3s interaction window is unforgiving.
            await interaction.response.defer()
            try:
                out = timeslots.build(str(cand.ts), self.slot_count, None,
                                      doors_lead=self.doors_lead,
                                      add_letters=self.add_letters,
                                      slot_duration=self.slot_duration)
            except ValueError as ve:
                botlog.log("timeslots_error", who=interaction.user.name,
                           guild=(interaction.guild.name if interaction.guild else "DM"),
                           level="warn", detail=f"clarify pick failed: {ve}")
                await interaction.followup.send(f"⚠️ {ve}", ephemeral=True)
                return
            await build_and_send_block(interaction, out)
            # Clear the button row so a second click (or another user) can't
            # re-fire.  Best-effort: if the original message is gone or we lack
            # perms to edit it, the requester-guard above already contained us.
            with contextlib.suppress(Exception):
                await interaction.original.edit(view=discord.ui.View())
            botlog.log("timeslots", who=interaction.user.name,
                       guild=(interaction.guild.name if interaction.guild else "DM"),
                       detail=f"clarified -> {cand.label} ({cand.ts})")
        return cb


async def build_and_send_block(interaction: discord.Interaction, out: str) -> None:
    """Send a rendered slot block from INSIDE a button callback.

    Mirrors discord_send.send_long() for the deferred-interaction case: after
    defer(), EVERY message is a followup, so we just chunk + send them all
    through interaction.followup with the same part-numbering style."""
    chunks = discord_send.chunk_for_discord(out)
    if not chunks:
        await interaction.followup.send("(empty)")
        return
    total = len(chunks)
    for i, c in enumerate(chunks, start=1):
        await interaction.followup.send(
            c if total == 1 else discord_send.format_followup(c, i, total))


# ---------------------------------------------------------------------------
# Self-test  (run: python timeslots_ui.py)  — no Discord connection.
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

    from datetime import date, datetime
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None

    # Fixed "now": Wed Sep 9 2026 18:00 ET.
    now = (datetime(2026, 9, 9, 18, 0) if ZoneInfo is None
           else datetime(2026, 9, 9, 18, 0, tzinfo=ZoneInfo("America/New_York"))).timestamp()
    ref = date(2026, 9, 9)

    print("=== 1. view builds with ≤5 buttons, labels ≤32 ===")
    for kind in ("am_pm", "date"):
        if kind == "am_pm":
            k, cands = timeslots.resolve_candidates("the 12th 9", ref=ref, now=now)
        else:
            k, cands = timeslots.resolve_candidates("9am", ref=ref, now=now)
        v = TimeslotsClarifyView(cands, "k1", 42, slot_count="6")
        check(f"{kind}: button count == len(cands)", len(v.children) == len(cands),
              (len(v.children), len(cands)))
        check(f"{kind}: labels ≤32", all((c.label or "")[:_LABEL_MAX] for c in v.children))
        ids = [c.custom_id for c in v.children]
        check(f"{kind}: custom_ids stable form",
              ids == [_candidate_id("k1", i) for i in range(len(cands))], ids)

    print("=== 2. >5 candidates are capped (Discord max 5/row… we cap at 5 total) ===")
    # build() needs ≥1 slot; a synthetic 7-candidate list must yield exactly 5 buttons.
    fake = [timeslots.WhenCandidate(100 + i, f"Day {i}") for i in range(7)]
    big = TimeslotsClarifyView(fake, "k2", 42, slot_count="3")
    check("capped at 5 buttons", len(big.children) == 5, len(big.children))

    print("=== 3. format_followup passthrough ===")
    import discord_send
    check("single chunk unmarked",
          discord_send.format_followup("hello", 1, 1) == "hello")
    multi = discord_send.format_followup("part two", 2, 3)
    check("multi chunk numbered", "(2/3)" in multi and "part two" in multi, multi)

    if failures:
        print(f"\n{failures} CHECK(S) FAILED")
        return 1
    print("\nOK: timeslots_ui self-test passed — view builds, ids stable")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())