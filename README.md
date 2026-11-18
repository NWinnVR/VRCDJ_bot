# <img width="32" height="32" alt="VRCDJ_bot thumbnail tiny" src="https://github.com/user-attachments/assets/7f6b48f4-3acb-4835-8ac8-45420f28c92c" /> VRCDJ_bot

**A portable, open-source Discord bot for VRChat DJ communities.**

Look up DJs from a shared master list, expand any VRCDN URL into all its
link versions, and build DJ event time-slots in one message — no LLM, no
accounts, no cloud, just your own Discord bot. Built to be self-hosted by
**any community** that wants the same tools the author uses.

> **v1.10.0** — versioning is **3-point** (`MAJOR.MINOR.PATCH`): the
> *third* number goes up with every small fix/tweak (`1.9 → 1.9.1 → 1.9.2 …`),
> the *second* on a notable add (`1.9.x → 1.10.0`), and the *first* only on a
> major overhaul. See [the Releasing & updating page in the wiki](docs/setup-and-use.md) and [`version.py`](version.py).
>
> **What's new in 1.13.0:** **`/timeslots` now takes natural-language starts**
> (`fri at 10pm EST`, `tonight 9`) — and when a start is ambiguous, the bot asks
> **one quick button question** with timestamped candidates instead of guessing.
> **`/signup` hosts can add free-form text**: extra bullets above the slot list
> (special-guest notes, doors-close times) plus a short note on any slot line —
> typically the DJ's name — that rides along through picks, edits and restarts.
> 1.12.x added input hints on every command (`v1.12.4`) and the **live
> `/signup` board** itself (one-click slots, real-time for everyone, host
> Edit-Event). 1.11.0 fixed Rich Presence reliability, added the 🔴 "Errors
> This Session" counter and a readable startup trace; 1.10.0 added the member-list
> session-uptime presence (`up: H:MM:SS · v1.10.0`), `/presence on|off`, the
> dashboard switch, and the blue + underlined footer **NWinn link**. See the
> [changelog](docs/changelog.md) for the full history.

---

## Add it to your Discord directly (fastest way)

The quickest path is a **one-click install** — no code, no terminal, no server
of your own. This adds the author's live bot instance to your server:

**[Add VRCDJ_bot to Discord](https://discord.com/oauth2/authorize?client_id=1547554560049156116)**

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

## What it does

A small set of **slash commands** aimed at running VRChat DJ events:

| Command | In one line |
|---|---|
| `/dj <name>` | Look up DJs — their Twitch / VRCDN links from the master list. |
| `/vrcdn <url or name>` | Expand any VRCDN URL into all three link versions (RTSP, MPEG-TS, preview). |
| `/djlineup` | Label a time-slot line **A–Z** so DJs sign up by letter. |
| `/timeslots` | Generate a full DJ time-slot block — DST-aware math; natural-language starts too. |
| `/signup` | Host a live slot sign-up board — one-click slots, real-time for everyone. → [full details](docs/commands.md). |
| `/adddj` | Suggest a new DJ for the master list (reviewed via GitHub issues). → [deep dive](docs/commands.md) |
| `/status` / `/help` | Bot availability + version / every command listed. |
| `/dj-refresh` | Re-pull the DJ list from the Google Sheet now (operator-only). |
| `/presence on/off` | 👀 Show or hide the live session-uptime in the Discord member list. → [deep dive](docs/commands.md) |

> **No `/on` / `/off` slash commands** — the kill-switch is only ever the
> password-protected dashboard (Start / Stop). Every option, edge case and
> deep dive lives in the [full command reference](docs/commands.md).

## Features

- 🔒 **Portable & self-hosted.** One folder, one `venv`, three dependencies
  (`discord.py` + `Flask` + `tzdata`). Runs on Windows (primary), macOS, or Linux.
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

## The dashboard (LAN web portal)

Dark-themed, mobile-friendly, password-gated: start / stop / restart the bot,
toggle Rich Presence, re-pull the DJ list, watch the live activity log, and
**check for & apply updates** — all from a browser on your network. → [full
details](docs/dashboard.md)

<img width="967" height="863" alt="VRCDJ_bot Dashboard screenshot" src="https://github.com/user-attachments/assets/ff46ba52-1999-48c2-bd83-032b94685de1" />
<img width="948" height="791" alt="image" src="https://github.com/user-attachments/assets/a88ac30c-0943-4cfe-8303-c0fb7ed4fed0" />

---

## More docs (the wiki)

The full documentation lives in [`docs/`](docs/index.md), split out so this
README stays a quick guide:

| Page | What's on it |
|---|---|
| [Commands — full reference](docs/commands.md) | Every command with all options + edge cases, and the deep dives for `/signup`, `/adddj` and Rich Presence. |
| [The dashboard — full details](docs/dashboard.md) | Every card, counter, toggle and log line on the web portal. |
| [First-time setup & everyday use](docs/setup-and-use.md) | Step-by-step bot creation → install → configure → run; the DJ master list; releasing & updating; running in parallel with other bots. |
| [Troubleshooting](docs/troubleshooting.md) | Every "when X happens → do Y" in one place. |
| [Files — what's in this repo](docs/files.md) | The file tree + a table of every file and why it exists. |
| [Changelog](docs/changelog.md) | The complete version history, newest first. |

---

## Requirements

- **Python 3.10 or newer** (3.11/3.12/3.13 all fine).
- A **Discord bot application** (free, see [Detailed setup](docs/setup-and-use.md)).
- Outbound internet access (Discord gateway + the public DJ Google Sheet).
- That's it. Only `discord.py` and `Flask` are installed.

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

## License

MIT — do whatever you want with it; it's yours to fork and run for your own
community. See [LICENSE](LICENSE).

## Credits

<img width="2048" height="2048" alt="Wyvern Logo Full (BlackBG)" src="https://github.com/user-attachments/assets/1fe58954-32cf-471b-8fc4-898da1f29993" />

<img width="1878" height="1637" alt="26-09-14 3 Years of Wyvern Part 2 Midweek Mania TWITTER" src="https://github.com/user-attachments/assets/50a13dcb-3b09-4522-b8e9-59fdae7829a2" />



Built for the VRChat DJ scene, by NWinn to make running events easier.

