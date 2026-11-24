import hashlib
import threading

import pytest

import ci_cache_guard as cg

NOW = "2026-11-24T10:00:00+00:00"
MAIN = "refs/heads/main"
FEATURE = "refs/heads/feature"
PR1 = "refs/pull/1/head"
PR2 = "refs/pull/2/head"


def make_guard(**kwargs):
    guard = cg.CiCacheGuard(**kwargs)
    guard.register_repository("app", protected_branches=(MAIN,))
    return guard


def scope(guard, ref=MAIN, event="push", fork=False):
    return guard.open_scope("app", ref=ref, event=event, fork=fork)["scope_id"]


def test_register_repository_is_idempotent_and_validates_inputs():
    guard = cg.CiCacheGuard()
    info = guard.register_repository("app", protected_branches=(MAIN, FEATURE))
    assert info == {"repo": "app", "protected_branches": [MAIN, FEATURE],
                    "created": True}
    again = guard.register_repository("app", protected_branches=(FEATURE, MAIN))
    assert again["created"] is False
    with pytest.raises(cg.CacheError) as exc:
        guard.register_repository("app", protected_branches=(MAIN,))
    assert exc.value.code == "CACHE_CONFLICT"
    with pytest.raises(cg.CacheError) as exc:
        guard.register_repository("bad name", protected_branches=(MAIN,))
    assert exc.value.code == "CACHE_INVALID_REPO"
    with pytest.raises(cg.CacheError) as exc:
        guard.register_repository("other", protected_branches="refs/heads/main")
    assert exc.value.code == "CACHE_INVALID_REF"
    with pytest.raises(cg.CacheError) as exc:
        guard.register_repository("other", protected_branches=("main",))
    assert exc.value.code == "CACHE_INVALID_REF"


def test_open_scope_derives_trust_from_event_and_fork_flag():
    guard = make_guard()
    protected = guard.open_scope("app", ref=MAIN, event="push")
    assert protected["kind"] == "protected"
    assert protected["trusted"] is True
    branch = guard.open_scope("app", ref=FEATURE, event="push")
    assert branch["kind"] == "branch" and branch["trusted"] is True
    pr = guard.open_scope("app", ref=PR1, event="pull_request")
    assert pr["kind"] == "pr" and pr["trusted"] is False
    fork = guard.open_scope("app", ref=PR1, event="push", fork=True)
    assert fork["kind"] == "fork" and fork["trusted"] is False
    assert fork["scope_id"] != pr["scope_id"]
    merge = guard.open_scope("app", ref=MAIN, event="merge_group")
    assert merge["kind"] == "branch" and merge["trusted"] is True

    with pytest.raises(cg.CacheError) as exc:
        guard.open_scope("missing", ref=MAIN, event="push")
    assert exc.value.code == "CACHE_UNKNOWN_REPO"
    with pytest.raises(cg.CacheError) as exc:
        guard.open_scope("app", ref=MAIN, event="tag")
    assert exc.value.code == "CACHE_INVALID_EVENT"
    with pytest.raises(cg.CacheError) as exc:
        guard.open_scope("app", ref="main", event="push")
    assert exc.value.code == "CACHE_INVALID_REF"


def test_put_and_get_round_trip_with_digest():
    guard = make_guard()
    main = scope(guard)
    payload = b"compiled-object-files"
    digest = hashlib.sha256(payload).hexdigest()
    stored = guard.put(main, key="build/linux/amd64", data=payload,
                       digest=digest, now=NOW)
    assert stored["status"] == "stored"
    assert stored["digest"] == digest
    assert stored["size"] == len(payload)
    assert "expires_at" in stored

    fetched = guard.get(main, key="build/linux/amd64", now=NOW)
    assert fetched["data"] == payload
    assert fetched["digest"] == digest
    assert fetched["source_scope"] == main
    assert guard.get(main, key="build/linux/arm64", now=NOW) is None
    rows = guard.entries(main)
    assert rows == [{"scope_id": main, "key": "build/linux/amd64",
                     "digest": digest, "size": len(payload),
                     "expires_at": stored["expires_at"]}]


def test_put_is_idempotent_for_same_digest_and_refuses_overwrite():
    guard = make_guard()
    main = scope(guard)
    first = guard.put(main, key="deps", data=b"lock-v1", now=NOW)
    again = guard.put(main, key="deps", data=b"lock-v1", now=NOW)
    assert again["status"] == "unchanged"
    assert again["digest"] == first["digest"]
    assert guard.stats()["put"] == 1

    with pytest.raises(cg.CacheError) as exc:
        guard.put(main, key="deps", data=b"lock-v2", now=NOW)
    assert exc.value.code == "CACHE_KEY_CONFLICT"
    assert guard.get(main, key="deps", now=NOW)["data"] == b"lock-v1"
    assert guard.stats()["conflict"] == 1
    assert guard.stats()["rejected"] == 1


def test_put_validates_digest_key_payload_and_ttl():
    guard = make_guard(max_entry_bytes=10)
    main = scope(guard)
    with pytest.raises(cg.CacheError) as exc:
        guard.put(main, key="k", data=b"abc", digest="00" * 32)
    assert exc.value.code == "CACHE_DIGEST_MISMATCH"
    with pytest.raises(cg.CacheError) as exc:
        guard.put(main, key="../escape", data=b"abc")
    assert exc.value.code == "CACHE_INVALID_KEY"
    with pytest.raises(cg.CacheError) as exc:
        guard.put(main, key="k", data="text")
    assert exc.value.code == "CACHE_INVALID_DATA"
    with pytest.raises(cg.CacheError) as exc:
        guard.put(main, key="k", data=b"")
    assert exc.value.code == "CACHE_INVALID_DATA"
    with pytest.raises(cg.CacheError) as exc:
        guard.put(main, key="k", data=b"x" * 11)
    assert exc.value.code == "CACHE_ENTRY_TOO_LARGE"
    for ttl in (0, -1, 10 ** 9):
        with pytest.raises(cg.CacheError) as exc:
            guard.put(main, key="k", data=b"x", ttl=ttl)
        assert exc.value.code == "CACHE_TTL_INVALID"
    with pytest.raises(cg.CacheError) as exc:
        guard.get("app|branch|refs/heads/none", key="k")
    assert exc.value.code == "CACHE_UNKNOWN_SCOPE"


def test_branch_scope_falls_back_to_protected_but_pr_does_not():
    guard = make_guard()
    main = scope(guard)
    feature = scope(guard, FEATURE)
    pr = scope(guard, PR1, event="pull_request")
    guard.put(main, key="toolchain", data=b"pinned", now=NOW)

    fallback = guard.resolve(feature, key="toolchain", now=NOW)
    assert fallback["data"] == b"pinned"
    assert fallback["fallback"] is True
    assert fallback["source_scope"] == main
    assert guard.resolve(pr, key="toolchain", now=NOW) is None

    guard.put(feature, key="toolchain", data=b"branch-local", now=NOW)
    own = guard.resolve(feature, key="toolchain", now=NOW)
    assert own["fallback"] is False
    assert own["data"] == b"branch-local"
    assert guard.stats()["fallback"] == 1

    guard.put(feature, key="branch-only", data=b"local", now=NOW)
    assert guard.resolve(main, key="branch-only", now=NOW) is None
    assert guard.get(main, key="branch-only", now=NOW) is None


def test_pull_request_scopes_are_isolated_from_each_other():
    guard = make_guard()
    first = scope(guard, PR1, event="pull_request")
    second = scope(guard, PR2, event="pull_request")
    guard.put(first, key="dist", data=b"pr-one", now=NOW)
    guard.put(second, key="dist", data=b"pr-two", now=NOW)
    assert guard.get(first, key="dist", now=NOW)["data"] == b"pr-one"
    assert guard.get(second, key="dist", now=NOW)["data"] == b"pr-two"
    assert guard.resolve(first, key="dist", now=NOW)["source_scope"] == first
    assert len(guard.entries(first)) == 1
    assert guard.stats()["conflict"] == 0


def test_promotion_requires_trust_and_matching_digest():
    guard = make_guard()
    main = scope(guard)
    feature = scope(guard, FEATURE)
    pr = scope(guard, PR1, event="pull_request")
    guard.put(feature, key="release", data=b"build-7", now=NOW)
    promoted = guard.promote(feature, key="release", target_scope_id=main, now=NOW)
    assert promoted["status"] == "promoted"
    assert guard.get(main, key="release", now=NOW)["data"] == b"build-7"
    assert guard.promote(feature, key="release", target_scope_id=main,
                         now=NOW)["status"] == "unchanged"

    guard.put(pr, key="release", data=b"fork-build", now=NOW)
    with pytest.raises(cg.CacheError) as exc:
        guard.promote(pr, key="release", target_scope_id=main, now=NOW)
    assert exc.value.code == "CACHE_SCOPE_DENIED"
    with pytest.raises(cg.CacheError) as exc:
        guard.promote(feature, key="missing", target_scope_id=main, now=NOW)
    assert exc.value.code == "CACHE_NOT_FOUND"
    with pytest.raises(cg.CacheError) as exc:
        guard.promote(feature, key="release", target_scope_id=feature, now=NOW)
    assert exc.value.code == "CACHE_SCOPE_DENIED"

    guard.put(main, key="clash", data=b"trusted", now=NOW)
    guard.put(feature, key="clash", data=b"branch", now=NOW)
    with pytest.raises(cg.CacheError) as exc:
        guard.promote(feature, key="clash", target_scope_id=main, now=NOW)
    assert exc.value.code == "CACHE_KEY_CONFLICT"


def test_close_scope_only_removes_untrusted_entries():
    guard = make_guard()
    main = scope(guard)
    feature = scope(guard, FEATURE)
    pr = scope(guard, PR1, event="pull_request")
    guard.put(pr, key="a", data=b"1", now=NOW)
    guard.put(pr, key="b", data=b"2", now=NOW)
    assert guard.close_scope(pr) == 2
    assert guard.entries(pr) == []
    assert guard.get(pr, key="a", now=NOW) is None
    for trusted in (main, feature):
        with pytest.raises(cg.CacheError) as exc:
            guard.close_scope(trusted)
        assert exc.value.code == "CACHE_SCOPE_DENIED"
    with pytest.raises(cg.CacheError) as exc:
        guard.close_scope("app|pr|refs/pull/9/head")
    assert exc.value.code == "CACHE_UNKNOWN_SCOPE"


def test_ttl_expiry_and_evict_count():
    guard = make_guard()
    main = scope(guard)
    guard.put(main, key="short", data=b"a", ttl=10, now=NOW)
    guard.put(main, key="long", data=b"b", now=NOW)
    assert guard.get(main, key="short", now=NOW)["data"] == b"a"
    assert guard.evict(now="2026-11-24T10:00:20+00:00") == 1
    assert guard.get(main, key="short", now="2026-11-24T10:00:20+00:00") is None
    assert guard.get(main, key="long", now="2026-11-24T10:00:20+00:00")["data"] == b"b"
    assert guard.evict(now="2026-11-24T10:00:20+00:00") == 0


def test_quota_evicts_untrusted_then_branch_but_keeps_protected():
    guard = make_guard(max_bytes=100)
    main = scope(guard)
    feature = scope(guard, FEATURE)
    pr = scope(guard, PR1, event="pull_request")

    guard.put(main, key="p", data=b"a" * 40, now=NOW)
    guard.put(pr, key="u1", data=b"b" * 30, now=NOW)
    guard.put(pr, key="u2", data=b"c" * 40, now=NOW)
    assert guard.get(pr, key="u1", now=NOW) is None
    assert guard.get(pr, key="u2", now=NOW) is not None

    guard.put(feature, key="b", data=b"d" * 50, now=NOW)
    assert guard.get(pr, key="u2", now=NOW) is None
    assert guard.get(feature, key="b", now=NOW) is not None

    guard.put(main, key="p2", data=b"e" * 30, now=NOW)
    assert guard.get(feature, key="b", now=NOW) is None
    assert guard.get(main, key="p", now=NOW) is not None
    assert guard.get(main, key="p2", now=NOW) is not None

    with pytest.raises(cg.CacheError) as exc:
        guard.put(main, key="p3", data=b"f" * 40, now=NOW)
    assert exc.value.code == "CACHE_QUOTA_EXCEEDED"
    assert guard.entries(main) and len(guard.entries(main)) == 2
    assert guard.stats()["evicted"] == 3


def test_concurrent_puts_store_once_per_key():
    guard = make_guard()
    main = scope(guard)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def same_worker():
        barrier.wait()
        try:
            out = guard.put(main, key="race", data=b"same", now=NOW)
        except cg.CacheError as exc:
            out = {"status": "error", "code": exc.code}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=same_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for row in results if row["status"] == "stored") == 1
    assert sum(1 for row in results if row["status"] == "unchanged") == 7
    assert guard.stats()["put"] == 1

    barrier2 = threading.Barrier(8)
    second = []

    def different_worker(index):
        barrier2.wait()
        try:
            out = guard.put(main, key="clash", data=f"payload-{index}".encode(),
                            now=NOW)
        except cg.CacheError as exc:
            out = {"status": "error", "code": exc.code}
        with lock:
            second.append(out)

    threads = [threading.Thread(target=different_worker, args=(i,))
               for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for row in second if row["status"] == "stored") == 1
    assert sum(1 for row in second if row.get("code") == "CACHE_KEY_CONFLICT") == 7
    assert guard.stats()["conflict"] == 7


def test_snapshot_restore_keeps_scopes_and_entries():
    guard = make_guard()
    main = scope(guard)
    feature = scope(guard, FEATURE)
    pr = scope(guard, PR1, event="pull_request")
    guard.put(main, key="base", data=b"trusted", now=NOW)
    guard.put(pr, key="fork", data=b"untrusted", now=NOW)
    state = guard.snapshot()
    state["entries"][(pr, "fork")]["trusted"] = True
    assert guard.get(pr, key="fork", now=NOW) is not None

    restored = make_guard()
    restored.restore(guard.snapshot())
    assert restored.get(pr, key="fork", now=NOW)["data"] == b"untrusted"
    assert restored.resolve(feature, key="base", now=NOW)["source_scope"] == main
    assert restored.stats()["put"] == 2
    assert restored.entries(main)[0]["key"] == "base"


def test_corrupt_snapshot_is_refused_without_touching_state():
    guard = make_guard()
    main = scope(guard)
    guard.put(main, key="keep", data=b"value", now=NOW)
    good = guard.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "repositories": {}, "scopes": {}, "entries": {},
         "stats": {}},
        dict(good, repositories=[]),
        dict(good, entries={("app|protected|refs/heads/main", "keep"): "nope"}),
        dict(good, stats={"put": 1}),
        dict(good, scopes={main: {"scope_id": main, "repo": "missing",
                                  "kind": "branch", "trusted": True,
                                  "fork": False}}),
    ]
    for bad in bad_states:
        with pytest.raises(cg.CacheError) as exc:
            guard.restore(bad)
        assert exc.value.code == "CACHE_SNAPSHOT_INVALID"
    assert guard.get(main, key="keep", now=NOW)["data"] == b"value"


def test_stats_and_entry_listing_accounting():
    guard = make_guard()
    main = scope(guard)
    feature = scope(guard, FEATURE)
    guard.put(main, key="shared", data=b"one", now=NOW)
    guard.get(main, key="shared", now=NOW)
    guard.resolve(feature, key="shared", now=NOW)
    guard.put(feature, key="local", data=b"two", now=NOW)
    with pytest.raises(cg.CacheError):
        guard.put(feature, key="local", data=b"three", now=NOW)
    assert guard.stats() == {"put": 2, "hit": 2, "fallback": 1, "evicted": 0,
                             "conflict": 1, "rejected": 1}
    rows = guard.entries()
    assert [(row["scope_id"], row["key"]) for row in rows] == [
        (feature, "local"), (main, "shared")]
    assert [row["key"] for row in guard.entries(main)] == ["shared"]
    guard.reset_stats()
    assert guard.stats() == {"put": 0, "hit": 0, "fallback": 0, "evicted": 0,
                             "conflict": 0, "rejected": 0}
