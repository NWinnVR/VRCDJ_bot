@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   VRCDJ_bot  -  START  (watchdog + dashboard + bot, silent)
echo ============================================================
echo.

if not exist "venv\Scripts\pythonw.exe" goto :noenv
if not exist "bot.env" goto :noenv

goto :clean

:noenv
echo   [!] Missing venv or bot.env. Run install.bat first.
echo ============================================================
endlocal & exit /b 1

:clean
REM --- clean slate: stop any existing VRCDJ processes (unique names only) ---
taskkill /F /T /IM vrcjw.exe  2>nul
taskkill /F /T /IM vrcjdw.exe 2>nul
taskkill /F /T /IM vrcjd.exe  2>nul
taskkill /F /T /IM vrcjb.exe  2>nul
timeout /t 1 /nobreak >nul

REM --- launch the watchdog detached + windowless (it owns the dashboard) ---
if exist "venv\Scripts\vrcjw.exe" (
    start "" venv\Scripts\vrcjw.exe vrcj_watchdog.py
) else (
    echo   [!] vrcjw.exe not found - falling back to a DIRECT dashboard launch.
    start "" venv\Scripts\vrcjdw.exe dashboard.py
)

echo.
echo   Watchdog + dashboard launched (silent).
echo   Open  http://127.0.0.1:8720  (or your LAN IP).
echo   If the dashboard ever dies, the watchdog brings it back automatically.
echo   Logs: vrcj_watchdog.log, vrcj_dashboard_crash.log
echo ============================================================
endlocal
exit /b 0
