import threading

import pytest

import package_provenance_admission as prov

ECO = "pypi"
NOW = "2026-11-24T10:00:00+00:00"


def dg(ch):
    return "sha256:" + ch * 64


def make(**kwargs):
    guard = prov.ProvenanceAdmission(**kwargs)
    guard.register_registry("public", ecosystem=ECO, priority=10)
    guard.register_registry("internal", ecosystem=ECO, priority=50)
    guard.register_project("app", policy="permissive")
    return guard


def publish(guard, name="lib", version="1.0.0", registry="internal",
            ch="a", yanked=False):
    return guard.publish(ECO, name, version, registry=registry, digest=dg(ch),
                         yanked=yanked)


def test_registry_registration_is_idempotent_and_priority_is_unique():
    guard = prov.ProvenanceAdmission()
    first = guard.register_registry("public", ecosystem=ECO, priority=10)
    assert first == {"name": "public", "ecosystem": ECO, "priority": 10, "trusted": True}
    assert guard.register_registry("public", ecosystem=ECO, priority=10)["priority"] == 10

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.register_registry("mirror", ecosystem=ECO, priority=10)
    assert exc.value.code == "PROV_PRIORITY_CONFLICT"

    guard.register_registry("npm-mirror", ecosystem="npm", priority=20)
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.publish(ECO, "lib", "1.0.0", registry="npm-mirror", digest=dg("a"))
    assert exc.value.code == "PROV_REGISTRY_ECOSYSTEM"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.publish(ECO, "lib", "1.0.0", registry="ghost", digest=dg("a"))
    assert exc.value.code == "PROV_UNKNOWN_REGISTRY"

    guard.register_project("app", policy="permissive")
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("app", ECO, "lib", "1.0.0")
    assert exc.value.code == "PROV_NOT_FOUND"
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.register_project("odd", policy="whatever")
    assert exc.value.code == "PROV_BAD_POLICY"
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("ghost", ECO, "lib", "1.0.0")
    assert exc.value.code == "PROV_UNKNOWN_PROJECT"


def test_scope_claims_block_foreign_publishers():
    guard = make()
    claimed = guard.claim_scope(ECO, "@acme", registry="internal")
    assert claimed == {"ecosystem": ECO, "scope": "@acme", "registry": "internal"}
    again = guard.claim_scope(ECO, "@acme", registry="internal")
    assert again["registry"] == "internal"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.claim_scope(ECO, "@acme", registry="public")
    assert exc.value.code == "PROV_SCOPE_CONFLICT"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.publish(ECO, "@acme/tool", "1.0.0", registry="public", digest=dg("a"))
    assert exc.value.code == "PROV_SCOPE_VIOLATION"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.publish(ECO, "@other/tool", "1.0.0", registry="internal", digest=dg("a"))
    assert exc.value.code == "PROV_UNCLAIMED_SCOPE"

    stored = guard.publish(ECO, "@acme/tool", "1.0.0", registry="internal", digest=dg("a"))
    assert stored["status"] == "published"
    resolved = guard.resolve("app", ECO, "@acme/tool", "1.0.0")
    assert resolved["registry"] == "internal"


def test_publish_validates_inputs_and_is_idempotent():
    guard = make()
    for version in ("^1.2.0", "1.2", "latest"):
        with pytest.raises(prov.ProvenanceError) as exc:
            publish(guard, version=version)
        assert exc.value.code == "PROV_BAD_VERSION"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.publish(ECO, "lib", "1.0.0", registry="internal", digest="sha256:nope")
    assert exc.value.code == "PROV_BAD_DIGEST"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.publish(ECO, "bad name", "1.0.0", registry="internal", digest=dg("a"))
    assert exc.value.code == "PROV_BAD_NAME"

    first = publish(guard)
    assert first["status"] == "published"
    assert first["yanked"] is False
    again = publish(guard)
    assert again["status"] == "exists"

    with pytest.raises(prov.ProvenanceError) as exc:
        publish(guard, ch="b")
    assert exc.value.code == "PROV_DIGEST_CONFLICT"


def test_publication_capacity_is_enforced_atomically():
    guard = prov.ProvenanceAdmission(max_publications=2)
    guard.register_registry("internal", ecosystem=ECO, priority=50)
    publish(guard, version="1.0.0")
    publish(guard, version="1.0.1")

    with pytest.raises(prov.ProvenanceError) as exc:
        publish(guard, version="1.0.2")
    assert exc.value.code == "PROV_CAPACITY"

    assert publish(guard, version="1.0.0")["status"] == "exists"
    with pytest.raises(prov.ProvenanceError) as exc:
        publish(guard, version="1.0.2", ch="b")
    assert exc.value.code == "PROV_CAPACITY"
    assert guard.stats()["published"] == 2


def test_yanked_versions_are_refused_unless_a_clean_copy_exists():
    guard = make()
    publish(guard, version="1.0.0")
    publish(guard, version="1.0.1")
    guard.yank(ECO, "lib", "1.0.0", registry="internal")

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("app", ECO, "lib", "1.0.0")
    assert exc.value.code == "PROV_YANKED"

    assert guard.resolve("app", ECO, "lib", "1.0.1")["version"] == "1.0.1"
    assert guard.yank(ECO, "lib", "1.0.0", registry="internal")["status"] == "yanked"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.yank(ECO, "lib", "9.9.9", registry="internal")
    assert exc.value.code == "PROV_UNKNOWN_PUBLICATION"


def test_highest_priority_registry_wins_for_unscoped_names():
    guard = make()
    publish(guard, registry="public", ch="a")
    publish(guard, registry="internal", ch="b")

    chosen = guard.resolve("app", ECO, "lib", "1.0.0")
    assert chosen["registry"] == "internal"
    assert chosen["digest"] == dg("b")

    guard.register_registry("secondary", ecosystem=ECO, priority=30)
    publish(guard, registry="secondary", ch="c")
    assert guard.resolve("app", ECO, "lib", "1.0.0")["digest"] == dg("b")

    guard.register_registry("top", ecosystem=ECO, priority=90)
    publish(guard, registry="top", ch="d")
    assert guard.resolve("app", ECO, "lib", "1.0.0")["digest"] == dg("d")


def test_untrusted_and_revoked_registries_are_excluded():
    guard = make()
    guard.register_registry("sketchy", ecosystem=ECO, priority=90, trusted=False)
    publish(guard, name="untrusted-lib", registry="sketchy")
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("app", ECO, "untrusted-lib", "1.0.0")
    assert exc.value.code == "PROV_UNTRUSTED_REGISTRY"

    guard.register_registry("mirror", ecosystem=ECO, priority=5)
    publish(guard, name="revoked-lib", registry="mirror")
    guard.revoke_registry("mirror")
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("app", ECO, "revoked-lib", "1.0.0")
    assert exc.value.code == "PROV_REGISTRY_REVOKED"

    with pytest.raises(prov.ProvenanceError) as exc:
        publish(guard, name="late-lib", registry="mirror")
    assert exc.value.code == "PROV_REGISTRY_REVOKED"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.revoke_registry("ghost")
    assert exc.value.code == "PROV_UNKNOWN_REGISTRY"


def test_pinned_project_requires_an_exact_pin():
    guard = make()
    guard.register_project("strict", policy="pinned")
    publish(guard, version="1.0.0", ch="b")
    publish(guard, version="1.0.1", ch="c")

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("strict", ECO, "lib", "1.0.0")
    assert exc.value.code == "PROV_UNPINNED"

    guard.pin("strict", ECO, "lib", version="1.0.0", digest=dg("a"))
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("strict", ECO, "lib", "1.0.0")
    assert exc.value.code == "PROV_DIGEST_MISMATCH"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("strict", ECO, "lib", "1.0.1")
    assert exc.value.code == "PROV_PIN_MISMATCH"


def test_pins_are_idempotent_and_immutable():
    guard = make()
    guard.register_project("strict", policy="pinned")
    publish(guard)
    first = guard.pin("strict", ECO, "lib", version="1.0.0", digest=dg("a"))
    assert first == {"status": "pinned", "project": "strict", "ecosystem": ECO,
                     "name": "lib", "version": "1.0.0", "digest": dg("a")}
    again = guard.pin("strict", ECO, "lib", version="1.0.0", digest=dg("a"))
    assert again["status"] == "unchanged"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.pin("strict", ECO, "lib", version="1.0.1", digest=dg("a"))
    assert exc.value.code == "PROV_PIN_CONFLICT"

    with pytest.raises(prov.ProvenanceError) as exc:
        guard.pin("ghost", ECO, "lib", version="1.0.0", digest=dg("a"))
    assert exc.value.code == "PROV_UNKNOWN_PROJECT"

    assert guard.resolve("strict", ECO, "lib", "1.0.0")["registry"] == "internal"


def test_permissive_projects_resolve_without_pins_but_honour_existing_pins():
    guard = make()
    publish(guard, version="1.0.0", ch="a")
    assert guard.resolve("app", ECO, "lib", "1.0.0")["digest"] == dg("a")

    guard.pin("app", ECO, "lib", version="1.0.0", digest=dg("a"))
    assert guard.resolve("app", ECO, "lib", "1.0.0")["status"] == "resolved"

    guard.register_project("strict", policy="pinned")
    publish(guard, name="other", version="2.0.0", ch="b")
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.resolve("strict", ECO, "other", "2.0.0")
    assert exc.value.code == "PROV_UNPINNED"


def test_admission_ledger_is_idempotent_per_artifact():
    guard = make()
    publish(guard, version="1.0.0", ch="a")
    publish(guard, version="1.0.1", ch="b")

    first = guard.admit("app", ECO, "lib", "1.0.0")
    assert first["status"] == "accepted"
    assert first["sequence"] == 1
    assert first["registry"] == "internal"
    assert first["digest"] == dg("a")

    second = guard.admit("app", ECO, "lib", "1.0.0")
    assert second["status"] == "duplicate"
    assert second["sequence"] == 1
    assert len(guard.ledger("app", limit=10)) == 1

    third = guard.admit("app", ECO, "lib", "1.0.1")
    assert third["sequence"] == 2
    versions = [row["version"] for row in guard.ledger("app", limit=10)]
    assert versions == ["1.0.1", "1.0.0"]
    assert guard.stats()["admitted"] == 2
    assert guard.stats()["duplicates"] == 1


def test_ledger_limit_and_capacity_trim_oldest():
    guard = make()
    for version in ("1.0.0", "1.0.1", "1.0.2"):
        publish(guard, version=version)
        guard.admit("app", ECO, "lib", version)

    rows = guard.ledger("app", limit=2)
    assert [row["version"] for row in rows] == ["1.0.2", "1.0.1"]
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.ledger("app", limit=0)
    assert exc.value.code == "PROV_BAD_LIMIT"
    with pytest.raises(prov.ProvenanceError) as exc:
        guard.ledger("ghost")
    assert exc.value.code == "PROV_UNKNOWN_PROJECT"

    capped = prov.ProvenanceAdmission(max_ledger=2)
    capped.register_registry("internal", ecosystem=ECO, priority=50)
    capped.register_project("app", policy="permissive")
    for version in ("1.0.0", "1.0.1", "1.0.2"):
        publish(capped, version=version, ch="a")
        capped.admit("app", ECO, "lib", version)
    assert [row["version"] for row in capped.ledger("app", limit=10)] == ["1.0.2", "1.0.1"]


def test_snapshot_restore_keeps_admissions_and_is_detached():
    guard = make()
    guard.register_project("strict", policy="pinned")
    publish(guard, version="1.0.0", ch="a")
    guard.pin("strict", ECO, "lib", version="1.0.0", digest=dg("a"))
    guard.admit("strict", ECO, "lib", "1.0.0")
    stats_before = guard.stats()
    state = guard.snapshot()
    assert state["version"] == 1

    state["stats"]["admitted"] = 99
    state["projects"]["strict"]["policy"] = "permissive"
    assert guard.stats() == stats_before

    clean = guard.snapshot()
    publish(guard, version="1.0.1", ch="b")
    guard.admit("app", ECO, "lib", "1.0.1")

    restored = prov.ProvenanceAdmission()
    restored.restore(clean)
    with pytest.raises(prov.ProvenanceError) as exc:
        restored.resolve("strict", ECO, "lib", "1.0.1")
    assert exc.value.code == "PROV_NOT_FOUND"
    assert restored.resolve("strict", ECO, "lib", "1.0.0")["digest"] == dg("a")
    duplicate = restored.admit("strict", ECO, "lib", "1.0.0")
    assert duplicate["status"] == "duplicate"
    assert [row["version"] for row in restored.ledger("strict", limit=10)] == ["1.0.0"]
    assert restored.stats()["admitted"] == 1


def test_corrupt_snapshot_is_rejected_and_state_survives():
    guard = make()
    publish(guard)
    guard.admit("app", ECO, "lib", "1.0.0")
    before = guard.stats()

    bad_states = [
        None,
        {},
        {"version": 2, "registries": {}, "scopes": {}, "projects": {},
         "pins": {}, "publications": {}, "ledger": {}, "stats": {}, "sequence": 0},
        {"version": 1, "registries": "nope", "scopes": {}, "projects": {},
         "pins": {}, "publications": {}, "ledger": {}, "stats": {}, "sequence": 0},
        {"version": 1, "registries": {}, "scopes": {}, "projects": {},
         "pins": {}, "publications": [], "ledger": {}, "stats": {}, "sequence": 0},
        {"version": 1, "registries": {}, "scopes": {}, "projects": {},
         "pins": {}, "publications": {}, "ledger": {}, "stats": "nope", "sequence": 0},
        {"version": 1, "registries": {}, "scopes": {}, "projects": {},
         "pins": {}, "publications": {}, "ledger": {}, "stats": {}, "sequence": "x"},
    ]
    for bad in bad_states:
        with pytest.raises(prov.ProvenanceError) as exc:
            guard.restore(bad)
        assert exc.value.code == "PROV_SNAPSHOT_INVALID"

    assert guard.stats() == before
    assert guard.resolve("app", ECO, "lib", "1.0.0")["status"] == "resolved"
    assert guard.admit("app", ECO, "lib", "1.0.0")["status"] == "duplicate"


def test_concurrent_publication_and_admission_are_serialized():
    guard = make()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def publisher(index):
        barrier.wait()
        try:
            out = guard.publish(ECO, "race", "1.0.0", registry="internal",
                                digest=dg(format(index, "x")))
            outcome = out["status"]
        except prov.ProvenanceError as exc:
            outcome = exc.code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=publisher, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["PROV_DIGEST_CONFLICT"] * 7 + ["published"]
    assert guard.stats()["published"] == 1

    winner = guard.resolve("app", ECO, "race", "1.0.0")["digest"]
    barrier = threading.Barrier(8)
    admissions = []

    def consumer():
        barrier.wait()
        try:
            out = guard.admit("app", ECO, "race", "1.0.0")
            outcome = (out["status"], out["digest"])
        except prov.ProvenanceError as exc:  # pragma: no cover
            outcome = (exc.code, None)
        with lock:
            admissions.append(outcome)

    threads = [threading.Thread(target=consumer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert admissions.count(("accepted", winner)) == 1
    assert admissions.count(("duplicate", winner)) == 7
    assert len(guard.ledger("app", limit=10)) == 1
    assert guard.stats()["admitted"] == 1
    assert guard.stats()["duplicates"] == 7
