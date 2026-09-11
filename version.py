"""version.py — the single source of truth for VRCDJ_bot's version.

VERSIONING POLICY (Nadia's rule, 2026-09-10 — now 3-point):
    * We ship in MAJOR.MINOR.PATCH form:  "1.9", "1.9.1", "1.9.2", ...
    * Small fixes / tweaks  -> bump the PATCH number (the third one) — the default.
    * A feature / notable add -> bump the MINOR number (the second one), reset patch.
    * A MAJOR bump (1.x -> 2.0) happens ONLY on a huge overhaul AND ONLY when
      Nadia explicitly says to change it. Do not auto-bump the major.
    * This is displayed on the dashboard, embedded in the bot's startup log,
      and used to tag GitHub Releases.

Keep it a plain string so it's trivial to read, edit, and grep. Bump it in
ONE place (here), and it flows to the dashboard and the release tooling.
"""

# ---- the version number (MAJOR.MINOR.PATCH) --------------------------------
# 1.12.3 — /signup button fix: the slot buttons and the "All" button now ACK
#          the click immediately (defer + ephemeral "thinking…") instead of
#          doing their work first — that was the "VRCDJ didn't respond in
#          time" error (the network round-trips pushed past Discord's 3 s
#          window before the interaction was ever acknowledged).  Success is
#          now SILENT (the bubble is cleared, the name on the board IS the
#          confirmation); you only get a reply on failure or when a limit
#          blocked part of an "All" action.  New: the "All" button TOGGLES —
#          click it once to grab every slot, click it again to give them all
#          back (mirrors the individual slot buttons).
VERSION = "1.12.3"

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
    """Return (major, minor, patch) as ints, e.g. (1, 9, 0).

    Tolerates 2-part ("1.9") and 3-part ("1.9.1") strings — a missing patch
    is read as 0, so old tags and the dashboard still parse cleanly.
    """
    parts = VERSION.strip().split(".")
    major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return major, minor, patch


def bump(kind: str = "patch") -> str:
    """Compute the next version string WITHOUT writing it.

    kind:
      'patch' -> bump the third number (default; use for every small fix/tweak):
                 1.9     -> 1.9.1     (a 2-part version gets its first patch)
                 1.9.0   -> 1.9.1
      'minor' -> bump the second number, reset patch (a feature / notable add):
                 1.9.0   -> 1.10.0
      'major' -> bump the first number, reset the rest (ONLY on Nadia's say):
                 1.x.y   -> 2.0.0
    """
    major, minor, patch = version_tuple()
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    # default: patch
    if patch == 0 and minor == 0:
        return f"{major}.0.1"
    if patch == 0:
        # 2-part version like "1.9" — give it its first patch, "1.9.1"
        return f"{major}.{minor}.1"
    return f"{major}.{minor}.{patch + 1}"


def write_version(kind: str = "patch") -> str:
    """Rewrite the VERSION constant in THIS file to the next value and
    return it. Convenience for the release tooling. Defaults to a patch bump.
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
