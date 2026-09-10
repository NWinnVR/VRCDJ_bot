@echo off
setlocal
REM ─────────────────────────────────────────────────────────
REM  VRCDJ_bot — run (Windows)  [primary launcher]
REM
REM  Starts the dashboard AND the bot (the bot auto-starts as a
REM  child of the dashboard, with a watchdog that restarts it if
REM  it ever crashes). Keep this window open while you want the
REM  bot running — closing it stops everything.
REM
REM  First run?  Run install.bat once, then edit bot.env to add
REM  your DISCORD_BOT_TOKEN, then run this.
REM ─────────────────────────────────────────────────────────
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [!] No venv found. Run install.bat first, then this file.
    pause & exit /b 1
)
if not exist "bot.env" (
    echo [!] No bot.env found. Run install.bat first (it copies bot.env.example).
    pause & exit /b 1
)

REM Prefer vrcjd.exe (shows as "vrcjd.exe" in Task Manager, distinct from WyBot's python.exe).
if exist "venv\Scripts\vrcjd.exe" (
    echo.
    echo == VRCDJ_bot dashboard starting ==
    echo    (process: vrcjd.exe  ·  bot child: vrcjb.exe)
    echo    (the dashboard will print its LAN URL right below)
    echo.
    venv\Scripts\vrcjd.exe dashboard.py
) else (
    echo.
    echo == VRCDJ_bot dashboard starting ==
    echo    (vrcjd.exe not found — falling back to python.exe)
    echo    (the dashboard will print its LAN URL right below)
    echo.
    venv\Scripts\python.exe dashboard.py
)
