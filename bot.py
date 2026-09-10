#!/usr/bin/env python
"""bot.py — VRCDJ_bot: a public, portable VRChat DJ-lookup Discord bot.

WHAT IT DOES (DJ + time-slot tools only — NO LLM, NO model, NO prompting)
  - /dj <name>      → instant link lookup from a public Google-Sheet DJ list.
                      Fuzzy "did you mean?" catches near-miss names. Each link
                      line is  ## Label: ```url``` — the ## heading makes the
                      label larger and the triple backticks wrap ONLY the url
                      as a one-click copy target (copying in a VR headset is
                      the whole point). Comma/semicolon-separated for several.
  - /vrcdn <url>    → paste ONE VRCDN link (RTSP or MPEG-TS) — or just the
                      streamer name — and get all three back: RTSP, MPEG-TS,
                      and host preview.
  - /djlineup <times> → paste an event's ham-time slots and they're labelled
                      A–Z (then AA, AB, …) so DJs sign up for "slot B" instead
                      of a time-zone.
  - /timeslots <start> <count> → generate the whole block of `<t:…:t>` DJ
                      slots (DST-aware timezone math).
  - /status         → on/off + DJ list freshness + version.
  - /help           → the command reference.
  - /on · /off · /dj-refresh → controls (who may run them is set server-side
    in Discord's per-command permissions, NOT in this code).
  - EVENT AUTO-LINEUP → paste a full event post (3+ time-slot lines each with
    a DJ name) and it's answered with every DJ's links — @mention me to
    trigger it in any channel, or drop it in a configured "lineup channel".

WHAT THIS IS NOT (deliberately stripped vs. the private WyBot)
  - No LLM / no local model / no prompting / no @-mention Q&A.
  - No host list, no /worldopen, no /draftsocials, no conversation threads,
    no schedule window, no model picker.
  - Runs on a plain headless Windows box. No LM Studio, no API keys.

SECURITY MODEL
  - Outbound connections only: Discord gateway + the public Google Sheet
    (https). No inbound ports, no local web server, no shell, no file writes
    outside this project folder.
  - The Discord token lives ONLY in bot.env (git-ignored). Never logged, never
    posted. Every string that reaches Discord is passed through scrub.scrub()
    so no ANSI, path, or control byte ever leaks out.
  - No command in this bot can be coerced into executing code, reading files,
    or changing config — the only external inputs (DJ names, URLs, time-slot
    text) are treated as opaque strings and validated by their parsers.

RUN:  python bot.py          (or double-click run_bot.bat)
"""
import asyncio
import contextlib
import logging
import os
import re
import sys
import time
from typing import Optional

import discord

# ----------------------------- logging ---------------------------------------
# Surface discord.py's own logging on stdout so an exception inside on_ready
# (e.g. a command-sync 400 / 50035) is visible in bot_stdout.log instead of a
# silent "Ignoring exception in on_ready".
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logging.getLogger("discord").setLevel(logging.INFO)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.INFO)
logging.getLogger("discord.http").setLevel(logging.WARNING)

# ----------------------------- paths / token ---------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import envload          # noqa: E402  (tiny dependency-free .env loader)

# Load bot.env (git-ignored) into the environment. Real env vars win —
# load_dotenv() never overrides variables already set.
envload.load_dotenv(os.path.join(BASE, "bot.env"))
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not TOKEN:
    sys.exit(
        "DISCORD_BOT_TOKEN missing — put it in bot.env (see bot.env.example).\n"
        "  Get it from: Discord Developer Portal → your app → Bot → Token."
    )

# ----------------------------- local modules ---------------------------------
import scrub          # noqa: E402  (output sanitizer — no LLM)
import bot_state      # noqa: E402
import bot_config     # noqa: E402
import botlog         # noqa: E402
import repo_version   # noqa: E402  (commit stamp for the dashboard)
import version        # noqa: E402  (the version number, single source of truth)
from dj_sheet import (                                # noqa: E402
    load_dj_list, count_djs,
    refresh_dj_list, get_dj_freshness, is_dj_stale,
    list_djs, find_djs,
)
import event_post     # noqa: E402  (event-post DJ lineup, no LLM)
import vrcdn          # noqa: E402  (/vrcdn — expand one VRCDN URL to all three)
import djlineup       # noqa: E402  (/djlineup — A–Z letters on time-slot lines)
import timeslots      # noqa: E402  (/timeslots — generate the <t:…:t> DJ-slot block)
import discord_send   # noqa: E402  (2000-char-safe sends)


# ----------------------------- config ----------------------------------------
def _cfg() -> dict:
    return bot_config.load_config()


def _event_lineup_channels() -> set[str]:
    """Channel IDs where the event auto-lineup fires WITHOUT an @mention.
    Configurable in bot_config.json ("event_lineup_channels": ["<id>", ...]).
    Empty by default → the @mention path is the primary trigger."""
    chans = _cfg().get("event_lineup_channels") or []
    return {str(c) for c in chans}


# ----------------------------- output sanitizer ------------------------------
# SECURITY: nothing that reaches Discord may carry ANSI escapes, control
# bytes, or internal filesystem paths. All external input in this bot is
# opaque user text (names, URLs, time slots), so this is the single choke
# point that guarantees a clean post.
def sanitize_for_discord(text: str) -> str:
    """Strip ANSI escapes, control bytes, and path-like tokens from any text
    about to be posted to Discord. Idempotent, never raises."""
    if not text:
        return "(empty)"
    t = scrub.scrub(text)
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t or "(empty)"


def safe_error(exc: Exception) -> str:
    """Format a caught exception into a Discord-safe string. The exception
    TYPE is shown (so we know WHAT failed) but the raw text — which on
    Windows routinely embeds absolute paths and stack frames — is scrubbed."""
    tname = type(exc).__name__
    t = scrub.scrub(str(exc))
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t).strip()
    if t:
        return f"{tname}: {t[:180]}"
    return f"{tname} (details hidden - see the local bot log)"


async def _safe_reply(message: discord.Message, content: str) -> discord.Message:
    """Send `content` as a reply, degrading to a plain channel send when
    Discord refuses the reply reference (error 160002 — the bot has Send
    Messages but not View Message History in some threads). Returns the
    Message either way so callers can edit() it."""
    try:
        return await message.reply(content)
    except discord.Forbidden:
        with contextlib.suppress(Exception):
            botlog.log(
                "reply_fallback",
                who=getattr(getattr(message, "author", None), "name", "?"),
                guild=message.guild.name if message.guild else "DM",
                detail="reply reference blocked (160002) → fell back to plain send")
        return await message.channel.send(content)


# ----------------------------- availability gate -----------------------------
# This bot is a single LOOKUP layer (no prompt layer to gate). Only the
# master kill-switch (/off · enabled) stops it. No schedule window, no
# prompts flag — those are WyBot's LLM features and don't exist here.
def lookup_unavailable_error() -> str | None:
    """Why the lookups can't serve right now, or None if they can."""
    if not bot_state.is_enabled():
        return ("🛑 This bot is **switched off** right now — "
                "it'll be back soon.")
    return None


# ----------------------------- DJ rendering ----------------------------------
def _render_dj(entry: dict, compact: bool = False) -> str:
    """Format one DJ entry into a Discord message block.

    Each stream-link line is  ## Label: ```url``` : the ## heading makes the
    label larger, and the triple backticks wrap ONLY the url — in Discord that
    code span becomes a single-click copy target, which is what matters when
    reaching for the copy button inside a VR headset.

    compact=True is for the event-post auto-lineup: first three stream links
    only (Twitch, VRCDN RTSP, VRCDN MPEG-TS). /dj keeps the full block.
    """
    lines = [f"🎧 **{entry['name']}**"]
    if entry.get("twitch"):
        lines.append(f"## Twitch: ```{entry['twitch']}```")
    if entry.get("rtspt"):
        lines.append(f"## VRCDN (RTSP): ```{entry['rtspt']}```")
    if entry.get("mpeg_ts"):
        lines.append(f"## VRCDN (MPEG-TS): ```{entry['mpeg_ts']}```")
    if compact:
        return "\n".join(lines)
    if entry.get("preview"):
        lines.append(f"## VRCDN (host preview): ```{entry['preview']}```")
    if entry.get("genres"):
        lines.append(f"Genres: {entry['genres']}")
    if entry.get("availability"):
        lines.append(f"Availability: {entry['availability']}")
    return "\n".join(lines)


def _build_dj_blocks(names: list[str], compact: bool = False) -> list[str]:
    """Look up a list of DJ names and return the rendered result blocks.
    Shared by /dj AND the event auto-lineup so both produce identical output
    and share the exact / didyoumean / ambiguous / none handling."""
    blocks: list[str] = []
    for q in names:
        result = find_djs(q)
        status = result["status"]
        matches = result["matches"]

        if status == "exact":
            blocks.append(_render_dj(matches[0]["entry"], compact=compact))
            continue

        if status == "didyoumean":
            top = matches[0]
            blocks.append(
                f"🤔 Did you mean **{top['name']}**? "
                f"Here's the closest match (if this isn't right, "
                f"reply `/dj <exact name>`):\n\n"
                + _render_dj(top["entry"], compact=compact))
            continue

        if status == "ambiguous":
            lines = [f"🤔 A few possible matches for **{q}** — say the exact one:"]
            for m in matches[:5]:
                lines.append(f"  • `/dj {m['name']}`  (match {m['score']:.0%})")
            blocks.append("\n".join(lines))
            continue

        closest = result.get("closest")
        if closest:
            blocks.append(
                f"I couldn't find **{q}** on the DJ list. "
                f"Closest I see is **{closest['name']}** (match {closest['score']:.0%}) — "
                f"if that's who you meant, `/dj {closest['name']}`.")
        else:
            blocks.append(
                f"I couldn't find **{q}** on the DJ list — "
                f"try part of their exact name.")
    return blocks


async def respond_event_lineup(message: discord.Message) -> None:
    """Auto-detect an event post and reply with the DJ lineup (NO LLM).

    Fires when a message carries the event-post signature — 3+ lines each
    starting with a time-slot timestamp `<t:…:t>` + a bold DJ name. Same
    deterministic logic as /dj: gated only by the master kill-switch, never
    touches a model. Output uses the exact _build_dj_blocks path as /dj.
    """
    err = lookup_unavailable_error()
    if err:
        await _safe_reply(message, err)
        return
    res = event_post.extract_lineup(message.content, 3)
    names = res.get("names") or []
    asker = message.author.name
    guild = message.guild.name if message.guild else "DM"

    if not names:
        botlog.log("event_lineup", who=asker, guild=guild,
                   detail="event post detected — no readable DJ names in lineup")
        await _safe_reply(
            message,
            "🎧 I can see an event post here, but I couldn't read any DJ "
            "names from the lineup. If you'd like links for a specific DJ, "
            "use `/dj <name>`.")
        return

    botlog.log("event_lineup", who=asker, guild=guild,
               detail=", ".join(names)[:300])
    blocks = _build_dj_blocks(names, compact=True)
    header = f"🎧 **DJ lineup links** — {len(names)} in the post\n\n"
    body = "\n\n".join(blocks)
    chunks = discord_send.chunk_for_discord(header + body) or ["(empty)"]
    await _safe_reply(message, chunks[0])
    for i, c in enumerate(chunks[1:], start=2):
        await message.channel.send(f"… (continued)  ({i}/{len(chunks)})\n\n{c}")


# ----------------------------- command builders ------------------------------
class Bot(discord.Client):
    """Client that owns a CommandTree. We add commands imperatively
    (tree.add_command) so each has EXACTLY ONE registration — global scope.
    WHO may run a command is decided by Discord's per-command permissions
    (Server Settings → Roles & Permissions → Application Commands), not here."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # needed for the event auto-lineup
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)

    def add_public(self, coro, *, name, description, **params):
        cmd = discord.app_commands.Command(
            name=name, description=description, callback=coro, **params)
        self.tree.add_command(cmd, guild=None)
        return cmd

    # ---- lifecycle ----------------------------------------------------------
    async def on_ready(self):
        # Register every command ONCE, global scope.
        self.add_public(self.cmd_dj, name="dj",
                        description="Look up one or more DJs (comma-separated for several) — stream links from the master list.")
        self.add_public(self.cmd_vrcdn, name="vrcdn",
                        description="One VRCDN URL (RTSP / MPEG-TS) or a streamer name, expanded to all three link versions.")
        self.add_public(self.cmd_djlineup, name="djlineup",
                        description="Label an event's time-slot lines A–Z so DJs sign up by letter, not time.")
        self.add_public(self.cmd_timeslots, name="timeslots",
                        description="Generate the DJ time-slot block — start time, day, and how many slots; I do the timezone math.")
        self.add_public(self.cmd_status, name="status",
                        description="Bot availability, DJ list freshness, and version.")
        self.add_public(self.cmd_help, name="help",
                        description="Show all the commands I can do.")

        # Controls — who can RUN them is Discord's per-command permissions.
        self.add_public(self.cmd_on, name="on",
                        description="Switch the bot ON.")
        self.add_public(self.cmd_off, name="off",
                        description="Switch the bot OFF.")
        self.add_public(self.cmd_dj_refresh, name="dj-refresh",
                        description="Re-pull the DJ list from the Google Sheet now.")

        synced = await self.tree.sync()
        print(f"[bot] ready as {self.user} — {len(synced)} commands synced "
              f"(v{version.VERSION})")
        print(f"[bot] DJ list: {count_djs()} entries — {get_dj_freshness()}")
        try:
            lineup = list_djs(8)
            print(f"[bot] lineup preview: {', '.join(e['name'] for e in lineup)}")
        except Exception as exc:
            print(f"[bot] lineup preview failed (non-fatal): {exc}")

        # READY = connected + commands synced. Flip the dashboard NOW.
        try:
            await asyncio.to_thread(bot_state.mark_ready)
            print("[bot] lifecycle → READY")
            botlog.log("ready", detail=(
                f"v{version.VERSION} — {len(synced)} commands · "
                f"{count_djs()} DJs"))
        except Exception:
            pass

    # ---- background: DJ auto-refresh ---------------------------------------
    async def _dj_auto_refresh_loop(self):
        """Re-pull the sheet on a pace set by `dj_refresh_days` (default 3).
        Every failure is swallowed — this loop must never crash the bot."""
        while True:
            try:
                days = bot_config.get_dj_refresh_days()
                if is_dj_stale(days):
                    t0 = time.time()
                    try:
                        n = await asyncio.to_thread(refresh_dj_list, bot_config.get_sheet_url())
                        print(f"[bot] DJ auto-refresh: {n} DJs "
                              f"({botlog.fmt_duration(time.time() - t0)})")
                        botlog.log("dj_auto_refresh",
                                   detail=(f"auto-refresh (every {days}d) — "
                                           f"{n} DJs · "
                                           f"{botlog.fmt_duration(time.time() - t0)}"))
                    except Exception as exc:
                        print(f"[bot] DJ auto-refresh failed: {exc}")
                        botlog.log("dj_auto_refresh_error", level="error",
                                   detail=f"auto-refresh failed: {safe_error(exc)}")
            except Exception:
                pass
            await asyncio.sleep(1800)

    # ---- DJ commands --------------------------------------------------------
    async def cmd_dj(self, ctx: discord.Interaction, name: str):
        err = lookup_unavailable_error()
        if err:
            await ctx.response.send_message(err)
            return
        asker = ctx.user.name
        raw_parts = re.split(r"[,;]\s*", name)
        names = [p.strip() for p in raw_parts if p.strip()]
        if not names:
            await ctx.response.send_message("Give me a DJ name — e.g. `/dj bobby j`")
            return
        multi = len(names) > 1
        botlog.log("dj", who=asker, detail=", ".join(names)[:300],
                   guild=ctx.guild.name if ctx.guild else "DM")

        blocks = _build_dj_blocks(names)
        header = (f"🎧 **{len(blocks)} result(s)** — asked by {asker}\n\n") if multi else ""
        body = "\n\n".join(blocks)
        await discord_send.send_long(ctx, header + body)

    async def cmd_vrcdn(self, ctx: discord.Interaction, url: str):
        """Expand ONE VRCDN URL (RTSP or MPEG-TS) — or a bare streamer name —
        into all three link versions: RTSP, MPEG-TS, and host preview.
        Deterministic — NO LLM, NO model call."""
        err = lookup_unavailable_error()
        if err:
            await ctx.response.send_message(err)
            return
        asker = ctx.user.name
        guild = ctx.guild.name if ctx.guild else "DM"
        res = vrcdn.parse(url)
        if res["status"] == "bad":
            botlog.log("vrcdn", who=asker, guild=guild,
                       detail=f"unparseable input: {(url or '')[:200]!r}")
            await ctx.response.send_message(res["message"])
            return
        username = res["username"]
        botlog.log("vrcdn", who=asker, guild=guild, detail=username)
        await discord_send.send_long(ctx, vrcdn.render(username))

    async def cmd_djlineup(self, ctx: discord.Interaction, times: str):
        """Label a pasted block of time-slot lines with A–Z letter emotes.
        TOKEN-based: finds every <t:…:t> slot in order and gives each its own
        labelled line. Deterministic — NO LLM, NO model call."""
        err = lookup_unavailable_error()
        if err:
            await ctx.response.send_message(err)
            return
        asker = ctx.user.name
        guild = ctx.guild.name if ctx.guild else "DM"

        if djlineup.count_slots(times) == 0:
            botlog.log("djlineup", who=asker, guild=guild,
                       detail=f"no time-slots found: {(times or '')[:120]!r}")
            await ctx.response.send_message(
                "I didn't find any time slots in that — make sure the "
                "`<t:…:t>` ham-time slots are included in what you pasted.")
            return

        labelled = djlineup.label_slots(times)
        n = djlineup.count_slots(times)
        botlog.log("djlineup", who=asker, guild=guild,
                   detail=f"{n} slot(s) labelled A→{djlineup.slot_code(n - 1)}")
        await discord_send.send_long(ctx, labelled)

    async def cmd_timeslots(self, ctx: discord.Interaction,
                            start_time: str,
                            slot_count: str,
                            day: Optional[str] = None,
                            doors_lead: Optional[str] = "15",
                            add_letters: Optional[str] = "no",
                            slot_duration: Optional[str] = "1 hour"):
        """Generate a block of equally-spaced DJ time-slots as `<t:…:t>` lines.
        DST-aware (OS tz database). Deterministic — NO LLM, NO model call."""
        err = lookup_unavailable_error()
        if err:
            await ctx.response.send_message(err)
            return
        asker = ctx.user.name
        guild = ctx.guild.name if ctx.guild else "DM"

        try:
            out = timeslots.build(start_time, slot_count, day,
                                  doors_lead=doors_lead,
                                  add_letters=add_letters,
                                  slot_duration=slot_duration)
        except ValueError as ve:
            botlog.log("timeslots", who=asker, guild=guild, level="warn",
                       detail=f"bad input: start={start_time!r} count={slot_count!r} "
                              f"day={day!r} → {ve}")
            await ctx.response.send_message(f"⚠️ {ve}")
            return
        except Exception as exc:
            botlog.log("timeslots_error", who=asker, guild=guild, level="error",
                       detail=safe_error(exc))
            await ctx.response.send_message(f"⚠️ {safe_error(exc)}")
            return

        n = len(out.splitlines()) - (1 if (doors_lead or "").strip() else 0)
        botlog.log("timeslots", who=asker, guild=guild,
                   detail=(f"start={start_time!r} day={day!r} count={slot_count!r} "
                           f"slots={n} · letters={bool(timeslots.letter_enabled(add_letters))}"))
        await discord_send.send_long(ctx, out)

    # ---- info ---------------------------------------------------------------
    async def cmd_status(self, ctx: discord.Interaction):
        on = bot_state.is_enabled()
        fresh = get_dj_freshness()
        lines = [
            f"**VRCDJ_bot v{version.VERSION}**",
            f"{'✅ ON' if on else '🛑 OFF'} — {'answering now' if on else 'fully paused'}",
            f"🎧 DJ list: {count_djs()} entries — {fresh}",
        ]
        await ctx.response.send_message("\n".join(lines))

    async def cmd_help(self, ctx: discord.Interaction):
        lines = [
            "**🤖 VRCDJ_bot — DJ lookup & time-slot tools** "
            f"*(v{version.VERSION})*",
            "",
            "🎧 **DJ list**",
            "  • `/dj <name>` — look up a DJ's stream links (Twitch / VRCDN)",
            "  • comma-separate several: `/dj hyndal, nwinn`",
            "  • fuzzy: typos and near-misses still find the closest DJ",
            "",
            "🔗 **VRCDN**",
            "  • `/vrcdn <url>` — paste ONE VRCDN link (RTSP or MPEG-TS) — or just "
            "the name — and I'll give you all three: RTSP, MPEG-TS, and host preview",
            "",
            "🕐 **Time slots**",
            "  • `/djlineup <times>` — paste an event's ham-time slots and I'll "
            "label them in order A→Z (then AA, AB, …) so DJs sign up for "
            "\"slot B\" instead of a time-zone",
            "  • `/timeslots <start> <count>` — generate the whole block of "
            "`<t:…:t>` DJ slots (e.g. `/timeslots 10pm ET 6 friday`, or just "
            "`/timeslots 1789178400 6` — a timestamp has its own date). "
            "Times: ET/CT/MT/PT/UTC/BST/GMT, DST-aware. "
            "Optional: `doors_lead:` (min before slot 1, default 15), "
            "`add_letters:` yes/no, `slot_duration:` (default 1 hour)",
            "",
            "🎵 **Whole event at once** — paste a full event post (3+ time-slot "
            "lines each with a DJ name) and **@mention me** — I'll read it and "
            "post every DJ's links, no command needed.",
            "",
            "📊 **Info**",
            "  • `/status` — am I on/off, DJ list freshness, version",
            "  • `/help` — this message",
            "",
            "🛠️ **Controls** (`/on` · `/off` · `/dj-refresh`)",
            "  • For staff — who can use them is set by the server admins in "
            "Discord's per-command permissions.",
        ]
        await discord_send.send_long(ctx, "\n".join(lines))

    # ---- controls -----------------------------------------------------------
    async def cmd_on(self, ctx: discord.Interaction):
        bot_state.set_enabled(True, by=str(ctx.user.id))
        botlog.log("toggle", who=ctx.user.name, detail="ON")
        await ctx.response.send_message("✅ Bot is now **ON**.")

    async def cmd_off(self, ctx: discord.Interaction):
        bot_state.set_enabled(False, by=str(ctx.user.id))
        botlog.log("toggle", who=ctx.user.name, detail="OFF")
        await ctx.response.send_message("🛑 Bot is now **OFF**.")

    async def cmd_dj_refresh(self, ctx: discord.Interaction):
        await ctx.response.send_message("🔄 Re-pulling the DJ sheet…")
        try:
            n = await asyncio.to_thread(refresh_dj_list, bot_config.get_sheet_url())
            botlog.log("dj_refresh", who=ctx.user.name, detail=f"{n} DJs loaded")
            await ctx.edit_original_response(
                content=f"✅ DJ list refreshed — **{n}** DJs loaded.")
        except Exception as exc:
            botlog.log("dj_refresh_error", who=ctx.user.name,
                       detail=str(exc)[:200], level="error")
            await ctx.edit_original_response(
                content=f"⚠️ Refresh failed: {safe_error(exc)}")


def _strip_mention(text: str, bot_user: discord.User) -> str:
    """Remove a single @bot mention from the front of the message."""
    t = text.strip()
    for m in (f"<@!{bot_user.id}>", f"<@{bot_user.id}>"):
        if t.startswith(m):
            t = t[len(m):].strip()
    return t


# ----------------------------- error handling --------------------------------
def register_error_handler(bot: Bot):
    """Route app-command errors to a friendly reply (CommandTree.on_error)."""
    async def on_app_command_error(interaction: discord.Interaction, error):
        cmd_name = getattr(getattr(interaction, "command", None), "name", "?")
        print(f"[bot] command error ({cmd_name}): {type(error).__name__}: {error}")
        with contextlib.suppress(Exception):
            botlog.log("command_error", who=getattr(interaction.user, "name", ""),
                       detail=f"{cmd_name}: {type(error).__name__}: {str(error)[:300]}",
                       level="error")
        with contextlib.suppress(Exception):
            if interaction.response.is_done():
                await interaction.followup.send("⚠️ Something went wrong running that.")
            else:
                await interaction.response.send_message("⚠️ Something went wrong running that.")

    bot.tree.on_error = on_app_command_error


# ----------------------------- lifecycle heartbeat ---------------------------
async def _heartbeat_loop(interval: float = 4.0):
    """Refresh the lifecycle heartbeat while the bot is running. The
    dashboard reads last_beat to tell 'alive right now' from 'left a stale
    file behind'."""
    while True:
        try:
            await asyncio.to_thread(bot_state.heartbeat)
        except Exception:
            pass
        await asyncio.sleep(interval)


# ----------------------------- main ------------------------------------------
async def main():
    bot = Bot()
    register_error_handler(bot)

    # DUPLICATE-INSTANCE GUARD — if another VRCDJ_bot is alive right now
    # (heartbeat fresh, different PID), refuse to start BEFORE touching the
    # Discord token. Two instances on one token fight the gateway.
    # NOTE: WyBot and VRCDJ_bot are separate processes with separate
    # bot_lifecycle.json files (different project folders), so this guard does
    # NOT block running VRCDJ_bot alongside WyBot — exactly what we want.
    _dup = None
    try:
        _dup = bot_state.duplicate_info()
    except Exception:
        _dup = None
    if _dup:
        try:
            botlog.log("duplicate",
                       detail=(f"another VRCDJ_bot is already running (PID "
                               f"{_dup.get('pid')} — heartbeat {_dup.get('age_s')}s "
                               f"ago). This second instance REFUSED to start."),
                       level="warn")
        except Exception:
            pass
        print("=" * 62)
        print("  ⚠️  STOPPED — another VRCDJ_bot is already running")
        print()
        print(f"      pid     : {_dup.get('pid')}")
        print(f"      heartbeat: {_dup.get('age_s')}s ago")
        print()
        print("      This second instance is NOT starting (two bots on the")
        print("      same Discord token would just fight each other).")
        print("      Stop the first one, then launch again.")
        print("=" * 62)
        sys.exit(77)  # 77 = 'already running'

    # Stamp which commit THIS running code is on (7-char short id). Best-effort:
    # no git / not a repo → starts without a commit field.
    try:
        import repo_version
        # (record_start is a WyBot state helper; VRCDJ_bot's bot_state uses
        #  mark_ready/heartbeat, so we just log the commit for the dashboard.)
        head = repo_version.get_local_head()
        botlog.log("boot", level="info",
                   detail=f"v{version.VERSION} · commit={head or 'unknown'}")
    except Exception:
        pass

    beat_task = asyncio.create_task(_heartbeat_loop())
    dj_task = asyncio.create_task(bot._dj_auto_refresh_loop())

    @bot.event
    async def on_message(message):
        if message.author.id == bot.user.id:
            return
        if not message.guild or not message.content:
            return
        text = message.content.strip()
        if not text:
            return

        mentions_bot = bool(
            message.mentions and any(m.id == bot.user.id for m in message.mentions))
        chan_id = str(message.channel.id)
        whitelisted = chan_id in _event_lineup_channels()

        # EVENT-POST AUTO-LINEUP: a post carrying the event signature (3+
        # lines each with a time-slot <t:…:t> + a DJ name) gets an automatic
        # DJ-links reply. Deterministic, NO model. Two ways it fires:
        #   A. in a whitelisted event_lineup_channels channel (mention optional);
        #   B. in ANY channel ONLY if the post explicitly @-mentions the bot.
        # A lineup post in another channel with no @mention is plain chat → ignored.
        if (event_post.is_event_post(text, 3) and (whitelisted or mentions_bot)):
            await respond_event_lineup(message)
            return
        # Everything else is not addressed to this bot (it has no prompt
        # layer) — silently ignore.
        return

    async with bot:
        await bot.start(TOKEN)

    # Clean shutdown.
    try:
        beat_task.cancel()
    except Exception:
        pass
    try:
        dj_task.cancel()
    except Exception:
        pass
    with contextlib.suppress(Exception):
        await asyncio.to_thread(bot_state.mark_stopped)
    with contextlib.suppress(Exception):
        botlog.log("stopped", detail="shutting down — bot process exiting",
                   level="info")
    print("[bot] lifecycle → STOPPED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        with contextlib.suppress(Exception):
            bot_state.mark_stopped()
        print("[bot] interrupted — shutting down")
