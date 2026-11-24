import threading

import pytest

import igmp_snoop_guard as ig

NOW = "2026-11-23T10:00:00+00:00"
EMPTY_STATS = {"joined": 0, "refreshed": 0, "left": 0, "expired": 0, "rejected": 0}


def make(**kwargs):
    guard = ig.IgmpSnoopGuard(**kwargs)
    guard.add_vlan(100, querier="10.0.0.1")
    return guard


def test_vlan_registration_validates_and_rejects_conflicts():
    guard = ig.IgmpSnoopGuard(max_vlans=1)
    with pytest.raises(ig.IgmpError) as exc:
        guard.add_vlan(0)
    assert exc.value.code == "IGMP_INVALID_VLAN"
    with pytest.raises(ig.IgmpError) as exc:
        guard.add_vlan(4095)
    assert exc.value.code == "IGMP_INVALID_VLAN"

    first = guard.add_vlan(100, querier="10.0.0.1")
    assert first == {"vlan": 100, "querier": "10.0.0.1", "fast_leave": False}
    again = guard.add_vlan(100, querier="10.0.0.1")
    assert again == first

    with pytest.raises(ig.IgmpError) as exc:
        guard.add_vlan(100, querier="10.0.0.1", fast_leave=True)
    assert exc.value.code == "IGMP_CONFLICT"
    with pytest.raises(ig.IgmpError) as exc:
        guard.add_vlan(200)
    assert exc.value.code == "IGMP_VLAN_LIMIT"
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(200, "Gi0/1", "239.1.1.1", now=NOW)
    assert exc.value.code == "IGMP_UNKNOWN_VLAN"


def test_join_requires_an_active_querier():
    guard = ig.IgmpSnoopGuard()
    guard.add_vlan(100)
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert exc.value.code == "IGMP_NO_QUERIER"

    with pytest.raises(ig.IgmpError) as exc:
        guard.set_querier(100, "10.0.0.999")
    assert exc.value.code == "IGMP_INVALID_QUERIER"
    assert guard.set_querier(100, "10.0.0.1", now=NOW) == {"vlan": 100,
                                                           "querier": "10.0.0.1"}

    ok = guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert ok["status"] == "joined"


def test_group_must_be_ipv4_multicast():
    guard = make()
    for bad in ("10.0.0.5", "239.1.1.999", "ff02::1"):
        with pytest.raises(ig.IgmpError) as exc:
            guard.join(100, "Gi0/1", bad, now=NOW)
        assert exc.value.code == "IGMP_INVALID_GROUP"
    ok = guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert ok["status"] == "joined"


def test_version_and_source_validation():
    guard = make()
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/1", "239.1.1.1", version=4, now=NOW)
    assert exc.value.code == "IGMP_INVALID_VERSION"
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/1", "239.1.1.1", version=2, source="10.0.0.5", now=NOW)
    assert exc.value.code == "IGMP_SOURCE_NOT_ALLOWED"
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/1", "239.1.1.1", version=3, source="10.0.0.999", now=NOW)
    assert exc.value.code == "IGMP_INVALID_SOURCE"
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "", "239.1.1.1", now=NOW)
    assert exc.value.code == "IGMP_INVALID_PORT"


def test_v2_join_then_refresh_keeps_one_membership():
    guard = make()
    first = guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert first == {"status": "joined", "vlan": 100, "port": "Gi0/1",
                     "group": "239.1.1.1", "version": 2, "sources": None,
                     "expires_at": "2026-11-23T10:04:20+00:00"}

    again = guard.join(100, "Gi0/1", "239.1.1.1", now="2026-11-23T10:01:00+00:00")
    assert again["status"] == "refreshed"
    assert again["expires_at"] == "2026-11-23T10:05:20+00:00"
    assert guard.members(100, "239.1.1.1") == [{
        "port": "Gi0/1", "version": 2, "sources": None,
        "last_seen": "2026-11-23T10:01:00+00:00", "leaving": False,
    }]
    assert guard.stats()["joined"] == 1
    assert guard.stats()["refreshed"] == 1
    assert guard.flood_targets(100, "239.1.1.1", now=NOW) == ["Gi0/1"]


def test_group_limits_per_port_and_per_vlan():
    guard = ig.IgmpSnoopGuard(max_groups_per_port=2, max_groups_per_vlan=3)
    guard.add_vlan(100, querier="10.0.0.1")
    guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    guard.join(100, "Gi0/1", "239.1.1.2", now=NOW)
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/1", "239.1.1.3", now=NOW)
    assert exc.value.code == "IGMP_GROUP_LIMIT"

    guard.join(100, "Gi0/2", "239.1.1.3", now=NOW)
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/2", "239.1.1.4", now=NOW)
    assert exc.value.code == "IGMP_VLAN_GROUP_LIMIT"
    assert len(guard.members(100, "239.1.1.3")) == 1


def test_denied_group_can_be_re_enabled():
    guard = make()
    assert guard.deny_group(100, "239.2.2.2") == {"vlan": 100,
                                                 "group": "239.2.2.2",
                                                 "denied": True}
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/1", "239.2.2.2", now=NOW)
    assert exc.value.code == "IGMP_GROUP_DENIED"

    assert guard.allow_group(100, "239.2.2.2") is True
    assert guard.allow_group(100, "239.2.2.2") is False
    assert guard.join(100, "Gi0/1", "239.2.2.2", now=NOW)["status"] == "joined"


def test_fast_leave_removes_membership_immediately():
    guard = ig.IgmpSnoopGuard()
    guard.add_vlan(100, querier="10.0.0.1", fast_leave=True)
    guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    left = guard.leave(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert left == {"status": "removed", "vlan": 100, "port": "Gi0/1",
                    "group": "239.1.1.1"}
    assert guard.members(100, "239.1.1.1") == []
    assert guard.flood_targets(100, "239.1.1.1", now=NOW) == []
    assert guard.stats()["left"] == 1

    with pytest.raises(ig.IgmpError) as exc:
        guard.leave(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert exc.value.code == "IGMP_NOT_MEMBER"


def test_pending_leave_removes_after_leave_window():
    guard = make()
    guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    pending = guard.leave(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert pending == {"status": "pending", "vlan": 100, "port": "Gi0/1",
                       "group": "239.1.1.1",
                       "deadline": "2026-11-23T10:00:02+00:00"}
    assert guard.members(100, "239.1.1.1")[0]["leaving"] is True
    assert guard.flood_targets(100, "239.1.1.1", now=NOW) == []

    with pytest.raises(ig.IgmpError) as exc:
        guard.leave(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert exc.value.code == "IGMP_ALREADY_LEAVING"

    assert guard.expire(now="2026-11-23T10:00:01+00:00") == 0
    assert guard.expire(now="2026-11-23T10:00:02+00:00") == 1
    assert guard.members(100, "239.1.1.1") == []
    assert guard.stats()["left"] == 1
    assert guard.stats()["expired"] == 0


def test_group_timeout_boundary_and_stats_reset():
    guard = make()
    guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert guard.expire(now="2026-11-23T10:04:19+00:00") == 0
    assert len(guard.members(100, "239.1.1.1")) == 1
    assert guard.expire(now="2026-11-23T10:04:20+00:00") == 1
    assert guard.members(100, "239.1.1.1") == []
    assert guard.stats()["expired"] == 1

    guard.reset_stats()
    assert guard.stats() == EMPTY_STATS


def test_v3_source_specific_membership_lifecycle():
    guard = make()
    first = guard.join(100, "Gi0/1", "239.3.3.3", version=3,
                       source="10.0.0.11", now=NOW)
    assert first["status"] == "joined"
    assert first["sources"] == ["10.0.0.11"]

    second = guard.join(100, "Gi0/1", "239.3.3.3", version=3,
                        source="10.0.0.12", now=NOW)
    assert second["status"] == "joined_source"
    assert second["sources"] == ["10.0.0.11", "10.0.0.12"]

    one = guard.leave(100, "Gi0/1", "239.3.3.3", source="10.0.0.11", now=NOW)
    assert one == {"status": "source_left", "vlan": 100, "port": "Gi0/1",
                   "group": "239.3.3.3", "sources": ["10.0.0.12"]}
    assert guard.stats()["left"] == 0

    last = guard.leave(100, "Gi0/1", "239.3.3.3", source="10.0.0.12", now=NOW)
    assert last["status"] == "removed"
    assert guard.members(100, "239.3.3.3") == []
    assert guard.stats()["left"] == 1


def test_version_conflict_and_untracked_source():
    guard = make()
    guard.join(100, "Gi0/1", "239.4.4.4", version=2, now=NOW)
    with pytest.raises(ig.IgmpError) as exc:
        guard.join(100, "Gi0/1", "239.4.4.4", version=3, now=NOW)
    assert exc.value.code == "IGMP_VERSION_CONFLICT"

    guard.join(100, "Gi0/2", "239.4.4.5", version=3, now=NOW)
    with pytest.raises(ig.IgmpError) as exc:
        guard.leave(100, "Gi0/2", "239.4.4.5", source="10.0.0.11", now=NOW)
    assert exc.value.code == "IGMP_SOURCE_NOT_TRACKED"


def test_router_ports_receive_all_group_traffic():
    guard = make()
    assert guard.flood_targets(100, "239.5.5.5", now=NOW) == []
    assert guard.mark_router_port(100, "Gi0/24") == {"vlan": 100, "port": "Gi0/24",
                                                     "router": True}
    assert guard.flood_targets(100, "239.5.5.5", now=NOW) == ["Gi0/24"]

    guard.join(100, "Gi0/1", "239.5.5.5", now=NOW)
    assert guard.flood_targets(100, "239.5.5.5", now=NOW) == ["Gi0/1", "Gi0/24"]
    assert guard.mark_router_port(100, "Gi0/24", router=False) == {
        "vlan": 100, "port": "Gi0/24", "router": False}
    assert guard.flood_targets(100, "239.5.5.5", now=NOW) == ["Gi0/1"]

    with pytest.raises(ig.IgmpError) as exc:
        guard.mark_router_port(100, "Gi0/9", router="yes")
    assert exc.value.code == "IGMP_INVALID_ROUTER"


def test_snapshot_restores_pending_leave_sources_and_router_ports():
    guard = ig.IgmpSnoopGuard()
    guard.add_vlan(100, querier="10.0.0.1")
    guard.mark_router_port(100, "Gi0/24")
    guard.join(100, "Gi0/1", "239.1.1.1", now=NOW)
    guard.leave(100, "Gi0/1", "239.1.1.1", now=NOW)
    guard.join(100, "Gi0/2", "239.1.1.2", version=3, source="10.0.0.11", now=NOW)
    state = guard.snapshot()
    guard.expire(now="2026-11-23T10:00:02+00:00")
    guard.mark_router_port(100, "Gi0/24", router=False)

    restored = ig.IgmpSnoopGuard()
    restored.restore(state)
    assert restored.members(100, "239.1.1.1")[0]["leaving"] is True
    assert restored.members(100, "239.1.1.2")[0]["sources"] == ["10.0.0.11"]
    assert restored.flood_targets(100, "239.1.1.2", now=NOW) == ["Gi0/2", "Gi0/24"]

    with pytest.raises(ig.IgmpError) as exc:
        restored.leave(100, "Gi0/1", "239.1.1.1", now=NOW)
    assert exc.value.code == "IGMP_ALREADY_LEAVING"
    assert restored.expire(now="2026-11-23T10:00:02+00:00") == 1
    assert restored.stats()["left"] == 1
    assert guard.members(100, "239.1.1.1") == []

    keeper = make()
    keeper.join(100, "Gi0/1", "239.9.9.9", now=NOW)
    bad_states = [
        None,
        {},
        {"version": 2, "vlans": {}, "stats": {}},
        dict(state, vlans=[]),
        dict(state, stats="nope"),
        dict(state, vlans={"100": {"querier": "10.0.0.1", "fast_leave": False,
                                   "router_ports": [], "denied": [],
                                   "memberships": "nope"}}),
    ]
    for bad in bad_states:
        with pytest.raises(ig.IgmpError) as exc:
            keeper.restore(bad)
        assert exc.value.code == "IGMP_SNAPSHOT_INVALID"
    assert len(keeper.members(100, "239.9.9.9")) == 1


def test_concurrent_join_creates_single_membership():
    guard = make()
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.join(100, "Gi0/1", "239.6.6.6", now=NOW)
            outcome = row["status"]
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            outcome = "error:" + type(exc).__name__
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["joined"] + ["refreshed"] * 7
    assert len(guard.members(100, "239.6.6.6")) == 1
    assert guard.stats()["joined"] == 1
    assert guard.stats()["refreshed"] == 7
