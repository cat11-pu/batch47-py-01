import threading
from datetime import datetime, timedelta, timezone

import pytest

import urpf_source_guard as ug

BASE = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def make_guard(**kwargs):
    params = dict(default_mode="strict", max_prefixes=8)
    params.update(kwargs)
    guard = ug.UrpfGuard(**params)
    guard.add_route("10.10.0.0/16", interface="et-uplink", metric=100)
    guard.add_route("10.10.7.0/24", interface="et-edge", metric=50)
    return guard


def test_route_add_update_and_remove():
    guard = ug.UrpfGuard()
    info = guard.add_route("10.0.0.0/8", interface="et1", metric=100)
    assert info["prefix"] == "10.0.0.0/8"
    assert info["interface"] == "et1"
    assert info["created"] is True
    again = guard.add_route("10.0.0.0/8", interface="et1", metric=100)
    assert again["created"] is False
    updated = guard.add_route("10.0.0.0/8", interface="et1", metric=50)
    assert updated["created"] is False
    assert updated["metric"] == 50
    assert guard.remove_route("10.0.0.0/8") is True
    assert guard.remove_route("10.0.0.0/8") is False
    with pytest.raises(ug.UrpfError) as exc:
        guard.add_route("10.0.0.0/33", interface="et1")
    assert exc.value.code == "URPF_INVALID_PREFIX"


def test_longest_prefix_match_wins():
    guard = make_guard()
    row = guard.route_lookup("10.10.7.9")
    assert row["prefix"] == "10.10.7.0/24"
    assert row["interface"] == "et-edge"
    coarse = guard.route_lookup("10.10.200.1")
    assert coarse["prefix"] == "10.10.0.0/16"
    assert coarse["interface"] == "et-uplink"
    assert guard.route_lookup("192.0.2.1") is None


def test_strict_rpf_accepts_return_path_and_drops_other_interface():
    guard = make_guard()
    ok = guard.check("et-edge", src_ip="10.10.7.9", dst_ip="203.0.113.1",
                     now=at(0))
    assert ok["action"] == "accept"
    assert ok["reason"] == "strict_rpf"
    assert ok["prefix"] == "10.10.7.0/24"
    assert ok["route_interface"] == "et-edge"
    bad = guard.check("et-uplink", src_ip="10.10.7.9", dst_ip="203.0.113.1",
                      now=at(0))
    assert bad["action"] == "drop"
    assert bad["reason"] == "interface_mismatch"


def test_loose_mode_accepts_any_known_source():
    guard = make_guard()
    info = guard.set_interface_mode("et-uplink", mode="loose")
    assert info["mode"] == "loose"
    out = guard.check("et-uplink", src_ip="10.10.7.9", dst_ip="203.0.113.1",
                      now=at(0))
    assert out["action"] == "accept"
    assert out["reason"] == "loose_rpf"


def test_off_mode_and_invalid_mode_handling():
    guard = make_guard()
    guard.set_interface_mode("et-mgmt", mode="off")
    out = guard.check("et-mgmt", src_ip="198.18.7.7", dst_ip="1.1.1.1",
                      now=at(0))
    assert out["action"] == "accept"
    assert out["reason"] == "disabled"
    assert guard.interface_state("et-unknown")["mode"] == "strict"
    unknown = guard.check("et-unknown", src_ip="10.10.7.9",
                          dst_ip="1.1.1.1", now=at(0))
    assert unknown["action"] == "drop"
    assert unknown["reason"] == "interface_mismatch"
    with pytest.raises(ug.UrpfError) as exc:
        guard.set_interface_mode("et-x", mode="bogus")
    assert exc.value.code == "URPF_INVALID_MODE"


def test_asymmetric_allow_relaxes_strict_mode():
    guard = make_guard()
    guard.set_interface_mode("et-uplink", mode="strict",
                             allow_asymmetric=True)
    out = guard.check("et-uplink", src_ip="10.10.7.9", dst_ip="203.0.113.1",
                      now=at(0))
    assert out["action"] == "accept"
    assert out["reason"] == "asymmetric_allow"


def test_unknown_source_is_dropped_with_no_route_reason():
    guard = make_guard()
    out = guard.check("et-edge", src_ip="198.18.0.1", dst_ip="203.0.113.1",
                      now=at(0))
    assert out["action"] == "drop"
    assert out["reason"] == "no_route"
    assert out["prefix"] is None
    assert guard.stats()["no_route"] == 1


def test_allowlisted_source_and_expiry():
    guard = make_guard()
    info = guard.allow_source("198.18.0.0/24", expires_at=at(60))
    assert info["prefix"] == "198.18.0.0/24"
    assert info["created"] is True
    hit = guard.check("et-edge", src_ip="198.18.0.9", dst_ip="203.0.113.1",
                      now=at(30))
    assert hit["action"] == "accept"
    assert hit["reason"] == "allowlisted"
    expired = guard.check("et-edge", src_ip="198.18.0.9",
                          dst_ip="203.0.113.1", now=at(61))
    assert expired["action"] == "drop"
    assert expired["reason"] == "no_route"
    assert guard.purge(now=at(61)) == 1
    assert guard.purge(now=at(61)) == 0


def test_allowlist_can_be_scoped_to_one_interface():
    guard = make_guard()
    guard.allow_source("198.18.5.0/24", interface="et-edge")
    accepted = guard.check("et-edge", src_ip="198.18.5.9",
                           dst_ip="203.0.113.1", now=at(0))
    assert accepted["reason"] == "allowlisted"
    other = guard.check("et-uplink", src_ip="198.18.5.9",
                        dst_ip="203.0.113.1", now=at(0))
    assert other["action"] == "drop"
    assert other["reason"] == "no_route"


def test_null_route_beats_allowlist():
    guard = make_guard()
    guard.add_route("10.66.0.0/16", interface="et-null", next_hop="null0")
    guard.allow_source("10.66.0.0/16")
    row = guard.route_lookup("10.66.1.1")
    assert row["null_route"] is True
    out = guard.check("et-null", src_ip="10.66.1.1", dst_ip="203.0.113.1",
                      now=at(0))
    assert out["action"] == "drop"
    assert out["reason"] == "null_route"


def test_equal_metric_uses_interface_name_as_tie_breaker():
    guard = make_guard()
    guard.add_route("10.77.0.0/16", interface="et-b", metric=100)
    guard.add_route("10.77.0.0/16", interface="et-a", metric=100)
    row = guard.route_lookup("10.77.1.1")
    assert row["prefix"] == "10.77.0.0/16"
    assert row["interface"] == "et-a"
    assert row["candidates"] == ["et-a", "et-b"]
    drop = guard.check("et-b", src_ip="10.77.1.1", dst_ip="203.0.113.1",
                       now=at(0))
    assert drop["reason"] == "interface_mismatch"
    ok = guard.check("et-a", src_ip="10.77.1.1", dst_ip="203.0.113.1",
                     now=at(0))
    assert ok["reason"] == "strict_rpf"


def test_prefix_capacity_refuses_new_prefixes_only():
    guard = ug.UrpfGuard(default_mode="strict", max_prefixes=2)
    guard.add_route("10.1.0.0/16", interface="e1")
    guard.add_route("10.2.0.0/16", interface="e1")
    with pytest.raises(ug.UrpfError) as exc:
        guard.add_route("10.3.0.0/16", interface="e1")
    assert exc.value.code == "URPF_TABLE_FULL"
    updated = guard.add_route("10.1.0.0/16", interface="e2")
    assert updated["created"] is False
    assert len(guard.list_routes()) == 2


def test_snapshot_restore_is_deep_and_keeps_modes_and_allowlist():
    guard = make_guard()
    guard.set_interface_mode("et-uplink", mode="loose")
    guard.allow_source("198.18.0.0/24", interface="et-uplink",
                       expires_at=at(600))
    state = guard.snapshot()
    assert state["version"] == 1
    guard.add_route("10.99.0.0/16", interface="et-x")
    restored = ug.UrpfGuard(default_mode="strict", max_prefixes=8)
    restored.restore(state)
    assert restored.route_lookup("10.99.1.1") is None
    row = restored.route_lookup("10.10.7.9")
    assert row["interface"] == "et-edge"
    out = restored.check("et-uplink", src_ip="198.18.0.9",
                         dst_ip="203.0.113.1", now=at(0))
    assert out["reason"] == "allowlisted"
    state["routes"].clear()
    assert restored.route_lookup("10.10.7.9") is not None


def test_corrupt_snapshot_is_rejected_without_state_change():
    guard = make_guard()
    bad_states = [
        None,
        {},
        {"version": 2, "routes": {}, "interfaces": {}, "allows": [],
         "stats": {}},
        {"version": 1, "routes": [], "interfaces": {}, "allows": [],
         "stats": {}},
        {"version": 1, "routes": {}, "interfaces": {}, "allows": {},
         "stats": {}},
        {"version": 1, "routes": {}, "interfaces": {}, "allows": [],
         "stats": "nope"},
    ]
    for state in bad_states:
        with pytest.raises(ug.UrpfError) as exc:
            guard.restore(state)
        assert exc.value.code == "URPF_SNAPSHOT_INVALID"
    assert guard.route_lookup("10.10.7.9")["interface"] == "et-edge"


def test_invalid_addresses_and_concurrent_checks():
    guard = make_guard()
    with pytest.raises(ug.UrpfError) as exc:
        guard.check("et-edge", src_ip="999.9.9.9", dst_ip="1.1.1.1")
    assert exc.value.code == "URPF_INVALID_ADDRESS"
    with pytest.raises(ug.UrpfError) as exc:
        guard.check("et-edge", src_ip="10.10.7.9", dst_ip="not-an-ip")
    assert exc.value.code == "URPF_INVALID_ADDRESS"
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.check("et-edge", src_ip="10.10.7.9",
                              dst_ip="203.0.113.1", now=at(0))
        except Exception as exc:
            out = {"action": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(row["action"] == "accept" for row in results)
    assert guard.stats() == {"checked": 8, "accepted": 8, "dropped": 0,
                             "allow_hits": 0, "no_route": 0}
    guard.reset_stats()
    assert guard.stats() == {"checked": 0, "accepted": 0, "dropped": 0,
                             "allow_hits": 0, "no_route": 0}
