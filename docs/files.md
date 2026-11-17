← [README](../README.md) · [Wiki index](index.md)

# Files — what's in this repo

The tree below is the whole app (plus the standard support files):

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

| File | What it does |
|---|---|
| `bot.py` | The Discord bot — DJ lookups, VRCDN expansion, time-slots, auto-lineup (no LLM). |
| `dashboard.py` | The Flask LAN dashboard + the bot's process manager (start / stop / restart, watchdog). |
| `version.py` | The version number — single source of truth; shown on the dashboard and in Releases. |
| `release.py` | Bump + tag + GitHub-release tooling (`--message`, `--minor`, `--major`, `--dry-run`). |
| `scrub.py` | Output sanitizer (no ANSI / paths / control bytes) for clean logs. |
| `envload.py` | Tiny dependency-free `.env` loader. |
| `dj_sheet.py` | Reads/refreshes the DJ master list from a public Google Sheet (CSV, no API key). |
| `timeslots.py` | Time-slot block builder (DST-aware timezone math). |
| `djlineup.py` | A–Z slot labeling for event lines. |
| `vrcdn.py` | VRCDN URL parsing / expansion into all three link versions. |
| `event_post.py` | The event auto-lineup logic (bound channels, `<t:…:t>` timestamps). |
| `discord_send.py` | Chunked Discord message sender (splits long posts safely). |
| `dj_add.py` | `/adddj` — classify + format a DJ suggestion (pure logic). |
| `gh_add.py` | `/adddj` — GitHub issue drop-box (create suggestions + list pending ones). |
| `verify_adddj.py` | End-to-end test of the `/adddj` → GitHub → dashboard pipeline. |
| `bot_config.py` | Config (sheet URL, defaults) + the small persisted settings. |
| `bot_state.json` / `bot_state.py` | Runtime state file + the duplicate-instance guard module. |
| `botlog.py` | Structured activity log (the dashboard's LOG pane reads it). |
| `repo_version.py` | Git commit stamping for the "last update" card. |
| `templates/` | The dashboard HTML (dark theme, mobile-friendly). |
| `assets/` | The bot logo — served as header mark + favicon (PNG + ICO). |
| `bot.env.example` | Copy to `bot.env`, fill in your token (no secrets committed). |
| `.gitignore` | Keeps `bot.env` + state files + `venv/` out of git. |
| `requirements.txt` | `discord.py` + `Flask` (+ `tzdata` on Windows) — that's all. |
| `install.bat` / `run.sh` | One-time setup: creates the venv, installs deps, makes friendly process copies. |
| `run_dashboard.bat` / `run_bot.bat` | Windows launchers (dashboard + bot; bot only). |

> **Rule for keeping it current:** granular detail lands *here*; the README keeps only quick-guide one-liners + links to these pages.
