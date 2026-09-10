@echo off
setlocal
REM ─────────────────────────────────────────────────────────
REM  VRCDJ_bot — run the DISCORD BOT ONLY (no dashboard)
REM  For when you just want the bot up, headless, no web UI.
REM  Ctrl+C stops it.
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

venv\Scripts\python.exe bot.py
