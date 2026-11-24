import threading

import pytest

import http_shared_cache_guard as hc

NOW = "2026-11-24T10:00:00+00:00"
ORIGIN = "https://cdn.example.com"


def request(**overrides):
    base = {"origin": ORIGIN, "path": "/assets/app.js", "query": {}, "headers": {}}
    base.update(overrides)
    return base


def response(**overrides):
    base = {
        "status": 200,
        "body": "body-v1",
        "headers": {"cache-control": "max-age=60", "etag": '"v1"'},
    }
    base.update(overrides)
    return base


def make(**kwargs):
    guard = hc.HttpSharedCacheGuard(**kwargs)
    guard.register_origin(ORIGIN, max_ttl=600)
    return guard


def test_fresh_hit_reports_body_and_remaining_ttl():
    guard = make()
    stored = guard.store(request(), response(), now=NOW)
    assert stored["stored"] is True
    assert stored["ttl"] == 60

    hit = guard.lookup(request(), now="2026-11-24T10:00:30+00:00")
    assert hit["status"] == "hit"
    assert hit["body"] == "body-v1"
    assert hit["status_code"] == 200
    assert hit["ttl_remaining"] == 30
    assert guard.stats() == {"hits": 1, "misses": 0, "stales": 0,
                             "stores": 1, "evictions": 0}


def test_no_store_and_private_responses_are_kept_out_of_shared_cache():
    guard = make()
    blocked = guard.store(request(), response(headers={"cache-control": "no-store"}),
                          now=NOW)
    assert blocked == {"stored": False, "reason": "no_store"}
    private = guard.store(request(path="/assets/private.js"),
                          response(headers={"cache-control": "private, max-age=60"}),
                          now=NOW)
    assert private == {"stored": False, "reason": "private"}
    assert guard.lookup(request(), now=NOW)["status"] == "miss"
    assert guard.lookup(request(path="/assets/private.js"), now=NOW)["status"] == "miss"
    assert guard.stats()["stores"] == 0


def test_authorized_requests_require_origin_opt_in():
    guard = make()
    with_auth = request(headers={"authorization": "Bearer token-1"})
    assert guard.store(with_auth, response(), now=NOW) == {
        "stored": False, "reason": "authorized"}

    guard.register_origin("https://api.example.com", allow_authorized=True,
                          max_ttl=600)
    open_req = request(origin="https://api.example.com",
                       headers={"authorization": "Bearer token-1"})
    assert guard.store(open_req, response(), now=NOW)["stored"] is True
    assert guard.lookup(open_req, now=NOW)["status"] == "hit"


def test_unknown_origin_and_traversal_paths_are_refused():
    guard = make()
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.store(request(origin="https://ghost.example.com"), response(), now=NOW)
    assert exc.value.code == "HTTP_CACHE_ORIGIN_UNKNOWN"
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.lookup(request(origin="https://ghost.example.com"), now=NOW)
    assert exc.value.code == "HTTP_CACHE_ORIGIN_UNKNOWN"
    for bad in ("/assets/../admin.js", "/assets;v=1/app.js", "assets/app.js"):
        with pytest.raises(hc.HttpCacheError) as exc:
            guard.store(request(path=bad), response(), now=NOW)
        assert exc.value.code == "HTTP_CACHE_BAD_PATH"


def test_oversized_object_is_refused_and_leaves_cache_empty():
    guard = make(max_object_bytes=8)
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.store(request(), response(body="0123456789"), now=NOW)
    assert exc.value.code == "HTTP_CACHE_OBJECT_TOO_LARGE"
    assert guard.lookup(request(), now=NOW)["status"] == "miss"
    assert guard.stats()["stores"] == 0


def test_query_order_is_normalised_while_path_stays_exact():
    guard = make()
    guard.store(request(query={"b": "2", "a": "1"}), response(), now=NOW)
    same = guard.lookup(request(query={"a": "1", "b": "2"}), now=NOW)
    assert same["status"] == "hit"
    assert guard.lookup(request(query={"a": "1", "b": "3"}), now=NOW)["status"] == "miss"
    assert guard.lookup(request(path="/assets/app.js/"), now=NOW)["status"] == "miss"


def test_vary_values_partition_entries_and_unknown_vary_is_refused():
    guard = hc.HttpSharedCacheGuard()
    guard.register_origin(ORIGIN, vary_headers=("Accept-Encoding",), max_ttl=600)
    gzip_req = request(headers={"accept-encoding": "gzip"})
    stored = guard.store(gzip_req, response(headers={"cache-control": "max-age=60",
                                                     "vary": "accept-encoding"}),
                         now=NOW)
    assert stored["vary"] == ["accept-encoding"]
    assert guard.lookup(request(headers={"accept-encoding": "br"}),
                        now=NOW)["status"] == "miss"
    assert guard.lookup(gzip_req, now=NOW)["status"] == "hit"

    with pytest.raises(hc.HttpCacheError) as exc:
        guard.store(request(path="/assets/x.js"),
                    response(headers={"cache-control": "max-age=60", "vary": "cookie"}),
                    now=NOW)
    assert exc.value.code == "HTTP_CACHE_VARY_UNSUPPORTED"
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.store(request(path="/assets/y.js"),
                    response(headers={"cache-control": "max-age=60", "vary": "*"}),
                    now=NOW)
    assert exc.value.code == "HTTP_CACHE_VARY_UNSUPPORTED"


def test_ttl_precedence_and_origin_cap():
    guard = make(default_ttl=45)
    capped = guard.store(request(path="/capped.js"),
                         response(headers={"cache-control": "max-age=3600"}), now=NOW)
    assert capped["ttl"] == 600
    shared = guard.store(request(path="/shared.js"),
                         response(headers={"cache-control": "s-maxage=30, max-age=300"}),
                         now=NOW)
    assert shared["ttl"] == 30
    default = guard.store(request(path="/default.js"), response(headers={}), now=NOW)
    assert default["ttl"] == 45


def test_freshness_boundary_moves_to_stale_then_expires():
    guard = make(stale_window=30)
    guard.store(request(), response(), now=NOW)
    edge = guard.lookup(request(), now="2026-11-24T10:01:00+00:00")
    assert edge["status"] == "stale"
    assert edge["stale_for"] == 0
    late = guard.lookup(request(), now="2026-11-24T10:01:29+00:00")
    assert late["status"] == "stale"
    assert late["stale_for"] == 29
    gone = guard.lookup(request(), now="2026-11-24T10:01:30+00:00")
    assert gone["status"] == "miss"
    assert guard.lookup(request(), now="2026-11-24T10:01:31+00:00")["status"] == "miss"
    assert guard.stats() == {"hits": 0, "misses": 2, "stales": 2,
                             "stores": 1, "evictions": 1}

    strict = make(stale_window=0)
    strict.store(request(), response(), now=NOW)
    assert strict.lookup(request(), now="2026-11-24T10:01:00+00:00")["status"] == "miss"


def test_invalidate_by_prefix_counts_only_matching_paths():
    guard = make()
    guard.store(request(path="/assets/a.js"), response(), now=NOW)
    guard.store(request(path="/assets/b.css"), response(), now=NOW)
    guard.store(request(path="/api/data.json"), response(), now=NOW)
    assert guard.invalidate(ORIGIN, path_prefix="/assets/") == 2
    assert guard.lookup(request(path="/assets/a.js"), now=NOW)["status"] == "miss"
    assert guard.lookup(request(path="/api/data.json"), now=NOW)["status"] == "hit"
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.invalidate("https://ghost.example.com")
    assert exc.value.code == "HTTP_CACHE_ORIGIN_UNKNOWN"


def test_capacity_purges_expired_entries_before_refusing():
    guard = make(max_entries=2, default_ttl=10, stale_window=0)
    assert guard.store(request(path="/one.js"), response(headers={}), now=NOW)["stored"] is True
    assert guard.store(request(path="/two.js"), response(headers={}), now=NOW)["stored"] is True
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.store(request(path="/three.js"), response(headers={}), now=NOW)
    assert exc.value.code == "HTTP_CACHE_FULL"

    later = "2026-11-24T10:00:20+00:00"
    assert guard.store(request(path="/three.js"), response(headers={}), now=later)["stored"] is True
    assert guard.purge(now=later) == 0
    assert guard.store(request(path="/four.js"), response(headers={}), now=later)["stored"] is True
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.store(request(path="/five.js"), response(headers={}), now=later)
    assert exc.value.code == "HTTP_CACHE_FULL"
    assert guard.stats()["stores"] == 4
    assert guard.stats()["evictions"] == 2


def test_revalidate_extends_on_match_and_downgrades_on_mismatch():
    guard = make(stale_window=30)
    guard.store(request(), response(headers={"cache-control": "max-age=30",
                                             "etag": '"v1"'}), now=NOW)
    hit = guard.revalidate(request(), now="2026-11-24T10:00:25+00:00", etag='"v1"')
    assert hit["status"] == "not_modified"
    assert hit["revalidated"] is True
    assert hit["ttl"] == 30
    assert guard.lookup(request(), now="2026-11-24T10:00:50+00:00")["status"] == "hit"

    miss = guard.revalidate(request(), now="2026-11-24T10:00:50+00:00", etag='"v2"')
    assert miss["status"] == "modified"
    assert miss["revalidated"] is False
    downgraded = guard.lookup(request(), now="2026-11-24T10:00:50+00:00")
    assert downgraded["status"] == "stale"
    assert guard.revalidate(request(path="/absent.js"), now=NOW)["status"] == "miss"


def test_fill_tokens_allow_only_one_inflight_request_per_key():
    guard = make()
    first = guard.begin_fill(request())
    assert first["token"]
    with pytest.raises(hc.HttpCacheError) as exc:
        guard.begin_fill(request())
    assert exc.value.code == "HTTP_CACHE_FILL_IN_PROGRESS"
    other = guard.begin_fill(request(path="/assets/other.js"))
    assert other["token"] != first["token"]

    with pytest.raises(hc.HttpCacheError) as exc:
        guard.end_fill(request(), token="fill-does-not-exist")
    assert exc.value.code == "HTTP_CACHE_UNKNOWN_FILL"
    guard.end_fill(request(), token=first["token"])
    retry = guard.begin_fill(request())
    assert retry["token"] != first["token"]


def test_snapshot_restore_is_deep_and_corrupt_state_is_atomic():
    guard = make()
    guard.store(request(path="/snap-a.js"), response(), now=NOW)
    state = guard.snapshot()
    guard.store(request(path="/snap-b.js"), response(), now=NOW)

    restored = hc.HttpSharedCacheGuard()
    restored.restore(state)
    assert restored.lookup(request(path="/snap-a.js"), now=NOW)["status"] == "hit"
    assert restored.lookup(request(path="/snap-b.js"), now=NOW)["status"] == "miss"

    state["stats"]["hits"] = 999
    state["entries"].clear()
    assert restored.stats() == {"hits": 1, "misses": 1, "stales": 0,
                                "stores": 1, "evictions": 0}
    assert restored.lookup(request(path="/snap-a.js"), now=NOW)["status"] == "hit"

    good_stats = {"hits": 0, "misses": 0, "stales": 0, "stores": 1, "evictions": 0}
    bad_states = [
        None,
        {},
        {"version": 2, "origins": {}, "entries": {}, "fills": {},
         "token_seq": 0, "stats": good_stats},
        {"version": 1, "origins": {}, "entries": "not-a-map", "fills": {},
         "token_seq": 0, "stats": good_stats},
        {"version": 1, "origins": {}, "entries": {}, "fills": {},
         "token_seq": 0, "stats": {"hits": 0}},
    ]
    for bad in bad_states:
        with pytest.raises(hc.HttpCacheError) as exc:
            restored.restore(bad)
        assert exc.value.code == "HTTP_CACHE_SNAPSHOT_INVALID"
    assert restored.lookup(request(path="/snap-a.js"), now=NOW)["status"] == "hit"


def test_concurrent_stores_respect_capacity_exactly():
    guard = make(max_entries=4)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            guard.store(request(path="/race-%d.js" % index), response(), now=NOW)
            result = "stored"
        except Exception as exc:
            result = getattr(exc, "code", repr(exc))
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("stored") == 4
    assert outcomes.count("HTTP_CACHE_FULL") == 4
    assert guard.stats()["stores"] == 4
