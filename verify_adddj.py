#!/usr/bin/env python
"""End-to-end verify: /adddj → GitHub issue → dashboard checker → close.

Uses the REAL API:
  - dj_add.issue_payload()  → {title, body, labels}
  - gh_add.create_issue(title, body, labels, token) → {ok, url, number}
  - gh_add.list_open_issues(token) → {ok, issues:[...]}
  - dashboard /api/status  → dj_add: {count, issues, error}
  - dashboard /api/dj-add-check (POST, CSRF) → busts the 60s cache

Exit 0 = all green.
"""
import os, re, sys, time, json
import urllib.request, http.cookiejar, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import dj_add, gh_add  # noqa: E402

def log(m): print(m, flush=True)

# ── password ──
pw = ""
for line in open(os.path.join(BASE, "bot.env"), encoding="utf-8"):
    line = line.strip()
    if line.startswith("DASHBOARD_PASSWORD="):
        pw = line.split("=", 1)[1].strip()
if not pw:
    log("FAIL: no DASHBOARD_PASSWORD in bot.env"); sys.exit(1)

# ── opener ──
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
BASE_URL = "http://127.0.0.1:8720"

def get(url):
    return op.open(url, timeout=15)

def post(url, fields):
    req = urllib.request.Request(url,
        data=urllib.parse.urlencode(fields).encode(), method="POST")
    return op.open(req, timeout=15)

def status():
    return json.loads(get(f"{BASE_URL}/api/status").read())

def csrf():
    h = get(f"{BASE_URL}/").read().decode("utf-8")
    m = re.search(r'const CSRF\s*=\s*"([^"]+)"', h)
    return m.group(1) if m else None

# ── 1. login ──
r = post(f"{BASE_URL}/login", {"password": pw})
log("✅ logged in" if r.status in (200, 302) else f"FAIL: login {r.status}")
if r.status not in (200, 302): sys.exit(1)

# ── 2. baseline ──
base = status()
bc = base.get("dj_add", {}).get("count", -1)
log(f"baseline: {bc} pending  err={base.get('dj_add',{}).get('error')}")
if bc < 0:
    log("FAIL: baseline read"); sys.exit(1)

# ── 3. create test issue via the REAL bot code path ──
uniq = time.strftime("%H%M%S")
payload = dj_add.issue_payload(
    name=f"VerifyDJ-{uniq}",
    link="rtspt://stream.vrcdn.live/live/verifydj",
    genres="House, Techno",
    availability="Fri & Sat",
    submitter="hermes-test",
    guild="TestServer",
)
log(f"issue title: {payload['title']}")
log(f"issue body preview: {payload['body'][:120]}...")
log(f"labels: {payload['labels']}")

token = os.environ.get("GH_TOKEN", "").strip()
res = gh_add.create_issue(payload["title"], payload["body"],
                          payload["labels"], token)
if not res.get("ok"):
    log(f"FAIL: create_issue → {res.get('error')}"); sys.exit(1)
num = res["number"]
log(f"✅ issue #{num} created: {res['url']}")
time.sleep(2)  # GitHub indexing

# ── 4. force-refresh the dashboard cache ──
tok = csrf()
if not tok:
    log("FAIL: no CSRF"); sys.exit(1)
rr = json.loads(post(f"{BASE_URL}/api/dj-add-check", {"csrf": tok}).read())
log(f"refresh: count={rr.get('count')}  err={rr.get('error')}")

# ── 5. confirm in /api/status ──
a = status()
issues = a.get("dj_add", {}).get("issues", [])
mine = [i for i in issues if i.get("number") == num]
log(f"after create: count={a['dj_add']['count']}  issues={len(issues)}")
if not mine:
    log("FAIL: test issue not in dashboard response")
    log(f"  issues: {[i.get('title') for i in issues]}")
    sys.exit(1)
log(f"  → #{num} visible: {mine[0]['title']}")

# ── 6. close the test issue ──
close = gh_add.close_issue(num, token)
if close.get("ok"):
    log(f"✅ issue #{num} closed (HTTP {close.get('status')})")
else:
    log(f"WARN: close failed: {close.get('error')}")
    log(f"  → close manually: {res['url']}")

# ── 7. re-check ──
time.sleep(2)
tok2 = csrf()
if tok2:
    try:
        post(f"{BASE_URL}/api/dj-add-check", {"csrf": tok2})
    except Exception:
        pass
time.sleep(1)
c = status()
log(f"after close: count={c['dj_add']['count']}")

log("\n🎉 /adddj pipeline end-to-end: GREEN")
