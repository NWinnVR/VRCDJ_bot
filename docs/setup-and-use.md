← [README](../README.md) · [Wiki index](index.md)

# First-time setup & everyday use

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
> [the /adddj section in commands.md](commands.md)). The one *control*
> command (`/dj-refresh`) should be restricted in the Developer Portal under
> **Application Commands → Permissions** (set it to your staff role) — or just
> don't assign it to the server. `/presence` is cosmetic (it only shows/hides
> the bot's uptime status) and is safe to leave open. There is no `/on` /
> `/off` slash command at
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
# dashboard show them. See [the /adddj section in commands.md](commands.md).
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
[dashboard] VRCDJ_bot v1.9
[dashboard] Dashboard: http://192.168.x.x:8720
[bot] ready as YourBot#1234 — 8 commands synced (v1.9)
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

## Releasing & updating

### How versioning works

The version lives in exactly one place: [`../version.py`](../version.py)
(`VERSION = "1.9.1"`, `MAJOR.MINOR.PATCH`). It flows to the dashboard, the
bot's startup log, and GitHub Releases.

- **Patch** (default): every small fix or tweak bumps the *third* number:
  `1.9 → 1.9.1 → 1.9.2 …`
- **Minor**: a feature or notable add bumps the *second* number and resets the
  patch: `1.9.x → 1.10.0`
- **Major**: the *first* number, `1.x → 2.0.0`, only on a big overhaul — and
  only when the maintainer decides it.

### Cutting a release

From the project folder:

```bash
venv/bin/python release.py --message "Add /foo command"   # patch bump → 1.9.1
venv/bin/python release.py --minor                        # minor bump → 1.10.0
venv/bin/python release.py --major                        # 1.x → 2.0.0 (rare)
venv/bin/python release.py --dry-run                      # preview, change nothing
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
