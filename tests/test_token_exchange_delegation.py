import copy
import threading

import pytest

import token_exchange_delegation as eg

NOW = "2026-11-24T13:00:00+00:00"
ISSUER = "https://idp.example.com"
API = "https://api.example.com"
REPORTS = "https://reports.example.com"


def make(**kwargs):
    guard = eg.DelegationExchangeGuard(**kwargs)
    guard.register_issuer(ISSUER, audiences=(API, REPORTS),
                          scopes=("read", "write", "admin"))
    guard.register_actor("svc-a", audiences=(API,), scopes=("read", "write"))
    guard.register_actor("svc-b", audiences=(API,), scopes=("read",))
    guard.register_resource(API, scopes=("read", "write"))
    guard.register_resource(REPORTS, scopes=("read",))
    return guard


def subject(guard, **overrides):
    args = {"issuer": ISSUER, "subject": "alice", "audience": API,
            "scopes": ("read", "write"), "now": NOW}
    args.update(overrides)
    return guard.issue_subject_token(**args)


def actor(guard, actor_id="svc-a", **overrides):
    args = {"audience": API, "scopes": ("read", "write"), "now": NOW}
    args.update(overrides)
    return guard.issue_actor_token(actor_id, **args)


def test_exchange_narrows_scopes_and_records_first_hop():
    guard = make()
    token = subject(guard)
    assert token["token_type"] == "subject"
    assert token["issuer"] == ISSUER
    assert token["subject"] == "alice"
    assert token["scopes"] == ["read", "write"]
    assert token["depth"] == 0
    assert token["act"] is None
    assert token["nbf"] == NOW
    assert token["exp"] == "2026-11-24T13:05:00+00:00"
    assert token["jti"]

    out = guard.exchange(subject_token=token, audience=API, scopes=("read",), now=NOW)
    assert out["token_type"] == "access"
    assert out["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
    assert out["access_token"].startswith("at_")
    assert out["scope"] == "read"
    assert out["audience"] == API
    assert out["depth"] == 1
    assert out["act"] is None
    assert out["expires_in"] == 300
    assert out["exp"] == "2026-11-24T13:05:00+00:00"
    assert guard.stats() == {"exchanged": 1, "rejected": 0, "replayed": 0, "revoked": 0}


def test_registration_conflicts_and_trust_boundaries():
    guard = make()
    same = guard.register_issuer(ISSUER, audiences=(REPORTS, API),
                                 scopes=("write", "admin", "read"))
    assert same["audiences"] == [API, REPORTS]
    assert same["scopes"] == ["admin", "read", "write"]
    with pytest.raises(eg.ExchangeError) as exc:
        guard.register_issuer(ISSUER, audiences=(API,), scopes=("read",))
    assert exc.value.code == "EXCHANGE_REGISTRATION_CONFLICT"
    with pytest.raises(eg.ExchangeError) as exc:
        guard.register_actor("svc-a", audiences=(API,), scopes=("read",))
    assert exc.value.code == "EXCHANGE_REGISTRATION_CONFLICT"

    with pytest.raises(eg.ExchangeError) as exc:
        guard.issue_subject_token(issuer="https://evil.example.com", subject="alice",
                                  audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_UNTRUSTED_ISSUER"
    with pytest.raises(eg.ExchangeError) as exc:
        subject(guard, audience="https://other.example.com")
    assert exc.value.code == "EXCHANGE_AUDIENCE_NOT_ALLOWED"

    extra = make()
    extra.register_issuer("https://idp2.example.com",
                          audiences=("https://extra.example.com",), scopes=("read",))
    foreign = extra.issue_subject_token(issuer="https://idp2.example.com", subject="bob",
                                        audience="https://extra.example.com",
                                        scopes=("read",), now=NOW)
    with pytest.raises(eg.ExchangeError) as exc:
        extra.exchange(subject_token=foreign, audience="https://extra.example.com",
                       scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_AUDIENCE_NOT_ALLOWED"


def test_scope_escalation_and_empty_scope_are_refused():
    guard = make()
    token = subject(guard)
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=token, audience=API, scopes=(), now=NOW)
    assert exc.value.code == "EXCHANGE_SCOPE_EMPTY"
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=token, audience=API,
                       scopes=("read", "admin"), now=NOW)
    assert exc.value.code == "EXCHANGE_SCOPE_ESCALATION"

    admin_token = subject(guard, subject="root", scopes=("admin",))
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=admin_token, audience=API,
                       scopes=("admin",), now=NOW)
    assert exc.value.code == "EXCHANGE_SCOPE_ESCALATION"
    assert guard.stats() == {"exchanged": 0, "rejected": 3, "replayed": 0, "revoked": 0}


def test_actor_scope_ceiling_and_chain_recording():
    guard = make()
    token = subject(guard)
    svc = actor(guard, "svc-a")
    out = guard.exchange(subject_token=token, actor_token=svc, audience=API,
                         scopes=("read", "write"), now=NOW)
    assert out["act"] == {"actor": "svc-a", "chain": ["svc-a"]}
    assert out["depth"] == 1

    forged = dict(svc)
    forged["actor"] = "svc-z"
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=subject(guard, subject="bob"), actor_token=forged,
                       audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_ACTOR_INVALID"

    tight = actor(guard, "svc-b", scopes=("read",))
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=subject(guard, subject="carol"), actor_token=tight,
                       audience=API, scopes=("read", "write"), now=NOW)
    assert exc.value.code == "EXCHANGE_SCOPE_ESCALATION"


def test_token_type_confusion_is_rejected():
    guard = make()
    svc = actor(guard, "svc-a", scopes=("read",))
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=svc, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_TYPE_INVALID"
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=subject(guard), actor_token=subject(guard, subject="x"),
                       audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_TYPE_INVALID"
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token={"nope": 1}, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_TYPE_INVALID"


def test_actor_audience_must_match_the_exchange_target():
    guard = make()
    with pytest.raises(eg.ExchangeError) as exc:
        guard.issue_actor_token("svc-a", audience=REPORTS, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_ACTOR_AUDIENCE_MISMATCH"
    with pytest.raises(eg.ExchangeError) as exc:
        guard.issue_actor_token("svc-z", audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_ACTOR_INVALID"

    svc = actor(guard, "svc-a", scopes=("read",))
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=subject(guard), actor_token=svc, audience=REPORTS,
                       scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_ACTOR_AUDIENCE_MISMATCH"


def test_delegation_depth_limit_does_not_consume_token():
    guard = make(max_delegation_depth=2)
    token = subject(guard)
    first = guard.exchange(subject_token=token, audience=API, scopes=("read",), now=NOW)
    second = guard.exchange(subject_token=first, audience=API, scopes=("read",), now=NOW)
    assert second["depth"] == 2
    for _ in range(2):
        with pytest.raises(eg.ExchangeError) as exc:
            guard.exchange(subject_token=second, audience=API, scopes=("read",), now=NOW)
        assert exc.value.code == "EXCHANGE_DEPTH_EXCEEDED"
    assert guard.stats()["exchanged"] == 2
    assert guard.stats()["replayed"] == 0


def test_expiry_not_before_and_actor_expiry():
    guard = make()
    short = subject(guard, subject="short", expires_at="2026-11-24T13:00:05+00:00")
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=short, audience=API, scopes=("read",),
                       now="2026-11-24T13:00:05+00:00")
    assert exc.value.code == "EXCHANGE_TOKEN_EXPIRED"
    assert guard.exchange(subject_token=short, audience=API, scopes=("read",),
                          now="2026-11-24T13:00:04+00:00")["token_type"] == "access"

    future = subject(guard, subject="future", not_before="2026-11-24T13:05:00+00:00")
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=future, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_NOT_YET_VALID"

    stale_actor = actor(guard, "svc-a", scopes=("read",),
                        expires_at="2026-11-24T13:00:01+00:00")
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=subject(guard, subject="late"), actor_token=stale_actor,
                       audience=API, scopes=("read",),
                       now="2026-11-24T13:00:02+00:00")
    assert exc.value.code == "EXCHANGE_ACTOR_INVALID"


def test_subject_token_can_be_consumed_exactly_once():
    guard = make()
    token = subject(guard)
    first = guard.exchange(subject_token=token, audience=API, scopes=("read",), now=NOW)
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=token, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_REPLAYED"
    assert guard.stats() == {"exchanged": 1, "rejected": 1, "replayed": 1, "revoked": 0}

    guard.exchange(subject_token=first, audience=API, scopes=("read",), now=NOW)
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=first, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_REPLAYED"
    assert guard.stats() == {"exchanged": 2, "rejected": 2, "replayed": 2, "revoked": 0}


def test_derived_lifetime_is_capped_by_subject_expiry():
    guard = make(ttl=300)
    token = subject(guard, expires_at="2026-11-24T13:01:40+00:00")
    out = guard.exchange(subject_token=token, audience=API, scopes=("read",), now=NOW)
    assert out["expires_in"] == 100
    assert out["exp"] == "2026-11-24T13:01:40+00:00"

    short_guard = make(ttl=30)
    short_out = short_guard.exchange(subject_token=subject(short_guard), audience=API,
                                     scopes=("read",), now=NOW)
    assert short_out["expires_in"] == 30
    assert short_out["exp"] == "2026-11-24T13:00:30+00:00"


def test_revoked_jti_blocks_exchange_idempotently():
    guard = make()
    token = subject(guard)
    guard.revoke(token["jti"])
    guard.revoke(token["jti"])
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=token, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_REVOKED"
    assert guard.stats() == {"exchanged": 0, "rejected": 1, "replayed": 0, "revoked": 1}

    other = subject(guard, subject="bob")
    derived = guard.exchange(subject_token=other, audience=API, scopes=("read",), now=NOW)
    guard.revoke(derived["jti"])
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=derived, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_REVOKED"
    assert guard.stats()["revoked"] == 2


def test_jti_store_capacity_and_purge():
    guard = make(max_jti=2)
    first = subject(guard, subject="a")
    second = subject(guard, subject="b")
    long_lived = subject(guard, subject="c", expires_at="2026-11-24T13:30:00+00:00")
    guard.exchange(subject_token=first, audience=API, scopes=("read",), now=NOW)
    guard.exchange(subject_token=second, audience=API, scopes=("read",), now=NOW)
    with pytest.raises(eg.ExchangeError) as exc:
        guard.exchange(subject_token=long_lived, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_JTI_FULL"
    assert guard.stats()["exchanged"] == 2

    assert guard.purge(now="2026-11-24T13:04:59+00:00") == 0
    assert guard.purge(now="2026-11-24T13:05:00+00:00") == 2
    out = guard.exchange(subject_token=long_lived, audience=API, scopes=("read",),
                         now="2026-11-24T13:05:01+00:00")
    assert out["token_type"] == "access"


def test_introspection_reports_state_without_side_effects():
    guard = make()
    token = subject(guard)
    active = guard.introspect(token, now=NOW)
    assert active["active"] is True
    assert active["subject"] == "alice"
    assert active["audience"] == API
    assert active["scopes"] == ["read", "write"]
    assert active["depth"] == 0
    assert active["expires_in"] == 300

    used = subject(guard, subject="bob")
    guard.exchange(subject_token=used, audience=API, scopes=("read",), now=NOW)
    info = guard.introspect(used, now=NOW)
    assert info["active"] is False
    assert info["reason"] == "replayed"

    revoked = subject(guard, subject="carol")
    guard.revoke(revoked["jti"])
    info = guard.introspect(revoked, now=NOW)
    assert info["active"] is False
    assert info["reason"] == "revoked"

    info = guard.introspect(token, now="2026-11-24T13:05:00+00:00")
    assert info["active"] is False
    assert info["reason"] == "expired"

    future = subject(guard, subject="dave", not_before="2026-11-24T13:02:00+00:00")
    info = guard.introspect(future, now=NOW)
    assert info["active"] is False
    assert info["reason"] == "not_yet_valid"

    with pytest.raises(eg.ExchangeError) as exc:
        guard.introspect({"token_type": "weird", "jti": "x"}, now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_TYPE_INVALID"
    assert guard.stats() == {"exchanged": 1, "rejected": 0, "replayed": 0, "revoked": 1}


def test_snapshot_restore_keeps_replay_and_revocation_state():
    guard = make()
    used = subject(guard, subject="used")
    guard.exchange(subject_token=used, audience=API, scopes=("read",), now=NOW)
    revoked = subject(guard, subject="revoked")
    guard.revoke(revoked["jti"])
    state = guard.snapshot()
    pristine = copy.deepcopy(state)
    state["used"].clear()
    assert guard.stats()["exchanged"] == 1

    restored = make()
    restored.restore(pristine)
    with pytest.raises(eg.ExchangeError) as exc:
        restored.exchange(subject_token=used, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_REPLAYED"
    with pytest.raises(eg.ExchangeError) as exc:
        restored.exchange(subject_token=revoked, audience=API, scopes=("read",), now=NOW)
    assert exc.value.code == "EXCHANGE_TOKEN_REVOKED"

    fresh = subject(guard, subject="fresh")
    assert restored.exchange(subject_token=fresh, audience=API, scopes=("read",),
                             now=NOW)["token_type"] == "access"
    assert restored.stats() == {"exchanged": 2, "rejected": 2, "replayed": 1, "revoked": 1}

    bad_states = [
        None,
        {},
        {"version": 2, "issuers": {}, "actors": {}, "resources": {},
         "used": {}, "revoked": [], "stats": {}},
        {"version": 1, "issuers": [], "actors": {}, "resources": {},
         "used": {}, "revoked": [], "stats": {}},
        {"version": 1, "issuers": {}, "actors": {}, "resources": {},
         "used": {}, "revoked": [], "stats": {"exchanged": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(eg.ExchangeError) as exc:
            restored.restore(bad)
        assert exc.value.code == "EXCHANGE_SNAPSHOT_INVALID"
    assert restored.exchange(subject_token=subject(guard, subject="after"), audience=API,
                             scopes=("read",), now=NOW)["token_type"] == "access"


def test_concurrent_exchange_yields_single_success():
    guard = make()
    token = subject(guard)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            guard.exchange(subject_token=token, audience=API, scopes=("read",), now=NOW)
            row = {"status": "ok"}
        except Exception as exc:  # pragma: no cover - surfaced through assertions
            row = {"status": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["error"] * 7 + ["ok"]
    assert [row["code"] for row in results if row["status"] == "error"] == \
        ["EXCHANGE_TOKEN_REPLAYED"] * 7
    assert guard.stats() == {"exchanged": 1, "rejected": 7, "replayed": 7, "revoked": 0}
