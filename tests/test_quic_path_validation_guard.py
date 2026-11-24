import threading

import pytest

import quic_path_validation_guard as qg

NOW = "2026-11-24T10:00:00+00:00"
PEER = {"ip": "203.0.113.7", "port": 4433}
OTHER = {"ip": "203.0.113.7", "port": 4434}
SECRET = "chunk7-secret-a"


def make(**kwargs):
    kwargs.setdefault("secret", SECRET)
    return qg.QuicPathValidationGuard(**kwargs)


def ready(**kwargs):
    guard = make(**kwargs)
    guard.open_connection("c1", peer=PEER, now=NOW)
    guard.record_received("c1", 100, now=NOW)
    return guard


def token_for(guard, peer=PEER, dcid="dcid-1", now=NOW):
    return guard.issue_token(peer=peer, original_dcid=dcid, now=now)["token"]


def test_retry_token_round_trip_and_expiry_boundary():
    guard = make(token_ttl=60)
    issued = guard.issue_token(peer=PEER, original_dcid="dcid-1", now=NOW)
    assert issued["original_dcid"] == "dcid-1"
    assert issued["expires_at"] == 1795514460
    assert isinstance(issued["token"], str) and issued["token"]

    verified = guard.verify_token(issued["token"], peer=PEER,
                                  now="2026-11-24T10:00:59+00:00")
    assert verified["original_dcid"] == "dcid-1"
    assert verified["peer"] == PEER
    with pytest.raises(qg.QuicError) as exc:
        guard.verify_token(issued["token"], peer=PEER,
                           now="2026-11-24T10:01:00+00:00")
    assert exc.value.code == "QUIC_TOKEN_EXPIRED"


def test_token_is_bound_to_peer_and_integrity_protected():
    guard = make()
    token = token_for(guard)
    with pytest.raises(qg.QuicError) as exc:
        guard.verify_token(token, peer=OTHER, now=NOW)
    assert exc.value.code == "QUIC_TOKEN_PEER_MISMATCH"

    foreign = qg.QuicPathValidationGuard(secret="another-secret")
    with pytest.raises(qg.QuicError) as exc:
        foreign.verify_token(token, peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_TOKEN_INVALID"

    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(qg.QuicError) as exc:
        guard.verify_token(tampered, peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_TOKEN_INVALID"
    with pytest.raises(qg.QuicError) as exc:
        guard.verify_token("not-a-token", peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_TOKEN_INVALID"
    assert guard.stats()["tokens_rejected"] == 3
    assert foreign.stats()["tokens_rejected"] == 1


def test_connection_registry_duplicate_limit_and_purge():
    guard = make(max_connections=2)
    opened = guard.open_connection("c1", peer=PEER, now=NOW)
    assert opened == {"conn_id": "c1", "peer": PEER, "validated": False,
                      "send_budget": 0, "amplification_factor": 3}
    guard.open_connection("c2", peer=PEER, now=NOW)
    with pytest.raises(qg.QuicError) as exc:
        guard.open_connection("c3", peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_CONNECTION_LIMIT"
    with pytest.raises(qg.QuicError) as exc:
        guard.open_connection("c1", peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_DUPLICATE_CONNECTION"
    with pytest.raises(qg.QuicError) as exc:
        guard.open_connection("c4", peer={"ip": "", "port": 443}, now=NOW)
    assert exc.value.code == "QUIC_BAD_PEER"

    guard.close_connection("c1", now=NOW)
    guard.open_connection("c3", peer=PEER, now=NOW)
    assert guard.purge(now=NOW) == {"connections": 1, "tickets": 0}


def test_received_and_sent_accounting_uses_three_times_budget():
    guard = ready()
    first = guard.record_received("c1", 50, now=NOW)
    assert first["received"] == 150
    assert first["send_budget"] == 450
    sent = guard.record_sent("c1", 400, now=NOW)
    assert sent["sent"] == 400
    assert sent["send_budget"] == 50


def test_amplification_limit_is_enforced_on_exact_byte_counts():
    guard = ready()
    allowed = guard.check_send("c1", 300, now=NOW)
    assert allowed["allowed"] is True
    assert allowed["send_budget"] == 0
    with pytest.raises(qg.QuicError) as exc:
        guard.check_send("c1", 301, now=NOW)
    assert exc.value.code == "QUIC_AMPLIFICATION_LIMIT"

    assert guard.record_sent("c1", 300, now=NOW)["send_budget"] == 0
    with pytest.raises(qg.QuicError) as exc:
        guard.record_sent("c1", 1, now=NOW)
    assert exc.value.code == "QUIC_AMPLIFICATION_LIMIT"
    assert guard.record_received("c1", 1, now=NOW) == {
        "conn_id": "c1", "received": 101, "sent": 300, "send_budget": 3}


def test_path_validation_requires_token_and_unlocks_sending():
    guard = ready()
    with pytest.raises(qg.QuicError) as exc:
        guard.validate_path("c1", peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_TOKEN_REQUIRED"
    with pytest.raises(qg.QuicError) as exc:
        guard.validate_path("c1", peer=PEER, token=token_for(guard, peer=OTHER), now=NOW)
    assert exc.value.code == "QUIC_TOKEN_PEER_MISMATCH"
    with pytest.raises(qg.QuicError) as exc:
        guard.check_send("c1", 1000, now=NOW)
    assert exc.value.code == "QUIC_AMPLIFICATION_LIMIT"

    ok = guard.validate_path("c1", peer=PEER, token=token_for(guard), now=NOW)
    assert ok["validated"] is True
    assert guard.check_send("c1", 10_000_000, now=NOW)["send_budget"] is None


def test_migration_resets_budget_until_new_path_is_validated():
    guard = ready()
    with pytest.raises(qg.QuicError) as exc:
        guard.migrate("c1", peer=OTHER, now=NOW)
    assert exc.value.code == "QUIC_PATH_UNVALIDATED"

    guard.validate_path("c1", peer=PEER, token=token_for(guard), now=NOW)
    with pytest.raises(qg.QuicError) as exc:
        guard.migrate("c1", peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_SAME_PATH"
    moved = guard.migrate("c1", peer=OTHER, now=NOW)
    assert moved == {"conn_id": "c1", "pending_peer": OTHER,
                     "validated": False, "send_budget": 0}
    with pytest.raises(qg.QuicError) as exc:
        guard.check_send("c1", 1, now=NOW)
    assert exc.value.code == "QUIC_AMPLIFICATION_LIMIT"
    assert guard.record_received("c1", 10, now=NOW)["send_budget"] == 30
    assert guard.validate_path("c1", peer=OTHER,
                               token=token_for(guard, peer=OTHER), now=NOW)["validated"] is True
    assert guard.check_send("c1", 5000, now=NOW)["allowed"] is True


def test_zero_rtt_ticket_replay_and_expiry():
    guard = ready()
    guard.register_ticket("t-1", expires_at="2026-11-24T10:02:00+00:00", now=NOW)
    admitted = guard.admit_0rtt("c1", ticket_id="t-1", now=NOW)
    assert admitted == {"ticket_id": "t-1", "conn_id": "c1", "replayed": False}
    with pytest.raises(qg.QuicError) as exc:
        guard.admit_0rtt("c1", ticket_id="t-1", now=NOW)
    assert exc.value.code == "QUIC_0RTT_REPLAY"
    with pytest.raises(qg.QuicError) as exc:
        guard.admit_0rtt("c1", ticket_id="t-missing", now=NOW)
    assert exc.value.code == "QUIC_UNKNOWN_TICKET"

    guard.register_ticket("t-2", expires_at=NOW, now=NOW)
    with pytest.raises(qg.QuicError) as exc:
        guard.admit_0rtt("c1", ticket_id="t-2", now=NOW)
    assert exc.value.code == "QUIC_TICKET_EXPIRED"
    with pytest.raises(qg.QuicError) as exc:
        guard.register_ticket("t-1", expires_at="2026-11-24T11:00:00+00:00", now=NOW)
    assert exc.value.code == "QUIC_CONFLICT"
    assert guard.stats()["zero_rtt_admitted"] == 1


def test_closed_and_unknown_connections_are_distinguished():
    guard = ready()
    closed = guard.close_connection("c1", now=NOW)
    assert closed == {"closed": True, "conn_id": "c1", "received": 100, "sent": 0}
    with pytest.raises(qg.QuicError) as exc:
        guard.close_connection("c1", now=NOW)
    assert exc.value.code == "QUIC_CONNECTION_CLOSED"
    with pytest.raises(qg.QuicError) as exc:
        guard.record_received("c1", 10, now=NOW)
    assert exc.value.code == "QUIC_CONNECTION_CLOSED"
    with pytest.raises(qg.QuicError) as exc:
        guard.check_send("ghost", 1, now=NOW)
    assert exc.value.code == "QUIC_UNKNOWN_CONNECTION"
    for bad in (-1, True, "10"):
        with pytest.raises(qg.QuicError) as exc:
            guard.record_received("c1", bad, now=NOW)
        assert exc.value.code == "QUIC_BAD_SIZE"


def test_invalid_identifiers_and_configuration_are_rejected():
    with pytest.raises(qg.QuicError) as exc:
        make(secret="")
    assert exc.value.code == "QUIC_BAD_SECRET"
    guard = make()
    with pytest.raises(qg.QuicError) as exc:
        guard.issue_token(peer=PEER, original_dcid="", now=NOW)
    assert exc.value.code == "QUIC_BAD_DCID"
    with pytest.raises(qg.QuicError) as exc:
        guard.issue_token(peer={"ip": "203.0.113.7"}, original_dcid="dcid", now=NOW)
    assert exc.value.code == "QUIC_BAD_PEER"
    with pytest.raises(qg.QuicError) as exc:
        guard.open_connection("", peer=PEER, now=NOW)
    assert exc.value.code == "QUIC_BAD_CONNECTION_ID"
    with pytest.raises(qg.QuicError) as exc:
        guard.register_ticket("", expires_at=NOW, now=NOW)
    assert exc.value.code == "QUIC_BAD_TICKET"
    with pytest.raises(qg.QuicError) as exc:
        guard.record_received("c1", -5, now=NOW)
    assert exc.value.code == "QUIC_BAD_SIZE"


def test_stats_cover_tokens_migrations_and_zero_rtt():
    guard = make()
    token = token_for(guard)
    for bad in ("bad-token", "another.bad"):
        with pytest.raises(qg.QuicError):
            guard.verify_token(bad, peer=PEER, now=NOW)
    guard.open_connection("c1", peer=PEER, now=NOW)
    guard.record_received("c1", 10, now=NOW)
    guard.validate_path("c1", peer=PEER, token=token, now=NOW)
    guard.migrate("c1", peer=OTHER, now=NOW)
    guard.register_ticket("t-1", expires_at="2026-11-24T10:10:00+00:00", now=NOW)
    guard.admit_0rtt("c1", ticket_id="t-1", now=NOW)
    guard.close_connection("c1", now=NOW)
    assert guard.stats() == {"connections_opened": 1, "connections_closed": 1,
                             "tokens_issued": 1, "tokens_rejected": 2,
                             "migrations": 1, "zero_rtt_admitted": 1}
    guard.reset_stats()
    assert guard.stats() == {"connections_opened": 0, "connections_closed": 0,
                             "tokens_issued": 0, "tokens_rejected": 0,
                             "migrations": 0, "zero_rtt_admitted": 0}


def test_snapshot_restore_keeps_budget_validation_and_ticket_state():
    guard = ready()
    guard.validate_path("c1", peer=PEER, token=token_for(guard), now=NOW)
    guard.register_ticket("t-1", expires_at="2026-11-24T10:10:00+00:00", now=NOW)
    guard.admit_0rtt("c1", ticket_id="t-1", now=NOW)
    guard.open_connection("c2", peer=PEER, now=NOW)
    guard.record_received("c2", 7, now=NOW)
    state = guard.snapshot()
    guard.record_received("c2", 100, now=NOW)

    restored = make()
    restored.restore(state)
    assert restored.check_send("c1", 999_999, now=NOW)["allowed"] is True
    assert restored.record_received("c2", 0, now=NOW)["send_budget"] == 21
    with pytest.raises(qg.QuicError) as exc:
        restored.admit_0rtt("c1", ticket_id="t-1", now=NOW)
    assert exc.value.code == "QUIC_0RTT_REPLAY"

    state["connections"].clear()
    state["stats"]["tokens_issued"] = 99
    assert restored.check_send("c1", 1, now=NOW)["allowed"] is True

    good_stats = {name: 0 for name in qg.STAT_KEYS}
    bad_states = [
        None,
        {},
        {"version": 2, "connections": {}, "tickets": {}, "used_tickets": {},
         "stats": good_stats},
        {"version": 1, "connections": "nope", "tickets": {}, "used_tickets": {},
         "stats": good_stats},
        {"version": 1, "connections": {}, "tickets": {}, "used_tickets": [],
         "stats": good_stats},
    ]
    for bad in bad_states:
        with pytest.raises(qg.QuicError) as exc:
            restored.restore(bad)
        assert exc.value.code == "QUIC_SNAPSHOT_INVALID"
    assert restored.record_received("c2", 1, now=NOW)["received"] == 8


def test_concurrent_opens_respect_the_connection_limit():
    guard = make(max_connections=3)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            guard.open_connection("c%d" % index, peer=PEER, now=NOW)
            value = "opened"
        except Exception as exc:
            value = getattr(exc, "code", repr(exc))
        with lock:
            outcomes.append(value)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("opened") == 3
    assert outcomes.count("QUIC_CONNECTION_LIMIT") == 5
    assert guard.stats()["connections_opened"] == 3


def test_purge_removes_expired_tickets_and_closed_connections():
    guard = make()
    guard.register_ticket("t-short", expires_at="2026-11-24T10:00:10+00:00", now=NOW)
    guard.register_ticket("t-long", expires_at="2026-11-24T11:00:00+00:00", now=NOW)
    guard.open_connection("c1", peer=PEER, now=NOW)
    guard.open_connection("c2", peer=PEER, now=NOW)
    guard.close_connection("c1", now=NOW)
    assert guard.purge(now="2026-11-24T10:00:20+00:00") == {"connections": 1, "tickets": 1}
    with pytest.raises(qg.QuicError) as exc:
        guard.check_send("c1", 1, now=NOW)
    assert exc.value.code == "QUIC_UNKNOWN_CONNECTION"
    assert guard.check_send("c2", 0, now=NOW)["allowed"] is True


def test_path_validation_rejects_tokens_for_the_wrong_path():
    guard = ready()
    with pytest.raises(qg.QuicError) as exc:
        guard.validate_path("c1", peer=OTHER, token=token_for(guard, peer=OTHER), now=NOW)
    assert exc.value.code == "QUIC_PATH_UNVALIDATED"

    guard.validate_path("c1", peer=PEER, token=token_for(guard), now=NOW)
    guard.migrate("c1", peer=OTHER, now=NOW)
    with pytest.raises(qg.QuicError) as exc:
        guard.validate_path("c1", peer=OTHER, token=token_for(guard, peer=PEER), now=NOW)
    assert exc.value.code == "QUIC_TOKEN_PEER_MISMATCH"
    third = {"ip": "198.51.100.9", "port": 4433}
    with pytest.raises(qg.QuicError) as exc:
        guard.validate_path("c1", peer=third, token=token_for(guard, peer=third), now=NOW)
    assert exc.value.code == "QUIC_PATH_MISMATCH"
    assert guard.validate_path("c1", peer=OTHER,
                               token=token_for(guard, peer=OTHER), now=NOW)["validated"] is True
