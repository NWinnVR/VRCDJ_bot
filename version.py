"""version.py — the single source of truth for VRCDJ_bot's version.

VERSIONING POLICY (Nadia's rule, 2026-09-10):
    * We ship in MAJOR.MINOR form:  "1.0", "1.1", "1.2", ...
    * Adding features / fixes  -> bump the MINOR number (the second one):
          1.0 -> 1.1 -> 1.2 -> ...
    * A MAJOR bump (1.x -> 2.0) happens ONLY on a huge overhaul AND ONLY when
      Nadia explicitly says to change it. Do not auto-bump the major.
    * This is displayed on the dashboard, embedded in the bot's startup log,
      and used to tag GitHub Releases.

Keep it a plain string so it's trivial to read, edit, and grep. Bump it in
ONE place (here), and it flows to the dashboard and the release tooling.
"""

# ---- the version number (MAJOR.MINOR) -------------------------------------
VERSION = "1.3"

# ---- repo / release metadata (public, safe to ship) ------------------------
# Used by the dashboard's "check for updates" and the release script.
# `repo_url` is the public GitHub repo. `latest_check` hits the Releases API.
REPO_NAME = "VRCDJ_bot"
OWNER = "NWinnVR"                      # your GitHub login — the public repo owner
REPO_URL = f"https://github.com/{OWNER}/{REPO_NAME}"
RELEASES_API = f"https://api.github.com/repos/{OWNER}/{REPO_NAME}/releases/latest"

# Human label shown in the UI.
SHORT = f"v{VERSION}"


def version_tuple():
    """Return (major, minor) as ints, e.g. (1, 0)."""
    parts = VERSION.strip().split(".")
    major = int(parts[0]) if parts and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return major, minor


def _bump_minor(v: str) -> str:
    """1.0 -> 1.1, 1.9 -> 1.10 (the default, safe increment)."""
    major, minor = version_tuple()
    return f"{major}.{minor + 1}"


def bump(kind: str = "minor") -> str:
    """Compute the next version string WITHOUT writing it.

    kind:
      'minor' -> bump the second number (default; use for every add/fix).
      'major' -> bump the first number, reset the second (ONLY on Nadia's say).
    """
    major, minor = version_tuple()
    if kind == "major":
        return f"{major + 1}.0"
    return _bump_minor(VERSION)


def write_version(kind: str = "minor") -> str:
    """Rewrite the VERSION constant in THIS file to the next value and
    return it. Convenience for the release tooling. Defaults to a minor bump.
    """
    import re
    from pathlib import Path

    new = bump(kind)
    path = Path(__file__).resolve()
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r'^VERSION\s*=\s*"[^"]*"',
        f'VERSION = "{new}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    path.write_text(text, encoding="utf-8")
    return new
