import threading

import pytest

import name_constraints_guard as ng

FP = "aa" * 32
FP2 = "bb" * 32
FP3 = "cc" * 32


def make(**kwargs):
    guard = ng.NameConstraintsGuard(**kwargs)
    guard.register_anchor(
        "corp",
        permitted={"dns": ["example.com"], "email": ["example.com"],
                   "ip": ["10.0.0.0/8", "2001:db8::/32"]},
        excluded={"dns": [".secret.example.com"], "ip": ["10.9.0.0/16"]},
        max_chain_depth=3,
    )
    return guard


def evaluate(guard, san, fingerprint=FP, chain_depth=2, anchor="corp"):
    return guard.evaluate(anchor, fingerprint=fingerprint, san=san,
                          chain_depth=chain_depth)


def test_register_anchor_is_idempotent_and_validates_constraints():
    guard = ng.NameConstraintsGuard()
    info = guard.register_anchor(
        "corp", permitted={"dns": ["example.com", ".internal.example.com"]},
        excluded={"email": ["blocked@example.com"]}, max_chain_depth=4)
    assert info["created"] is True
    assert info["permitted"]["dns"] == ["example.com", ".internal.example.com"]
    assert info["excluded"]["email"] == ["blocked@example.com"]
    assert info["allow_empty_san"] is False
    assert info["revoked"] is False
    assert "created" in info

    again = guard.register_anchor(
        "corp", permitted={"dns": ["example.com", ".internal.example.com"]},
        excluded={"email": ["blocked@example.com"]}, max_chain_depth=4)
    assert again["created"] is False

    with pytest.raises(ng.ConstraintError) as exc:
        guard.register_anchor("corp", permitted={"dns": ["other.example.com"]})
    assert exc.value.code == "CONSTRAINT_CONFLICT"

    for bad_permitted in ({"uri": ["https://example.com"]},
                          {"dns": ["*.example.com"]},
                          {"ip": ["10.0.0.0/33"]},
                          {"email": ["a@@b"]}):
        with pytest.raises(ng.ConstraintError) as exc:
            guard.register_anchor("bad", permitted=bad_permitted)
        assert exc.value.code == "CONSTRAINT_INVALID"
    with pytest.raises(ng.ConstraintError) as exc:
        guard.register_anchor("bad", max_chain_depth=0)
    assert exc.value.code == "CONSTRAINT_INVALID"


def test_anchor_capacity_and_revocation_lifecycle():
    guard = ng.NameConstraintsGuard(max_anchors=1)
    guard.register_anchor("corp", permitted={"dns": ["example.com"]})
    with pytest.raises(ng.ConstraintError) as exc:
        guard.register_anchor("second", permitted={"dns": ["example.org"]})
    assert exc.value.code == "CONSTRAINT_CAPACITY_FULL"

    with pytest.raises(ng.ConstraintError) as exc:
        guard.revoke_anchor("missing")
    assert exc.value.code == "CONSTRAINT_UNKNOWN_ANCHOR"
    guard.revoke_anchor("corp")
    guard.revoke_anchor("corp")
    assert guard.is_revoked("corp") is True
    assert guard.anchor("corp")["revoked"] is True
    assert guard.list_anchors()[0]["revoked"] is True
    with pytest.raises(ng.ConstraintError) as exc:
        guard.anchor("missing")
    assert exc.value.code == "CONSTRAINT_UNKNOWN_ANCHOR"
    with pytest.raises(ng.ConstraintError) as exc:
        guard.is_revoked("missing")
    assert exc.value.code == "CONSTRAINT_UNKNOWN_ANCHOR"
    with pytest.raises(ng.ConstraintError) as exc:
        evaluate(guard, {"dns": ["example.com"]})
    assert exc.value.code == "CONSTRAINT_ANCHOR_REVOKED"
    with pytest.raises(ng.ConstraintError) as exc:
        evaluate(guard, {"dns": ["example.com"]}, anchor="missing")
    assert exc.value.code == "CONSTRAINT_UNKNOWN_ANCHOR"


def test_empty_san_is_denied_unless_anchor_allows_it():
    guard = make()
    denied = evaluate(guard, {})
    assert denied["allowed"] is False
    assert denied["code"] == "NAME_SAN_EMPTY"
    assert evaluate(guard, {"dns": [], "email": []})["code"] == "NAME_SAN_EMPTY"

    relaxed = ng.NameConstraintsGuard()
    relaxed.register_anchor("open", permitted={"dns": ["example.com"]},
                            allow_empty_san=True)
    allowed = relaxed.evaluate("open", fingerprint=FP, san={}, chain_depth=1)
    assert allowed["allowed"] is True
    assert allowed["violations"] == []
    assert allowed["code"] is None


def test_dns_constraint_matches_apex_and_subdomains_only():
    guard = make()
    assert evaluate(guard, {"dns": ["example.com"]})["allowed"] is True
    assert evaluate(guard, {"dns": ["api.example.com"]})["allowed"] is True
    assert evaluate(guard, {"dns": ["deep.api.example.com"]},
                    fingerprint=FP2)["allowed"] is True
    for name, fp in (("notexample.com", FP2), ("example.com.evil", FP3)):
        decision = evaluate(guard, {"dns": [name]}, fingerprint=fp)
        assert decision["allowed"] is False
        assert decision["code"] == "NAME_NOT_PERMITTED"
        assert decision["violations"][0] == {
            "type": "dns", "value": name, "constraint": None,
            "code": "NAME_NOT_PERMITTED"}


def test_leading_dot_constraint_excludes_the_apex():
    guard = ng.NameConstraintsGuard()
    guard.register_anchor("corp", permitted={"dns": [".example.com"]})
    assert evaluate(guard, {"dns": ["api.example.com"]})["allowed"] is True
    apex = evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP2)
    assert apex["allowed"] is False
    assert apex["code"] == "NAME_NOT_PERMITTED"


def test_excluded_subtrees_win_and_all_violations_are_reported():
    guard = make()
    excluded = evaluate(guard, {"dns": ["api.secret.example.com"]})
    assert excluded["allowed"] is False
    assert excluded["code"] == "NAME_EXCLUDED"
    assert excluded["violations"][0]["constraint"] == ".secret.example.com"

    apex = evaluate(guard, {"dns": ["example.com"],
                            "email": ["dev@example.com"]},
                    fingerprint=FP2)
    assert apex["allowed"] is True

    both = evaluate(guard, {"dns": ["evil.test"], "email": ["dev@evil.test"]},
                    fingerprint=FP3)
    assert both["allowed"] is False
    assert len(both["violations"]) == 2
    assert {row["type"] for row in both["violations"]} == {"dns", "email"}


def test_wildcards_unknown_types_and_malformed_values_are_rejected():
    guard = make()
    wildcard = evaluate(guard, {"dns": ["*.example.com"]})
    assert wildcard["code"] == "NAME_WILDCARD_DENIED"
    unknown = evaluate(guard, {"dns": ["example.com"], "uri": ["https://example.com"]},
                       fingerprint=FP2)
    assert unknown["code"] == "NAME_SAN_TYPE_UNKNOWN"
    assert unknown["violations"][-1]["type"] == "uri"

    for san, fp in (({"dns": [123]}, FP3),
                    ({"email": ["bad@@example.com"]}, FP3),
                    ({"ip": ["999.1.1.1"]}, FP3),
                    ({"dns": [""]}, FP3)):
        decision = evaluate(guard, san, fingerprint=fp)
        assert decision["allowed"] is False
        assert decision["code"] == "NAME_SAN_INVALID"


def test_email_constraints_support_host_subdomain_and_mailbox_forms():
    guard = ng.NameConstraintsGuard()
    guard.register_anchor("host", permitted={"email": ["example.com"]})
    assert evaluate(guard, {"email": ["ops@example.com"]}, anchor="host",
                    chain_depth=1)["allowed"] is True
    assert evaluate(guard, {"email": ["ops@sub.example.com"]}, anchor="host",
                    fingerprint=FP2, chain_depth=1)["code"] == "NAME_NOT_PERMITTED"

    guard.register_anchor("sub", permitted={"email": [".example.com"]})
    assert evaluate(guard, {"email": ["ops@sub.example.com"]}, anchor="sub",
                    fingerprint=FP2, chain_depth=1)["allowed"] is True
    assert evaluate(guard, {"email": ["ops@example.com"]}, anchor="sub",
                    fingerprint=FP3, chain_depth=1)["code"] == "NAME_NOT_PERMITTED"

    guard.register_anchor("mailbox", permitted={"email": ["ops@example.com"]})
    assert evaluate(guard, {"email": ["ops@example.com"]}, anchor="mailbox",
                    fingerprint=FP2, chain_depth=1)["allowed"] is True
    assert evaluate(guard, {"email": ["dev@example.com"]}, anchor="mailbox",
                    fingerprint=FP3, chain_depth=1)["code"] == "NAME_NOT_PERMITTED"


def test_ip_constraints_respect_address_families_and_exclusions():
    guard = make()
    assert evaluate(guard, {"ip": ["10.1.2.3"]})["allowed"] is True
    assert evaluate(guard, {"ip": ["2001:db8::1"]}, fingerprint=FP2)["allowed"] is True
    assert evaluate(guard, {"ip": ["192.168.1.1"]}, fingerprint=FP3)["code"] == "NAME_NOT_PERMITTED"
    assert evaluate(guard, {"ip": ["2001:db9::1"]}, fingerprint=FP3)["code"] == "NAME_NOT_PERMITTED"
    assert evaluate(guard, {"ip": ["::ffff:10.1.2.3"]}, fingerprint=FP3)["code"] == "NAME_NOT_PERMITTED"
    blocked = evaluate(guard, {"ip": ["10.9.7.7"]}, fingerprint=FP3)
    assert blocked["code"] == "NAME_EXCLUDED"
    assert blocked["violations"][0]["constraint"] == "10.9.0.0/16"


def test_multiple_san_entries_are_evaluated_together():
    guard = make()
    good = evaluate(guard, {"dns": ["api.example.com"],
                            "email": ["ops@example.com"],
                            "ip": ["10.0.0.7"]})
    assert good["allowed"] is True

    mixed = evaluate(guard, {"dns": ["api.example.com"],
                             "email": ["ops@elsewhere.test"]},
                     fingerprint=FP2)
    assert mixed["allowed"] is False
    assert mixed["code"] == "NAME_NOT_PERMITTED"
    assert mixed["violations"] == [
        {"type": "email", "value": "ops@elsewhere.test", "constraint": None,
         "code": "NAME_NOT_PERMITTED"}]


def test_chain_depth_budget_is_enforced():
    guard = make()
    assert evaluate(guard, {"dns": ["example.com"]}, chain_depth=3)["allowed"] is True
    deep = evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP2, chain_depth=4)
    assert deep["allowed"] is False
    assert deep["code"] == "NAME_DEPTH_EXCEEDED"
    assert deep["violations"][0]["type"] == "chain"


def test_decision_cache_hits_and_evicts_by_capacity():
    guard = make(decision_cache=1)
    first = evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP)
    assert first["cached"] is False
    second = evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP)
    assert second["cached"] is True
    assert guard.stats() == {"evaluated": 1, "cached": 1, "allowed": 2, "denied": 0}

    evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP2)
    third = evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP)
    assert third["cached"] is False
    assert guard.stats()["evaluated"] == 3
    assert guard.stats()["cached"] == 1


def test_concurrent_evaluation_computes_once():
    guard = make()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.evaluate("corp", fingerprint=FP,
                                 san={"dns": ["api.example.com"],
                                      "ip": ["10.2.3.4"]}, chain_depth=2)
        except ng.ConstraintError as exc:
            out = {"allowed": False, "code": exc.code}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(row["allowed"] for row in results)
    assert sum(1 for row in results if row.get("cached")) == 7
    assert guard.stats() == {"evaluated": 1, "cached": 7, "allowed": 8, "denied": 0}


def test_snapshot_is_detached_and_restore_carries_anchors_and_cache():
    guard = make()
    evaluate(guard, {"dns": ["api.example.com"]})
    state = guard.snapshot()
    state["anchors"]["corp"]["max_chain_depth"] = 99
    assert guard.list_anchors()[0]["max_chain_depth"] == 3

    restored = ng.NameConstraintsGuard()
    restored.restore(guard.snapshot())
    assert restored.list_anchors()[0]["max_chain_depth"] == 3
    hit = restored.evaluate("corp", fingerprint=FP,
                            san={"dns": ["api.example.com"]}, chain_depth=2)
    assert hit["cached"] is True
    assert hit["allowed"] is True
    assert restored.stats() == {"evaluated": 1, "cached": 1, "allowed": 2,
                                "denied": 0}
    fresh = restored.evaluate("corp", fingerprint=FP2,
                              san={"dns": ["api.example.com"]}, chain_depth=2)
    assert fresh["cached"] is False
    assert restored.stats()["evaluated"] == 2


def test_corrupt_snapshot_is_refused_and_stats_can_be_reset():
    guard = make()
    evaluate(guard, {"dns": ["example.com"]})
    good = guard.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "anchors": {}, "cache": [], "stats": {}},
        dict(good, anchors="nope"),
        dict(good, cache="nope"),
        dict(good, stats={"evaluated": 1}),
        dict(good, anchors={"corp": {"permitted": "nope", "excluded": {},
                                     "max_chain_depth": 1,
                                     "allow_empty_san": False,
                                     "revoked": False}}),
        dict(good, cache=[["short", {"allowed": True}]]),
    ]
    for bad in bad_states:
        with pytest.raises(ng.ConstraintError) as exc:
            guard.restore(bad)
        assert exc.value.code == "CONSTRAINT_SNAPSHOT_INVALID"
    assert guard.list_anchors()[0]["anchor_id"] == "corp"
    assert guard.stats() == {"evaluated": 1, "cached": 0, "allowed": 1, "denied": 0}

    reset = evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP2)
    assert reset["cached"] is False
    guard.reset_stats()
    assert guard.stats() == {"evaluated": 0, "cached": 0, "allowed": 0, "denied": 0}
    still_cached = evaluate(guard, {"dns": ["example.com"]}, fingerprint=FP2)
    assert still_cached["cached"] is True
