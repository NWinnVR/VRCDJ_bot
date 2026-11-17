← [README](../README.md) · [Wiki index](index.md)

# Troubleshooting

| Symptom | Fix |
|---|---|
| `DISCORD_BOT_TOKEN missing` | Add your token to `bot.env` and restart. |
| Bot connects but commands don't appear | Wait up to ~60 s for global command sync; check the Developer Portal that the bot is invited and the Message Content Intent is on. |
| Dashboard won't open from another device | Make sure both devices are on the same network; use the LAN IP (not `localhost`); check your firewall allows the port. |
| `port already in use` | Set a different `DASHBOARD_PORT` in `bot.env`. |
| `another VRCDJ_bot is already running (PID …)` | You started a second copy of the *same* bot. Stop the first one, or you're fine — the guard did its job. |
| `/dj` finds nobody | Run `/dj-refresh`, and confirm `DJ_SHEET_URL` points at a viewable sheet with a `Name` column. |
| PyNaCl warning on start | Harmless — voice support is an optional extra this bot doesn't use. |
