#!/usr/bin/env python
"""release.py — bump the version, tag it, and publish a GitHub Release.

This is the one-tool way to cut a new release from the VRCDJ_bot folder.
It implements Nadia's versioning policy (see version.py):
  * default = MINOR bump   (1.0 → 1.1 → 1.2 ...)   for every add/fix
  * --major = MAJOR bump   (1.x → 2.0)              ONLY when explicitly wanted

WHAT IT DOES
  1. Bumps version.py (in place, the single source of truth).
  2. git commit + git tag v<new>  (only if a git repo is present).
  3. If the `gh` CLI is available and the repo is pushed, creates a GitHub
     Release so it shows up in the "releases" section and is downloadable.
     (If no network / no gh, it still bumps + tags and tells you the rest.)

USAGE
  python release.py                  # minor bump (default) → 1.1
  python release.py --message "Add /foo command"
  python release.py --major          # major bump → 2.0 (only on Nadia's say)
  python release.py --dry-run        # show what it WOULD do, change nothing

No third-party deps (uses git + gh + stdlib).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import version  # noqa: E402


def sh(cmd: list[str], *, check: bool = True) -> tuple[int, str]:
    """Run a command, return (exit_code, combined_output). No shell (safe)."""
    try:
        r = subprocess.run(cmd, cwd=str(BASE), capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if check and r.returncode != 0:
            raise RuntimeError(f"command failed: {' '.join(cmd)}\n{out}")
        return r.returncode, out
    except FileNotFoundError:
        return 127, f"(not found) {' '.join(cmd)}"


def git(*args: str) -> tuple[int, str]:
    return sh(["git", *args])


def gh(*args: str) -> tuple[int, str]:
    return sh(["gh", *args])


def has_git() -> bool:
    return (BASE / ".git").exists()


def main() -> int:
    ap = argparse.ArgumentParser(description="Cut a new VRCDJ_bot release.")
    ap.add_argument("--major", action="store_true",
                    help="bump the MAJOR version (1.x → 2.0). Default is minor.")
    ap.add_argument("--message", "-m", default="",
                    help="release notes / commit message (Markdown supported).")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen without changing anything.")
    ap.add_argument("--no-push", action="store_true",
                    help="bump + tag locally, but don't push / create a GH release.")
    args = ap.parse_args()

    kind = "major" if args.major else "minor"
    current = version.VERSION
    new = version.bump(kind)

    print(f"VRCDJ_bot release")
    print(f"  current : v{current}")
    print(f"  bump    : {kind}")
    print(f"  new     : v{new}")
    print(f"  repo    : {version.REPO_URL}")
    print()

    if args.dry_run:
        print("  --dry-run: no files or git state will be changed.")
        print("  You would:")
        print(f"    1. set version.py  VERSION = \"{new}\"")
        print(f"    2. git commit -m \"Release v{new}\"")
        print(f"    3. git tag -a v{new} -m \"{args.message or 'Release v'+new}\"")
        print(f"    4. gh release create v{new} --title ... --notes ...")
        return 0

    # 1) write the new version into version.py
    written = version.write_version(kind)
    print(f"  ✓ version.py → v{written}")

    notes = args.message.strip() or f"Release v{new}"

    if not has_git():
        print("\n  (no git repo here — version bumped only. "
              "Push and tag manually if you want a GH release.)")
        return 0

    # 2) commit the version bump
    git("add", "version.py")
    c1, _ = git("commit", "-m", f"Release v{new}")
    print(f"  ✓ git commit (exit {c1})")

    # 3) tag
    tag = f"v{new}"
    t1, tout = git("tag", "-a", tag, "-m", notes)
    if t1 != 0 and "already exists" not in tout:
        print(f"  ! git tag: {tout}")
        return 1
    print(f"  ✓ git tag {tag}")

    if args.no_push:
        print("\n  --no-push: stopping before push / GitHub release.")
        return 0

    # 4) push + GitHub release (best-effort; needs gh + network + origin)
    p1, pout = git("push", "origin", "main")
    print(f"  ✓ git push (exit {p1})" + (f" — {pout}" if p1 and pout else ""))
    git("push", "origin", tag)

    rc, gout = gh("release", "create", tag, "--title", f"VRCDJ_bot v{new}",
                  "--notes", notes, "--target", "main")
    if rc == 0:
        print(f"  ✓ GitHub Release created for {tag}")
        print(f"      {version.REPO_URL}/releases/{tag}")
    else:
        print(f"  ! gh release: {gout or 'not created'}")
        print(f"    (If you don't have the `gh` CLI, create it in the GitHub web UI:\n"
        f"     {version.REPO_URL}/releases)")

    print("\nDone. Other machines can now pull this version (the dashboard's")
    print('"Check for updates" will pick it up, or run `git pull`).')
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
