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
  - /status         → DJ list freshness + version.
  - /help           → the command reference.
  - /dj-refresh     → staff: re-pull the DJ list now (who may run it is set
    in Discord's per-command permissions, NOT in this code).
  - EVENT AUTO-LINEUP → paste a full event post (3+ time-slot lines each with
    a DJ name) and it's answered with every DJ's links — @mention me to
    trigger it in any channel, or drop it in a configured "lineup channel".

  NOTE: there is deliberately NO /on · /off slash command.  The bot's
  master kill-switch is owned by the dashboard (password-gated) so that
  members of other servers can't pause a bot that isn't theirs.

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
import signup         # noqa: E402  (/signup — pure engine: render + parse)
import signup_store   # noqa: E402  (/signup — persistent slot-assignment state)
import signup_ui      # noqa: E402  (/signup — modals + button views)
import discord_send   # noqa: E402  (2000-char-safe sends)
import dj_add         # noqa: E402  (/adddj — classify + format a DJ suggestion)
import gh_add         # noqa: E402  (/adddj — the GitHub issue drop-box)


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


def fmt_uptime(seconds: float) -> str:
    """Compact uptime for the Discord presence + /status: MM:SS → H:MM:SS → Dd H:MM:SS.

    Matches the dashboard's uptime display in spirit (always readable, no
    units) but is unit-free so it reads like a live counter in the member list.
    """
    try:
        s = max(0, int(round(seconds)))
    except (TypeError, ValueError):
        return "?"
    p = lambda n: str(n).zfill(2)
    d, r = divmod(s, 86400)
    h, r = divmod(r, 3600)
    m, sec = divmod(r, 60)
    if d:
        return f"{d}d {h}:{p(m)}:{p(sec)}"
    if h:
        return f"{h}:{p(m)}:{p(sec)}"
    return f"{p(m)}:{p(sec)}"


def _session_uptime() -> float | None:
    """Seconds the current bot session has been ready (from bot_state), or None
    if not ready yet. This is the number the Discord presence counts up."""
    ra = bot_state.ready_at()
    if ra is None:
        return None
    return max(0.0, time.time() - ra)


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
        # A fresh bot session begins — zero the session counter (the lifetime
        # total in bot_state.json is left alone).
        try:
            await asyncio.to_thread(bot_state.reset_session)
        except Exception:
            pass
        # Record how many servers we're in (Discord exposes this via bot.guilds).
        try:
            await asyncio.to_thread(bot_state.set_guild_count, len(self.guilds))
        except Exception:
            pass
        # Register every command ONCE, global scope.
        self.add_public(self.cmd_dj, name="dj",
                        description="Look up one or more DJs (comma-separated for several) — stream links from the master list.")
        self.add_public(self.cmd_vrcdn, name="vrcdn",
                        description="One VRCDN URL (RTSP / MPEG-TS) or a streamer name, expanded to all three link versions.")
        self.add_public(self.cmd_adddj, name="adddj",
                        description="Suggest a new DJ for the master list — name + link (Twitch or VRCDN), optional genres.")
        self.add_public(self.cmd_djlineup, name="djlineup",
                        description="Label an event's time-slot lines A–Z so DJs sign up by letter, not time.")
        self.add_public(self.cmd_timeslots, name="timeslots",
                        description="Generate the DJ time-slot block — start time, day, and how many slots; I do the timezone math.")
        self.add_public(self.cmd_signup, name="signup",
                        description="Host: post a live slot-signup board — DJs/dancers pick their own slots (real-time).")
        self.add_public(self.cmd_status, name="status",
                        description="Bot availability, DJ list freshness, and version.")
        self.add_public(self.cmd_help, name="help",
                        description="Show all the commands I can do.")

        # Controls (staff-only via Discord per-command permissions).
        # /on · /off are deliberately NOT registered — only the dashboard
        # (behind its password) can pause/resume the bot.  Other servers'
        # members must not be able to switch it off.
        self.add_public(self.cmd_dj_refresh, name="dj-refresh",
                        description="Re-pull the DJ list from the Google Sheet now.")
        self.add_public(self.cmd_presence, name="presence",
                        description="Show or hide my live session-uptime status in the member list — /presence on or off.")

        # NOTE: the /signup "Event Creation" modal submits through
        # signup_ui.SignupModal.on_submit (discord.py 2.x modals use an
        # on_submit hook on the Modal subclass — there is NO tree.on_modal_submit).
        # It routes to signup_ui.handle_submit, which handles BOTH create and
        # edit (edit is opened by the event post's [Edit Event] button).

        # Re-register every stored event's persistent button view so the
        # [Pick Slot] / [Edit Event] buttons keep working across a bot restart.
        try:
            for _msg_id, _rec in signup_store.all_events().items():
                self.add_view(signup_ui.EventView(str(_msg_id)),
                              message_id=int(_msg_id))
        except Exception as _ve:
            print(f"[bot] signup view re-registration: {_ve}")

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

        # Start the Discord presence loop (live session uptime in the member
        # list). ready_at is set above, so the very first push is accurate.
        try:
            if getattr(self, "_presence_task", None) is None:
                self._presence_task = asyncio.create_task(self._presence_loop())
        except Exception as exc:
            print(f"[bot] presence loop start failed (non-fatal): {safe_error(exc)}")

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

    # ---- usage counters -----------------------------------------------------
    # discord.py fires `app_command_completion` only after a command runs
    # successfully (the tree's `else` branch, i.e. the user actually got a
    # response). That's the exact moment we want to count a reply — so this
    # fires once per answered command and never on errors.
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        try:
            await asyncio.to_thread(bot_state.bump_reply)
        except Exception:
            pass  # counting is best-effort; never let it break a reply

    # ---- server (guild) tracking -------------------------------------------
    # Keep the "Servers" counter live: refresh it when a server adds the bot
    # (guild_join) or kicks it (guild_remove). on_ready sets the initial value.
    async def on_guild_join(self, guild: discord.Guild):
        try:
            await asyncio.to_thread(bot_state.set_guild_count, len(self.guilds))
        except Exception:
            pass
        with contextlib.suppress(Exception):
            botlog.log("guild_join", guild=guild.name,
                       detail=f"joined a server ({len(self.guilds)} total)")

    async def on_guild_remove(self, guild: discord.Guild):
        try:
            await asyncio.to_thread(bot_state.set_guild_count, len(self.guilds))
        except Exception:
            pass
        with contextlib.suppress(Exception):
            botlog.log("guild_remove", guild=guild.name,
                       detail=f"left a server ({len(self.guilds)} remaining)")

    # ---- DJ commands --------------------------------------------------------
    @discord.app_commands.describe(
        name="DJ name(s) to look up — separate several with commas",
    )
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

    @discord.app_commands.describe(
        url="One VRCDN URL, or just the streamer name",
    )
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

    @discord.app_commands.describe(
        name="The DJ's name as it should appear on the list",
        link="Twitch URL, VRCDN link, or just their streamer name",
        genres="Their genres or vibe (optional)",
        availability="When they usually play (optional)",
    )
    async def cmd_adddj(self, ctx: discord.Interaction,
                        name: str, link: str,
                        genres: Optional[str] = None,
                        availability: Optional[str] = None):
        """`/adddj` — a community member suggests a new DJ for the master list.

        - `name` + `link` are REQUIRED. If the link is a Twitch URL it's kept
          as-is; if it's a VRCDN URL (or a bare streamer name) it's expanded
          with the same /vrcdn engine into all three VRCDN links.
        - `genres` / `availability` are OPTIONAL.
        The suggestion is formatted the master-list way and pushed as a GitHub
        issue in the owner's repo (the "drop-box"). It NEVER touches the Google
        Sheet or repo files — the owner reviews it (dashboard checker) and adds
        it manually.
        """
        err = lookup_unavailable_error()
        if err:
            await ctx.response.send_message(err)
            return
        asker = ctx.user.name
        guild = ctx.guild.name if ctx.guild else "DM"

        # 1. classify + format (pure, deterministic, no network)
        text, cls = dj_add.render_block(name, link, genres, availability)
        if cls.get("kind") == "bad":
            botlog.log("adddj", who=asker, guild=guild,
                       detail=f"rejected: {cls.get('message')!r} link={link!r}")
            await ctx.response.send_message(
                "⚠️ I need the DJ's name **and** a link. "
                "Twitch or VRCDN (RTSP / MPEG-TS / preview) — or just their "
                "streamer name — for the link. Try again, e.g. "
                "`/adddj Hyndal https://twitch.tv/hyndal`.")
            return

        # 2. build the issue payload
        payload = dj_add.issue_payload(name, link, genres, availability,
                                       submitter=asker, guild=guild)

        # 3. push to the GitHub drop-box. Token (if any) comes from the
        #    environment — it is never logged or echoed here.
        token = os.environ.get("GH_TOKEN", "").strip()
        result = await asyncio.to_thread(
            gh_add.create_issue, payload["title"], payload["body"],
            payload["labels"], token)

        if result.get("ok"):
            botlog.log("adddj", who=asker, guild=guild,
                       detail=f"{name!r} {link!r} → {result.get('url')}")
            await ctx.response.send_message(
                f"✅ **{name}** submitted for the master list!\n\n"
                f"Here's what I sent (copy-paste ready):\n\n{text}\n\n"
                f"📮 It's in the owner's review queue: "
                f"{result.get('url')}\n"
                f"*You'll be added once the owner approves — nothing is added "
                f"to the public list until they review it.*")
        else:
            # Suggestion was NOT delivered — be honest, and hand over the
            # formatted block so the host can send it to the owner directly.
            botlog.log("adddj_submit_failed", who=asker, guild=guild,
                       detail=f"{name!r} {link!r} → {result.get('error')}",
                       level="error")
            await ctx.response.send_message(
                "⚠️ I couldn't submit that to the review queue right now "
                f"({result.get('error', 'unknown error')}).\n\n"
                "Here's the formatted suggestion so you can send it to the "
                "owner manually:\n\n"
                "```md\n" + text + "\n```")

    @discord.app_commands.describe(
        times="Paste the event's <t:…:t> time-slot lines here",
    )
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

    @discord.app_commands.describe(
        start_time="Time the first slot starts, e.g. 10pm ET or 22:00",
        slot_count="Total number of slots",
        day="The day, e.g. friday or June 5th (skip if start is a full timestamp)",
        doors_lead="Minutes before slot 1 for the doors line (default 15, blank = none)",
        add_letters="Append letter emotes :regional_indicator_a: to each slot — type yes to enable",
        slot_duration="How long each slot is, e.g. 1 hour, 90 min, 1.5 hours",
    )
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

    # ---- /signup (DJ / dancer slot auto-signup) ---------------------------
    @staticmethod
    def _parse_opt_int(value: Optional[str], default: int, *,
                       lo: int = 0, hi: int = 100) -> int:
        """Parse an optional integer field (doors lead, per-slot, per-person).
        Blank / invalid -> default.  Clamped to [lo, hi]."""
        raw = (value or "").strip()
        if not raw:
            return default
        try:
            v = int(re.search(r"\d+", raw).group(0))
        except (AttributeError, ValueError):
            return default
        return max(lo, min(hi, v))

    @discord.app_commands.describe(
        start_time="Time the first slot starts, e.g. 10pm ET or 22:00",
        slots="Total number of slots",
        slot_time="Length of each slot, e.g. 1 hour or 90 min",
        day="The day, e.g. friday or June 5th (skip if start is a full timestamp)",
        doors_lead="Minutes before slot 1 for the doors line (default 15, blank = none)",
        limit_per_slot="Max people allowed per slot",
        limit_per_person="Max slots one person can pick",
    )
    async def cmd_signup(self, ctx: discord.Interaction,
                         start_time: str,
                         slots: str,
                         slot_time: str,
                         day: Optional[str] = None,
                         doors_lead: Optional[str] = "15",
                         limit_per_slot: Optional[str] = "1",
                         limit_per_person: Optional[str] = "1"):
        """`/signup` — host builds a live slot-signup board.

        1. Compute the slot list from start / count / slot_time (+ day for a
           time-of-day start).
        2. Open the "Event Creation" modal — the host sets the event name in
           the modal's text field, reviews / edits the slot block, then
           submits.
        3. On submit (via SignupModal.on_submit → signup_ui.handle_submit) the
           event is posted with a [Pick Slot] + [Edit Event] button row, and
           everyone can sign up live, limits enforced, no host babysitting.
        """
        err = lookup_unavailable_error()
        if err:
            await ctx.response.send_message(err)
            return
        asker = ctx.user.name
        guild = ctx.guild.name if ctx.guild else "DM"

        doors = self._parse_opt_int(doors_lead, signup.DEFAULT_DOORS_LEAD, lo=0, hi=600)
        per_slot = self._parse_opt_int(limit_per_slot, signup.DEFAULT_PER_SLOT, lo=1, hi=100)
        per_person = self._parse_opt_int(limit_per_person, signup.DEFAULT_PER_PERSON, lo=1, hi=50)

        try:
            slot_ts = signup.build_slots(start_time, slots, slot_time, day=day)
        except ValueError as ve:
            botlog.log("signup", who=asker, guild=guild, level="warn",
                       detail=f"bad slots: start={start_time!r} n={slots!r} "
                              f"dur={slot_time!r} day={day!r} → {ve}")
            await ctx.response.send_message(f"⚠️ {ve}")
            return
        if not (1 <= len(slot_ts) <= signup.MAX_SLOTS):
            await ctx.response.send_message(
                f"⚠️ I can handle **{signup.MAX_SLOTS} slots** on one board — "
                "that's a lot for a single event.  Try fewer slots or split "
                "the event into two `/signup`s.")
            return

        slot_seconds = (slot_ts[1] - slot_ts[0]) if len(slot_ts) >= 2 else 3600
        doors_ts = (slot_ts[0] - doors * 60) if doors > 0 else None
        # The event name lives in the modal's text field (su_name) — the host
        # types it there on submit. This is just a starting placeholder.
        title = "New Event"

        # Prefill the modal with the exact template, then hand it over.
        prefill = signup.render_template(title, slot_ts, slot_seconds, doors_ts)
        # One modal class for both create and edit (msg_id=None here = create).
        modal = signup_ui.SignupModal(
            prefill_name=title, prefill_block=prefill,
            max_per_slot=per_slot, max_per_person=per_person)
        await ctx.response.send_modal(modal)
        botlog.log("signup", who=asker, guild=guild,
                   detail=(f"modal opened: {len(slot_ts)} slot(s) · "
                           f"doors={doors}m · per_slot={per_slot} · per_person={per_person}"))

    # ---- info ---------------------------------------------------------------
    async def cmd_status(self, ctx: discord.Interaction):
        on = bot_state.is_enabled()
        fresh = get_dj_freshness()
        botlog.log("status", who=ctx.user.name,
                   guild=ctx.guild.name if ctx.guild else "DM")
        lines = [
            f"**VRCDJ_bot v{version.VERSION}**",
            f"{'✅ ON' if on else '🛑 OFF'} — {'answering now' if on else 'fully paused'}",
            f"🎧 DJ list: {count_djs()} entries — {fresh}",
        ]
        await ctx.response.send_message("\n".join(lines))

    async def cmd_help(self, ctx: discord.Interaction):
        botlog.log("help", who=ctx.user.name,
                   guild=ctx.guild.name if ctx.guild else "DM")
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
            "  • `/adddj <name> <link>` — **suggest a new DJ** for the master list. "
            "Twitch links are used as-is; VRCDN links (or just the streamer name) "
            "get expanded to all three links. Optional: `genres:` and "
            "`availability:`. It goes to the owner's review queue — you'll be "
            "added once it's approved",
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
            "📅 **Slot sign-up board** — `/signup` (DJ / dancer auto-signup)",
            "  • Host sets it up in the DJ/dancer channel — everyone signs up "
            "for their own slots live, no host babysitting, no double-booking",
            "  • `/signup <start> <slots> <slot_time> [day] [doors_lead] "
            "[limit_per_slot] [limit_per_person]`",
            "  • I open an \"Event Creation\" window prefilled with the exact "
            "layout — review / tweak it, then submit; I post the board with a "
            "**Pick Slot** button and an **Edit Event** button",
            "  • **Pick Slot** → a private prompt (only you see it) with one "
            "button per slot; click `#1` and your name is placed on slot 1 "
            "(click again to cancel). Others see it update instantly",
            "  • **Edit Event** (host) → re-opens the window; save edits the "
            "same post in place and **keeps everyone who's already signed up**",
            "  • start time: raw unix (`1788670800`) or a ham-time wrap "
            "(`10pm ET`); `doors_lead` = minutes before slot 1 (default 15)",
            "",
            "🎵 **Whole event at once** — paste a full event post (3+ time-slot "
            "lines each with a DJ name) and **@mention me** — I'll read it and "
            "post every DJ's links, no command needed.",
            "",
            "📊 **Info**",
            "  • `/status` — bot version + DJ list freshness",
            "  • `/help` — this message",
            "",
            "🛠️ **Staff** (`/dj-refresh`)",
            "  • Re-pull the DJ list now — who can run it is set by the server "
            "admins in Discord's per-command permissions.",
        ]
        await discord_send.send_long(ctx, "\n".join(lines))

    # ---- controls (dashboard-only kill-switch; no /on /off slash cmds) ----
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

    # ---- Discord presence (live session uptime in the member list) --------
    # A "custom status" activity that ticks every 60s so anyone looking at the
    # bot in the server member list sees how long this session has been up —
    # no command needed. Off → clears the status entirely.
    def _presence_name(self) -> str | None:
        """The current presence text, or None if the session isn't up yet."""
        up = _session_uptime()
        if up is None:
            return None
        return f"up: {fmt_uptime(up)} · v{version.VERSION}"

    async def _push_presence(self, log_result: bool = False):
        """Push the current custom-status activity (or clear it if disabled).

        ``log_result`` (True on the first push / explicit toggles) writes one
        line to the dashboard Activity Log so we can see the outcome instead of
        guessing — 'presence_up' on success, 'presence_error' on failure.
        """
        try:
            if bot_config.is_presence_enabled():
                name = self._presence_name()
                if name:
                    await self.change_presence(
                        activity=discord.Activity(
                            type=discord.ActivityType.custom, name=name))
                    if log_result:
                        with contextlib.suppress(Exception):
                            botlog.log("presence_up",
                                       detail=f"custom status set → “{name}”")
            else:
                await self.change_presence(activity=None)
                if log_result:
                    with contextlib.suppress(Exception):
                        botlog.log("presence_off",
                                   detail="custom status cleared (presence off)")
        except Exception as exc:
            print(f"[bot] presence update failed (non-fatal): {safe_error(exc)}")
            with contextlib.suppress(Exception):
                botlog.log("presence_error",
                           detail=f"change_presence failed: {safe_error(exc)}",
                           level="error")

    async def _presence_loop(self):
        """Refresh the presence every 60s. Swallows every error. The FIRST
        push is logged to the dashboard log so the outcome is visible."""
        try:
            await self._push_presence(log_result=True)
        except Exception:
            pass  # _push_presence already handles its own errors
        while True:
            await self._push_presence()
            await asyncio.sleep(60)

    @discord.app_commands.describe(
        state="Show my session uptime in the member list: on or off",
    )
    async def cmd_presence(self, ctx: discord.Interaction, state: str):
        """Toggle the member-list uptime status on or off (staff-only via
        Discord's per-command permissions, like /dj-refresh)."""
        state = state.strip().lower()
        if state not in ("on", "off"):
            await ctx.response.send_message(
                "Please say `/presence on` or `/presence off`.")
            return
        new = state == "on"
        try:
            await asyncio.to_thread(bot_config.set_presence_enabled, new)
            await self._push_presence()
            botlog.log("presence_toggle", who=ctx.user.name, detail=state)
            msg = ("👀 Presence uptime **on** — I'll show my session time in the "
                   "member list.") if new else \
                  "👀 Presence uptime **off** — status cleared."
            await ctx.response.send_message(msg)
        except Exception as exc:
            botlog.log("presence_error", who=ctx.user.name,
                       detail=str(exc)[:200], level="error")
            await ctx.response.send_message(f"⚠️ {safe_error(exc)}")


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

    # Stamp a clean SESSION boundary for 'session'-scoped counters (e.g.
    # 'Errors This Session' in the dashboard). Done AFTER the duplicate-instance
    # guard above so a refused second instance can't stomp the live bot's stamp.
    try:
        bot_state.mark_started()
    except Exception:
        pass

    # ---- granular startup trace (visible in the dashboard Activity Log) ----
    # The bot's console now also goes to bot_console.log, but the structured
    # botlog entries are what the dashboard surface shows — so narrate each
    # phase here to answer "what is the bot doing while it starts?"
    head = "unknown"
    try:
        import repo_version
        head = repo_version.get_local_head() or "unknown"
    except Exception:
        pass
    botlog.log("boot", detail=(
        f"v{version.VERSION} · commit={head} · pid={os.getpid()} · "
        f"discord.py {discord.__version__}"))
    botlog.log("boot_config", detail=(
        f"presence={'on' if bot_config.is_presence_enabled() else 'off'} · "
        f"kill-switch={'ON' if bot_state.is_enabled() else 'OFF'} · "
        f"dj-refresh every {bot_config.get_dj_refresh_days()}d · "
        f"sheet={'configured' if (bot_config.get_sheet_url() or '').strip() else 'NOT SET — lookups will fail'}"))
    try:
        botlog.log("boot_djlist",
                   detail=f"DJ cache: {count_djs()} entries · {get_dj_freshness()}")
    except Exception as exc:
        botlog.log("boot_djlist", level="warn",
                   detail=f"DJ cache not readable yet: {safe_error(exc)}")
    botlog.log("boot_connect", detail="connecting to Discord gateway…")

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
    try:
        if getattr(bot, "_presence_task", None) is not None:
            bot._presence_task.cancel()
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
