import pytest

import refresh_token_rotation_guard as rrg

NOW = "2026-11-23T10:00:00+00:00"
SOON = "2026-11-23T10:00:10+00:00"
LATER = "2026-11-23T10:00:31+00:00"
STAT_ZERO = {"rotated": 0, "grace_hits": 0, "reused": 0, "revoked": 0, "rejected": 0}


def make_guard(**kwargs):
    guard = rrg.RefreshRotationGuard(**kwargs)
    guard.register_client("web", scopes=("read", "write", "admin"))
    return guard


def grant(guard, *, scopes=("write", "read"), subject="alice", now=NOW):
    return guard.issue("web", subject=subject, scopes=scopes, now=now)


def test_issue_and_rotate_advances_generation():
    guard = make_guard()
    first = grant(guard)
    assert first["generation"] == 0
    assert first["scopes"] == ["read", "write"]
    assert first["subject"] == "alice"
    assert first["expires_at"] == "2026-11-23T11:00:00+00:00"

    rotated = guard.rotate(first["refresh_token"], client_id="web", now=NOW)
    assert rotated["generation"] == 1
    assert rotated["family_id"] == first["family_id"]
    assert rotated["refresh_token"] != first["refresh_token"]
    assert rotated["grace_hit"] is False
    assert rotated["expires_at"] == "2026-11-23T11:00:00+00:00"

    assert guard.inspect(first["refresh_token"])["status"] == "consumed"
    chain = guard.family_tokens(first["family_id"])
    assert [row["generation"] for row in chain] == [0, 1]
    assert [row["status"] for row in chain] == ["consumed", "active"]
    assert guard.stats() == {"rotated": 1, "grace_hits": 0, "reused": 0,
                             "revoked": 0, "rejected": 0}


def test_reuse_after_grace_revokes_the_whole_family():
    guard = make_guard()
    first = grant(guard)
    second = guard.rotate(first["refresh_token"], client_id="web", now=NOW)

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(first["refresh_token"], client_id="web", now=LATER)
    assert exc.value.code == "RT_REUSE_DETECTED"
    assert guard.stats()["reused"] == 1

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(second["refresh_token"], client_id="web", now=LATER)
    assert exc.value.code == "RT_FAMILY_REVOKED"
    assert guard.inspect(second["refresh_token"])["status"] == "revoked"
    assert [row["status"] for row in guard.family_tokens(first["family_id"])] == ["consumed", "revoked"]


def test_repeat_rotation_inside_grace_returns_same_successor():
    guard = make_guard(grace_seconds=30)
    first = grant(guard)
    second = guard.rotate(first["refresh_token"], client_id="web", now=NOW)

    again = guard.rotate(first["refresh_token"], client_id="web", now=SOON)
    assert again["grace_hit"] is True
    assert again["refresh_token"] == second["refresh_token"]
    assert again["generation"] == 1

    third = guard.rotate(second["refresh_token"], client_id="web", now=SOON)
    assert third["generation"] == 2
    assert guard.stats() == {"rotated": 2, "grace_hits": 1, "reused": 0,
                             "revoked": 0, "rejected": 0}


def test_unknown_token_and_client_mismatch_are_distinct():
    guard = make_guard()
    guard.register_client("mobile", scopes=("read",))
    first = grant(guard)

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate("no-such-token", client_id="web", now=NOW)
    assert exc.value.code == "RT_UNKNOWN"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(first["refresh_token"], client_id="mobile", now=NOW)
    assert exc.value.code == "RT_CLIENT_MISMATCH"
    assert guard.inspect(first["refresh_token"])["status"] == "active"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.inspect("no-such-token")
    assert exc.value.code == "RT_UNKNOWN"


def test_scope_escalation_is_refused_and_downscoping_sticks():
    guard = make_guard()
    first = grant(guard)

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(first["refresh_token"], client_id="web",
                     scopes=("read", "admin"), now=NOW)
    assert exc.value.code == "RT_SCOPE_ESCALATION"

    narrowed = guard.rotate(first["refresh_token"], client_id="web",
                            scopes=("read",), now=NOW)
    assert narrowed["scopes"] == ["read"]

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(narrowed["refresh_token"], client_id="web",
                     scopes=("write",), now=NOW)
    assert exc.value.code == "RT_SCOPE_ESCALATION"
    assert guard.stats()["rejected"] == 2
    assert guard.stats()["rotated"] == 1


def test_expiry_uses_exact_instant_and_purge_drops_tokens():
    guard = rrg.RefreshRotationGuard()
    guard.register_client("short", scopes=("read",), refresh_ttl=60)
    first = guard.issue("short", subject="alice", scopes=("read",), now=NOW)
    assert first["expires_at"] == "2026-11-23T10:01:00+00:00"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(first["refresh_token"], client_id="short",
                     now="2026-11-23T10:01:00+00:00")
    assert exc.value.code == "RT_EXPIRED"

    assert guard.purge(now="2026-11-23T10:01:00+00:00") == 1
    assert guard.purge(now="2026-11-23T10:01:00+00:00") == 0
    with pytest.raises(rrg.RefreshError) as exc:
        guard.inspect(first["refresh_token"])
    assert exc.value.code == "RT_UNKNOWN"


def test_chain_limit_revokes_family():
    guard = make_guard(max_chain=2)
    first = grant(guard)
    second = guard.rotate(first["refresh_token"], client_id="web", now=NOW)
    third = guard.rotate(second["refresh_token"], client_id="web", now=NOW)
    assert third["generation"] == 2

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(third["refresh_token"], client_id="web", now=NOW)
    assert exc.value.code == "RT_CHAIN_LIMIT"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(third["refresh_token"], client_id="web", now=NOW)
    assert exc.value.code == "RT_FAMILY_REVOKED"
    assert guard.stats()["rotated"] == 2
    assert guard.stats()["rejected"] == 2


def test_client_registration_conflicts_and_unknown_clients():
    guard = rrg.RefreshRotationGuard()
    payload = guard.register_client("web", scopes=("write", "read"), refresh_ttl=120)
    assert payload["client_id"] == "web"
    assert payload["scopes"] == ["read", "write"]
    assert payload["refresh_ttl"] == 120

    again = guard.register_client("web", scopes=("read", "write"), refresh_ttl=120)
    assert again == payload

    with pytest.raises(rrg.RefreshError) as exc:
        guard.register_client("web", scopes=("read",), refresh_ttl=120)
    assert exc.value.code == "RT_CLIENT_CONFLICT"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.register_client("empty", scopes=())
    assert exc.value.code == "RT_INVALID_CLIENT"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.issue("ghost", subject="alice", scopes=("read",), now=NOW)
    assert exc.value.code == "RT_UNKNOWN_CLIENT"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.issue("web", subject="alice", scopes=("admin",), now=NOW)
    assert exc.value.code == "RT_SCOPE_NOT_ALLOWED"


def test_revoke_family_is_idempotent_and_blocks_descendants():
    guard = make_guard()
    first = grant(guard)
    second = guard.rotate(first["refresh_token"], client_id="web", now=NOW)

    assert guard.revoke_family(first["family_id"]) == 1
    assert guard.revoke_family(first["family_id"]) == 0

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(second["refresh_token"], client_id="web", now=NOW)
    assert exc.value.code == "RT_FAMILY_REVOKED"
    assert [row["status"] for row in guard.family_tokens(first["family_id"])] == ["consumed", "revoked"]

    with pytest.raises(rrg.RefreshError) as exc:
        guard.revoke_family("family-missing")
    assert exc.value.code == "RT_UNKNOWN_FAMILY"
    assert guard.stats()["revoked"] == 1


def test_single_token_revocation_does_not_touch_the_family():
    guard = make_guard()
    first = grant(guard)
    guard.revoke_token(first["refresh_token"], reason="lost-device")
    assert guard.inspect(first["refresh_token"])["status"] == "revoked"

    with pytest.raises(rrg.RefreshError) as exc:
        guard.rotate(first["refresh_token"], client_id="web", now=NOW)
    assert exc.value.code == "RT_REVOKED"

    guard.revoke_token(first["refresh_token"])
    assert guard.stats()["revoked"] == 1
    with pytest.raises(rrg.RefreshError) as exc:
        guard.revoke_token("no-such-token")
    assert exc.value.code == "RT_UNKNOWN"


def test_purge_only_removes_tokens_that_have_expired():
    guard = rrg.RefreshRotationGuard()
    guard.register_client("short", scopes=("read",), refresh_ttl=60)
    guard.register_client("long", scopes=("read",), refresh_ttl=600)
    old = guard.issue("short", subject="alice", scopes=("read",), now=NOW)
    live = guard.issue("long", subject="bob", scopes=("read",), now=NOW)

    assert guard.purge(now="2026-11-23T10:00:59+00:00") == 0
    assert guard.purge(now="2026-11-23T10:01:00+00:00") == 1
    assert guard.inspect(live["refresh_token"])["status"] == "active"
    assert guard.family_tokens(old["family_id"]) == []

    rotated = guard.rotate(live["refresh_token"], client_id="long",
                           now="2026-11-23T10:01:00+00:00")
    assert rotated["generation"] == 1
    assert rotated["expires_at"] == "2026-11-23T10:11:00+00:00"


def test_snapshot_and_restore_carry_families_and_counters():
    guard = make_guard()
    first = grant(guard)
    second = guard.rotate(first["refresh_token"], client_id="web", now=NOW)
    state = guard.snapshot()

    guard.rotate(second["refresh_token"], client_id="web", now=NOW)
    assert state["stats"]["rotated"] == 1

    restored = rrg.RefreshRotationGuard()
    restored.restore(state)
    assert restored.stats() == {"rotated": 1, "grace_hits": 0, "reused": 0,
                                "revoked": 0, "rejected": 0}
    assert restored.inspect(first["refresh_token"])["status"] == "consumed"

    third = restored.rotate(second["refresh_token"], client_id="web", now=NOW)
    assert third["generation"] == 2
    assert restored.stats()["rotated"] == 2

    with pytest.raises(rrg.RefreshError) as exc:
        restored.rotate(second["refresh_token"], client_id="web", now=LATER)
    assert exc.value.code == "RT_REUSE_DETECTED"
    assert restored.stats()["reused"] == 1


def test_corrupt_snapshot_is_rejected_without_changing_state():
    guard = make_guard()
    first = grant(guard)
    good = guard.snapshot()
    stats = {"rotated": 0, "grace_hits": 0, "reused": 0, "revoked": 0, "rejected": 0}
    bad_states = [
        None,
        {},
        {"version": 2, "config": {"grace_seconds": 30, "max_chain": 50},
         "clients": {}, "families": {}, "tokens": {}, "stats": stats},
        {"version": 1, "config": {"grace_seconds": "thirty", "max_chain": 50},
         "clients": {}, "families": {}, "tokens": {}, "stats": stats},
        {"version": 1, "config": {"grace_seconds": 30, "max_chain": 50},
         "clients": [], "families": {}, "tokens": {}, "stats": stats},
        {"version": 1, "config": {"grace_seconds": 30, "max_chain": 50},
         "clients": {}, "families": {}, "tokens": {}, "stats": {"rotated": 0}},
        dict(good, version=1, tokens="not-a-map"),
    ]
    for bad in bad_states:
        with pytest.raises(rrg.RefreshError) as exc:
            guard.restore(bad)
        assert exc.value.code == "RT_SNAPSHOT_INVALID"

    rotated = guard.rotate(first["refresh_token"], client_id="web", now=NOW)
    assert rotated["generation"] == 1
    assert guard.stats()["rotated"] == 1


def test_stats_and_reset_accounting():
    guard = make_guard()
    first = grant(guard)
    guard.rotate(first["refresh_token"], client_id="web", now=NOW)
    guard.rotate(first["refresh_token"], client_id="web", now=SOON)
    with pytest.raises(rrg.RefreshError):
        guard.rotate(first["refresh_token"], client_id="web", now=LATER)
    with pytest.raises(rrg.RefreshError):
        guard.rotate("no-such-token", client_id="web", now=NOW)
    with pytest.raises(rrg.RefreshError):
        guard.issue("ghost", subject="alice", scopes=("read",), now=NOW)

    assert guard.stats() == {"rotated": 1, "grace_hits": 1, "reused": 1,
                             "revoked": 0, "rejected": 2}
    guard.reset_stats()
    assert guard.stats() == STAT_ZERO


def test_concurrent_rotation_is_single_use_without_grace():
    import threading

    guard = make_guard(grace_seconds=0)
    first = grant(guard)
    barrier = threading.Barrier(8)
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.rotate(first["refresh_token"], client_id="web", now=NOW)
        except rrg.RefreshError as exc:
            with lock:
                errors.append(exc.code)
        else:
            with lock:
                results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert results[0]["generation"] == 1
    assert errors == ["RT_REUSE_DETECTED"] * 7
    assert guard.stats() == {"rotated": 1, "grace_hits": 0, "reused": 7,
                             "revoked": 0, "rejected": 0}
    assert guard.inspect(results[0]["refresh_token"])["status"] == "revoked"
