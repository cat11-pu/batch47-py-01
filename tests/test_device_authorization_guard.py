import copy
import re
import threading

import pytest

import device_authorization_guard as dg

NOW = "2026-11-24T10:00:00+00:00"
URI = "https://id.example.com/device"


def make(**kwargs):
    guard = dg.DeviceAuthorizationGuard(**kwargs)
    guard.register_client("tv-app", scopes=("read", "profile"), verification_uri=URI)
    return guard


def start(guard, **overrides):
    args = {"client_id": "tv-app", "scope": "read profile", "now": NOW}
    args.update(overrides)
    return guard.start(**args)


def test_start_returns_canonical_codes_and_metadata():
    guard = make(poll_interval=7, expires_in=300)
    out = start(guard)
    assert out["verification_uri"] == URI
    assert out["interval"] == 7
    assert out["expires_in"] == 300
    assert out["expires_at"] == "2026-11-24T10:05:00+00:00"
    assert re.fullmatch(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}",
                        out["user_code"])
    assert isinstance(out["device_code"], str) and len(out["device_code"]) >= 16

    seen = {out["user_code"]}
    for _ in range(3):
        seen.add(start(guard)["user_code"])
    assert len(seen) == 4
    assert guard.stats() == {"started": 4, "approved": 0, "denied": 0,
                             "redeemed": 0, "slow_down": 0}


def test_client_registration_conflicts_and_scope_ceiling():
    guard = make()
    again = guard.register_client("tv-app", scopes=("profile", "read"),
                                  verification_uri=URI)
    assert again["verification_uri"] == URI
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.register_client("tv-app", scopes=("read",), verification_uri=URI)
    assert exc.value.code == "DEVICE_CLIENT_CONFLICT"

    with pytest.raises(dg.DeviceGrantError) as exc:
        start(guard, client_id="ghost-box")
    assert exc.value.code == "DEVICE_UNKNOWN_CLIENT"
    with pytest.raises(dg.DeviceGrantError) as exc:
        start(guard, scope="read admin")
    assert exc.value.code == "DEVICE_SCOPE_NOT_ALLOWED"
    assert guard.stats()["started"] == 0


def test_user_code_lookup_normalizes_case_and_separators():
    guard = make()
    out = start(guard)
    code = out["user_code"]
    variants = [
        code.lower(),
        code.replace("-", ""),
        code.lower().replace("-", " "),
        "  " + code + "  ",
    ]
    for variant in variants:
        info = guard.lookup_user_code(variant, now=NOW)
        assert info["user_code"] == code
        assert info["client_id"] == "tv-app"
        assert info["scope"] == "read profile"
        assert info["status"] == "pending"
        assert info["redeemed"] is False


def test_malformed_user_codes_are_rejected_before_lookup():
    guard = make()
    for bad in [None, "", "1", "12", "AAAA-AAAA", "BCDF-GHJ", "BCDF-GHJK-LMNP"]:
        with pytest.raises(dg.DeviceGrantError) as exc:
            guard.lookup_user_code(bad)
        assert exc.value.code == "DEVICE_CODE_MALFORMED"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.approve("", subject="alice", now=NOW)
    assert exc.value.code == "DEVICE_CODE_MALFORMED"


def test_approval_issues_one_token_then_marks_redeemed():
    guard = make()
    out = start(guard)
    decided = guard.approve(out["user_code"], subject="alice", now=NOW)
    assert decided["status"] == "approved"
    assert decided["subject"] == "alice"

    first = guard.poll(client_id="tv-app", device_code=out["device_code"], now=NOW)
    assert first["status"] == "approved"
    assert first["subject"] == "alice"
    assert first["token_type"] == "Bearer"
    assert isinstance(first["access_token"], str) and first["access_token"]
    assert first["scope"] == "read profile"

    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="tv-app", device_code=out["device_code"], now=NOW)
    assert exc.value.code == "DEVICE_CODE_ALREADY_USED"
    assert guard.lookup_user_code(out["user_code"], now=NOW)["redeemed"] is True
    assert guard.stats() == {"started": 1, "approved": 1, "denied": 0,
                             "redeemed": 1, "slow_down": 0}


def test_pending_polls_respect_interval_without_slow_down():
    guard = make(poll_interval=5)
    out = start(guard)
    first = guard.poll(client_id="tv-app", device_code=out["device_code"], now=NOW)
    assert first["status"] == "authorization_pending"
    assert first["interval"] == 5

    second = guard.poll(client_id="tv-app", device_code=out["device_code"],
                        now="2026-11-24T10:00:05+00:00")
    assert second["status"] == "authorization_pending"

    guard.approve(out["user_code"], subject="bob",
                  now="2026-11-24T10:00:06+00:00")
    done = guard.poll(client_id="tv-app", device_code=out["device_code"],
                      now="2026-11-24T10:00:06+00:00")
    assert done["status"] == "approved"
    assert guard.stats() == {"started": 1, "approved": 1, "denied": 0,
                             "redeemed": 1, "slow_down": 0}


def test_slow_down_doubles_interval_from_last_poll():
    guard = make(poll_interval=5)
    out = start(guard)
    assert guard.poll(client_id="tv-app", device_code=out["device_code"],
                      now=NOW)["status"] == "authorization_pending"

    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="tv-app", device_code=out["device_code"],
                   now="2026-11-24T10:00:02+00:00")
    assert exc.value.code == "DEVICE_SLOW_DOWN"

    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="tv-app", device_code=out["device_code"],
                   now="2026-11-24T10:00:07+00:00")
    assert exc.value.code == "DEVICE_SLOW_DOWN"

    later = guard.poll(client_id="tv-app", device_code=out["device_code"],
                       now="2026-11-24T10:00:27+00:00")
    assert later["status"] == "authorization_pending"
    assert later["interval"] == 20
    assert guard.stats()["slow_down"] == 2


def test_denial_is_terminal_for_polling_and_approval():
    guard = make()
    out = start(guard)
    denied = guard.deny(out["user_code"], reason="user said no", now=NOW)
    assert denied["status"] == "denied"
    assert denied["reason"] == "user said no"

    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="tv-app", device_code=out["device_code"], now=NOW)
    assert exc.value.code == "DEVICE_ACCESS_DENIED"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.approve(out["user_code"], subject="mallory", now=NOW)
    assert exc.value.code == "DEVICE_ALREADY_DECIDED"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.deny(out["user_code"], reason="again", now=NOW)
    assert exc.value.code == "DEVICE_ALREADY_DECIDED"
    assert guard.stats() == {"started": 1, "approved": 0, "denied": 1,
                             "redeemed": 0, "slow_down": 0}


def test_expiry_blocks_every_operation_at_the_boundary():
    guard = make(expires_in=60)
    out = start(guard)
    assert guard.poll(client_id="tv-app", device_code=out["device_code"],
                      now="2026-11-24T10:00:59+00:00")["status"] == "authorization_pending"

    late = "2026-11-24T10:01:00+00:00"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="tv-app", device_code=out["device_code"], now=late)
    assert exc.value.code == "DEVICE_CODE_EXPIRED"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.lookup_user_code(out["user_code"], now=late)
    assert exc.value.code == "DEVICE_CODE_EXPIRED"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.approve(out["user_code"], subject="late", now=late)
    assert exc.value.code == "DEVICE_CODE_EXPIRED"


def test_poll_rejects_client_mismatch_and_unknown_device_code():
    guard = make()
    out = start(guard)
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="other-app", device_code=out["device_code"], now=NOW)
    assert exc.value.code == "DEVICE_CLIENT_MISMATCH"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="tv-app", device_code="dc-missing", now=NOW)
    assert exc.value.code == "DEVICE_CODE_UNKNOWN"


def test_repeated_approval_with_same_subject_is_idempotent():
    guard = make()
    out = start(guard)
    guard.approve(out["user_code"], subject="alice", now=NOW)
    again = guard.approve(out["user_code"], subject="alice", now=NOW)
    assert again["status"] == "approved"
    assert guard.stats()["approved"] == 1

    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.approve(out["user_code"], subject="mallory", now=NOW)
    assert exc.value.code == "DEVICE_ALREADY_DECIDED"
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.deny(out["user_code"], reason="late denial", now=NOW)
    assert exc.value.code == "DEVICE_ALREADY_DECIDED"


def test_pending_limit_and_purge_release_capacity():
    guard = make(max_pending=2, expires_in=60)
    first = start(guard)
    start(guard)
    with pytest.raises(dg.DeviceGrantError) as exc:
        start(guard)
    assert exc.value.code == "DEVICE_PENDING_FULL"
    assert guard.stats()["started"] == 2

    assert guard.purge(now="2026-11-24T10:00:59+00:00") == 0
    assert guard.purge(now="2026-11-24T10:01:00+00:00") == 2
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.lookup_user_code(first["user_code"], now=NOW)
    assert exc.value.code == "DEVICE_CODE_UNKNOWN"
    assert start(guard, now="2026-11-24T10:01:01+00:00")["user_code"]


def test_cancel_only_applies_to_pending_devices():
    guard = make()
    first = start(guard)
    second = start(guard)
    assert guard.cancel(first["device_code"]) is True
    assert guard.cancel(first["device_code"]) is False
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.poll(client_id="tv-app", device_code=first["device_code"], now=NOW)
    assert exc.value.code == "DEVICE_CODE_UNKNOWN"

    guard.approve(second["user_code"], subject="carl", now=NOW)
    with pytest.raises(dg.DeviceGrantError) as exc:
        guard.cancel(second["device_code"])
    assert exc.value.code == "DEVICE_ALREADY_DECIDED"
    assert guard.poll(client_id="tv-app", device_code=second["device_code"],
                      now=NOW)["status"] == "approved"


def test_snapshot_restore_keeps_approval_and_redemption_state():
    guard = make()
    out = start(guard)
    guard.approve(out["user_code"], subject="dora", now=NOW)
    state = guard.snapshot()
    pristine = copy.deepcopy(state)
    state["devices"].clear()
    assert guard.stats()["started"] == 1

    assert guard.poll(client_id="tv-app", device_code=out["device_code"],
                      now=NOW)["status"] == "approved"

    restored = dg.DeviceAuthorizationGuard()
    restored.restore(pristine)
    info = restored.lookup_user_code(out["user_code"], now=NOW)
    assert info["status"] == "approved"
    assert info["subject"] == "dora"
    assert info["redeemed"] is False
    assert restored.poll(client_id="tv-app", device_code=out["device_code"],
                         now=NOW)["status"] == "approved"
    with pytest.raises(dg.DeviceGrantError) as exc:
        restored.poll(client_id="tv-app", device_code=out["device_code"], now=NOW)
    assert exc.value.code == "DEVICE_CODE_ALREADY_USED"
    assert restored.stats()["redeemed"] == 1

    bad_states = [
        None,
        {},
        {"version": 2, "clients": {}, "devices": {}, "stats": {}},
        {"version": 1, "clients": [], "devices": {}, "stats": {}},
        {"version": 1, "clients": {}, "devices": {}, "stats": {"started": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(dg.DeviceGrantError) as exc:
            restored.restore(bad)
        assert exc.value.code == "DEVICE_SNAPSHOT_INVALID"
    assert restored.lookup_user_code(out["user_code"], now=NOW)["status"] == "approved"


def test_concurrent_poll_releases_exactly_one_token():
    guard = make()
    out = start(guard)
    guard.approve(out["user_code"], subject="erin", now=NOW)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.poll(client_id="tv-app", device_code=out["device_code"], now=NOW)
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
    assert statuses == ["approved"] + ["error"] * 7
    assert [row["code"] for row in results if row["status"] == "error"] == \
        ["DEVICE_CODE_ALREADY_USED"] * 7
    assert guard.stats()["redeemed"] == 1
