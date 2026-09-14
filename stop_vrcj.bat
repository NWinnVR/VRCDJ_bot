@echo off
REM ==========================================================================
REM  stop_vrcj.bat — STOP the VRCDJ_bot (dashboard AND bot), no PID hunting.
REM
REM  Uses only `taskkill /F /T /IM <name>` — kills by the UNIQUE image name
REM  (vrcjw.exe = watchdog, vrcjb.exe = bot, vrcjdw.exe / vrcjd.exe =
REM  dashboard) plus their child interpreters via /T.  Those names exist
REM  ONLY in VRCDJ_bot's own venv, so this can NEVER touch WyBot
REM  (wybot*.exe) or the LM Viewport (pythonw.exe).
REM
REM  Double-click it, or run from a terminal.  Safe to run repeatedly.
REM  Bring it back with start_vrcj.bat.
REM ==========================================================================
setlocal
cd /d "%~dp0"
set KILLED=0

echo ============================================================
echo   VRCDJ_bot  -  STOP  (dashboard + bot)
echo ============================================================
echo.

REM --- watchdog FIRST (so it can't relaunch anything we're stopping) ---
taskkill /F /T /IM vrcjw.exe >nul 2>&1
if errorlevel 1 goto skip_wd
echo   stopped  watchdog   (vrcjw.exe)
set KILLED=1
:skip_wd

REM --- dashboard (windowless GUI-subsystem copy) + its children ---
taskkill /F /T /IM vrcjdw.exe >nul 2>&1
if errorlevel 1 goto skip_dash
echo   stopped  dashboard  (vrcjdw.exe)
set KILLED=1
:skip_dash

REM --- dashboard (console-subsystem copy, fallback) + its children ---
taskkill /F /T /IM vrcjd.exe >nul 2>&1
if errorlevel 1 goto skip_dash2
echo   stopped  dashboard  (vrcjd.exe)
set KILLED=1
:skip_dash2

REM --- bot + its children ---
taskkill /F /T /IM vrcjb.exe >nul 2>&1
if errorlevel 1 goto skip_bot
echo   stopped  bot        (vrcjb.exe)
set KILLED=1
:skip_bot

echo.
echo   Done - VRCDJ_bot is stopped.
echo.
echo   Bring it back:  start_vrcj.bat   (or just ask me "start VRCDJ")
echo ============================================================
endlocal
exit /b 0
