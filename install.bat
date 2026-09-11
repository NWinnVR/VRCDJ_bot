@echo off
setlocal
REM ─────────────────────────────────────────────────────────
REM  VRCDJ_bot — one-time install (Windows)
REM  Creates a portable venv and installs the 2 dependencies.
REM  Re-run any time; it's safe to run again.
REM ─────────────────────────────────────────────────────────
cd /d "%~dp0"

echo.
echo == VRCDJ_bot install ==
echo.

REM --- find python -------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         (tick "Add python.exe to PATH" during install) and re-run.
    pause & exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [i] Using %PYVER%

REM --- create venv (if missing) -----------------------------------------
if not exist "venv\Scripts\python.exe" (
    echo [i] Creating virtualenv...
    python -m venv venv || (echo [ERROR] venv failed & pause & exit /b 1)
)

REM --- install / refresh deps -------------------------------------------
echo [i] Installing dependencies (discord.py + Flask)...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] dependency install failed.
    pause & exit /b 1
)

REM --- make sure bot.env exists -----------------------------------------
if not exist "bot.env" (
    echo [i] No bot.env yet — copying bot.env.example so you can fill in the token.
    copy /y bot.env.example bot.env >nul
    echo [!] Edit bot.env and paste your DISCORD_BOT_TOKEN, then re-run run_dashboard.bat.
)

REM --- distinct process names (Task Manager shows vrcjd.exe / vrcjb.exe) --
REM Copies of the venv's python.exe under friendly names.  This makes VRCDJ_bot
REM trivially distinguishable from WyBot (which runs as plain python.exe) when
REM both are on the same machine.  Safe to re-run; just refreshes the copies.
if not exist "venv\Scripts\vrcjd.exe" (
    echo [i] Creating vrcjd.exe (dashboard) + vrcjb.exe (bot) process names...
    copy /y "venv\Scripts\python.exe" "venv\Scripts\vrcjd.exe" >nul
    copy /y "venv\Scripts\python.exe" "venv\Scripts\vrcjb.exe" >nul
)

REM --- silent launcher (vrcjdw.exe = pythonw, GUI subsystem → NO console) --
REM Used by run_dashboard.bat so a double-click starts the dashboard with
REM no empty terminal window.  The dashboard guards its print() (see
REM dashboard.py top) so it runs fine with no console attached.
if not exist "venv\Scripts\vrcjdw.exe" (
    echo [i] Creating vrcjdw.exe (silent dashboard launcher)...
    copy /y "venv\Scripts\pythonw.exe" "venv\Scripts\vrcjdw.exe" >nul
)

echo.
echo == install complete ==
echo Next:  edit bot.env (add your token), then double-click run_dashboard.bat
echo.
pause
