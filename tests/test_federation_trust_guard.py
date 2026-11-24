import pytest

import federation_trust_guard as ftg

NOW = "2026-11-23T10:00:00+00:00"
SOON = "2026-11-23T10:05:00+00:00"
LATER = "2026-11-23T10:06:00+00:00"
EXPIRY = "2026-11-23T11:00:00+00:00"

ANCHOR = "https://anchor.example"
MID = "https://mid.example"
LEAF = "https://leaf.example"


def make_guard(**kwargs):
    guard = ftg.FederationTrustGuard(**kwargs)
    guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                           expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                   jwks_fingerprint="FP-ANCHOR",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(MID, issuer=MID, jwks_fingerprint="FP-MID",
                                   authority_hints=(ANCHOR,),
                                   expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(LEAF, issuer=LEAF, jwks_fingerprint="FP-LEAF",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_subordinate_statement(ANCHOR, subject=MID,
                                    jwks_fingerprint="FP-MID",
                                    expires_at=EXPIRY, now=NOW)
    guard.add_subordinate_statement(MID, subject=LEAF,
                                    jwks_fingerprint="FP-LEAF",
                                    expires_at=EXPIRY, now=NOW)
    return guard


def test_resolve_chain_and_trusted_entities():
    guard = make_guard()
    assert guard.resolve_chain(LEAF, now=NOW) == [ANCHOR, MID, LEAF]
    assert guard.resolve_chain(ANCHOR, now=NOW) == [ANCHOR]
    assert guard.trusted_entities(now=NOW) == [ANCHOR, LEAF, MID]
    assert guard.stats() == {"resolved": 2, "verified": 0,
                             "revoked": 0, "rejected": 0}


def test_unknown_entity_missing_anchor_and_missing_path():
    guard = make_guard()
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain("https://ghost.example", now=NOW)
    assert exc.value.code == "FED_UNKNOWN_ENTITY"
    assert guard.is_revoked("https://ghost.example") is False

    lonely = ftg.FederationTrustGuard()
    lonely.add_entity_configuration(LEAF, issuer=LEAF, jwks_fingerprint="FP-LEAF",
                                    expires_at=EXPIRY, now=NOW)
    with pytest.raises(ftg.FederationError) as exc:
        lonely.resolve_chain(LEAF, now=NOW)
    assert exc.value.code == "FED_NO_TRUST_ANCHOR"

    disconnected = ftg.FederationTrustGuard()
    disconnected.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                                  expires_at=EXPIRY, now=NOW)
    disconnected.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                          jwks_fingerprint="FP-ANCHOR",
                                          expires_at=EXPIRY, now=NOW)
    disconnected.add_entity_configuration(MID, issuer=MID, jwks_fingerprint="FP-MID",
                                          expires_at=EXPIRY, now=NOW)
    with pytest.raises(ftg.FederationError) as exc:
        disconnected.resolve_chain(MID, now=NOW)
    assert exc.value.code == "FED_NO_TRUST_PATH"


def test_registration_conflicts_and_invalid_records():
    guard = make_guard()
    anchor = guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                                    expires_at=EXPIRY, now=NOW)
    assert anchor["entity_id"] == ANCHOR
    assert guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                                  expires_at=EXPIRY, now=NOW) == anchor
    with pytest.raises(ftg.FederationError) as exc:
        guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ROTATED",
                               expires_at=EXPIRY, now=NOW)
    assert exc.value.code == "FED_CONFLICT"

    config = guard.add_entity_configuration(MID, issuer=MID,
                                            jwks_fingerprint="FP-MID",
                                            authority_hints=(ANCHOR,),
                                            expires_at=EXPIRY, now=NOW)
    assert config["authority_hints"] == [ANCHOR]
    with pytest.raises(ftg.FederationError) as exc:
        guard.add_entity_configuration(MID, issuer=MID,
                                       jwks_fingerprint="FP-CHANGED",
                                       authority_hints=(ANCHOR,),
                                       expires_at=EXPIRY, now=NOW)
    assert exc.value.code == "FED_CONFLICT"

    statement = guard.add_subordinate_statement(ANCHOR, subject=MID,
                                                jwks_fingerprint="FP-MID",
                                                expires_at=EXPIRY, now=NOW)
    assert statement["issuer"] == ANCHOR
    assert statement["subject"] == MID
    with pytest.raises(ftg.FederationError) as exc:
        guard.add_subordinate_statement(ANCHOR, subject=MID,
                                        jwks_fingerprint="FP-OTHER",
                                        expires_at=EXPIRY, now=NOW)
    assert exc.value.code == "FED_CONFLICT"

    with pytest.raises(ftg.FederationError) as exc:
        guard.add_entity_configuration("https://bad.example",
                                       issuer="https://other.example",
                                       jwks_fingerprint="FP-BAD",
                                       expires_at=EXPIRY, now=NOW)
    assert exc.value.code == "FED_INVALID_CONFIG"
    with pytest.raises(ftg.FederationError) as exc:
        guard.add_subordinate_statement(ANCHOR, subject=ANCHOR,
                                        jwks_fingerprint="FP-ANCHOR",
                                        expires_at=EXPIRY, now=NOW)
    assert exc.value.code == "FED_INVALID_STATEMENT"
    with pytest.raises(ftg.FederationError) as exc:
        guard.add_subordinate_statement("https://unknown.example", subject=MID,
                                        jwks_fingerprint="FP-MID",
                                        expires_at=EXPIRY, now=NOW)
    assert exc.value.code == "FED_UNKNOWN_ENTITY"
    with pytest.raises(ftg.FederationError) as exc:
        guard.add_subordinate_statement(ANCHOR, subject="",
                                        jwks_fingerprint="FP-X",
                                        expires_at=EXPIRY, now=NOW)
    assert exc.value.code == "FED_INVALID_STATEMENT"


def test_chain_length_limit_and_shortest_path_wins():
    short = make_guard(max_chain_length=1)
    assert short.resolve_chain(MID, now=NOW) == [ANCHOR, MID]
    with pytest.raises(ftg.FederationError) as exc:
        short.resolve_chain(LEAF, now=NOW)
    assert exc.value.code == "FED_CHAIN_TOO_LONG"

    guard = make_guard()
    alt = "https://alt.example"
    guard.add_trust_anchor(alt, jwks_fingerprint="FP-ALT",
                           expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(alt, issuer=alt, jwks_fingerprint="FP-ALT",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_subordinate_statement(alt, subject=LEAF,
                                    jwks_fingerprint="FP-LEAF",
                                    expires_at=EXPIRY, now=NOW)
    assert guard.resolve_chain(LEAF, now=NOW) == [alt, LEAF]


def test_expired_statement_blocks_chain_until_purged():
    guard = ftg.FederationTrustGuard()
    guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                           expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                   jwks_fingerprint="FP-ANCHOR",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(LEAF, issuer=LEAF,
                                   jwks_fingerprint="FP-LEAF",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_subordinate_statement(ANCHOR, subject=LEAF,
                                    jwks_fingerprint="FP-LEAF",
                                    expires_at=SOON, now=NOW)
    assert guard.resolve_chain(LEAF, now=NOW) == [ANCHOR, LEAF]
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=LATER)
    assert exc.value.code == "FED_EXPIRED"

    assert guard.purge(now=LATER) == 1
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=LATER)
    assert exc.value.code == "FED_NO_TRUST_PATH"


def test_expired_anchor_is_reported_and_removed():
    guard = ftg.FederationTrustGuard()
    guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                           expires_at=SOON, now=NOW)
    guard.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                   jwks_fingerprint="FP-ANCHOR",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(LEAF, issuer=LEAF,
                                   jwks_fingerprint="FP-LEAF",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_subordinate_statement(ANCHOR, subject=LEAF,
                                    jwks_fingerprint="FP-LEAF",
                                    expires_at=EXPIRY, now=NOW)
    assert guard.resolve_chain(LEAF, now=NOW) == [ANCHOR, LEAF]
    assert guard.trusted_entities(now=NOW) == [ANCHOR, LEAF]

    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=LATER)
    assert exc.value.code == "FED_EXPIRED"
    assert guard.trusted_entities(now=LATER) == []

    assert guard.purge(now=LATER) == 1
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=LATER)
    assert exc.value.code == "FED_NO_TRUST_ANCHOR"


def test_authority_hints_reject_disallowed_issuer():
    guard = ftg.FederationTrustGuard()
    guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                           expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                   jwks_fingerprint="FP-ANCHOR",
                                   expires_at=EXPIRY, now=NOW)
    guard.add_entity_configuration(LEAF, issuer=LEAF, jwks_fingerprint="FP-LEAF",
                                   authority_hints=("https://other.example",),
                                   expires_at=EXPIRY, now=NOW)
    guard.add_subordinate_statement(ANCHOR, subject=LEAF,
                                    jwks_fingerprint="FP-LEAF",
                                    expires_at=EXPIRY, now=NOW)
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=NOW)
    assert exc.value.code == "FED_AUTHORITY_MISMATCH"

    permissive = ftg.FederationTrustGuard()
    permissive.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                                expires_at=EXPIRY, now=NOW)
    permissive.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                        jwks_fingerprint="FP-ANCHOR",
                                        expires_at=EXPIRY, now=NOW)
    permissive.add_entity_configuration(LEAF, issuer=LEAF,
                                        jwks_fingerprint="FP-LEAF",
                                        expires_at=EXPIRY, now=NOW)
    permissive.add_subordinate_statement(ANCHOR, subject=LEAF,
                                         jwks_fingerprint="FP-LEAF",
                                         expires_at=EXPIRY, now=NOW)
    assert permissive.resolve_chain(LEAF, now=NOW) == [ANCHOR, LEAF]


def test_revoke_entity_cascades_to_descendants():
    guard = make_guard()
    assert guard.revoke_entity(MID) == 2
    assert guard.is_revoked(MID) is True
    assert guard.is_revoked(LEAF) is True
    assert guard.is_revoked(ANCHOR) is False
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=NOW)
    assert exc.value.code == "FED_REVOKED"
    assert guard.trusted_entities(now=NOW) == [ANCHOR]
    assert guard.revoke_entity(MID) == 0

    with pytest.raises(ftg.FederationError) as exc:
        guard.revoke_entity("https://ghost.example")
    assert exc.value.code == "FED_UNKNOWN_ENTITY"
    assert guard.stats()["revoked"] == 2


def test_revoke_without_cascade_leaves_leaf_untrusted_not_revoked():
    guard = make_guard()
    assert guard.revoke_entity(MID, cascade=False) == 1
    assert guard.is_revoked(MID) is True
    assert guard.is_revoked(LEAF) is False
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=NOW)
    assert exc.value.code == "FED_NO_TRUST_PATH"
    assert guard.trusted_entities(now=NOW) == [ANCHOR]
    assert guard.stats()["revoked"] == 1


def test_verify_entity_fingerprints_and_chain_binding():
    guard = make_guard()
    result = guard.verify_entity(LEAF, expected_fingerprint="FP-LEAF", now=NOW)
    assert result["entity_id"] == LEAF
    assert result["trust_anchor"] == ANCHOR
    assert result["chain"] == [ANCHOR, MID, LEAF]
    assert result["fingerprint"] == "FP-LEAF"
    assert result["expires_at"] == EXPIRY
    assert guard.verify_entity(MID, expected_fingerprint="FP-MID", now=NOW)["chain"] == [ANCHOR, MID]

    with pytest.raises(ftg.FederationError) as exc:
        guard.verify_entity(LEAF, expected_fingerprint="FP-WRONG", now=NOW)
    assert exc.value.code == "FED_FINGERPRINT_MISMATCH"
    with pytest.raises(ftg.FederationError) as exc:
        guard.verify_entity("https://ghost.example", expected_fingerprint="FP", now=NOW)
    assert exc.value.code == "FED_UNKNOWN_ENTITY"
    assert guard.stats()["verified"] == 2
    assert guard.stats()["rejected"] == 2

    broken = ftg.FederationTrustGuard()
    broken.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                            expires_at=EXPIRY, now=NOW)
    broken.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                    jwks_fingerprint="FP-ANCHOR",
                                    expires_at=EXPIRY, now=NOW)
    broken.add_entity_configuration(LEAF, issuer=LEAF,
                                    jwks_fingerprint="FP-LEAF",
                                    expires_at=EXPIRY, now=NOW)
    broken.add_subordinate_statement(ANCHOR, subject=LEAF,
                                     jwks_fingerprint="FP-BAD",
                                     expires_at=EXPIRY, now=NOW)
    with pytest.raises(ftg.FederationError) as exc:
        broken.verify_entity(LEAF, expected_fingerprint="FP-LEAF", now=NOW)
    assert exc.value.code == "FED_FINGERPRINT_MISMATCH"


def test_entity_configuration_expiry():
    guard = make_guard()
    assert guard.resolve_chain(LEAF, now="2026-11-23T10:59:59+00:00") == [ANCHOR, MID, LEAF]
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=EXPIRY)
    assert exc.value.code == "FED_EXPIRED"
    with pytest.raises(ftg.FederationError) as exc:
        guard.verify_entity(LEAF, expected_fingerprint="FP-LEAF", now=EXPIRY)
    assert exc.value.code == "FED_EXPIRED"
    assert guard.stats()["resolved"] == 1
    assert guard.stats()["rejected"] == 2


def test_purge_removes_expired_records():
    guard = ftg.FederationTrustGuard()
    guard.add_trust_anchor(ANCHOR, jwks_fingerprint="FP-ANCHOR",
                           expires_at=SOON, now=NOW)
    guard.add_entity_configuration(ANCHOR, issuer=ANCHOR,
                                   jwks_fingerprint="FP-ANCHOR",
                                   expires_at=SOON, now=NOW)
    guard.add_entity_configuration(LEAF, issuer=LEAF,
                                   jwks_fingerprint="FP-LEAF",
                                   expires_at=SOON, now=NOW)
    guard.add_subordinate_statement(ANCHOR, subject=LEAF,
                                    jwks_fingerprint="FP-LEAF",
                                    expires_at=SOON, now=NOW)
    assert guard.purge(now=NOW) == 0
    assert guard.purge(now=LATER) == 4
    assert guard.purge(now=LATER) == 0
    with pytest.raises(ftg.FederationError) as exc:
        guard.resolve_chain(LEAF, now=LATER)
    assert exc.value.code == "FED_UNKNOWN_ENTITY"


def test_snapshot_restore_and_invalid_state():
    guard = make_guard()
    state = guard.snapshot()
    assert state["stats"]["resolved"] == 0

    assert guard.resolve_chain(LEAF, now=NOW) == [ANCHOR, MID, LEAF]
    assert state["stats"]["resolved"] == 0

    restored = ftg.FederationTrustGuard()
    restored.restore(state)
    assert restored.resolve_chain(LEAF, now=NOW) == [ANCHOR, MID, LEAF]
    assert restored.verify_entity(LEAF, expected_fingerprint="FP-LEAF",
                                  now=NOW)["trust_anchor"] == ANCHOR

    with pytest.raises(ftg.FederationError) as exc:
        restored.restore({"version": 2})
    assert exc.value.code == "FED_SNAPSHOT_INVALID"
    assert restored.resolve_chain(LEAF, now=NOW) == [ANCHOR, MID, LEAF]
    assert restored.stats()["resolved"] == 2
    assert restored.stats()["verified"] == 1


def test_shortest_path_tie_breaks_lexicographically():
    guard = make_guard()
    for entity in ("https://aaa.example", "https://zzz.example"):
        guard.add_trust_anchor(entity, jwks_fingerprint=entity,
                               expires_at=EXPIRY, now=NOW)
        guard.add_entity_configuration(entity, issuer=entity,
                                       jwks_fingerprint=entity,
                                       expires_at=EXPIRY, now=NOW)
        guard.add_subordinate_statement(entity, subject=LEAF,
                                        jwks_fingerprint="FP-LEAF",
                                        expires_at=EXPIRY, now=NOW)
    assert guard.resolve_chain(LEAF, now=NOW) == ["https://aaa.example", LEAF]
    assert guard.trusted_entities(now=NOW) == ["https://aaa.example", ANCHOR,
                                               LEAF, MID, "https://zzz.example"]


def test_concurrent_verification_is_consistent():
    import threading

    guard = make_guard()
    barrier = threading.Barrier(8)
    chains = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        result = guard.verify_entity(LEAF, expected_fingerprint="FP-LEAF", now=NOW)
        with lock:
            chains.append(result["chain"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert chains == [[ANCHOR, MID, LEAF]] * 8
    assert guard.stats() == {"resolved": 0, "verified": 8,
                             "revoked": 0, "rejected": 0}
