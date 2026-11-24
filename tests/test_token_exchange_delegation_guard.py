import pytest

import token_exchange_delegation_guard as tx

NOW = "2026-11-23T10:00:00+00:00"
STAT_ZERO = {"issued": 0, "exchanged": 0, "deduped": 0,
             "revoked": 0, "rejected": 0, "validated": 0}


def make_guard(**kwargs):
    guard = tx.TokenExchangeGuard(**kwargs)
    guard.register_client("portal", allowed_audiences=("api", "billing"))
    guard.register_audience("api", allowed_scopes=("read", "write"))
    guard.register_audience("billing", allowed_scopes=("read", "invoice"))
    guard.register_audience("admin", allowed_scopes=("root",))
    return guard


def root(guard, *, subject="alice", scopes=("read", "write"), audience="api",
         ttl=600, now=NOW):
    return guard.issue_root(subject, scopes=scopes, audience=audience,
                            ttl=ttl, now=now)


def principal(guard, subject, *, audience="api", scopes=("read",), ttl=600):
    return guard.issue_root(subject, scopes=scopes, audience=audience,
                            ttl=ttl, now=NOW)


def test_root_token_and_introspection():
    guard = make_guard()
    issued = root(guard, scopes=("write", "read"))
    assert issued["subject"] == "alice"
    assert issued["actor_chain"] == []
    assert issued["scopes"] == ["read", "write"]
    assert issued["audience"] == "api"
    assert issued["generation"] == 0
    assert issued["expires_at"] == "2026-11-23T10:10:00+00:00"

    info = guard.introspect(issued["token"], now=NOW)
    assert info["active"] is True
    assert info["subject"] == "alice"
    assert info["actor_chain"] == []
    assert info["generation"] == 0
    assert guard.chain(issued["token"]) == ["alice"]
    assert guard.stats() == {"issued": 1, "exchanged": 0, "deduped": 0,
                             "revoked": 0, "rejected": 0, "validated": 0}


def test_exchange_adds_actor_and_builds_two_hop_chain():
    guard = make_guard()
    subject = root(guard)
    svc_a = principal(guard, "svc-a")
    first = guard.exchange(subject["token"], client_id="portal",
                           audience="billing", scopes=("read",),
                           actor_token=svc_a["token"], now=NOW)
    assert first["subject"] == "alice"
    assert first["actor_chain"] == ["svc-a"]
    assert first["scopes"] == ["read"]
    assert first["audience"] == "billing"
    assert first["generation"] == 1
    assert first["parent"] == subject["token"]
    assert first["deduped"] is False
    assert guard.chain(first["token"]) == ["alice", "svc-a"]

    svc_b = principal(guard, "svc-b")
    second = guard.exchange(first["token"], client_id="portal",
                            audience="api", scopes=("read",),
                            actor_token=svc_b["token"], now=NOW)
    assert second["actor_chain"] == ["svc-a", "svc-b"]
    assert second["generation"] == 2
    assert second["parent"] == first["token"]
    assert guard.chain(second["token"]) == ["alice", "svc-a", "svc-b"]
    assert guard.introspect(second["token"], now=NOW)["active"] is True
    assert guard.stats()["issued"] == 3
    assert guard.stats()["exchanged"] == 2


def test_unknown_tokens_clients_and_audiences():
    guard = make_guard()
    issued = root(guard)
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange("no-such-token", client_id="portal", audience="api", now=NOW)
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.validate("no-such-token", audience="api", now=NOW)
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.introspect("no-such-token", now=NOW)
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.chain("no-such-token")
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="ghost", audience="api", now=NOW)
    assert exc.value.code == "TX_UNKNOWN_CLIENT"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="ghost", now=NOW)
    assert exc.value.code == "TX_UNKNOWN_AUDIENCE"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.revoke("no-such-token")
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    assert guard.stats()["rejected"] == 7


def test_expiry_boundary_and_child_never_outlives_parent():
    guard = make_guard()
    short = root(guard, ttl=60)
    assert guard.introspect(short["token"], now="2026-11-23T10:00:59+00:00")["active"] is True
    assert guard.introspect(short["token"], now="2026-11-23T10:01:00+00:00")["active"] is False

    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(short["token"], client_id="portal", audience="api", now="2026-11-23T10:01:00+00:00")
    assert exc.value.code == "TX_TOKEN_EXPIRED"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.validate(short["token"], audience="api", now="2026-11-23T10:01:00+00:00")
    assert exc.value.code == "TX_TOKEN_EXPIRED"

    another = root(guard, ttl=60)
    child = guard.exchange(another["token"], client_id="portal", audience="api",
                           scopes=("read",), now="2026-11-23T10:00:30+00:00")
    assert child["expires_at"] == "2026-11-23T10:01:00+00:00"
    assert child["generation"] == 1
    assert guard.validate(child["token"], audience="api", required_scope="read",
                          now="2026-11-23T10:00:59+00:00")["active"] is True
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.validate(child["token"], audience="api", now="2026-11-23T10:01:00+00:00")
    assert exc.value.code == "TX_TOKEN_EXPIRED"


def test_client_audience_restrictions_and_audience_scope_limits():
    guard = make_guard()
    issued = root(guard)
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="admin", now=NOW)
    assert exc.value.code == "TX_AUDIENCE_NOT_ALLOWED"

    guard.register_client("console", allowed_audiences=("api", "admin"))
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="console", audience="admin",
                       scopes=("read",), now=NOW)
    assert exc.value.code == "TX_SCOPE_NOT_ALLOWED"

    narrowed = guard.exchange(issued["token"], client_id="console", audience="api",
                              scopes=("write",), now=NOW)
    assert narrowed["audience"] == "api"
    assert narrowed["scopes"] == ["write"]

    guard.register_client("admin-client", allowed_audiences=("admin",))
    admin_root = root(guard, scopes=("root",), audience="admin")
    admin_child = guard.exchange(admin_root["token"], client_id="admin-client",
                                 audience="admin", scopes=("root",), now=NOW)
    assert admin_child["audience"] == "admin"
    assert admin_child["scopes"] == ["root"]


def test_scope_escalation_is_refused_and_downscoping_sticks():
    guard = make_guard()
    issued = root(guard)
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="billing",
                       scopes=("read", "invoice"), now=NOW)
    assert exc.value.code == "TX_SCOPE_ESCALATION"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="api",
                       scopes=(), now=NOW)
    assert exc.value.code == "TX_SCOPE_ESCALATION"

    child = guard.exchange(issued["token"], client_id="portal", audience="api",
                           scopes=("read",), now=NOW)
    assert child["scopes"] == ["read"]
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(child["token"], client_id="portal", audience="api",
                       scopes=("write",), now=NOW)
    assert exc.value.code == "TX_SCOPE_ESCALATION"
    assert guard.stats()["exchanged"] == 1
    assert guard.stats()["rejected"] == 3


def test_delegation_depth_global_and_per_audience():
    guard = make_guard(max_delegation_depth=2)
    issued = root(guard)
    actor_a = principal(guard, "svc-a")
    actor_b = principal(guard, "svc-b")
    actor_c = principal(guard, "svc-c")

    first = guard.exchange(issued["token"], client_id="portal", audience="billing",
                           scopes=("read",), actor_token=actor_a["token"], now=NOW)
    second = guard.exchange(first["token"], client_id="portal", audience="api",
                            scopes=("read",), actor_token=actor_b["token"], now=NOW)
    assert second["generation"] == 2
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(second["token"], client_id="portal", audience="billing",
                       scopes=("read",), actor_token=actor_c["token"], now=NOW)
    assert exc.value.code == "TX_DEPTH_EXCEEDED"

    guard.register_audience("tight", max_delegation_depth=1,
                            allowed_scopes=("read",))
    guard.register_client("tight-client", allowed_audiences=("tight",))
    tight_root = root(guard, scopes=("read",))
    tight_first = guard.exchange(tight_root["token"], client_id="tight-client",
                                 audience="tight", scopes=("read",), now=NOW)
    assert tight_first["generation"] == 1
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(tight_first["token"], client_id="tight-client",
                       audience="tight", scopes=("read",), now=NOW)
    assert exc.value.code == "TX_DEPTH_EXCEEDED"
    assert guard.stats()["rejected"] == 2


def test_actor_token_must_be_valid_and_distinct():
    guard = make_guard()
    issued = root(guard)

    expired_actor = principal(guard, "svc-old", ttl=60)
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="api",
                       actor_token=expired_actor["token"],
                       now="2026-11-23T10:01:00+00:00")
    assert exc.value.code == "TX_ACTOR_INVALID"

    revoked_actor = principal(guard, "svc-revoked")
    guard.revoke(revoked_actor["token"])
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="api",
                       actor_token=revoked_actor["token"], now=NOW)
    assert exc.value.code == "TX_ACTOR_INVALID"

    self_actor = principal(guard, "alice")
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="api",
                       actor_token=self_actor["token"], now=NOW)
    assert exc.value.code == "TX_ACTOR_SELF"

    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="api",
                       actor_token="no-such-token", now=NOW)
    assert exc.value.code == "TX_ACTOR_INVALID"
    assert guard.stats()["rejected"] == 4


def test_validate_checks_audience_scope_and_reports_chain():
    guard = make_guard()
    issued = root(guard)
    actor = principal(guard, "svc-a")
    child = guard.exchange(issued["token"], client_id="portal", audience="billing",
                           scopes=("read",), actor_token=actor["token"], now=NOW)

    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.validate(child["token"], audience="api", now=NOW)
    assert exc.value.code == "TX_AUDIENCE_MISMATCH"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.validate(child["token"], audience="billing", required_scope="write", now=NOW)
    assert exc.value.code == "TX_SCOPE_MISSING"

    ok = guard.validate(child["token"], audience="billing", required_scope="read", now=NOW)
    assert ok["active"] is True
    assert ok["subject"] == "alice"
    assert ok["actor_chain"] == ["svc-a"]
    assert ok["scopes"] == ["read"]
    assert ok["audience"] == "billing"
    assert ok["generation"] == 1
    assert ok["expires_at"] == child["expires_at"]

    root_ok = guard.validate(issued["token"], audience="api", required_scope="read", now=NOW)
    assert root_ok["actor_chain"] == []
    assert guard.stats()["validated"] == 2
    assert guard.stats()["rejected"] == 2


def test_revoke_cascades_to_all_descendants():
    guard = make_guard()
    issued = root(guard)
    actor_a = principal(guard, "svc-a")
    actor_b = principal(guard, "svc-b")
    first = guard.exchange(issued["token"], client_id="portal", audience="billing",
                           scopes=("read",), actor_token=actor_a["token"], now=NOW)
    second = guard.exchange(first["token"], client_id="portal", audience="api",
                            scopes=("read",), actor_token=actor_b["token"], now=NOW)

    assert guard.revoke(first["token"]) == 2
    assert guard.introspect(first["token"], now=NOW)["active"] is False
    assert guard.introspect(second["token"], now=NOW)["revoked"] is True
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.validate(second["token"], audience="api", now=NOW)
    assert exc.value.code == "TX_TOKEN_REVOKED"
    assert guard.introspect(issued["token"], now=NOW)["active"] is True

    assert guard.revoke(first["token"]) == 0
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.revoke("no-such-token")
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    assert guard.stats()["revoked"] == 2


def test_revoke_principal_offboarding():
    guard = make_guard()
    svc_root = principal(guard, "svc-a")
    alice_root = root(guard)
    child = guard.exchange(alice_root["token"], client_id="portal", audience="billing",
                           scopes=("read",), actor_token=svc_root["token"], now=NOW)
    svc_b = principal(guard, "svc-b")
    grand = guard.exchange(child["token"], client_id="portal", audience="api",
                           scopes=("read",), actor_token=svc_b["token"], now=NOW)
    bob_root = root(guard, subject="bob", scopes=("read",))

    assert guard.revoke_principal("svc-a") == 3
    assert guard.introspect(svc_root["token"], now=NOW)["active"] is False
    assert guard.introspect(child["token"], now=NOW)["active"] is False
    assert guard.introspect(grand["token"], now=NOW)["active"] is False
    assert guard.introspect(alice_root["token"], now=NOW)["active"] is True
    assert guard.introspect(bob_root["token"], now=NOW)["active"] is True
    assert guard.revoke_principal("svc-a") == 0
    assert guard.revoke_principal("nobody") == 0
    assert guard.stats()["revoked"] == 3


def test_dedupe_window_returns_existing_exchange():
    guard = make_guard(dedupe_window=30)
    issued = root(guard)
    first = guard.exchange(issued["token"], client_id="portal", audience="api",
                           scopes=("read",), now=NOW)
    again = guard.exchange(issued["token"], client_id="portal", audience="api",
                           scopes=("read",), now="2026-11-23T10:00:10+00:00")
    assert again["deduped"] is True
    assert again["token"] == first["token"]
    assert again["generation"] == 1

    later = guard.exchange(issued["token"], client_id="portal", audience="api",
                           scopes=("read",), now="2026-11-23T10:00:31+00:00")
    assert later["deduped"] is False
    assert later["token"] != first["token"]
    assert guard.stats() == {"issued": 1, "exchanged": 2, "deduped": 1,
                             "revoked": 0, "rejected": 0, "validated": 0}


def test_fanout_limit_and_purge():
    guard = make_guard(max_children_per_token=2)
    issued = root(guard, ttl=60)
    first = guard.exchange(issued["token"], client_id="portal", audience="api",
                           scopes=("read",), now=NOW)
    second = guard.exchange(issued["token"], client_id="portal", audience="api",
                            scopes=("write",), now=NOW)
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.exchange(issued["token"], client_id="portal", audience="billing",
                       scopes=("read",), now=NOW)
    assert exc.value.code == "TX_FANOUT_EXCEEDED"

    long_lived = root(guard, subject="bob", scopes=("read",))
    assert guard.purge(now="2026-11-23T10:01:00+00:00") == 3
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.introspect(issued["token"], now=NOW)
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    with pytest.raises(tx.TokenExchangeError) as exc:
        guard.introspect(first["token"], now=NOW)
    assert exc.value.code == "TX_UNKNOWN_TOKEN"
    assert guard.introspect(long_lived["token"], now=NOW)["active"] is True


def test_snapshot_restore_and_invalid_state():
    guard = make_guard()
    issued = root(guard)
    actor = principal(guard, "svc-a")
    child = guard.exchange(issued["token"], client_id="portal", audience="billing",
                           scopes=("read",), actor_token=actor["token"], now=NOW)
    state = guard.snapshot()
    assert state["stats"]["exchanged"] == 1

    guard.exchange(issued["token"], client_id="portal", audience="api",
                   scopes=("write",), now=NOW)
    assert state["stats"]["exchanged"] == 1

    restored = tx.TokenExchangeGuard()
    restored.restore(state)
    assert restored.stats()["issued"] == 2
    assert restored.stats()["exchanged"] == 1
    assert restored.introspect(child["token"], now=NOW)["active"] is True
    assert restored.chain(child["token"]) == ["alice", "svc-a"]
    assert restored.validate(child["token"], audience="billing",
                             required_scope="read", now=NOW)["active"] is True

    with pytest.raises(tx.TokenExchangeError) as exc:
        restored.restore({"version": 2})
    assert exc.value.code == "TX_SNAPSHOT_INVALID"
    assert restored.validate(child["token"], audience="billing", now=NOW)["active"] is True
    assert restored.stats()["validated"] == 2


def test_concurrent_identical_exchange_dedupes_to_one_token():
    import threading

    guard = make_guard(dedupe_window=30)
    issued = root(guard)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        out = guard.exchange(issued["token"], client_id="portal", audience="api",
                             scopes=("read",), now=NOW)
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len({row["token"] for row in results}) == 1
    assert sum(1 for row in results if row["deduped"] is True) == 7
    assert sum(1 for row in results if row["deduped"] is False) == 1
    assert guard.stats() == {"issued": 1, "exchanged": 1, "deduped": 7,
                             "revoked": 0, "rejected": 0, "validated": 0}
