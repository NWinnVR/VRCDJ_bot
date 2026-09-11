"""signup_ui.py — the Discord UI for `/signup` (modals + button views).

This is the thin discord.py layer over the pure engine (signup.py) and the
persistent state (signup_store.py).  It defines:

  * SignupModal   — the "Event Creation" modal (name + slots/info block), used
                    for BOTH a new event (create mode) and "Edit Event"
                    (edit mode, prefilled with the current event + a msg_id).
  * EventView     — the button row on the event post: [Pick Slot] [Edit Event].
  * SlotPickView  — the EPHEMERAL "you-only" grid of #1..#N slot buttons.
  * handle_submit — runs when the host submits the modal (create or edit).
  * helpers       — post_new_event / apply_event_edit / refresh_event_post.

DISCORD.PY 2.7.1 API — verified against this venv (see the self-test):
  * Modals: subclass discord.ui.Modal (title=...); fields are class-level
    discord.ui.TextInput(...).  Prefill is set with the `default=` kwarg
    (there is NO before_open() in 2.7.1).  The submit handler is
    `async def on_submit(self, interaction)` on the subclass — there is NO
    `CommandTree.on_modal_submit` in 2.7.1.  `send_modal()` is on the
    InteractionResponse (ctx.response.send_modal / interaction.response).
    The submitted instance is round-tripped by its custom_id, so any attribute
    you set in __init__ (like the edit msg_id) is available in on_submit.
  * Buttons: discord.ui.Button(style, label, custom_id) + `.callback = coro`
    (coro takes one Interaction arg), added with View.add_item (auto-rows,
    max 5/row, 25/view).  A View is persistent iff timeout=None AND every
    button has an explicit custom_id.
  * interaction.user / .channel / .guild / .response / .followup all exist.
  * A modal submit CANNOT send_message first — defer(), then followup.send().

CUSTOM-ID SCHEME (stable, stateless, parseable without a DB lookup)
  Every button carries the event's Discord message id:
      su:{msg_id}:pickbtn   -> "Pick Slot" on event msg_id
      su:{msg_id}:editbtn   -> "Edit Event" on event msg_id
      su:{msg_id}:slot:{i}  -> slot #i (0-based) in the pick grid
"""
from __future__ import annotations

import discord
import botlog
import signup
import signup_store

# Cap for one ephemeral pick grid (a View holds max 25 buttons; stay under it
# and under the signup engine's MAX_SLOTS so the two never disagree).
_MAX_PICK_BUTTONS = 25


# ---------------------------------------------------------------------------
# custom_id helpers (pure string work — no state needed)
# ---------------------------------------------------------------------------
def _pickbtn_id(msg_id: str) -> str:
    return f"su:{msg_id}:pickbtn"


def _editbtn_id(msg_id: str) -> str:
    return f"su:{msg_id}:editbtn"


def _slot_id(msg_id: str, i: int) -> str:
    return f"su:{msg_id}:slot:{i}"


def parse_pickbtn(custom_id: str):
    """`su:{msg_id}:pickbtn` -> msg_id, else None."""
    parts = custom_id.split(":")
    if len(parts) == 3 and parts[0] == "su" and parts[2] == "pickbtn":
        return parts[1]
    return None


def parse_editbtn(custom_id: str):
    """`su:{msg_id}:editbtn` -> msg_id, else None."""
    parts = custom_id.split(":")
    if len(parts) == 3 and parts[0] == "su" and parts[2] == "editbtn":
        return parts[1]
    return None


def parse_slot(custom_id: str):
    """`su:{msg_id}:slot:{i}` -> (msg_id, i), else None."""
    parts = custom_id.split(":")
    if len(parts) == 4 and parts[0] == "su" and parts[2] == "slot":
        try:
            return parts[1], int(parts[3])
        except ValueError:
            return None
    return None


def _asg_int(rec: dict) -> dict:
    """{str: [ids]} (store) -> {int: [ids]} for signup.render_event."""
    out = {}
    for k, v in (rec.get("assignments") or {}).items():
        try:
            out[int(k)] = [int(x) for x in v]
        except (TypeError, ValueError):
            continue
    return out


def _guild_name(channel) -> str:
    g = getattr(channel, "guild", None)
    return (getattr(g, "name", "") or "DM") if g else "DM"


# ---------------------------------------------------------------------------
# the modal  (one class for create AND edit — edit just prefills + carries id)
# ---------------------------------------------------------------------------
class SignupModal(discord.ui.Modal, title="Event Creation"):
    """Host-facing "Event Creation" modal.

    Two fields:
      * Event name — the `# title` heading.
      * Slots & info — the full block (slots + doors + duration + instruction).

    CREATE mode: `msg_id` is None; the host edits the prefilled template, hits
    Submit -> a new event post is created.

    EDIT mode: `msg_id` is the existing event's id and the fields are prefilled
    with that event's CURRENT text (names included, so the host sees what's
    filled).  Submit -> that SAME post is edited in place and filled slots are
    preserved by timestamp (see signup_store.edit_event).
    """

    def __init__(self, prefill_name: str = "", prefill_block: str = "",
                 msg_id: str | None = None, custom_id: str | None = None,
                 max_per_slot: int = signup.DEFAULT_PER_SLOT,
                 max_per_person: int = signup.DEFAULT_PER_PERSON):
        # NOTE: only pass custom_id if we actually have one. Passing None
        # overrides discord.py's auto-generated id (it does `self.custom_id =
        # custom_id` verbatim), so Discord receives an empty custom_id and
        # rejects the modal with 50035 "custom_id: This field is required".
        super().__init__(custom_id=custom_id if custom_id is not None else "su_modal")
        self.msg_id = msg_id  # None = create ; str = edit this event
        self.max_per_slot = int(max_per_slot)
        self.max_per_person = int(max_per_person)

        self.name_field = discord.ui.TextInput(
            label="Event name",
            custom_id="su_name",
            placeholder="e.g. Friday Night Gogo",
            default=(prefill_name or "")[:signup.MAX_TITLE_LEN],
            required=False,
            max_length=signup.MAX_TITLE_LEN,
        )
        self.block_field = discord.ui.TextInput(
            label="Slots & info",
            custom_id="su_block",
            style=discord.TextStyle.paragraph,
            # Discord caps placeholder at 100 chars — keep a compact hint, NOT
            # the full template (the prefilled `default` holds the real text).
            placeholder=("> #1, <t:...:t>  ...  Doors: **__<t:...:F>__**"),
default=(prefill_block or "")[:signup.MAX_TEXT_LEN],
            required=True,
            max_length=signup.MAX_TEXT_LEN,
        )
        # add to the view so they're part of the payload (required for modals)
        self.add_item(self.name_field)
        self.add_item(self.block_field)

    async def on_submit(self, interaction: discord.Interaction):
        name = (self.name_field.value or "").strip() or "New Event"
        block = (self.block_field.value or "").strip()
        await handle_submit(interaction, name, block, self.msg_id,
                            self.max_per_slot, self.max_per_person)


# ---------------------------------------------------------------------------
# button views
# ---------------------------------------------------------------------------
class EventView(discord.ui.View):
    """The persistent button row on the event post: [Pick Slot] [Edit Event].

    `timeout=None` + explicit custom_ids makes it persistent (survives a bot
    restart, registered in bot.on_ready).  Both buttons re-read the store on
    click, so they always reflect the latest state.
    """

    def __init__(self, msg_id: str):
        super().__init__(timeout=None)
        self.msg_id = msg_id

        pick = discord.ui.Button(
            style=discord.ButtonStyle.green,
            label="Pick Slot",
            custom_id=_pickbtn_id(msg_id),
        )
        pick.callback = self._on_pick
        self.add_item(pick)

        edit = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Edit Event",
            custom_id=_editbtn_id(msg_id),
        )
        edit.callback = self._on_edit
        self.add_item(edit)

    async def _on_pick(self, interaction: discord.Interaction):
        rec = signup_store.get_event(self.msg_id)
        if rec is None:
            await interaction.response.send_message(
                "⚠️ This event's data is gone (post deleted or bot data cleared). "
                "Ask a host to re-run `/signup`.",
                ephemeral=True)
            return
        slots = [int(s) for s in rec.get("slot_timestamps", [])]
        if not slots:
            await interaction.response.send_message(
                "⚠️ This event has no slots to sign up for.", ephemeral=True)
            return
        if len(slots) > _MAX_PICK_BUTTONS:
            await interaction.response.send_message(
                f"⚠️ This event has **{len(slots)} slots** — too many for one "
                f"pick grid (max {_MAX_PICK_BUTTONS}).  Ask a host to split it.",
                ephemeral=True)
            return
        view = SlotPickView(self.msg_id, len(slots))
        # Ephemeral prompt: only this user sees it.
        await interaction.response.send_message(
            f"**Pick your slot(s)** — {len(slots)} on this event.\n"
            "Click a slot to sign up · click it again to cancel.",
            view=view, ephemeral=True)

    async def _on_edit(self, interaction: discord.Interaction):
        rec = signup_store.get_event(self.msg_id)
        if rec is None:
            await interaction.response.send_message(
                "⚠️ This event's data is gone.  Ask a host to re-run `/signup`.",
                ephemeral=True)
            return
        # Only the host (or someone with admin/manager) can edit.
        is_host = (int(interaction.user.id) == int(rec.get("host_id", -1)))
        if not is_host:
            perms = interaction.user.guild_permissions if interaction.guild else None
            if not (perms and (perms.manage_guild or perms.administrator)):
                await interaction.response.send_message(
                    "🔒 Only the event host (or an admin) can edit this event.",
                    ephemeral=True)
                return
        slots = [int(s) for s in rec.get("slot_timestamps", [])]
        prefill_block = signup.render_event(
            rec.get("title", "Event"), slots,
            int(rec.get("slot_seconds", 3600)), rec.get("doors_ts"),
            _asg_int(rec))
        await interaction.response.send_modal(
            SignupModal(prefill_name=rec.get("title", "Event"),
                        prefill_block=prefill_block,
                        msg_id=self.msg_id,
                        max_per_slot=rec.get("max_per_slot", signup.DEFAULT_PER_SLOT),
                        max_per_person=rec.get("max_per_person", signup.DEFAULT_PER_PERSON)))


class SlotPickView(discord.ui.View):
    """The ephemeral #1..#N grid (one button per slot, in order).

    Clicking a button toggles the CURRENT USER on that slot — limits enforced
    by signup_store.toggle_slot — then refreshes the public event post so
    everyone sees the change, and confirms to the user (ephemeral).
    """

    def __init__(self, msg_id: str, slot_count: int):
        super().__init__(timeout=None)
        self.msg_id = msg_id
        for i in range(min(slot_count, _MAX_PICK_BUTTONS)):
            btn = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label=f"#{i + 1}",
                custom_id=_slot_id(msg_id, i),
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, slot_index: int):
        async def cb(interaction: discord.Interaction):
            rec = signup_store.get_event(self.msg_id)
            if rec is None:
                await interaction.response.send_message(
                    "⚠️ This event's data is gone.  Ask a host to re-run `/signup`.",
                    ephemeral=True)
                return
            uid = interaction.user.id
            is_host = (int(interaction.user.id) == int(rec.get("host_id", -1)))
            result = signup_store.toggle_slot(self.msg_id, slot_index, uid,
                                              is_host=is_host)
            state = result.get("state")
            await refresh_event_post(interaction, self.msg_id)
            if state == "added":
                msg = f"✅ You're signed up for **slot #{slot_index + 1}**."
            elif state == "removed":
                msg = (f"✋ Cancelled — you're no longer on "
                       f"**slot #{slot_index + 1}**.")
            elif state in ("full_slot", "limit_person"):
                msg = f"🚫 {result.get('reason') or 'That slot is unavailable.'}"
            else:
                msg = f"⚠️ {result.get('reason') or 'Could not update.'}"
            await interaction.response.send_message(msg, ephemeral=True)
        return cb


# ---------------------------------------------------------------------------
# message helpers
# ---------------------------------------------------------------------------
async def post_new_event(channel, host_id: int, title: str, slots: list,
                         slot_seconds: int, doors_ts, max_per_slot: int,
                         max_per_person: int) -> int:
    """Create the public event post with [Pick Slot]+[Edit Event], register it
    in the store, and return the message id."""
    text = signup.render_event(title, [int(s) for s in slots],
                               int(slot_seconds), doors_ts, {})
    view0 = EventView("0")
    msg = await channel.send(content=text, view=view0)
    view = EventView(str(msg.id))
    await msg.edit(content=text, view=view)
    guild_id = str(getattr(getattr(channel, "guild", None), "id", "0"))
    signup_store.create_event(
        str(msg.id), guild_id, str(channel.id), title,
        [int(s) for s in slots], int(slot_seconds),
        int(doors_ts) if doors_ts else None,
        int(max_per_slot), int(max_per_person), int(host_id))
    # NOTE: `channel.send()` / `msg.edit()` already register the persistent
    # view keyed by msg.id for THIS session (discord.py does it internally).
    # Surviving a bot RESTART is handled in bot.on_ready, which re-adds every
    # stored event's view.  No manual add_view needed here.
    return int(msg.id)


async def refresh_event_post(interaction: discord.Interaction, msg_id: str):
    """Re-render + edit the event message to reflect current assignments.
    Best-effort — a failure here never blocks the user's confirmation."""
    rec = signup_store.get_event(msg_id)
    if rec is None:
        return
    channel = interaction.channel
    try:
        m = await channel.fetch_message(int(msg_id))
    except (discord.HTTPException, ValueError):
        return
    slots = [int(s) for s in rec.get("slot_timestamps", [])]
    text = signup.render_event(
        rec.get("title", "Event"), slots,
        int(rec.get("slot_seconds", 3600)), rec.get("doors_ts"),
        _asg_int(rec))
    view = EventView(msg_id)
    try:
        # `Message.edit` re-registers the view keyed by msg.id automatically.
        await m.edit(content=text, view=view)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# modal submit (create OR edit) — called from SignupModal.on_submit
# ---------------------------------------------------------------------------
async def handle_submit(interaction: discord.Interaction, name: str,
                        block: str, msg_id: str | None,
                        max_per_slot: int = signup.DEFAULT_PER_SLOT,
                        max_per_person: int = signup.DEFAULT_PER_PERSON):
    channel = interaction.channel
    who = interaction.user.name

    # 1) parse the block back into structured data.
    try:
        if not block.strip():
            await interaction.response.send_message(
                "⚠️ The **Slots & info** block is required — give me the "
                "slots to sign up for.", ephemeral=True)
            return
        parsed = signup.parse_event_text(block)
        slots = parsed["slots"]
        if not slots:
            await interaction.response.send_message(
                "⚠️ I couldn't find any slots in the block — each slot should "
                "look like `> #1, <t:1788670800:t>`.", ephemeral=True)
            return
        if len(slots) > signup.MAX_SLOTS_EDIT:
            await interaction.response.send_message(
                f"⚠️ That's **{len(slots)} slots** — the cap is "
                f"{signup.MAX_SLOTS_EDIT}.  Trim the list.", ephemeral=True)
            return
        slot_seconds = parsed["slot_seconds"] or 3600
        doors_ts = parsed["doors_ts"]
        # use the modal's name field as the heading
        title = name
    except Exception as exc:
        import signup as _s  # noqa
        botlog.log("signup_submit_error", who=who, level="error",
                   detail=f"{type(exc).__name__}: {exc}")
        await interaction.response.send_message(
            f"⚠️ Something went wrong reading that: {type(exc).__name__}.",
            ephemeral=True)
        return

    host_id = interaction.user.id

    # 2) EDIT an existing event (preserve filled slots) or CREATE a new one.
    await interaction.response.defer(ephemeral=True)
    try:
        if msg_id:
            rec = signup_store.get_event(msg_id)
            if rec is None:
                await interaction.followup.send(
                    "⚠️ That event's data is gone — I can't edit it. "
                    "Please re-run `/signup` to make a fresh one.",
                    ephemeral=True)
                return
            result = await _apply_edit(
                channel, msg_id, title, slots, slot_seconds, doors_ts,
                int(rec.get("max_per_slot", signup.DEFAULT_PER_SLOT)),
                int(rec.get("max_per_person", signup.DEFAULT_PER_PERSON)))
            botlog.log("signup_edited", who=who,
                       guild=_guild_name(channel),
                       detail=(f"event {msg_id} · {len(slots)} slot(s) · "
                               f"preserved={result.get('preserved')} "
                               f"lost={result.get('lost')}"))
            lost = result.get("lost", 0)
            kept = result.get("preserved", 0)
            extra = (f"  **{lost} filled slot(s) were lost** because their "
                     "time changed." if lost else "")
            await interaction.followup.send(
                f"✅ Event updated in place — **{len(slots)} slot(s)** · "
                f"{kept} filled slot(s) kept.{extra}",
                ephemeral=True)
        else:
            msg_id2 = await post_new_event(
                channel, host_id, title, slots, slot_seconds, doors_ts,
                int(max_per_slot), int(max_per_person))
            botlog.log("signup_posted", who=who,
                       guild=_guild_name(channel),
                       detail=f"event msg {msg_id2} · {len(slots)} slot(s) · "
                             f"host {host_id}")
            await interaction.followup.send(
                f"✅ Event posted — **{len(slots)} slot(s)**.  Click **Pick "
                "Slot** to grab one, or **Edit Event** to tweak the times "
                "(filled slots are preserved).",
                ephemeral=True)
    except discord.HTTPException as exc:
        botlog.log("signup_http_error", who=who, level="error",
                   detail=f"{type(exc).__name__}: {exc}")
        try:
            await interaction.followup.send(
                f"⚠️ I couldn't update the event: {type(exc).__name__}.",
                ephemeral=True)
        except Exception:
            pass


async def _apply_edit(channel, msg_id: str, title: str, slots: list,
                      slot_seconds: int, doors_ts, max_per_slot: int,
                      max_per_person: int) -> dict:
    """Update an existing event: edit the store (preserving filled slots by
    timestamp) and re-render the SAME message in place."""
    result = signup_store.edit_event(
        msg_id, title, slots, slot_seconds, doors_ts,
        max_per_slot, max_per_person)
    rec = signup_store.get_event(msg_id)
    if rec is None:
        return result
    slots_int = [int(s) for s in rec.get("slot_timestamps", [])]
    text = signup.render_event(
        rec.get("title", "Event"), slots_int,
        int(rec.get("slot_seconds", 3600)), rec.get("doors_ts"),
        _asg_int(rec))
    view = EventView(msg_id)
    m = await channel.fetch_message(int(msg_id))
    await m.edit(content=text, view=view)
    return result


# ---------------------------------------------------------------------------
# Self-test  (run: python signup_ui.py)  — no Discord connection.
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

    print("=== 1. custom_id parsing ===")
    check("pickbtn", parse_pickbtn("su:123:pickbtn") == "123")
    check("editbtn", parse_editbtn("su:456:editbtn") == "456")
    check("slot", parse_slot("su:789:slot:4") == ("789", 4))
    check("slot rejects pickbtn", parse_slot("su:789:pickbtn") is None)
    check("pickbtn rejects slot", parse_pickbtn("su:123:slot:0") is None)

    print("=== 2. modals instantiate (create + edit) ===")
    m = SignupModal(prefill_name="My Event", prefill_block="> #1, <t:123:t>")
    check("create msg_id None", m.msg_id is None)
    check("name prefill default",
          m.name_field.default == "My Event", m.name_field.default)
    check("block prefill default",
          m.block_field.default == "> #1, <t:123:t>", m.block_field.default)
    em = SignupModal(prefill_name="E", prefill_block="> #1, <t:1:t>",
                     msg_id="999")
    check("edit msg_id set", em.msg_id == "999")
    check("modal has on_submit", hasattr(m, "on_submit"))
    check("modal to_components ok", len(m.to_components()) == 2,
          len(m.to_components()))

    print("=== 3. views instantiate + button labels/ids ===")
    v = EventView("123")
    check("event view 2 buttons", len(v.children) == 2, len(v.children))
    check("event labels",
          [c.label for c in v.children] == ["Pick Slot", "Edit Event"],
          [c.label for c in v.children])
    check("event ids",
          [c.custom_id for c in v.children] ==
          ["su:123:pickbtn", "su:123:editbtn"],
          [c.custom_id for c in v.children])
    check("event view persistent", v.is_persistent())
    spv = SlotPickView("123", 5)
    check("slot view 5 buttons", len(spv.children) == 5, len(spv.children))
    check("slot labels",
          [c.label for c in spv.children] == ["#1", "#2", "#3", "#4", "#5"],
          [c.label for c in spv.children])
    check("slot ids",
          [c.custom_id for c in spv.children] ==
          [f"su:123:slot:{i}" for i in range(5)],
          [c.custom_id for c in spv.children])

    print("=== 4. many-slot grid auto-rows (max 5/row) ===")
    big = SlotPickView("123", 12)
    check("12 slot buttons", len(big.children) == 12, len(big.children))

    if failures:
        print(f"\n{failures} CHECK(S) FAILED")
        return 1
    print("\nOK: signup_ui self-test passed — custom_ids parse + views build")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
