← [README](../README.md) · [Wiki index](index.md)

# Changelog

- **v1.13.0** — **The four recent WyBot fixes, ported to the public bot.**
  - **🗓 `/timeslots` natural-language starts** — `start_time` now accepts plain
    English like "fri at 10pm EST" or "tonight 9" (unix and ham-times still work).
    When a start is ambiguous, the bot asks **ONE quick button question** with up
    to three timestamped candidates ("Fri Nov 21 · 10:00 PM — pick this one") plus
    a Cancel — new `timeslots_ui.py` clarify view (the WyBot pattern).
  - **✍️ `/signup` host free-form text** — the modal block now carries extra
    bullets above the slot list (`notes`) and per-slot notes that ride along with
    each slot line (`slot_extras`): they survive creation, every pick-refresh,
    the Edit Event prefill, and retroactive edits; old events render unchanged.
  - **📝 Descriptions re-synced** — `/timeslots` + `add_public` now carry the NL
    wording (all command strings <100 chars for Discord); `/help` mentions host
    extras on `/signup`.
  - New module: `timeslots_ui.py`; e2e suite extended to **66 checks** (free-form
    round-trips included).
- **v1.12.0** — **`/signup`: a live slot sign-up board for DJs & dancers.**
  - **📅 New `/signup` command** — the host posts a slot-signup board in the
    DJ/dancer channel; everyone else **self-serves** their own slots with one
    click. The bot opens an **"Event Creation"** modal **prefilled** with the
    exact board layout (title, `#1…#N` slot lines, Doors Open line, slot-length
    line), the host reviews/tweaks/submits, and the bot posts the board with a
    **Pick Slot** and an **Edit Event** button.
  - **🖱 Pick Slot** — a **private** prompt (only the clicking user sees it)
    with one button per slot, in order. Click `#1` to claim slot 1 (click
    again to cancel). Multiple people on a slot show space-separated; the whole
    channel sees it update **in real time**. Per-slot and per-person limits are
    enforced; no double-booking.
  - **✏️ Edit Event** — re-opens the modal; saving edits the **same post in
    place** (no duplicate event) and **preserves everyone already signed up**,
    matched by slot timestamp.
  - **⏱ Smart time handling** — start accepts raw unix (`1788670800`) or a
    ham-time wrap (`10pm ET`); the **Doors Open** line is auto-derived from slot
    1 minus `doors_lead` (default 15 min); the slot-length line **switches to
    hours** past 60 minutes.
  - **♻️ Persistent** — assignments live in `signup_events.json` (git-ignored)
    and each event's buttons are **re-registered on restart**, so a board keeps
    working across bot restarts. New modules: `signup.py` (pure engine),
    `signup_store.py` (state), `signup_ui.py` (modals + views).
  - **🐛 Fixed a Windows-only crash** — `zoneinfo` (used by `/timeslots` and
    `/signup` for time-of-day starts like "10pm ET") required the `tzdata`
    package, which wasn't pinned; without it those commands raised
    `ZoneInfoNotFoundError` on Windows. `tzdata` is now in `requirements.txt`.
  - **`/help` + README** now document the sign-up board.
- **v1.11.0** — **Rich Presence actually ships, a new error counter, and a startup you can read.**
  - **👀 Rich Presence — fixed so it actually shows.** The bot's stdout was
    going into a pipe the dashboard never read, and on Windows that ~4KB buffer
    fills fast — once it did, the bot *blocked on the next `print()`* and its
    event loop (including the presence push) stalled. The bot's console now
    writes to `bot_console.log` instead of an undrained pipe, and the first
    presence push is logged to the Activity Log as `presence_up` (or
    `presence_error`) so the outcome is visible, not a guess. **The status now
    reliably appears** under the bot in the member list.
  - **🔴 "Errors This Session" counter** — a new stat on the *Bot Process*
    status line (next to **PID / Uptime / State**), in **light red**, counting
    the `error`-level events since the current bot session started. It resets
    on every (re)start, so a handful of stale errors from a previous session
    don't haunt the current one. `/api/status` now reports
    `errors_this_session`.
  - **🪵 A startup you can read** — the bot now narrates each boot phase to
    the Activity Log: `boot` (version · commit · pid · discord.py version),
    `boot_config` (presence on/off · kill-switch · dj-refresh cadence ·
    **sheet configured or not**), `boot_djlist` (cached count · freshness ·
    URL), then `boot_connect`. So "what is the bot doing while it starts?" has
    an answer right in the log.
  - **Dashboard: tighter *Bot Process* card** — the **Rich Presence Toggle**
    (relabeled from "member-list status") now sits **on the same line** as the
    🗑 Reset "All Time" Counter button, right-aligned; the redundant
    *"this session's count already resets on every start"* note is gone.
  - **The `No sheet URL configured` error was stale** — it fired this morning
    before the sheet URL was in `bot_config.json`; the next refresh (414 DJs)
    succeeded. The new `boot_config` line now reports the sheet's status at
    every boot so an empty URL is obvious *before* it ever errors.
- **v1.10.0** — **Rich Presence: the bot shows its live session uptime in the
  Discord member list, plus a couple of polish fixes.**
  - **👀 Rich Presence — live session uptime** — the bot now sets a Discord
    custom status reading `up: H:MM:SS · v1.10.0`, visible under its name in
    the **member list** of any server it's in. It ticks every minute and
    measures the **current session** (resets on restart), matching the
    dashboard's *Bot process* uptime. A background task pushes it via
    `change_presence()` and reuses the `ready_at` session timestamp; it's
    cancelled cleanly on shutdown. See [the /presence section in commands.md](commands.md).
  - **New `/presence on|off` command** — toggle the status on and off. Cosmetic
    only (never touches commands); safe for any member to use. Registered
    staff-only alongside `/dj-refresh` if you want to restrict it.
  - **Dashboard: 👀 Presence switch** — a new toggle on the *Bot process* card
    (with a live preview of the status text) controls the same setting.
    `/api/status` now reports `presence`, and the new `/api/presence-toggle`
    endpoint flips it; the bot applies the change within a minute, **no
    restart needed**.
  - **Footer NWinn link is blue + underlined** — the *NWinn* link in the
    "built by NWinn…" footer is now styled **blue and underlined** so it reads
    as a link without having to hover.
  - **3-point versioning in action** — this is the first **minor** release
    (`1.9.x → 1.10.0`): a new feature bumps the second number and resets the
    patch, exactly per the policy set in 1.9.1.
- **v1.9.1** — **A little more breathing room + 3-point versioning.**
  - **Ticking dashboard uptime in the header** — the header now shows a live
    `up: H:MM:SS` timer (dashboard-process uptime) between the version and the
    commit hash, like the WyBot dashboard. It updates every second; the value
    comes from a `dash_started_at` epoch the backend captures at startup.
  - **Bot Process counters: label left, number right** — each usage stat
    (uptime / DJ lookups / prompts) now spreads its **label to the left** and
    the **number to the right** of its own cell (`justify-content:space-between`),
    so the eye tracks "what" then "how much" without the number crowding the word.
  - **Space between log timestamp and text** — the log line now puts a small
    gap between the `YYYY-MM-DD HH:MM:SS` stamp and the entry text, so long
    timestamps no longer run into the first word.
  - **Footer: NWinn → Linktree** — the *NWinn* name in the *"built by NWinn for
    the community…"* footer is now a hyperlink to
    [linktr.ee/nwinn](https://linktr.ee/nwinn) (opens in a new tab).
  - **3-point versioning** — `version.py` now ships `MAJOR.MINOR.PATCH`.
    `release.py` defaults to a **patch** bump (`1.9 → 1.9.1`), with `--minor`
    (`→ 1.10.0`) and `--major` (`→ 2.0.0`). `version_tuple()` returns a 3-tuple
    and `bump()` / `write_version()` understand the patch position, so the
    version flows correctly to the dashboard, the bot log, and GitHub Releases.
- **v1.9** — **A tighter, prettier dashboard — dates, colors, clear, and a custom icon.**
  - **Custom icon everywhere** — your DJ-bot logo (PNG + ICO) is now the
    **header mark** on the dashboard *and* sign-in page, and the **browser
    favicon** (served from `assets/`, auth-gated like the rest of the app).
  - **Dates on every log line** — timestamps now show **`YYYY-MM-DD HH:MM:SS`**
    (date + time, in your local zone) instead of time-only, so a bot running for
    weeks or months stays readable and older entries keep their date context.
  - **🗑 Clear logs button** — inline on the log toolbar, just before the search
    box. It empties the on-screen log and rotates the history to disk
    (`bot_activity.jsonl.1`), so you can tidy the view without losing the data.
    CSRF-protected; a "log cleared" event is written.
  - **Vibrant, consistent color scheme** — the log lines and the filter chips
    now use the **same category colors**: **red** = errors, **blue** = commands,
    **violet** = system, **amber** = warnings. Each filter chip lights up in its
    own category color when active, so the eye can tell at a glance which class
    of event is on screen.
  - **Tighter layout (width over height)** — the **Bot Process** usage counters
    and the **Pending DJ Additions** rows are compacted to use the horizontal
    space instead of stacking taller rows; the Pending DJ description is
    shortened to one line.
  - **Footer** — now reads *"built by NWinn for the community to make managing
    events easier"* (the version number still auto-updates with the release).
  - **Sign-in page** — matches the dashboard: same icon logo + favicon.
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

