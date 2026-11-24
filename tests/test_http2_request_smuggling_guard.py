import threading

import pytest

import http2_request_smuggling_guard as hg

T0 = "2026-11-23T10:00:00+00:00"


def make(**kwargs):
    guard = hg.RequestSmugglingGuard(**kwargs)
    guard.register_backend("edge", address="10.0.0.5:8080")
    return guard


def open_conn(guard, conn_id="c1", *, backend="edge", peer="203.0.113.9", now=T0):
    return guard.open_connection(conn_id, backend=backend, peer=peer, now=now)


def test_backend_registration_is_idempotent_and_rejects_conflicts():
    guard = hg.RequestSmugglingGuard()
    first = guard.register_backend("edge", address="10.0.0.5:8080")
    assert first == {
        "backend": "edge",
        "address": "10.0.0.5:8080",
        "protocol": "http/1.1",
        "max_streams": 100,
    }
    again = guard.register_backend(
        "edge", address="10.0.0.5:8080", protocol="http/1.1", max_streams=100)
    assert again == first

    with pytest.raises(hg.SmugglingError) as exc:
        guard.register_backend("edge", address="10.0.0.6:8080")
    assert exc.value.code == "SMUGGLE_CONFLICT"

    for kwargs in (
        {"address": ""},
        {"address": "10.0.0.7:80", "protocol": "http/3"},
        {"address": "10.0.0.7:80", "max_streams": 0},
    ):
        with pytest.raises(hg.SmugglingError) as exc:
            guard.register_backend("bad", **kwargs)
        assert exc.value.code == "SMUGGLE_BAD_BACKEND"


def test_connection_open_is_idempotent_and_backend_bound():
    guard = make(max_streams=7)
    guard.register_backend("shadow", address="10.0.0.9:8080", max_streams=3)

    record = open_conn(guard)
    assert record["state"] == "open"
    assert record["streams"] == 0
    assert record["stream_limit"] == 7

    again = guard.open_connection(
        "c1", backend="edge", peer="198.51.100.7", now="2026-11-23T10:05:00+00:00")
    assert again["streams"] == 0
    assert again["peer"] == "203.0.113.9"
    assert again["opened_at"] == record["opened_at"]

    assert open_conn(guard, "c2", backend="shadow")["stream_limit"] == 3

    with pytest.raises(hg.SmugglingError) as exc:
        guard.open_connection("c1", backend="shadow", peer="203.0.113.10")
    assert exc.value.code == "SMUGGLE_CONN_CONFLICT"

    with pytest.raises(hg.SmugglingError) as exc:
        guard.open_connection("c9", backend="missing", peer="203.0.113.10")
    assert exc.value.code == "SMUGGLE_UNKNOWN_BACKEND"


def test_content_length_duplicates_and_invalid_values():
    guard = make()
    plain = guard.normalize([("x-a", "1")])
    assert plain["content_length"] is None
    assert plain["chunked"] is False
    assert plain["framing"] == "none"

    parsed = guard.normalize(
        [("content-length", "5"), ("content-length", "5"), ("x-trace", "a")])
    assert parsed["content_length"] == 5
    assert parsed["framing"] == "content-length"
    assert parsed["headers"] == {"content-length": "5", "x-trace": "a"}

    with pytest.raises(hg.SmugglingError) as exc:
        guard.normalize([("content-length", "5"), ("content-length", "6")])
    assert exc.value.code == "SMUGGLE_LENGTH_CONFLICT"

    for value in ("abc", "-1", "5, 5", ""):
        with pytest.raises(hg.SmugglingError) as exc:
            guard.normalize([("content-length", value)])
        assert exc.value.code == "SMUGGLE_BAD_LENGTH"


def test_transfer_encoding_conflicts_are_refused():
    guard = make()
    parsed = guard.normalize([("transfer-encoding", "chunked")])
    assert parsed["chunked"] is True
    assert parsed["content_length"] is None
    assert parsed["framing"] == "chunked"

    with pytest.raises(hg.SmugglingError) as exc:
        guard.normalize([("transfer-encoding", "chunked"), ("content-length", "10")])
    assert exc.value.code == "SMUGGLE_TE_CL_MISMATCH"

    for value in ("gzip, chunked", "chunked, identity", "identity", "chunked, chunked"):
        with pytest.raises(hg.SmugglingError) as exc:
            guard.normalize([("transfer-encoding", value)])
        assert exc.value.code == "SMUGGLE_TE_AMBIGUITY"


def test_obs_fold_control_characters_and_bad_names_are_refused():
    guard = make()

    with pytest.raises(hg.SmugglingError) as exc:
        guard.normalize([("x-note", " folded")])
    assert exc.value.code == "SMUGGLE_HEADER_FOLD"

    for value in ("a\r\nX-Evil: 1", "a\x00b", "a\nb"):
        with pytest.raises(hg.SmugglingError) as exc:
            guard.normalize([("x-note", value)])
        assert exc.value.code == "SMUGGLE_CTRL_CHAR"

    for name in ("x:evil", "", " bad name"):
        with pytest.raises(hg.SmugglingError) as exc:
            guard.normalize([(name, "1")])
        assert exc.value.code == "SMUGGLE_BAD_HEADER_NAME"


def test_pseudo_headers_must_precede_regular_headers():
    guard = make()
    parsed = guard.normalize([
        (":method", "POST"),
        (":path", "/login"),
        (":scheme", "https"),
        (":authority", "api.example.com"),
        ("x-id", "7"),
    ])
    assert parsed["method"] == "POST"
    assert parsed["target"] == "/login"
    assert parsed["scheme"] == "https"
    assert parsed["authority"] == "api.example.com"
    assert parsed["headers"] == {"x-id": "7"}

    cases = [
        ([("x-id", "7"), (":method", "GET")], "SMUGGLE_PSEUDO_ORDER"),
        ([(":foo", "1")], "SMUGGLE_BAD_PSEUDO"),
        ([(":status", "200")], "SMUGGLE_BAD_PSEUDO"),
        ([(":path", "/a"), (":path", "/b")], "SMUGGLE_BAD_PSEUDO"),
        ([(":path", "")], "SMUGGLE_BAD_PSEUDO"),
    ]
    for headers, code in cases:
        with pytest.raises(hg.SmugglingError) as exc:
            guard.normalize(headers)
        assert exc.value.code == code


def test_header_size_and_count_limits_are_enforced():
    guard = make(max_header_bytes=24, max_headers=2)
    parsed = guard.normalize([("x-a", "1"), ("x-b", "2")])
    assert parsed["headers"] == {"x-a": "1", "x-b": "2"}

    with pytest.raises(hg.SmugglingError) as exc:
        guard.normalize([("x-a", "1"), ("x-b", "2"), ("x-c", "3")])
    assert exc.value.code == "SMUGGLE_TOO_MANY_HEADERS"

    tight = make(max_header_bytes=20)
    with pytest.raises(hg.SmugglingError) as exc:
        tight.normalize([("x-pad", "0123456789abcdef")])
    assert exc.value.code == "SMUGGLE_HEADER_TOO_LARGE"


def test_admit_assigns_stream_ids_and_reports_reuse():
    guard = make()
    open_conn(guard)
    parsed = guard.normalize([(":method", "GET"), (":path", "/a")])

    first = guard.admit("c1", parsed)
    assert first == {
        "conn_id": "c1",
        "stream_id": 1,
        "backend": "edge",
        "status": "forwarded",
        "reused": False,
    }
    second = guard.admit("c1", parsed)
    assert second["stream_id"] == 2
    assert second["reused"] is True

    state = guard.connection_state("c1")
    assert state["state"] == "open"
    assert state["streams"] == 2
    assert guard.stats() == {
        "forwarded": 2, "rejected": 0, "opened": 1, "closed": 0}

    guard.reset_stats()
    assert guard.stats() == {
        "forwarded": 0, "rejected": 0, "opened": 0, "closed": 0}

    with pytest.raises(hg.SmugglingError) as exc:
        guard.admit("c1", {"headers": {}})
    assert exc.value.code == "SMUGGLE_BAD_REQUEST"
    assert guard.stats()["rejected"] == 1


def test_connection_stream_budget_is_exhausted_once():
    guard = make()
    guard.register_backend("tight", address="10.0.0.11:8080", max_streams=2)
    open_conn(guard, "c1", backend="tight")
    parsed = guard.normalize([(":method", "GET"), (":path", "/")])

    assert guard.admit("c1", parsed)["stream_id"] == 1
    assert guard.admit("c1", parsed)["stream_id"] == 2

    with pytest.raises(hg.SmugglingError) as exc:
        guard.admit("c1", parsed)
    assert exc.value.code == "SMUGGLE_STREAM_LIMIT"
    assert guard.connection_state("c1")["streams"] == 2

    guard.release("c1", now=T0)
    with pytest.raises(hg.SmugglingError) as exc:
        guard.admit("c1", parsed)
    assert exc.value.code == "SMUGGLE_STREAM_LIMIT"

    open_conn(guard, "c2", backend="tight")
    assert guard.admit("c2", parsed)["stream_id"] == 1


def test_poisoned_connection_refuses_further_streams():
    guard = make()
    open_conn(guard)
    parsed = guard.normalize([(":method", "GET"), (":path", "/")])
    guard.admit("c1", parsed)

    released = guard.release("c1", clean=False, now=T0)
    assert released["state"] == "poisoned"
    assert released["streams"] == 1

    with pytest.raises(hg.SmugglingError) as exc:
        guard.admit("c1", parsed)
    assert exc.value.code == "SMUGGLE_CONN_POISONED"

    with pytest.raises(hg.SmugglingError) as exc:
        guard.release("c1", now=T0)
    assert exc.value.code == "SMUGGLE_BAD_STATE"

    with pytest.raises(hg.SmugglingError) as exc:
        guard.quarantine("c9", reason="cl-te")
    assert exc.value.code == "SMUGGLE_CONN_UNKNOWN"

    open_conn(guard, "c2")
    assert guard.quarantine("c2", reason="cl-te")["state"] == "poisoned"


def test_closed_connection_cannot_be_reused():
    guard = make()
    open_conn(guard)
    parsed = guard.normalize([(":method", "GET"), (":path", "/")])

    closed = guard.close_connection("c1", reason="backend-desync", now=T0)
    assert closed == {"conn_id": "c1", "state": "closed", "streams": 0}

    with pytest.raises(hg.SmugglingError) as exc:
        guard.close_connection("c1")
    assert exc.value.code == "SMUGGLE_CONN_CLOSED"

    with pytest.raises(hg.SmugglingError) as exc:
        guard.admit("c1", parsed)
    assert exc.value.code == "SMUGGLE_CONN_CLOSED"

    with pytest.raises(hg.SmugglingError) as exc:
        guard.admit("nope", parsed)
    assert exc.value.code == "SMUGGLE_CONN_UNKNOWN"
    assert guard.stats()["closed"] == 1


def test_recycle_closes_only_idle_connections_past_ttl():
    guard = make(idle_ttl=30)
    open_conn(guard, "c1")
    open_conn(guard, "c2")
    parsed = guard.normalize([(":method", "GET"), (":path", "/")])
    guard.admit("c2", parsed)
    guard.release("c1", now=T0)

    assert guard.recycle(now="2026-11-23T10:00:29+00:00") == 0
    assert guard.recycle(now="2026-11-23T10:00:30+00:00") == 1
    assert guard.connection_state("c1")["state"] == "closed"
    assert guard.connection_state("c2")["state"] == "open"
    assert guard.recycle(now="2026-11-23T10:30:00+00:00") == 0
    assert guard.stats()["closed"] == 1


def test_snapshot_restore_preserves_poison_and_stream_budget():
    guard = make()
    open_conn(guard, "c1")
    open_conn(guard, "c2")
    parsed = guard.normalize([(":method", "GET"), (":path", "/")])
    guard.admit("c1", parsed)
    guard.quarantine("c2", reason="te-cl-ambiguity")

    state = guard.snapshot()
    assert state["version"] == 1

    guard.admit("c1", parsed)
    guard.close_connection("c2")

    clone = hg.RequestSmugglingGuard()
    clone.restore(state)
    assert clone.connection_state("c1")["streams"] == 1
    assert clone.connection_state("c2")["state"] == "poisoned"
    assert clone.stats() == {
        "forwarded": 1, "rejected": 0, "opened": 2, "closed": 0}

    with pytest.raises(hg.SmugglingError) as exc:
        clone.admit("c2", parsed)
    assert exc.value.code == "SMUGGLE_CONN_POISONED"

    nxt = clone.admit("c1", parsed)
    assert nxt["stream_id"] == 2
    assert nxt["reused"] is True
    assert clone.backend_status("edge")["connections"] == 2


def test_corrupt_snapshot_is_rejected_without_touching_state():
    guard = make()
    open_conn(guard)
    parsed = guard.normalize([(":method", "GET"), (":path", "/")])
    guard.admit("c1", parsed)

    bad_states = [
        None,
        {},
        {"version": 2, "backends": {}, "connections": {}, "stats": {}},
        {"version": 1, "backends": [], "connections": {}, "stats": {}},
        {"version": 1, "backends": {}, "connections": [], "stats": {}},
        {"version": 1, "backends": {}, "connections": {}, "stats": []},
        {"version": 1, "backends": {}, "connections": {"c1": {}}, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(hg.SmugglingError) as exc:
            guard.restore(bad)
        assert exc.value.code == "SMUGGLE_SNAPSHOT_INVALID"

    assert guard.connection_state("c1")["streams"] == 1
    assert guard.stats() == {
        "forwarded": 1, "rejected": 0, "opened": 1, "closed": 0}
    assert guard.admit("c1", parsed)["stream_id"] == 2


def test_concurrent_admits_cannot_exceed_stream_budget():
    guard = make()
    guard.register_backend("tight", address="10.0.0.12:8080", max_streams=5)
    open_conn(guard, "c1", backend="tight")
    parsed = guard.normalize([(":method", "GET"), (":path", "/race")])

    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.admit("c1", parsed)
        except hg.SmugglingError as exc:
            out = {"status": "rejected", "code": exc.code}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["forwarded"] * 5 + ["rejected"] * 3
    rejected = sorted(row["code"] for row in results if row["status"] == "rejected")
    assert rejected == ["SMUGGLE_STREAM_LIMIT"] * 3
    assert guard.connection_state("c1")["streams"] == 5
    assert guard.stats() == {
        "forwarded": 5, "rejected": 3, "opened": 1, "closed": 0}
