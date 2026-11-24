import threading
from datetime import datetime, timedelta

import pytest

import guild_event_signup_guard as sg

T0 = "2026-11-24T10:00:00+00:00"
OPEN = "2026-11-24T09:00:00+00:00"
CLOSE = "2026-11-24T11:00:00+00:00"
START = "2026-11-24T12:00:00+00:00"


def ts(base, seconds):
    return (datetime.fromisoformat(base) + timedelta(seconds=seconds)).isoformat()


def make_event(guard, *, event_id="ev-1", capacity=4, open_at=OPEN,
               close_at=CLOSE, start_at=START, name="星火团本"):
    return guard.create_event(
        event_id,
        name=name,
        capacity=capacity,
        start_at=start_at,
        signup_open_at=open_at,
        signup_close_at=close_at,
    )


def open_event(guard, **kwargs):
    event_id = kwargs.get("event_id", "ev-1")
    make_event(guard, **kwargs)
    guard.open_signup(event_id, now=T0)


def test_create_event_validation_and_summary():
    guard = sg.EventSignupGuard()
    row = guard.create_event(
        "ev-1", name="星火团本", capacity=4, start_at=START,
        signup_open_at=OPEN, signup_close_at=CLOSE)
    assert row == {
        "event_id": "ev-1",
        "name": "星火团本",
        "capacity": 4,
        "status": "draft",
        "start_at": START,
        "signup_open_at": OPEN,
        "signup_close_at": CLOSE,
        "confirmed": 0,
        "waitlisted": 0,
    }
    with pytest.raises(sg.SignupError) as exc:
        guard.create_event("ev-1", name="重复登记", capacity=4,
                           start_at=START, signup_open_at=OPEN)
    assert exc.value.code == "SIGNUP_EVENT_EXISTS"

    bad_specs = [
        dict(name="", capacity=4, start_at=START, signup_open_at=OPEN,
             signup_close_at=CLOSE),
        dict(name="x", capacity=0, start_at=START, signup_open_at=OPEN,
             signup_close_at=CLOSE),
        dict(name="x", capacity="4", start_at=START, signup_open_at=OPEN,
             signup_close_at=CLOSE),
        dict(name="x", capacity=4, start_at=OPEN, signup_open_at=OPEN,
             signup_close_at=OPEN),
        dict(name="x", capacity=4, start_at=START, signup_open_at=OPEN,
             signup_close_at="2026-11-24T13:00:00+00:00"),
        dict(name="x", capacity=4, start_at=START,
             signup_open_at="not-a-time", signup_close_at=CLOSE),
    ]
    for spec in bad_specs:
        with pytest.raises(sg.SignupError) as exc:
            guard.create_event("ev-bad", **spec)
        assert exc.value.code == "SIGNUP_BAD_SPEC"
    assert guard.overview("ev-1")["status"] == "draft"


def test_open_signup_and_signup_window_boundaries():
    guard = sg.EventSignupGuard()
    make_event(guard, capacity=2)
    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-1", "p-a", now=T0)
    assert exc.value.code == "SIGNUP_BAD_STATE"

    assert guard.open_signup("ev-1", now=T0)["status"] == "open"
    with pytest.raises(sg.SignupError) as exc:
        guard.open_signup("ev-1", now=T0)
    assert exc.value.code == "SIGNUP_BAD_STATE"
    with pytest.raises(sg.SignupError) as exc:
        guard.open_signup("ev-ghost", now=T0)
    assert exc.value.code == "SIGNUP_UNKNOWN_EVENT"
    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-ghost", "p-a", now=T0)
    assert exc.value.code == "SIGNUP_UNKNOWN_EVENT"

    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-1", "p-early", now="2026-11-24T08:59:59+00:00")
    assert exc.value.code == "SIGNUP_WINDOW_CLOSED"
    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-1", "p-late", now=CLOSE)
    assert exc.value.code == "SIGNUP_WINDOW_CLOSED"

    row = guard.sign_up("ev-1", "p-last", now="2026-11-24T10:59:59+00:00")
    assert row["status"] == "confirmed"


def test_capacity_spills_into_fifo_waitlist():
    guard = sg.EventSignupGuard()
    open_event(guard, capacity=4)
    first = guard.sign_up("ev-1", "p-a", party_size=2, now=T0)
    second = guard.sign_up("ev-1", "p-b", party_size=2, now=T0)
    third = guard.sign_up("ev-1", "p-c", party_size=1, now=T0)
    fourth = guard.sign_up("ev-1", "p-d", party_size=1, now=T0)

    assert first == {
        "ticket_id": first["ticket_id"],
        "event_id": "ev-1",
        "player_id": "p-a",
        "party_size": 2,
        "status": "confirmed",
        "created_at": T0,
        "hold_until": ts(T0, 900),
        "checked_in_at": None,
        "request_id": None,
        "idempotent": False,
        "position": 0,
    }
    assert second["status"] == "confirmed"
    assert third["status"] == "waitlisted"
    assert third["position"] == 1
    assert third["hold_until"] is None
    assert fourth["status"] == "waitlisted"
    assert fourth["position"] == 2

    assert guard.overview("ev-1") == {
        "event_id": "ev-1",
        "name": "星火团本",
        "capacity": 4,
        "status": "open",
        "start_at": START,
        "signup_open_at": OPEN,
        "signup_close_at": CLOSE,
        "confirmed": 4,
        "waitlisted": 2,
    }
    assert guard.position("ev-1", "p-a") == 0
    assert guard.position("ev-1", "p-c") == 1
    assert guard.position("ev-1", "p-d") == 2
    assert guard.position("ev-1", "p-ghost") == -1

    assert [row["player_id"] for row in guard.tickets("ev-1")] == [
        "p-a", "p-b", "p-c", "p-d"]
    assert [row["player_id"] for row in guard.tickets(
        "ev-1", status="confirmed")] == ["p-a", "p-b"]
    assert [row["player_id"] for row in guard.tickets(
        "ev-1", status="waitlisted")] == ["p-c", "p-d"]


def test_party_and_waitlist_limits():
    guard = sg.EventSignupGuard(max_party=3, max_waitlist=1, hold_ttl=120)
    open_event(guard, capacity=1)
    for bad in (0, 4, "2"):
        with pytest.raises(sg.SignupError) as exc:
            guard.sign_up("ev-1", "p-bad", party_size=bad, now=T0)
        assert exc.value.code == "SIGNUP_BAD_PARTY"

    keeper = guard.sign_up("ev-1", "p-a", party_size=1, now=T0)
    waiting = guard.sign_up("ev-1", "p-b", party_size=1, now=T0)
    assert waiting["status"] == "waitlisted"
    assert waiting["position"] == 1
    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-1", "p-c", party_size=1, now=T0)
    assert exc.value.code == "SIGNUP_WAITLIST_FULL"
    assert guard.stats()["signups"] == 2

    guard.cancel_signup(keeper["ticket_id"], player_id="p-a", now=T0)
    assert guard.overview("ev-1")["confirmed"] == 1
    assert guard.overview("ev-1")["waitlisted"] == 0
    assert guard.tickets("ev-1")[1]["status"] == "confirmed"

    late = guard.sign_up("ev-1", "p-c", party_size=1, now=T0)
    assert late["status"] == "waitlisted"
    assert late["position"] == 1


def test_duplicate_and_idempotent_request_replay():
    guard = sg.EventSignupGuard()
    open_event(guard, capacity=3)
    first = guard.sign_up("ev-1", "p-a", request_id="r-1", now=T0)
    again = guard.sign_up("ev-1", "p-a", request_id="r-1",
                          now="2026-11-24T10:05:00+00:00")
    assert again["ticket_id"] == first["ticket_id"]
    assert again["idempotent"] is True
    assert again["created_at"] == T0
    assert again["hold_until"] == first["hold_until"] == ts(T0, 900)

    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-1", "p-a", request_id="r-2", now=T0)
    assert exc.value.code == "SIGNUP_DUPLICATE"

    other = guard.sign_up("ev-1", "p-b", request_id="r-1", now=T0)
    assert other["status"] == "confirmed"
    assert other["idempotent"] is False
    assert guard.stats() == {
        "signups": 2, "confirmed": 2, "waitlisted": 0, "promoted": 0,
        "checked_in": 0, "cancelled": 0, "expired": 0, "replayed": 1,
    }


def test_cancel_confirmed_releases_seats_and_promotes_head():
    guard = sg.EventSignupGuard()
    open_event(guard, capacity=2)
    a = guard.sign_up("ev-1", "p-a", now=T0)
    guard.sign_up("ev-1", "p-b", now=T0)
    c = guard.sign_up("ev-1", "p-c", now=T0)
    d = guard.sign_up("ev-1", "p-d", now=T0)
    assert c["position"] == 1
    assert d["position"] == 2

    out = guard.cancel_signup(a["ticket_id"], player_id="p-a",
                              now="2026-11-24T10:03:00+00:00")
    assert out == {
        "ticket_id": a["ticket_id"],
        "status": "cancelled",
        "freed_seats": 1,
        "promoted": [c["ticket_id"]],
    }
    promoted = [row for row in guard.tickets("ev-1")
                if row["ticket_id"] == c["ticket_id"]][0]
    assert promoted["status"] == "confirmed"
    assert promoted["hold_until"] == ts("2026-11-24T10:03:00+00:00", 900)
    assert guard.position("ev-1", "p-c") == 0
    assert guard.position("ev-1", "p-d") == 1
    assert guard.overview("ev-1")["confirmed"] == 2
    assert guard.overview("ev-1")["waitlisted"] == 1
    assert guard.stats()["promoted"] == 1
    assert guard.stats()["cancelled"] == 1


def test_cancel_waitlisted_compacts_queue_and_rejects_bad_calls():
    guard = sg.EventSignupGuard()
    open_event(guard, capacity=1)
    guard.sign_up("ev-1", "p-a", now=T0)
    b = guard.sign_up("ev-1", "p-b", now=T0)
    c = guard.sign_up("ev-1", "p-c", now=T0)
    guard.sign_up("ev-1", "p-d", now=T0)

    out = guard.cancel_signup(b["ticket_id"], player_id="p-b", now=T0)
    assert out == {
        "ticket_id": b["ticket_id"],
        "status": "cancelled",
        "freed_seats": 0,
        "promoted": [],
    }
    assert guard.position("ev-1", "p-c") == 1
    assert guard.position("ev-1", "p-d") == 2

    with pytest.raises(sg.SignupError) as exc:
        guard.cancel_signup(b["ticket_id"], player_id="p-b", now=T0)
    assert exc.value.code == "SIGNUP_TICKET_CLOSED"
    with pytest.raises(sg.SignupError) as exc:
        guard.cancel_signup(c["ticket_id"], player_id="p-x", now=T0)
    assert exc.value.code == "SIGNUP_NOT_OWNER"
    with pytest.raises(sg.SignupError) as exc:
        guard.cancel_signup("tk-ghost", player_id="p-a", now=T0)
    assert exc.value.code == "SIGNUP_UNKNOWN_TICKET"


def test_head_of_line_waitlist_blocks_smaller_parties():
    guard = sg.EventSignupGuard(max_party=5)
    open_event(guard, capacity=4)
    a = guard.sign_up("ev-1", "p-a", party_size=2, now=T0)
    b = guard.sign_up("ev-1", "p-b", party_size=2, now=T0)
    c = guard.sign_up("ev-1", "p-c", party_size=3, now=T0)
    guard.sign_up("ev-1", "p-d", party_size=1, now=T0)

    out = guard.cancel_signup(a["ticket_id"], player_id="p-a", now=T0)
    assert out["freed_seats"] == 2
    assert out["promoted"] == []
    assert guard.position("ev-1", "p-c") == 1
    assert guard.position("ev-1", "p-d") == 2

    out2 = guard.cancel_signup(b["ticket_id"], player_id="p-b", now=T0)
    assert out2["freed_seats"] == 2
    assert out2["promoted"] == [c["ticket_id"],
                                guard.tickets("ev-1")[3]["ticket_id"]]
    assert guard.overview("ev-1")["confirmed"] == 4
    assert guard.overview("ev-1")["waitlisted"] == 0


def test_check_in_guards_success_and_late_expiry():
    guard = sg.EventSignupGuard(hold_ttl=30)
    open_event(guard, capacity=1)
    e1 = guard.sign_up("ev-1", "p-a", now=T0)
    waiting = guard.sign_up("ev-1", "p-w", now=T0)

    with pytest.raises(sg.SignupError) as exc:
        guard.check_in(waiting["ticket_id"], player_id="p-w", now=T0)
    assert exc.value.code == "SIGNUP_NOT_CONFIRMED"
    with pytest.raises(sg.SignupError) as exc:
        guard.check_in("tk-ghost", player_id="p-a", now=T0)
    assert exc.value.code == "SIGNUP_UNKNOWN_TICKET"
    with pytest.raises(sg.SignupError) as exc:
        guard.check_in(e1["ticket_id"], player_id="p-other", now=T0)
    assert exc.value.code == "SIGNUP_NOT_OWNER"

    ok = guard.check_in(e1["ticket_id"], player_id="p-a", now=ts(T0, 10))
    assert ok == {
        "ticket_id": e1["ticket_id"],
        "event_id": "ev-1",
        "player_id": "p-a",
        "party_size": 1,
        "status": "checked_in",
        "created_at": T0,
        "hold_until": None,
        "checked_in_at": ts(T0, 10),
        "request_id": None,
        "idempotent": False,
    }
    with pytest.raises(sg.SignupError) as exc:
        guard.check_in(e1["ticket_id"], player_id="p-a", now=ts(T0, 11))
    assert exc.value.code == "SIGNUP_ALREADY_CHECKED_IN"
    with pytest.raises(sg.SignupError) as exc:
        guard.cancel_signup(e1["ticket_id"], player_id="p-a", now=ts(T0, 11))
    assert exc.value.code == "SIGNUP_ALREADY_CHECKED_IN"

    make_event(guard, event_id="ev-2", capacity=1)
    guard.open_signup("ev-2", now=T0)
    late = guard.sign_up("ev-2", "p-late", now=T0)
    with pytest.raises(sg.SignupError) as exc:
        guard.check_in(late["ticket_id"], player_id="p-late", now=ts(T0, 30))
    assert exc.value.code == "SIGNUP_HOLD_EXPIRED"
    assert guard.tickets("ev-2")[0]["status"] == "expired"
    assert guard.overview("ev-2")["confirmed"] == 0
    with pytest.raises(sg.SignupError) as exc:
        guard.check_in(late["ticket_id"], player_id="p-late", now=ts(T0, 31))
    assert exc.value.code == "SIGNUP_TICKET_CLOSED"


def test_expire_sweeps_hold_deadlines_and_promotes():
    guard = sg.EventSignupGuard(hold_ttl=60)
    open_event(guard, capacity=2)
    guard.sign_up("ev-1", "p-a", now=T0)
    guard.sign_up("ev-1", "p-b", now=T0)
    c = guard.sign_up("ev-1", "p-c", now=T0)
    make_event(guard, event_id="ev-2", capacity=1)
    guard.open_signup("ev-2", now=T0)
    guard.sign_up("ev-2", "p-e", now=ts(T0, 30))

    assert guard.expire(now=ts(T0, 59)) == 0
    assert guard.expire(now=ts(T0, 60)) == 2
    assert [row["status"] for row in guard.tickets("ev-1")] == [
        "expired", "expired", "confirmed"]
    assert guard.overview("ev-1")["confirmed"] == 1
    assert guard.position("ev-1", "p-c") == 0
    assert guard.tickets("ev-1")[2]["hold_until"] == ts(T0, 120)
    assert guard.expire(now=ts(T0, 60)) == 0
    assert guard.expire(now=ts(T0, 90)) == 1
    assert guard.tickets("ev-2")[0]["status"] == "expired"
    assert guard.expire(now=ts(T0, 120)) == 1
    assert guard.overview("ev-1")["confirmed"] == 0
    assert guard.stats()["expired"] == 4
    with pytest.raises(sg.SignupError) as exc:
        guard.check_in(c["ticket_id"], player_id="p-c", now=ts(T0, 121))
    assert exc.value.code == "SIGNUP_TICKET_CLOSED"


def test_set_capacity_grows_promotions_and_rejects_shrink():
    guard = sg.EventSignupGuard()
    open_event(guard, capacity=4)
    guard.sign_up("ev-1", "p-a", party_size=2, now=T0)
    guard.sign_up("ev-1", "p-b", party_size=2, now=T0)
    c = guard.sign_up("ev-1", "p-c", party_size=1, now=T0)
    guard.sign_up("ev-1", "p-d", party_size=1, now=T0)

    out = guard.set_capacity("ev-1", 5, now=T0)
    assert out["capacity"] == 5
    assert out["confirmed"] == 5
    assert out["waitlisted"] == 1
    assert out["promoted"] == [c["ticket_id"]]

    with pytest.raises(sg.SignupError) as exc:
        guard.set_capacity("ev-1", 4, now=T0)
    assert exc.value.code == "SIGNUP_CAPACITY_BELOW_CONFIRMED"
    assert guard.set_capacity("ev-1", 5, now=T0)["promoted"] == []
    for bad in (0, "5"):
        with pytest.raises(sg.SignupError) as exc:
            guard.set_capacity("ev-1", bad, now=T0)
        assert exc.value.code == "SIGNUP_BAD_SPEC"
    with pytest.raises(sg.SignupError) as exc:
        guard.set_capacity("ev-ghost", 3, now=T0)
    assert exc.value.code == "SIGNUP_UNKNOWN_EVENT"

    guard.lock_event("ev-1", now=T0)
    with pytest.raises(sg.SignupError) as exc:
        guard.set_capacity("ev-1", 6, now=T0)
    assert exc.value.code == "SIGNUP_BAD_STATE"


def test_lock_and_cancel_event_close_all_tickets():
    guard = sg.EventSignupGuard()
    open_event(guard, capacity=2)
    a = guard.sign_up("ev-1", "p-a", now=T0)
    guard.sign_up("ev-1", "p-b", now=T0)
    with pytest.raises(sg.SignupError) as exc:
        guard.lock_event("ev-ghost", now=T0)
    assert exc.value.code == "SIGNUP_UNKNOWN_EVENT"

    locked = guard.lock_event("ev-1", now=T0)
    assert locked["status"] == "locked"
    with pytest.raises(sg.SignupError) as exc:
        guard.lock_event("ev-1", now=T0)
    assert exc.value.code == "SIGNUP_BAD_STATE"
    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-1", "p-c", now=T0)
    assert exc.value.code == "SIGNUP_BAD_STATE"

    checked = guard.check_in(a["ticket_id"], player_id="p-a", now=T0)
    assert checked["status"] == "checked_in"
    closed = guard.cancel_event("ev-1", now=T0)
    assert closed == {
        "event_id": "ev-1",
        "status": "cancelled",
        "cancelled_tickets": 2,
    }
    assert [row["status"] for row in guard.tickets("ev-1")] == [
        "cancelled", "cancelled"]
    assert guard.overview("ev-1")["confirmed"] == 0
    with pytest.raises(sg.SignupError) as exc:
        guard.cancel_event("ev-1", now=T0)
    assert exc.value.code == "SIGNUP_EVENT_CANCELLED"
    with pytest.raises(sg.SignupError) as exc:
        guard.open_signup("ev-1", now=T0)
    assert exc.value.code == "SIGNUP_EVENT_CANCELLED"
    with pytest.raises(sg.SignupError) as exc:
        guard.sign_up("ev-1", "p-c", now=T0)
    assert exc.value.code == "SIGNUP_EVENT_CANCELLED"


def test_snapshot_restore_roundtrip_and_invalid_states():
    guard = sg.EventSignupGuard(hold_ttl=300)
    open_event(guard, capacity=2)
    a = guard.sign_up("ev-1", "p-a", request_id="r-a", now=T0)
    b = guard.sign_up("ev-1", "p-b", now=T0)
    c = guard.sign_up("ev-1", "p-c", now=T0)
    state = guard.snapshot()
    assert state["version"] == 1

    guard.cancel_signup(a["ticket_id"], player_id="p-a", now=T0)
    assert guard.position("ev-1", "p-c") == 0
    guard.sign_up("ev-1", "p-d", now=T0)
    guard.restore(state)
    assert guard.position("ev-1", "p-a") == 0
    assert guard.position("ev-1", "p-c") == 1
    assert guard.position("ev-1", "p-d") == -1
    assert guard.overview("ev-1")["confirmed"] == 2

    replay = guard.sign_up("ev-1", "p-a", request_id="r-a", now=ts(T0, 100))
    assert replay["ticket_id"] == a["ticket_id"]
    assert replay["idempotent"] is True
    assert replay["hold_until"] == ts(T0, 300)
    fresh = guard.sign_up("ev-1", "p-e", now=T0)
    assert fresh["ticket_id"] not in {
        a["ticket_id"], b["ticket_id"], c["ticket_id"]}

    bad_states = [
        None,
        {},
        {"version": 2},
        {"version": 1, "events": [], "tickets": {}, "queues": {},
         "counters": {}, "next_id": 4},
        {"version": 1, "events": {}, "tickets": [], "queues": {},
         "counters": {}, "next_id": 4},
        {"version": 1, "events": {}, "tickets": {}, "queues": {},
         "counters": {}, "next_id": "4"},
    ]
    for bad in bad_states:
        with pytest.raises(sg.SignupError) as exc:
            guard.restore(bad)
        assert exc.value.code == "SIGNUP_SNAPSHOT_INVALID"
    assert guard.position("ev-1", "p-a") == 0
    assert guard.sign_up(
        "ev-1", "p-a", request_id="r-a", now=T0)["idempotent"] is True


def test_concurrent_signups_respect_capacity_and_request_idempotency():
    guard = sg.EventSignupGuard(max_waitlist=50)
    open_event(guard, capacity=2)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = guard.sign_up("ev-1", f"p-{index}", now=T0)
        except Exception as exc:  # pragma: no cover - surfaced below
            out = {"status": "error", "detail": repr(exc)}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(row["status"] for row in results) == ["confirmed"] * 2 + [
        "waitlisted"] * 6
    assert guard.stats()["signups"] == 8
    assert sorted(row["position"] for row in results
                  if row["status"] == "waitlisted") == [1, 2, 3, 4, 5, 6]

    make_event(guard, event_id="ev-2", capacity=1)
    guard.open_signup("ev-2", now=T0)
    guard.sign_up("ev-2", "p-anchor", now=T0)
    barrier2 = threading.Barrier(8)
    racing = []

    def worker2():
        barrier2.wait()
        try:
            out = guard.sign_up("ev-2", "p-race", request_id="r-race",
                                now=T0)
        except Exception as exc:  # pragma: no cover - surfaced below
            out = {"status": "error", "detail": repr(exc)}
        with lock:
            racing.append(out)

    threads = [threading.Thread(target=worker2) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len({row["ticket_id"] for row in racing}) == 1
    assert sum(1 for row in racing if row.get("idempotent") is True) == 7
    assert guard.stats()["replayed"] == 7
    assert len(guard.tickets("ev-2")) == 2


def test_stats_reset_and_read_only_queries():
    guard = sg.EventSignupGuard(hold_ttl=60)
    open_event(guard, capacity=2)
    guard.sign_up("ev-1", "p-a", request_id="r-a", now=T0)
    b = guard.sign_up("ev-1", "p-b", now=T0)
    c = guard.sign_up("ev-1", "p-c", now=T0)
    guard.sign_up("ev-1", "p-d", now=T0)
    guard.sign_up("ev-1", "p-a", request_id="r-a", now=ts(T0, 1))
    guard.check_in(b["ticket_id"], player_id="p-b", now=ts(T0, 1))
    guard.cancel_signup(c["ticket_id"], player_id="p-c", now=ts(T0, 1))
    guard.set_capacity("ev-1", 3, now=ts(T0, 2))

    assert [row["player_id"] for row in guard.tickets(
        "ev-1", status="confirmed")] == ["p-a", "p-d"]
    assert [row["player_id"] for row in guard.tickets(
        "ev-1", status="checked_in")] == ["p-b"]
    assert guard.overview("ev-1")["confirmed"] == 3
    assert guard.position("ev-1", "p-c") == -1
    assert guard.position("ev-1", "p-ghost") == -1
    assert guard.stats() == {
        "signups": 4, "confirmed": 2, "waitlisted": 2, "promoted": 1,
        "checked_in": 1, "cancelled": 1, "expired": 0, "replayed": 1,
    }
    with pytest.raises(sg.SignupError) as exc:
        guard.tickets("ev-1", status="ghost")
    assert exc.value.code == "SIGNUP_BAD_STATUS"
    for call in (
        lambda: guard.tickets("ev-ghost"),
        lambda: guard.overview("ev-ghost"),
        lambda: guard.position("ev-ghost", "p-a"),
    ):
        with pytest.raises(sg.SignupError) as exc:
            call()
        assert exc.value.code == "SIGNUP_UNKNOWN_EVENT"

    guard.reset_stats()
    assert guard.stats() == {
        "signups": 0, "confirmed": 0, "waitlisted": 0, "promoted": 0,
        "checked_in": 0, "cancelled": 0, "expired": 0, "replayed": 0,
    }
    assert guard.overview("ev-1")["confirmed"] == 3
