"""e2e_test.py — headless end-to-end test of the /signup flow (NO live bot,
NO model). Drives the REAL code path with only the Discord network edge faked:

    build_slots -> render_template -> handle_submit(fake interaction)
    -> post_new_event (fake channel) -> SlotPickView pick -> toggle_slot
    -> [host edit] -> _apply_edit -> edit_event preserve -> render_event

Every function the bot actually calls is exercised for real and asserted on.
Run:  python e2e_test.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force the store to a throwaway file so we never touch real state.
import signup_store
from pathlib import Path
_tmpdir = Path(tempfile.mkdtemp(prefix="signup_e2e_"))
signup_store.STATE_PATH = _tmpdir / "signup_events.json"
if signup_store.STATE_PATH.exists():
    signup_store.STATE_PATH.unlink()

import signup
import signup_ui

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS — {label}")
    else:
        FAIL += 1
        print(f"  FAIL — {label}  {extra}")


# ---------------------------------------------------------------------------
# Fake Discord channel + message (records what the bot "posts").
# ---------------------------------------------------------------------------
class FakeMessage:
    def __init__(self, mid):
        self.id = mid
        self.content = ""
        self._view = None

    async def edit(self, content=None, view=None, **kw):
        if content is not None:
            self.content = content
        if view is not None:
            self._view = view
        return self


class FakeChannel:
    def __init__(self, cid, guild_id):
        self.id = cid
        self.guild = type("G", (), {"id": guild_id, "name": "FakeServer"})()
        self.messages = {}
        self._next = 9000

    async def send(self, content=None, view=None, **kw):
        self._next += 1
        m = FakeMessage(self._next)
        m.content = content or ""
        m._view = view
        self.messages[self._next] = m
        return m

    async def fetch_message(self, mid):
        return self.messages[int(mid)]


# ---------------------------------------------------------------------------
# Fake interaction (just enough surface for handle_submit / the button cbs).
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.sent = []

    async def defer(self, ephemeral=False, **kw):
        self.deferred = True

    async def send_message(self, content=None, view=None, ephemeral=False, **kw):
        self.sent.append({"content": content, "view": view,
                          "ephemeral": ephemeral})
        return None


class FakeFollowup:
    def __init__(self, sink):
        self._sink = sink

    async def send(self, content=None, view=None, ephemeral=False, **kw):
        self._sink.append({"content": content, "view": view,
                           "ephemeral": ephemeral})
        return None


class FakeInteraction:
    def __init__(self, channel, uid, uname):
        self.channel = channel
        self.guild = channel.guild
        self.user = type("U", (), {"id": uid, "name": uname,
                                  "guild_permissions": type("P", (),
                                  {"manage_guild": False,
                                   "administrator": False})()})()
        self.response = FakeResponse()
        self.followup = FakeFollowup(self.response.sent)


def find_button(view, custom_id):
    for item in view.children:
        if getattr(item, "custom_id", None) == custom_id:
            return item
    return None


async def run():
    print("=== /signup end-to-end (headless) ===\n")

    # ------------------------------------------------------------------
    # 1) Host computes slots (golden example: 8 slots, 1h, from 1788670800).
    # ------------------------------------------------------------------
    start = 1788670800
    slot_ts = signup.build_slots(str(start), "8", "1h")
    check("build_slots -> 8 slots", len(slot_ts) == 8, f"got {len(slot_ts)}")
    check("slot 1 == start", slot_ts[0] == start)
    check("slot spacing 60min", slot_ts[1] == start + 3600)

    doors_lead = 15
    doors_ts = slot_ts[0] - doors_lead * 60

    # ------------------------------------------------------------------
    # 2) Modal prefill + parse round-trip.
    # ------------------------------------------------------------------
    prefill = signup.render_template("New Event", slot_ts, 3600, doors_ts)
    parsed = signup.parse_event_text(prefill)
    check("parse: title", parsed["title"] == "New Event", repr(parsed["title"]))
    check("parse: 8 slots", len(parsed["slots"]) == 8, f"got {len(parsed['slots'])}")
    check("parse: 3600s slot_seconds", parsed["slot_seconds"] == 3600,
          str(parsed["slot_seconds"]))
    check("parse: doors back", parsed["doors_ts"] == doors_ts,
          str(parsed["doors_ts"]))

    # ------------------------------------------------------------------
    # 3) Host submits the modal -> handle_submit creates the board.
    # ------------------------------------------------------------------
    channel = FakeChannel(cid=555, guild_id=777)
    host_uid, host_uname = 100, "Host"
    itx = FakeInteraction(channel, host_uid, host_uname)
    await signup_ui.handle_submit(
        itx, name="New Event", block=prefill, msg_id=None,
        max_per_slot=1, max_per_person=2)
    check("defer called", itx.response.deferred)
    follow = itx.response.sent
    check("a followup was sent", len(follow) >= 1, f"sent={follow}")
    if follow:
        check("followup confirms posted",
              "Event posted" in (follow[-1]["content"] or ""),
              follow[-1]["content"])

    posted_msg = None
    for m in channel.messages.values():
        if m._view is not None:
            posted_msg = m
            break
    check("board was posted with a view", posted_msg is not None)
    msg_id = str(posted_msg.id)
    check("msg id is numeric", msg_id.isdigit(), repr(msg_id))

    rec = signup_store.get_event(msg_id)
    check("store has the event", rec is not None)
    check("store host id == 100", rec is not None and int(rec.get("host_id", -1)) == 100)
    check("store has 8 slot_timestamps",
          rec is not None and len(rec.get("slot_timestamps", [])) == 8)

    # ------------------------------------------------------------------
    # 4) The posted view has [Pick Slot] and [Edit Event].
    # ------------------------------------------------------------------
    view = posted_msg._view
    pick = find_button(view, signup_ui._pickbtn_id(msg_id))
    edit = find_button(view, signup_ui._editbtn_id(msg_id))
    check("posted view has [Pick Slot]", pick is not None)
    check("posted view has [Edit Event]", edit is not None)
    check("[Pick Slot] label", pick is not None and pick.label == "Pick Slot")
    check("[Edit Event] label", edit is not None and edit.label == "Edit Event")
    check("[Pick Slot] is green", pick is not None and pick.style.name == "success")
    check("[Edit Event] is secondary", edit is not None and edit.style.name == "secondary")

    # ------------------------------------------------------------------
    # 5) The pick grid renders #1..#8 in order with decodable custom_ids.
    # ------------------------------------------------------------------
    grid = signup_ui.SlotPickView(msg_id, 8)
    labels = [c.label for c in grid.children]
    check("slot grid has 8 buttons", len(labels) == 8, str(labels))
    check("slot grid labels #1..#8",
          labels == [f"#{i}" for i in range(1, 9)], str(labels))
    idxs = [signup_ui.parse_slot(c.custom_id) for c in grid.children]
    check("slot custom_ids decode to (msg_id, 0..7)",
          all(x is not None and x[0] == msg_id for x in idxs) and
          [x[1] for x in idxs] == list(range(8)), str(idxs))

    # ------------------------------------------------------------------
    # 6) A user claims slot 0 via the button callback (real code path).
    # ------------------------------------------------------------------
    dj_uid = 200
    dj_itx = FakeInteraction(channel, dj_uid, "Mous")
    btn = find_button(grid, signup_ui._slot_id(msg_id, 0))
    check("slot 0 button exists in grid", btn is not None)
    await btn.callback(dj_itx)
    check("dj gets a confirmation", len(dj_itx.response.sent) >= 1,
          f"sent={dj_itx.response.sent}")
    if dj_itx.response.sent:
        check("confirmation says slot #1",
              "slot #1" in (dj_itx.response.sent[-1]["content"] or ""),
              dj_itx.response.sent[-1]["content"])

    rec = signup_store.get_event(msg_id)
    asg = rec["assignments"]
    check("user 200 on slot 0 in store", 200 in [int(x) for x in asg.get("0", [])],
          str(asg))

    # The public post was refreshed and now shows the mention.
    refreshed = channel.messages[int(msg_id)]
    check("refreshed board contains <@200>", "<@200>" in refreshed.content,
          refreshed.content[:200])
    check("refreshed board still has slot #2 (empty)",
          "#2, <t:%d:t>" % slot_ts[1] in refreshed.content)

    # ------------------------------------------------------------------
    # 7) Limits: per_slot=1 blocks user 300 on the same slot.
    # ------------------------------------------------------------------
    other_itx = FakeInteraction(channel, 300, "Other")
    await btn.callback(other_itx)
    if other_itx.response.sent:
        check("user 300 gets full_slot message",
              "full" in (other_itx.response.sent[-1]["content"] or "").lower(),
              other_itx.response.sent[-1]["content"])
    rec = signup_store.get_event(msg_id)
    slot0 = [int(x) for x in rec["assignments"].get("0", [])]
    check("user 300 NOT on slot 0", 300 not in slot0, str(slot0))
    check("user 200 still on slot 0", 200 in slot0, str(slot0))

    # ------------------------------------------------------------------
    # 8) Toggle: user 200 cancels (clicks the same button again).
    # ------------------------------------------------------------------
    dj_itx2 = FakeInteraction(channel, dj_uid, "Mous")
    await btn.callback(dj_itx2)
    rec = signup_store.get_event(msg_id)
    slot0 = [int(x) for x in rec["assignments"].get("0", [])]
    check("toggle removes user 200 from slot 0", 200 not in slot0, str(slot0))

    # ------------------------------------------------------------------
    # 9) Host edit: shift all slots by +1h — user 200 on old slot 0 should
    #    be DROPPED (its timestamp no longer exists), per the store contract.
    # ------------------------------------------------------------------
    # Re-fill slot 0 with a NEW user first so we can test preservation.
    fill_itx = FakeInteraction(channel, 400, "Dancer1")
    await btn.callback(fill_itx)
    rec = signup_store.get_event(msg_id)
    check("user 400 now on slot 0",
          400 in [int(x) for x in rec["assignments"].get("0", [])],
          str(rec["assignments"]))

    # Now edit: shift ALL slots +1h (old slot 0 ts is gone -> user 400 lost).
    new_slots = [t + 3600 for t in slot_ts]
    await signup_ui._apply_edit(
        channel, msg_id, "New Event", new_slots, 3600, doors_ts + 3600,
        max_per_slot=1, max_per_person=2)
    rec = signup_store.get_event(msg_id)
    check("event updated to new slot times",
          rec["slot_timestamps"][0] == slot_ts[0] + 3600,
          str(rec["slot_timestamps"][:2]))
    check("old slot 0 user (400) was dropped (ts no longer exists)",
          400 not in [int(x) for v in rec["assignments"].values()
                      for x in v],
          str(rec["assignments"]))

    # ------------------------------------------------------------------
    # 10) Host edit PRESERVES a slot whose timestamp survives.
    #     Put user 500 on new slot 0, then re-edit keeping slot 0's ts.
    # ------------------------------------------------------------------
    # New slot 0 is at slot_ts[0]+3600. Fill it.
    # (slot 0 button in the new grid)
    new_grid = signup_ui.SlotPickView(msg_id, 8)
    btn0 = find_button(new_grid, signup_ui._slot_id(msg_id, 0))
    fill2_itx = FakeInteraction(channel, 500, "Dancer2")
    await btn0.callback(fill2_itx)
    rec = signup_store.get_event(msg_id)
    check("user 500 on new slot 0",
          500 in [int(x) for x in rec["assignments"].get("0", [])],
          str(rec["assignments"]))

    # Edit with the SAME slot list (only the title changes).
    await signup_ui._apply_edit(
        channel, msg_id, "Edited Title", new_slots, 3600, doors_ts + 3600,
        max_per_slot=1, max_per_person=2)
    rec = signup_store.get_event(msg_id)
    check("edit kept user 500 on slot 0 (same ts)",
          500 in [int(x) for x in rec["assignments"].get("0", [])],
          str(rec["assignments"]))
    check("edit updated the title", rec["title"] == "Edited Title",
          rec["title"])

    # ------------------------------------------------------------------
    # 11) Restart safety: state is on disk and readable.
    # ------------------------------------------------------------------
    fresh = signup_store.get_event(msg_id)
    check("state persisted to disk", fresh is not None and
          "slot_timestamps" in fresh and "assignments" in fresh)
    all_events = signup_store.all_events()
    check("all_events lists this event", msg_id in all_events,
          str(list(all_events)))

    print(f"\n{'=' * 48}")
    print(f"  {PASS} passed, {FAIL} failed")
    print(f"{'=' * 48}")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    print("\n" + ("OK: end-to-end flow passed" if ok else "FAILED: see above"))
    sys.exit(0 if ok else 1)
