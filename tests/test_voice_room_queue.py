import threading
from datetime import datetime, timedelta

import pytest

import voice_room_queue as vq

T0 = "2026-11-24T20:00:00+00:00"


def ts(base, seconds):
    return (datetime.fromisoformat(base) + timedelta(seconds=seconds)).isoformat()


def make_room(guard, room_id="r-1", *, seats=2, owner="host", name="周末歌会"):
    return guard.create_room(room_id, name=name, owner=owner, seats=seats)


def test_create_room_validation_and_summary():
    guard = vq.VoiceRoomQueue(max_seats=8)
    row = guard.create_room("r-1", name="周末歌会", owner="host", seats=2)
    assert row == {
        "room_id": "r-1",
        "name": "周末歌会",
        "owner": "host",
        "seats": 2,
        "status": "open",
        "occupied": 0,
        "queued": 0,
    }
    assert guard.describe_room("r-1") == row
    with pytest.raises(vq.VoiceError) as exc:
        guard.create_room("r-1", name="重复房", owner="host", seats=2)
    assert exc.value.code == "VOICE_ROOM_EXISTS"

    bad_specs = [
        dict(name="", owner="host", seats=1),
        dict(name="x", owner="host", seats=0),
        dict(name="x", owner="host", seats=9),
        dict(name="x", owner="host", seats="2"),
    ]
    for spec in bad_specs:
        with pytest.raises(vq.VoiceError) as exc:
            guard.create_room("r-bad", **spec)
        assert exc.value.code == "VOICE_BAD_SPEC"
    with pytest.raises(vq.VoiceError) as exc:
        guard.close_room("r-ghost", by_user="host", now=T0)
    assert exc.value.code == "VOICE_UNKNOWN_ROOM"


def test_join_seats_when_free_then_queues():
    guard = vq.VoiceRoomQueue()
    make_room(guard, seats=2)
    first = guard.join_queue("r-1", "u-a", now=T0)
    assert first == {
        "room_id": "r-1",
        "user_id": "u-a",
        "status": "seated",
        "seat": 0,
        "position": 0,
        "priority": 0,
        "expires_at": ts(T0, 60),
        "idempotent": False,
    }
    second = guard.join_queue("r-1", "u-b", now=T0)
    assert second["status"] == "seated"
    assert second["seat"] == 1
    third = guard.join_queue("r-1", "u-c", now=T0)
    assert third["status"] == "queued"
    assert third["seat"] is None
    assert third["position"] == 1
    assert third["expires_at"] is None

    assert guard.describe_room("r-1") == {
        "room_id": "r-1",
        "name": "周末歌会",
        "owner": "host",
        "seats": 2,
        "status": "open",
        "occupied": 2,
        "queued": 1,
    }
    assert guard.seats("r-1") == [
        {"seat": 0, "user_id": "u-a", "muted": False,
         "expires_at": ts(T0, 60)},
        {"seat": 1, "user_id": "u-b", "muted": False,
         "expires_at": ts(T0, 60)},
    ]
    assert guard.queue("r-1") == [
        {"user_id": "u-c", "priority": 0, "position": 1}]


def test_queue_orders_by_priority_then_arrival():
    guard = vq.VoiceRoomQueue()
    make_room(guard, seats=1)
    guard.join_queue("r-1", "u-seat", priority=9, now=T0)
    guard.join_queue("r-1", "u-low1", priority=0, now=T0)
    guard.join_queue("r-1", "u-low2", priority=0, now=T0)
    high1 = guard.join_queue("r-1", "u-high1", priority=5, now=T0)
    high2 = guard.join_queue("r-1", "u-high2", priority=5, now=T0)
    assert high1["position"] == 1
    assert high2["position"] == 2
    assert [row["user_id"] for row in guard.queue("r-1")] == [
        "u-high1", "u-high2", "u-low1", "u-low2"]
    assert [row["position"] for row in guard.queue("r-1")] == [1, 2, 3, 4]
    assert [row["priority"] for row in guard.queue("r-1")] == [5, 5, 0, 0]


def test_queue_limits_duplicate_and_idempotent_replay():
    guard = vq.VoiceRoomQueue(max_queue=2)
    make_room(guard, seats=1)
    for bad in (10, -1, "1"):
        with pytest.raises(vq.VoiceError) as exc:
            guard.join_queue("r-1", f"u-bad-{bad}", priority=bad, now=T0)
        assert exc.value.code == "VOICE_BAD_PRIORITY"
    with pytest.raises(vq.VoiceError) as exc:
        guard.join_queue("r-ghost", "u-a", now=T0)
    assert exc.value.code == "VOICE_UNKNOWN_ROOM"

    guard.join_queue("r-1", "u-seat", now=T0)
    first = guard.join_queue("r-1", "u-a", request_id="rq-a", now=T0)
    guard.join_queue("r-1", "u-b", request_id="rq-b", now=T0)
    with pytest.raises(vq.VoiceError) as exc:
        guard.join_queue("r-1", "u-c", now=T0)
    assert exc.value.code == "VOICE_QUEUE_FULL"
    with pytest.raises(vq.VoiceError) as exc:
        guard.join_queue("r-1", "u-seat", now=T0)
    assert exc.value.code == "VOICE_ALREADY_SEATED"
    with pytest.raises(vq.VoiceError) as exc:
        guard.join_queue("r-1", "u-a", request_id="rq-other", now=T0)
    assert exc.value.code == "VOICE_ALREADY_QUEUED"

    again = guard.join_queue("r-1", "u-a", request_id="rq-a", now=ts(T0, 5))
    assert again["idempotent"] is True
    assert again["position"] == first["position"] == 1
    assert again["expires_at"] is None
    assert guard.stats()["replayed"] == 1


def test_set_seats_expands_and_promotes_queue():
    guard = vq.VoiceRoomQueue()
    make_room(guard, seats=1)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", priority=0, now=T0)
    guard.join_queue("r-1", "u-c", priority=5, now=T0)
    guard.join_queue("r-1", "u-d", priority=1, now=T0)
    assert [row["user_id"] for row in guard.queue("r-1")] == [
        "u-c", "u-d", "u-b"]

    out = guard.set_seats("r-1", 3, by_user="host", now=ts(T0, 10))
    assert out["seats"] == 3
    assert out["occupied"] == 3
    assert out["queued"] == 1
    assert out["promoted"] == ["u-c", "u-d"]
    seats = guard.seats("r-1")
    assert [row["user_id"] for row in seats] == ["u-a", "u-c", "u-d"]
    assert seats[1]["expires_at"] == ts(T0, 70)
    assert guard.queue("r-1") == [
        {"user_id": "u-b", "priority": 0, "position": 1}]

    with pytest.raises(vq.VoiceError) as exc:
        guard.set_seats("r-1", 2, by_user="host", now=ts(T0, 11))
    assert exc.value.code == "VOICE_SEATS_BELOW_OCCUPIED"
    assert guard.set_seats("r-1", 3, by_user="host",
                           now=ts(T0, 12))["promoted"] == []
    for bad in (0, 17, "3"):
        with pytest.raises(vq.VoiceError) as exc:
            guard.set_seats("r-1", bad, by_user="host", now=ts(T0, 12))
        assert exc.value.code == "VOICE_BAD_SPEC"
    with pytest.raises(vq.VoiceError) as exc:
        guard.set_seats("r-1", 4, by_user="u-a", now=ts(T0, 12))
    assert exc.value.code == "VOICE_NOT_OWNER"
    assert guard.stats()["promoted"] == 2


def test_leave_seat_promotes_one_and_leave_queue_compacts():
    guard = vq.VoiceRoomQueue()
    make_room(guard, seats=2)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)
    guard.join_queue("r-1", "u-c", now=T0)
    guard.join_queue("r-1", "u-d", now=T0)

    out = guard.leave_seat("r-1", "u-a", now=ts(T0, 5))
    assert out == {
        "room_id": "r-1",
        "user_id": "u-a",
        "seat": 0,
        "promoted": "u-c",
    }
    assert guard.seats("r-1")[0]["user_id"] == "u-c"
    assert guard.seats("r-1")[0]["expires_at"] == ts(T0, 65)
    assert [row["user_id"] for row in guard.queue("r-1")] == ["u-d"]

    with pytest.raises(vq.VoiceError) as exc:
        guard.leave_seat("r-1", "u-a", now=ts(T0, 6))
    assert exc.value.code == "VOICE_NOT_SEATED"
    with pytest.raises(vq.VoiceError) as exc:
        guard.leave_queue("r-1", "u-b", now=ts(T0, 6))
    assert exc.value.code == "VOICE_ALREADY_SEATED"
    left = guard.leave_queue("r-1", "u-d", now=ts(T0, 6))
    assert left == {"room_id": "r-1", "user_id": "u-d", "position": 1}
    with pytest.raises(vq.VoiceError) as exc:
        guard.leave_queue("r-1", "u-d", now=ts(T0, 7))
    assert exc.value.code == "VOICE_NOT_QUEUED"


def test_preempt_by_owner_requeues_displaced_at_head():
    guard = vq.VoiceRoomQueue()
    make_room(guard, seats=2)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)
    guard.join_queue("r-1", "u-c", priority=1, now=T0)

    out = guard.preempt("r-1", "u-a", by_user="host", now=ts(T0, 5))
    assert out == {
        "room_id": "r-1",
        "user_id": "host",
        "seat": 0,
        "muted": True,
        "expires_at": ts(T0, 65),
        "preempted": "u-a",
        "requeued": True,
        "position": 1,
    }
    assert [row["user_id"] for row in guard.queue("r-1")] == ["u-a", "u-c"]
    assert guard.seats("r-1")[0]["user_id"] == "host"
    assert guard.seats("r-1")[0]["muted"] is True
    assert guard.seats("r-1")[1]["user_id"] == "u-b"

    with pytest.raises(vq.VoiceError) as exc:
        guard.preempt("r-1", "u-c", by_user="u-b", now=T0)
    assert exc.value.code == "VOICE_NOT_MODERATOR"
    with pytest.raises(vq.VoiceError) as exc:
        guard.preempt("r-1", "host", by_user="host", now=T0)
    assert exc.value.code == "VOICE_CANNOT_PREEMPT_OWNER"
    with pytest.raises(vq.VoiceError) as exc:
        guard.preempt("r-1", "u-c", by_user="host", now=T0)
    assert exc.value.code == "VOICE_NOT_SEATED"
    with pytest.raises(vq.VoiceError) as exc:
        guard.preempt("r-1", "u-b", by_user="host", now=T0)
    assert exc.value.code == "VOICE_ALREADY_SEATED"


def test_preempt_evicts_displaced_when_queue_is_full():
    guard = vq.VoiceRoomQueue(max_queue=1)
    make_room(guard, seats=1)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", priority=2, now=T0)

    out = guard.preempt("r-1", "u-a", by_user="host", now=T0)
    assert out["user_id"] == "host"
    assert out["preempted"] == "u-a"
    assert out["requeued"] is False
    assert out["position"] is None
    assert [row["user_id"] for row in guard.queue("r-1")] == ["u-b"]
    assert guard.describe_room("r-1")["queued"] == 1


def test_renew_extends_hold_and_late_renew_releases_seat():
    guard = vq.VoiceRoomQueue(seat_ttl=60)
    make_room(guard, seats=1)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)

    out = guard.renew("r-1", "u-a", now=ts(T0, 30))
    assert out == {
        "room_id": "r-1",
        "user_id": "u-a",
        "seat": 0,
        "expires_at": ts(T0, 90),
    }
    with pytest.raises(vq.VoiceError) as exc:
        guard.renew("r-1", "u-a", now=ts(T0, 90))
    assert exc.value.code == "VOICE_SEAT_EXPIRED"
    assert guard.seats("r-1")[0]["user_id"] == "u-b"
    assert guard.seats("r-1")[0]["expires_at"] == ts(T0, 150)
    with pytest.raises(vq.VoiceError) as exc:
        guard.renew("r-1", "u-a", now=ts(T0, 91))
    assert exc.value.code == "VOICE_NOT_SEATED"
    with pytest.raises(vq.VoiceError) as exc:
        guard.renew("r-1", "u-ghost", now=T0)
    assert exc.value.code == "VOICE_NOT_SEATED"


def test_mute_permissions_and_state():
    guard = vq.VoiceRoomQueue()
    make_room(guard, seats=2, owner="host")
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)
    guard.join_queue("r-1", "u-q", now=T0)

    assert guard.mute("r-1", "u-a", by_user="u-a") == {
        "room_id": "r-1", "user_id": "u-a", "muted": True}
    assert guard.mute("r-1", "u-a", by_user="host",
                      muted=False)["muted"] is False
    with pytest.raises(vq.VoiceError) as exc:
        guard.mute("r-1", "u-a", by_user="u-b", muted=True)
    assert exc.value.code == "VOICE_NOT_MODERATOR"
    with pytest.raises(vq.VoiceError) as exc:
        guard.mute("r-1", "u-q", by_user="host")
    assert exc.value.code == "VOICE_NOT_SEATED"


def test_expire_sweeps_rooms_and_promotes_in_seat_order():
    guard = vq.VoiceRoomQueue(seat_ttl=60)
    make_room(guard, room_id="r-1", seats=2)
    make_room(guard, room_id="r-2", seats=1)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)
    guard.join_queue("r-1", "u-c", now=T0)
    guard.join_queue("r-2", "u-d", now=ts(T0, 30))

    assert guard.expire(now=ts(T0, 59)) == 0
    assert guard.expire(now=ts(T0, 60)) == 2
    seats = guard.seats("r-1")
    assert seats[0]["user_id"] == "u-c"
    assert seats[0]["expires_at"] == ts(T0, 120)
    assert seats[1]["user_id"] is None
    assert guard.expire(now=ts(T0, 60)) == 0
    assert guard.expire(now=ts(T0, 90)) == 1
    assert guard.seats("r-2")[0]["user_id"] is None
    assert guard.expire(now=ts(T0, 120)) == 1
    assert guard.stats()["expired"] == 4
    assert guard.describe_room("r-1")["occupied"] == 0


def test_close_room_permissions_and_cleanup():
    guard = vq.VoiceRoomQueue()
    make_room(guard, seats=2, owner="host")
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)
    guard.join_queue("r-1", "u-c", now=T0)
    guard.join_queue("r-1", "u-d", now=T0)

    with pytest.raises(vq.VoiceError) as exc:
        guard.close_room("r-1", by_user="u-a", now=T0)
    assert exc.value.code == "VOICE_NOT_OWNER"
    out = guard.close_room("r-1", by_user="host", now=T0)
    assert out == {
        "room_id": "r-1",
        "status": "closed",
        "displaced": 2,
        "dropped_queue": 2,
    }
    assert guard.seats("r-1") == [
        {"seat": 0, "user_id": None, "muted": None, "expires_at": None},
        {"seat": 1, "user_id": None, "muted": None, "expires_at": None},
    ]
    assert guard.queue("r-1") == []
    assert guard.describe_room("r-1")["status"] == "closed"
    with pytest.raises(vq.VoiceError) as exc:
        guard.close_room("r-1", by_user="host", now=T0)
    assert exc.value.code == "VOICE_ROOM_CLOSED"
    with pytest.raises(vq.VoiceError) as exc:
        guard.join_queue("r-1", "u-e", now=T0)
    assert exc.value.code == "VOICE_ROOM_CLOSED"
    with pytest.raises(vq.VoiceError) as exc:
        guard.set_seats("r-1", 3, by_user="host", now=T0)
    assert exc.value.code == "VOICE_ROOM_CLOSED"


def test_snapshot_restore_roundtrip_and_invalid_states():
    guard = vq.VoiceRoomQueue(seat_ttl=60)
    make_room(guard, seats=2)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)
    guard.join_queue("r-1", "u-c", request_id="rq-c", priority=0, now=T0)
    guard.join_queue("r-1", "u-d", priority=7, now=T0)
    state = guard.snapshot()
    assert state["version"] == 1

    guard.preempt("r-1", "u-a", by_user="host", now=ts(T0, 5))
    guard.leave_queue("r-1", "u-d", now=ts(T0, 6))
    guard.mute("r-1", "u-b", by_user="u-b", now=ts(T0, 7))
    guard.restore(state)

    assert guard.seats("r-1") == [
        {"seat": 0, "user_id": "u-a", "muted": False,
         "expires_at": ts(T0, 60)},
        {"seat": 1, "user_id": "u-b", "muted": False,
         "expires_at": ts(T0, 60)},
    ]
    assert [row["user_id"] for row in guard.queue("r-1")] == ["u-d", "u-c"]
    replay = guard.join_queue("r-1", "u-c", request_id="rq-c",
                              now=ts(T0, 20))
    assert replay["idempotent"] is True
    assert replay["position"] == 2
    assert guard.stats() == {
        "joined": 4, "seated": 2, "queued": 2, "promoted": 0,
        "preempted": 0, "expired": 0, "left": 0, "replayed": 1,
    }

    bad_states = [
        None,
        {},
        {"version": 2},
        {"version": 1, "rooms": [], "seats": {}, "queues": {},
         "counters": {}, "next_seq": 1},
        {"version": 1, "rooms": {}, "seats": [], "queues": {},
         "counters": {}, "next_seq": 1},
        {"version": 1, "rooms": {}, "seats": {}, "queues": {},
         "counters": {}, "next_seq": "1"},
    ]
    for bad in bad_states:
        with pytest.raises(vq.VoiceError) as exc:
            guard.restore(bad)
        assert exc.value.code == "VOICE_SNAPSHOT_INVALID"
    assert guard.seats("r-1")[0]["user_id"] == "u-a"
    assert guard.seats("r-1")[1]["user_id"] == "u-b"


def test_concurrent_joins_respect_single_seat_and_idempotency():
    guard = vq.VoiceRoomQueue(max_queue=32)
    make_room(guard, seats=1)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = guard.join_queue("r-1", f"u-{index}", now=T0)
        except Exception as exc:  # pragma: no cover - surfaced below
            out = {"status": "error", "detail": repr(exc)}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(row["status"] for row in results) == ["queued"] * 7 + [
        "seated"]
    assert guard.seats("r-1")[0]["user_id"] is not None
    assert sorted(row["position"] for row in results
                  if row["status"] == "queued") == [1, 2, 3, 4, 5, 6, 7]
    assert guard.stats()["joined"] == 8

    make_room(guard, room_id="r-2", seats=1)
    guard.join_queue("r-2", "u-anchor", now=T0)
    barrier2 = threading.Barrier(8)
    racing = []

    def worker2():
        barrier2.wait()
        try:
            out = guard.join_queue("r-2", "u-race", request_id="rq-race",
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

    assert len({row["position"] for row in racing}) == 1
    assert sum(1 for row in racing if row.get("idempotent") is True) == 7
    assert len(guard.queue("r-2")) == 1


def test_stats_reset_and_read_only_queries():
    guard = vq.VoiceRoomQueue(seat_ttl=60)
    make_room(guard, seats=2)
    guard.join_queue("r-1", "u-a", now=T0)
    guard.join_queue("r-1", "u-b", now=T0)
    guard.join_queue("r-1", "u-c", request_id="rq-c", now=T0)
    guard.join_queue("r-1", "u-d", now=T0)
    guard.join_queue("r-1", "u-c", request_id="rq-c", now=ts(T0, 1))
    guard.preempt("r-1", "u-a", by_user="host", now=ts(T0, 2))
    guard.leave_seat("r-1", "host", now=ts(T0, 3))
    guard.leave_queue("r-1", "u-d", now=ts(T0, 3))
    guard.renew("r-1", "u-a", now=ts(T0, 4))
    guard.expire(now=ts(T0, 130))

    assert guard.describe_room("r-1") == {
        "room_id": "r-1",
        "name": "周末歌会",
        "owner": "host",
        "seats": 2,
        "status": "open",
        "occupied": 1,
        "queued": 0,
    }
    assert guard.seats("r-1")[0]["user_id"] == "u-c"
    assert guard.seats("r-1")[0]["expires_at"] == ts(T0, 190)
    assert guard.seats("r-1")[1]["user_id"] is None
    assert guard.queue("r-1") == []
    assert guard.stats() == {
        "joined": 4, "seated": 2, "queued": 2, "promoted": 2,
        "preempted": 1, "expired": 2, "left": 2, "replayed": 1,
    }
    for call in (
        lambda: guard.seats("r-ghost"),
        lambda: guard.queue("r-ghost"),
        lambda: guard.describe_room("r-ghost"),
    ):
        with pytest.raises(vq.VoiceError) as exc:
            call()
        assert exc.value.code == "VOICE_UNKNOWN_ROOM"

    guard.reset_stats()
    assert guard.stats() == {
        "joined": 0, "seated": 0, "queued": 0, "promoted": 0,
        "preempted": 0, "expired": 0, "left": 0, "replayed": 0,
    }
    assert guard.describe_room("r-1")["occupied"] == 1
