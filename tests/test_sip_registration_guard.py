import hashlib
import threading

import pytest

import sip_registration_guard as sg

NOW = "2026-11-23T10:00:00+00:00"
REALM = "example.com"
AOR = "sip:alice@example.com"
URI = "sip:example.com"
PASSWORD = "s3cret"
A = "sip:alice@10.0.0.5:5060"
B = "sip:alice@10.0.0.6:5060"
C = "sip:alice@10.0.0.7:5060"


def md5(text):
    return hashlib.md5(text.encode()).hexdigest()


def digest(*, nonce, nc="00000001", cnonce="cn-1", qop="auth",
           password=PASSWORD, uri=URI, realm=REALM, username="alice"):
    ha1 = md5("%s:%s:%s" % (username, realm, password))
    ha2 = md5("REGISTER:%s" % uri)
    response = md5("%s:%s:%s:%s:%s:%s" % (ha1, nonce, nc, cnonce, qop, ha2))
    return {"username": username, "realm": realm, "nonce": nonce, "uri": uri,
            "nc": nc, "cnonce": cnonce, "qop": qop, "response": response}


def make(**kwargs):
    guard = sg.SipRegistrationGuard(**kwargs)
    assert guard.add_realm(REALM)["created"] is True
    assert guard.add_user(AOR, password=PASSWORD)["created"] is True
    return guard


def request(*, nonce, nc="00000001", cseq=1, contact=A, call_id="call-1",
            expires=None, auth=None):
    payload = {"aor": AOR, "call_id": call_id, "cseq": cseq, "contact": contact}
    if expires is not None:
        payload["expires"] = expires
    payload["authorization"] = auth if auth is not None else digest(
        nonce=nonce, nc=nc)
    return payload


def issue(guard, now=NOW):
    return guard.issue_nonce(REALM, now=now)["nonce"]


def test_realm_and_user_registration_are_idempotent():
    guard = sg.SipRegistrationGuard()
    assert guard.add_realm(REALM)["created"] is True
    again = guard.add_realm(REALM)
    assert again["created"] is False
    assert again["algorithm"] == "MD5"
    with pytest.raises(sg.SipError) as exc:
        guard.add_realm(REALM, qop="auth-int")
    assert exc.value.code == "SIP_REALM_CONFLICT"
    with pytest.raises(sg.SipError) as exc2:
        guard.add_realm("sha.example", algorithm="SHA-256")
    assert exc2.value.code == "SIP_UNSUPPORTED_ALGORITHM"

    assert guard.add_user(AOR, password=PASSWORD)["created"] is True
    assert guard.add_user(AOR, password=PASSWORD)["created"] is False
    with pytest.raises(sg.SipError) as exc3:
        guard.add_user(AOR, password="other")
    assert exc3.value.code == "SIP_CREDENTIAL_CONFLICT"
    with pytest.raises(sg.SipError) as exc4:
        guard.add_user("sip:ghost@nowhere.example", password=PASSWORD)
    assert exc4.value.code == "SIP_UNKNOWN_REALM"
    with pytest.raises(sg.SipError) as exc5:
        guard.add_user(AOR, password="")
    assert exc5.value.code == "SIP_BAD_PASSWORD"


def test_valid_register_creates_binding_with_default_expiry():
    guard = make()
    nonce_info = guard.issue_nonce(REALM, now=NOW)
    assert nonce_info["realm"] == REALM
    assert nonce_info["qop"] == "auth"
    assert nonce_info["issued_at"] == NOW
    assert nonce_info["expires_at"] == "2026-11-23T10:05:00+00:00"
    assert len(nonce_info["nonce"]) == 32

    result = guard.submit_register(request(nonce=nonce_info["nonce"]), now=NOW)
    assert result["aor"] == AOR
    assert result["contact"] == A
    assert result["expires"] == 300
    assert result["expires_at"] == "2026-11-23T10:05:00+00:00"
    assert result["registered"] is True
    assert result["removed"] is False
    assert result["created"] is True
    assert result["retransmission"] is False
    assert guard.query_bindings(AOR) == [{
        "contact": A,
        "expires": 300,
        "expires_at": "2026-11-23T10:05:00+00:00",
        "call_id": "call-1",
        "cseq": 1,
    }]
    assert guard.stats()["registered"] == 1


def test_missing_or_malformed_authorization_is_refused():
    guard = make()
    nonce = issue(guard)
    without = request(nonce=nonce)
    del without["authorization"]
    with pytest.raises(sg.SipError) as exc:
        guard.submit_register(without, now=NOW)
    assert exc.value.code == "SIP_AUTH_REQUIRED"
    assert guard.stats()["auth_failed"] == 1

    bad_nc = request(nonce=nonce, auth=digest(nonce=nonce, nc="zzz"))
    with pytest.raises(sg.SipError) as exc2:
        guard.submit_register(bad_nc, now=NOW)
    assert exc2.value.code == "SIP_BAD_AUTH_DIGEST"
    assert guard.stats()["auth_failed"] == 2

    good = guard.submit_register(request(nonce=nonce, cseq=2), now=NOW)
    assert good["registered"] is True
    assert guard.stats()["auth_failed"] == 2


def test_wrong_password_or_username_fails_digest():
    guard = make()
    nonce = issue(guard)
    with pytest.raises(sg.SipError) as exc:
        guard.submit_register(
            request(nonce=nonce, auth=digest(nonce=nonce, password="wrong")),
            now=NOW)
    assert exc.value.code == "SIP_AUTH_FAILED"

    with pytest.raises(sg.SipError) as exc2:
        guard.submit_register(
            request(nonce=nonce, cseq=2,
                    auth=digest(nonce=nonce, username="mallory")),
            now=NOW)
    assert exc2.value.code == "SIP_AUTH_FAILED"
    assert guard.stats()["auth_failed"] == 2
    assert guard.query_bindings(AOR) == []

    ok = guard.submit_register(request(nonce=nonce, cseq=3), now=NOW)
    assert ok["registered"] is True


def test_nonce_realm_staleness_and_unknown_nonce():
    guard = make()
    with pytest.raises(sg.SipError) as exc:
        guard.issue_nonce("missing.example", now=NOW)
    assert exc.value.code == "SIP_UNKNOWN_REALM"

    guard.add_realm("other.example")
    guard.add_user("sip:bob@other.example", password="bob-pass")
    foreign = guard.issue_nonce("other.example", now=NOW)["nonce"]
    with pytest.raises(sg.SipError) as exc2:
        guard.submit_register(request(nonce=foreign), now=NOW)
    assert exc2.value.code == "SIP_NONCE_REALM_MISMATCH"

    with pytest.raises(sg.SipError) as exc3:
        guard.submit_register(
            request(nonce=foreign, cseq=2,
                    auth=digest(nonce="deadbeef" * 4)), now=NOW)
    assert exc3.value.code == "SIP_UNKNOWN_NONCE"

    fresh = issue(guard)
    with pytest.raises(sg.SipError) as exc4:
        guard.submit_register(request(nonce=fresh, cseq=3),
                              now="2026-11-23T10:05:01+00:00")
    assert exc4.value.code == "SIP_STALE_NONCE"
    assert guard.stats()["replayed"] == 3
    assert guard.query_bindings(AOR) == []


def test_nonce_count_must_increase_and_replays_are_refused():
    guard = make()
    nonce = issue(guard)
    first = guard.submit_register(
        request(nonce=nonce, nc="00000001", cseq=1), now=NOW)
    assert first["expires"] == 300

    with pytest.raises(sg.SipError) as exc:
        guard.submit_register(
            request(nonce=nonce, nc="00000001", cseq=2, expires=120), now=NOW)
    assert exc.value.code == "SIP_REPLAY"
    assert guard.stats()["replayed"] == 1
    assert guard.query_bindings(AOR)[0]["expires"] == 300

    second = guard.submit_register(
        request(nonce=nonce, nc="00000002", cseq=2, expires=120), now=NOW)
    assert second["expires"] == 120
    assert second["expires_at"] == "2026-11-23T10:02:00+00:00"

    with pytest.raises(sg.SipError) as exc2:
        guard.submit_register(
            request(nonce=nonce, nc="00000001", cseq=3), now=NOW)
    assert exc2.value.code == "SIP_REPLAY"
    assert guard.stats()["replayed"] == 2


def test_cseq_regression_versus_exact_retransmission():
    guard = make()
    nonce = issue(guard)
    first = guard.submit_register(request(nonce=nonce, nc="00000001", cseq=1),
                                  now=NOW)
    retry = guard.submit_register(
        request(nonce=nonce, nc="00000002", cseq=1, contact=A), now=NOW)
    assert retry["retransmission"] is True
    assert retry["registered"] is True
    assert retry["expires_at"] == first["expires_at"]
    assert guard.stats()["registered"] == 1
    assert guard.stats()["retransmitted"] == 1
    assert len(guard.query_bindings(AOR)) == 1

    with pytest.raises(sg.SipError) as exc:
        guard.submit_register(
            request(nonce=nonce, nc="00000003", cseq=1, contact=B), now=NOW)
    assert exc.value.code == "SIP_CSEQ_REGRESSION"

    with pytest.raises(sg.SipError) as exc2:
        guard.submit_register(request(nonce=nonce, nc="00000004", cseq=0),
                              now=NOW)
    assert exc2.value.code == "SIP_BAD_CSEQ"
    assert guard.stats()["rejected"] == 2


def test_expiry_defaults_clamps_and_contact_parameter():
    guard = make()
    nonce = issue(guard)
    rows = []
    rows.append(guard.submit_register(
        request(nonce=nonce, nc="00000001", cseq=1, contact=A), now=NOW))
    rows.append(guard.submit_register(
        request(nonce=nonce, nc="00000002", cseq=2, contact=B, expires=5),
        now=NOW))
    rows.append(guard.submit_register(
        request(nonce=nonce, nc="00000003", cseq=3, contact="sip:alice@10.0.0.9:5060",
                expires=99999), now=NOW))
    rows.append(guard.submit_register(
        request(nonce=nonce, nc="00000004", cseq=4,
                contact="sip:alice@10.0.0.8:5060;expires=120"), now=NOW))
    assert [row["expires"] for row in rows] == [300, 60, 3600, 120]
    assert rows[1]["expires_at"] == "2026-11-23T10:01:00+00:00"
    assert rows[2]["expires_at"] == "2026-11-23T11:00:00+00:00"
    assert rows[3]["contact"] == "sip:alice@10.0.0.8:5060"

    with pytest.raises(sg.SipError) as exc:
        guard.submit_register(
            request(nonce=nonce, nc="00000005", cseq=5, contact=C, expires=-1),
            now=NOW)
    assert exc.value.code == "SIP_BAD_EXPIRES"
    bindings = guard.query_bindings(AOR)
    assert [row["contact"] for row in bindings] == [
        A, B, "sip:alice@10.0.0.8:5060", "sip:alice@10.0.0.9:5060"]
    assert [row["expires"] for row in bindings] == [300, 60, 120, 3600]


def test_zero_expiry_removes_only_the_named_contact():
    guard = make()
    nonce = issue(guard)
    guard.submit_register(request(nonce=nonce, nc="00000001", cseq=1, contact=A),
                          now=NOW)
    guard.submit_register(request(nonce=nonce, nc="00000002", cseq=2, contact=B),
                          now=NOW)
    removed = guard.submit_register(
        request(nonce=nonce, nc="00000003", cseq=3, contact=A, expires=0),
        now=NOW)
    assert removed["registered"] is False
    assert removed["removed"] is True
    assert removed["expires_at"] is None
    assert [row["contact"] for row in guard.query_bindings(AOR)] == [B]
    assert guard.stats()["deregistered"] == 1

    with pytest.raises(sg.SipError) as exc:
        guard.remove_binding(AOR, contact=A)
    assert exc.value.code == "SIP_NO_SUCH_BINDING"
    assert guard.remove_binding(AOR, contact=B) == {
        "aor": AOR, "contact": B, "removed": True, "remaining": 0}
    assert guard.stats()["deregistered"] == 2


def test_contact_quota_is_enforced_but_updates_are_free():
    guard = make(max_contacts=2)
    nonce = issue(guard)
    guard.submit_register(request(nonce=nonce, nc="00000001", cseq=1, contact=A),
                          now=NOW)
    guard.submit_register(request(nonce=nonce, nc="00000002", cseq=2, contact=B),
                          now=NOW)
    with pytest.raises(sg.SipError) as exc:
        guard.submit_register(
            request(nonce=nonce, nc="00000003", cseq=3, contact=C), now=NOW)
    assert exc.value.code == "SIP_CONTACT_QUOTA"
    assert guard.stats()["rejected"] == 1

    updated = guard.submit_register(
        request(nonce=nonce, nc="00000004", cseq=4, contact=A, expires=120),
        now=NOW)
    assert updated["registered"] is True
    assert updated["created"] is False
    assert len(guard.query_bindings(AOR)) == 2


def test_contact_aor_and_call_id_validation():
    guard = make()
    nonce = issue(guard)
    with pytest.raises(sg.SipError) as exc:
        guard.submit_register(
            request(nonce=nonce, contact="http://alice@example.com"), now=NOW)
    assert exc.value.code == "SIP_BAD_CONTACT"
    with pytest.raises(sg.SipError) as exc2:
        guard.submit_register(
            request(nonce=nonce, cseq=2, contact="sip:alice@10.0.0.5:5060\r\nX: 1"),
            now=NOW)
    assert exc2.value.code == "SIP_BAD_CONTACT"

    unknown = request(nonce=nonce, cseq=3)
    unknown["aor"] = "sip:nobody@example.com"
    with pytest.raises(sg.SipError) as exc3:
        guard.submit_register(unknown, now=NOW)
    assert exc3.value.code == "SIP_UNKNOWN_USER"

    no_call = request(nonce=nonce, cseq=4)
    del no_call["call_id"]
    with pytest.raises(sg.SipError) as exc4:
        guard.submit_register(no_call, now=NOW)
    assert exc4.value.code == "SIP_BAD_CALL_ID"
    assert guard.stats()["rejected"] == 4


def test_expire_bindings_sweeps_only_due_entries():
    guard = make()
    nonce = issue(guard)
    guard.submit_register(
        request(nonce=nonce, nc="00000001", cseq=1, contact=A, expires=60),
        now=NOW)
    guard.submit_register(
        request(nonce=nonce, nc="00000002", cseq=2, contact=B, expires=300),
        now=NOW)
    assert guard.expire_bindings(now=NOW) == 0
    assert guard.expire_bindings(now="2026-11-23T10:02:00+00:00") == 1
    assert [row["contact"] for row in guard.query_bindings(AOR)] == [B]
    assert guard.stats()["expired"] == 1
    assert guard.expire_bindings(now="2026-11-23T10:06:00+00:00") == 1
    assert guard.query_bindings(AOR) == []
    assert guard.stats()["expired"] == 2


def test_snapshot_restore_keeps_bindings_and_nonce_counters():
    guard = make()
    nonce = issue(guard)
    guard.submit_register(request(nonce=nonce, nc="00000001", cseq=1, contact=A),
                          now=NOW)
    state = guard.snapshot()
    guard.submit_register(request(nonce=nonce, nc="00000002", cseq=2, contact=B),
                          now=NOW)
    assert len(guard.query_bindings(AOR)) == 2

    restored = sg.SipRegistrationGuard()
    restored.restore(state)
    assert [row["contact"] for row in restored.query_bindings(AOR)] == [A]
    assert restored.stats()["registered"] == 1

    with pytest.raises(sg.SipError) as exc:
        restored.submit_register(
            request(nonce=nonce, nc="00000001", cseq=3, contact=C), now=NOW)
    assert exc.value.code == "SIP_REPLAY"

    added = restored.submit_register(
        request(nonce=nonce, nc="00000002", cseq=3, contact=B), now=NOW)
    assert added["created"] is True
    assert [row["contact"] for row in restored.query_bindings(AOR)] == [A, B]

    corrupt = state.copy()
    corrupt["version"] = 2
    bad_bindings = state.copy()
    bad_bindings["bindings"] = []
    bad_stats = state.copy()
    bad_stats["stats"] = {"registered": 1}
    bad_nonce = state.copy()
    bad_nonce["nonces"] = {"deadbeef": {"realm": REALM, "issued_at": "x",
                                       "expires_at": "not-a-time",
                                       "algorithm": "MD5", "qop": "auth"}}
    for bad in (None, {}, corrupt, bad_bindings, bad_stats, bad_nonce):
        with pytest.raises(sg.SipError) as err:
            restored.restore(bad)
        assert err.value.code == "SIP_SNAPSHOT_INVALID"
    assert len(restored.query_bindings(AOR)) == 2
    assert restored.stats()["registered"] == 2


def test_stats_are_exact_and_reset_keeps_bindings():
    guard = make()
    nonce = issue(guard)
    guard.submit_register(request(nonce=nonce, nc="00000001", cseq=1, contact=A),
                          now=NOW)
    guard.submit_register(request(nonce=nonce, nc="00000002", cseq=2, contact=B),
                          now=NOW)
    guard.remove_binding(AOR, contact=A)
    with pytest.raises(sg.SipError):
        guard.submit_register(
            request(nonce=nonce, nc="00000003", cseq=3, contact=C,
                    auth=digest(nonce=nonce, nc="00000003", password="bad")),
            now=NOW)
    with pytest.raises(sg.SipError):
        guard.submit_register(
            request(nonce=nonce, nc="00000001", cseq=4, contact=C), now=NOW)
    assert guard.stats() == {
        "registered": 2,
        "deregistered": 1,
        "auth_failed": 1,
        "replayed": 1,
        "expired": 0,
        "rejected": 0,
        "retransmitted": 0,
    }
    guard.reset_stats()
    assert guard.stats() == {name: 0 for name in (
        "registered", "deregistered", "auth_failed", "replayed", "expired",
        "rejected", "retransmitted")}
    assert [row["contact"] for row in guard.query_bindings(AOR)] == [B]


def test_concurrent_retransmissions_register_exactly_once():
    guard = make()
    nonces = [issue(guard) for _ in range(8)]
    barrier = threading.Barrier(8)
    results = []
    failures = []
    lock = threading.Lock()

    def worker(nonce):
        barrier.wait()
        try:
            out = guard.submit_register(
                request(nonce=nonce, nc="00000001", cseq=1, contact=A),
                now=NOW)
        except Exception as exc:  # pragma: no cover
            with lock:
                failures.append(exc)
            return
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(nonce,))
               for nonce in nonces]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert sum(1 for row in results if row["retransmission"] is False) == 1
    assert sum(1 for row in results if row["retransmission"] is True) == 7
    assert guard.stats()["registered"] == 1
    assert guard.stats()["retransmitted"] == 7
    assert len(guard.query_bindings(AOR)) == 1
