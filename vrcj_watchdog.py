#!/usr/bin/env python3
"""VRCDJ_bot watchdog — keep the dashboard (127.0.0.1:8720) + its bot alive.

Why this exists: the dashboard runs windowless (vrcjdw.exe), so when it dies it
leaves NO traceback and nothing restarts it -> "problem loading page" in the
browser while the bot may still be up. Observed 2026-09-13: dashboard died
silently during a system-wide low-memory event (llama-server crash at the same
moment); the bot went with it. This watchdog:

  * launches the dashboard windowless via vrcjdw.exe with ALL output captured
    to vrcj_dashboard_crash.log  (so the NEXT death leaves a real traceback),
  * records the dashboard PID to vrcj_dashboard.pid (clean, killable),
  * relaunches the dashboard if it dies, AND force-recovers a process that is
    alive-but-wedged (port never comes up within a grace window).

The dashboard itself auto-starts the bot as a child (CREATE_NO_WINDOW) or
ADOPTS an already-running bot, so a watchdog relaunch never creates a
duplicate bot.

Idempotent: safe to start even if the dashboard is already running.
Stop everything with stop_vrcj.bat (kills vrcjw.exe + vrcjdw.exe + vrcjb.exe).
"""
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", 8720
CRASH_LOG = os.path.join(HERE, "vrcj_dashboard_crash.log")
WATCH_LOG = os.path.join(HERE, "vrcj_watchdog.log")
WATCHDOG_PID = os.path.join(HERE, "vrcj_watchdog.pid")
DASH_PID = os.path.join(HERE, "vrcj_dashboard.pid")

# The windowless dashboard launcher that already exists in VRCDJ_bot's venv.
DASH_EXE = os.path.join(HERE, "venv", "Scripts", "vrcjdw.exe")

# If the process is alive but hasn't served a port in this many seconds,
# treat it as wedged and restart it.
STUCK_GRACE_SECONDS = 90


def log(msg):
    line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(WATCH_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def port_up():
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            return True
    except OSError:
        return False


def read_pid(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) does NOT work on Windows (signal 0 is unsupported
        # and raises SystemError). Use OpenProcess with PROCESS_QUERY_LIMITED_INFORMATION.
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        kernel32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)  # signal 0 = existence check (POSIX only)
        return True
    except OSError:
        return False


def launch():
    """Start dashboard.py windowless via vrcjdw.exe, output -> crash log."""
    if not os.path.exists(DASH_EXE):
        log("ERROR: %s not found — cannot launch dashboard" % DASH_EXE)
        return None
    logf = open(CRASH_LOG, "ab")
    p = subprocess.Popen(
        [DASH_EXE, os.path.join(HERE, "dashboard.py")],
        cwd=HERE,
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    logf.close()
    try:
        with open(DASH_PID, "w", encoding="utf-8") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    log("launched dashboard (pid %d)" % p.pid)
    return p


def holder_live(holder) -> bool:
    """Is the lock-file holder a REAL live process?

    Two checks, both must pass:
      * OpenProcess — succeeds even for WINDOWS ZOMBIES (the trap we hit:
        a dead pythonw still answered, permanently blocking new watchdogs)
      * Get-Process — the taskmgr view: zombies are INVISIBLE here, real
        processes show. Fast (no CIM).
    """
    if not holder:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(holder))
        if not h:
            return False
        k.CloseHandle(h)
        try:
            r = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "exit (0 if (Get-Process -Id %d -EA SilentlyContinue) else 1)" % holder],
                capture_output=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            return r.returncode == 0
        except Exception:
            return True  # can't verify -> be conservative, treat as live
    try:
        os.kill(holder, 0)
        return True
    except OSError:
        return False


def claim_single_instance() -> bool:
    """Single-instance guard:

      1. If the port is already serving, another watcher's dashboard is live
         -> we'd be a redundant manager, so exit.
      2. If the lock file is held by a REAL live process, we are a stray ->
         exit. (OpenProcess + Get-Process, so Windows zombies can't
         permanently block a new watchdog.)
      3. Otherwise claim the lock (atomic replace) and continue.
    """
    if port_up():
        log("port %d already serving — another watcher is handling it; "
            "this instance (pid %d) exits." % (PORT, os.getpid()))
        return False

    holder = read_pid(WATCHDOG_PID)
    if holder and holder != os.getpid():
        if holder_live(holder):
            log("another watcher (pid %d) is already alive — "
                "this instance (pid %d) is a stray, exiting." % (holder, os.getpid()))
            return False
        log("lock holder pid %d is dead/zombie — claiming the lock" % holder)

    # Sole owner (or stale lock): claim it.
    global _GLOBAL_LOCK_FD
    try:
        tmp = WATCHDOG_PID + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("%d\n" % os.getpid())
        os.replace(tmp, WATCHDOG_PID)  # atomic
        try:
            _GLOBAL_LOCK_FD = os.open(WATCHDOG_PID, os.O_RDONLY)
        except OSError:
            pass
    except OSError as e:
        log("guard: could not claim lock file (%s) — assuming another owner" % e)
        return False
    return True


_GLOBAL_LOCK_FD = None  # set by claim_single_instance(); keeps the lock fd open


def main():
    if not claim_single_instance():
        return
    log("=== vrcj watchdog started (pid %d) ===" % os.getpid())

    stuck_since = None  # time.time() when we first saw alive-but-not-serving

    if not port_up():
        launch()
        stuck_since = time.time()

    while True:
        if not port_up():
            vp = read_pid(DASH_PID)
            if not pid_alive(vp):
                # cleanly dead -> relaunch
                log("dashboard down (pid %s) -> relaunching" % vp)
                launch()
                stuck_since = time.time()
            else:
                # alive but not serving
                if stuck_since is None:
                    stuck_since = time.time()
                elif time.time() - stuck_since > STUCK_GRACE_SECONDS:
                    log("dashboard alive (pid %s) but not serving after grace "
                        "-> force restart" % vp)
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(vp)],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                    except Exception:
                        pass
                    launch()
                    stuck_since = time.time()
        else:
            stuck_since = None
        time.sleep(12)


if __name__ == "__main__":
    main()
