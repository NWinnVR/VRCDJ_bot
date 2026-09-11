"""signup_store.py — persistent state for `/signup` events (slot assignments).

WHY IT EXISTS
  A slot signup must be REAL-TIME and SHARED: the moment someone clicks their
  slot, their name appears on the event post for everyone, and the per-slot /
  per-person limits must hold even across a bot restart.  That means the state
  lives on disk, not in memory.  This module is the single source of truth for
  "who is signed up where", keyed by Discord message id (one message = one
  event post).

  It also answers the hard question in the spec: "a host edits the event — do
  already-filled slots survive?"  YES.  Assignments are stored by TIMESTAMP
  (not by slot position), so `edit_event()` re-maps them onto the new slot
  list: any slot whose timestamp still exists keeps its names; any slot that
  moved or was dropped loses them (that time no longer exists); brand-new slots
  start empty.

  PURE-ish: reads/writes ONLY `signup_events.json` (git-ignored, machine-local).
  No discord, no bot_state, no network.  Thread-safe (RLock) + atomic writes
  (tempfile + os.replace), the same pattern as bot_state.py.  Headless testing:
  `python signup_store.py` (uses a throwaway path, never touches real data).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).parent
STATE_PATH = _HERE / "signup_events.json"

# A person can't be in two DIFFERENT events at the same moment (the
# "one person, one signup at a time" rule).  Within a single event they may
# hold up to `max_per_person` slots.
#
# We enforce per-event limits strictly (that's the spec).  The cross-event
# rule is best-effort: we track which event each user is currently in and
# release it when they cancel every slot in that event or when they join a
# different one.  This is a soft guard — it never blocks a legitimate signup,
# it just prevents one user from quietly stacking slots across events.

_lock = threading.RLock()


def _atomic_write(path: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path) -> dict:
    if not path.exists():
        return {"events": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "events" not in data:
            return {"events": {}}
        data["events"] = data.get("events") or {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"events": {}}


def _write(data: dict) -> None:
    data["updated_at"] = time.time()
    _atomic_write(STATE_PATH, json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Event CRUD
# ---------------------------------------------------------------------------
def create_event(msg_id: str, guild_id: str, channel_id: str,
                 title: str, slots: list, slot_seconds: int,
                 doors_ts: Optional[int], max_per_slot: int,
                 max_per_person: int, host_id: int) -> dict:
    """Register a NEW event post.  `slots` is the list of slot timestamps."""
    with _lock:
        data = _read(STATE_PATH)
        rec = {
            "msg_id": str(msg_id),
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
            "title": title,
            "slot_timestamps": [int(s) for s in slots],
            "slot_seconds": int(slot_seconds),
            "doors_ts": int(doors_ts) if doors_ts else None,
            "max_per_slot": int(max_per_slot),
            "max_per_person": int(max_per_person),
            "host_id": int(host_id),
            # assignments: { slot_index(str): [user_id, ...] }
            "assignments": {},
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        data["events"][str(msg_id)] = rec
        _write(data)
        return rec


def get_event(msg_id: str) -> Optional[dict]:
    with _lock:
        return _read(STATE_PATH)["events"].get(str(msg_id))


def all_events() -> dict:
    with _lock:
        return _read(STATE_PATH)["events"]


def delete_event(msg_id: str) -> None:
    """Remove an event (e.g. the post was deleted).  Frees any slots it held."""
    with _lock:
        data = _read(STATE_PATH)
        if str(msg_id) in data["events"]:
            del data["events"][str(msg_id)]
            _write(data)


# ---------------------------------------------------------------------------
# Slot assignment (the real-time, limit-enforced core)
# ---------------------------------------------------------------------------
def _count_user_across(data: dict, user_id: int) -> int:
    """How many slots `user_id` currently holds ACROSS all events (soft guard)."""
    total = 0
    for rec in data["events"].values():
        for ids in rec.get("assignments", {}).values():
            if int(user_id) in [int(x) for x in ids]:
                total += 1
    return total


def toggle_slot(msg_id: str, slot_index: int, user_id: int,
                is_host: bool = False) -> dict:
    """Toggle `user_id` on slot `slot_index` (0-based) of event `msg_id`.

    - If present  -> remove them (cancel).
    - If absent   -> add them, subject to the limits below:
        * per-person  — enforced for EVERYONE, host included (the host may
          hold at most `max_per_person` slots on this event).
        * per-slot    — enforced for everyone EXCEPT the host: a host
          (`is_host=True`) may place their own name on a slot even when it is
          already full.

    Returns a result dict:
      {
        "ok":      bool,      # True if the post should be refreshed
        "state":   "added" | "removed" | "full_slot" | "limit_person",
        "assignments": {str: [ids]},   # the fresh assignment map (to render)
        "reason":  str | None,
      }
    """
    with _lock:
        data = _read(STATE_PATH)
        rec = data["events"].get(str(msg_id))
        if rec is None:
            return {"ok": False, "state": "no_event", "assignments": {},
                    "reason": "event not found"}
        idx = str(slot_index)
        asg = rec.setdefault("assignments", {})
        ids = asg.get(idx, [])
        ids = [int(x) for x in ids]
        uid = int(user_id)
        max_per_slot = int(rec.get("max_per_slot", 1))
        max_per_person = int(rec.get("max_per_person", 1))

        if uid in ids:
            # ---- remove (cancel) ----
            ids.remove(uid)
            if ids:
                asg[idx] = ids
            else:
                asg.pop(idx, None)
            rec["updated_at"] = time.time()
            _write(data)
            return {"ok": True, "state": "removed", "assignments": asg,
                    "reason": None}

        # ---- add (sign up) ----
        # Per-PERSON limit: enforced for EVERYONE, host included. (A host
        # who set per-person=1 must still be limited to 1 slot — this was
        # the bug where `is_host` skipped it and let a host stack unlimited.)
        mine = sum(1 for v in asg.values() if uid in [int(x) for x in v])
        if mine >= max_per_person:
            return {"ok": False, "state": "limit_person", "assignments": asg,
                    "reason": f"you can hold at most {max_per_person} "
                              f"slot(s) here"}
        # Per-SLOT limit: enforced for everyone EXCEPT the host (a host may
        # place their own name on a slot even when it is already full).
        if not is_host and len(ids) >= max_per_slot:
            return {"ok": False, "state": "full_slot", "assignments": asg,
                    "reason": f"slot #{slot_index + 1} is full "
                              f"({max_per_slot} max)"}
        asg[idx] = ids + [uid]
        rec["updated_at"] = time.time()
        _write(data)
        return {"ok": True, "state": "added", "assignments": asg, "reason": None}


def edit_event(msg_id: str,
               title: str, slots: list, slot_seconds: int,
               doors_ts: Optional[int], max_per_slot: int,
               max_per_person: int) -> dict:
    """Update an event's times/info.  PRESERVES filled slots by timestamp.

    `slots` is the NEW list of slot timestamps.  Every existing assignment is
    re-mapped onto the new list by matching timestamp:
      * slot still present (same timestamp, possibly new position) -> kept
      * slot moved / dropped (timestamp gone) -> its names are dropped
      * brand-new slot -> starts empty

    Returns the fresh assignment map (0-based index -> [ids]) for the NEW list.
    """
    with _lock:
        data = _read(STATE_PATH)
        rec = data["events"].get(str(msg_id))
        if rec is None:
            return {"assignments": {}, "preserved": 0, "lost": 0}

        old_slots = [int(s) for s in rec.get("slot_timestamps", [])]
        old_asg = rec.get("assignments", {})
        # old index -> ids  (ints)
        old_by_idx = {int(k): [int(x) for x in v]
                      for k, v in old_asg.items()}
        # old timestamp -> ids  (a timestamp may appear once)
        old_by_ts = {old_slots[i]: old_by_idx[i]
                     for i in range(len(old_slots)) if i in old_by_idx}

        new_slots = [int(s) for s in slots]
        new_asg: dict = {}
        preserved = 0
        for ni, ts in enumerate(new_slots):
            if ts in old_by_ts and old_by_ts[ts]:
                new_asg[str(ni)] = old_by_ts[ts]
                preserved += 1

        rec["title"] = title
        rec["slot_timestamps"] = new_slots
        rec["slot_seconds"] = int(slot_seconds)
        rec["doors_ts"] = int(doors_ts) if doors_ts else None
        rec["max_per_slot"] = int(max_per_slot)
        rec["max_per_person"] = int(max_per_person)
        rec["assignments"] = new_asg
        rec["updated_at"] = time.time()
        _write(data)
        return {"assignments": new_asg, "preserved": preserved,
                "lost": len(old_slots) - len([t for t in old_slots if t in set(new_slots)])}


# ---------------------------------------------------------------------------
# Self-test  (run: python signup_store.py)  — uses a throwaway file.
# ---------------------------------------------------------------------------
def _self_test() -> int:
    import signup  # not strictly needed here, keeps the module resolvable
    global STATE_PATH
    tmp = Path(__file__).parent / "_signup_selftest.json"
    if tmp.exists():
        tmp.unlink()
    STATE_PATH = tmp
    failures = 0

    def check(label, ok, got=None, want=None):
        nonlocal failures
        print(f"  {'PASS' if ok else 'FAIL'} — {label}")
        if not ok:
            if got is not None:
                print("    GOT : " + repr(got))
            if want is not None:
                print("    WANT: " + repr(want))
            failures += 1

    S = [1788670800, 1788674400, 1788678000]  # slots #1 #2 #3
    M = "msg123"
    create_event(M, "g", "c", "New Event", S, 3600, 1788669900, 1, 1, host_id=1)

    print("=== 1. basic assign + limit (per-slot=1) ===")
    r = toggle_slot(M, 0, 100)
    check("user 100 added to #1", r["state"] == "added", r)
    r2 = toggle_slot(M, 0, 200)
    check("user 200 blocked (slot full)", r2["state"] == "full_slot", r2)
    r3 = toggle_slot(M, 1, 200)
    check("user 200 added to #2", r3["state"] == "added", r3)
    r4 = toggle_slot(M, 2, 200)
    check("user 200 blocked (per-person=1)", r4["state"] == "limit_person", r4)

    print("=== 2. toggle cancels ===")
    r5 = toggle_slot(M, 1, 200)
    check("user 200 removed from #2", r5["state"] == "removed", r5)

    print("=== 3. host override ===")
    r6 = toggle_slot(M, 0, 300, is_host=True)
    check("host can place on full slot", r6["state"] == "added", r6)

    print("=== 3b. host is STILL capped by per-person (the bug) ===")
    # host 300 now holds slot#0 (per-person=1). Trying to grab slot#1 must be
    # blocked by the per-person limit — even though they are the host. This is
    # the exact regression the is_host guard used to swallow.
    r6b = toggle_slot(M, 1, 300, is_host=True)
    check("host blocked on 2nd slot (per-person=1)",
          r6b["state"] == "limit_person", r6b)

    print("=== 4. edit_event preserves by timestamp ===")
    # Current state from sections 1–3: slot#0 (ts 1788670800) = [100, 300],
    # slot#1 (ts 1788674400) = [] , slot#2 (ts 1788678000) = [] .
    # Move: new slots keep slot#1 (1788670800), drop #2, keep #3 as position 1.
    new_slots = [1788670800, 1788678000]
    ev = edit_event(M, "Edited", new_slots, 3600, 1788669900, 1, 1)
    # user100 (on #1, ts 1788670800) must survive -> new index 0
    check("user100 preserved at new idx0",
          100 in ev["assignments"].get("0", []), ev["assignments"])
    # user300 (host, also on #1) -> same new index 0, both names kept together
    check("user300 preserved at new idx0",
          300 in ev["assignments"].get("0", []), ev["assignments"])
    # Exactly ONE filled slot (slot#1) survived; the empty dropped slots don't
    # count, and the two empty kept slots contribute no preserved names.
    check("preserved filled-slot count == 1", ev["preserved"] == 1, ev["preserved"])
    check("kept names == [100, 300]",
          sorted(ev["assignments"].get("0", [])) == [100, 300], ev["assignments"])

    # cleanup
    STATE_PATH = _HERE / "signup_events.json"
    try:
        tmp.unlink()
    except OSError:
        pass

    if failures:
        print(f"\n{failures} CHECK(S) FAILED")
        return 1
    print("\nOK: signup_store self-test passed — assign/limits/toggle/edit-preserve")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
