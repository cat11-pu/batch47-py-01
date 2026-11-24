import threading
from datetime import datetime, timedelta, timezone

import pytest

import bpdu_guard as bg

BASE = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def bpdu(**overrides):
    base = {"bridge_id": "32768.aa:bb:cc:00:00:01",
            "root_id": "32768.aa:bb:cc:00:00:01",
            "root_priority": 32768, "bridge_priority": 32768,
            "tc_flag": False}
    base.update(overrides)
    return base


def make_guard(**kwargs):
    params = dict(tc_threshold=3, tc_window=10, err_disable_seconds=300,
                  max_err_disable=2)
    params.update(kwargs)
    guard = bg.BpduGuard(**params)
    guard.set_root_bridge("32768.00:11:22:33:44:55", priority=32768)
    guard.register_port("et1", role="access")
    guard.register_port("et2", role="uplink")
    guard.register_port("et3", role="root")
    return guard


def test_port_registration_roles_and_conflicts():
    guard = bg.BpduGuard()
    info = guard.register_port("et1", role="access")
    assert info["port"] == "et1"
    assert info["role"] == "access"
    assert info["bpdu_guard"] is True
    assert info["root_guard"] is False
    again = guard.register_port("et1", role="access")
    assert again["bpdu_guard"] is True
    with pytest.raises(bg.BpduError) as exc:
        guard.register_port("et1", role="uplink")
    assert exc.value.code == "BPDU_PORT_CONFLICT"
    with pytest.raises(bg.BpduError) as exc:
        guard.register_port("et9", role="trunk")
    assert exc.value.code == "BPDU_INVALID_ROLE"
    assert guard.unregister_port("et1") is True
    assert guard.unregister_port("et1") is False


def test_bpdu_on_access_port_err_disables_it():
    guard = make_guard()
    out = guard.receive_bpdu("et1", bpdu(), now=at(0))
    assert out["action"] == "err_disable"
    assert out["reason"] == "bpdu_on_access"
    assert guard.is_err_disabled("et1", now=at(0)) is True
    with pytest.raises(bg.BpduError) as exc:
        guard.receive_bpdu("et1", bpdu(), now=at(1))
    assert exc.value.code == "BPDU_PORT_ERR_DISABLED"
    assert guard.stats()["err_disabled"] == 1


def test_err_disabled_port_recovers_after_timer_via_purge():
    guard = make_guard(err_disable_seconds=300)
    guard.receive_bpdu("et1", bpdu(), now=at(0))
    assert guard.is_err_disabled("et1", now=at(299)) is True
    assert guard.purge(now=at(301)) == 1
    assert guard.is_err_disabled("et1", now=at(301)) is False


def test_second_err_disable_becomes_sticky_until_manual_recovery():
    guard = make_guard(err_disable_seconds=300, max_err_disable=2)
    guard.receive_bpdu("et1", bpdu(), now=at(0))
    assert guard.purge(now=at(301)) == 1
    guard.receive_bpdu("et1", bpdu(), now=at(310))
    assert guard.purge(now=at(620)) == 0
    assert guard.is_err_disabled("et1", now=at(620)) is True
    assert guard.recover_port("et1", now=at(620)) is True
    assert guard.is_err_disabled("et1", now=at(620)) is False
    assert guard.recover_port("et1", now=at(620)) is False


def test_valid_bpdu_on_uplink_updates_port_state():
    guard = make_guard()
    out = guard.receive_bpdu(
        "et2", bpdu(bridge_id="32768.aa:bb:cc:00:00:07",
                    root_priority=4096), now=at(0))
    assert out["action"] == "accept"
    state = guard.port_state("et2", now=at(0))
    assert state["port"] == "et2"
    assert state["role"] == "uplink"
    assert state["last_bridge_id"] == "32768.aa:bb:cc:00:00:07"
    assert state["tc_events"] == 0
    assert state["err_disabled"] is False
    assert state["root_inconsistent"] is False


def test_root_guard_blocks_better_root_and_clears_on_inferior_bpdu():
    guard = make_guard()
    out = guard.receive_bpdu("et3", bpdu(root_priority=4096), now=at(0))
    assert out["action"] == "root_inconsistent"
    assert guard.port_state("et3")["root_inconsistent"] is True
    ok = guard.receive_bpdu("et3", bpdu(root_priority=40960), now=at(1))
    assert ok["action"] == "accept"
    assert guard.port_state("et3")["root_inconsistent"] is False
    assert guard.stats()["root_inconsistent"] == 1


def test_root_guard_without_configured_root_is_refused():
    guard = bg.BpduGuard()
    guard.register_port("et3", role="root")
    with pytest.raises(bg.BpduError) as exc:
        guard.receive_bpdu("et3", bpdu(root_priority=4096), now=at(0))
    assert exc.value.code == "BPDU_ROOT_UNKNOWN"


def test_root_bridge_priority_validation():
    guard = bg.BpduGuard()
    with pytest.raises(bg.BpduError) as exc:
        guard.set_root_bridge("32768.00:11", priority=1234)
    assert exc.value.code == "BPDU_INVALID_PRIORITY"
    with pytest.raises(bg.BpduError) as exc:
        guard.set_root_bridge("32768.00:11", priority=65536)
    assert exc.value.code == "BPDU_INVALID_PRIORITY"
    info = guard.set_root_bridge("32768.00:11:22:33:44:55", priority=8192)
    assert info["bridge_id"] == "32768.00:11:22:33:44:55"
    assert info["priority"] == 8192


def test_topology_change_storm_err_disables_uplink():
    guard = make_guard(tc_threshold=3, tc_window=10)
    for second in range(3):
        out = guard.receive_bpdu("et2", bpdu(tc_flag=True), now=at(second))
        assert out["action"] == "accept"
    out = guard.receive_bpdu("et2", bpdu(tc_flag=True), now=at(3))
    assert out["action"] == "err_disable"
    assert out["reason"] == "tc_storm"
    assert out["tc_count"] == 4
    assert guard.is_err_disabled("et2", now=at(3)) is True
    assert guard.stats()["err_disabled"] == 1


def test_topology_change_window_expires_old_samples():
    guard = make_guard(tc_threshold=2, tc_window=10)
    assert guard.receive_bpdu("et2", bpdu(tc_flag=True), now=at(0))["action"] == "accept"
    assert guard.receive_bpdu("et2", bpdu(tc_flag=True), now=at(5))["action"] == "accept"
    assert guard.receive_bpdu("et2", bpdu(tc_flag=True), now=at(20))["action"] == "accept"
    assert guard.receive_bpdu("et2", bpdu(tc_flag=True), now=at(21))["action"] == "accept"
    out = guard.receive_bpdu("et2", bpdu(tc_flag=True), now=at(22))
    assert out["action"] == "err_disable"
    assert guard.port_state("et2", now=at(22))["tc_events"] == 5


def test_unknown_port_and_malformed_bpdu_are_rejected():
    guard = make_guard()
    with pytest.raises(bg.BpduError) as exc:
        guard.receive_bpdu("nope", bpdu(), now=at(0))
    assert exc.value.code == "BPDU_UNKNOWN_PORT"
    with pytest.raises(bg.BpduError) as exc:
        guard.receive_bpdu("et2", {}, now=at(0))
    assert exc.value.code == "BPDU_INVALID"
    with pytest.raises(bg.BpduError) as exc:
        guard.receive_bpdu("et2", bpdu(root_priority="high"), now=at(0))
    assert exc.value.code == "BPDU_INVALID"
    with pytest.raises(bg.BpduError) as exc:
        guard.receive_bpdu("et2", bpdu(tc_flag="yes"), now=at(0))
    assert exc.value.code == "BPDU_INVALID"


def test_snapshot_restore_keeps_ports_root_and_disabled_state():
    guard = make_guard()
    guard.receive_bpdu("et1", bpdu(), now=at(0))
    guard.receive_bpdu("et2", bpdu(bridge_id="32768.aa:bb:cc:00:00:09"),
                       now=at(0))
    state = guard.snapshot()
    assert state["version"] == 1
    guard.receive_bpdu("et2", bpdu(bridge_id="32768.aa:bb:cc:00:00:0a"),
                       now=at(1))
    restored = bg.BpduGuard(tc_threshold=3, tc_window=10,
                            err_disable_seconds=300, max_err_disable=2)
    restored.restore(state)
    assert restored.is_err_disabled("et1", now=at(1)) is True
    assert restored.port_state("et2")["last_bridge_id"] == "32768.aa:bb:cc:00:00:09"
    assert restored.stats()["err_disabled"] == 1
    out = restored.receive_bpdu("et3", bpdu(root_priority=4096), now=at(2))
    assert out["action"] == "root_inconsistent"


def test_corrupt_snapshot_is_rejected_without_state_change():
    guard = make_guard()
    guard.receive_bpdu("et2", bpdu(), now=at(0))
    bad_states = [
        None,
        {},
        {"version": 2, "ports": {}, "root": None, "stats": {}},
        {"version": 1, "ports": [], "root": None, "stats": {}},
        {"version": 1, "ports": {},
         "root": {"bridge_id": "x", "priority": "nope"}, "stats": {}},
        {"version": 1, "ports": {}, "root": None, "stats": "nope"},
    ]
    for state in bad_states:
        with pytest.raises(bg.BpduError) as exc:
            guard.restore(state)
        assert exc.value.code == "BPDU_SNAPSHOT_INVALID"
    assert guard.port_state("et2")["last_bridge_id"] == "32768.aa:bb:cc:00:00:01"
    assert guard.stats()["accepted"] == 1


def test_concurrent_bpdu_reception_is_counted_exactly():
    guard = make_guard()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        for second in range(5):
            try:
                out = guard.receive_bpdu("et2", bpdu(), now=at(second))
            except Exception as exc:
                out = {"action": "error",
                       "code": getattr(exc, "code", repr(exc))}
            with lock:
                results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 40
    assert all(row["action"] == "accept" for row in results)
    assert guard.stats()["received"] == 40
    assert guard.stats()["accepted"] == 40


def test_stats_cover_all_outcomes_and_reset_clears_them():
    guard = make_guard()
    guard.receive_bpdu("et2", bpdu(), now=at(0))
    guard.receive_bpdu("et1", bpdu(), now=at(0))
    guard.receive_bpdu("et3", bpdu(root_priority=4096), now=at(0))
    assert guard.stats() == {"received": 3, "accepted": 1, "err_disabled": 1,
                             "root_inconsistent": 1, "purged": 0}
    guard.reset_stats()
    assert guard.stats() == {"received": 0, "accepted": 0, "err_disabled": 0,
                             "root_inconsistent": 0, "purged": 0}
    assert [row["port"] for row in guard.list_ports()] == ["et1", "et2", "et3"]
