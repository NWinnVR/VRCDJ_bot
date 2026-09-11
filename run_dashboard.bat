@echo off
setlocal
REM ─────────────────────────────────────────────────────────
REM  VRCDJ_bot — run (Windows)  [silent launcher]
REM
REM  Starts the dashboard AND the bot with NO console window left behind.
REM  The dashboard runs under vrcjdw.exe (a GUI-subsystem copy of pythonw),
REM  which CANNOT allocate a console window — so nothing sits on your
REM  desktop.  The bat itself just fires it and exits.
REM
REM  The bot auto-starts as a child of the dashboard (NO_WINDOW), with a
REM  watchdog that restarts it if it ever crashes.
REM
REM  Stop it from the dashboard (Full flush / Stop bot) or Task Manager
REM  (vrcjdw.exe = dashboard, vrcjb.exe = bot).
REM
REM  First run?  Run install.bat once, then edit bot.env to add
REM  your DISCORD_BOT_TOKEN, then run this.
REM ─────────────────────────────────────────────────────────
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
    echo [!] No venv found. Run install.bat first, then this file.
    pause & exit /b 1
)
if not exist "bot.env" (
    echo [!] No bot.env found. Run install.bat first (it copies bot.env.example).
    pause & exit /b 1
)

REM Prefer the silent GUI-subsystem launcher (vrcjdw.exe = pythonw).  `start`
REM launches it detached and returns immediately; because it is a GUI-subsystem
REM process it never creates a console window, so this bat closes with nothing
REM left on screen.  pythonw survives the bat exiting (it has no console).
if exist "venv\Scripts\vrcjdw.exe" (
    start "" venv\Scripts\vrcjdw.exe dashboard.py
    exit /b 0
)

REM Fallback: no silent launcher yet (pre-install).  This path DOES show a
REM window — run install.bat to create vrcjdw.exe.
echo [!] vrcjdw.exe not found — falling back to the console launcher.
echo     Run install.bat once to create the silent launcher.
echo.
echo == VRCDJ_bot dashboard starting (windowed) ==
venv\Scripts\vrcjd.exe dashboard.py
exit /b 0
