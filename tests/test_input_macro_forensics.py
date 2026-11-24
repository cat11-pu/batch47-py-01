import threading

import pytest

import input_macro_forensics as imf


def events(action, times):
    return [{"action": action, "at_ms": at} for at in times]


def feed_uniform(engine, session_id, action="attack", count=12, step=100,
                 start=0):
    times = [start + index * step for index in range(count)]
    return engine.ingest(session_id, events(action, times), now_ms=times[-1])


def test_open_session_is_idempotent_and_respects_the_session_cap():
    engine = imf.InputMacroForensics(max_sessions=2)
    first = engine.open_session("s1", player_id="p1", device_class="pc")
    assert first["created"] is True
    assert first["player_id"] == "p1"
    again = engine.open_session("s1", player_id="p1", device_class="pc")
    assert again["created"] is False

    with pytest.raises(imf.ForensicsError) as exc:
        engine.open_session("s1", player_id="other")
    assert exc.value.code == "FORENSICS_SESSION_CONFLICT"
    assert engine.open_session("s2", player_id="p2")["created"] is True
    with pytest.raises(imf.ForensicsError) as exc:
        engine.open_session("s3", player_id="p3")
    assert exc.value.code == "FORENSICS_SESSION_LIMIT"

    with pytest.raises(imf.ForensicsError) as exc:
        engine.open_session("", player_id="p4")
    assert exc.value.code == "FORENSICS_BAD_SESSION"


def test_out_of_order_batch_is_rejected_atomically():
    engine = imf.InputMacroForensics()
    engine.open_session("s1", player_id="p1")
    first = engine.ingest("s1", events("attack", [100, 200]), now_ms=200)
    assert first["accepted"] == 2

    with pytest.raises(imf.ForensicsError) as exc:
        engine.ingest("s1", events("attack", [150, 250]), now_ms=250)
    assert exc.value.code == "FORENSICS_OUT_OF_ORDER"
    assert exc.value.detail == {"at_ms": 150, "last_at_ms": 200}
    assert engine.stats()["events_ingested"] == 2
    assert engine.session_report("s1")["samples"] == 2

    assert engine.ingest("s1", events("attack", [200]), now_ms=200)["accepted"] == 1
    assert engine.ingest("s1", events("attack", [250]), now_ms=250)["accepted"] == 1
    assert engine.stats()["events_ingested"] == 4


def test_batch_limit_and_event_retention_are_enforced():
    engine = imf.InputMacroForensics(batch_limit=3)
    engine.open_session("s1", player_id="p1")
    with pytest.raises(imf.ForensicsError) as exc:
        engine.ingest("s1", events("attack", [0, 100, 200, 300]), now_ms=300)
    assert exc.value.code == "FORENSICS_BATCH_TOO_LARGE"
    assert exc.value.detail == {"size": 4, "limit": 3}
    assert engine.stats()["events_ingested"] == 0

    small = imf.InputMacroForensics(max_events=5, batch_limit=10)
    small.open_session("s2", player_id="p2")
    out = small.ingest("s2", events("attack", list(range(0, 700, 100))), now_ms=600)
    assert (out["accepted"], out["dropped"]) == (7, 2)
    assert small.stats()["events_ingested"] == 7
    assert small.stats()["events_dropped"] == 2
    assert small.session_report("s2")["samples"] == 5

    empty = small.ingest("s2", [], now_ms=600)
    assert (empty["accepted"], empty["dropped"]) == (0, 0)


def test_bad_event_payload_rejects_the_whole_batch():
    engine = imf.InputMacroForensics()
    engine.open_session("s1", player_id="p1")
    bad_batches = [
        [{"action": "attack", "at_ms": 100}, {"action": "", "at_ms": 200}],
        [{"action": "attack", "at_ms": 100}, {"action": "attack"}],
        [{"action": "attack", "at_ms": -1}],
        [{"action": "attack", "at_ms": "100"}],
        [{"at_ms": 100}],
    ]
    for batch in bad_batches:
        with pytest.raises(imf.ForensicsError) as exc:
            engine.ingest("s1", batch, now_ms=200)
        assert exc.value.code == "FORENSICS_BAD_EVENT"
    assert engine.stats()["events_ingested"] == 0
    assert engine.session_report("s1")["samples"] == 0

    assert engine.ingest("s1", events("attack", [100]), now_ms=100)["accepted"] == 1


def test_two_metronomic_actions_reach_a_macro_verdict():
    engine = imf.InputMacroForensics()
    engine.open_session("s1", player_id="p1")
    merged = []
    for index in range(12):
        merged.append({"action": "attack", "at_ms": index * 100})
        merged.append({"action": "dash", "at_ms": index * 100 + 50})
    merged.sort(key=lambda row: row["at_ms"])
    engine.ingest("s1", merged, now_ms=1150)

    report = engine.session_report("s1")
    assert report["samples"] == 24
    assert report["score"] == 100
    assert report["verdict"] == "macro"
    assert [row["action"] for row in report["signals"]] == ["attack", "dash"]
    for signal in report["signals"]:
        assert signal["samples"] == 12
        assert signal["mean_interval_ms"] == 100.0
        assert signal["stddev_ms"] == 0.0
        assert signal["mode_ratio"] == 1.0
        assert signal["metronomic"] is True
        assert signal["repetitive"] is True
        assert signal["points"] == 70


def test_low_jitter_without_repetition_is_suspicious_only():
    engine = imf.InputMacroForensics()
    engine.open_session("s1", player_id="p1")
    intervals = [95, 105, 96, 104, 97, 103, 98, 102, 99, 101, 100]
    times = [1000]
    for interval in intervals:
        times.append(times[-1] + interval)
    engine.ingest("s1", events("attack", times), now_ms=times[-1])

    report = engine.session_report("s1")
    signal = report["signals"][0]
    assert signal["samples"] == 12
    assert signal["mean_interval_ms"] == 100.0
    assert signal["stddev_ms"] == 3.162
    assert signal["mode_ratio"] == 0.091
    assert signal["metronomic"] is True
    assert signal["repetitive"] is False
    assert signal["points"] == 45
    assert report["score"] == 45
    assert report["verdict"] == "suspicious"


def test_repeated_interval_with_high_jitter_stays_clean():
    engine = imf.InputMacroForensics()
    engine.open_session("s1", player_id="p1")
    intervals = [100] * 17 + [1000] * 3
    times = [0]
    for interval in intervals:
        times.append(times[-1] + interval)
    engine.ingest("s1", events("attack", times), now_ms=times[-1])

    report = engine.session_report("s1")
    signal = report["signals"][0]
    assert signal["samples"] == 21
    assert signal["mode_ratio"] == 0.85
    assert signal["metronomic"] is False
    assert signal["repetitive"] is True
    assert signal["points"] == 30
    assert report["score"] == 30
    assert report["verdict"] == "clean"


def test_minimum_sample_gate_applies_before_scoring():
    engine = imf.InputMacroForensics(min_samples=12)
    engine.open_session("s1", player_id="p1")
    feed_uniform(engine, "s1", count=11)
    first = engine.session_report("s1")
    assert first["samples"] == 11
    assert first["signals"][0]["points"] == 0
    assert first["score"] == 0
    assert first["verdict"] == "clean"

    engine.ingest("s1", events("attack", [1100]), now_ms=1100)
    second = engine.session_report("s1")
    assert second["samples"] == 12
    assert second["score"] == 70
    assert second["verdict"] == "macro"


def test_report_window_uses_the_latest_event_time():
    engine = imf.InputMacroForensics(window_ms=1000, min_samples=5)
    engine.open_session("s1", player_id="p1")
    feed_uniform(engine, "s1", count=10, step=100, start=0)
    assert engine.session_report("s1")["score"] == 70

    engine.ingest("s1", events("attack", [5000]), now_ms=5000)
    report = engine.session_report("s1")
    assert report["samples"] == 1
    assert report["signals"] == []
    assert report["score"] == 0
    assert report["verdict"] == "clean"


def test_closed_session_refuses_ingest_but_keeps_its_report():
    engine = imf.InputMacroForensics()
    engine.open_session("s1", player_id="p1")
    feed_uniform(engine, "s1")
    closed = engine.close_session("s1", now_ms=2000)
    assert closed["closed"] is True
    assert closed["closed_at_ms"] == 2000
    assert closed["score"] == 70
    assert closed["verdict"] == "macro"
    again = engine.close_session("s1", now_ms=3000)
    assert again["closed"] is False
    assert again["closed_at_ms"] == 2000

    with pytest.raises(imf.ForensicsError) as exc:
        engine.ingest("s1", events("attack", [2100]), now_ms=2100)
    assert exc.value.code == "FORENSICS_SESSION_CLOSED"
    assert engine.session_report("s1")["score"] == 70
    assert engine.stats() == {"sessions_opened": 1, "sessions_closed": 1,
                              "events_ingested": 12, "events_dropped": 0,
                              "penalties_flagged": 0, "penalties_cleared": 0}


def test_player_penalties_extend_and_expire_on_the_boundary():
    engine = imf.InputMacroForensics(penalty_ttl_ms=5000)
    first = engine.flag_player("p1", reason="macro", now_ms=1000)
    assert first == {"player_id": "p1", "until_ms": 6000,
                     "reason": "macro", "extended": False}
    assert engine.is_penalized("p1", now_ms=5999) is True
    assert engine.is_penalized("p1", now_ms=6000) is False

    shorter = engine.flag_player("p1", reason="repeat", now_ms=2000, ttl_ms=3000)
    assert shorter["until_ms"] == 6000
    assert shorter["extended"] is True
    longer = engine.flag_player("p1", reason="repeat", now_ms=5000, ttl_ms=3000)
    assert longer["until_ms"] == 8000
    assert longer["extended"] is True

    assert engine.clear_penalty("p1") is True
    assert engine.clear_penalty("p1") is False
    assert engine.stats()["penalties_flagged"] == 3
    assert engine.stats()["penalties_cleared"] == 1
    with pytest.raises(imf.ForensicsError) as exc:
        engine.flag_player("", reason="macro", now_ms=0)
    assert exc.value.code == "FORENSICS_BAD_PENALTY"


def test_top_suspects_orders_by_score_then_player_id():
    engine = imf.InputMacroForensics()
    for session_id, player_id in (("sa", "alpha"), ("sb", "beta"),
                                  ("sc", "gamma")):
        engine.open_session(session_id, player_id=player_id)
    feed_uniform(engine, "sa", count=12)
    feed_uniform(engine, "sb", count=12)
    intervals = [95, 105, 96, 104, 97, 103, 98, 102, 99, 101, 100]
    times = [0]
    for interval in intervals:
        times.append(times[-1] + interval)
    engine.ingest("sc", events("move", times), now_ms=times[-1])

    top = engine.top_suspects(2)
    assert top == [{"player_id": "alpha", "score": 70, "sessions": 1},
                   {"player_id": "beta", "score": 70, "sessions": 1}]
    everything = engine.top_suspects(10)
    assert [row["player_id"] for row in everything] == ["alpha", "beta", "gamma"]
    assert everything[2]["score"] == 45

    summary = engine.player_summary("alpha", now_ms=0)
    assert summary == {"player_id": "alpha", "sessions": 1, "best_score": 70,
                       "verdict": "macro", "penalized": False}

    engine.close_session("sb", now_ms=5000)
    assert engine.top_suspects(10)[1]["player_id"] == "beta"
    with pytest.raises(imf.ForensicsError) as exc:
        engine.top_suspects(0)
    assert exc.value.code == "FORENSICS_BAD_LIMIT"


def test_purge_removes_only_old_closed_sessions_and_expired_penalties():
    engine = imf.InputMacroForensics(window_ms=1000)
    for session_id, closed_at in (("old", 1000), ("mid", 5000)):
        engine.open_session(session_id, player_id=session_id)
        engine.close_session(session_id, now_ms=closed_at)
    engine.open_session("live", player_id="live")
    engine.flag_player("p1", reason="macro", now_ms=0, ttl_ms=100)

    assert engine.purge(2999) == {"sessions": 0, "penalties": 1}
    assert engine.purge(3000) == {"sessions": 1, "penalties": 0}
    assert engine.purge(7000) == {"sessions": 1, "penalties": 0}
    assert engine.purge(7001) == {"sessions": 0, "penalties": 0}
    assert engine.session_report("live")["session_id"] == "live"

    with pytest.raises(imf.ForensicsError) as exc:
        engine.session_report("old")
    assert exc.value.code == "FORENSICS_UNKNOWN_SESSION"


def test_snapshot_restore_preserves_sessions_events_and_penalties():
    engine = imf.InputMacroForensics()
    engine.open_session("s1", player_id="p1")
    feed_uniform(engine, "s1")
    engine.flag_player("p1", reason="macro", now_ms=1000, ttl_ms=10000)
    state = engine.snapshot()
    assert state["version"] == 1

    tampered = engine.snapshot()
    tampered["sessions"]["s1"]["events"].append({"action": "attack", "at_ms": 9999})
    assert engine.session_report("s1")["samples"] == 12

    engine.close_session("s1", now_ms=2000)
    engine.open_session("s2", player_id="p2")
    engine.ingest("s2", events("dash", [0, 100]), now_ms=100)
    engine.clear_penalty("p1")
    engine.restore(state)

    report = engine.session_report("s1")
    assert report["samples"] == 12
    assert report["score"] == 70
    assert engine.is_penalized("p1", now_ms=1000) is True
    assert engine.is_penalized("p1", now_ms=11000) is False
    assert engine.stats()["events_ingested"] == 12
    assert engine.stats()["sessions_opened"] == 1
    assert engine.ingest("s1", events("attack", [1200]), now_ms=1200)["accepted"] == 1

    bad_states = [
        None,
        {},
        dict(state, version=2),
        dict(state, sessions=[]),
        dict(state, penalties="nope"),
        dict(state, stats={"sessions_opened": -1, "sessions_closed": 0,
                           "events_ingested": 0, "events_dropped": 0,
                           "penalties_flagged": 0, "penalties_cleared": 0}),
    ]
    for bad in bad_states:
        with pytest.raises(imf.ForensicsError) as exc:
            engine.restore(bad)
        assert exc.value.code == "FORENSICS_SNAPSHOT_INVALID"
    assert engine.session_report("s1")["samples"] == 13


def test_concurrent_open_of_one_session_creates_it_once():
    engine = imf.InputMacroForensics()
    barrier = threading.Barrier(8)
    results = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = engine.open_session("same", player_id="p1")
        except Exception as exc:  # pragma: no cover - surfaced below
            row = {"created": "error", "detail": repr(exc)}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [row["created"] for row in results].count(True) == 1
    assert [row["created"] for row in results].count(False) == 7
    assert engine.stats()["sessions_opened"] == 1
    with pytest.raises(imf.ForensicsError) as exc:
        engine.open_session("same", player_id="p2")
    assert exc.value.code == "FORENSICS_SESSION_CONFLICT"
