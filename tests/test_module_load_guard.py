import threading

import pytest

import module_load_guard as mg

SIG = {"signer": "build-key", "fingerprint": "SHA256:aa"}


def make(**kwargs):
    guard = mg.ModuleLoadGuard(**kwargs)
    guard.trust_signer("build-key", fingerprints=("SHA256:aa", "SHA256:bb"),
                       priority=10)
    return guard


def test_signer_registration_is_sorted_idempotent_and_conflict_checked():
    guard = mg.ModuleLoadGuard()
    row = guard.trust_signer("k1", fingerprints=("SHA256:y", "SHA256:x"),
                             priority=5)
    assert row == {"signer": "k1", "fingerprints": ["SHA256:x", "SHA256:y"],
                   "priority": 5, "trusted": True}
    again = guard.trust_signer("k1", fingerprints=("SHA256:x", "SHA256:y"),
                               priority=5)
    assert again == row

    with pytest.raises(mg.ModuleError) as exc:
        guard.trust_signer("k1", fingerprints=("SHA256:z",), priority=5)
    assert exc.value.code == "MOD_CONFLICT"
    with pytest.raises(mg.ModuleError) as exc:
        guard.trust_signer("", fingerprints=("SHA256:x",))
    assert exc.value.code == "MOD_INVALID_SIGNER"
    with pytest.raises(mg.ModuleError) as exc:
        guard.trust_signer("k2", fingerprints=())
    assert exc.value.code == "MOD_INVALID_SIGNER"
    assert guard.stats()["rejected"] == 3


def test_revoked_signer_blocks_allow_load_and_reregistration():
    guard = make()
    guard.allow_module("mod", signer="build-key", fingerprint="SHA256:aa")
    guard.revoke_signer("build-key")

    with pytest.raises(mg.ModuleError) as exc:
        guard.load("mod", signature=SIG)
    assert exc.value.code == "MOD_SIGNER_REVOKED"
    assert guard.is_loaded("mod") is False
    with pytest.raises(mg.ModuleError) as exc:
        guard.trust_signer("build-key", fingerprints=("SHA256:aa",))
    assert exc.value.code == "MOD_SIGNER_REVOKED"
    with pytest.raises(mg.ModuleError) as exc:
        guard.revoke_signer("ghost")
    assert exc.value.code == "MOD_UNKNOWN_SIGNER"
    assert guard.stats()["rejected"] == 3


def test_allow_module_requires_signature_or_explicit_unsigned_mode():
    strict = mg.ModuleLoadGuard()
    with pytest.raises(mg.ModuleError) as exc:
        strict.allow_module("u1")
    assert exc.value.code == "MOD_SIGNATURE_MISSING"

    relaxed = mg.ModuleLoadGuard(allow_unsigned=True)
    row = relaxed.allow_module("u1")
    assert row["signer"] is None
    assert row["fingerprint"] is None
    assert row["taints"] == []

    guard = make()
    with pytest.raises(mg.ModuleError) as exc:
        guard.allow_module("m2", signer="stranger", fingerprint="SHA256:aa")
    assert exc.value.code == "MOD_SIGNER_UNTRUSTED"
    with pytest.raises(mg.ModuleError) as exc:
        guard.allow_module("m3", signer="build-key",
                           fingerprint="SHA256:zz")
    assert exc.value.code == "MOD_SIGNATURE_INVALID"
    assert guard.loaded() == []


def test_allow_module_is_idempotent_and_conflicting_metadata_is_refused():
    guard = make()
    first = guard.allow_module("dm", signer="build-key",
                               fingerprint="SHA256:aa",
                               depends=("core",), taints=("out-of-tree",))
    assert first == {"name": "dm", "signer": "build-key",
                     "fingerprint": "SHA256:aa", "depends": ["core"],
                     "taints": ["out-of-tree"]}
    second = guard.allow_module("dm", signer="build-key",
                                fingerprint="SHA256:aa",
                                depends=("core",), taints=("out-of-tree",))
    assert second == first
    with pytest.raises(mg.ModuleError) as exc:
        guard.allow_module("dm", signer="build-key",
                           fingerprint="SHA256:bb")
    assert exc.value.code == "MOD_CONFLICT"


def test_dependency_cycles_are_rejected_at_allow_time():
    guard = make()
    guard.allow_module("b", signer="build-key", fingerprint="SHA256:aa",
                       depends=("c",))
    guard.allow_module("c", signer="build-key", fingerprint="SHA256:aa",
                       depends=("a",))
    with pytest.raises(mg.ModuleError) as exc:
        guard.allow_module("a", signer="build-key", fingerprint="SHA256:aa",
                           depends=("b",))
    assert exc.value.code == "MOD_DEPENDENCY_CYCLE"
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("a", signature=SIG)
    assert exc.value.code == "MOD_UNKNOWN"
    with pytest.raises(mg.ModuleError) as exc:
        guard.allow_module("self", signer="build-key",
                           fingerprint="SHA256:aa", depends=("self",))
    assert exc.value.code == "MOD_DEPENDENCY_CYCLE"


def test_denied_module_cannot_be_loaded_even_when_allowlisted():
    guard = make()
    guard.allow_module("bad", signer="build-key", fingerprint="SHA256:aa")
    row = guard.deny_module("bad", reason="known bug")
    assert row == {"name": "bad", "denied": True, "reason": "known bug"}
    assert guard.deny_module("bad", reason="known bug") == row
    assert guard.deny_module("never") == {"name": "never", "denied": True,
                                          "reason": ""}

    with pytest.raises(mg.ModuleError) as exc:
        guard.load("bad", signature=SIG)
    assert exc.value.code == "MOD_DENIED"
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("ghost", signature=SIG)
    assert exc.value.code == "MOD_UNKNOWN"
    assert guard.stats()["denied"] == 2
    assert guard.stats()["rejected"] == 2


def test_load_records_order_and_taints_once():
    guard = make()
    guard.allow_module("nvme", signer="build-key", fingerprint="SHA256:aa",
                       taints=("staging", "out-of-tree"))
    result = guard.load("nvme", signature=SIG)
    assert result == {"status": "loaded", "name": "nvme", "deps": [],
                      "taints": ["out-of-tree", "staging"]}
    assert guard.is_loaded("nvme") is True
    assert guard.loaded() == ["nvme"]
    assert guard.taints() == ["out-of-tree", "staging"]

    again = guard.load("nvme")
    assert again["status"] == "already_loaded"
    assert guard.stats()["loaded"] == 1


def test_load_rejects_missing_and_mismatched_signatures():
    guard = make()
    guard.allow_module("mod", signer="build-key", fingerprint="SHA256:aa")
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("mod")
    assert exc.value.code == "MOD_SIGNATURE_MISSING"
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("mod", signature={"fingerprint": "SHA256:aa"})
    assert exc.value.code == "MOD_SIGNATURE_MISSING"
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("mod", signature={"signer": "build-key",
                                     "fingerprint": "SHA256:bb"})
    assert exc.value.code == "MOD_SIGNATURE_INVALID"
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("mod", signature={"signer": "other",
                                     "fingerprint": "SHA256:aa"})
    assert exc.value.code == "MOD_SIGNATURE_INVALID"
    assert guard.is_loaded("mod") is False
    assert guard.stats()["rejected"] == 4

    guard.load("mod", signature=SIG)
    assert guard.loaded() == ["mod"]


def test_missing_dependencies_are_listed_in_sorted_order():
    guard = make()
    guard.allow_module("core", signer="build-key", fingerprint="SHA256:aa")
    guard.allow_module("nic", signer="build-key", fingerprint="SHA256:aa",
                       depends=("dma", "core"))
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("nic", signature=SIG)
    assert exc.value.code == "MOD_DEPENDENCY_MISSING"
    assert exc.value.detail == ["core", "dma"]

    guard.load("core", signature=SIG)
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("nic", signature=SIG)
    assert exc.value.detail == ["dma"]

    guard.allow_module("dma", signer="build-key", fingerprint="SHA256:aa")
    guard.load("dma", signature=SIG)
    result = guard.load("nic", signature=SIG)
    assert result["deps"] == ["core", "dma"]
    assert guard.loaded() == ["core", "dma", "nic"]


def test_loaded_module_budget_blocks_new_loads_but_not_repeats():
    guard = make(max_loaded=2)
    for name in ("m1", "m2", "m3"):
        guard.allow_module(name, signer="build-key", fingerprint="SHA256:aa")
    guard.load("m1", signature=SIG)
    guard.load("m2", signature=SIG)
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("m3", signature=SIG)
    assert exc.value.code == "MOD_LIMIT"
    assert guard.load("m2", signature=SIG)["status"] == "already_loaded"
    with pytest.raises(mg.ModuleError) as exc:
        guard.load("m3", signature=SIG)
    assert exc.value.code == "MOD_LIMIT"
    assert guard.loaded() == ["m1", "m2"]


def test_force_unload_cascades_through_dependents_in_reverse_order():
    guard = make()
    guard.allow_module("base", signer="build-key", fingerprint="SHA256:aa")
    guard.allow_module("mid", signer="build-key", fingerprint="SHA256:aa",
                       depends=("base",))
    guard.allow_module("leaf", signer="build-key", fingerprint="SHA256:aa",
                       depends=("mid",))
    for name in ("base", "mid", "leaf"):
        guard.load(name, signature=SIG)

    with pytest.raises(mg.ModuleError) as exc:
        guard.unload("base")
    assert exc.value.code == "MOD_IN_USE"
    assert exc.value.detail == ["mid"]
    assert guard.loaded() == ["base", "mid", "leaf"]

    result = guard.unload("base", force=True)
    assert result == {"status": "unloaded", "name": "base",
                      "cascade": ["leaf", "mid"]}
    assert guard.loaded() == []
    assert guard.is_loaded("base") is False
    assert guard.stats()["unloaded"] == 3


def test_unload_errors_and_taints_survive_unload():
    guard = make()
    guard.allow_module("m", signer="build-key", fingerprint="SHA256:aa",
                       taints=("oops",))
    guard.load("m", signature=SIG)
    result = guard.unload("m")
    assert result == {"status": "unloaded", "name": "m", "cascade": []}
    assert guard.is_loaded("m") is False
    assert guard.taints() == ["oops"]

    with pytest.raises(mg.ModuleError) as exc:
        guard.unload("m")
    assert exc.value.code == "MOD_NOT_LOADED"
    with pytest.raises(mg.ModuleError) as exc:
        guard.unload("never")
    assert exc.value.code == "MOD_NOT_LOADED"
    assert guard.stats()["unloaded"] == 1
    assert guard.stats()["rejected"] == 2


def test_snapshot_restore_preserves_allowlist_order_taints_and_config():
    guard = make(allow_unsigned=True, max_loaded=5)
    guard.allow_module("a", signer="build-key", fingerprint="SHA256:aa",
                       taints=("t1",))
    guard.allow_module("b", signer="build-key", fingerprint="SHA256:aa",
                       depends=("a",))
    guard.load("a", signature=SIG)
    guard.load("b", signature=SIG)
    snap = guard.snapshot()
    snap["loaded"].remove("a")
    snap["stats"]["loaded"] = 42
    assert guard.is_loaded("a") is True
    assert guard.stats()["loaded"] == 2

    current = guard.snapshot()
    fresh = mg.ModuleLoadGuard()
    fresh.restore(current)
    assert fresh.max_loaded == 5
    assert fresh.allow_unsigned is True
    assert fresh.loaded() == ["a", "b"]
    assert fresh.taints() == ["t1"]
    assert fresh.stats()["loaded"] == 2
    assert fresh.load("b", signature=SIG)["status"] == "already_loaded"
    assert fresh.unload("a", force=True)["cascade"] == ["b"]


def test_corrupt_snapshot_is_rejected_without_losing_state():
    guard = make()
    guard.allow_module("a", signer="build-key", fingerprint="SHA256:aa")
    guard.load("a", signature=SIG)
    snap = guard.snapshot()

    bad_version = dict(snap)
    bad_version["version"] = 2
    bad_signers = dict(snap)
    bad_signers["signers"] = "not-signers"
    bad_loaded = dict(snap)
    bad_loaded["loaded"] = ["ghost"]
    bad_taints = dict(snap)
    bad_taints["taints"] = "not-taints"
    bad_stats = dict(snap)
    bad_stats["stats"] = "not-stats"
    for state in (None, {}, bad_version, bad_signers, bad_loaded,
                  bad_taints, bad_stats):
        with pytest.raises(mg.ModuleError) as exc:
            guard.restore(state)
        assert exc.value.code == "MOD_SNAPSHOT_INVALID"

    assert guard.loaded() == ["a"]
    assert guard.stats()["loaded"] == 1
    assert guard.stats()["rejected"] == 7
    assert guard.load("a", signature=SIG)["status"] == "already_loaded"


def test_concurrent_load_of_same_module_happens_once():
    guard = make()
    guard.allow_module("hot", signer="build-key", fingerprint="SHA256:aa")
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.load("hot", signature=SIG)
        except Exception as exc:  # pragma: no cover - stub path only
            out = {"status": "boom", "code": type(exc).__name__}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["already_loaded"] * 7 + ["loaded"]
    assert guard.stats()["loaded"] == 1
    assert guard.loaded() == ["hot"]
