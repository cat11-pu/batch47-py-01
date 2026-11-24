import threading
from datetime import datetime, timedelta

import pytest

import oidc_backchannel_logout_guard as og

NOW = "2026-11-24T10:00:00+00:00"
FAR = "2026-11-24T12:00:00+00:00"
_DROP = object()


def ts(offset=0):
    return int((datetime.fromisoformat(NOW) + timedelta(seconds=offset)).timestamp())


def token(**overrides):
    base = {
        "iss": "https://idp.example.com",
        "aud": "rp-portal",
        "sub": "user-1",
        "sid": "sid-1",
        "iat": ts(-30),
        "exp": ts(120),
        "jti": "jti-1",
        "alg": "RS256",
        "fingerprint": "SHA256:good",
        "events": {og.LOGOUT_EVENT: {}},
    }
    for key, value in overrides.items():
        if value is _DROP:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def make(**kwargs):
    guard = og.BackchannelLogoutGuard(**kwargs)
    guard.register_client(
        "rp-portal",
        issuer="https://idp.example.com",
        audiences=("rp-portal", "rp-portal-alt"),
        logout_endpoint="https://rp.example.com/backchannel",
    )
    guard.trust_issuer(
        "https://idp.example.com",
        fingerprints=("SHA256:good", "SHA256:next"),
    )
    return guard


def test_valid_sid_token_revokes_session_and_records_jti():
    guard = make()
    guard.start_session("sess-1", client_id="rp-portal", subject="user-1", sid="sid-1")

    preview = guard.validate_logout_token(token(), client_id="rp-portal", now=NOW)
    assert preview["status"] == "valid"
    assert preview["jti"] == "jti-1"
    assert preview["issuer"] == "https://idp.example.com"
    assert preview["client_id"] == "rp-portal"
    assert preview["subject"] == "user-1"
    assert preview["sid"] == "sid-1"
    assert preview["targets"] == ("sess-1",)
    assert guard.is_session_active("sess-1") is True
    assert guard.validate_logout_token(token(), client_id="rp-portal", now=NOW)["targets"] == ("sess-1",)

    result = guard.apply_logout(token(), client_id="rp-portal", now=NOW)
    assert result["status"] == "accepted"
    assert result["code"] == "OIDC_LOGOUT_OK"
    assert result["jti"] == "jti-1"
    assert result["client_id"] == "rp-portal"
    assert result["session_ids"] == ("sess-1",)
    assert result["revoked"] == 1
    assert guard.is_session_active("sess-1") is False
    status = guard.session_status("sess-1")
    assert status["session_id"] == "sess-1"
    assert status["client_id"] == "rp-portal"
    assert status["subject"] == "user-1"
    assert status["sid"] == "sid-1"
    assert status["active"] is False
    assert status["revoked_reason"] == "backchannel_logout"
    assert guard.stats() == {
        "accepted": 1,
        "replayed": 0,
        "rejected": 0,
        "sessions_revoked": 1,
    }


def test_replayed_jti_is_reported_and_counted_once():
    guard = make()
    guard.start_session("sess-1", client_id="rp-portal", subject="user-1", sid="sid-1")
    first = guard.apply_logout(token(), client_id="rp-portal", now=NOW)
    assert first["status"] == "accepted"
    assert first["revoked"] == 1

    second = guard.apply_logout(token(), client_id="rp-portal", now=NOW)
    assert second["status"] == "replayed"
    assert second["code"] == "OIDC_LOGOUT_REPLAY"
    assert second["jti"] == "jti-1"
    assert second["client_id"] == "rp-portal"
    assert second["session_ids"] == ()
    assert guard.stats() == {
        "accepted": 1,
        "replayed": 1,
        "rejected": 0,
        "sessions_revoked": 1,
    }
    assert guard.is_session_active("sess-1") is False

    guard.reset_stats()
    assert guard.stats() == {
        "accepted": 0,
        "replayed": 0,
        "rejected": 0,
        "sessions_revoked": 0,
    }


def test_client_registration_conflicts_and_unknown_client():
    guard = make()
    same = guard.register_client(
        "rp-portal",
        issuer="https://idp.example.com",
        audiences=("rp-portal", "rp-portal-alt"),
        logout_endpoint="https://rp.example.com/backchannel",
    )
    assert same["client_id"] == "rp-portal"
    assert same["issuer"] == "https://idp.example.com"
    assert same["audiences"] == ("rp-portal", "rp-portal-alt")

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.register_client(
            "rp-portal",
            issuer="https://idp.example.com",
            audiences=("rp-portal", "rp-portal-alt"),
            logout_endpoint="https://rp.example.com/other",
        )
    assert exc.value.code == "OIDC_CLIENT_CONFLICT"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(), client_id="rp-unknown", now=NOW)
    assert exc.value.code == "OIDC_UNKNOWN_CLIENT"
    assert guard.stats()["rejected"] == 1


def test_issuer_untrusted_revoked_and_unknown():
    guard = make()
    guard.start_session("sess-1", client_id="rp-portal", subject="user-1", sid="sid-1")

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(iss="https://idp.evil.example", jti="jti-evil"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_UNTRUSTED_ISSUER"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.revoke_issuer("https://idp.none.example")
    assert exc.value.code == "OIDC_UNKNOWN_ISSUER"

    assert guard.revoke_issuer("https://idp.example.com") is None
    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_ISSUER_REVOKED"
    assert guard.stats()["rejected"] == 2


def test_issuer_mismatch_and_audience_checks():
    guard = make()
    guard.register_client(
        "rp-other",
        issuer="https://idp.other.example",
        audiences=("rp-other",),
    )
    guard.trust_issuer("https://idp.other.example", fingerprints=("SHA256:good",))

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(iss="https://idp.other.example", aud="rp-other", jti="jti-mismatch"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_ISSUER_MISMATCH"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(aud=("rp-portal-other",), jti="jti-aud"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_AUDIENCE_MISMATCH"

    listed = guard.apply_logout(
        token(aud=("legacy-sp", "rp-portal"), sid=_DROP, jti="jti-aud-listed"),
        client_id="rp-portal",
        now=NOW,
    )
    assert listed["status"] == "accepted"
    single = guard.apply_logout(
        token(aud="rp-portal", sid=_DROP, jti="jti-aud-single"),
        client_id="rp-portal",
        now=NOW,
    )
    assert single["status"] == "accepted"


def test_missing_claims_and_bad_events_claim():
    guard = make()
    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(exp=_DROP), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_MALFORMED_TOKEN"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(jti="", sid=_DROP), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_MALFORMED_TOKEN"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(events=_DROP, sid=_DROP), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_BAD_EVENTS"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(events={"http://example.com/other": {}}, sid=_DROP),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_BAD_EVENTS"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(events={og.LOGOUT_EVENT: "yes"}, sid=_DROP),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_BAD_EVENTS"
    assert guard.stats()["rejected"] == 5


def test_nonce_and_missing_subject_rejected():
    guard = make()
    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(nonce="nonce-1", sid=_DROP), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_NONCE_PRESENT"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(sub=_DROP, sid=_DROP), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_SUBJECT_REQUIRED"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(sub="", sid=""), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_SUBJECT_REQUIRED"
    assert guard.stats()["rejected"] == 3


def test_signature_and_algorithm_rejections():
    guard = make()
    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(fingerprint="SHA256:old", sid=_DROP, jti="jti-old"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_SIGNATURE_UNTRUSTED"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(alg="HS256", sid=_DROP, jti="jti-hs"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_ALG_NOT_ALLOWED"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(alg=_DROP, sid=_DROP, jti="jti-noalg"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_MALFORMED_TOKEN"

    accepted = guard.apply_logout(
        token(alg="RS256", sid=_DROP, jti="jti-alg-ok"),
        client_id="rp-portal",
        now=NOW,
    )
    assert accepted["status"] == "accepted"


def test_time_window_boundaries_use_clock_skew():
    guard = make()
    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(iat=ts(120), exp=ts(300), sid=_DROP, jti="jti-future"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_NOT_YET_VALID"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(iat=ts(-300), exp=ts(-60), sid=_DROP, jti="jti-expired"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_TOKEN_EXPIRED"

    edge = guard.apply_logout(
        token(iat=ts(-300), exp=ts(-59), sid=_DROP, jti="jti-edge"),
        client_id="rp-portal",
        now=NOW,
    )
    assert edge["status"] == "accepted"
    assert guard.stats() == {
        "accepted": 1,
        "replayed": 0,
        "rejected": 2,
        "sessions_revoked": 0,
    }


def test_sid_lookup_and_subject_wide_revocation():
    guard = make()
    guard.start_session("sess-a", client_id="rp-portal", subject="alice", sid="sid-a")
    guard.start_session("sess-b", client_id="rp-portal", subject="alice", sid="sid-b")
    guard.start_session("sess-c", client_id="rp-portal", subject="bob", sid="sid-c")

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(sid="sid-missing", sub="alice", jti="jti-ghost"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_SID_UNKNOWN"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(
            token(sid="sid-a", sub="bob", jti="jti-swap"),
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_SUBJECT_MISMATCH"

    result = guard.apply_logout(
        token(sub="alice", sid=_DROP, jti="jti-alice"),
        client_id="rp-portal",
        now=NOW,
    )
    assert result["status"] == "accepted"
    assert result["session_ids"] == ("sess-a", "sess-b")
    assert result["revoked"] == 2
    assert guard.is_session_active("sess-a") is False
    assert guard.is_session_active("sess-b") is False
    assert guard.is_session_active("sess-c") is True


def test_jti_cache_limit_blocks_new_token_until_purge():
    guard = make(max_jti=2)
    assert guard.apply_logout(
        token(sid=_DROP, jti="jti-1"), client_id="rp-portal", now=NOW
    )["status"] == "accepted"
    assert guard.apply_logout(
        token(sid=_DROP, jti="jti-2"), client_id="rp-portal", now=NOW
    )["status"] == "accepted"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout(token(sid=_DROP, jti="jti-3"), client_id="rp-portal", now=NOW)
    assert exc.value.code == "OIDC_JTI_FULL"
    assert guard.purge(now=NOW) == 0
    assert guard.purge(now=FAR) == 2

    resumed = guard.apply_logout(
        token(sid=_DROP, jti="jti-3"), client_id="rp-portal", now=NOW
    )
    assert resumed["status"] == "accepted"
    assert guard.stats()["rejected"] == 1


def test_batch_isolates_failures_and_enforces_limit():
    guard = make(batch_limit=3)
    tokens = [
        token(sid=_DROP, jti="jti-b1"),
        token(sid=_DROP, jti="jti-b2", fingerprint="SHA256:old"),
        token(sid=_DROP, jti="jti-b1"),
    ]
    results = guard.apply_logout_batch(tokens, client_id="rp-portal", now=NOW)
    assert [item["index"] for item in results] == [0, 1, 2]
    assert [item["status"] for item in results] == ["accepted", "rejected", "replayed"]
    assert results[1]["code"] == "OIDC_SIGNATURE_UNTRUSTED"
    assert results[2]["code"] == "OIDC_LOGOUT_REPLAY"
    assert guard.stats() == {
        "accepted": 1,
        "replayed": 1,
        "rejected": 1,
        "sessions_revoked": 0,
    }

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.apply_logout_batch(
            tokens + [token(sid=_DROP, jti="jti-b4")],
            client_id="rp-portal",
            now=NOW,
        )
    assert exc.value.code == "OIDC_BATCH_TOO_LARGE"
    assert guard.stats() == {
        "accepted": 1,
        "replayed": 1,
        "rejected": 1,
        "sessions_revoked": 0,
    }


def test_snapshot_restore_preserves_replay_and_isolation():
    guard = make()
    guard.start_session("sess-1", client_id="rp-portal", subject="user-1", sid="sid-1")
    guard.apply_logout(token(), client_id="rp-portal", now=NOW)
    snap = guard.snapshot()
    guard.apply_logout(
        token(sid=_DROP, jti="jti-after-snapshot"), client_id="rp-portal", now=NOW
    )

    clone = og.BackchannelLogoutGuard()
    clone.restore(snap)
    assert clone.stats() == {
        "accepted": 1,
        "replayed": 0,
        "rejected": 0,
        "sessions_revoked": 1,
    }
    replay = clone.apply_logout(token(), client_id="rp-portal", now=NOW)
    assert replay["status"] == "replayed"
    assert clone.is_session_active("sess-1") is False

    with pytest.raises(og.OidcLogoutError) as exc:
        clone.restore({"clients": {}, "issuers": {}, "revoked": [], "sessions": {}})
    assert exc.value.code == "OIDC_SNAPSHOT_INVALID"
    assert clone.stats()["accepted"] == 1
    assert clone.stats()["replayed"] == 1
    assert clone.is_session_active("sess-1") is False

    resumed = clone.apply_logout(
        token(sid=_DROP, jti="jti-after-snapshot"), client_id="rp-portal", now=NOW
    )
    assert resumed["status"] == "accepted"
    assert clone.stats()["accepted"] == 2


def test_session_capacity_conflicts_and_unknown_lookup():
    guard = make(session_capacity=2)
    guard.start_session("sess-1", client_id="rp-portal", subject="user-1", sid="sid-1")
    guard.start_session("sess-2", client_id="rp-portal", subject="user-1", sid="sid-2")

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.start_session("sess-3", client_id="rp-portal", subject="user-2", sid="sid-3")
    assert exc.value.code == "OIDC_SESSION_FULL"

    with pytest.raises(og.OidcLogoutError) as exc:
        guard.start_session("sess-1", client_id="rp-portal", subject="user-9", sid="sid-9")
    assert exc.value.code == "OIDC_SESSION_CONFLICT"

    assert guard.is_session_active("sess-missing") is False
    with pytest.raises(og.OidcLogoutError) as exc:
        guard.session_status("sess-missing")
    assert exc.value.code == "OIDC_UNKNOWN_SESSION"

    guard.apply_logout(
        token(sid="sid-1", sub="user-1", jti="jti-s1"), client_id="rp-portal", now=NOW
    )
    assert guard.is_session_active("sess-1") is False

    started = guard.start_session("sess-3", client_id="rp-portal", subject="user-2", sid="sid-3")
    assert started["session_id"] == "sess-3"
    assert started["active"] is True

    again = guard.start_session("sess-2", client_id="rp-portal", subject="user-1", sid="sid-2")
    assert again["session_id"] == "sess-2"
    assert again["active"] is True


def test_concurrent_logout_of_same_jti_accepts_once():
    guard = make()
    guard.start_session("sess-1", client_id="rp-portal", subject="user-1", sid="sid-1")
    barrier = threading.Barrier(8)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(guard.apply_logout(token(), client_id="rp-portal", now=NOW))
        except BaseException as err:  # noqa: BLE001
            errors.append(err)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    statuses = [item["status"] for item in results]
    assert statuses.count("accepted") == 1
    assert statuses.count("replayed") == 7
    assert guard.stats() == {
        "accepted": 1,
        "replayed": 7,
        "rejected": 0,
        "sessions_revoked": 1,
    }
