import threading

import pytest

import dot1x_port_guard as dg

NOW = "2026-11-23T10:00:00+00:00"
EMPTY_STATS = {"authorized": 0, "denied": 0, "deauthorized": 0, "reauth_revoked": 0}


def make(**kwargs):
    guard = dg.Dot1xPortGuard(**kwargs)
    guard.add_port("Gi0/1", access_vlan=100, guest_vlan=999)
    return guard


def test_port_registration_is_idempotent_and_strict():
    guard = dg.Dot1xPortGuard()
    with pytest.raises(dg.Dot1xError) as exc:
        guard.port_state("Gi0/9", now=NOW)
    assert exc.value.code == "DOT1X_UNKNOWN_PORT"

    first = guard.add_port("Gi0/1", access_vlan=100, guest_vlan=999)
    assert first["port"] == "Gi0/1"
    assert first["mode"] == "auto"
    assert first["state"] == "unauthorized"
    assert first["vlan"] == 999
    assert first["max_sessions"] == 4

    again = guard.add_port("Gi0/1", access_vlan=100, guest_vlan=999)
    assert again == first

    with pytest.raises(dg.Dot1xError) as exc:
        guard.add_port("Gi0/1", access_vlan=200, guest_vlan=999)
    assert exc.value.code == "DOT1X_CONFLICT"


def test_port_capacity_and_vlan_validation():
    guard = dg.Dot1xPortGuard(max_ports=1)
    with pytest.raises(dg.Dot1xError) as exc:
        guard.add_port("Gi0/1", mode="bogus")
    assert exc.value.code == "DOT1X_INVALID_MODE"

    with pytest.raises(dg.Dot1xError) as exc:
        guard.add_port("Gi0/1", access_vlan=0)
    assert exc.value.code == "DOT1X_INVALID_VLAN"

    with pytest.raises(dg.Dot1xError) as exc:
        guard.add_port("Gi0/1", guest_vlan=5000)
    assert exc.value.code == "DOT1X_INVALID_VLAN"

    guard.add_port("Gi0/1")
    with pytest.raises(dg.Dot1xError) as exc:
        guard.add_port("Gi0/2")
    assert exc.value.code == "DOT1X_PORT_LIMIT"
    assert len(guard.sessions("Gi0/1")) == 0


def test_auto_and_force_authorized_initial_states():
    guard = dg.Dot1xPortGuard()
    auto = guard.add_port("Gi0/1", access_vlan=100, guest_vlan=999)
    assert (auto["state"], auto["vlan"]) == ("unauthorized", 999)

    forced = guard.add_port("Gi0/2", mode="force-authorized", access_vlan=200)
    assert (forced["state"], forced["vlan"]) == ("authorized", 200)

    assert guard.port_state("Gi0/2", now=NOW) == {
        "port": "Gi0/2",
        "mode": "force-authorized",
        "state": "authorized",
        "vlan": 200,
        "sessions": 0,
        "failures": 0,
        "held_until": None,
    }


def test_eapol_start_opens_window_and_respects_forced_unauthorized():
    guard = make()
    started = guard.eapol_start("Gi0/1", now=NOW)
    assert started == {"port": "Gi0/1", "state": "authenticating", "vlan": 999}
    assert guard.port_state("Gi0/1", now=NOW)["state"] == "authenticating"

    guard.add_port("Gi0/2", mode="force-unauthorized", guest_vlan=999)
    with pytest.raises(dg.Dot1xError) as exc:
        guard.eapol_start("Gi0/2", now=NOW)
    assert exc.value.code == "DOT1X_PORT_FORCED_UNAUTHORIZED"


def test_successful_authentication_creates_session_with_dynamic_vlan():
    guard = make()
    guard.eapol_start("Gi0/1", now=NOW)
    row = guard.authenticate("Gi0/1", identity="alice", method="peap",
                             credential_ok=True, vlan=150, now=NOW)
    assert row == {"status": "authorized", "port": "Gi0/1", "identity": "alice",
                   "vlan": 150, "reauth_at": "2026-11-23T11:00:00+00:00"}

    state = guard.port_state("Gi0/1", now=NOW)
    assert state["state"] == "authorized"
    assert state["vlan"] == 150
    assert state["sessions"] == 1

    assert guard.sessions("Gi0/1") == [{
        "identity": "alice",
        "method": "peap",
        "vlan": 150,
        "authorized_at": NOW,
        "reauth_at": "2026-11-23T11:00:00+00:00",
    }]
    assert guard.stats() == {"authorized": 1, "denied": 0, "deauthorized": 0,
                             "reauth_revoked": 0}


def test_failures_accumulate_into_hold_down_then_release():
    guard = make(max_failures=2, hold_down=120)
    first = guard.authenticate("Gi0/1", identity="bob", method="tls",
                               credential_ok=False, now=NOW)
    assert first == {"status": "rejected", "code": "DOT1X_AUTH_FAILED",
                     "port": "Gi0/1", "identity": "bob", "failures": 1,
                     "held": False}
    assert guard.failure_count("Gi0/1") == 1
    assert guard.port_state("Gi0/1", now=NOW)["state"] == "unauthorized"

    second = guard.authenticate("Gi0/1", identity="bob", method="tls",
                                credential_ok=False, now=NOW)
    assert second["held"] is True
    assert second["failures"] == 2
    state = guard.port_state("Gi0/1", now=NOW)
    assert state["state"] == "held"
    assert state["held_until"] == "2026-11-23T10:02:00+00:00"

    with pytest.raises(dg.Dot1xError) as exc:
        guard.authenticate("Gi0/1", identity="bob", method="tls",
                           credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_PORT_HELD"

    ticked = guard.tick(now="2026-11-23T10:02:00+00:00")
    assert ticked == {"reauth_revoked": 0, "released": ["Gi0/1"]}
    state = guard.port_state("Gi0/1", now="2026-11-23T10:02:00+00:00")
    assert state["state"] == "unauthorized"
    assert state["failures"] == 0
    assert state["held_until"] is None
    assert guard.stats()["denied"] == 3


def test_method_and_identity_validation():
    guard = make()
    with pytest.raises(dg.Dot1xError) as exc:
        guard.authenticate("Gi0/1", identity="carol", method="chap",
                           credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_METHOD_UNSUPPORTED"

    with pytest.raises(dg.Dot1xError) as exc:
        guard.authenticate("Gi0/1", identity="   ", method="peap",
                           credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_INVALID_IDENTITY"
    assert guard.sessions("Gi0/1") == []
    assert guard.stats()["denied"] == 2


def test_identity_can_only_be_authorized_on_one_port():
    guard = make()
    guard.add_port("Gi0/2", access_vlan=200, guest_vlan=999)
    guard.authenticate("Gi0/1", identity="dave", method="peap",
                       credential_ok=True, now=NOW)
    with pytest.raises(dg.Dot1xError) as exc:
        guard.authenticate("Gi0/2", identity="dave", method="peap",
                           credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_IDENTITY_IN_USE"
    assert guard.sessions("Gi0/2") == []

    assert guard.deauthenticate("Gi0/1", identity="dave", now=NOW) == 1
    ok = guard.authenticate("Gi0/2", identity="dave", method="peap",
                            credential_ok=True, now=NOW)
    assert ok["vlan"] == 200
    assert guard.port_state("Gi0/1", now=NOW)["state"] == "unauthorized"


def test_session_limit_per_port_and_policy_override():
    guard = dg.Dot1xPortGuard(max_sessions_per_port=1)
    guard.add_port("Gi0/1", access_vlan=100)
    guard.authenticate("Gi0/1", identity="eve", method="peap",
                       credential_ok=True, now=NOW)
    with pytest.raises(dg.Dot1xError) as exc:
        guard.authenticate("Gi0/1", identity="frank", method="peap",
                           credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_SESSION_LIMIT"
    assert [s["identity"] for s in guard.sessions("Gi0/1")] == ["eve"]

    assert guard.add_port("Gi0/2", max_sessions=2)["max_sessions"] == 2
    guard.authenticate("Gi0/2", identity="erin", method="peap",
                       credential_ok=True, now=NOW)
    guard.authenticate("Gi0/2", identity="frank", method="peap",
                       credential_ok=True, now=NOW)
    assert [s["identity"] for s in guard.sessions("Gi0/2")] == ["erin", "frank"]


def test_deauthenticate_removes_one_or_all_sessions():
    guard = make(max_sessions_per_port=4)
    guard.authenticate("Gi0/1", identity="a1", method="peap",
                       credential_ok=True, now=NOW)
    guard.authenticate("Gi0/1", identity="a2", method="peap",
                       credential_ok=True, now=NOW)
    with pytest.raises(dg.Dot1xError) as exc:
        guard.deauthenticate("Gi0/1", identity="ghost", now=NOW)
    assert exc.value.code == "DOT1X_SESSION_NOT_FOUND"

    assert guard.deauthenticate("Gi0/1", identity="a1", now=NOW) == 1
    assert [s["identity"] for s in guard.sessions("Gi0/1")] == ["a2"]
    assert guard.deauthenticate("Gi0/1", now=NOW) == 1
    assert guard.port_state("Gi0/1", now=NOW) == {
        "port": "Gi0/1", "mode": "auto", "state": "unauthorized", "vlan": 999,
        "sessions": 0, "failures": 0, "held_until": None,
    }
    assert guard.stats()["deauthorized"] == 2


def test_reauth_deadline_revokes_sessions_and_resets_stats():
    guard = make(reauth_interval=60)
    guard.authenticate("Gi0/1", identity="gina", method="tls",
                       credential_ok=True, now=NOW)
    assert guard.tick(now="2026-11-23T10:00:59+00:00") == {"reauth_revoked": 0,
                                                          "released": []}
    assert guard.port_state("Gi0/1", now="2026-11-23T10:00:59+00:00")["state"] == "authorized"

    assert guard.tick(now="2026-11-23T10:01:00+00:00") == {"reauth_revoked": 1,
                                                          "released": []}
    assert guard.sessions("Gi0/1") == []
    assert guard.port_state("Gi0/1", now="2026-11-23T10:01:00+00:00")["state"] == "unauthorized"
    assert guard.stats() == {"authorized": 1, "denied": 0, "deauthorized": 0,
                             "reauth_revoked": 1}

    guard.reset_stats()
    assert guard.stats() == EMPTY_STATS


def test_force_modes_override_authentication_outcome():
    guard = make()
    guard.add_port("Gi0/2", mode="force-unauthorized", guest_vlan=999)
    with pytest.raises(dg.Dot1xError) as exc:
        guard.authenticate("Gi0/2", identity="hank", method="peap",
                           credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_PORT_FORCED_UNAUTHORIZED"
    assert guard.port_state("Gi0/2", now=NOW)["vlan"] == 999

    switched = guard.set_port_mode("Gi0/2", "force-authorized")
    assert switched["mode"] == "force-authorized"
    assert switched["state"] == "authorized"
    assert switched["vlan"] == 100

    with pytest.raises(dg.Dot1xError) as exc:
        guard.authenticate("Gi0/2", identity="hank", method="peap",
                           credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_MODE_CONFLICT"

    with pytest.raises(dg.Dot1xError) as exc:
        guard.set_port_mode("Gi0/2", "semi")
    assert exc.value.code == "DOT1X_INVALID_MODE"

    assert guard.set_session_limit("Gi0/1", 2) == {"port": "Gi0/1", "max_sessions": 2}
    assert guard.add_port("Gi0/1", access_vlan=100, guest_vlan=999)["max_sessions"] == 2


def test_snapshot_restores_sessions_hold_state_and_counters():
    guard = make(max_failures=2, hold_down=120)
    guard.add_port("Gi0/2", access_vlan=200, guest_vlan=999)
    guard.authenticate("Gi0/1", identity="ivy", method="tls",
                       credential_ok=True, now=NOW)
    guard.authenticate("Gi0/2", identity="joe", method="tls",
                       credential_ok=False, now=NOW)
    guard.authenticate("Gi0/2", identity="joe", method="tls",
                       credential_ok=False, now=NOW)
    state = guard.snapshot()
    guard.deauthenticate("Gi0/1", identity="ivy", now=NOW)

    restored = dg.Dot1xPortGuard()
    restored.restore(state)
    assert [s["identity"] for s in restored.sessions("Gi0/1")] == ["ivy"]
    assert restored.port_state("Gi0/2", now=NOW)["state"] == "held"
    assert restored.port_state("Gi0/2", now=NOW)["held_until"] == "2026-11-23T10:02:00+00:00"

    with pytest.raises(dg.Dot1xError) as exc:
        restored.authenticate("Gi0/2", identity="joe", method="tls",
                              credential_ok=True, now=NOW)
    assert exc.value.code == "DOT1X_PORT_HELD"

    assert restored.deauthenticate("Gi0/1", identity="ivy", now=NOW) == 1
    assert guard.sessions("Gi0/1") == []
    assert restored.stats()["authorized"] == 1
    assert restored.stats()["denied"] == 3


def test_corrupt_snapshot_is_rejected_without_state_change():
    guard = make()
    guard.authenticate("Gi0/1", identity="kim", method="peap",
                       credential_ok=True, now=NOW)
    good = guard.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "ports": {}, "stats": {}},
        dict(good, ports=[]),
        dict(good, stats="nope"),
        dict(good, ports={"Gi0/1": {"mode": "auto"}}),
    ]
    for bad in bad_states:
        with pytest.raises(dg.Dot1xError) as exc:
            guard.restore(bad)
        assert exc.value.code == "DOT1X_SNAPSHOT_INVALID"

    assert [s["identity"] for s in guard.sessions("Gi0/1")] == ["kim"]
    assert guard.port_state("Gi0/1", now=NOW)["state"] == "authorized"


def test_concurrent_authentication_creates_exactly_one_session():
    guard = make(max_sessions_per_port=8)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.authenticate("Gi0/1", identity="race", method="peap",
                                     credential_ok=True, now=NOW)
            outcome = row["status"]
        except dg.Dot1xError as exc:  # pragma: no cover - surfaced below
            outcome = exc.code
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["DOT1X_IDENTITY_IN_USE"] * 7 + ["authorized"]
    assert len(guard.sessions("Gi0/1")) == 1
    assert guard.stats() == {"authorized": 1, "denied": 7, "deauthorized": 0,
                             "reauth_revoked": 0}
