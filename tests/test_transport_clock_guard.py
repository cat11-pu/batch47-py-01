import threading

import pytest

import transport_clock_guard as tg

MOD = 1 << 33
MS = 90  # 90 kHz ticks in one millisecond


def packet(pid=256, cc=0, pts=0, dts=None, **overrides):
    payload = {"pid": pid, "cc": cc, "pts": pts,
               "dts": pts if dts is None else dts}
    payload.update(overrides)
    return payload


def make(**kwargs):
    guard = tg.TransportClockGuard(**kwargs)
    guard.open_stream("s1")
    return guard


def test_open_stream_is_idempotent_and_program_conflict_is_refused():
    guard = tg.TransportClockGuard()
    first = guard.open_stream("s1", program=1)
    assert first == {"stream_id": "s1", "program": 1, "pids": []}
    again = guard.open_stream("s1", program=1)
    assert again["program"] == 1
    assert again["pids"] == []
    with pytest.raises(tg.TsClockError) as exc:
        guard.open_stream("s1", program=2)
    assert exc.value.code == "TS_STREAM_CONFLICT"


def test_stream_capacity_unknown_stream_and_close():
    guard = tg.TransportClockGuard(max_streams=2)
    guard.open_stream("a")
    guard.open_stream("b")
    with pytest.raises(tg.TsClockError) as exc:
        guard.open_stream("c")
    assert exc.value.code == "TS_STREAM_LIMIT"
    guard.close_stream("a")
    assert guard.open_stream("c")["stream_id"] == "c"
    with pytest.raises(tg.TsClockError) as exc:
        guard.close_stream("gone")
    assert exc.value.code == "TS_STREAM_UNKNOWN"
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("gone", packet())
    assert exc.value.code == "TS_STREAM_UNKNOWN"


def test_packet_schema_and_dts_order_are_validated():
    guard = make()
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("s1", packet(pid=8191))
    assert exc.value.code == "TS_PID_INVALID"
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("s1", packet(cc=16))
    assert exc.value.code == "TS_CC_INVALID"
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("s1", packet(pid="256"))
    assert exc.value.code == "TS_PACKET_INVALID"
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("s1", packet(pts=1000, dts=1001))
    assert exc.value.code == "TS_DTS_AFTER_PTS"


def test_timestamp_wrap_at_33_bits_is_unwrapped_forward():
    guard = make()
    first = guard.push_packet("s1", packet(cc=0, pts=MOD - 900, dts=MOD - 900))
    assert first["status"] == "ok"
    assert first["pts"] == MOD - 900
    second = guard.push_packet("s1", packet(cc=1, pts=900, dts=900))
    assert second["status"] == "ok"
    assert second["pts"] == MOD + 900
    assert second["dts"] == MOD + 900
    assert guard.unwrap("s1", 1800) == MOD + 1800


def test_small_backward_delta_within_budget_is_accepted():
    guard = make(max_jump_ms=5000)
    guard.push_packet("s1", packet(cc=0, pts=2_000_000))
    back = guard.push_packet("s1", packet(cc=1, pts=2_000_000 - 9000))
    assert back["status"] == "ok"
    assert back["pts"] == 2_000_000 - 9000


def test_unannounced_jump_is_rejected_without_moving_state():
    guard = make(max_jump_ms=5000)
    guard.push_packet("s1", packet(cc=0, pts=0))
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("s1", packet(cc=1, pts=MS * 6000))
    assert exc.value.code == "TS_CLOCK_JUMP"
    recovered = guard.push_packet("s1", packet(cc=1, pts=MS * 10))
    assert recovered["status"] == "ok"
    assert recovered["pts"] == MS * 10
    assert guard.stream_state("s1")["pids"][256]["packets"] == 2


def test_announced_discontinuity_rebases_timeline_and_is_counted():
    guard = make()
    guard.push_packet("s1", packet(cc=0, pts=0))
    jump = guard.push_packet("s1", packet(cc=1, pts=MS * 6000,
                                           discontinuity=True))
    assert jump["status"] == "discontinuity"
    assert jump["pts"] == MS * 6000
    nxt = guard.push_packet("s1", packet(cc=2, pts=MS * 6000 + MS * 10))
    assert nxt["pts"] == MS * 6000 + MS * 10
    assert guard.stats()["discontinuities"] == 1
    assert guard.stats()["accepted"] == 3


def test_continuity_gap_is_counted_and_same_counter_is_duplicate():
    guard = make()
    start = guard.push_packet("s1", packet(cc=0, pts=0))
    assert start["cc_expected"] == 1
    gap = guard.push_packet("s1", packet(cc=3, pts=MS))
    assert gap["status"] == "gap"
    assert gap["lost"] == 2
    dup = guard.push_packet("s1", packet(cc=3, pts=MS))
    assert dup["status"] == "duplicate"
    ok = guard.push_packet("s1", packet(cc=4, pts=MS * 2))
    assert ok["status"] == "ok"
    state = guard.stream_state("s1")["pids"][256]
    assert state["cc"] == 4
    assert state["packets"] == 3
    assert guard.stats()["duplicates"] == 1
    assert guard.stats()["lost_packets"] == 2


def test_continuity_loss_beyond_budget_is_rejected_atomically():
    guard = make(cc_loss_budget=8)
    guard.push_packet("s1", packet(cc=0, pts=0))
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("s1", packet(cc=10, pts=MS))
    assert exc.value.code == "TS_CC_LOSS_EXCEEDED"
    after = guard.push_packet("s1", packet(cc=1, pts=MS))
    assert after["status"] == "ok"
    assert guard.stream_state("s1")["pids"][256]["packets"] == 2


def test_repair_resynchronises_the_next_packet_counter():
    guard = make()
    guard.push_packet("s1", packet(cc=0, pts=0))
    report = guard.repair("s1")
    assert report["pids_reset"] == 1
    resync = guard.push_packet("s1", packet(cc=9, pts=MS))
    assert resync["status"] == "resync"
    assert resync["cc_expected"] == 10
    assert guard.stats()["resyncs"] == 1


def test_pid_capacity_per_stream_is_enforced():
    guard = make(pids_per_stream=2)
    guard.push_packet("s1", packet(pid=256, cc=0, pts=0))
    guard.push_packet("s1", packet(pid=257, cc=0, pts=0))
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_packet("s1", packet(pid=258, cc=0, pts=0))
    assert exc.value.code == "TS_PID_LIMIT"
    assert sorted(guard.stream_state("s1")["pids"]) == [256, 257]


def test_batch_reports_per_item_and_rejects_oversized_batches():
    guard = make(batch_limit=3)
    rows = guard.push_batch(
        "s1",
        [
            packet(pid=256, cc=0, pts=0),
            packet(pid=256, cc=99, pts=0),
            packet(pid=256, cc=1, pts=MS),
        ],
    )
    assert [row["status"] for row in rows] == ["ok", "rejected", "ok"]
    assert rows[1]["code"] == "TS_CC_INVALID"
    with pytest.raises(tg.TsClockError) as exc:
        guard.push_batch("s1", [packet(cc=2, pts=MS * 2)] * 4)
    assert exc.value.code == "TS_BATCH_TOO_LARGE"


def test_snapshot_and_restore_keep_clock_and_reject_corrupt_state():
    guard = make()
    guard.push_packet("s1", packet(cc=0, pts=MOD - 900))
    state = guard.snapshot()
    guard.push_packet("s1", packet(cc=1, pts=900))

    restored = tg.TransportClockGuard()
    restored.restore(state)
    assert restored.stream_state("s1")["pids"][256]["cc"] == 0
    assert restored.stats()["accepted"] == 1
    resumed = restored.push_packet("s1", packet(cc=1, pts=900))
    assert resumed["status"] == "ok"
    assert resumed["pts"] == MOD + 900
    assert state["streams"]["s1"]["pids"][256]["cc"] == 0

    bad_states = [
        None,
        {},
        {"version": 2, "streams": {}, "stats": {}},
        {"version": 1, "streams": "nope", "stats": {}},
        {"version": 1, "streams": {}, "stats": "nope"},
    ]
    for bad in bad_states:
        with pytest.raises(tg.TsClockError) as exc:
            restored.restore(bad)
        assert exc.value.code == "TS_SNAPSHOT_INVALID"
    assert restored.stream_state("s1")["pids"][256]["packets"] == 2


def test_concurrent_identical_packets_are_accepted_exactly_once():
    guard = make()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.push_packet("s1", packet(cc=0, pts=100, dts=100))
        except Exception as err:  # pragma: no cover - surfaced via statuses
            out = {"status": "error", "code": repr(err)}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(row["status"] for row in results) == ["duplicate"] * 7 + ["ok"]
    assert guard.stats()["accepted"] == 1
    assert guard.stats()["duplicates"] == 7


def test_stats_and_reset_accounting():
    guard = make()
    guard.push_packet("s1", packet(cc=0, pts=0))
    guard.push_packet("s1", packet(cc=0, pts=0))
    with pytest.raises(tg.TsClockError):
        guard.push_packet("s1", packet(cc=99))
    assert guard.stats() == {
        "packets": 3,
        "accepted": 1,
        "duplicates": 1,
        "gaps": 0,
        "discontinuities": 0,
        "resyncs": 0,
        "rejected": 1,
        "lost_packets": 0,
    }
    guard.reset_stats()
    assert guard.stats() == {
        "packets": 0,
        "accepted": 0,
        "duplicates": 0,
        "gaps": 0,
        "discontinuities": 0,
        "resyncs": 0,
        "rejected": 0,
        "lost_packets": 0,
    }
    assert guard.stream_state("s1")["pids"][256]["cc"] == 0
