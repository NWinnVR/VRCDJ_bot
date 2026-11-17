← [README](../README.md) · [Wiki index](index.md)

# Commands — full reference

A small set of **slash commands** aimed at running VRChat DJ events:

| Command | What it does |
|---|---|
| `/dj <name>` | Look up one or more DJs (comma-separated) — pulls their Twitch / VRCDN links from the master list. |
| `/vrcdn <url or name>` | Take one VRCDN URL (RTSP / MPEG-TS) or a streamer name and expand it into **all three** link versions (RTSP, MPEG-TS, preview). |
| `/djlineup` | Label an event's time-slot lines **A–Z** so DJs sign up by letter, not by time. |
| `/timeslots` | Generate a full DJ time-slot block from a start time, day, and slot count — the bot does the timezone math (DST-aware). |
| `/signup` | **Host a live slot sign-up board** — DJs/dancers pick their own slots with one click, real-time and visible to everyone, with per-slot and per-person limits and a host **Edit Event** that keeps already-filled slots. → [deep dive below](#slot-sign-up-board-signup). |
| `/adddj` | **Suggest a new DJ** for the master list — name + link required, genres / availability optional. A VRCDN link is expanded to all three versions for you. → [deep dive below](#suggesting-a-new-dj-community). |
| `/status` | Bot availability, DJ-list freshness, and the version. |
| `/help` | List every command. |
| `/dj-refresh` | Re-pull the DJ list from the Google Sheet right now. |
| `/presence on` / `/presence off` | 👀 Show or hide the **live session-uptime** the bot displays in the Discord **member list** (Rich Presence). A cosmetic status — safe for anyone to toggle. → [deep dive below](#rich-presence-live-session-uptime-in-discord). |

> **No `/on` / `/off` slash commands.** This bot is designed to run across
> several servers at once, so the kill-switch is **not** a slash command anyone
> can type. Only the operator — through the **password-protected dashboard**
> (Start / Stop) — can pause or stop the bot. The `/status` command still *reports*
> ON/OFF state; it just can't change it.

There is also an **event auto-lineup**: post a line of time-slots (with the
`<t:…:t>` timestamp format) in a bound channel and the bot labels them A–Z
automatically.

**What it is *not*:** no AI / LLM, no prompt generation, no host-matching, no
social-post drafting. It does exactly the DJ + time-slot job and nothing
more — which is what makes it safe, portable, and cheap to run.
---

## Slot sign-up board (`/signup`)

**New in 1.12.0.** A live slot sign-up board for DJ sets and gogo-stage
dancers — the host sets it up *once* in the DJ/dancer channel, and everyone
else signs up for their own slots with a single click. No host babysitting,
no double-booking, and every change is visible to the whole channel in real
time.

**How it works:**

1. The host runs
   `/signup <start> <slots> <slot_time> [day] [doors_lead] [limit_per_slot] [limit_per_person]`.
   Example: `/signup 10pm ET 8 1h friday 15 1 2`.
2. The bot opens an **"Event Creation"** window (a modal) **prefilled** with
   the exact board layout:

   ```
   # New Event
   > #1, <t:1788670800:t>
   > #2, <t:1788674400:t>
   > …
   - Doors Open: **__<t:…:F>__** (<t:…:R>)
   - Each slot is 1 hour long
   - Click on the slots you want to sign-up for
   ```

   The host reviews or tweaks anything (title, times, limits) and submits.
3. The bot posts the board with two buttons underneath:
   - **Pick Slot** — opens a **private prompt** (only that user sees it) with
     one button per slot, `#1 … #N`, in order. Click `#1` and the user's name
     is placed on slot 1; clicking it again cancels. Multiple people on one
     slot are shown space-separated. Everyone in the channel sees it update
     the moment it happens.
   - **Edit Event** (host) — re-opens the Event Creation window. Saving edits
     the **same post in place** (no duplicate event) and **preserves everyone
     who has already signed up**, matching by slot timestamp.

**Time input:** the start accepts a raw unix timestamp (`1788670800`) or a
ham-time wrap (`10pm ET`). The **Doors Open** line is **auto-derived** from the
first slot's time — `doors_lead` is how many minutes before slot 1 the doors
open (default 15), so the Doors timestamp = slot 1 − `doors_lead`.
The slot-length line auto-updates and **switches to hours** past 60 minutes
("Each slot is 90 minutes long" / "2 hours long").

**Persistence:** assignments live in `signup_events.json` (git-ignored,
per-machine) and the event's buttons are **re-registered on every bot
restart**, so a board keeps working across restarts. State is shared across
all servers the bot is in, keyed by the post's message id.

> **Why buttons and not a form?** The whole point is that a *host* sets it up
> and *everyone else* self-serves. A button the whole channel can see is the
> lowest-friction way to let 400 DJs and dancers grab slots without a human
> watching the channel, and it makes the current state of every slot
> impossible to miss.
---

## Suggesting a new DJ (community)

A lot of communities have DJs that aren't on the master list yet. Rather than
letting random people edit the shared sheet, `/adddj` lets **any member of any
server the bot is in** submit a suggestion — the bot formats it in the master
list's style and drops it somewhere the maintainer can review.

**How it works:**

1. The user runs `/adddj` with a DJ name + one link, plus optional genres and
   availability.
2. If the link is a VRCDN URL (RTSP or MPEG-TS), the bot expands it to all
   three versions using the **exact same logic as `/vrcdn`** — so the
   suggestion arrives fully formatted, ready to paste into the sheet.
   (Twitch / other links are kept as-is.)
3. The bot POSTs the formatted block as a **GitHub issue** on the target repo,
   labelled `dj-addition`.
4. The maintainer opens the **Pending DJ additions** card on the dashboard,
   sees the badge, copies the formatted block into the master list, and closes
   the issue.

**For the bot operator (you):**

- Set `GH_TOKEN` in `bot.env` (see [the Configure step in docs/setup-and-use.md](setup-and-use.md)). Without it the
  command is still visible but tells users it can't submit.
- The dashboard's **Pending DJ additions** card polls the GitHub Issues API
  (read-only, no token needed) and shows:
  - a **new** badge when a suggestion is fresh (less than 7 days old),
  - the DJ name, how many days it's been pending, and a jump-to-issue link,
  - a **Check now** button if you want to force a fresh poll.
- After copying a suggestion into the sheet, **close the issue** from GitHub —
  the badge disappears on the next poll.

**For the bot user (anyone in a server the bot is in):**

```
/adddj name: "Aurora"
     link: rtspt://stream.vrcdn.live/live/aurora
     genres: "House, Techno"           (optional)
     availability: "Fri/Sat nights"   (optional)
```

You'll get a confirmation with the issue number and a link to it. The
maintainer handles the rest — you don't need a GitHub account or a Google
Sheet to suggest a DJ. 🎧

**For forkers:** by default `/adddj` targets the *upstream* repo
(`NWinnVR/VRCDJ_bot`) — the maintainer's drop box. If you want suggestions to
land on **your own** fork instead, set `VRCDJ_REPO` in `bot.env` to
`<your-user>/<your-repo>` (or `https://github.com/<user>/<repo>`) and point
`GH_TOKEN` at a token with issue-write access on that repo.

> **Why GitHub issues and not pastebin / the sheet?** GitHub is free, has no
> rate limit at this scale, keeps the history in the same repo as the bot, and
> doesn't require the user to have any account. Issues are the maintainer's
> inbox, and they're closed once the DJ is added — so the "pending" state
> never drifts out of sync.
---

## Rich Presence — live session uptime in Discord

**New in 1.10.0.** The bot now advertises a **Discord Rich Presence** status so
you can see, right in the **member list**, how long the bot has been up for its
current session — no channel, no command, just a glance at the bot's name in
any server it's in.

- **What people see** — a custom status under the bot's name reading
  `up: H:MM:SS · v1.10.0`, e.g. `up: 3:42:11 · v1.10.0`. It **ticks** — the
  seconds roll forward every minute (Discord's presence refresh cadence).
- **What it measures** — the **current session** uptime (since the bot
  process last started), the same value the dashboard's *Bot process* card
  shows. A bot that's been restarted resets to `0:00:00`, so the number is a
  true "how long has it been up this run" read, not a lifetime clock.
- **Turn it off** — `/presence off` (and `/presence on` to bring it back). It's
  a cosmetic status only: it never affects commands, and it's safe for any
  member to toggle.
- **Dashboard control** — the **👀 Presence** switch on the *Bot process* card
  does the same thing with a live preview of the status text. The change
  applies within a minute, **no restart needed**.
- **Default** — **on**. First-time installs show the counter out of the box.
  The setting persists in `bot_config.json` (git-ignored) across restarts.
- **How it works** — a lightweight background task calls
  `client.change_presence(activity=discord.Activity(type=custom, …))` once a
  minute and again immediately on a toggle. It reuses the same
  `ready_at` session timestamp the dashboard uses, and is fully cancelled on
  clean shutdown.

> **Note:** the member-list status is a *global* bot presence — it's the same
> text in every server the bot is in (that's how Discord presence works), which
> is exactly why it doubles as a nice at-a-glance "the bot is alive and has
> been up a while" signal.
