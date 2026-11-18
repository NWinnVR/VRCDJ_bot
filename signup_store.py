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

# A person's limits are enforced PER EVENT (see toggle_slot): up to
# `max_per_person` slots on a given event, and up to `max_per_slot` people on
# any one slot.  A limit of `UNLIMITED` (<= 0) means "no limit" for that
# half.  (There is deliberately NO cross-event cap — a person may hold slots
# on as many different events as they like.)

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
def clear_user(msg_id: str, user_id: int) -> dict:
    """Remove `user_id` from EVERY slot of event `msg_id` (the panel's
    **Clear** button).  Returns:
      {"ok": bool, "removed": int, "assignments": {str: [ids]}}
    `removed` is the number of slots the user was actually on (0 is fine —
    the button still works as a no-op confirmation).
    """
    with _lock:
        data = _read(STATE_PATH)
        rec = data["events"].get(str(msg_id))
        if rec is None:
            return {"ok": False, "removed": 0, "assignments": {}}
        uid = int(user_id)
        asg = rec.setdefault("assignments", {})
        removed = 0
        for idx in list(asg.keys()):
            ids = [int(x) for x in asg.get(idx, [])]
            if uid in ids:
                ids.remove(uid)
                removed += 1
                if ids:
                    asg[idx] = ids
                else:
                    asg.pop(idx, None)
        if removed:
            rec["updated_at"] = time.time()
            _write(data)
        return {"ok": True, "removed": removed, "assignments": asg}


def create_event(msg_id: str, guild_id: str, channel_id: str,
                 title: str, slots: list, slot_seconds: int,
                 doors_ts: Optional[int], max_per_slot: int,
                 max_per_person: int, host_id: int, notes: str = "",
                 pre_slot_notes: str = "", slot_extras: Optional[dict] = None,
                 slot_pre_extras: Optional[dict] = None) -> dict:
    """Register a NEW event post.  `slots` is the list of slot timestamps;
    the host's free-form text — `notes` (top bullets), `pre_slot_notes`
    (mid-zone bullets, between the click line and the slots) and per-slot
    text on both sides of each token (`slot_extras` trailing / e.g. DJ name,
    `slot_pre_extras` inline before it) — is kept so every re-render keeps them."""
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
            # Host free-form text (re-emitted on every render; empty = none).
            "notes": str(notes or ""),
            "pre_slot_notes": str(pre_slot_notes or ""),
            # {str slot_index: text} — JSON keys are strings.
            "slot_extras": ({str(k): v for k, v in (slot_extras or {}).items()
                             if v}),
            "slot_pre_extras": ({str(k): v for k, v in (slot_pre_extras or {}).items()
                                 if v}),
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
        # Per-PERSON limit: enforced for EVERYONE, host included (a host who
        # set per-person=N is capped to N slots).  A value <= 0 (UNLIMITED)
        # means no cap at all — the check is simply skipped.
        mine = sum(1 for v in asg.values() if uid in [int(x) for x in v])
        if max_per_person > 0 and mine >= max_per_person:
            return {"ok": False, "state": "limit_person", "assignments": asg,
                    "reason": f"you can hold at most {max_per_person} "
                              f"slot(s) here"}
        # Per-SLOT limit: enforced for everyone EXCEPT the host (a host may
        # place their own name on a slot even when it is already full).
        # A value <= 0 (UNLIMITED) means the slot never fills — skipped.
        if max_per_slot > 0 and not is_host and len(ids) >= max_per_slot:
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
               max_per_person: int, notes: str = "",
               pre_slot_notes: str = "", slot_extras: Optional[dict] = None,
               slot_pre_extras: Optional[dict] = None) -> dict:
    """Update an event's times/info.  PRESERVES filled slots by timestamp.

    `slots` is the NEW list of slot timestamps.  Every existing assignment is
    re-mapped onto the new list by matching timestamp:
      * slot still present (same timestamp, possibly new position) -> kept
      * slot moved / dropped (timestamp gone) -> its names are dropped
      * brand-new slot -> starts empty

    Host free-form text follows the same rules: `notes` / `pre_slot_notes`
    are REPLACED by what's submitted (the edit modal is always prefilled with
    them, so "what you see is what gets saved" — and a host who deletes them
    in the modal clears them); existing per-slot extras (BOTH sides: trailing
    `slot_extras` + inline pre-token text) travel WITH their slot by timestamp,
    and any NEW text on a slot line overrides it.  A slot whose timestamp
    disappears takes its extras with it (same as names).

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

        # --- host free-form text --------------------------------------
        # Existing extras travel by timestamp (like assignments); the host's
        # freshly-typed text on each slot line overrides it.  Both sides:
        # trailing (`slot_extras`) and pre-token (`slot_pre_extras`).
        def _remap_extra(old_dict, new_dict) -> dict:
            old_by_ts = {}
            for k, v in (old_dict or {}).items():
                try:
                    oi = int(k)
                except (TypeError, ValueError):
                    continue
                if 0 <= oi < len(old_slots) and v:
                    old_by_ts[old_slots[oi]] = str(v)
            remapped = {}
            for k, v in (new_dict or {}).items():
                try:
                    ni2 = int(k)
                except (TypeError, ValueError):
                    continue
                if 0 <= ni2 < len(new_slots) and v and str(v).strip():
                    remapped[str(ni2)] = str(v)
            for ni3, ts in enumerate(new_slots):
                if str(ni3) not in remapped and old_by_ts.get(ts):
                    remapped[str(ni3)] = old_by_ts[ts]
            return remapped

        new_extras = _remap_extra(rec.get("slot_extras"), slot_extras)
        new_pre_extras = _remap_extra(rec.get("slot_pre_extras"),
                                      slot_pre_extras)

        rec["title"] = title
        rec["slot_timestamps"] = new_slots
        rec["slot_seconds"] = int(slot_seconds)
        rec["doors_ts"] = int(doors_ts) if doors_ts else None
        rec["max_per_slot"] = int(max_per_slot)
        rec["max_per_person"] = int(max_per_person)
        rec["notes"] = str(notes or "")
        rec["pre_slot_notes"] = str(pre_slot_notes or "")
        rec["slot_extras"] = new_extras
        rec["slot_pre_extras"] = new_pre_extras
        rec["assignments"] = new_asg
        rec["updated_at"] = time.time()
        _write(data)
        return {"assignments": new_asg, "preserved": preserved,
                "lost": len(old_slots) - len([t for t in old_slots if t in set(new_slots)])}


# ---------------------------------------------------------------------------
# Self-test  (run: python signup_store.py)  — uses a throwaway file.
# ---------------------------------------------------------------------------
def _self_test() -> int:
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

    print("=== 4b. notes + slot_extras persist & remap on edit ===")
    Mx = "msgNotes"
    create_event(Mx, "g", "c", "Guest Night", S, 3600, 1788669900, -1, -1,
                 host_id=1, notes="- Bring your own drinks!",
                 pre_slot_notes="- Special guest night!\n- Late set until 2am.",
                 slot_extras={1: "Kiki", 2: "Rex"},
                 slot_pre_extras={0: "teams dfg"})
    rx = get_event(Mx)
    check("notes stored verbatim", rx["notes"] == "- Bring your own drinks!",
          rx.get("notes"))
    check("mid notes stored verbatim",
          rx["pre_slot_notes"] == "- Special guest night!\n- Late set until 2am.",
          rx.get("pre_slot_notes"))
    check("extras stored (str keys)", rx["slot_extras"] == {"1": "Kiki", "2": "Rex"},
          rx.get("slot_extras"))
    check("pre-extras stored (str keys)", rx["slot_pre_extras"] == {"0": "teams dfg"},
          rx.get("slot_pre_extras"))
    # Move: keep ts 1788678000 (=old idx2, "Rex") as new slot #1, and old idx1
    # (ts 1788674400, "Kiki") — Rex travels to index 0.
    evx = edit_event(Mx, "Guest Night v2", [1788678000, 1788674400], 3600,
                     None, -1, -1)
    rx = get_event(Mx)
    check("extras moved with their timestamps (Rex->0, Kiki->1)",
          rx["slot_extras"] == {"0": "Rex", "1": "Kiki"}, rx.get("slot_extras"))
    # The pre-extra sat on old idx0 (ts 1788670800) — that timestamp was DROPPED,
    # so it travels nowhere and is gone.
    check("pre-extras dropped with their timestamp",
          rx["slot_pre_extras"] == {}, rx.get("slot_pre_extras"))
    # What-you-see-is-what-saves: no notes in the edit submission -> cleared.
    check("notes cleared when the modal had none left",
          rx["notes"] == "", repr(rx.get("notes")))
    check("mid notes cleared too (not resubmitted)",
          rx["pre_slot_notes"] == "", repr(rx.get("pre_slot_notes")))
    # Host edits the same modal: new trailing text OVERRIDES, notes replaced.
    edit_event(Mx, "Guest Night v3", [1788678000, 1788674400], 3600, None,
               -1, -1, notes="- Doors close at 3am.",
               pre_slot_notes="- Doors open late!", slot_extras={0: "New DJ"},
               slot_pre_extras={1: "fdsa"})
    rx = get_event(Mx)
    check("new extra overrides; untouched extras stay",
          rx["slot_extras"] == {"0": "New DJ", "1": "Kiki"},
          rx.get("slot_extras"))
    check("notes replaced", rx["notes"] == "- Doors close at 3am.",
          rx.get("notes"))
    check("mid notes stored on edit", rx["pre_slot_notes"] == "- Doors open late!",
          rx.get("pre_slot_notes"))
    check("new pre-extra lands; none elsewhere",
          rx["slot_pre_extras"] == {"1": "fdsa"}, rx.get("slot_pre_extras"))

    print("=== 5. UNLIMITED per-person lets a user take every slot ===")
    # Fresh event, per-person=UNLIMITED(-1), per-slot=UNLIMITED.
    M2 = "msgUnlim"
    create_event(M2, "g", "c", "Open", [1788670800, 1788674400, 1788678000],
                 3600, 1788669900, -1, -1, host_id=1)
    rU1 = toggle_slot(M2, 0, 700)
    rU2 = toggle_slot(M2, 1, 700)
    rU3 = toggle_slot(M2, 2, 700)
    check("user 700 on slot #1", rU1["state"] == "added", rU1)
    check("user 700 on slot #2 (no per-person cap)", rU2["state"] == "added", rU2)
    check("user 700 on slot #3 (no per-person cap)", rU3["state"] == "added", rU3)
    # And a SECOND person can pile onto the SAME slot (no per-slot cap).
    rU4 = toggle_slot(M2, 0, 800)
    check("user 800 joins full-looking slot #1 (no per-slot cap)",
          rU4["state"] == "added", rU4)
    recU = get_event(M2)
    check("slot #1 holds both 700 and 800",
          sorted(recU["assignments"].get("0", [])) == [700, 800],
          recU["assignments"])
    # A LIMITED per-person event still caps (regression guard for the
    # unlimited code path not leaking into limited events).
    M3 = "msgLimited"
    create_event(M3, "g", "c", "Capped", [1788670800, 1788674400, 1788678000],
                 3600, 1788669900, 1, 1, host_id=1)
    rL1 = toggle_slot(M3, 0, 900)
    rL2 = toggle_slot(M3, 1, 900)
    check("limited event: first slot ok", rL1["state"] == "added", rL1)
    check("limited event: second slot blocked (per-person=1)",
          rL2["state"] == "limit_person", rL2)

    # --- 6. clear_user: the panel's **Clear** button ---------------------
    # Use M2 (unlimited per-person) — user 700 is already on slots 0,1,2
    # from §5, which is a perfect 3-slot setup for a one-tap clear.
    print("=== 6. clear_user removes the user from every slot ===")
    rec = get_event(M2)
    on_before = sum(1 for v in rec["assignments"].values() if 700 in [int(x) for x in v])
    check("setup: user 700 on 3 slots (M2 unlimited)", on_before == 3, on_before)
    cr = clear_user(M2, 700)
    rec2 = get_event(M2)
    on_after = sum(1 for v in rec2["assignments"].values() if 700 in [int(x) for x in v])
    check("clear_user: reports 3 removed", cr["removed"] == 3, cr)
    check("clear_user: user 700 gone from every slot", on_after == 0, on_after)
    # Other users on M2 (e.g. 800 on slot 0) must be untouched.
    check("clear_user: leaves other users alone",
          800 in [int(x) for x in rec2["assignments"].get("0", [])],
          rec2["assignments"])
    # Clearing a user who is on nothing is a harmless no-op.
    cr2 = clear_user(M2, 700)
    check("clear_user: no-op when already clear", cr2["removed"] == 0, cr2)
    # Clearing on a missing event is safe.
    cr3 = clear_user("no-such-event", 100)
    check("clear_user: missing event ok=False", cr3["ok"] is False, cr3)

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
