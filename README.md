# 🎧 VRCDJ_bot

**A portable, open-source Discord bot for VRChat DJ communities.**

Look up DJs from a shared master list, expand any VRCDN URL into all its
link versions, and build DJ event time-slots in one message — no LLM, no
accounts, no cloud, just your own Discord bot. Built to be self-hosted by
**any community** that wants the same tools the author uses.

> **v1.8** — versioning policy: the *second* number goes up with every add or
> fix (`1.0 → 1.1 → 1.2 …`); the *first* number only changes on a major
> overhaul. See [Releases](#releasing--updating) and [`version.py`](version.py).
>
> **What's new in 1.8:** **Activity-log timestamps now show the correct time
> on every device.** Log entries were written in the server's local time with
> no timezone marker, so a phone, tablet, or box in a different timezone
> would display them shifted — the "random / goes backwards" times. Entries
> are now stamped in explicit **UTC**, so every browser converts them to *your*
> local time correctly, and the sequence is always monotonic. **Also in this
> release line:** the dashboard now **shows in Task Manager like WyBot**
> (titled `VRCDJ_bot — Control Dashboard · v… · pid … · uptime`, ticking live),
> and the **Updates** card stamps a **last-checked time** so you can see on a
> headless box that it really is checking.

---

## 🚀 Add it to your Discord (fastest way)

The quickest path is a **one-click install** — no code, no terminal, no server
of your own. This adds the author's live bot instance to your server:

**👉 [Add VRCDJ_bot to Discord](https://discord.com/oauth2/authorize?client_id=1547554560049156116)**

What to do on the Discord page it opens:

1. Click **Authorize** — Discord will ask for permission to add the bot.
2. Make sure the **Bot** scope is checked and the **Send Messages** permission
   is on (it's pre-set; no need to hunt around).
3. Pick the server you want to add it to and click **Continue** / **Confirm**.
4. You're in — type `/` in any text channel and the commands appear:
   `/dj`, `/vrcdn`, `/djlineup`, `/timeslots`, `/adddj`, `/status`, `/help`.

> **Good to know:** this links the *author's* running bot, so it already works
> and needs no maintenance from you. DJ lookups pull from the shared master
> list, and `/adddj` suggestions route to the author's review queue — a
> community feature, not a leak of your data. If you'd rather run your **own
> copy** (own token, own list, own review queue), skip ahead to
> [Quick start](#quick-start) for a self-hosted install.

---

## What it does

A small set of **slash commands** aimed at running VRChat DJ events:

| Command | What it does |
|---|---|
| `/dj <name>` | Look up one or more DJs (comma-separated) — pulls their Twitch / VRCDN links from the master list. |
| `/vrcdn <url or name>` | Take one VRCDN URL (RTSP / MPEG-TS) or a streamer name and expand it into **all three** link versions (RTSP, MPEG-TS, preview). |
| `/djlineup` | Label an event's time-slot lines **A–Z** so DJs sign up by letter, not by time. |
| `/timeslots` | Generate a full DJ time-slot block from a start time, day, and slot count — the bot does the timezone math (DST-aware). |
| `/adddj` | **Suggest a new DJ** for the master list — name + link required, genres / availability optional. A VRCDN link is expanded to all three versions for you. See [Suggesting a new DJ](#suggesting-a-new-dj-community). |
| `/status` | Bot availability, DJ-list freshness, and the version. |
| `/help` | List every command. |
| `/dj-refresh` | Re-pull the DJ list from the Google Sheet right now. |

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

## Features

- 🔒 **Portable & self-hosted.** One folder, one `venv`, two dependencies
  (`discord.py` + `Flask`). Runs on Windows (primary), macOS, or Linux.
  No cloud, no API keys, no LLM.
- 🌐 **LAN web dashboard** (dark theme) to watch and control the bot from
  your network: start / stop / restart, toggle the kill-switch, re-pull the
  DJ list, view the live activity log, and **check for & apply updates**.
- ♻️ **Watchdog.** The dashboard keeps the bot alive — if it crashes, it's
  restarted automatically.
- 🔐 **Secure by default.** Password-gated dashboard, CSRF tokens on every
  state change, rate-limited login, secure headers, no shell-injection
  surface, and the Discord token **never leaves `bot.env`** (git-ignored).
- 🗣 **Parallel-safe.** A duplicate-instance guard stops a second copy of the
  *same* bot from starting (so you can't double-post) — while still letting
  a *different* bot run alongside it.
- 📈 **Versioned.** One source of truth for the version, shown in the
  dashboard and used for GitHub Releases.

---

## Requirements

- **Python 3.10 or newer** (3.11/3.12/3.13 all fine).
- A **Discord bot application** (free, see [Setup](#1-create-your-discord-bot)).
- Outbound internet access (Discord gateway + the public DJ Google Sheet).
- That's it. Only `discord.py` and `Flask` are installed.

---

## Quick start

```text
1.  Install Python 3.10+ (Windows: tick "Add python.exe to PATH").
2.  Get this folder onto the machine that will run the bot.
3.  Windows:  double-click  install.bat        (creates venv + deps)
    macOS/Lin: ./run.sh                          (creates venv + deps)
4.  Edit  bot.env  → paste your DISCORD_BOT_TOKEN.
5.  Windows:  double-click  run_dashboard.bat   (starts dashboard + bot)
    macOS/Lin: ./run.sh
6.  Open the printed LAN URL in a browser, sign in, and you're live. 🎧
```

That's the whole setup. Everything after that is optional.

---

## Detailed setup

### 1. Create your Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   → **New Application** → give it any name (e.g. `VRCDJ_bot`).
2. Open the **Bot** tab → **Reset Token** → copy the token.
   - This token is **secret**. Anyone with it can control your bot.
   - You'll paste it into `bot.env` in a moment. It is never uploaded anywhere.
3. Still on the **Bot** tab, enable the **Message Content Intent**
   (required for the event auto-lineup). Leave voice intents off.
4. Open the **OAuth2** tab → **URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Embed Links`,
     (and `Read Message History` if you want the bot to see the channel it
     auto-lineups in).
5. Copy the generated URL, open it in a browser, and **invite the bot** to
   your server.

> **Who can use the commands?** By default, every member of the server can run
> the read-only commands (`/dj`, `/vrcdn`, `/djlineup`, `/timeslots`,
> `/status`, `/help`) — and `/adddj`, if the operator enabled it (see
> [Suggesting a new DJ](#suggesting-a-new-dj-community)). The one *control*
> command (`/dj-refresh`) should be restricted in the Developer Portal under
> **Application Commands → Permissions** (set it to your staff role) — or just
> don't assign it to the server. There is no `/on` / `/off` slash command at
> all; the only kill-switch is the password-protected **dashboard** (Start /
> Stop). The bot itself has no hard-coded staff list, so Discord is the single
> place you control access.

### 2. Install

**Windows**

```bat
install.bat
```

**macOS / Linux**

```bash
./run.sh          # first run does the setup for you
```

Both create a `venv/` and install `discord.py` + `Flask`. If you'd rather do
it by hand:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt    # Windows: venv\Scripts\pip
```

### 3. Configure

Open `bot.env` (created from `bot.env.example` by the install) and add your
token:

```env
DISCORD_BOT_TOKEN=your-token-here
# optional:
DASHBOARD_PASSWORD=a-long-random-password
# DASHBOARD_PORT=8720

# /adddj (optional but recommended) — a GitHub token with `repo` scope on the
# target repo. Lets the bot post DJ suggestions as issues and lets the
# dashboard show them. See "Suggesting a new DJ" below.
#GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- `DISCORD_BOT_TOKEN` — **required**. From step 1.2.
- `DASHBOARD_PASSWORD` — optional. If blank, the dashboard prints a random
  one **once** on first start. Set your own for a server.
- `DASHBOARD_PORT` — optional, default `8720`.
- `GH_TOKEN` — optional. Needed for `/adddj` (and the dashboard's Pending DJ
  additions checker). A *classic* GitHub token with `repo` scope on the target
  repo is the simplest choice; a *fine-grained* token with `Issues: Read &
  write` also works. Without it, `/adddj` tells the user it can't submit, and
  the checker shows an error. **Never commit `bot.env`** — it's git-ignored.

### 4. Run

**Windows** → double-click `run_dashboard.bat`.
**macOS / Linux** → `./run.sh`.

You'll see:

```
[dashboard] VRCDJ_bot v1.8
[dashboard] Dashboard: http://192.168.x.x:8720
[bot] ready as YourBot#1234 — 8 commands synced (v1.8)
[bot] DJ list: 402 entries — ...
```

Open `http://192.168.x.x:8720` (your LAN IP) in a browser on any device on the
network, sign in with the password, and you have the live dashboard.

> **Just the bot, no dashboard?** Use `run_bot.bat` (or
> `venv/bin/python bot.py`).

---

## The DJ master list

The bot reads its DJ list from a **public Google Sheet** ("anyone with the
link can view") and downloads it as CSV — **no API key needed**.

**By default, every install — including someone who downloads the repo and
runs their own copy — pulls the *same* community master list that ships in the
code.** It's the largest public list of its kind, so that's the point: you get
the full list out of the box, no configuration required.

To point the bot at a **different** sheet (your community's DJs):

1. Make your Google Sheet shareable with "Anyone with the link → Viewer".
2. Set `DJ_SHEET_URL` in `bot.env` to your sheet's CSV link — the
   publish-to-web form is the most reliable:
   `https://docs.google.com/spreadsheets/d/e/<ID>/pub?output=csv`
   (the `…/export?format=csv` form is also supported).
3. Restart, or hit **Re-pull the DJ sheet** on the dashboard.

Expected columns (case-insensitive, extra columns ignored): `Name`, plus any
of `Twitch`, `VRC`, `VRCDN`, `RTSP`, `MPEG`, `Preview`. The bot strips
labels and normalizes URLs, so a human-friendly sheet is fine.

The list is cached locally and auto-refreshed in the background (it will
re-pull if it goes stale).

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

- Set `GH_TOKEN` in `bot.env` (see [Configure](#3-configure)). Without it the
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

## The dashboard (LAN web portal)

Dark-themed, mobile-friendly, password-gated. Sections (in this order):

- **Bot process** — running / stopped, PID, uptime, and the **Start / Stop /
  Restart** buttons (inline on the status row — **Start / Stop is the
  kill-switch**, the only place you can pause or stop the bot; the watchdog
  keeps it alive). Plus **usage counters**: 💬 replies this session, 📈
  replies all-time (persistent, with a Reset button), and 🌐 how many servers
  the bot is running in.
- **Activity log** — recent lookups, DJ suggestions, refreshes, toggles, and
  errors.
- **DJ list & lookups** — DJ count, list freshness, and a one-click
  **Re-pull** button.
- **Pending DJ additions** — the `/adddj` review queue: how many community
  suggestions are waiting, a **new** badge on fresh ones, each entry with the
  DJ's name + a jump-to-issue link, and a **Check now** button. See
  [Suggesting a new DJ](#suggesting-a-new-dj-community).
- **Updates & version** — current version, **Check for updates** (auto-runs on
  load and every 10 min; queries GitHub Releases), **Update & restart** (pulls
  the latest code and restarts the bot — no manual file copying), and
  **Restart All** (kills the bot *and* the dashboard, then relaunches a fresh
  dashboard — one-click headless management).

The dashboard binds to `0.0.0.0` so it's reachable across your local network.
**Keep it there** — don't expose the dashboard port to the public internet.
Use the strong password; it's the gate to the controls.

---

## Releasing & updating

### How versioning works

The version lives in exactly one place: [`version.py`](version.py)
(`VERSION = "1.8"`). It flows to the dashboard, the bot's startup log, and
GitHub Releases.

- **Minor** (default): every add or fix bumps the *second* number:
  `1.0 → 1.1 → 1.2 …`
- **Major**: the *first* number, `1.x → 2.0`, only on a big overhaul — and only
  when the maintainer decides it.

### Cutting a release

From the project folder:

```bash
venv/bin/python release.py --message "Add /foo command"   # minor bump → 1.1
venv/bin/python release.py --dry-run                       # preview, change nothing
venv/bin/python release.py --major                         # 1.x → 2.0 (rare)
```

`release.py` bumps `version.py`, commits, tags `v<new>`, and (if the `gh` CLI
and network are available) creates a **GitHub Release** so it shows up in the
Releases section and is downloadable. See
[GitHub Releases](https://github.com/NWinnVR/VRCDJ_bot/releases).

### Updating a running bot

On the machine where the bot runs, either:

- click **Check for updates → Update & restart** on the dashboard, or
- **Restart All** (same card) to flush the bot *and* the dashboard in one shot
  and come back on the latest code — the cleanest path on a **headless server**,
  or
- `git pull && restart` (or just re-run the launcher).

Because it's a public repo, the update path is plain `git pull` — no private
file copying, no zips.

---

## Changelog

- **v1.8** — **Activity-log times are now correct on every device.**
  - **Fixed the "random / goes backwards" log timestamps.** Entries were
    written in the *server's* local time with **no timezone marker**
    (`2026-09-10T15:03:05.130`). A browser on a device set to a different
    zone (phone, tablet, another box) interpreted that naive string with *its*
    offset, so the same entry showed a different time depending on where you
    opened the dashboard — and could even look like it "went backwards" against
    your wall clock. Entries are now stamped in explicit **UTC**
    (`2026-09-10T20:03:05.130Z`), which every browser converts to the viewer's
    local time correctly, and the sequence is always monotonic. `_parse_ts`
    keeps reading both old and new entries, so nothing breaks on the cutover.
- **v1.7** — **Task Manager visibility + last-checked timestamp.**
  - **Shows in Task Manager like WyBot** — the dashboard now sets its console
    window title to
    `VRCDJ_bot — Control Dashboard · v<ver> · pid <pid> · <uptime>`, refreshed
    every 30 s with a live ticking uptime counter. It appears in the Task
    Manager **Apps** tab with its PID, just like the WyBot dashboard.
  - **Last-checked time** — the **Updates & Version** card now stamps
    `last checked: HH:MM:SS` on every auto/manual check, so on a headless box
    you can glance and see it really is checking.
- **v1.6** — **Reliable control: Stop / Restart / Update that actually work,
  plus one-click headless management.**
  - **Fixed the "another bot is already running" loop** — the real bug. The bot
    is launched through `vrcjb.exe` (a copy of `python.exe` whose Windows
    launcher re-execs a *child* `python.exe`). The dashboard tracked the
    launcher PID, so **Stop / Restart / Update killed only the launcher and
    orphaned the real bot** — which kept beating the heartbeat and refused every
    respawn. `BotManager.stop()` now kills the **whole process tree**
    (`taskkill /F /T`, with a targeted fallback) and **waits for the heartbeat
    to go stale** before spawning a replacement, so a respawn can't collide with
    a ghost.
  - **New "Restart All" button** (Updates & Version card) — one click **kills
    the bot and the dashboard**, spawns a fresh detached dashboard, and exits.
    Built for a **headless server**: no window to click, no manual relaunch.
    CSRF-protected like every other state change.
  - **Self-restart is port-safe** — the relaunched dashboard waits for the port
    to free before binding, so it can't crash on a "port already in use" race.
  - **Updates card checks by itself** — the check now runs **on page load and
    every 10 minutes** (shared `checkUpdates()`), instead of sitting on
    "checking…" until you clicked **Check For Updates**. **Update & Restart**
    is disabled while you're already up to date.
  - **How to kill a stuck bot from the dashboard (headless):** Stop/Restart now
    works (they use the tree-kill), or press **Restart All** for a full flush.
    Both are safe to run unattended — the new process is fully detached from
    the old one.
- **v1.5** — **Reliability + a much richer dashboard.**
  - **`/adddj` GitHub 401 fixed** — the bot now authenticates to GitHub with a
    proper token, and the error message **self-diagnoses** (tells you exactly
    what's wrong and how to fix it) instead of surfacing a bare "HTTP 401".
  - **Activity Log shows *who* and *where*** — the username and server name
    were previously never displayed (a field mismatch); now every entry shows
    them. `/status`, `/help` and **bot joins/leaves** are now logged too.
  - **Log toolbar** — a compact one-line row of **filter chips** (All ·
    Errors · Commands · System) + a **free-text search** box, plus a
    "showing X of Y" counter. Log history cap raised **100 → 400** entries.
  - **Pending DJ Additions** card moved up to sit directly **below the log**.
  - **README** — one-click **"Add it to your Discord"** install section near the
    top with a step-by-step walkthrough.
  - **Dashboard title-cased** throughout (Bot Process, Activity Log, Updates &
    Version, stat labels, buttons, Sign Out).
  - Ships the `verify_adddj.py` end-to-end pipeline test.
- **v1.4** — `/adddj` community DJ suggestions (name + link required, genres
  / availability optional; a VRCDN link auto-expands to all three versions
  using the `/vrcdn` engine); GitHub **issue** drop-box (no pastebin, no sheet
  editing, no sheet access needed); dashboard **Pending DJ additions** checker
  (new badge + Check now); dashboard layout tidy (Start / Stop / Restart inline
  on the status row, activity log moved under Bot process); **bundles the full
  feature set into the released code** — usage counters, the `/on`·`/off`
  removal, and the distinct process names; README + setup + forker guidance
  refresh.
- **v1.3** — usage counters (replies this session, all-time total with a
  Reset button, live server count).
- **v1.2** — dashboard-hang fix (git-spawn robustness); `/on`·`/off` removed
  (the dashboard is the only kill-switch); distinct process names
  (`vrcjd.exe` / `vrcjb.exe`) so it's easy to tell from other bots in Task
  Manager; default master list.
- **v1.1** — DJ sheet-URL wiring fix (the sheet URL now flows through
  `dj_sheet`), runtime cache ignored.
- **v1.0** — initial public release: `/dj`, `/vrcdn`, `/djlineup`,
  `/timeslots`, `/status`, `/help`, `/dj-refresh` (plus the then-current
  `/on`·`/off`), the event auto-lineup, the LAN web dashboard, portable
  Windows/macOS/Linux, and the duplicate-instance guard.

> **Note on v1.2 / v1.3:** those release *notes* describe the features, but
> the earlier tags were cut before the code for them was committed. **v1.4 is
> the first release that actually ships the complete feature set** — if you
> already downloaded a v1.2 / v1.3 zip, just update to v1.4 (or `git pull` on
> `main`).

---

## Security notes

This is a **public** repo, and the bot typically runs on a home server — so
it's built to be hard to abuse from outside:

- **Secrets never leave the box.** The Discord token lives only in `bot.env`,
  which is **git-ignored**. `bot.env.example` (committed) has no secrets.
- **No injection surface.** All subprocess calls (git, python) use argument
  lists with `shell=False`. Nothing user-supplied is ever shell-interpolated.
- **Dashboard is gated.** Password login (PBKDF2), CSRF tokens on every
  state-changing POST, per-IP login rate limiting, and secure headers
  (CSP, `X-Frame-Options: DENY`, `nosniff`). Unauthenticated API calls get a
  JSON 401, not an HTML page.
- **Binds LAN by default.** `0.0.0.0` for your local network; keep the port
  closed to the internet in your firewall.
- **Duplicate-instance guard.** A second copy of the *same* bot refuses to
  start (exit 77), so you can't double-run and spam a server. A *different*
  bot (e.g. your other project) can run alongside it fine.
- **No inbound ports for the bot itself.** It only makes outbound connections
  (Discord gateway, the public Google Sheet). The only listening port is the
  dashboard, and only if you start it.

**You are responsible for** the strength of `DASHBOARD_PASSWORD` and for
keeping the dashboard port off the public internet.

---

## Project layout

```
VRCDJ_bot/
├── bot.py               # the Discord bot (DJ + time-slot commands, no LLM)
├── dashboard.py         # the Flask LAN dashboard + bot process manager
├── version.py           # the version number (single source of truth)
├── release.py           # bump + tag + GitHub release tooling
├── scrub.py             # output sanitizer (no ANSI / paths / control bytes)
├── envload.py           # tiny dependency-free .env loader
│
├── dj_sheet.py          # read/refresh the DJ master list (public CSV)
├── timeslots.py         # time-slot block builder (DST-aware)
├── djlineup.py          # A–Z slot labeling
├── vrcdn.py             # VRCDN URL parsing / expansion
├── event_post.py        # event auto-lineup logic
├── discord_send.py      # chunked Discord message sender
├── dj_add.py            # /adddj — classify + format a DJ suggestion (pure)
├── gh_add.py            # /adddj — GitHub issue drop-box (create + list)
├── verify_adddj.py      # end-to-end test of the /adddj → GitHub → dashboard pipeline
├── bot_config.py        # config (sheet URL, defaults)
├── bot_state.py         # runtime state + duplicate-instance guard
├── botlog.py            # structured activity log
├── repo_version.py      # git commit stamping
│
├── templates/           # the dashboard HTML (dark theme)
├── bot.env.example      # copy to bot.env, fill in your token  (no secrets)
├── .gitignore           # keeps bot.env + state + venv out of git
├── requirements.txt     # discord.py + Flask (that's all)
├── install.bat          # Windows one-time setup
├── run_dashboard.bat    # Windows: start dashboard + bot  (primary)
├── run_bot.bat          # Windows: start bot only
└── run.sh               # macOS / Linux: setup + start
```

---

## Running in parallel with another bot

Yes — this is a whole separate application with its own token, its own state
files, and its own dashboard port. Run it next to any other Discord bot you
have. The only guard is against a *second copy of VRCDJ_bot itself*
(duplicate-instance), which is what you want.

If two bots would use the **same dashboard port**, set a different
`DASHBOARD_PORT` for one of them.

### Telling VRCDJ_bot apart from other bots in Task Manager

The launchers run the code under **distinct process names** instead of a
generic `python.exe`, so when several bots are on one machine you can see at a
glance which is which:

| Process | What it is |
|---|---|
| `vrcjd.exe` | the **dashboard** (the Flask web portal) |
| `vrcjb.exe` | the **bot** (the Discord process the dashboard spawns) |

Any bot you don't rename (e.g. one running as plain `python.exe`) is *not*
VRCDJ_bot. These names are just friendly copies of the venv's interpreter
inside `venv/`, recreated automatically by `install.bat` / `run.sh`, so a fresh
machine gets them too.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DISCORD_BOT_TOKEN missing` | Add your token to `bot.env` and restart. |
| Bot connects but commands don't appear | Wait up to ~60 s for global command sync; check the Developer Portal that the bot is invited and the Message Content Intent is on. |
| Dashboard won't open from another device | Make sure both devices are on the same network; use the LAN IP (not `localhost`); check your firewall allows the port. |
| `port already in use` | Set a different `DASHBOARD_PORT` in `bot.env`. |
| `another VRCDJ_bot is already running (PID …)` | You started a second copy of the *same* bot. Stop the first one, or you're fine — the guard did its job. |
| `/dj` finds nobody | Run `/dj-refresh`, and confirm `DJ_SHEET_URL` points at a viewable sheet with a `Name` column. |
| PyNaCl warning on start | Harmless — voice support is an optional extra this bot doesn't use. |

---

## License

MIT — do whatever you want with it; it's yours to fork and run for your own
community. See [LICENSE](LICENSE).

## Credits

Built for the VRChat DJ scene. If you run it for your community and it's
useful, a shout-out is all that's asked. 🎧
