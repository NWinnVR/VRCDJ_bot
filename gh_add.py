#!/usr/bin/env python
"""gh_add.py — GitHub drop-box for `/adddj` suggestions.

WHAT IT DOES
  - create_issue(title, body, labels)  → POST a new issue to the owner's repo.
  - list_open_issues(labels, since)    → GET the repo's open issues (the
                                         dashboard's "pending DJ additions"
                                         checker reads this).
  Both hit the GitHub REST API with urllib (no `requests` dependency, no
  shell, no subprocess).

AUTH & TOKEN HYGIENE (this is the security-critical part)
  - The PAT is read from the environment (GH_TOKEN) by the CALLER and passed
    in — it never lives in this file, never in bot.env.example, never in a
    log line, and is never echoed into an exception message.
  - Every error string that returns to the UI is passed through
    _safe_err(), which strips any token-shaped secret and any absolute path
    before it can be shown. A 401/403 just says "GitHub rejected the token"
    — it does NOT repeat the token or the request body.
  - Without a token, create_issue still tries (the repo is public and the
    owner may allow issue creation) — but it will 401, and that's reported
    cleanly. The dashboard checker only needs READ (no token needed on a
    public repo).

SECURITY
  - Pure outbound HTTPS. No file writes, no shell, no eval. The issue title
    and body are JSON-serialised by urllib, never interpolated into a URL.
  - The URL is built from a fixed base + the configured owner/repo — user
    text never reaches the request line.
"""
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_OWNER_REPO = "NWinnVR/VRCDJ_bot"
API = "https://api.github.com"

# A PAT looks like ghp_/gho_/ghu_/ghs_/ghr_ + 36 hex chars, or a classic 40-hex
# token. Strip these out of ANY string that might be surfaced to the UI.
_TOKEN_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|[a-f0-9]{40})", re.IGNORECASE)


def _safe_err(exc: Exception) -> str:
    """Format an exception for display, scrubbing tokens and absolute paths."""
    msg = str(exc) or type(exc).__name__
    if isinstance(exc, urllib.error.HTTPError):
        # Do NOT dump exc.body (it may echo the request / token). Just the code,
        # but with a SELF-DIAGNOSING hint so the user knows exactly what to do
        # instead of staring at a bare "GitHub HTTP 401".
        if exc.code == 401:
            return ("GitHub rejected the token (HTTP 401). It's missing, expired, "
                    "or lacks the 'repo' scope — re-add a valid GH_TOKEN to "
                    "bot.env.")
        if exc.code == 403:
            return ("GitHub refused the request (HTTP 403) — the token is likely "
                    "rate-limited or missing the 'repo' scope. Re-add a valid "
                    "GH_TOKEN to bot.env.")
        if exc.code == 404:
            return "GitHub says that repo/label doesn't exist (HTTP 404)."
        return f"GitHub HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"GitHub unreachable: {type(exc.reason).__name__}"
    msg = _TOKEN_RE.sub("[redacted]", msg)
    msg = re.sub(r"[A-Za-z]:\\[^\s]+", "<path>", msg)  # Windows paths
    return msg[:300]


def _repo() -> str:
    """owner/repo from the environment, else the default (Nadia's repo)."""
    r = os.environ.get("VRCDJ_REPO", "").strip()
    return r if r and "/" in r else DEFAULT_OWNER_REPO


def _headers(token: str = "") -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "VRCDJ_bot/dj-add",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def create_issue(title: str, body: str, labels: list,
                 token: str = "") -> dict:
    """Create a GitHub issue. Returns {ok, url?, error?}.

    Never raises — the caller (bot command / dashboard) always gets a dict.
    """
    url = f"{API}/repos/{_repo()}/issues"
    payload = {
        "title": (title or "")[:300],
        "body": body or "",
        "labels": labels or [],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            created = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "url": created.get("html_url", url),
                    "number": created.get("number")}
    except urllib.error.HTTPError as he:
        return {"ok": False, "error": _safe_err(he)}
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the bot
        return {"ok": False, "error": _safe_err(exc)}


def list_open_issues(label: str = "dj-addition", token: str = "") -> dict:
    """List the repo's open issues (optionally filtered to one label).

    Returns {ok, issues:[...], error?} where each issue has:
      number, title, created_at, updated_at, html_url, labels[]
    """
    label = (label or "").strip()
    query = f"state=open&per_page=30"
    if label:
        query += f"&labels={urllib.request.quote(label)}"
    url = f"{API}/repos/{_repo()}/issues?{query}"
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            items = json.loads(resp.read().decode("utf-8"))
        issues = []
        for it in items:
            if "pull_request" in it:  # issues endpoint sometimes mixes PRs
                continue
            issues.append({
                "number": it.get("number"),
                "title": it.get("title"),
                "created_at": it.get("created_at"),
                "updated_at": it.get("updated_at"),
                "url": it.get("html_url"),
                "labels": [l.get("name") for l in (it.get("labels") or [])],
            })
        return {"ok": True, "issues": issues}
    except urllib.error.HTTPError as he:
        return {"ok": False, "issues": [], "error": _safe_err(he)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "issues": [], "error": _safe_err(exc)}


def close_issue(number: int, token: str = "",
                comment: str = "") -> dict:
    """Close a GitHub issue (used to clean up test issues, or by the owner
    after reviewing a real suggestion). Returns {ok, error?}. Never raises."""
    url = f"{API}/repos/{_repo()}/issues/{int(number)}"
    payload = {"state": "closed"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PATCH",
                                 headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as he:
        return {"ok": False, "error": _safe_err(he)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _safe_err(exc)}


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    print("repo target:", _repo())
    print("\n── list open 'dj-addition' issues (public, no token) ──")
    r = list_open_issues("dj-addition")
    print("ok:", r["ok"], "| error:", r.get("error"))
    for it in r.get("issues", [])[:10]:
        print(f"  #{it['number']}  {it['created_at']}  {it['title'][:60]}")
    if not r.get("issues"):
        print("  (none yet — that's the normal empty state)")
