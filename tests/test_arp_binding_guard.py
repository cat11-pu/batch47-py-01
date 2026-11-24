import threading
from datetime import datetime, timedelta, timezone

import pytest

import arp_binding_guard as ab

BASE = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def make_guard(**kwargs):
    params = dict(binding_ttl=300, max_bindings=32, violation_threshold=3,
                  quarantine_seconds=60, learning=True)
    params.update(kwargs)
    guard = ab.ArpBindingGuard(**params)
    guard.create_vlan(10, subnet="10.0.10.0/24", gateway_ip="10.0.10.1",
                      gateway_mac="AA:BB:CC:00:00:01", uplink_port="et0")
    return guard


def arp(guard, *, src_ip, src_mac, dst_ip="10.0.10.1", port, now=None, vlan=10):
    return guard.inspect(vlan, src_ip=src_ip, src_mac=src_mac, dst_ip=dst_ip,
                         port=port, now=at(0) if now is None else now)


def test_vlan_registration_is_idempotent_and_conflicts_are_refused():
    guard = ab.ArpBindingGuard()
    first = guard.create_vlan(10, subnet="10.0.10.0/24", gateway_ip="10.0.10.1",
                              gateway_mac="AA:BB:CC:00:00:01", uplink_port="et0")
    assert first["vlan_id"] == 10
    assert first["subnet"] == "10.0.10.0/24"
    assert first["gateway_mac"] == "aa:bb:cc:00:00:01"
    assert first["uplink_port"] == "et0"
    again = guard.create_vlan(10, subnet="10.0.10.0/24", gateway_ip="10.0.10.1",
                              gateway_mac="aa:bb:cc:00:00:01", uplink_port="et0")
    assert again["vlan_id"] == 10
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.create_vlan(10, subnet="10.0.99.0/24", gateway_ip="10.0.99.1",
                          gateway_mac="aa:bb:cc:00:00:09", uplink_port="et0")
    assert exc.value.code == "ARP_VLAN_CONFLICT"
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.create_vlan(11, subnet="10.0.11.0/24", gateway_ip="10.0.12.1",
                          gateway_mac="aa:bb:cc:00:00:02", uplink_port="et1")
    assert exc.value.code == "ARP_SUBNET_MISMATCH"
    assert guard.remove_vlan(10) is True
    assert guard.remove_vlan(10) is False
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.lookup(10, "10.0.10.20")
    assert exc.value.code == "ARP_UNKNOWN_VLAN"


def test_first_contact_learning_and_repeat_allow_are_counted():
    guard = make_guard()
    first = arp(guard, src_ip="10.0.10.20", src_mac="02:00:00:00:00:20", port="et1")
    assert first["action"] == "learned"
    assert first["kind"] == "dynamic"
    assert first["vlan_id"] == 10
    binding = guard.lookup(10, "10.0.10.20")
    assert binding["mac"] == "02:00:00:00:00:20"
    assert binding["port"] == "et1"
    assert binding["kind"] == "dynamic"
    again = arp(guard, src_ip="10.0.10.20", src_mac="02:00:00:00:00:20",
                port="et1", now=at(30))
    assert again["action"] == "allow"
    assert guard.stats() == {"inspections": 2, "accepted": 1, "learned": 1,
                             "violations": 0, "rejected": 0, "quarantined": 0}


def test_static_binding_blocks_dynamic_overwrite_and_mac_mismatch_is_a_violation():
    guard = make_guard()
    static = guard.bind_static(10, "10.0.10.5", "02:00:00:00:00:05", port="et5")
    assert static["kind"] == "static"
    assert static["created"] is True
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.bind_dynamic(10, "10.0.10.5", "02:00:00:00:00:99", port="et9",
                           now=at(0))
    assert exc.value.code == "ARP_BINDING_CONFLICT"
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.5", src_mac="02:00:00:00:00:99", port="et9")
    assert exc.value.code == "ARP_VIOLATION"
    ok = arp(guard, src_ip="10.0.10.5", src_mac="02:00:00:00:00:05", port="et5")
    assert ok["action"] == "allow"
    assert ok["kind"] == "static"


def test_consecutive_violations_quarantine_port_and_success_resets_streak():
    guard = make_guard(violation_threshold=3, quarantine_seconds=60)
    guard.bind_static(10, "10.0.10.5", "02:00:00:00:00:05", port="et5")
    guard.bind_static(10, "10.0.10.6", "02:00:00:00:00:06", port="et7")
    for _ in range(2):
        with pytest.raises(ab.ArpGuardError) as exc:
            arp(guard, src_ip="10.0.10.5", src_mac="02:00:00:00:00:99", port="et7")
        assert exc.value.code == "ARP_VIOLATION"
    ok = arp(guard, src_ip="10.0.10.6", src_mac="02:00:00:00:00:06", port="et7")
    assert ok["action"] == "allow"
    for _ in range(2):
        with pytest.raises(ab.ArpGuardError):
            arp(guard, src_ip="10.0.10.5", src_mac="02:00:00:00:00:99", port="et7")
    assert guard.is_quarantined("et7", now=at(0)) is False
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.5", src_mac="02:00:00:00:00:99", port="et7")
    assert exc.value.code == "ARP_VIOLATION"
    assert guard.is_quarantined("et7", now=at(0)) is True
    assert guard.is_quarantined("et7", now=at(61)) is False
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.6", src_mac="02:00:00:00:00:06", port="et7")
    assert exc.value.code == "ARP_PORT_QUARANTINED"
    assert guard.release_port("et7") is True
    assert guard.release_port("et7") is False
    assert guard.stats()["quarantined"] == 1


def test_gateway_mac_from_access_port_and_gateway_ip_spoof_are_violations():
    guard = make_guard()
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.77", src_mac="aa:bb:cc:00:00:01", port="et3")
    assert exc.value.code == "ARP_VIOLATION"
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.1", src_mac="02:00:00:00:00:44", port="et4")
    assert exc.value.code == "ARP_VIOLATION"
    ok = arp(guard, src_ip="10.0.10.1", src_mac="aa:bb:cc:00:00:01", port="et0")
    assert ok["action"] == "allow"
    assert ok["kind"] == "static"


def test_strict_mode_rejects_unknown_source_and_validates_subnet():
    guard = make_guard(learning=False)
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.30", src_mac="02:00:00:00:00:30", port="et2")
    assert exc.value.code == "ARP_UNKNOWN_BINDING"
    guard.bind_dynamic(10, "10.0.10.30", "02:00:00:00:00:30", port="et2",
                       now=at(0))
    ok = arp(guard, src_ip="10.0.10.30", src_mac="02:00:00:00:00:30", port="et2")
    assert ok["action"] == "allow"
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.bind_static(10, "10.0.99.30", "02:00:00:00:00:31", port="et2")
    assert exc.value.code == "ARP_SUBNET_MISMATCH"
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.30", src_mac="02:00:00:00:00:30",
            port="et2", vlan=99)
    assert exc.value.code == "ARP_UNKNOWN_VLAN"
    assert guard.stats()["rejected"] == 1


def test_expired_dynamic_binding_can_be_replaced_and_purge_counts_it():
    guard = make_guard(binding_ttl=60)
    learned = arp(guard, src_ip="10.0.10.40", src_mac="02:00:00:00:00:40",
                  port="et4")
    assert learned["action"] == "learned"
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.bind_dynamic(10, "10.0.10.40", "02:00:00:00:00:41", port="et4",
                           now=at(30))
    assert exc.value.code == "ARP_BINDING_CONFLICT"
    replaced = guard.bind_dynamic(10, "10.0.10.40", "02:00:00:00:00:41",
                                  port="et4", now=at(120))
    assert replaced["created"] is True
    assert replaced["kind"] == "dynamic"
    assert guard.lookup(10, "10.0.10.40")["mac"] == "02:00:00:00:00:41"
    assert guard.purge(now=at(200)) == 1
    assert guard.lookup(10, "10.0.10.40") is None
    assert guard.purge(now=at(200)) == 0


def test_binding_capacity_purges_expired_entries_before_refusing():
    guard = make_guard(max_bindings=3, binding_ttl=60)
    first = arp(guard, src_ip="10.0.10.50", src_mac="02:00:00:00:00:50",
                port="et5")
    assert first["action"] == "learned"
    second = arp(guard, src_ip="10.0.10.51", src_mac="02:00:00:00:00:51",
                 port="et5", now=at(5))
    assert second["action"] == "learned"
    with pytest.raises(ab.ArpGuardError) as exc:
        arp(guard, src_ip="10.0.10.52", src_mac="02:00:00:00:00:52",
            port="et6", now=at(10))
    assert exc.value.code == "ARP_TABLE_FULL"
    assert guard.lookup(10, "10.0.10.52") is None
    third = arp(guard, src_ip="10.0.10.52", src_mac="02:00:00:00:00:52",
                port="et6", now=at(120))
    assert third["action"] == "learned"


def test_snapshot_restore_is_deep_and_keeps_bindings_and_stats():
    guard = make_guard()
    arp(guard, src_ip="10.0.10.60", src_mac="02:00:00:00:00:60", port="et6")
    state = guard.snapshot()
    assert state["version"] == 1
    assert set(state["bindings"]) == {"10:10.0.10.1", "10:10.0.10.60"}
    assert state["stats"]["learned"] == 1
    arp(guard, src_ip="10.0.10.61", src_mac="02:00:00:00:00:61", port="et6")
    restored = ab.ArpBindingGuard()
    restored.restore(state)
    assert restored.lookup(10, "10.0.10.60")["mac"] == "02:00:00:00:00:60"
    assert restored.lookup(10, "10.0.10.61") is None
    assert restored.lookup(10, "10.0.10.1")["kind"] == "static"
    assert restored.stats()["learned"] == 1
    state["bindings"]["10:10.0.10.60"]["mac"] = "ff:ff:ff:ff:ff:ff"
    assert guard.lookup(10, "10.0.10.60")["mac"] == "02:00:00:00:00:60"
    assert restored.lookup(10, "10.0.10.60")["mac"] == "02:00:00:00:00:60"


def test_corrupt_snapshot_is_rejected_without_changing_state():
    guard = make_guard()
    arp(guard, src_ip="10.0.10.70", src_mac="02:00:00:00:00:70", port="et7")
    bad_states = [
        None,
        {},
        {"version": 2, "vlans": {}, "bindings": {}, "quarantine": {}, "stats": {}},
        {"version": 1, "vlans": [], "bindings": {}, "quarantine": {}, "stats": {}},
        {"version": 1, "vlans": {}, "bindings": {"broken": {}},
         "quarantine": {}, "stats": {}},
        {"version": 1, "vlans": {}, "bindings": {}, "quarantine": {},
         "stats": "nope"},
    ]
    for state in bad_states:
        with pytest.raises(ab.ArpGuardError) as exc:
            guard.restore(state)
        assert exc.value.code == "ARP_SNAPSHOT_INVALID"
    assert guard.lookup(10, "10.0.10.70")["port"] == "et7"
    assert guard.lookup(10, "10.0.10.20") is None


def test_manual_quarantine_expires_and_purge_reports_cleared_entries():
    guard = make_guard(quarantine_seconds=30)
    info = guard.quarantine_port("et9", now=at(0), reason="port-scan")
    assert info["port"] == "et9"
    assert info["quarantined"] is True
    assert guard.is_quarantined("et9", now=at(0)) is True
    assert guard.is_quarantined("et9", now=at(31)) is False
    assert guard.purge(now=at(31)) == 1
    assert guard.purge(now=at(31)) == 0
    again = guard.quarantine_port("et9", now=at(70))
    assert again["quarantined"] is True


def test_concurrent_first_contact_learns_exactly_one_binding():
    guard = make_guard()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = arp(guard, src_ip="10.0.10.88",
                      src_mac="02:00:00:00:00:88", port="et8")
        except Exception as exc:
            out = {"action": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    actions = sorted(row["action"] for row in results)
    assert actions == ["allow"] * 7 + ["learned"]
    assert guard.stats()["learned"] == 1
    assert guard.stats()["accepted"] == 7
    assert guard.stats()["violations"] == 0


def test_static_binding_replaces_dynamic_and_is_idempotent():
    guard = make_guard()
    guard.bind_dynamic(10, "10.0.10.9", "02:00:00:00:00:09", port="et2",
                       now=at(0))
    static = guard.bind_static(10, "10.0.10.9", "02:00:00:00:00:09", port="et2")
    assert static["kind"] == "static"
    assert static["created"] is True
    again = guard.bind_static(10, "10.0.10.9", "02:00:00:00:00:09", port="et2")
    assert again["created"] is False
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.bind_static(10, "10.0.10.9", "02:00:00:00:00:0a", port="et2")
    assert exc.value.code == "ARP_BINDING_CONFLICT"
    assert guard.remove_binding(10, "10.0.10.9") is True
    assert guard.remove_binding(10, "10.0.10.9") is False
    assert guard.lookup(10, "10.0.10.9") is None


def test_stats_account_for_each_outcome_and_reset_clears_counters():
    guard = make_guard(learning=False)
    guard.bind_dynamic(10, "10.0.10.11", "02:00:00:00:00:11", port="et1",
                       now=at(0))
    arp(guard, src_ip="10.0.10.11", src_mac="02:00:00:00:00:11", port="et1")
    with pytest.raises(ab.ArpGuardError):
        arp(guard, src_ip="10.0.10.12", src_mac="02:00:00:00:00:12", port="et1")
    with pytest.raises(ab.ArpGuardError):
        arp(guard, src_ip="10.0.10.11", src_mac="02:00:00:00:00:99", port="et1")
    counts = guard.stats()
    assert counts == {"inspections": 3, "accepted": 1, "learned": 0,
                      "violations": 1, "rejected": 1, "quarantined": 0}
    assert counts["inspections"] == (counts["accepted"] + counts["learned"]
                                     + counts["violations"] + counts["rejected"])
    guard.reset_stats()
    assert guard.stats() == {"inspections": 0, "accepted": 0, "learned": 0,
                             "violations": 0, "rejected": 0, "quarantined": 0}


def test_remove_vlan_drops_only_its_bindings():
    guard = make_guard()
    guard.create_vlan(20, subnet="10.0.20.0/24", gateway_ip="10.0.20.1",
                      gateway_mac="aa:bb:cc:00:00:02", uplink_port="et0")
    arp(guard, src_ip="10.0.10.20", src_mac="02:00:00:00:00:20", port="et1")
    arp(guard, src_ip="10.0.20.20", src_mac="02:00:00:00:00:21", port="et1",
        vlan=20)
    assert guard.remove_vlan(10) is True
    with pytest.raises(ab.ArpGuardError) as exc:
        guard.lookup(10, "10.0.10.20")
    assert exc.value.code == "ARP_UNKNOWN_VLAN"
    assert guard.lookup(20, "10.0.20.20")["mac"] == "02:00:00:00:00:21"
    assert len(guard.snapshot()["bindings"]) == 2
