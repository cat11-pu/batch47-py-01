import threading
from datetime import datetime, timedelta, timezone

import pytest

import syn_admission_guard as sg

BASE = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def make_guard(**kwargs):
    params = dict(max_half_open=4, per_source_limit=2, syn_timeout=15,
                  established_timeout=300, cookie_secret=b"unit-secret",
                  cookie_window=60)
    params.update(kwargs)
    guard = sg.SynAdmissionGuard(**params)
    guard.register_listener(443, backlog=4)
    return guard


def test_normal_handshake_moves_half_open_to_established():
    guard = make_guard()
    syn = guard.syn("192.0.2.10", 40000, 443, seq=1000, now=at(0))
    assert syn["action"] == "accept"
    assert syn["seq"] == 1000
    assert guard.half_open_count() == 1
    ack = guard.ack("192.0.2.10", 40000, 443, ack=1001, now=at(0))
    assert ack["status"] == "established"
    assert ack["via"] == "buffer"
    assert guard.is_established("192.0.2.10", 40000, 443, now=at(0)) is True
    assert guard.half_open_count() == 0
    assert guard.established_count() == 1
    assert guard.stats() == {"syn": 1, "accepted": 1, "retransmit": 0,
                             "challenged": 0, "dropped": 0, "established": 1}


def test_retransmitted_syn_is_idempotent_and_sequence_change_is_refused():
    guard = make_guard()
    first = guard.syn("192.0.2.10", 40000, 443, seq=1000, now=at(0))
    assert first["action"] == "accept"
    again = guard.syn("192.0.2.10", 40000, 443, seq=1000, now=at(1))
    assert again["action"] == "retransmit"
    assert guard.half_open_count() == 1
    with pytest.raises(sg.SynGuardError) as exc:
        guard.syn("192.0.2.10", 40000, 443, seq=9, now=at(2))
    assert exc.value.code == "SYN_SEQ_CONFLICT"
    assert guard.half_open_count() == 1
    assert guard.stats() == {"syn": 3, "accepted": 1, "retransmit": 1,
                             "challenged": 0, "dropped": 0, "established": 0}


def test_listener_registration_and_input_validation():
    guard = sg.SynAdmissionGuard()
    info = guard.register_listener(443, backlog=8)
    assert info["port"] == 443
    assert info["backlog"] == 8
    assert guard.register_listener(443, backlog=8)["backlog"] == 8
    with pytest.raises(sg.SynGuardError) as exc:
        guard.register_listener(443, backlog=9)
    assert exc.value.code == "SYN_LISTENER_CONFLICT"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.register_listener(70000, backlog=8)
    assert exc.value.code == "SYN_INVALID_PORT"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.syn("192.0.2.10", 40000, 8443, seq=1, now=at(0))
    assert exc.value.code == "SYN_UNKNOWN_LISTENER"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.syn("999.1.1.1", 40000, 443, seq=1, now=at(0))
    assert exc.value.code == "SYN_INVALID"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.syn("192.0.2.10", 0, 443, seq=1, now=at(0))
    assert exc.value.code == "SYN_INVALID"
    assert guard.unregister_listener(443) is True
    assert guard.unregister_listener(443) is False


def test_per_source_limit_switches_to_cookie_challenge():
    guard = make_guard(per_source_limit=2, max_half_open=8)
    assert guard.syn("192.0.2.20", 1, 443, seq=1, now=at(0))["action"] == "accept"
    assert guard.syn("192.0.2.20", 2, 443, seq=2, now=at(0))["action"] == "accept"
    challenge = guard.syn("192.0.2.20", 3, 443, seq=3, now=at(0))
    assert challenge["action"] == "challenge"
    cookie = challenge["cookie"]
    assert isinstance(cookie, str)
    assert len(cookie) == 32
    assert guard.half_open_count() == 2
    done = guard.ack("192.0.2.20", 3, 443, ack=4, cookie=cookie, now=at(1))
    assert done["status"] == "established"
    assert done["via"] == "cookie"
    assert guard.half_open_count() == 2
    assert guard.is_established("192.0.2.20", 3, 443, now=at(1)) is True
    assert guard.established_count() == 1


def test_global_backlog_full_issues_cookie_instead_of_dropping():
    guard = make_guard(max_half_open=2, per_source_limit=5)
    assert guard.syn("192.0.2.30", 1, 443, seq=1, now=at(0))["action"] == "accept"
    assert guard.syn("192.0.2.31", 2, 443, seq=2, now=at(0))["action"] == "accept"
    challenge = guard.syn("192.0.2.32", 3, 443, seq=3, now=at(0))
    assert challenge["action"] == "challenge"
    assert len(challenge["cookie"]) == 32
    assert guard.half_open_count() == 2


def test_listener_backlog_full_issues_cookie():
    guard = make_guard(max_half_open=8, per_source_limit=8)
    guard.register_listener(80, backlog=1)
    assert guard.syn("192.0.2.40", 1, 80, seq=1, now=at(0))["action"] == "accept"
    challenge = guard.syn("192.0.2.41", 2, 80, seq=2, now=at(0))
    assert challenge["action"] == "challenge"
    assert guard.half_open_count() == 1


def test_cookie_verification_rejects_tampering_expiry_and_replay():
    guard = make_guard(per_source_limit=1, cookie_window=60)
    assert guard.syn("192.0.2.90", 1, 443, seq=1, now=at(0))["action"] == "accept"
    challenge = guard.syn("192.0.2.90", 2, 443, seq=2, now=at(0))
    assert challenge["action"] == "challenge"
    cookie = challenge["cookie"]
    with pytest.raises(sg.SynGuardError) as exc:
        guard.ack("192.0.2.90", 2, 443, ack=3, cookie="deadbeef", now=at(0))
    assert exc.value.code == "SYN_COOKIE_INVALID"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.ack("192.0.2.90", 2, 443, ack=99, cookie=cookie, now=at(0))
    assert exc.value.code == "SYN_COOKIE_INVALID"
    done = guard.ack("192.0.2.90", 2, 443, ack=3, cookie=cookie, now=at(1))
    assert done["status"] == "established"
    assert done["via"] == "cookie"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.ack("192.0.2.90", 2, 443, ack=3, cookie=cookie, now=at(2))
    assert exc.value.code == "SYN_COOKIE_REPLAY"
    later = guard.syn("192.0.2.90", 3, 443, seq=4, now=at(10))
    assert later["action"] == "challenge"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.ack("192.0.2.90", 3, 443, ack=5, cookie=later["cookie"],
                  now=at(131))
    assert exc.value.code == "SYN_COOKIE_EXPIRED"


def test_ack_mismatch_keeps_half_open_and_unknown_tuple_is_refused():
    guard = make_guard()
    guard.syn("192.0.2.70", 1, 443, seq=500, now=at(0))
    with pytest.raises(sg.SynGuardError) as exc:
        guard.ack("192.0.2.70", 1, 443, ack=999, now=at(1))
    assert exc.value.code == "SYN_ACK_MISMATCH"
    assert guard.half_open_count() == 1
    done = guard.ack("192.0.2.70", 1, 443, ack=501, now=at(2))
    assert done["status"] == "established"
    assert done["via"] == "buffer"
    with pytest.raises(sg.SynGuardError) as exc:
        guard.ack("192.0.2.71", 2, 443, ack=1, now=at(2))
    assert exc.value.code == "SYN_UNKNOWN_HANDSHAKE"


def test_duplicate_ack_is_idempotent():
    guard = make_guard()
    guard.syn("192.0.2.80", 1, 443, seq=700, now=at(0))
    first = guard.ack("192.0.2.80", 1, 443, ack=701, now=at(1))
    assert first["status"] == "established"
    assert first.get("already") is not True
    second = guard.ack("192.0.2.80", 1, 443, ack=701, now=at(2))
    assert second["status"] == "established"
    assert second["already"] is True
    assert guard.stats()["established"] == 1
    assert guard.established_count() == 1


def test_reset_releases_half_open_and_established_state():
    guard = make_guard()
    guard.syn("192.0.2.60", 1, 443, seq=1, now=at(0))
    assert guard.reset("192.0.2.60", 1, 443) is True
    assert guard.half_open_count() == 0
    assert guard.reset("192.0.2.60", 1, 443) is False
    guard.syn("192.0.2.60", 2, 443, seq=2, now=at(0))
    guard.ack("192.0.2.60", 2, 443, ack=3, now=at(0))
    assert guard.is_established("192.0.2.60", 2, 443, now=at(0)) is True
    assert guard.reset("192.0.2.60", 2, 443) is True
    assert guard.is_established("192.0.2.60", 2, 443, now=at(0)) is False


def test_blocked_source_is_dropped_without_allocating_state():
    guard = make_guard()
    info = guard.block_source("192.0.2.66", reason="abuse")
    assert info["ip"] == "192.0.2.66"
    assert info["blocked"] is True
    assert info["reason"] == "abuse"
    out = guard.syn("192.0.2.66", 1, 443, seq=1, now=at(0))
    assert out["action"] == "drop"
    assert out["reason"] == "blocked"
    assert guard.half_open_count() == 0
    assert guard.stats()["dropped"] == 1
    assert guard.stats()["syn"] == 1
    with pytest.raises(sg.SynGuardError) as exc:
        guard.block_source("not-an-ip")
    assert exc.value.code == "SYN_INVALID"
    assert guard.unblock_source("192.0.2.66") is True
    assert guard.unblock_source("192.0.2.66") is False
    assert guard.syn("192.0.2.66", 1, 443, seq=1, now=at(1))["action"] == "accept"


def test_purge_removes_timed_out_half_open_and_established_entries():
    guard = make_guard(syn_timeout=15, established_timeout=60)
    guard.syn("192.0.2.50", 1, 443, seq=1, now=at(0))
    assert guard.purge(now=at(14)) == 0
    assert guard.half_open_count() == 1
    assert guard.purge(now=at(15)) == 1
    assert guard.half_open_count() == 0
    guard.syn("192.0.2.50", 2, 443, seq=2, now=at(20))
    guard.ack("192.0.2.50", 2, 443, ack=3, now=at(20))
    assert guard.is_established("192.0.2.50", 2, 443, now=at(79)) is True
    assert guard.purge(now=at(81)) == 1
    assert guard.established_count() == 0
    assert guard.is_established("192.0.2.50", 2, 443, now=at(81)) is False


def test_snapshot_restore_keeps_handshakes_cookies_and_blocklist():
    guard = make_guard(per_source_limit=1)
    guard.syn("192.0.2.101", 1, 443, seq=1, now=at(0))
    challenge = guard.syn("192.0.2.101", 2, 443, seq=2, now=at(0))
    cookie = challenge["cookie"]
    guard.block_source("192.0.2.200", reason="abuse")
    state = guard.snapshot()
    assert state["version"] == 1
    guard.syn("192.0.2.102", 3, 443, seq=3, now=at(0))
    restored = sg.SynAdmissionGuard(max_half_open=4, per_source_limit=1,
                                    syn_timeout=15, established_timeout=300,
                                    cookie_secret=b"unit-secret",
                                    cookie_window=60)
    restored.restore(state)
    assert restored.half_open_count() == 1
    assert restored.stats()["syn"] == 2
    assert restored.is_blocked("192.0.2.200") is True
    buffer_ack = restored.ack("192.0.2.101", 1, 443, ack=2, now=at(1))
    assert buffer_ack["status"] == "established"
    cookie_ack = restored.ack("192.0.2.101", 2, 443, ack=3, cookie=cookie,
                              now=at(1))
    assert cookie_ack["status"] == "established"
    assert cookie_ack["via"] == "cookie"
    assert restored.stats()["established"] == 2
    blocked = restored.syn("192.0.2.200", 9, 443, seq=9, now=at(1))
    assert blocked["action"] == "drop"


def test_corrupt_snapshot_is_rejected_without_state_change():
    guard = make_guard()
    guard.syn("192.0.2.111", 1, 443, seq=1, now=at(0))
    bad_states = [
        None,
        {},
        {"version": 2, "listeners": {}, "half_open": {},
         "established": {}, "blocked": {}, "cookies": {}, "stats": {}},
        {"version": 1, "listeners": [], "half_open": {},
         "established": {}, "blocked": {}, "cookies": {}, "stats": {}},
        {"version": 1, "listeners": {}, "half_open": [],
         "established": {}, "blocked": {}, "cookies": {}, "stats": {}},
        {"version": 1, "listeners": {}, "half_open": {},
         "established": {}, "blocked": {}, "cookies": {}, "stats": "nope"},
    ]
    for state in bad_states:
        with pytest.raises(sg.SynGuardError) as exc:
            guard.restore(state)
        assert exc.value.code == "SYN_SNAPSHOT_INVALID"
    assert guard.half_open_count() == 1
    assert guard.stats()["syn"] == 1


def test_concurrent_syn_for_same_tuple_accepts_once():
    guard = make_guard()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.syn("192.0.2.120", 40000, 443, seq=777, now=at(0))
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
    assert actions == ["accept"] + ["retransmit"] * 7
    assert guard.half_open_count() == 1
    assert guard.stats()["syn"] == 8
    assert guard.stats()["accepted"] == 1
    assert guard.stats()["retransmit"] == 7
