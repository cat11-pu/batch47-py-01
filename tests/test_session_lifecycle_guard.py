import copy
import threading

import pytest

import session_lifecycle_guard as sg

NOW = "2026-11-24T12:00:00+00:00"


def make(**kwargs):
    return sg.SessionLifecycleGuard(**kwargs)


def login(guard, user_id="u1", device_id="mac-1", ip="10.0.0.5", **overrides):
    args = {"user_id": user_id, "device_id": device_id, "ip": ip, "now": NOW}
    args.update(overrides)
    return guard.create_session(**args)


def test_create_session_returns_metadata_and_distinct_ids():
    guard = make()
    first = login(guard)
    assert first["session_id"].startswith("sess_")
    assert len(first["session_id"]) >= 24
    assert first["user_id"] == "u1"
    assert first["auth_level"] == 1
    assert first["created_at"] == NOW
    assert first["expires_at"] == "2026-11-24T20:00:00+00:00"
    assert first["idle_expires_at"] == "2026-11-24T12:15:00+00:00"

    second = login(guard, user_id="u2", device_id="pc-2", ip="10.0.0.6")
    assert second["session_id"] != first["session_id"]
    assert first["session_id"] not in second["session_id"]

    out = guard.validate(first["session_id"], device_id="mac-1", ip="10.0.0.5", now=NOW)
    assert out["status"] == "ok"
    assert out["user_id"] == "u1"
    assert out["auth_level"] == 1
    assert out["session_id"] == first["session_id"]
    assert guard.stats() == {"created": 2, "validated": 1, "rejected": 0,
                             "revoked": 0, "rotated": 0}


def test_rotation_replaces_id_and_keeps_absolute_expiry():
    guard = make()
    session = login(guard)
    rotated = guard.rotate(session["session_id"], now="2026-11-24T12:10:00+00:00")
    assert rotated["session_id"] != session["session_id"]
    assert rotated["created_at"] == session["created_at"]
    assert rotated["expires_at"] == session["expires_at"]
    assert rotated["idle_expires_at"] == "2026-11-24T12:25:00+00:00"

    with pytest.raises(sg.SessionError) as exc:
        guard.validate(session["session_id"], device_id="mac-1", ip="10.0.0.5",
                       now="2026-11-24T12:10:00+00:00")
    assert exc.value.code == "SESSION_UNKNOWN"
    out = guard.validate(rotated["session_id"], device_id="mac-1", ip="10.0.0.5",
                         now="2026-11-24T12:10:00+00:00")
    assert out["status"] == "ok"

    with pytest.raises(sg.SessionError) as exc:
        guard.rotate("sess-missing", now=NOW)
    assert exc.value.code == "SESSION_UNKNOWN"
    assert guard.stats()["rotated"] == 1


def test_idle_timeout_boundary_resets_with_activity():
    guard = make()
    active = login(guard)
    assert guard.validate(active["session_id"], device_id="mac-1",
                          now="2026-11-24T12:14:59+00:00")["status"] == "ok"
    with pytest.raises(sg.SessionError) as exc:
        guard.validate(active["session_id"], device_id="mac-1",
                       now="2026-11-24T12:29:59+00:00")
    assert exc.value.code == "SESSION_IDLE_TIMEOUT"

    stale = login(guard, user_id="u2")
    with pytest.raises(sg.SessionError) as exc:
        guard.validate(stale["session_id"], device_id="mac-1",
                       now="2026-11-24T12:15:00+00:00")
    assert exc.value.code == "SESSION_IDLE_TIMEOUT"
    assert guard.stats()["validated"] == 1
    assert guard.stats()["rejected"] == 2


def test_absolute_timeout_cannot_be_refreshed():
    guard = make(idle_timeout=7200, absolute_timeout=28800)
    session = login(guard)
    for hour in range(13, 20):
        stamp = "2026-11-24T%02d:00:00+00:00" % hour
        assert guard.validate(session["session_id"], device_id="mac-1",
                              now=stamp)["status"] == "ok"
    with pytest.raises(sg.SessionError) as exc:
        guard.validate(session["session_id"], device_id="mac-1",
                       now="2026-11-24T20:00:00+00:00")
    assert exc.value.code == "SESSION_ABSOLUTE_TIMEOUT"
    assert guard.stats()["validated"] == 7
    assert guard.stats()["rejected"] == 1


def test_device_mismatch_is_rejected():
    guard = make()
    session = login(guard)
    with pytest.raises(sg.SessionError) as exc:
        guard.validate(session["session_id"], device_id="ipad-9", now=NOW)
    assert exc.value.code == "SESSION_DEVICE_MISMATCH"
    assert guard.validate(session["session_id"], device_id="mac-1", now=NOW)["status"] == "ok"
    assert guard.stats()["rejected"] == 1


def test_ip_is_checked_only_for_pinned_sessions():
    guard = make()
    pinned = login(guard, pin_ip=True)
    with pytest.raises(sg.SessionError) as exc:
        guard.validate(pinned["session_id"], device_id="mac-1", ip="10.9.9.9", now=NOW)
    assert exc.value.code == "SESSION_IP_MISMATCH"
    assert guard.validate(pinned["session_id"], device_id="mac-1", ip="10.0.0.5",
                          now=NOW)["status"] == "ok"
    assert guard.validate(pinned["session_id"], device_id="mac-1", now=NOW)["status"] == "ok"

    loose = login(guard, user_id="u2", device_id="pc-2", ip="10.0.0.6")
    assert guard.validate(loose["session_id"], device_id="pc-2", ip="192.168.1.4",
                          now=NOW)["status"] == "ok"


def test_per_user_session_limit_and_purge_release():
    guard = make(max_sessions_per_user=2, idle_timeout=60)
    login(guard)
    login(guard)
    with pytest.raises(sg.SessionError) as exc:
        login(guard)
    assert exc.value.code == "SESSION_LIMIT_EXCEEDED"
    assert guard.stats()["created"] == 2

    assert guard.purge(now="2026-11-24T12:00:59+00:00") == 0
    assert guard.purge(now="2026-11-24T12:01:00+00:00") == 2
    login(guard, now="2026-11-24T12:01:01+00:00")
    login(guard, user_id="u2", device_id="pc-2", ip="10.0.0.6",
          now="2026-11-24T12:01:01+00:00")
    assert guard.stats()["created"] == 4


def test_elevation_rules_and_invalid_levels():
    guard = make()
    session = login(guard)
    for level in (1, 0, 4, -1):
        with pytest.raises(sg.SessionError) as exc:
            guard.elevate(session["session_id"], auth_level=level, now=NOW)
        assert exc.value.code == "SESSION_AUTH_LEVEL_INVALID"

    raised = guard.elevate(session["session_id"], auth_level=3,
                           now="2026-11-24T12:05:00+00:00")
    assert raised["auth_level"] == 3
    assert guard.validate(session["session_id"], device_id="mac-1",
                          now="2026-11-24T12:05:00+00:00")["auth_level"] == 3
    with pytest.raises(sg.SessionError) as exc:
        guard.elevate(session["session_id"], auth_level=2,
                      now="2026-11-24T12:05:00+00:00")
    assert exc.value.code == "SESSION_AUTH_LEVEL_INVALID"
    with pytest.raises(sg.SessionError) as exc:
        guard.elevate(session["session_id"], auth_level=2,
                      now="2026-11-24T13:00:00+00:00")
    assert exc.value.code == "SESSION_IDLE_TIMEOUT"
    with pytest.raises(sg.SessionError) as exc:
        guard.elevate("sess-missing", auth_level=2, now=NOW)
    assert exc.value.code == "SESSION_UNKNOWN"


def test_revoke_is_idempotent_and_blocks_validation():
    guard = make()
    session = login(guard)
    assert guard.revoke(session["session_id"]) is True
    assert guard.revoke(session["session_id"]) is False
    with pytest.raises(sg.SessionError) as exc:
        guard.validate(session["session_id"], device_id="mac-1", now=NOW)
    assert exc.value.code == "SESSION_UNKNOWN"
    assert guard.stats()["revoked"] == 1


def test_revoke_all_keeps_the_exempted_session():
    guard = make()
    first = login(guard)
    second = login(guard)
    third = login(guard)
    other = login(guard, user_id="u2", device_id="pc-2", ip="10.0.0.6")

    assert guard.revoke_all("u1", except_session=second["session_id"]) == 2
    with pytest.raises(sg.SessionError):
        guard.validate(first["session_id"], device_id="mac-1", now=NOW)
    with pytest.raises(sg.SessionError):
        guard.validate(third["session_id"], device_id="mac-1", now=NOW)
    assert guard.validate(second["session_id"], device_id="mac-1", now=NOW)["status"] == "ok"
    assert guard.validate(other["session_id"], device_id="pc-2", now=NOW)["status"] == "ok"

    assert guard.revoke_all("u1") == 1
    assert guard.revoke_all("u1") == 0
    assert guard.stats()["revoked"] == 3


def test_list_sessions_filters_expired_and_sorts():
    guard = make(idle_timeout=60)
    first = login(guard)
    second = login(guard, now="2026-11-24T12:00:10+00:00")
    login(guard, user_id="u2", device_id="pc-2", ip="10.0.0.6")

    rows = guard.list_sessions("u1", now=NOW)
    assert [row["session_id"] for row in rows] == sorted(
        [first["session_id"], second["session_id"]])
    assert set(rows[0]) == {"session_id", "user_id", "device_id", "auth_level",
                            "created_at", "expires_at", "idle_expires_at"}
    assert guard.list_sessions("u1", now="2026-11-24T12:01:10+00:00") == []
    assert guard.list_sessions("nobody", now=NOW) == []


def test_purge_removes_idle_and_absolute_expired_sessions():
    idle_guard = make(idle_timeout=60)
    login(idle_guard)
    login(idle_guard, user_id="u2", device_id="pc-2", ip="10.0.0.6")
    assert idle_guard.purge(now="2026-11-24T12:00:59+00:00") == 0
    assert idle_guard.purge(now="2026-11-24T12:01:00+00:00") == 2

    absolute_guard = make(idle_timeout=100000, absolute_timeout=3600)
    login(absolute_guard)
    assert absolute_guard.purge(now="2026-11-24T12:59:59+00:00") == 0
    assert absolute_guard.purge(now="2026-11-24T13:00:00+00:00") == 1


def test_snapshot_restore_preserves_sessions_and_timestamps():
    guard = make()
    session = login(guard)
    state = guard.snapshot()
    pristine = copy.deepcopy(state)
    state["sessions"].clear()
    assert guard.stats()["created"] == 1

    restored = make()
    restored.restore(pristine)
    assert restored.stats() == {"created": 1, "validated": 0, "rejected": 0,
                                "revoked": 0, "rotated": 0}
    out = restored.validate(session["session_id"], device_id="mac-1", ip="10.0.0.5",
                            now=NOW)
    assert out["status"] == "ok"
    with pytest.raises(sg.SessionError) as exc:
        restored.validate(session["session_id"], device_id="mac-1",
                          now="2026-11-24T20:00:00+00:00")
    assert exc.value.code == "SESSION_ABSOLUTE_TIMEOUT"
    assert restored.stats()["validated"] == 1
    assert restored.stats()["rejected"] == 1


def test_corrupt_snapshot_leaves_live_state_untouched():
    guard = make()
    session = login(guard)
    bad_states = [
        None,
        {},
        {"version": 2, "sessions": {}, "stats": {}},
        {"version": 1, "sessions": [], "stats": {}},
        {"version": 1, "sessions": {}, "stats": {"created": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(sg.SessionError) as exc:
            guard.restore(bad)
        assert exc.value.code == "SESSION_SNAPSHOT_INVALID"
    assert guard.validate(session["session_id"], device_id="mac-1", now=NOW)["status"] == "ok"
    assert guard.stats()["created"] == 1


def test_concurrent_rotation_yields_exactly_one_session():
    guard = make()
    session = login(guard)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.rotate(session["session_id"], now=NOW)
            row = {"status": "ok", "session_id": row["session_id"]}
        except Exception as exc:  # pragma: no cover - surfaced through assertions
            row = {"status": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [row for row in results if row["status"] == "ok"]
    losers = [row for row in results if row["status"] == "error"]
    assert len(winners) == 1
    assert len(losers) == 7
    assert [row["code"] for row in losers] == ["SESSION_UNKNOWN"] * 7
    assert guard.stats()["rotated"] == 1
    assert guard.validate(winners[0]["session_id"], device_id="mac-1",
                          now=NOW)["status"] == "ok"
