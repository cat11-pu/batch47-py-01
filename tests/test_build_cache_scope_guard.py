import threading

import pytest

import build_cache_scope_guard as cache

NOW = "2026-11-24T10:00:00+00:00"
LATER = "2026-11-24T10:10:00+00:00"
MAIN = "refs/heads/main"
FEAT = "refs/heads/feature-x"
FEAT2 = "refs/heads/feature-y"
PR = "refs/pull/7/merge"


def dg(ch):
    return "sha256:" + ch * 64


def make(**kwargs):
    guard = cache.BuildCacheGuard(**kwargs)
    guard.register_project("proj", default_branch=MAIN)
    return guard


def test_project_registration_is_idempotent_and_conflicts():
    guard = cache.BuildCacheGuard()
    first = guard.register_project("proj", default_branch=MAIN)
    assert first == {"project": "proj", "default_branch": MAIN, "visibility": "private"}

    again = guard.register_project("proj", default_branch=MAIN)
    assert again["default_branch"] == MAIN

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.register_project("proj", default_branch=MAIN, visibility="public")
    assert exc.value.code == "CACHE_CONFLICT"

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.register_project("proj", default_branch=FEAT)
    assert exc.value.code == "CACHE_CONFLICT"

    desc = guard.describe_project("proj")
    assert desc["entries"] == 0
    assert desc["bytes"] == 0
    assert desc["default_branch"] == MAIN

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.describe_project("nope")
    assert exc.value.code == "CACHE_UNKNOWN_PROJECT"


def test_put_and_get_round_trip():
    guard = make()
    stored = guard.put("proj", "deps-linux", scope=MAIN, size=120,
                       digest=dg("a"), now=NOW)
    assert stored["status"] == "stored"
    assert stored["expires_at"] == "2026-11-24T11:00:00+00:00"
    assert stored["digest"] == dg("a")

    hit = guard.get("proj", "deps-linux", scope=MAIN, now=NOW)
    assert hit["status"] == "hit"
    assert hit["digest"] == dg("a")
    assert hit["size"] == 120
    assert hit["scope"] == MAIN
    assert guard.stats() == {"writes": 1, "hits": 1, "misses": 0,
                            "evictions": 0, "rejected": 0}


def test_put_is_immutable_per_key_and_digest():
    guard = make()
    guard.put("proj", "deps-linux", scope=MAIN, size=50, digest=dg("a"), now=NOW)

    same = guard.put("proj", "deps-linux", scope=MAIN, size=50, digest=dg("a"), now=NOW)
    assert same["status"] == "exists"
    assert guard.stats()["writes"] == 1

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.put("proj", "deps-linux", scope=MAIN, size=50, digest=dg("b"), now=NOW)
    assert exc.value.code == "CACHE_KEY_CONFLICT"

    hit = guard.get("proj", "deps-linux", scope=MAIN, now=NOW)
    assert hit["digest"] == dg("a")
    assert guard.describe_project("proj")["bytes"] == 50
    assert guard.stats()["rejected"] == 1


def test_put_rejects_invalid_keys_digests_sizes_and_scopes():
    guard = make()
    cases = [
        ({"key": "", "size": 10, "digest": dg("a"), "scope": MAIN}, "CACHE_BAD_KEY"),
        ({"key": "bad key", "size": 10, "digest": dg("a"), "scope": MAIN}, "CACHE_BAD_KEY"),
        ({"key": "ok", "size": 10, "digest": "sha256:zz", "scope": MAIN}, "CACHE_BAD_DIGEST"),
        ({"key": "ok", "size": 10, "digest": "md5:" + "a" * 32, "scope": MAIN}, "CACHE_BAD_DIGEST"),
        ({"key": "ok", "size": 0, "digest": dg("a"), "scope": MAIN}, "CACHE_BAD_SIZE"),
        ({"key": "ok", "size": 10, "digest": dg("a"), "scope": MAIN, "ttl": 0}, "CACHE_BAD_TTL"),
        ({"key": "ok", "size": 10, "digest": dg("a"), "scope": "main"}, "CACHE_BAD_SCOPE"),
    ]
    for kwargs, code in cases:
        with pytest.raises(cache.CacheScopeError) as exc:
            guard.put("proj", kwargs.pop("key"), now=NOW, **kwargs)
        assert exc.value.code == code

    assert guard.describe_project("proj")["entries"] == 0
    assert guard.stats()["rejected"] == len(cases)


def test_pull_request_scope_never_leaks_to_other_refs():
    guard = make()
    guard.put("proj", "pr-cache", scope=PR, size=10, digest=dg("a"), now=NOW)
    assert guard.get("proj", "pr-cache", scope=PR, now=NOW)["status"] == "hit"

    for scope in (MAIN, FEAT):
        with pytest.raises(cache.CacheScopeError) as exc:
            guard.get("proj", "pr-cache", scope=scope, now=NOW)
        assert exc.value.code == "CACHE_SCOPE_DENIED"


def test_default_branch_cache_is_shared_across_branches():
    guard = make()
    guard.put("proj", "base-cache", scope=MAIN, size=10, digest=dg("a"), now=NOW)
    guard.put("proj", "feature-cache", scope=FEAT, size=20, digest=dg("b"), now=NOW)

    from_feature = guard.get("proj", "base-cache", scope=FEAT, now=NOW)
    assert from_feature["status"] == "hit"
    assert from_feature["scope"] == MAIN

    from_main = guard.get("proj", "feature-cache", scope=MAIN, now=NOW)
    assert from_main["status"] == "hit"
    assert from_main["scope"] == FEAT

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.get("proj", "feature-cache", scope=FEAT2, now=NOW)
    assert exc.value.code == "CACHE_SCOPE_DENIED"

    assert guard.get("proj", "feature-cache", scope=FEAT, now=NOW)["size"] == 20


def test_restore_lookup_orders_newest_first_and_filters_scope():
    guard = make()
    guard.put("proj", "deps-a", scope=MAIN, size=10, digest=dg("a"), now=NOW)
    guard.put("proj", "deps-b", scope=MAIN, size=10, digest=dg("b"),
              now="2026-11-24T10:01:00+00:00")
    guard.put("proj", "deps-c", scope=FEAT, size=10, digest=dg("c"),
              now="2026-11-24T10:02:00+00:00")
    guard.put("proj", "other", scope=MAIN, size=10, digest=dg("d"),
              now="2026-11-24T10:03:00+00:00")
    guard.put("proj", "deps-pr", scope=PR, size=10, digest=dg("e"),
              now="2026-11-24T10:05:00+00:00")

    rows = guard.restore_lookup("proj", "deps-", scope=MAIN, limit=5, now=LATER)
    assert [row["key"] for row in rows] == ["deps-c", "deps-b", "deps-a"]
    assert guard.restore_lookup("proj", "deps-", scope=MAIN, limit=1, now=LATER)[0]["key"] == "deps-c"

    sibling = guard.restore_lookup("proj", "deps-", scope=FEAT2, limit=5, now=LATER)
    assert [row["key"] for row in sibling] == ["deps-b", "deps-a"]

    pull = guard.restore_lookup("proj", "deps-", scope=PR, limit=5, now=LATER)
    assert [row["key"] for row in pull] == ["deps-pr", "deps-b", "deps-a"]
    assert guard.restore_lookup("proj", "zzz", scope=MAIN, now=LATER) == []

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.restore_lookup("proj", "deps-", scope=MAIN, limit=0, now=LATER)
    assert exc.value.code == "CACHE_BAD_LIMIT"
    with pytest.raises(cache.CacheScopeError) as exc:
        guard.restore_lookup("proj", "deps-", scope=MAIN, limit=21, now=LATER)
    assert exc.value.code == "CACHE_BAD_LIMIT"


def test_entries_expire_at_the_ttl_boundary():
    guard = make()
    guard.put("proj", "short", scope=MAIN, size=10, digest=dg("a"),
              ttl=600, now=NOW)
    assert guard.get("proj", "short", scope=MAIN,
                     now="2026-11-24T10:09:59+00:00")["status"] == "hit"

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.get("proj", "short", scope=MAIN, now=LATER)
    assert exc.value.code == "CACHE_MISS"
    assert guard.describe_project("proj")["entries"] == 0

    again = guard.put("proj", "short", scope=MAIN, size=10, digest=dg("b"), now=LATER)
    assert again["status"] == "stored"
    assert guard.describe_project("proj")["entries"] == 1


def test_purge_expired_removes_only_stale_entries():
    guard = make()
    guard.put("proj", "short", scope=MAIN, size=10, digest=dg("a"), ttl=60, now=NOW)
    guard.put("proj", "long", scope=MAIN, size=20, digest=dg("b"), now=NOW)

    assert guard.purge_expired(now="2026-11-24T10:00:30+00:00") == 0
    assert guard.purge_expired(now="2026-11-24T10:01:00+00:00") == 1
    assert guard.describe_project("proj")["entries"] == 1
    assert guard.describe_project("proj")["bytes"] == 20
    with pytest.raises(cache.CacheScopeError) as exc:
        guard.get("proj", "short", scope=MAIN, now=LATER)
    assert exc.value.code == "CACHE_MISS"
    assert guard.get("proj", "long", scope=MAIN, now=LATER)["status"] == "hit"


def test_entry_capacity_evicts_least_recently_used():
    guard = make(max_entries=2)
    guard.put("proj", "a", scope=MAIN, size=10, digest=dg("a"), now=NOW)
    guard.put("proj", "b", scope=MAIN, size=10, digest=dg("b"), now=NOW)
    guard.get("proj", "a", scope=MAIN, now=NOW)
    guard.put("proj", "c", scope=MAIN, size=10, digest=dg("c"), now=LATER)

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.get("proj", "b", scope=MAIN, now=LATER)
    assert exc.value.code == "CACHE_MISS"
    assert guard.get("proj", "a", scope=MAIN, now=LATER)["status"] == "hit"
    assert guard.get("proj", "c", scope=MAIN, now=LATER)["status"] == "hit"
    assert guard.stats()["evictions"] == 1
    assert guard.describe_project("proj")["entries"] == 2


def test_byte_budget_evicts_and_oversized_payload_is_refused():
    guard = make(max_bytes=100)
    guard.put("proj", "a", scope=MAIN, size=60, digest=dg("a"), now=NOW)
    guard.put("proj", "b", scope=MAIN, size=30, digest=dg("b"), now=NOW)
    guard.put("proj", "c", scope=MAIN, size=20, digest=dg("c"), now=LATER)

    assert guard.describe_project("proj")["bytes"] == 50
    assert guard.describe_project("proj")["entries"] == 2
    with pytest.raises(cache.CacheScopeError) as exc:
        guard.get("proj", "a", scope=MAIN, now=LATER)
    assert exc.value.code == "CACHE_MISS"

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.put("proj", "huge", scope=MAIN, size=150, digest=dg("d"), now=LATER)
    assert exc.value.code == "CACHE_TOO_LARGE"
    assert guard.describe_project("proj")["entries"] == 2
    assert guard.stats()["rejected"] == 1


def test_invalidate_by_key_scope_and_project():
    guard = make()
    guard.put("proj", "a", scope=MAIN, size=10, digest=dg("a"), now=NOW)
    guard.put("proj", "b", scope=MAIN, size=10, digest=dg("b"), now=NOW)
    guard.put("proj", "c", scope=FEAT, size=10, digest=dg("c"), now=NOW)

    assert guard.invalidate("proj", key="a") == 1
    assert guard.invalidate("proj", scope=FEAT) == 1
    assert guard.invalidate("proj") == 1
    with pytest.raises(cache.CacheScopeError) as exc:
        guard.get("proj", "b", scope=MAIN, now=NOW)
    assert exc.value.code == "CACHE_MISS"
    assert guard.describe_project("proj")["entries"] == 0

    with pytest.raises(cache.CacheScopeError) as exc:
        guard.invalidate("nope")
    assert exc.value.code == "CACHE_UNKNOWN_PROJECT"


def test_snapshot_is_detached_and_restore_round_trips():
    guard = make()
    guard.put("proj", "a", scope=MAIN, size=10, digest=dg("a"), ttl=7200, now=NOW)
    guard.put("proj", "b", scope=FEAT, size=20, digest=dg("b"), ttl=7200, now=NOW)
    stats_before = guard.stats()
    state = guard.snapshot()
    assert state["version"] == 1

    state["stats"]["hits"] = 99
    state["projects"]["proj"]["visibility"] = "public"
    assert guard.stats() == stats_before
    assert guard.describe_project("proj")["visibility"] == "private"

    clean = guard.snapshot()
    guard.put("proj", "c", scope=MAIN, size=30, digest=dg("c"), now=LATER)

    restored = cache.BuildCacheGuard()
    restored.restore(clean)
    assert restored.get("proj", "a", scope=MAIN, now=NOW)["expires_at"] == \
        "2026-11-24T12:00:00+00:00"
    assert restored.get("proj", "b", scope=FEAT, now=NOW)["status"] == "hit"
    with pytest.raises(cache.CacheScopeError) as exc:
        restored.get("proj", "c", scope=MAIN, now=NOW)
    assert exc.value.code == "CACHE_MISS"
    assert restored.stats()["writes"] == 2
    assert restored.stats()["hits"] == 2


def test_corrupt_snapshot_is_rejected_and_state_survives():
    guard = make()
    guard.put("proj", "keep", scope=MAIN, size=10, digest=dg("a"), now=NOW)
    before = guard.stats()

    bad_states = [
        None,
        {},
        {"version": 2, "projects": {}, "entries": {}, "stats": {}, "tick": 0},
        {"version": 1, "projects": "nope", "entries": {}, "stats": {}, "tick": 0},
        {"version": 1, "projects": {}, "entries": [], "stats": {}, "tick": 0},
        {"version": 1, "projects": {}, "entries": {}, "stats": "nope", "tick": 0},
        {"version": 1, "projects": {}, "entries": {}, "stats": {}, "tick": "late"},
    ]
    for bad in bad_states:
        with pytest.raises(cache.CacheScopeError) as exc:
            guard.restore(bad)
        assert exc.value.code == "CACHE_SNAPSHOT_INVALID"

    assert guard.stats() == before
    assert guard.get("proj", "keep", scope=MAIN, now=NOW)["digest"] == dg("a")


def test_concurrent_put_keeps_one_digest_per_key():
    guard = make()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = guard.put("proj", "race", scope=MAIN, size=10,
                            digest=dg(format(index, "x")), now=NOW)
            outcome = out["status"]
        except cache.CacheScopeError as exc:
            outcome = exc.code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["CACHE_KEY_CONFLICT"] * 7 + ["stored"]
    assert guard.stats()["writes"] == 1
    assert guard.stats()["rejected"] == 7
    assert guard.describe_project("proj")["bytes"] == 10

    winner = guard.get("proj", "race", scope=MAIN, now=NOW)["digest"]
    barrier = threading.Barrier(8)
    repeats = []

    def repeater():
        barrier.wait()
        out = guard.put("proj", "race", scope=MAIN, size=10, digest=winner, now=LATER)
        with lock:
            repeats.append(out["status"])

    threads = [threading.Thread(target=repeater) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert repeats == ["exists"] * 8
    assert guard.stats()["writes"] == 1
    assert guard.describe_project("proj")["entries"] == 1
