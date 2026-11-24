import pytest

import step_up_assurance_guard as suag

NOW = "2026-11-23T10:00:00+00:00"
STAT_ZERO = {"evaluated": 0, "allowed": 0, "denied": 0,
             "stepped_up": 0, "revoked": 0, "rejected": 0}


def make_guard(**kwargs):
    guard = suag.StepUpAssuranceGuard(**kwargs)
    guard.define_acr_level("pwd", rank=1)
    guard.define_acr_level("mfa", rank=2)
    guard.define_acr_level("phr", rank=3)
    guard.register_resource("wiki", required_acr="mfa")
    guard.register_resource("payments", required_acr="phr", max_age=300,
                            allowed_amr=("otp", "webauthn"))
    return guard


def add_session(guard, *, sid="s1", subject="alice", acr="pwd",
                amr=("pwd",), auth_time=NOW, now=NOW):
    return guard.register_session(sid, subject=subject, acr=acr, amr=amr,
                                  auth_time=auth_time, now=now)


def test_acr_levels_register_conflict_and_unknown():
    guard = suag.StepUpAssuranceGuard()
    assert guard.define_acr_level("pwd", rank=1) == {"acr": "pwd", "rank": 1}
    assert guard.define_acr_level("pwd", rank=1) == {"acr": "pwd", "rank": 1}

    with pytest.raises(suag.StepUpError) as exc:
        guard.define_acr_level("pwd", rank=2)
    assert exc.value.code == "STEP_CONFLICT"
    with pytest.raises(suag.StepUpError) as exc:
        guard.define_acr_level("bad", rank=0)
    assert exc.value.code == "STEP_INVALID_ACR"

    with pytest.raises(suag.StepUpError) as exc:
        guard.register_resource("wiki", required_acr="mfa")
    assert exc.value.code == "STEP_UNKNOWN_ACR"
    with pytest.raises(suag.StepUpError) as exc:
        guard.register_session("s1", subject="alice", acr="mfa", amr=("pwd",),
                               auth_time=NOW, now=NOW)
    assert exc.value.code == "STEP_UNKNOWN_ACR"


def test_resource_policies_register_and_conflict():
    guard = make_guard()
    payload = guard.register_resource("wiki", required_acr="mfa")
    assert payload == {"resource": "wiki", "required_acr": "mfa",
                       "max_age": 900, "allowed_amr": None}
    assert guard.register_resource("wiki", required_acr="mfa") == payload

    with pytest.raises(suag.StepUpError) as exc:
        guard.register_resource("wiki", required_acr="phr")
    assert exc.value.code == "STEP_CONFLICT"
    with pytest.raises(suag.StepUpError) as exc:
        guard.register_resource("age", required_acr="mfa", max_age=-1)
    assert exc.value.code == "STEP_INVALID_RESOURCE"
    with pytest.raises(suag.StepUpError) as exc:
        guard.register_resource("amr", required_acr="mfa", allowed_amr=())
    assert exc.value.code == "STEP_INVALID_RESOURCE"

    add_session(guard)
    with pytest.raises(suag.StepUpError) as exc:
        guard.evaluate("s1", resource="ghost", now=NOW)
    assert exc.value.code == "STEP_UNKNOWN_RESOURCE"


def test_session_registration_and_listing():
    guard = make_guard()
    first = add_session(guard, sid="s1")
    second = add_session(guard, sid="s2", subject="bob", acr="mfa", amr=("otp",))
    assert first["session_id"] == "s1"
    assert first["subject"] == "alice"
    assert first["acr"] == "pwd"
    assert first["amr"] == ["pwd"]
    assert first["auth_time"] == NOW
    assert second["acr"] == "mfa"

    assert [row["session_id"] for row in guard.list_sessions("alice")] == ["s1"]
    assert [row["session_id"] for row in guard.list_sessions("bob")] == ["s2"]
    assert guard.list_sessions("nobody") == []

    with pytest.raises(suag.StepUpError) as exc:
        add_session(guard, sid="s1")
    assert exc.value.code == "STEP_SESSION_EXISTS"
    with pytest.raises(suag.StepUpError) as exc:
        add_session(guard, sid="s3", amr=())
    assert exc.value.code == "STEP_INVALID_SESSION"


def test_acr_too_low_is_denied_but_not_fatal():
    guard = make_guard()
    add_session(guard, acr="pwd")
    decision = guard.evaluate("s1", resource="wiki", now=NOW)
    assert decision == {"allowed": False, "reason": "acr_too_low",
                        "session_id": "s1", "subject": "alice",
                        "current_acr": "pwd", "required_acr": "mfa",
                        "auth_age": 0, "max_age": 900}
    assert guard.stats() == {"evaluated": 1, "allowed": 0, "denied": 1,
                             "stepped_up": 0, "revoked": 0, "rejected": 0}


def test_step_up_upgrades_and_downgrade_is_refused():
    guard = make_guard()
    add_session(guard, acr="pwd")
    upgraded = guard.step_up("s1", acr="mfa", amr=("otp",), now=NOW)
    assert upgraded["previous_acr"] == "pwd"
    assert upgraded["acr"] == "mfa"
    assert upgraded["amr"] == ["otp"]
    assert upgraded["auth_time"] == NOW

    decision = guard.evaluate("s1", resource="wiki", now=NOW)
    assert decision["allowed"] is True
    assert decision["reason"] is None
    assert decision["current_acr"] == "mfa"
    assert decision["auth_age"] == 0

    with pytest.raises(suag.StepUpError) as exc:
        guard.step_up("s1", acr="pwd", now=NOW)
    assert exc.value.code == "STEP_DOWNGRADE"
    assert guard.evaluate("s1", resource="wiki", now=NOW)["allowed"] is True
    assert guard.stats() == {"evaluated": 2, "allowed": 2, "denied": 0,
                             "stepped_up": 1, "revoked": 0, "rejected": 1}


def test_unknown_session_and_acr_errors():
    guard = make_guard()
    add_session(guard, acr="mfa", amr=("otp",))
    with pytest.raises(suag.StepUpError) as exc:
        guard.step_up("ghost", acr="mfa", now=NOW)
    assert exc.value.code == "STEP_UNKNOWN_SESSION"
    with pytest.raises(suag.StepUpError) as exc:
        guard.step_up("s1", acr="ghost", now=NOW)
    assert exc.value.code == "STEP_UNKNOWN_ACR"
    with pytest.raises(suag.StepUpError) as exc:
        guard.evaluate("ghost", resource="wiki", now=NOW)
    assert exc.value.code == "STEP_UNKNOWN_SESSION"
    assert guard.stats()["rejected"] == 3


def test_auth_freshness_boundary():
    guard = make_guard()
    add_session(guard, acr="mfa", amr=("otp",))
    inside = guard.evaluate("s1", resource="wiki",
                            now="2026-11-23T10:15:00+00:00")
    assert inside["allowed"] is True
    assert inside["auth_age"] == 900
    assert inside["max_age"] == 900

    outside = guard.evaluate("s1", resource="wiki",
                             now="2026-11-23T10:15:01+00:00")
    assert outside == {"allowed": False, "reason": "stale",
                       "session_id": "s1", "subject": "alice",
                       "current_acr": "mfa", "required_acr": "mfa",
                       "auth_age": 901, "max_age": 900}
    assert guard.stats() == {"evaluated": 2, "allowed": 1, "denied": 1,
                             "stepped_up": 0, "revoked": 0, "rejected": 0}


def test_resource_max_age_overrides_default():
    guard = make_guard()
    add_session(guard, acr="phr", amr=("otp",))
    inside = guard.evaluate("s1", resource="payments",
                            now="2026-11-23T10:05:00+00:00")
    assert inside["allowed"] is True
    assert inside["max_age"] == 300
    assert inside["auth_age"] == 300

    outside = guard.evaluate("s1", resource="payments",
                             now="2026-11-23T10:05:01+00:00")
    assert outside == {"allowed": False, "reason": "stale",
                       "session_id": "s1", "subject": "alice",
                       "current_acr": "phr", "required_acr": "phr",
                       "auth_age": 301, "max_age": 300}

    add_session(guard, sid="s2", acr="mfa", amr=("otp",))
    looser = guard.evaluate("s2", resource="wiki",
                            now="2026-11-23T10:05:01+00:00")
    assert looser["allowed"] is True
    assert looser["max_age"] == 900


def test_amr_allowlist_enforced_after_acr():
    guard = make_guard()
    add_session(guard, sid="s1", acr="phr", amr=("pwd",))
    denied = guard.evaluate("s1", resource="payments", now=NOW)
    assert denied == {"allowed": False, "reason": "amr_not_allowed",
                      "session_id": "s1", "subject": "alice",
                      "current_acr": "phr", "required_acr": "phr",
                      "auth_age": 0, "max_age": 300}

    add_session(guard, sid="s2", acr="phr", amr=("otp", "pwd"))
    allowed = guard.evaluate("s2", resource="payments", now=NOW)
    assert allowed["allowed"] is True
    assert allowed["reason"] is None
    assert guard.stats()["evaluated"] == 2
    assert guard.stats()["allowed"] == 1
    assert guard.stats()["denied"] == 1


def test_revoked_session_is_rejected():
    guard = make_guard()
    add_session(guard, acr="mfa", amr=("otp",))
    guard.revoke_session("s1")
    guard.revoke_session("s1")

    with pytest.raises(suag.StepUpError) as exc:
        guard.evaluate("s1", resource="wiki", now=NOW)
    assert exc.value.code == "STEP_SESSION_REVOKED"
    with pytest.raises(suag.StepUpError) as exc:
        guard.step_up("s1", acr="phr", now=NOW)
    assert exc.value.code == "STEP_SESSION_REVOKED"
    with pytest.raises(suag.StepUpError) as exc:
        guard.revoke_session("ghost")
    assert exc.value.code == "STEP_UNKNOWN_SESSION"
    assert guard.stats() == {"evaluated": 0, "allowed": 0, "denied": 0,
                             "stepped_up": 0, "revoked": 1, "rejected": 3}


def test_idle_timeout_and_touch():
    guard = make_guard()
    guard.register_resource("long", required_acr="pwd", max_age=100000)
    add_session(guard, sid="s1", acr="pwd")
    assert guard.evaluate("s1", resource="long",
                          now="2026-11-23T10:28:20+00:00")["allowed"] is True
    still_alive = guard.evaluate("s1", resource="long",
                                 now="2026-11-23T10:50:00+00:00")
    assert still_alive["allowed"] is True
    assert still_alive["auth_age"] == 3000

    add_session(guard, sid="s2", acr="pwd")
    with pytest.raises(suag.StepUpError) as exc:
        guard.evaluate("s2", resource="long",
                       now="2026-11-23T10:30:01+00:00")
    assert exc.value.code == "STEP_SESSION_EXPIRED"
    with pytest.raises(suag.StepUpError) as exc:
        guard.touch("s2", now="2026-11-23T10:30:01+00:00")
    assert exc.value.code == "STEP_SESSION_EXPIRED"
    assert guard.stats() == {"evaluated": 2, "allowed": 2, "denied": 0,
                             "stepped_up": 0, "revoked": 0, "rejected": 2}


def test_rotate_session_invalidates_old_id():
    guard = make_guard()
    add_session(guard, sid="s1", acr="mfa", amr=("otp",))
    rotated = guard.rotate_session("s1", now=NOW)
    assert rotated["session_id"] != "s1"
    assert rotated["previous_session_id"] == "s1"
    assert rotated["subject"] == "alice"
    assert rotated["acr"] == "mfa"
    assert rotated["amr"] == ["otp"]
    assert rotated["auth_time"] == NOW

    with pytest.raises(suag.StepUpError) as exc:
        guard.evaluate("s1", resource="wiki", now=NOW)
    assert exc.value.code == "STEP_UNKNOWN_SESSION"
    assert guard.evaluate(rotated["session_id"], resource="wiki", now=NOW)["allowed"] is True
    assert [row["session_id"] for row in guard.list_sessions("alice")] == [rotated["session_id"]]
    with pytest.raises(suag.StepUpError) as exc:
        guard.rotate_session("ghost", now=NOW)
    assert exc.value.code == "STEP_UNKNOWN_SESSION"


def test_session_limit_and_purge():
    guard = make_guard(max_sessions_per_subject=2)
    add_session(guard, sid="a")
    add_session(guard, sid="b")
    with pytest.raises(suag.StepUpError) as exc:
        add_session(guard, sid="c")
    assert exc.value.code == "STEP_SESSION_LIMIT"

    guard.revoke_session("a")
    add_session(guard, sid="c", now="2026-11-23T10:30:00+00:00")
    assert [row["session_id"] for row in guard.list_sessions("alice")] == ["a", "b", "c"]
    assert guard.purge(now=NOW) == 1
    assert [row["session_id"] for row in guard.list_sessions("alice")] == ["b", "c"]

    with pytest.raises(suag.StepUpError) as exc:
        guard.evaluate("b", resource="wiki", now="2026-11-23T10:30:01+00:00")
    assert exc.value.code == "STEP_SESSION_EXPIRED"
    assert guard.purge(now="2026-11-23T10:30:01+00:00") == 1
    assert [row["session_id"] for row in guard.list_sessions("alice")] == ["c"]


def test_snapshot_restore_and_invalid_state():
    guard = make_guard()
    add_session(guard, acr="mfa", amr=("otp",))
    state = guard.snapshot()
    assert state["stats"]["evaluated"] == 0

    assert guard.evaluate("s1", resource="wiki", now=NOW)["allowed"] is True
    assert state["stats"]["evaluated"] == 0

    restored = suag.StepUpAssuranceGuard()
    restored.restore(state)
    assert restored.evaluate("s1", resource="wiki", now=NOW)["allowed"] is True

    with pytest.raises(suag.StepUpError) as exc:
        restored.restore({"version": 2})
    assert exc.value.code == "STEP_SNAPSHOT_INVALID"
    assert restored.evaluate("s1", resource="wiki", now=NOW)["allowed"] is True
    assert restored.stats()["evaluated"] == 2
    assert restored.stats()["allowed"] == 2


def test_concurrent_session_registration_respects_limit():
    import threading

    guard = make_guard(max_sessions_per_subject=5)
    barrier = threading.Barrier(16)
    results = []
    errors = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = guard.register_session(f"c{index}", subject="alice", acr="pwd",
                                         amr=("pwd",), auth_time=NOW, now=NOW)
        except suag.StepUpError as exc:
            with lock:
                errors.append(exc.code)
        else:
            with lock:
                results.append(out)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 5
    assert errors == ["STEP_SESSION_LIMIT"] * 11
    assert len(guard.list_sessions("alice")) == 5
    assert guard.stats()["rejected"] == 11
