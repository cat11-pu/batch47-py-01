import copy
import threading

import pytest

import macaroon_caveat_guard as mg

NOW = "2026-11-24T09:00:00+00:00"
EXPIRES = "2026-11-24T09:10:00+00:00"


def make(**kwargs):
    guard = mg.MacaroonCaveatGuard(**kwargs)
    guard.rotate_root_key("k1", b"root-secret-one")
    return guard


def token_args(**overrides):
    base = {
        "location": "https://authz.example.com",
        "identifier": "user-1",
        "caveats": [
            {"kind": "scope", "value": "read"},
            {"kind": "context", "key": "tenant", "value": "t1"},
            {"kind": "expires", "at": EXPIRES},
        ],
        "key_id": "k1",
    }
    base.update(overrides)
    return base


def test_mint_and_authorize_records_audit_counters():
    guard = make()
    token = guard.mint(**token_args())
    assert token["version"] == 1
    assert token["identifier"] == "user-1"
    assert token["key_id"] == "k1"
    assert token["binding"] is None
    assert isinstance(token["signature"], str) and len(token["signature"]) >= 32

    out = guard.authorize(
        token,
        context={"scopes": ["read", "write"], "tenant": "t1"},
        now=NOW,
    )
    assert out["status"] == "ok"
    assert out["identifier"] == "user-1"
    assert out["key_id"] == "k1"
    assert out["caveat_count"] == 3
    assert guard.stats() == {
        "minted": 1, "authorized": 1, "denied": 0, "attenuated": 0,
    }

    with pytest.raises(mg.MacaroonError) as exc:
        guard.mint(**token_args())
    assert exc.value.code == "MACAROON_DUPLICATE_IDENTIFIER"
    assert guard.stats()["minted"] == 1


def test_tampered_or_malformed_token_is_refused():
    guard = make()
    token = guard.mint(**token_args())

    widened = copy.deepcopy(token)
    widened["caveats"][0]["value"] = "admin"
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(widened, context={"scopes": ["admin"], "tenant": "t1"}, now=NOW)
    assert exc.value.code == "MACAROON_SIGNATURE_INVALID"

    resigned = copy.deepcopy(token)
    resigned["signature"] = "0" * 64
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(resigned, context={"scopes": ["read"], "tenant": "t1"}, now=NOW)
    assert exc.value.code == "MACAROON_SIGNATURE_INVALID"

    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize({"identifier": "user-1"}, context={}, now=NOW)
    assert exc.value.code == "MACAROON_INVALID_TOKEN"
    assert guard.stats()["denied"] == 3


def test_attenuation_appends_without_mutating_source_token():
    guard = make()
    token = guard.mint(location="https://authz.example.com", identifier="user-2",
                       caveats=(), key_id="k1")
    assert token["caveats"] == []

    tighter = guard.attenuate(token, {"kind": "scope", "value": "admin"})
    assert len(tighter["caveats"]) == 1
    assert token["caveats"] == []
    assert tighter["signature"] != token["signature"]
    assert guard.authorize(token, context={}, now=NOW)["status"] == "ok"

    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(tighter, context={"scopes": ["read"]}, now=NOW)
    assert exc.value.code == "MACAROON_SCOPE_MISSING"
    assert guard.authorize(tighter, context={"scopes": ["admin"]}, now=NOW)["status"] == "ok"
    assert guard.stats()["attenuated"] == 1


def test_every_scope_caveat_must_be_satisfied():
    guard = make()
    token = guard.mint(location="https://authz.example.com", identifier="user-3",
                       caveats=[{"kind": "scope", "value": "read"},
                                {"kind": "scope", "value": "write"}],
                       key_id="k1")
    assert guard.authorize(token, context={"scopes": ["read", "write"]}, now=NOW)["status"] == "ok"

    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(token, context={"scopes": ["read"]}, now=NOW)
    assert exc.value.code == "MACAROON_SCOPE_MISSING"
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(token, context={}, now=NOW)
    assert exc.value.code == "MACAROON_SCOPE_MISSING"
    assert guard.stats()["authorized"] == 1
    assert guard.stats()["denied"] == 2


def test_expiry_boundary_absorbs_clock_skew():
    guard = make(clock_skew=30)
    token = guard.mint(location="https://authz.example.com", identifier="user-4",
                       caveats=[{"kind": "expires", "at": EXPIRES}], key_id="k1")
    assert guard.authorize(token, context={}, now="2026-11-24T09:10:29+00:00")["status"] == "ok"
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(token, context={}, now="2026-11-24T09:10:30+00:00")
    assert exc.value.code == "MACAROON_EXPIRED"


def test_caveat_ceiling_applies_to_mint_and_attenuation():
    guard = make(max_caveats=2)
    with pytest.raises(mg.MacaroonError) as exc:
        guard.mint(location="https://authz.example.com", identifier="user-5",
                   caveats=[{"kind": "scope", "value": "a"},
                            {"kind": "scope", "value": "b"},
                            {"kind": "scope", "value": "c"}],
                   key_id="k1")
    assert exc.value.code == "MACAROON_TOO_MANY_CAVEATS"
    assert guard.stats()["minted"] == 0

    token = guard.mint(location="https://authz.example.com", identifier="user-5b",
                       caveats=[{"kind": "scope", "value": "a"},
                                {"kind": "scope", "value": "b"}],
                       key_id="k1")
    with pytest.raises(mg.MacaroonError) as exc:
        guard.attenuate(token, {"kind": "scope", "value": "c"})
    assert exc.value.code == "MACAROON_TOO_MANY_CAVEATS"
    with pytest.raises(mg.MacaroonError) as exc:
        guard.attenuate(token, {"kind": "ip", "value": "10.0.0.7"})
    assert exc.value.code == "MACAROON_CAVEAT_INVALID"
    assert len(token["caveats"]) == 2
    assert guard.stats()["minted"] == 1
    assert guard.stats()["attenuated"] == 0


def test_third_party_caveat_needs_bound_discharge():
    guard = make()
    root = guard.mint(
        location="https://authz.example.com",
        identifier="user-6",
        caveats=[{"kind": "third_party", "location": "https://authz.example.com",
                  "caveat_key": "cav-1"}],
        key_id="k1",
    )
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(root, context={"dept": "eng"}, now=NOW)
    assert exc.value.code == "MACAROON_DISCHARGE_REQUIRED"

    discharge = guard.mint(
        location="https://authz.example.com",
        identifier="cav-1",
        caveats=[{"kind": "context", "key": "dept", "value": "eng"}],
        key_id="k1",
    )
    discharge = guard.bind(discharge, binding=root["signature"])
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(root, context={"dept": "sales"}, discharges=[discharge], now=NOW)
    assert exc.value.code == "MACAROON_DISCHARGE_INVALID"
    assert guard.authorize(root, context={"dept": "eng"}, discharges=[discharge],
                           now=NOW)["status"] == "ok"

    spare_root = guard.mint(
        location="https://authz.example.com",
        identifier="user-6b",
        caveats=[{"kind": "third_party", "location": "https://authz.example.com",
                  "caveat_key": "cav-9"}],
        key_id="k1",
    )
    stray = guard.bind(
        guard.mint(location="https://authz.example.com", identifier="cav-9",
                   caveats=[], key_id="k1"),
        binding=root["signature"],
    )
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(spare_root, context={}, discharges=[stray], now=NOW)
    assert exc.value.code == "MACAROON_DISCHARGE_INVALID"

    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(root, context={"dept": "eng"}, discharges=[stray], now=NOW)
    assert exc.value.code == "MACAROON_DISCHARGE_REQUIRED"


def test_channel_binding_is_signed_and_single_assignment():
    guard = make()
    token = guard.mint(**token_args())
    bound = guard.bind(token, binding="tls:abc")
    assert token["binding"] is None
    assert bound["binding"] == "tls:abc"
    assert bound["signature"] != token["signature"]

    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(bound, context={"scopes": ["read"], "tenant": "t1",
                                        "binding": "tls:zzz"}, now=NOW)
    assert exc.value.code == "MACAROON_BINDING_MISMATCH"
    with pytest.raises(mg.MacaroonError) as exc:
        guard.bind(bound, binding="tls:other")
    assert exc.value.code == "MACAROON_ALREADY_BOUND"

    ok = guard.authorize(bound, context={"scopes": ["read"], "tenant": "t1",
                                         "binding": "tls:abc"}, now=NOW)
    assert ok["status"] == "ok"

    forged = copy.deepcopy(bound)
    forged["binding"] = "tls:zzz"
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(forged, context={"scopes": ["read"], "tenant": "t1",
                                         "binding": "tls:zzz"}, now=NOW)
    assert exc.value.code == "MACAROON_SIGNATURE_INVALID"


def test_identifier_revocation_is_sticky_and_idempotent():
    guard = make()
    token = guard.mint(**token_args())
    assert guard.is_revoked("user-1") is False
    guard.revoke("user-1")
    guard.revoke("user-1")
    assert guard.is_revoked("user-1") is True
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(token, context={"scopes": ["read"], "tenant": "t1"}, now=NOW)
    assert exc.value.code == "MACAROON_REVOKED"
    assert guard.stats()["denied"] == 1
    assert guard.stats()["authorized"] == 0


def test_root_key_rotation_conflicts_and_revocation():
    guard = mg.MacaroonCaveatGuard()
    with pytest.raises(mg.MacaroonError) as exc:
        guard.mint(location="https://authz.example.com", identifier="user-7", caveats=[])
    assert exc.value.code == "MACAROON_UNKNOWN_KEY"

    guard.rotate_root_key("k1", b"root-secret-one")
    first = guard.mint(location="https://authz.example.com", identifier="user-7",
                       caveats=[], key_id="k1")
    guard.rotate_root_key("k2", b"root-secret-two")
    second = guard.mint(location="https://authz.example.com", identifier="user-8", caveats=[])
    assert second["key_id"] == "k2"
    assert guard.authorize(first, context={}, now=NOW)["status"] == "ok"

    guard.rotate_root_key("k1", b"root-secret-one")
    with pytest.raises(mg.MacaroonError) as exc:
        guard.rotate_root_key("k1", b"root-secret-other")
    assert exc.value.code == "MACAROON_KEY_CONFLICT"

    guard.revoke_root_key("k1")
    with pytest.raises(mg.MacaroonError) as exc:
        guard.authorize(first, context={}, now=NOW)
    assert exc.value.code == "MACAROON_KEY_REVOKED"
    assert guard.authorize(second, context={}, now=NOW)["status"] == "ok"
    with pytest.raises(mg.MacaroonError) as exc:
        guard.revoke_root_key("k9")
    assert exc.value.code == "MACAROON_UNKNOWN_KEY"


def test_ledger_capacity_counts_only_live_entries():
    guard = make(max_tokens=2)
    guard.mint(location="https://authz.example.com", identifier="led-1",
               caveats=[{"kind": "expires", "at": "2026-11-24T09:05:00+00:00"}], key_id="k1")
    guard.mint(location="https://authz.example.com", identifier="led-2",
               caveats=[{"kind": "expires", "at": "2026-11-24T09:06:00+00:00"}], key_id="k1")
    with pytest.raises(mg.MacaroonError) as exc:
        guard.mint(location="https://authz.example.com", identifier="led-3",
                   caveats=[{"kind": "expires", "at": "2026-11-24T09:07:00+00:00"}],
                   key_id="k1")
    assert exc.value.code == "MACAROON_LEDGER_FULL"
    assert guard.stats()["minted"] == 2

    assert guard.purge(now="2026-11-24T09:05:29+00:00") == 0
    assert guard.purge(now="2026-11-24T09:06:00+00:00") == 1
    assert guard.purge(now="2026-11-24T09:07:00+00:00") == 1
    fresh = guard.mint(location="https://authz.example.com", identifier="led-3",
                       caveats=[{"kind": "expires", "at": "2026-11-24T09:30:00+00:00"}],
                       key_id="k1")
    assert guard.authorize(fresh, context={}, now="2026-11-24T09:08:00+00:00")["status"] == "ok"


def test_inspect_summary_is_detached_from_live_token():
    guard = make()
    token = guard.mint(**token_args())
    info = guard.inspect(token)
    assert info["identifier"] == "user-1"
    assert info["key_id"] == "k1"
    assert info["location"] == "https://authz.example.com"
    assert info["caveat_count"] == 3
    assert info["kinds"] == ["scope", "context", "expires"]

    info["caveats"][0]["value"] = "admin"
    info["caveats"].append({"kind": "scope", "value": "root"})
    assert token["caveats"][0]["value"] == "read"
    assert guard.authorize(token, context={"scopes": ["read"], "tenant": "t1"},
                           now=NOW)["status"] == "ok"


def test_snapshot_restore_keeps_keys_ledger_and_audit():
    guard = make()
    first = guard.mint(**token_args())
    guard.revoke("user-1")
    second = guard.mint(location="https://authz.example.com", identifier="user-8",
                        caveats=[{"kind": "expires", "at": EXPIRES}], key_id="k1")
    state = guard.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine["ledger"]) == {"user-1", "user-8"}
    assert pristine["stats"]["minted"] == 2

    state["ledger"].clear()
    state["stats"]["minted"] = 999
    assert guard.stats()["minted"] == 2

    restored = mg.MacaroonCaveatGuard()
    restored.restore(pristine)
    assert restored.stats() == {"minted": 2, "authorized": 0, "denied": 0, "attenuated": 0}
    assert restored.is_revoked("user-1") is True
    with pytest.raises(mg.MacaroonError) as exc:
        restored.authorize(first, context={"scopes": ["read"], "tenant": "t1"}, now=NOW)
    assert exc.value.code == "MACAROON_REVOKED"

    assert restored.authorize(second, context={}, now=NOW)["status"] == "ok"
    third = restored.mint(location="https://authz.example.com", identifier="user-9",
                          caveats=[], key_id="k1")
    assert third["key_id"] == "k1"


def test_corrupt_snapshot_leaves_live_state_untouched():
    guard = make()
    token = guard.mint(**token_args())
    bad_states = [
        None,
        {},
        {"version": 2, "keys": {}, "ledger": {}, "revoked": [], "stats": {}},
        {"version": 1, "keys": "nope", "ledger": {}, "revoked": [], "stats": {}},
        {"version": 1, "keys": {}, "ledger": {}, "revoked": "nope", "stats": {}},
        {"version": 1, "keys": {}, "ledger": {}, "revoked": [],
         "stats": {"minted": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(mg.MacaroonError) as exc:
            guard.restore(bad)
        assert exc.value.code == "MACAROON_SNAPSHOT_INVALID"

    assert guard.stats()["minted"] == 1
    assert guard.authorize(token, context={"scopes": ["read"], "tenant": "t1"},
                           now=NOW)["status"] == "ok"


def test_concurrent_mint_and_authorize_keep_counts_exact():
    guard = make(max_tokens=200)
    errors = []

    def worker(index):
        try:
            for round_no in range(3):
                ident = f"race-{index}-{round_no}"
                token = guard.mint(location="https://authz.example.com",
                                   identifier=ident, caveats=[], key_id="k1")
                guard.authorize(token, context={}, now=NOW)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append((index, repr(exc)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert guard.stats()["minted"] == 30
    assert guard.stats()["authorized"] == 30
    assert len(guard.snapshot()["ledger"]) == 30
