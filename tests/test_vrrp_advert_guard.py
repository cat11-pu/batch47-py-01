import threading

import pytest

import vrrp_advert_guard as vg

NOW = "2026-11-23T10:00:00+00:00"
EMPTY_STATS = {"accepted": 0, "refreshed": 0, "ignored": 0, "rejected": 0,
               "promotions": 0}


def advert(**overrides):
    base = {
        "vrid": 7,
        "priority": 120,
        "src": "10.0.0.2",
        "interval": 1,
        "ttl": 255,
        "version": 2,
        "auth_type": "none",
        "auth_data": "",
        "vip_count": 1,
    }
    base.update(overrides)
    return base


def make(**kwargs):
    guard = vg.VrrpAdvertGuard(**kwargs)
    guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=100)
    return guard


def test_group_creation_validates_and_rejects_conflicts():
    guard = vg.VrrpAdvertGuard()
    with pytest.raises(vg.VrrpError) as exc:
        guard.create_group(0, interface="eth0", vip="10.0.0.10", priority=100)
    assert exc.value.code == "VRRP_INVALID_VRID"
    with pytest.raises(vg.VrrpError) as exc:
        guard.create_group(256, interface="eth0", vip="10.0.0.10", priority=100)
    assert exc.value.code == "VRRP_INVALID_VRID"
    with pytest.raises(vg.VrrpError) as exc:
        guard.create_group(7, interface="eth0", vip="10.0.0.999", priority=100)
    assert exc.value.code == "VRRP_INVALID_VIP"
    with pytest.raises(vg.VrrpError) as exc:
        guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=0)
    assert exc.value.code == "VRRP_PRIORITY_INVALID"

    first = guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=100)
    assert first["vrid"] == 7
    assert first["state"] == "backup"
    assert first["master"] is None
    assert first["auth_type"] == "none"
    assert first["advert_interval"] == 1
    again = guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=100)
    assert again == first

    with pytest.raises(vg.VrrpError) as exc:
        guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=120)
    assert exc.value.code == "VRRP_CONFLICT"


def test_ttl_version_and_unknown_vrid_are_rejected():
    guard = make()
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(ttl=64), now=NOW)
    assert exc.value.code == "VRRP_TTL_INVALID"
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(version=3), now=NOW)
    assert exc.value.code == "VRRP_VERSION_MISMATCH"
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(vrid=99), now=NOW)
    assert exc.value.code == "VRRP_UNKNOWN_VRID"
    assert guard.stats() == {"accepted": 0, "refreshed": 0, "ignored": 0,
                             "rejected": 3, "promotions": 0}


def test_source_must_be_valid_and_not_blocked():
    guard = make()
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(src="bogus"), now=NOW)
    assert exc.value.code == "VRRP_INVALID_SOURCE"

    assert guard.block_router("10.0.0.9") is None
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(src="10.0.0.9"), now=NOW)
    assert exc.value.code == "VRRP_SOURCE_BLOCKED"
    assert guard.unblock_router("10.0.0.9") is True
    assert guard.unblock_router("10.0.0.9") is False

    ok = guard.receive(advert(src="10.0.0.9"), now=NOW)
    assert ok["status"] == "accepted"


def test_first_advert_establishes_backup_and_master_timer():
    guard = make()
    row = guard.receive(advert(), now=NOW)
    assert row == {"status": "accepted", "vrid": 7, "master": "10.0.0.2",
                   "master_priority": 120, "state": "backup"}
    state = guard.group_state(7, now=NOW)
    assert state["state"] == "backup"
    assert state["master"] == "10.0.0.2"
    assert state["master_priority"] == 120
    assert state["vip"] == "10.0.0.10"
    assert state["priority"] == 100
    assert state["master_down_remaining"] == 3
    assert guard.groups() == [7]


def test_repeat_advert_refreshes_master_deadline():
    guard = make()
    guard.receive(advert(), now=NOW)
    row = guard.receive(advert(), now="2026-11-23T10:00:01+00:00")
    assert row["status"] == "refreshed"
    state = guard.group_state(7, now="2026-11-23T10:00:01+00:00")
    assert state["master_down_remaining"] == 3
    assert state["state"] == "backup"
    assert guard.stats()["refreshed"] == 1
    assert guard.stats()["accepted"] == 1


def test_higher_priority_router_replaces_master():
    guard = make()
    guard.receive(advert(), now=NOW)
    row = guard.receive(advert(src="10.0.0.3", priority=150),
                        now="2026-11-23T10:00:01+00:00")
    assert row["status"] == "accepted"
    assert row["master"] == "10.0.0.3"
    assert row["master_priority"] == 150
    assert guard.group_state(7, now="2026-11-23T10:00:01+00:00")["master"] == "10.0.0.3"


def test_equal_priority_resolved_by_higher_source_address():
    guard = vg.VrrpAdvertGuard()
    guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=50)
    guard.receive(advert(src="10.0.0.2", priority=100), now=NOW)
    won = guard.receive(advert(src="10.0.0.3", priority=100),
                        now="2026-11-23T10:00:01+00:00")
    assert won["status"] == "accepted"
    assert won["master"] == "10.0.0.3"

    lost = guard.receive(advert(src="10.0.0.2", priority=100),
                         now="2026-11-23T10:00:02+00:00")
    assert lost == {"status": "ignored", "code": "VRRP_TIE_LOST", "vrid": 7,
                    "master": "10.0.0.3"}
    assert guard.stats()["ignored"] == 1


def test_lower_priority_advert_is_ignored():
    guard = vg.VrrpAdvertGuard()
    guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=50)
    guard.receive(advert(src="10.0.0.2", priority=100), now=NOW)
    row = guard.receive(advert(src="10.0.0.4", priority=90),
                        now="2026-11-23T10:00:01+00:00")
    assert row == {"status": "ignored", "code": "VRRP_LOWER_PRIORITY", "vrid": 7,
                   "master": "10.0.0.2"}
    assert guard.group_state(7, now=NOW)["master"] == "10.0.0.2"


def test_priority_zero_makes_the_master_resign():
    guard = make()
    guard.receive(advert(), now=NOW)
    row = guard.receive(advert(priority=0), now="2026-11-23T10:00:01+00:00")
    assert row == {"status": "stopped", "vrid": 7, "master": None,
                   "state": "backup"}
    state = guard.group_state(7, now="2026-11-23T10:00:01+00:00")
    assert state["master"] is None
    assert state["master_down_remaining"] == 0

    other = guard.receive(advert(src="10.0.0.5", priority=0),
                          now="2026-11-23T10:00:02+00:00")
    assert other["status"] == "ignored"
    assert other["code"] == "VRRP_PRIORITY_ZERO"


def test_interval_and_authentication_must_match_group_config():
    guard = vg.VrrpAdvertGuard()
    guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=100,
                       advert_interval=1, auth=("simple", "s3cret"))
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(interval=2), now=NOW)
    assert exc.value.code == "VRRP_INTERVAL_MISMATCH"
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(auth_type="simple", auth_data="wrong"), now=NOW)
    assert exc.value.code == "VRRP_AUTH_MISMATCH"
    with pytest.raises(vg.VrrpError) as exc:
        guard.receive(advert(auth_type="none", auth_data=""), now=NOW)
    assert exc.value.code == "VRRP_AUTH_MISMATCH"

    ok = guard.receive(advert(auth_type="simple", auth_data="s3cret"), now=NOW)
    assert ok["status"] == "accepted"


def test_preemption_waits_for_the_configured_delay():
    guard = vg.VrrpAdvertGuard()
    guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=150,
                       preempt=True, preempt_delay=10)
    guard.create_group(8, interface="eth1", vip="10.0.1.10", priority=150,
                       preempt=False)
    guard.receive(advert(vrid=7, src="10.0.0.2", priority=100), now=NOW)
    guard.receive(advert(vrid=8, src="10.0.1.2", priority=100), now=NOW)

    early = guard.receive(advert(vrid=7, src="10.0.0.2", priority=100),
                          now="2026-11-23T10:00:09+00:00")
    assert early["status"] == "refreshed"
    assert guard.group_state(7, now="2026-11-23T10:00:09+00:00")["state"] == "backup"

    late = guard.receive(advert(vrid=7, src="10.0.0.2", priority=100),
                         now="2026-11-23T10:00:10+00:00")
    assert late["status"] == "takeover"
    assert late["state"] == "master"
    assert late["master"] is None
    assert guard.stats()["promotions"] == 1

    guard.receive(advert(vrid=8, src="10.0.1.2", priority=100),
                  now="2026-11-23T10:00:10+00:00")
    assert guard.group_state(8, now="2026-11-23T10:00:10+00:00")["state"] == "backup"
    assert guard.stats()["promotions"] == 1


def test_master_down_promotes_local_router_after_three_intervals():
    guard = make()
    guard.receive(advert(), now=NOW)
    assert guard.flush(now="2026-11-23T10:00:02+00:00") == 0
    assert guard.group_state(7, now="2026-11-23T10:00:02+00:00")["state"] == "backup"
    assert guard.flush(now="2026-11-23T10:00:03+00:00") == 1
    state = guard.group_state(7, now="2026-11-23T10:00:03+00:00")
    assert state["state"] == "master"
    assert state["master"] is None
    assert state["master_down_remaining"] == 0
    assert guard.stats()["promotions"] == 1
    assert guard.flush(now="2026-11-23T10:00:04+00:00") == 0


def test_manual_takeover_step_down_and_priority_change():
    guard = make()
    row = guard.takeover(7, now=NOW)
    assert row["state"] == "master"
    assert row["master"] is None
    assert guard.stats()["promotions"] == 1

    stepped = guard.step_down(7, now=NOW)
    assert stepped["state"] == "backup"
    assert stepped["master"] is None
    assert guard.group_state(7, now=NOW)["state"] == "backup"

    assert guard.set_priority(7, 120) == {"vrid": 7, "priority": 120}
    with pytest.raises(vg.VrrpError) as exc:
        guard.set_priority(7, 0)
    assert exc.value.code == "VRRP_PRIORITY_INVALID"
    with pytest.raises(vg.VrrpError) as exc:
        guard.takeover(99, now=NOW)
    assert exc.value.code == "VRRP_UNKNOWN_VRID"

    guard.reset_stats()
    assert guard.stats() == EMPTY_STATS


def test_snapshot_restores_groups_blocklist_and_stats():
    guard = make()
    guard.receive(advert(), now=NOW)
    guard.block_router("10.0.0.9")
    state = guard.snapshot()
    guard.step_down(7, now=NOW)
    guard.unblock_router("10.0.0.9")

    restored = vg.VrrpAdvertGuard()
    restored.restore(state)
    assert restored.groups() == [7]
    assert restored.group_state(7, now=NOW)["master"] == "10.0.0.2"
    refreshed = restored.receive(advert(), now="2026-11-23T10:00:01+00:00")
    assert refreshed["status"] == "refreshed"
    with pytest.raises(vg.VrrpError) as exc:
        restored.receive(advert(src="10.0.0.9"), now=NOW)
    assert exc.value.code == "VRRP_SOURCE_BLOCKED"
    assert restored.stats()["accepted"] == 1

    keeper = make()
    keeper.receive(advert(), now=NOW)
    bad_states = [
        None,
        {},
        {"version": 2, "groups": [], "blocked": [], "stats": {},
         "protocol_version": 2},
        dict(state, groups=[{"vrid": 7}]),
        dict(state, stats="nope"),
    ]
    for bad in bad_states:
        with pytest.raises(vg.VrrpError) as exc:
            keeper.restore(bad)
        assert exc.value.code == "VRRP_SNAPSHOT_INVALID"
    assert keeper.group_state(7, now=NOW)["master"] == "10.0.0.2"


def test_concurrent_adverts_refresh_without_state_drift():
    guard = vg.VrrpAdvertGuard()
    guard.create_group(7, interface="eth0", vip="10.0.0.10", priority=50)
    guard.receive(advert(priority=120), now=NOW)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.receive(advert(priority=120),
                                now="2026-11-23T10:00:01+00:00")
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

    assert outcomes == ["refreshed"] * 8
    assert guard.stats()["refreshed"] == 8
    assert guard.stats()["accepted"] == 1
    assert guard.group_state(7, now="2026-11-23T10:00:01+00:00")["state"] == "backup"
