import threading

import pytest

import http1_framing_guard as fg

HOST = "Host: edge.example\r\n"


def new_guard(conn="c1", **kwargs):
    guard = fg.FramingGuard(**kwargs)
    guard.begin_connection(conn, peer="10.1.2.3:51000")
    return guard


def test_content_length_request_is_framed_and_counted():
    guard = new_guard()
    info = guard.begin_connection("c1", peer="10.1.2.3:51000")
    assert info["existing"] is True

    payload = (
        b"POST /submit HTTP/1.1\r\nHost: edge.example\r\nContent-Length: 5\r\n"
        b"X-Trace: 7\r\n\r\nhello"
    )
    msgs = guard.feed("c1", payload)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["method"] == "POST"
    assert msg["target"] == "/submit"
    assert msg["version"] == "HTTP/1.1"
    assert msg["body"] == b"hello"
    assert msg["framing"] == "content-length"
    assert msg["content_length"] == 5
    assert msg["headers"]["x-trace"] == "7"
    assert msg["seq"] == 1
    assert guard.buffered_bytes("c1") == 0
    assert guard.outstanding("c1") == 1
    assert guard.stats() == {
        "connections": 1,
        "requests": 1,
        "responses": 0,
        "rejected": 0,
        "bytes_in": len(payload),
    }


def test_pipelined_requests_keep_wire_order_and_response_budget():
    guard = new_guard("c2")
    payload = (
        b"GET /a HTTP/1.1\r\nHost: edge.example\r\n\r\n"
        b"GET /b HTTP/1.1\r\nHost: edge.example\r\n\r\n"
        b"POST /c HTTP/1.1\r\nHost: edge.example\r\nContent-Length: 2\r\n\r\nhi"
    )
    msgs = guard.feed("c2", payload)
    assert [m["target"] for m in msgs] == ["/a", "/b", "/c"]
    assert [m["seq"] for m in msgs] == [1, 2, 3]
    assert [m["framing"] for m in msgs] == ["none", "none", "content-length"]
    assert guard.outstanding("c2") == 3
    assert guard.complete_response("c2", status=200) == {
        "conn_id": "c2",
        "answered": 1,
        "outstanding": 2,
    }
    assert guard.complete_response("c2", status=204)["answered"] == 2
    assert guard.complete_response("c2", status=500)["outstanding"] == 0
    assert guard.stats()["responses"] == 3


def test_content_length_plus_transfer_encoding_poisons_connection():
    guard = new_guard("c3")
    with pytest.raises(fg.FramingError) as exc:
        guard.feed(
            "c3",
            b"POST / HTTP/1.1\r\nHost: edge.example\r\nContent-Length: 6\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
        )
    assert exc.value.code == "HTTP_SMUGGLING_RISK"
    assert guard.stats()["rejected"] == 1
    assert guard.outstanding("c3") == 0
    assert guard.buffered_bytes("c3") == 0
    with pytest.raises(fg.FramingError) as exc2:
        guard.feed("c3", b"GET /again HTTP/1.1\r\nHost: edge.example\r\n\r\n")
    assert exc2.value.code == "HTTP_CONNECTION_POISONED"


def test_duplicate_content_lengths_must_agree():
    guard = new_guard("ok")
    msgs = guard.feed(
        "ok",
        b"POST / HTTP/1.1\r\nHost: edge.example\r\nContent-Length: 5\r\n"
        b"Content-Length: 5\r\n\r\nhello",
    )
    assert msgs[0]["content_length"] == 5
    assert msgs[0]["body"] == b"hello"

    other = new_guard("bad")
    with pytest.raises(fg.FramingError) as exc:
        other.feed(
            "bad",
            b"POST / HTTP/1.1\r\nHost: edge.example\r\nContent-Length: 5\r\n"
            b"Content-Length: 6\r\n\r\nhello!",
        )
    assert exc.value.code == "HTTP_SMUGGLING_RISK"


def test_chunked_body_is_decoded_with_trailers():
    guard = new_guard("c5")
    msgs = guard.feed(
        "c5",
        b"POST /up HTTP/1.1\r\nHost: edge.example\r\nTransfer-Encoding: chunked\r\n"
        b"\r\n5;ext=1\r\nhello\r\n6\r\n world\r\n0\r\nX-Checksum: abc\r\n\r\n",
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["body"] == b"hello world"
    assert msg["framing"] == "chunked"
    assert msg["content_length"] is None
    assert msg["trailers"] == {"x-checksum": "abc"}


def test_bad_chunk_sizes_and_oversized_chunked_body():
    guard = new_guard("hex")
    with pytest.raises(fg.FramingError) as exc:
        guard.feed(
            "hex",
            b"POST / HTTP/1.1\r\nHost: edge.example\r\nTransfer-Encoding: chunked\r\n"
            b"\r\n0x5\r\nhello\r\n0\r\n\r\n",
        )
    assert exc.value.code == "HTTP_BAD_CHUNK"

    plus = new_guard("plus")
    with pytest.raises(fg.FramingError) as exc2:
        plus.feed(
            "plus",
            b"POST / HTTP/1.1\r\nHost: edge.example\r\nTransfer-Encoding: chunked\r\n"
            b"\r\n+5\r\nhello\r\n0\r\n\r\n",
        )
    assert exc2.value.code == "HTTP_BAD_CHUNK"

    big = new_guard("big", max_body_bytes=8)
    with pytest.raises(fg.FramingError) as exc3:
        big.feed(
            "big",
            b"POST / HTTP/1.1\r\nHost: edge.example\r\nTransfer-Encoding: chunked\r\n"
            b"\r\n9\r\n123456789\r\n0\r\n\r\n",
        )
    assert exc3.value.code == "HTTP_BODY_TOO_LARGE"


def test_trailer_with_framing_header_is_refused():
    guard = new_guard("tr")
    with pytest.raises(fg.FramingError) as exc:
        guard.feed(
            "tr",
            b"POST / HTTP/1.1\r\nHost: edge.example\r\nTransfer-Encoding: chunked\r\n"
            b"\r\n0\r\nContent-Length: 0\r\n\r\n",
        )
    assert exc.value.code == "HTTP_SMUGGLING_RISK"


def test_obsolete_line_folding_and_bad_header_names():
    folded = new_guard("fold")
    with pytest.raises(fg.FramingError) as exc:
        folded.feed(
            "fold",
            b"GET / HTTP/1.1\r\nHost: edge.example\r\nX-A: 1\r\n\tcontinued\r\n\r\n",
        )
    assert exc.value.code == "HTTP_HEADER_FOLDING"

    spaced = new_guard("space")
    with pytest.raises(fg.FramingError) as exc2:
        spaced.feed(
            "space",
            b"GET / HTTP/1.1\r\nHost: edge.example\r\nBad Header: x\r\n\r\n",
        )
    assert exc2.value.code == "HTTP_BAD_HEADER"


def test_host_header_must_be_present_exactly_once():
    missing = new_guard("nohost")
    with pytest.raises(fg.FramingError) as exc:
        missing.feed("nohost", b"GET / HTTP/1.1\r\nX-A: 1\r\n\r\n")
    assert exc.value.code == "HTTP_BAD_HOST"

    twice = new_guard("twohost")
    with pytest.raises(fg.FramingError) as exc2:
        twice.feed(
            "twohost",
            b"GET / HTTP/1.1\r\nHost: edge.example\r\nHost: other.example\r\n\r\n",
        )
    assert exc2.value.code == "HTTP_BAD_HOST"


def test_absolute_form_target_must_agree_with_host():
    guard = new_guard("abs")
    msgs = guard.feed(
        "abs",
        b"GET http://edge.example/x HTTP/1.1\r\nHost: edge.example\r\n\r\n",
    )
    assert msgs[0]["target"] == "http://edge.example/x"

    wrong = new_guard("wrong")
    with pytest.raises(fg.FramingError) as exc:
        wrong.feed(
            "wrong",
            b"GET http://edge.example/x HTTP/1.1\r\nHost: other.example\r\n\r\n",
        )
    assert exc.value.code == "HTTP_BAD_HOST"


def test_header_budget_rejects_oversized_head():
    guard = new_guard("hdr", max_header_bytes=64)
    with pytest.raises(fg.FramingError) as exc:
        guard.feed(
            "hdr",
            b"GET / HTTP/1.1\r\nHost: edge.example\r\nX-Long: " + b"a" * 80,
        )
    assert exc.value.code == "HTTP_HEADER_TOO_LARGE"
    assert guard.stats()["rejected"] == 1


def test_partial_message_is_buffered_until_complete():
    guard = new_guard("slow", max_pipeline=4)
    first = b"GET /partial HTTP/1.1\r\nHost: edge.example\r\nX-Half: "
    assert guard.feed("slow", first) == []
    assert guard.buffered_bytes("slow") == len(first)
    assert guard.outstanding("slow") == 0

    msgs = guard.feed("slow", b"yes\r\n\r\n")
    assert [m["target"] for m in msgs] == ["/partial"]
    assert guard.buffered_bytes("slow") == 0
    assert guard.complete_response("slow", status=200)["answered"] == 1
    with pytest.raises(fg.FramingError) as exc:
        guard.complete_response("slow", status=200)
    assert exc.value.code == "HTTP_NO_PENDING"


def test_pipeline_overflow_poisons_connection():
    guard = new_guard("pipe", max_pipeline=2)
    one = b"GET /1 HTTP/1.1\r\nHost: edge.example\r\n\r\n"
    two = b"GET /2 HTTP/1.1\r\nHost: edge.example\r\n\r\n"
    assert len(guard.feed("pipe", one + two)) == 2
    with pytest.raises(fg.FramingError) as exc:
        guard.feed("pipe", b"GET /3 HTTP/1.1\r\nHost: edge.example\r\n\r\n")
    assert exc.value.code == "HTTP_PIPELINE_OVERFLOW"
    assert guard.stats()["requests"] == 2
    assert guard.stats()["rejected"] == 1
    assert guard.outstanding("pipe") == 2
    with pytest.raises(fg.FramingError) as exc2:
        guard.complete_response("pipe", status=200)
    assert exc2.value.code == "HTTP_CONNECTION_POISONED"


def test_snapshot_restore_keeps_buffers_and_counters():
    guard = new_guard("s1")
    guard.feed(
        "s1",
        b"GET /one HTTP/1.1\r\nHost: edge.example\r\n\r\nGET /part",
    )
    assert guard.buffered_bytes("s1") == len(b"GET /part")
    state = guard.snapshot()
    guard.feed("s1", b"ial HTTP/1.1\r\nHost: edge.example\r\n\r\n")
    assert guard.outstanding("s1") == 2
    assert guard.stats()["requests"] == 2

    restored = fg.FramingGuard()
    restored.restore(state)
    assert restored.buffered_bytes("s1") == len(b"GET /part")
    assert restored.outstanding("s1") == 1
    assert restored.stats()["requests"] == 1
    msgs = restored.feed("s1", b"ial HTTP/1.1\r\nHost: edge.example\r\n\r\n")
    assert [m["target"] for m in msgs] == ["/partial"]
    assert [m["seq"] for m in msgs] == [2]
    assert restored.stats()["requests"] == 2

    for bad in (
        None,
        {},
        {"version": 2, "connections": {}, "stats": {}},
        {
            "version": 1,
            "connections": {"s1": {"peer": "p", "buffer": "text", "outstanding": 0,
                                   "seq": 0, "answered": 0, "poisoned": False}},
            "stats": {"connections": 0, "requests": 0, "responses": 0,
                      "rejected": 0, "bytes_in": 0},
        },
        {
            "version": 1,
            "connections": {},
            "stats": {"connections": 0, "requests": 0, "responses": 0,
                      "rejected": 0},
        },
    ):
        with pytest.raises(fg.FramingError) as exc:
            restored.restore(bad)
        assert exc.value.code == "HTTP_SNAPSHOT_INVALID"
    assert restored.outstanding("s1") == 2
    assert restored.buffered_bytes("s1") == 0


def test_concurrent_feeds_and_responses_are_atomic():
    guard = fg.FramingGuard(max_pipeline=32)
    guard.begin_connection("race", peer="10.9.9.9:4000")
    barrier = threading.Barrier(8)
    parsed = []
    failures = []
    lock = threading.Lock()

    def feeder(index):
        barrier.wait()
        try:
            out = guard.feed(
                "race",
                ("GET /r%d HTTP/1.1\r\nHost: edge.example\r\n\r\n" % index).encode(),
            )
        except Exception as exc:  # pragma: no cover
            with lock:
                failures.append(exc)
            return
        with lock:
            parsed.append(out[0])

    threads = [threading.Thread(target=feeder, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert sorted(m["target"] for m in parsed) == ["/r%d" % i for i in range(8)]
    assert len({m["seq"] for m in parsed}) == 8
    assert guard.outstanding("race") == 8
    assert guard.stats()["requests"] == 8

    barrier2 = threading.Barrier(8)
    answered = []
    failures.clear()

    def responder():
        barrier2.wait()
        try:
            out = guard.complete_response("race", status=200)
        except Exception as exc:  # pragma: no cover
            with lock:
                failures.append(exc)
            return
        with lock:
            answered.append(out["answered"])

    threads = [threading.Thread(target=responder) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert sorted(answered) == list(range(1, 9))
    assert guard.outstanding("race") == 0
    with pytest.raises(fg.FramingError) as exc:
        guard.complete_response("race", status=200)
    assert exc.value.code == "HTTP_NO_PENDING"
