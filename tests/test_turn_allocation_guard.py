import copy
import threading

import pytest

import turn_allocation_guard as tg

NOW = "2026-11-23T10:00:00+00:00"
REALM = "turn.example"
FT1 = ("udp", "10.1.0.1:50000", "10.1.0.2:3478")
FT2 = ("udp", "10.1.0.1:50001", "10.1.0.2:3478")
FT3 = ("tcp", "10.1.0.3:50002", "10.1.0.2:3478")
PEER1 = "203.0.113.10"
PEER2 = "203.0.113.11"
PEER3 = "203.0.113.12"


def make(**kwargs):
    guard = tg.TurnAllocationGuard(**kwargs)
    guard.add_user("alice", realm=REALM)
    return guard


def allocate(guard, *, ft=FT1, lifetime=None, now=NOW, username="alice",
             realm=REALM):
    return guard.allocate(username=username, realm=realm, five_tuple=ft,
                          requested_lifetime=lifetime, now=now)


def test_user_registration_is_idempotent():
    guard = tg.TurnAllocationGuard()
    assert guard.add_user("alice", realm=REALM)["created"] is True
    again = guard.add_user("alice", realm=REALM)
    assert again["created"] is False
    with pytest.raises(tg.TurnError) as exc:
        guard.add_user("alice", realm="other.example")
    assert exc.value.code == "TURN_USER_CONFLICT"
    with pytest.raises(tg.TurnError) as exc2:
        guard.add_user("", realm=REALM)
    assert exc2.value.code == "TURN_BAD_USER"
    with pytest.raises(tg.TurnError) as exc3:
        guard.add_user("bob", realm="")
    assert exc3.value.code == "TURN_BAD_REALM"


def test_allocate_returns_allocation_and_describe_state():
    guard = make()
    created = allocate(guard)
    assert created["allocation_id"] == "alloc-1"
    assert created["username"] == "alice"
    assert created["five_tuple"] == list(FT1)
    assert created["lifetime"] == 600
    assert created["expires_at"] == "2026-11-23T10:10:00+00:00"
    assert created["created"] is True
    assert created["existing"] is False

    described = guard.describe(username="alice", allocation_id="alloc-1",
                               now=NOW)
    assert described["five_tuple"] == list(FT1)
    assert described["expires_at"] == "2026-11-23T10:10:00+00:00"
    assert described["permissions"] == []
    assert described["channels"] == {}
    assert described["relayed"] == 0
    assert guard.stats()["allocations"] == 1


def test_duplicate_five_tuple_and_allocation_quota():
    guard = make(max_allocations=1)
    allocate(guard)
    with pytest.raises(tg.TurnError) as exc:
        allocate(guard)
    assert exc.value.code == "TURN_ALLOCATION_EXISTS"
    with pytest.raises(tg.TurnError) as exc2:
        allocate(guard, ft=FT2)
    assert exc2.value.code == "TURN_ALLOCATION_QUOTA"

    released = guard.release(username="alice", allocation_id="alloc-1")
    assert released == {"allocation_id": "alloc-1", "released": True,
                        "permissions": 0, "channels": 0, "relayed": 0}
    again = allocate(guard, ft=FT2)
    assert again["created"] is True
    assert again["allocation_id"] == "alloc-2"


def test_lifetime_defaults_clamp_and_validation():
    guard = make()
    for bad in (30, 0, -1, "600", True):
        with pytest.raises(tg.TurnError) as exc:
            allocate(guard, lifetime=bad)
        assert exc.value.code == "TURN_BAD_LIFETIME"
    assert guard.stats()["allocations"] == 0

    long_lived = allocate(guard, lifetime=99999)
    assert long_lived["lifetime"] == 3600
    assert long_lived["expires_at"] == "2026-11-23T11:00:00+00:00"
    short = allocate(guard, ft=FT2, lifetime=60)
    assert short["lifetime"] == 60
    assert short["expires_at"] == "2026-11-23T10:01:00+00:00"


def test_unknown_user_realm_and_malformed_five_tuple():
    guard = make()
    with pytest.raises(tg.TurnError) as exc:
        allocate(guard, username="ghost")
    assert exc.value.code == "TURN_UNKNOWN_USER"
    with pytest.raises(tg.TurnError) as exc2:
        allocate(guard, realm="wrong.example")
    assert exc2.value.code == "TURN_REALM_MISMATCH"

    for bad in (
        ("udp", "10.1.0.1:5000"),
        ("sctp", "10.1.0.1:5000", "10.1.0.2:3478"),
        ("udp", "10.1.0.300:5000", "10.1.0.2:3478"),
        ("udp", "10.1.0.1:0", "10.1.0.2:3478"),
        ("udp", "10.1.0.1:5000", "10.1.0.2:99999"),
        "udp:10.1.0.1:5000",
    ):
        with pytest.raises(tg.TurnError) as err:
            allocate(guard, ft=bad)
        assert err.value.code == "TURN_BAD_FIVE_TUPLE"


def test_refresh_extends_allocation_and_expired_is_refused():
    guard = make()
    allocate(guard, lifetime=60)
    refreshed = guard.refresh(username="alice", allocation_id="alloc-1",
                              lifetime=600, now="2026-11-23T10:00:30+00:00")
    assert refreshed["lifetime"] == 600
    assert refreshed["expires_at"] == "2026-11-23T10:10:30+00:00"
    assert refreshed["refreshed"] is True
    assert guard.stats()["refreshed"] == 1

    with pytest.raises(tg.TurnError) as exc:
        guard.refresh(username="alice", allocation_id="alloc-99", now=NOW)
    assert exc.value.code == "TURN_NO_ALLOCATION"
    with pytest.raises(tg.TurnError) as exc2:
        guard.refresh(username="alice", allocation_id="alloc-1",
                      now="2026-11-23T10:11:00+00:00")
    assert exc2.value.code == "TURN_ALLOCATION_EXPIRED"


def test_refresh_and_relay_by_other_user_are_mismatch():
    guard = make()
    guard.add_user("bob", realm=REALM)
    allocate(guard)
    for call in (
        lambda: guard.refresh(username="bob", allocation_id="alloc-1", now=NOW),
        lambda: guard.create_permission(username="bob", allocation_id="alloc-1",
                                        peer_ip=PEER1, now=NOW),
        lambda: guard.bind_channel(username="bob", allocation_id="alloc-1",
                                   peer_ip=PEER2, now=NOW),
        lambda: guard.relay(username="bob", allocation_id="alloc-1",
                            peer_ip=PEER1, size=10, now=NOW),
        lambda: guard.describe(username="bob", allocation_id="alloc-1", now=NOW),
        lambda: guard.release(username="bob", allocation_id="alloc-1"),
    ):
        with pytest.raises(tg.TurnError) as exc:
            call()
        assert exc.value.code == "TURN_ALLOCATION_MISMATCH"


def test_permissions_are_idempotent_and_quota_bounded():
    guard = make(max_permissions=2)
    allocate(guard)
    first = guard.create_permission(username="alice", allocation_id="alloc-1",
                                    peer_ip=PEER1, now=NOW)
    assert first["created"] is True
    assert first["expires_at"] == "2026-11-23T10:05:00+00:00"
    again = guard.create_permission(username="alice", allocation_id="alloc-1",
                                    peer_ip=PEER1, now=NOW)
    assert again["created"] is False
    assert again["expires_at"] == first["expires_at"]
    guard.create_permission(username="alice", allocation_id="alloc-1",
                            peer_ip=PEER2, now=NOW)
    with pytest.raises(tg.TurnError) as exc:
        guard.create_permission(username="alice", allocation_id="alloc-1",
                                peer_ip=PEER3, now=NOW)
    assert exc.value.code == "TURN_PERMISSION_QUOTA"
    with pytest.raises(tg.TurnError) as exc2:
        guard.create_permission(username="alice", allocation_id="alloc-1",
                                peer_ip="203.0.113", now=NOW)
    assert exc2.value.code == "TURN_BAD_PEER"
    with pytest.raises(tg.TurnError) as exc3:
        guard.create_permission(username="alice", allocation_id="alloc-1",
                                peer_ip="203.0.113.300", now=NOW)
    assert exc3.value.code == "TURN_BAD_PEER"
    assert guard.describe(username="alice", allocation_id="alloc-1",
                          now=NOW)["permissions"] == [PEER1, PEER2]
    assert guard.stats()["permissions"] == 2


def test_channel_auto_assignment_range_and_permission():
    guard = make()
    allocate(guard)
    first = guard.bind_channel(username="alice", allocation_id="alloc-1",
                               peer_ip=PEER1, now=NOW)
    assert first["channel"] == 0x4000
    assert first["created"] is True
    assert first["expires_at"] == "2026-11-23T10:10:00+00:00"
    second = guard.bind_channel(username="alice", allocation_id="alloc-1",
                                peer_ip=PEER2, now=NOW)
    assert second["channel"] == 0x4001
    explicit = guard.bind_channel(username="alice", allocation_id="alloc-1",
                                  peer_ip=PEER3, channel=0x5000, now=NOW)
    assert explicit["channel"] == 0x5000

    repeat = guard.bind_channel(username="alice", allocation_id="alloc-1",
                                peer_ip=PEER1, now=NOW)
    assert repeat["created"] is False
    assert repeat["channel"] == 0x4000
    with pytest.raises(tg.TurnError) as exc:
        guard.bind_channel(username="alice", allocation_id="alloc-1",
                           peer_ip=PEER1, channel=0x5001, now=NOW)
    assert exc.value.code == "TURN_CHANNEL_IN_USE"
    for bad in (0x3FFF, 0x8000, -1, "4000"):
        with pytest.raises(tg.TurnError) as err:
            guard.bind_channel(username="alice", allocation_id="alloc-1",
                               peer_ip="203.0.113.40", channel=bad, now=NOW)
        assert err.value.code == "TURN_BAD_CHANNEL"

    described = guard.describe(username="alice", allocation_id="alloc-1", now=NOW)
    assert described["channels"] == {PEER1: 0x4000, PEER2: 0x4001, PEER3: 0x5000}
    assert described["permissions"] == [PEER1, PEER2, PEER3]
    assert guard.stats()["channels"] == 3


def test_channel_quota_permission_quota_and_channel_in_use():
    guard = make(max_channels=1, max_permissions=4)
    allocate(guard)
    guard.bind_channel(username="alice", allocation_id="alloc-1",
                       peer_ip=PEER1, now=NOW)
    with pytest.raises(tg.TurnError) as exc:
        guard.bind_channel(username="alice", allocation_id="alloc-1",
                           peer_ip=PEER2, now=NOW)
    assert exc.value.code == "TURN_CHANNEL_QUOTA"

    tight = make(max_channels=4, max_permissions=1)
    tight.allocate(username="alice", realm=REALM, five_tuple=FT1, now=NOW)
    tight.bind_channel(username="alice", allocation_id="alloc-1",
                       peer_ip=PEER1, now=NOW)
    with pytest.raises(tg.TurnError) as exc2:
        tight.bind_channel(username="alice", allocation_id="alloc-1",
                           peer_ip=PEER2, now=NOW)
    assert exc2.value.code == "TURN_PERMISSION_QUOTA"

    clash = make()
    clash.allocate(username="alice", realm=REALM, five_tuple=FT1, now=NOW)
    clash.bind_channel(username="alice", allocation_id="alloc-1",
                       peer_ip=PEER1, channel=0x4000, now=NOW)
    with pytest.raises(tg.TurnError) as exc3:
        clash.bind_channel(username="alice", allocation_id="alloc-1",
                           peer_ip=PEER2, channel=0x4000, now=NOW)
    assert exc3.value.code == "TURN_CHANNEL_IN_USE"


def test_relay_requires_permission_and_enforces_size():
    guard = make()
    allocate(guard)
    with pytest.raises(tg.TurnError) as exc:
        guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER1,
                    size=100, now=NOW)
    assert exc.value.code == "TURN_NO_PERMISSION"

    guard.create_permission(username="alice", allocation_id="alloc-1",
                            peer_ip=PEER1, now=NOW)
    moved = guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER1,
                        size=100, now=NOW)
    assert moved == {"allocation_id": "alloc-1", "peer_ip": PEER1,
                     "relayed": 100, "used": 100,
                     "remaining": 1048476}
    for bad in (0, -5, "10", True):
        with pytest.raises(tg.TurnError) as err:
            guard.relay(username="alice", allocation_id="alloc-1",
                        peer_ip=PEER1, size=bad, now=NOW)
        assert err.value.code == "TURN_BAD_SIZE"
    with pytest.raises(tg.TurnError) as exc2:
        guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER2,
                    size=10, now=NOW)
    assert exc2.value.code == "TURN_NO_PERMISSION"
    assert guard.stats()["relayed_bytes"] == 100


def test_relay_quota_rejects_whole_datagram():
    guard = make(bandwidth_bytes=150)
    allocate(guard)
    guard.create_permission(username="alice", allocation_id="alloc-1",
                            peer_ip=PEER1, now=NOW)
    assert guard.relay(username="alice", allocation_id="alloc-1",
                       peer_ip=PEER1, size=100, now=NOW)["remaining"] == 50
    with pytest.raises(tg.TurnError) as exc:
        guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER1,
                    size=60, now=NOW)
    assert exc.value.code == "TURN_QUOTA_EXCEEDED"
    assert guard.describe(username="alice", allocation_id="alloc-1",
                          now=NOW)["relayed"] == 100
    assert guard.stats()["relayed_bytes"] == 100

    assert guard.relay(username="alice", allocation_id="alloc-1",
                       peer_ip=PEER1, size=50, now=NOW)["remaining"] == 0
    with pytest.raises(tg.TurnError) as exc2:
        guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER1,
                    size=1, now=NOW)
    assert exc2.value.code == "TURN_QUOTA_EXCEEDED"
    assert guard.stats()["relayed_bytes"] == 150


def test_release_sweep_and_reset_stats():
    guard = make()
    allocate(guard, lifetime=60)
    guard.create_permission(username="alice", allocation_id="alloc-1",
                            peer_ip=PEER1, now=NOW)
    guard.bind_channel(username="alice", allocation_id="alloc-1",
                       peer_ip=PEER2, now=NOW)
    guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER1,
                size=40, now=NOW)
    assert guard.sweep(now="2026-11-23T10:00:30+00:00") == 0
    assert guard.sweep(now="2026-11-23T10:01:00+00:00") == 1
    with pytest.raises(tg.TurnError) as exc:
        guard.describe(username="alice", allocation_id="alloc-1", now=NOW)
    assert exc.value.code == "TURN_NO_ALLOCATION"
    assert guard.stats()["expired"] == 1

    fresh = allocate(guard, ft=FT2, now="2026-11-23T10:01:00+00:00")
    released = guard.release(username="alice",
                             allocation_id=fresh["allocation_id"])
    assert released["released"] is True
    with pytest.raises(tg.TurnError) as exc2:
        guard.release(username="alice", allocation_id=fresh["allocation_id"])
    assert exc2.value.code == "TURN_NO_ALLOCATION"

    guard.reset_stats()
    assert guard.stats() == {name: 0 for name in (
        "allocations", "refreshed", "permissions", "channels",
        "relayed_bytes", "expired", "rejected")}


def test_snapshot_restore_keeps_allocations_and_budgets():
    guard = make()
    allocate(guard)
    guard.create_permission(username="alice", allocation_id="alloc-1",
                            peer_ip=PEER1, now=NOW)
    guard.bind_channel(username="alice", allocation_id="alloc-1",
                       peer_ip=PEER2, channel=0x4100, now=NOW)
    guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER1,
                size=40, now=NOW)
    state = guard.snapshot()

    guard.relay(username="alice", allocation_id="alloc-1", peer_ip=PEER1,
                size=60, now=NOW)
    allocate(guard, ft=FT2)
    assert len(guard.snapshot()["allocations"]) == 2

    restored = make()
    restored.restore(state)
    described = restored.describe(username="alice", allocation_id="alloc-1",
                                  now=NOW)
    assert described["relayed"] == 40
    assert described["permissions"] == [PEER1, PEER2]
    assert described["channels"] == {PEER2: 0x4100}
    assert restored.stats()["relayed_bytes"] == 40

    moved = restored.relay(username="alice", allocation_id="alloc-1",
                           peer_ip=PEER1, size=10, now=NOW)
    assert moved["used"] == 50
    again = restored.allocate(username="alice", realm=REALM, five_tuple=FT2,
                              now=NOW)
    assert again["created"] is True
    with pytest.raises(tg.TurnError) as exc:
        restored.allocate(username="alice", realm=REALM, five_tuple=FT1, now=NOW)
    assert exc.value.code == "TURN_ALLOCATION_EXISTS"
    assert restored.stats()["allocations"] == 2

    bad_version = copy.deepcopy(state)
    bad_version["version"] = 2
    bad_stats = copy.deepcopy(state)
    bad_stats["stats"] = {"allocations": 1}
    bad_allocs = copy.deepcopy(state)
    bad_allocs["allocations"] = []
    duplicate = copy.deepcopy(state)
    clone = copy.deepcopy(state["allocations"]["alloc-1"])
    clone["allocation_id"] = "alloc-9"
    duplicate["allocations"]["alloc-9"] = clone
    for bad in (None, {}, bad_version, bad_stats, bad_allocs, duplicate):
        with pytest.raises(tg.TurnError) as err:
            restored.restore(bad)
        assert err.value.code == "TURN_SNAPSHOT_INVALID"
    assert restored.describe(username="alice", allocation_id="alloc-1",
                             now=NOW)["relayed"] == 50
    assert len(restored.snapshot()["allocations"]) == 2


def test_concurrent_allocation_and_relay_quota():
    guard = make(bandwidth_bytes=100)
    allocate(guard)
    guard.create_permission(username="alice", allocation_id="alloc-1",
                            peer_ip=PEER1, now=NOW)
    barrier = threading.Barrier(8)
    created = []
    refused = []
    lock = threading.Lock()

    def allocator():
        barrier.wait()
        try:
            out = guard.allocate(username="alice", realm=REALM,
                                 five_tuple=FT3, now=NOW)
        except tg.TurnError as exc:
            with lock:
                refused.append(exc.code)
            return
        with lock:
            created.append(out)

    threads = [threading.Thread(target=allocator) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(created) == 1
    assert refused.count("TURN_ALLOCATION_EXISTS") == 7
    assert guard.stats()["allocations"] == 2

    barrier2 = threading.Barrier(8)
    moved = []
    refused.clear()

    def relayer():
        barrier2.wait()
        try:
            out = guard.relay(username="alice", allocation_id="alloc-1",
                              peer_ip=PEER1, size=20, now=NOW)
        except tg.TurnError as exc:
            with lock:
                refused.append(exc.code)
            return
        with lock:
            moved.append(out["relayed"])

    threads = [threading.Thread(target=relayer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(moved) == 5
    assert sum(moved) == 100
    assert refused.count("TURN_QUOTA_EXCEEDED") == 3
    assert guard.describe(username="alice", allocation_id="alloc-1",
                          now=NOW)["relayed"] == 100
    assert guard.stats()["relayed_bytes"] == 100
