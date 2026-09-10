"""dashboard.py — VRCDJ_bot LAN web dashboard (Flask, dark theme).

One process does two jobs:
  1. Serves the dashboard web UI (login-gated) on 0.0.0.0:<port>.
  2. Owns the bot as a child subprocess, with a watchdog that auto-restarts
     it if it crashes (so a flaky gateway connection doesn't leave the bot
     down until a human notices).

SECURITY
  - Password login (werkzeug PBKDF2). Password auto-generated on first run
    if DASHBOARD_PASSWORD is not set; persisted to dashboard_password.json
    (git-ignored). Change it any time from the dashboard.
  - CSRF token on every state-changing POST.
  - Rate-limited login (10 attempts / 15 min per IP).
  - Secure headers on every response (CSP, X-Frame-Options, etc.).
  - No shell injection: subprocess calls use list args, shell=False.
  - Binds 0.0.0.0 so it's reachable over your LAN. Protect with a strong
    password and keep your home firewall on.

RUN:  python dashboard.py          (or double-click run_dashboard.bat)
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)

# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import version          # noqa: E402  (the version number, single source of truth)
import bot_config       # noqa: E402
import bot_state        # noqa: E402
import botlog           # noqa: E402
import dj_sheet         # noqa: E402
import repo_version     # noqa: E402
import envload          # noqa: E402  (tiny dependency-free .env loader)

# Load bot.env (git-ignored) into the environment BEFORE we read PORT /
# DASHBOARD_PASSWORD / DASHBOARD_SECRET below, so a single .env file is the
# one place the user configures the dashboard. Real env vars win.
envload.load_dotenv(str(BASE / "bot.env"))

# ---------------------------------------------------------------------------
# dashboard config
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("DASHBOARD_PORT", "8720"))
HOST = "0.0.0.0"
PASSWORD_FILE = BASE / "dashboard_password.json"
BOT_LOG_PATH = BASE / "bot_activity.jsonl"
GIT = os.environ.get("GIT", "git")

# ---------------------------------------------------------------------------
# password management
#
# Priority for the dashboard login password:
#   1. DASHBOARD_PASSWORD in the environment (your .env)  — highest.
#   2. A previously-stored hash in dashboard_password.json (git-ignored).
#   3. A freshly generated random password, printed ONCE to the console.
# ---------------------------------------------------------------------------
def _hash_password(pw: str) -> str:
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw, method="pbkdf2:sha256", salt_length=16)


def _env_password() -> str:
    return os.environ.get("DASHBOARD_PASSWORD", "").strip()


def _verify_password(pw: str) -> bool:
    from werkzeug.security import check_password_hash
    env_pw = _env_password()
    if env_pw:
        return secrets.compare_digest(env_pw, pw)
    stored = PASSWORD_FILE.read_text(encoding="utf-8").strip() if PASSWORD_FILE.exists() else ""
    if not stored:
        return False
    try:
        return check_password_hash(stored, pw)
    except Exception:
        return False


def ensure_password() -> None:
    """Make sure a login password exists. Prints the password ONCE if it was
    freshly generated (so the user can read it), otherwise stays quiet."""
    if _env_password():
        # Password is being supplied via .env — nothing to store or print.
        return
    if PASSWORD_FILE.exists():
        return  # already has a stored password
    pw = secrets.token_urlsafe(12)
    PASSWORD_FILE.write_text(_hash_password(pw), encoding="utf-8")
    try:
        os.chmod(PASSWORD_FILE, 0o600)
    except OSError:
        pass
    print(f"[dashboard] No DASHBOARD_PASSWORD set and none stored yet.")
    print(f"[dashboard] A random dashboard password was generated:  {pw}")
    print(f"[dashboard] Use it to sign in, then set DASHBOARD_PASSWORD in your")
    print(f"[dashboard] .env (or change it later from the dashboard) and restart.")


# ---------------------------------------------------------------------------
# bot process management
# ---------------------------------------------------------------------------
class BotManager:
    """Owns the bot as a subprocess. Watchdog thread auto-restarts it if
    it dies while we still want it running."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._want_running = True
        self._last_start = 0.0
        self._restart_log: list[tuple[float, str]] = []

    @staticmethod
    def _python() -> str:
        """Prefer the running interpreter (it has the venv)."""
        return sys.executable or "python"

    def _spawn(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return  # already running
            self._last_start = time.time()
            env = dict(os.environ)
            # The bot reads DISCORD_BOT_TOKEN from bot.env via dotenv, so we
            # don't need to inject it here.
            try:
                self._proc = subprocess.Popen(
                    [BotManager._python(), str(BASE / "bot.py")],
                    cwd=str(BASE),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    # On Windows: CREATE_NEW_PROCESS_GROUP so we can kill
                    # the whole tree with os.kill(pid, 9) → TerminateProcess.
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                self._log("bot_started", f"pid={self._proc.pid}")
            except Exception as exc:
                self._log("bot_start_failed", str(exc)[:300])

    def _log(self, kind: str, detail: str) -> None:
        ts = time.time()
        self._restart_log.append((ts, f"{kind}: {detail}"))
        self._restart_log = self._restart_log[-50:]
        with contextlib.suppress(Exception):
            botlog.log(kind, detail=detail)

    def _watchdog(self) -> None:
        """Loop: keep the bot alive. If it dies and we still want it,
        restart it (with a 2 s cooldown to avoid a crash-storm)."""
        while True:
            time.sleep(3)
            if not self._want_running:
                continue
            with self._lock:
                proc = self._proc
            if proc is None or proc.poll() is not None:
                if time.time() - self._last_start < 2:
                    continue  # cooldown
                exit_code = proc.returncode if proc else None
                self._log("bot_restarting",
                          f"bot process exited (code={exit_code}); restarting")
                self._spawn()

    def start(self) -> None:
        self._want_running = True
        self._spawn()

    def stop(self) -> None:
        self._want_running = False
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc and proc.poll() is None:
            # proc.kill() → TerminateProcess on Windows, SIGKILL on POSIX.
            # We spawn the bot with CREATE_NEW_PROCESS_GROUP (Windows) so a
            # single kill covers the whole process tree.
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        self._log("bot_stopped", "user requested stop")

    def restart(self) -> None:
        self.stop()
        time.sleep(1)
        self.start()

    def status(self) -> dict:
        with self._lock:
            proc = self._proc
        alive = proc is not None and proc.poll() is None
        # Read the bot's own lifecycle file for richer state.
        lc = bot_state._read_json(bot_state.LIFECYCLE_PATH,
                                  bot_state._LIFECYCLE_DEFAULTS)
        return {
            "alive": alive,
            "want_running": self._want_running,
            "pid": proc.pid if proc else None,
            "exit_code": proc.returncode if proc else None,
            "lifecycle_state": lc.get("state"),
            "ready_at": lc.get("ready_at"),
            "started_at": lc.get("started_at"),
            "last_beat": lc.get("last_beat"),
            "restart_log": [
                {"t": t, "msg": m} for t, m in self._restart_log[-10:]
            ],
        }


bot_manager = BotManager()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder=str(BASE / "templates"))
app.secret_key = os.environ.get(
    "DASHBOARD_SECRET") or secrets.token_hex(32)

# ---- rate-limit login -------------------------------------------------------
_login_attempts: dict[str, list[float]] = {}


def _login_allowed(ip: str) -> bool:
    now = time.time()
    attempts = _login_attempts.setdefault(ip, [])
    attempts[:] = [t for t in attempts if now - t < 15 * 60]
    return len(attempts) < 10


def _login_record_fail(ip: str) -> None:
    _login_attempts.setdefault(ip, []).append(time.time())


# ---- CSRF -------------------------------------------------------------------
def _csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]


def _check_csrf() -> bool:
    sent = (request.form.get("csrf") or request.headers.get("X-CSRF-Token") or "").strip()
    want = (session.get("_csrf") or "").strip()
    # Both must be present and equal. (An empty token must never validate.)
    return bool(want) and secrets.compare_digest(want, sent)


# ---- auth middleware --------------------------------------------------------
@app.before_request
def _require_login():
    if request.endpoint in ("login", "static"):
        return None
    if not session.get("logged_in"):
        # API routes: always a JSON 401 (fetch().json() and programmatic
        # clients must never receive an HTML login page).
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_logged_in"}), 401
        return redirect(url_for("login"))
    return None


@app.after_request
def _secure_headers(resp: Response) -> Response:
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---- routes -----------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if not _login_allowed(ip):
            return render_template("login.html",
                                   error="Too many attempts — try again in 15 minutes."), 429
        pw = request.form.get("password", "")
        if _verify_password(pw):
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("home"))
        _login_record_fail(ip)
        error = "Wrong password."
    _csrf_token()
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def home():
    data = {
        "version": version.VERSION,
        "short": version.SHORT,
        "repo_url": version.REPO_URL,
        "bot": bot_manager.status(),
        "enabled": bot_state.is_enabled(),
        "dj_count": _safe_count_djs(),
        "dj_freshness": _safe_freshness(),
        "commit": _safe_commit(),
        "lan_url": _lan_url(),
        "csrf": _csrf_token(),
    }
    return render_template("home.html", **data)


@app.route("/api/status")
def api_status():
    return jsonify({
        "version": version.VERSION,
        "bot": bot_manager.status(),
        "enabled": bot_state.is_enabled(),
        "dj_count": _safe_count_djs(),
        "dj_freshness": _safe_freshness(),
        "commit": _safe_commit(),
    })


@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    if not _check_csrf():
        return jsonify({"error": "csrf"}), 403
    new_state = not bot_state.is_enabled()
    bot_state.set_enabled(new_state, by="dashboard")
    botlog.log("toggle", detail=f"dashboard → {'ON' if new_state else 'OFF'}")
    return jsonify({"enabled": new_state})


@app.route("/api/dj-refresh", methods=["POST"])
def api_dj_refresh():
    if not _check_csrf():
        return jsonify({"error": "csrf"}), 403

    def _do():
        try:
            n = dj_sheet.refresh_dj_list(bot_config.get_sheet_url())
            botlog.log("dj_refresh", detail=f"dashboard → {n} DJs loaded")
            return {"ok": True, "count": n}
        except Exception as exc:
            botlog.log("dj_refresh_error", level="error", detail=str(exc)[:300])
            return {"ok": False, "error": str(exc)[:300]}

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    return jsonify({"ok": True, "started": True})


@app.route("/api/log")
def api_log():
    try:
        entries = botlog.tail(100)
    except Exception:
        entries = []
    return jsonify({"entries": entries})


@app.route("/api/check-updates")
def api_check_updates():
    """Query the GitHub Releases API for the latest release tag and compare
    to our local version. Best-effort; offline → {"ok":false,"error":...}."""
    try:
        import urllib.request
        req = urllib.request.Request(
            version.RELEASES_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"VRCDJ_bot/v{version.VERSION}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        local = version.VERSION
        if latest == local:
            up_to_date = True
        else:
            # naive semver compare (major.minor)
            try:
                lm = tuple(int(x) for x in local.split("."))
                rm = tuple(int(x) for x in latest.split("."))
                up_to_date = lm >= rm
            except Exception:
                up_to_date = False
        return jsonify({
            "ok": True,
            "local": local,
            "latest": latest,
            "up_to_date": up_to_date,
            "html_url": data.get("html_url"),
            "published_at": data.get("published_at"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "local": version.VERSION,
                        "error": str(exc)[:200]})


@app.route("/api/update", methods=["POST"])
def api_update():
    """git fetch + git pull in the project folder, then restart the bot.
    This is the 'push updates to the other machine' path: the other machine
    runs the same command (or the user does `git pull` manually), and this
    button makes it one click."""
    if not _check_csrf():
        return jsonify({"error": "csrf"}), 403

    def _do():
        try:
            r = subprocess.run([GIT, "pull", "origin", "main"],
                               cwd=str(BASE), capture_output=True,
                               text=True, timeout=120, shell=False)
            out = (r.stdout + r.stderr).strip()[:2000]
            botlog.log("update", detail=f"git pull exit={r.returncode}")
            if r.returncode == 0:
                bot_manager.restart()
                return {"ok": True, "output": out, "restarted": True}
            return {"ok": False, "output": out, "restarted": False}
        except Exception as exc:
            botlog.log("update_error", level="error", detail=str(exc)[:300])
            return {"ok": False, "error": str(exc)[:300]}

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    return jsonify({"ok": True, "started": True})


@app.route("/api/bot-restart", methods=["POST"])
def api_bot_restart():
    if not _check_csrf():
        return jsonify({"error": "csrf"}), 403

    def _do():
        bot_manager.restart()
        return

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "restarting": True})


@app.route("/api/bot-stop", methods=["POST"])
def api_bot_stop():
    if not _check_csrf():
        return jsonify({"error": "csrf"}), 403

    def _do():
        bot_manager.stop()
        return

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "stopping": True})


@app.route("/api/bot-start", methods=["POST"])
def api_bot_start():
    if not _check_csrf():
        return jsonify({"error": "csrf"}), 403

    def _do():
        bot_manager.start()
        return

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "starting": True})


# ---- helpers ----------------------------------------------------------------
def _safe_count_djs() -> int:
    try:
        return dj_sheet.count_djs()
    except Exception:
        return 0


def _safe_freshness() -> str:
    try:
        return dj_sheet.get_dj_freshness()
    except Exception:
        return "unknown"


def _safe_commit() -> str:
    try:
        return repo_version.get_local_head() or "unknown"
    except Exception:
        return "unknown"


def _lan_url() -> str:
    """Best-effort LAN IP for the user to open in a browser."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:{PORT}"
    except Exception:
        return f"http://localhost:{PORT}"


# ---- main -------------------------------------------------------------------
def main():
    ensure_password()
    no_autostart = os.environ.get("VRCDJ_NO_AUTOSTART", "").strip() in ("1", "true", "yes")
    print(f"[dashboard] VRCDJ_bot v{version.VERSION}")
    print(f"[dashboard] Dashboard: {_lan_url()}")
    print(f"[dashboard] Opening a browser at that URL will show the login page.")
    print()

    # Start the watchdog thread (auto-restarts the bot if it dies).
    threading.Thread(target=bot_manager._watchdog, daemon=True,
                     name="bot-watchdog").start()
    if no_autostart:
        print("[dashboard] VRCDJ_NO_AUTOSTART set — NOT auto-starting the bot.")
        print("[dashboard] Use the ▶ Start button (or run_bot.bat) to start it.")
    else:
        # Start the bot now.
        bot_manager.start()

    # Run the Flask server. threaded=True so API polling + page loads
    # don't block each other.
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
