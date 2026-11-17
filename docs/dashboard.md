← [README](../README.md) · [Wiki index](index.md)

# The dashboard — full details

Dark-themed, mobile-friendly, password-gated. Sections (in this order):

- **Bot process** — running / stopped, PID, uptime, and the **Start / Stop /
  Restart** buttons (inline on the status row — **Start / Stop is the
  kill-switch**, the only place you can pause or stop the bot; the watchdog
  keeps it alive). Plus **usage counters**: 💬 replies this session, 📈
  replies all-time (persistent, with a Reset button), and 🌐 how many servers
  the bot is running in. And a **👀 Presence** switch — toggles the bot's
  live **session-uptime status in the Discord member list** (Rich Presence) on
  and off, with a live preview of the status text; the bot applies the change
  within a minute, no restart needed. See commands.md — Rich Presence.
- **Activity log** — recent lookups, DJ suggestions, refreshes, toggles, and
  errors. Every line carries a **`YYYY-MM-DD HH:MM:SS` timestamp** (date + time,
  local) so a bot running for weeks or months stays readable; lines are
  **color-coded by category** — **red** = errors, **blue** = commands, **violet**
  = system — matching the filter chips above. The one-line toolbar has
  **All · Errors · Commands · System** chips (each chip lights up in its
  category color), a **🗑 Clear** button (empties the on-screen log; the history
  is rotated to `bot_activity.jsonl.1` on disk), and a **free-text search** box.
- **Custom icon** — the dashboard and the sign-in page use the bot's own logo
  as both the **header mark** and the **browser favicon** (PNG + ICO, served from
  `assets/`).
- **Header & footer** — the header shows the **version**, a **live
  `up: H:MM:SS` uptime** for the dashboard process (ticks every second), and the
  current **commit hash** in that order. The footer reads *"built by
  [NWinn](https://linktr.ee/nwinn) for the community to make managing events
  easier"* — **NWinn** is a hyperlink to the author's Linktree (opens in a new
  tab), styled **blue and underlined** so it reads as a link without hovering.
- **DJ list & lookups** — DJ count, list freshness, and a one-click
  **Re-pull** button.
- **Pending DJ additions** — the `/adddj` review queue: how many community
  suggestions are waiting, a **new** badge on fresh ones, each entry with the
  DJ's name + a jump-to-issue link, and a **Check now** button. See
  [the /adddj section](commands.md).
- **Updates & version** — current version, **Check for updates** (auto-runs on
  load and every 10 min; queries GitHub Releases), **Update & restart** (pulls
  the latest code and restarts the bot — no manual file copying), and
  **Restart All** (kills the bot *and* the dashboard, then relaunches a fresh
  dashboard — one-click headless management).

The dashboard binds to `0.0.0.0` so it's reachable across your local network.
**Keep it there** — don't expose the dashboard port to the public internet.
Use the strong password; it's the gate to the controls.
<img width="967" height="863" alt="VRCDJ_bot Dashboard screenshot" src="https://github.com/user-attachments/assets/ff46ba52-1999-48c2-bd83-032b94685de1" />
<img width="948" height="791" alt="image" src="https://github.com/user-attachments/assets/a88ac30c-0943-4cfe-8303-c0fb7ed4fed0" />
