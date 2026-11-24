import threading

import pytest

import lldp_neighbor_guard as lg

NOW = "2026-11-23T10:00:00+00:00"


def frame(**overrides):
    base = {
        "chassis_id": "aa:bb:cc:00:00:01",
        "port_id": "Gi0/1",
        "ttl": 120,
        "system_name": "sw-core-1",
        "capabilities": ["bridge"],
        "mgmt_addr": "10.0.0.11",
    }
    base.update(overrides)
    return base


def make(**kwargs):
    guard = lg.LldpNeighborGuard(**kwargs)
    guard.configure_port("Gi0/1", trust="trusted")
    return guard


def test_port_must_be_configured_before_any_frame():
    guard = lg.LldpNeighborGuard()
    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/9", frame(), now=NOW)
    assert exc.value.code == "LLDP_UNKNOWN_PORT"

    first = guard.configure_port("Gi0/1", trust="trusted", max_neighbors=4)
    assert first["port"] == "Gi0/1"
    assert first["trust"] == "trusted"
    assert first["max_neighbors"] == 4
    again = guard.configure_port("Gi0/1", trust="trusted", max_neighbors=4)
    assert again == first

    with pytest.raises(lg.LldpError) as exc:
        guard.configure_port("Gi0/1", trust="trusted", max_neighbors=8)
    assert exc.value.code == "LLDP_CONFLICT"


def test_untrusted_port_accepts_only_allowlisted_system_names():
    guard = lg.LldpNeighborGuard()
    guard.configure_port("Gi0/1", trust="untrusted",
                         allowed_system_names=("sw-edge-1",))
    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/1", frame(system_name="sw-rogue"), now=NOW)
    assert exc.value.code == "LLDP_UNTRUSTED_PEER"

    ok = guard.receive("Gi0/1", frame(system_name="sw-edge-1"), now=NOW)
    assert ok["status"] == "accepted"
    assert ok["port"] == "Gi0/1"
    assert ok["chassis_id"] == "aa:bb:cc:00:00:01"
    assert ok["expires_at"] == "2026-11-23T10:02:00+00:00"
    assert guard.stats() == {"received": 2, "accepted": 1, "updated": 0,
                             "expired": 0, "rejected": 1}


def test_repeated_frame_refreshes_neighbor_without_duplicating():
    guard = make()
    first = guard.receive("Gi0/1", frame(), now=NOW)
    assert first["status"] == "accepted"
    second = guard.receive("Gi0/1", frame(ttl=60),
                           now="2026-11-23T10:00:30+00:00")
    assert second["status"] == "updated"
    assert second["expires_at"] == "2026-11-23T10:01:30+00:00"

    rows = guard.neighbors("Gi0/1")
    assert len(rows) == 1
    assert rows[0]["ttl"] == 60
    assert rows[0]["port_id"] == "Gi0/1"
    assert guard.stats()["accepted"] == 1
    assert guard.stats()["updated"] == 1


def test_port_can_require_a_declared_capability():
    guard = lg.LldpNeighborGuard()
    guard.configure_port("Gi0/1", trust="trusted", require_capability="router")
    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/1", frame(capabilities=["bridge", "phone"]), now=NOW)
    assert exc.value.code == "LLDP_CAPABILITY_MISMATCH"

    ok = guard.receive("Gi0/1", frame(capabilities=["bridge", "router"]), now=NOW)
    assert ok["status"] == "accepted"
    assert guard.neighbors("Gi0/1")[0]["capabilities"] == ["bridge", "router"]


def test_mgmt_address_must_parse_and_match_allowed_networks():
    guard = lg.LldpNeighborGuard()
    guard.configure_port("Gi0/1", trust="trusted",
                         allowed_mgmt_networks=("10.10.0.0/16",))
    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/1", frame(mgmt_addr="not-an-ip"), now=NOW)
    assert exc.value.code == "LLDP_MGMT_ADDR_INVALID"

    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/1", frame(mgmt_addr="192.168.5.5"), now=NOW)
    assert exc.value.code == "LLDP_MGMT_ADDR_DENIED"

    ok = guard.receive("Gi0/1", frame(mgmt_addr="10.10.7.9"), now=NOW)
    assert ok["status"] == "accepted"
    assert guard.is_quarantined("Gi0/1") is False


def test_ttl_is_scaled_and_zero_is_refused():
    guard = make(ttl_scale=30, max_age=3600)
    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/1", frame(ttl=0), now=NOW)
    assert exc.value.code == "LLDP_TTL_INVALID"

    ok = guard.receive("Gi0/1", frame(ttl=4), now=NOW)
    assert ok["expires_at"] == "2026-11-23T10:02:00+00:00"
    assert guard.expire(now="2026-11-23T10:01:59+00:00") == 0
    assert guard.expire(now="2026-11-23T10:02:00+00:00") == 1


def test_max_age_caps_long_ttl():
    guard = make(ttl_scale=1, max_age=60)
    ok = guard.receive("Gi0/1", frame(ttl=65535), now=NOW)
    assert ok["expires_at"] == "2026-11-23T10:01:00+00:00"
    assert guard.expire(now="2026-11-23T10:00:59+00:00") == 0
    assert guard.expire(now="2026-11-23T10:01:00+00:00") == 1


def test_neighbor_limit_counts_only_new_chassis():
    guard = lg.LldpNeighborGuard(max_neighbors=2)
    guard.configure_port("Gi0/1", trust="trusted")
    guard.receive("Gi0/1", frame(chassis_id="ch-1"), now=NOW)
    guard.receive("Gi0/1", frame(chassis_id="ch-2"), now=NOW)

    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/1", frame(chassis_id="ch-3"), now=NOW)
    assert exc.value.code == "LLDP_NEIGHBOR_LIMIT"
    assert [row["chassis_id"] for row in guard.neighbors("Gi0/1")] == ["ch-1", "ch-2"]

    refreshed = guard.receive("Gi0/1", frame(chassis_id="ch-2", ttl=90), now=NOW)
    assert refreshed["status"] == "updated"
    assert guard.lookup("Gi0/1", "ch-3") is None


def test_same_chassis_on_two_ports_is_refused():
    guard = make()
    guard.configure_port("Gi0/2", trust="trusted")
    guard.receive("Gi0/1", frame(chassis_id="ch-move"), now=NOW)
    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/2", frame(chassis_id="ch-move", port_id="Gi0/9"), now=NOW)
    assert exc.value.code == "LLDP_CHASSIS_CONFLICT"
    assert guard.lookup("Gi0/2", "ch-move") is None
    assert guard.lookup("Gi0/1", "ch-move")["port_id"] == "Gi0/1"


def test_quarantine_blocks_frames_until_released():
    guard = make()
    guard.receive("Gi0/1", frame(), now=NOW)
    marked = guard.quarantine("Gi0/1", reason="storm")
    assert marked == {"port": "Gi0/1", "quarantined": True, "reason": "storm"}
    assert guard.quarantine("Gi0/1", reason="storm") == marked
    assert guard.is_quarantined("Gi0/1") is True

    with pytest.raises(lg.LldpError) as exc:
        guard.receive("Gi0/1", frame(chassis_id="ch-new"), now=NOW)
    assert exc.value.code == "LLDP_PORT_QUARANTINED"
    assert len(guard.neighbors("Gi0/1")) == 1

    assert guard.release("Gi0/1") == {"port": "Gi0/1", "quarantined": False,
                                      "reason": None}
    assert guard.is_quarantined("Gi0/1") is False
    assert guard.receive("Gi0/1", frame(chassis_id="ch-new"), now=NOW)["status"] == "accepted"


def test_expire_ages_out_per_port_and_reports_count():
    guard = make(max_age=120)
    guard.configure_port("Gi0/2", trust="trusted")
    guard.receive("Gi0/1", frame(chassis_id="a", ttl=10), now=NOW)
    guard.receive("Gi0/1", frame(chassis_id="b", ttl=30), now=NOW)
    guard.receive("Gi0/2", frame(chassis_id="c", ttl=20), now=NOW)

    assert guard.expire(now="2026-11-23T10:00:15+00:00") == 1
    assert [row["chassis_id"] for row in guard.neighbors("Gi0/1")] == ["b"]
    assert guard.expire(now="2026-11-23T10:00:30+00:00") == 2
    assert guard.stats()["expired"] == 3


def test_snapshot_restores_ports_neighbors_and_counters():
    guard = make(max_neighbors=3)
    guard.configure_port("Gi0/2", trust="untrusted",
                         allowed_system_names=("sw-edge-1",))
    guard.receive("Gi0/1", frame(chassis_id="ch-1", ttl=60), now=NOW)
    guard.quarantine("Gi0/2", reason="loop")
    state = guard.snapshot()
    guard.receive("Gi0/1", frame(chassis_id="ch-late"), now=NOW)

    restored = lg.LldpNeighborGuard()
    restored.restore(state)
    assert [row["chassis_id"] for row in restored.neighbors("Gi0/1")] == ["ch-1"]
    assert restored.is_quarantined("Gi0/2") is True
    restored.release("Gi0/2")

    with pytest.raises(lg.LldpError) as exc:
        restored.receive("Gi0/2", frame(chassis_id="ch-x", system_name="sw-rogue"),
                         now=NOW)
    assert exc.value.code == "LLDP_UNTRUSTED_PEER"

    ok = restored.receive("Gi0/1", frame(chassis_id="ch-2"), now=NOW)
    assert ok["status"] == "accepted"
    assert restored.stats()["accepted"] == 2
    assert [row["chassis_id"] for row in guard.neighbors("Gi0/1")] == ["ch-1", "ch-late"]


def test_corrupt_snapshot_leaves_existing_state_untouched():
    guard = make()
    guard.receive("Gi0/1", frame(chassis_id="keep"), now=NOW)
    good = guard.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "ports": {}, "stats": {}, "ttl_scale": 1,
         "max_neighbors": 1, "max_age": 1, "max_ports": 1},
        {"version": 1, "ports": [], "stats": {}, "ttl_scale": 1,
         "max_neighbors": 1, "max_age": 1, "max_ports": 1},
        {"version": 1, "ttl_scale": 1, "max_neighbors": 1, "max_age": 1,
         "max_ports": 1, "stats": {},
         "ports": {"Gi0/1": {"trust": "trusted", "neighbors": "nope"}}},
        dict(good, stats="nope"),
    ]
    for bad in bad_states:
        with pytest.raises(lg.LldpError) as exc:
            guard.restore(bad)
        assert exc.value.code == "LLDP_SNAPSHOT_INVALID"

    assert guard.lookup("Gi0/1", "keep") is not None
    assert guard.receive("Gi0/1", frame(chassis_id="keep"), now=NOW)["status"] == "updated"


def test_stats_count_every_outcome_and_reset_zeroes():
    guard = make()
    guard.receive("Gi0/1", frame(chassis_id="s-1"), now=NOW)
    guard.receive("Gi0/1", frame(chassis_id="s-1"), now=NOW)
    guard.receive("Gi0/1", frame(chassis_id="s-2", ttl=10), now=NOW)
    with pytest.raises(lg.LldpError):
        guard.receive("Gi0/9", frame(), now=NOW)
    with pytest.raises(lg.LldpError):
        guard.receive("Gi0/1", frame(chassis_id="s-3", ttl="120"), now=NOW)

    assert guard.stats() == {"received": 5, "accepted": 2, "updated": 1,
                             "expired": 0, "rejected": 2}
    assert guard.expire(now="2026-11-23T10:00:20+00:00") == 1
    assert guard.stats()["expired"] == 1

    guard.reset_stats()
    assert guard.stats() == {"received": 0, "accepted": 0, "updated": 0,
                             "expired": 0, "rejected": 0}


def test_concurrent_frames_for_one_chassis_yield_one_neighbor():
    guard = make()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.receive("Gi0/1", frame(chassis_id="race"), now=NOW)
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            row = {"status": "error", "detail": repr(exc)}
        with lock:
            results.append(row["status"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["accepted"] + ["updated"] * 7
    assert len(guard.neighbors("Gi0/1")) == 1
    assert guard.stats() == {"received": 8, "accepted": 1, "updated": 7,
                             "expired": 0, "rejected": 0}
