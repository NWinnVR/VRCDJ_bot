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


def _allbtn_id(msg_id: str) -> str:
    return f"su:{msg_id}:allbtn"


def parse_allbtn(custom_id: str):
    """`su:{msg_id}:allbtn` -> msg_id, else None."""
    parts = custom_id.split(":")
    if len(parts) == 3 and parts[0] == "su" and parts[2] == "allbtn":
        return parts[1]
    return None


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
    """The ephemeral #1..#N grid (one button per slot, in order) + an **All** button.

    Clicking a slot toggles the CURRENT USER on that slot — limits enforced
    by signup_store.toggle_slot — then refreshes the public event post so
    everyone sees the change.  The click is ACKed immediately (defer +
    ephemeral "thinking…") so Discord never hits the 3 s timeout, and success
    is SILENT — the bubble is cleared and the name appearing on the event post
    IS the confirmation.  A reply is sent only on failure (slot full / per-
    person limit / data gone), because there's no other place for the user to
    learn about it.

    The **All** button TOGGLES the whole set: click it once and the user is
    added to every slot they're not already on (honouring per-slot / per-
    person limits); click it again and they're removed from every slot they're
    on.  It's silent on a clean all-add / all-remove, and replies once only
    when a limit blocked part of the action (so the user knows they didn't get
    everything they expected).
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
        allbtn = discord.ui.Button(
            style=discord.ButtonStyle.green,
            label="All",
            custom_id=_allbtn_id(msg_id),
        )
        allbtn.callback = self._make_all_callback()
        self.add_item(allbtn)

    def _make_callback(self, slot_index: int):
        async def cb(interaction: discord.Interaction):
            # ACK the click immediately so Discord doesn't hit the 3 s
            # "didn't respond in time" timeout.  Ephemeral → only this user
            # sees the (transient) "thinking…" bubble.
            await interaction.response.defer(ephemeral=True)
            rec = signup_store.get_event(self.msg_id)
            if rec is None:
                await interaction.followup.send(
                    "⚠️ This event's data is gone.  Ask a host to re-run `/signup`.",
                    ephemeral=True)
                return
            uid = interaction.user.id
            is_host = (int(interaction.user.id) == int(rec.get("host_id", -1)))
            result = signup_store.toggle_slot(self.msg_id, slot_index, uid,
                                              is_host=is_host)
            state = result.get("state")
            if state in ("added", "removed"):
                # Success: refresh the public post, then CLEAR the deferred
                # "thinking…" bubble — no reply (the name appearing on the
                # board IS the confirmation).
                await refresh_event_post(interaction, self.msg_id)
                try:
                    await interaction.delete_original_response()
                except discord.HTTPException:
                    pass  # bubble already gone — nothing to clear
                return
            # Failure: tell the user WHY (there's no other channel for it).
            if state in ("full_slot", "limit_person"):
                msg = f"🚫 {result.get('reason') or 'That slot is unavailable.'}"
            else:
                msg = f"⚠️ {result.get('reason') or 'Could not update.'}"
            await interaction.followup.send(msg, ephemeral=True)
        return cb

    def _make_all_callback(self):
        async def cb(interaction: discord.Interaction):
            # ACK the click immediately so Discord doesn't hit the 3 s
            # "didn't respond in time" timeout (the loop below can take
            # longer than that when it touches many slots).
            await interaction.response.defer(ephemeral=True)
            rec = signup_store.get_event(self.msg_id)
            if rec is None:
                await interaction.followup.send(
                    "⚠️ This event's data is gone.  Ask a host to re-run `/signup`.",
                    ephemeral=True)
                return
            uid = interaction.user.id
            is_host = (int(interaction.user.id) == int(rec.get("host_id", -1)))
            slots = [int(s) for s in rec.get("slot_timestamps", [])]
            asg = {str(k): [int(x) for x in v]
                   for k, v in (rec.get("assignments") or {}).items()}
            # Toggle: if the user is on at least ONE slot, remove them from
            # ALL; otherwise add them to ALL.  (Mirrors the individual
            # slot buttons — click All once to grab everything, click it
            # again to give it all back.)
            on_any = any(uid in asg.get(str(i), []) for i in range(len(slots)))
            if on_any:
                removed, skipped = [], []
                for i in range(len(slots)):
                    if uid not in asg.get(str(i), []):
                        continue
                    r = signup_store.toggle_slot(self.msg_id, i, uid,
                                                 is_host=is_host)
                    if r.get("state") == "removed":
                        removed.append(i + 1)
                    else:
                        skipped.append((i + 1, r.get("reason") or "unavailable"))
            else:
                added, skipped = [], []
                for i in range(len(slots)):
                    if uid in asg.get(str(i), []):
                        continue
                    r = signup_store.toggle_slot(self.msg_id, i, uid,
                                                 is_host=is_host)
                    if r.get("state") == "added":
                        added.append(i + 1)
                    else:
                        skipped.append((i + 1, r.get("reason") or "unavailable"))
            # Refresh the public post either way so the board reflects the
            # change (or the partial change).
            await refresh_event_post(interaction, self.msg_id)
            # Silent on success — the board already shows the result.
            # Reply ONLY when a limit blocked part of it, so the user knows
            # they didn't get everything they expected.
            if not skipped:
                try:
                    await interaction.delete_original_response()
                except discord.HTTPException:
                    pass  # bubble already gone — nothing to clear
                return
            if on_any:
                parts = [f"🚫 Couldn't remove **{len(skipped)}**: "
                         + ", ".join(f"**#{n}** ({r})" for n, r in skipped)]
            else:
                parts = []
                if added:
                    parts.append(f"✅ Added to **{len(added)} slot(s)**: "
                                 + ", ".join(f"**#{n}**" for n in added))
                parts.append(f"🚫 Skipped **{len(skipped)}**: "
                             + ", ".join(f"**#{n}** ({r})" for n, r in skipped))
            await interaction.followup.send(
                "\n".join(parts), ephemeral=True)
        return cb


# ---------------------------------------------------------------------------
# permission helpers
# ---------------------------------------------------------------------------
def _bot_channel_missing(channel) -> list:
    """Return the list of permission names the BOT is missing in `channel`
    that are needed to post an event message (View Channel + Send Messages).
    Returns [] when the bot has everything it needs.

    Uses channel.permissions_for(bot) which reflects the EFFECTIVE permissions
    in that channel, i.e. role perms + any channel-specific @everyone / role /
    member overrides.  This is what Discord actually enforces, and it is the
    reason 'I gave it every role perm' can still 403 — a red ✗ override on
    Send Messages in that channel wins.
    """
    try:
        bot = channel.guild.me
    except Exception:
        return []
    if bot is None:
        return []
    perms = channel.permissions_for(bot)
    missing = []
    if not perms.view_channel:
        missing.append("View Channel (read messages)")
    if not perms.send_messages:
        missing.append("Send Messages")
    return missing


def _describe_missing(missing: list) -> str:
    """Human-readable explanation of missing perms, pointing at the fix."""
    if not missing:
        return "I'm missing a permission in this channel."
    names = ", ".join(f"**{m}**" for m in missing)
    return (
        f"I can't post in this channel — I'm missing: {names}.\n\n"
        "You gave the *role* the post permissions, but this channel likely has "
        "a **channel-specific override** blocking me.  To fix:\n"
        "1. Right-click the channel → **Edit Channel** → **Permissions**\n"
        "2. Find `@everyone` and my role → set **Send Messages** (and "
        "**View Channel**) to **Allow** (green ✓), or remove any red ✗\n"
        "3. Save → try again.\n\n"
        "No admin needed — just the two green ✓ on this channel."
    )


def _decode_missing_perm(exc, channel) -> list:
    """Best-effort name of the missing permission(s) from a 403 Forbidden.

    Two sources, tried in order:
      1. Discord's 403 body has a `missing_permissions` bitmask field (decimal
         string, e.g. "2048" == Send Messages).  discord.py 2.7.1 does NOT
         surface it as an attribute, but it is in the raw parsed body, so we
         look for it on the exception's response/message.
      2. Fallback: compute what the bot is missing in THIS channel via
         permissions_for (reflects role + channel overrides).
    Returns a list of permission-name strings (possibly empty).
    """
    names = []
    mask = None
    # (1) try to pull the bitmask out of the exception / its body
    for src in (getattr(exc, "missing_permissions", None),
                getattr(exc, "response", None)):
        if isinstance(src, (int, str)) and str(src).isdigit():
            mask = int(src); break
        if isinstance(src, dict) and "missing_permissions" in src:
            mask = int(src.get("missing_permissions", 0)); break
    # some discord builds keep the parsed body as exc._body / exc.message
    for attr in ("_body", "message", "data"):
        body = getattr(exc, attr, None)
        if isinstance(body, dict) and "missing_permissions" in body:
            try: mask = int(body["missing_permissions"])
            except Exception: pass
            break
    if mask:
        try:
            p = discord.Permissions(mask)
            names = [n for n, on in list(p) if on]
        except Exception:
            names = []
    # (2) fallback: compute from the channel
    if not names and channel is not None:
        names = _bot_channel_missing(channel)
    return names


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

    # 1b) Fail fast on a channel where I can't post, with an ACTIONABLE
    #     message (instead of a cryptic 403 Forbidden later).  This is the
    #     common gotcha: the bot's ROLE has Send Messages, but this channel
    #     has a channel-specific override (red ✗) that denies it.
    missing = _bot_channel_missing(channel)
    if missing:
        botlog.log("signup_perm_missing", who=who, level="error",
                   guild=_guild_name(channel),
                   detail="missing in channel: " + "; ".join(missing))
        await interaction.response.send_message(
            _describe_missing(missing), ephemeral=True)
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
            if lost:
                # Only notify when something was actually LOST — that's
                # actionable.  A clean edit is visible on the post itself.
                await interaction.followup.send(
                    f"⚠️ Updated, but **{lost} filled slot(s) were lost** "
                    "because their time changed.",
                    ephemeral=True)
        else:
            msg_id2 = await post_new_event(
                channel, host_id, title, slots, slot_seconds, doors_ts,
                int(max_per_slot), int(max_per_person))
            botlog.log("signup_posted", who=who,
                       guild=_guild_name(channel),
                       detail=f"event msg {msg_id2} · {len(slots)} slot(s) · "
                             f"host {host_id}")
            # No confirmation message — the user can see the post exists.
    except discord.HTTPException as exc:
        # If it's a 403 Forbidden, decode WHICH permission is missing and
        # tell the user exactly what to do (instead of a bare "Forbidden").
        is_forbidden = (getattr(exc, "status", 0) == 403
                        or type(exc).__name__ == "Forbidden")
        if is_forbidden:
            missing = _decode_missing_perm(exc, channel)
            detail = f"{type(exc).__name__}: {exc}"
            if missing:
                detail += "  -> missing: " + "; ".join(missing)
            botlog.log("signup_http_error", who=who, level="error",
                       detail=detail[:400])
            try:
                if missing:
                    await interaction.followup.send(
                        _describe_missing(missing), ephemeral=True)
                else:
                    await interaction.followup.send(
                        "⚠️ I can't post in this channel — it looks like I'm "
                        "missing **Send Messages** (or **View Channel**).  "
                        "Check this channel's **Permissions** tab and make "
                        "sure my role (or @everyone) has both set to **Allow** "
                        "(green ✓).", ephemeral=True)
            except Exception:
                pass
            return
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
    check("slot view 6 buttons (5 slots + All)",
          len(spv.children) == 6, len(spv.children))
    check("slot labels (incl. All)",
          [c.label for c in spv.children] == ["#1", "#2", "#3", "#4", "#5", "All"],
          [c.label for c in spv.children])
    check("slot ids (incl. allbtn)",
          [c.custom_id for c in spv.children] ==
          [f"su:123:slot:{i}" for i in range(5)] + ["su:123:allbtn"],
          [c.custom_id for c in spv.children])
    check("allbtn decodes", parse_allbtn("su:123:allbtn") == "123",
          parse_allbtn("su:123:allbtn"))
    check("allbtn rejects slot", parse_allbtn("su:123:slot:0") is None)

    print("=== 4. many-slot grid auto-rows (max 5/row) ===")
    big = SlotPickView("123", 12)
    check("13 buttons (12 slots + All)", len(big.children) == 13, len(big.children))

    if failures:
        print(f"\n{failures} CHECK(S) FAILED")
        return 1
    print("\nOK: signup_ui self-test passed — custom_ids parse + views build")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
