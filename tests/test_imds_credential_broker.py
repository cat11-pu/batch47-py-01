import threading

import pytest

import imds_credential_broker as imds

NOW = "2026-11-24T10:00:00+00:00"
ARN = "arn:aws:iam::123456789012:role/web"


def make(**kwargs):
    broker = imds.ImdsCredentialBroker(**kwargs)
    broker.register_role("web", arn=ARN, max_sessions=3, session_ttl=3600,
                         max_ttl=7200)
    broker.register_node("node-a", hop_limit=2)
    broker.allow_role("node-a", "web")
    return broker


def token(broker, node="node-a", now=NOW):
    return broker.rotate_token(node, now=now)["token"]


def issue(broker, tok, **kwargs):
    kwargs.setdefault("hops", 1)
    kwargs.setdefault("now", NOW)
    return broker.issue_session("web", node="node-a", token=tok, **kwargs)


def test_role_registration_validates_and_conflicts():
    broker = imds.ImdsCredentialBroker()
    first = broker.register_role("web", arn=ARN, max_sessions=2,
                                 session_ttl=600, max_ttl=1800)
    assert first == {"role": "web", "arn": ARN, "max_sessions": 2,
                     "session_ttl": 600, "max_ttl": 1800}
    assert broker.register_role("web", arn=ARN, max_sessions=2,
                                session_ttl=600, max_ttl=1800)["max_ttl"] == 1800

    with pytest.raises(imds.ImdsError) as exc:
        broker.register_role("web", arn=ARN, max_sessions=5,
                             session_ttl=600, max_ttl=1800)
    assert exc.value.code == "IMDS_CONFLICT"

    with pytest.raises(imds.ImdsError) as exc:
        broker.register_role("short", arn=ARN, session_ttl=600, max_ttl=300)
    assert exc.value.code == "IMDS_BAD_ROLE"

    with pytest.raises(imds.ImdsError) as exc:
        broker.register_role("bad-arn", arn="web-role", session_ttl=600,
                             max_ttl=1800)
    assert exc.value.code == "IMDS_BAD_ARN"

    with pytest.raises(imds.ImdsError) as exc:
        broker.issue_session("ghost", node="node-a", token="tok", hops=1, now=NOW)
    assert exc.value.code == "IMDS_UNKNOWN_ROLE"


def test_node_registration_and_role_allow_list():
    broker = make()
    node = broker.register_node("node-b", hop_limit=1)
    assert node == {"node": "node-b", "hop_limit": 1, "roles": []}
    assert broker.register_node("node-b", hop_limit=1)["hop_limit"] == 1

    with pytest.raises(imds.ImdsError) as exc:
        broker.register_node("node-b", hop_limit=2)
    assert exc.value.code == "IMDS_CONFLICT"
    with pytest.raises(imds.ImdsError) as exc:
        broker.register_node("node-c", hop_limit=0)
    assert exc.value.code == "IMDS_BAD_HOP_LIMIT"

    with pytest.raises(imds.ImdsError) as exc:
        broker.allow_role("ghost", "web")
    assert exc.value.code == "IMDS_UNKNOWN_NODE"
    with pytest.raises(imds.ImdsError) as exc:
        broker.allow_role("node-b", "ghost")
    assert exc.value.code == "IMDS_UNKNOWN_ROLE"

    tok = token(broker, "node-b")
    with pytest.raises(imds.ImdsError) as exc:
        broker.issue_session("web", node="node-b", token=tok, hops=1, now=NOW)
    assert exc.value.code == "IMDS_ROLE_NOT_ALLOWED"

    allowed = broker.allow_role("node-b", "web")
    assert allowed == {"node": "node-b", "role": "web", "roles": ["web"]}
    assert broker.allow_role("node-b", "web")["roles"] == ["web"]

    out = broker.issue_session("web", node="node-b", token=tok, hops=1, now=NOW)
    assert out["status"] == "issued"


def test_token_rotation_invalidates_previous_and_token_expiry():
    broker = make()
    first = broker.rotate_token("node-a", now=NOW)
    assert first["expires_at"] == "2026-11-24T16:00:00+00:00"
    stale = first["token"]

    second = broker.rotate_token("node-a", now=NOW)
    fresh = second["token"]
    assert fresh != stale

    with pytest.raises(imds.ImdsError) as exc:
        issue(broker, stale)
    assert exc.value.code == "IMDS_TOKEN_INVALID"

    assert issue(broker, fresh, now="2026-11-24T15:59:59+00:00")["status"] == "issued"
    with pytest.raises(imds.ImdsError) as exc:
        issue(broker, fresh, now="2026-11-24T16:00:00+00:00")
    assert exc.value.code == "IMDS_TOKEN_EXPIRED"

    with pytest.raises(imds.ImdsError) as exc:
        broker.rotate_token("ghost", now=NOW)
    assert exc.value.code == "IMDS_UNKNOWN_NODE"


def test_issue_session_returns_short_lived_credentials():
    broker = make()
    tok = token(broker)
    out = issue(broker, tok)
    assert out["status"] == "issued"
    assert out["role"] == "web"
    assert out["node"] == "node-a"
    assert out["ttl"] == 3600
    assert out["expires_at"] == "2026-11-24T11:00:00+00:00"

    credentials = out["credentials"]
    assert credentials["access_key"].startswith("ASIA")
    assert len(credentials["access_key"]) == 20
    assert len(credentials["secret_access_key"]) == 40
    assert len(credentials["session_token"]) >= 32

    other = issue(broker, tok)
    assert other["session_id"] != out["session_id"]
    assert other["credentials"]["access_key"] != credentials["access_key"]

    assert issue(broker, tok, hops=2)["status"] == "issued"


def test_hop_limit_is_enforced():
    broker = make()
    tok = token(broker)
    for hops in (3, 0, "1"):
        with pytest.raises(imds.ImdsError) as exc:
            issue(broker, tok, hops=hops)
        assert exc.value.code == "IMDS_HOP_LIMIT"
    assert issue(broker, tok, hops=2)["status"] == "issued"


def test_tokens_are_bound_to_their_node():
    broker = make()
    broker.register_node("node-b", hop_limit=1)
    broker.allow_role("node-b", "web")
    token_a = token(broker, "node-a")
    token_b = token(broker, "node-b")

    with pytest.raises(imds.ImdsError) as exc:
        broker.issue_session("web", node="node-b", token=token_a, hops=1, now=NOW)
    assert exc.value.code == "IMDS_TOKEN_INVALID"
    with pytest.raises(imds.ImdsError) as exc:
        broker.issue_session("web", node="node-a", token=token_b, hops=1, now=NOW)
    assert exc.value.code == "IMDS_TOKEN_INVALID"
    with pytest.raises(imds.ImdsError) as exc:
        broker.issue_session("web", node="node-a", token=None, hops=1, now=NOW)
    assert exc.value.code == "IMDS_TOKEN_INVALID"

    assert broker.issue_session("web", node="node-b", token=token_b,
                                hops=1, now=NOW)["status"] == "issued"


def test_per_role_session_capacity_ignores_expired_and_revoked_sessions():
    broker = make()
    tok = token(broker)
    ids = [issue(broker, tok, ttl=60)["session_id"] for _ in range(3)]

    with pytest.raises(imds.ImdsError) as exc:
        issue(broker, tok, ttl=60, now="2026-11-24T10:00:30+00:00")
    assert exc.value.code == "IMDS_SESSION_LIMIT"

    assert issue(broker, tok, ttl=60, now="2026-11-24T10:01:00+00:00")["status"] == "issued"

    revoked = make()
    tok = token(revoked)
    revoked_ids = [issue(revoked, tok)["session_id"] for _ in range(3)]
    revoked.revoke_session(revoked_ids[0], reason="compromised")
    assert issue(revoked, tok)["status"] == "issued"


def test_session_ttl_is_clamped_to_role_maximum():
    broker = make()
    tok = token(broker)
    long = issue(broker, tok, ttl=999999)
    assert long["ttl"] == 7200
    assert long["expires_at"] == "2026-11-24T12:00:00+00:00"

    with pytest.raises(imds.ImdsError) as exc:
        issue(broker, tok, ttl=0)
    assert exc.value.code == "IMDS_BAD_TTL"
    with pytest.raises(imds.ImdsError) as exc:
        issue(broker, tok, ttl=-5)
    assert exc.value.code == "IMDS_BAD_TTL"

    short = issue(broker, tok, ttl=120)
    assert short["ttl"] == 120
    assert short["expires_at"] == "2026-11-24T10:02:00+00:00"


def test_renewal_window_and_extension():
    broker = make()
    tok = token(broker)
    sid = issue(broker, tok)["session_id"]

    with pytest.raises(imds.ImdsError) as exc:
        broker.renew_session(sid, now="2026-11-24T10:30:00+00:00")
    assert exc.value.code == "IMDS_RENEW_TOO_EARLY"

    renewed = broker.renew_session(sid, now="2026-11-24T10:50:00+00:00", ttl=1800)
    assert renewed["status"] == "renewed"
    assert renewed["renewals"] == 1
    assert renewed["expires_at"] == "2026-11-24T11:20:00+00:00"
    assert broker.stats()["renewed"] == 1


def test_renewal_count_is_limited():
    broker = make()
    tok = token(broker)
    sid = issue(broker, tok)["session_id"]

    broker.renew_session(sid, now="2026-11-24T10:50:00+00:00", ttl=1800)
    broker.renew_session(sid, now="2026-11-24T11:10:00+00:00")
    broker.renew_session(sid, now="2026-11-24T11:50:00+00:00")

    with pytest.raises(imds.ImdsError) as exc:
        broker.renew_session(sid, now="2026-11-24T11:55:00+00:00")
    assert exc.value.code == "IMDS_RENEW_LIMIT"
    assert broker.stats()["renewed"] == 3


def test_renewal_never_exceeds_the_role_maximum_lifetime():
    broker = make()
    tok = token(broker)
    sid = issue(broker, tok)["session_id"]

    first = broker.renew_session(sid, now="2026-11-24T10:50:00+00:00", ttl=3600)
    assert first["expires_at"] == "2026-11-24T11:50:00+00:00"
    second = broker.renew_session(sid, now="2026-11-24T11:40:00+00:00", ttl=3600)
    assert second["expires_at"] == "2026-11-24T12:00:00+00:00"

    with pytest.raises(imds.ImdsError) as exc:
        broker.renew_session(sid, now="2026-11-24T12:00:00+00:00")
    assert exc.value.code == "IMDS_TTL_EXCEEDED"

    short_broker = make()
    short = issue(short_broker, token(short_broker), ttl=60)["session_id"]
    with pytest.raises(imds.ImdsError) as exc:
        short_broker.renew_session(short, now="2026-11-24T10:02:00+00:00")
    assert exc.value.code == "IMDS_SESSION_EXPIRED"


def test_revoke_and_validate_status_transitions():
    broker = make()
    tok = token(broker)
    sid = issue(broker, tok)["session_id"]

    active = broker.validate(sid, now=NOW)
    assert active["status"] == "active"
    assert active["remaining"] == 3600
    assert broker.validate(sid, now="2026-11-24T10:30:00+00:00")["remaining"] == 1800

    revoked = broker.revoke_session(sid, reason="credential leak")
    assert revoked["status"] == "revoked"
    assert revoked["revoked"] is True
    after = broker.validate(sid, now=NOW)
    assert after["status"] == "revoked"
    assert after["reason"] == "credential leak"
    assert broker.revoke_session(sid, reason="again")["status"] == "revoked"

    other = issue(broker, tok)["session_id"]
    with pytest.raises(imds.ImdsError) as exc:
        broker.revoke_session(other, reason="")
    assert exc.value.code == "IMDS_BAD_REASON"
    assert broker.validate(other, now=NOW)["status"] == "active"
    assert broker.validate(other, now="2026-11-24T11:00:00+00:00")["status"] == "expired"

    with pytest.raises(imds.ImdsError) as exc:
        broker.validate("sess-999", now=NOW)
    assert exc.value.code == "IMDS_UNKNOWN_SESSION"


def test_sweep_removes_expired_sessions():
    broker = make()
    tok = token(broker)
    short = issue(broker, tok, ttl=60)["session_id"]
    keep = issue(broker, tok)["session_id"]

    assert broker.sweep(now="2026-11-24T10:00:30+00:00") == 0
    assert broker.sweep(now="2026-11-24T10:01:00+00:00") == 1
    with pytest.raises(imds.ImdsError) as exc:
        broker.validate(short, now="2026-11-24T10:01:01+00:00")
    assert exc.value.code == "IMDS_UNKNOWN_SESSION"
    assert broker.validate(keep, now="2026-11-24T10:01:01+00:00")["status"] == "active"


def test_snapshot_restore_round_trip_and_corruption():
    broker = make()
    tok = token(broker)
    first = issue(broker, tok)["session_id"]
    second = issue(broker, tok)["session_id"]
    broker.revoke_session(second, reason="done")
    stats_before = broker.stats()
    state = broker.snapshot()
    assert state["version"] == 1

    state["stats"]["issued"] = 99
    state["roles"]["web"]["max_sessions"] = 1
    assert broker.stats() == stats_before

    clean = broker.snapshot()
    third = issue(broker, tok)["session_id"]
    broker.revoke_session(first, reason="rotated")

    restored = imds.ImdsCredentialBroker()
    restored.restore(clean)
    assert restored.validate(first, now=NOW)["status"] == "active"
    assert restored.validate(second, now=NOW)["status"] == "revoked"
    assert restored.renew_session(first, now="2026-11-24T10:50:00+00:00",
                                  ttl=1800)["status"] == "renewed"
    with pytest.raises(imds.ImdsError) as exc:
        restored.validate(third, now=NOW)
    assert exc.value.code == "IMDS_UNKNOWN_SESSION"
    assert restored.stats()["issued"] == 2

    before_bad = restored.stats()
    bad_states = [
        None,
        {},
        {"version": 2, "roles": {}, "nodes": {}, "sessions": {},
         "stats": {}, "token_sequence": 0, "session_sequence": 0},
        {"version": 1, "roles": [], "nodes": {}, "sessions": {},
         "stats": {}, "token_sequence": 0, "session_sequence": 0},
        {"version": 1, "roles": {}, "nodes": "nope", "sessions": {},
         "stats": {}, "token_sequence": 0, "session_sequence": 0},
        {"version": 1, "roles": {}, "nodes": {}, "sessions": {},
         "stats": "nope", "token_sequence": 0, "session_sequence": 0},
        {"version": 1, "roles": {}, "nodes": {}, "sessions": {},
         "stats": {}, "token_sequence": "x", "session_sequence": 0},
    ]
    for bad in bad_states:
        with pytest.raises(imds.ImdsError) as exc:
            restored.restore(bad)
        assert exc.value.code == "IMDS_SNAPSHOT_INVALID"

    assert restored.stats() == before_bad
    assert restored.validate(first, now=NOW)["status"] == "active"


def test_concurrent_issue_honours_session_cap():
    broker = make()
    tok = token(broker)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = issue(broker, tok)
            outcome = (out["status"], out["session_id"])
        except imds.ImdsError as exc:
            outcome = (exc.code, None)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    issued = [session for status, session in results if status == "issued"]
    limited = [status for status, _ in results if status == "IMDS_SESSION_LIMIT"]
    assert len(issued) == 3
    assert len(set(issued)) == 3
    assert len(limited) == 5
    assert broker.stats() == {"issued": 3, "renewed": 0, "revoked": 0, "rejected": 5}
