@echo off
REM ==========================================================================
REM  start_vrcj.bat — (RE)START the VRCDJ_bot under its WATCHDOG, windowless.
REM
REM  1. Stops any half-dead VRCDJ processes first (by unique image name),
REM     so we never end up with two dashboards or a stuck port.
REM  2. Launches the WATCHDOG (vrcjw.exe) — it starts the dashboard
REM     (vrcjdw.exe) windowless and relaunches it automatically if the
REM     dashboard ever dies (silent death, low-memory kill, wedged port).
REM  3. The bot (vrcjb.exe) auto-starts as a child of the dashboard
REM     (CREATE_NO_WINDOW); if a healthy bot is already beating, the
REM     dashboard ADOPTS it. Nothing ever sits on your desktop.
REM
REM  Double-click it, or run from a terminal.  Stop it with stop_vrcj.bat.
REM ==========================================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo   VRCDJ_bot  -  START  (watchdog + dashboard + bot, silent)
echo ============================================================
echo.

if not exist "venv\Scripts\pythonw.exe" (
    echo   [!] No venv found. Run install.bat first.
    pause & exit /b 1
)
if not exist "bot.env" (
    echo   [!] No bot.env found. Run install.bat first (copies bot.env.example).
    pause & exit /b 1
)

REM --- clean slate: stop any existing VRCDJ processes (unique names only) ---
taskkill /F /T /IM vrcjw.exe  2>nul >nul
taskkill /F /T /IM vrcjdw.exe 2>nul >nul
taskkill /F /T /IM vrcjd.exe  2>nul >nul
taskkill /F /T /IM vrcjb.exe  2>nul >nul
timeout /t 1 /nobreak >nul

REM --- launch the watchdog detached + windowless (it owns the dashboard) ---
if exist "venv\Scripts\vrcjw.exe" (
    start "" venv\Scripts\vrcjw.exe vrcj_watchdog.py
) else (
    echo   [!] vrcjw.exe not found - falling back to a DIRECT dashboard launch.
    echo       (No watchdog in this mode: if the dashboard dies it stays dead.)
    start "" venv\Scripts\vrcjdw.exe dashboard.py
)

echo.
echo   Watchdog + dashboard launched (silent).
echo   Open  http://127.0.0.1:8720  (or your LAN IP, e.g. 192.168.1.65).
echo   If the dashboard ever dies, the watchdog brings it back automatically.
echo   Logs: vrcj_watchdog.log, vrcj_dashboard_crash.log
echo ============================================================
endlocal
