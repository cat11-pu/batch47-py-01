import threading

import pytest

import speech_prompt_scheduler as sp


def test_constructor_and_submit_reject_invalid_parameters():
    with pytest.raises(sp.SpeechError) as exc:
        sp.SpeechPromptScheduler(max_pending=0)
    assert exc.value.code == "SPEECH_CONFIG_INVALID"

    with pytest.raises(sp.SpeechError) as exc:
        sp.SpeechPromptScheduler(barge_in_priority=10)
    assert exc.value.code == "SPEECH_CONFIG_INVALID"

    scheduler = sp.SpeechPromptScheduler()
    with pytest.raises(sp.SpeechError) as exc:
        scheduler.submit("c1", "p1", priority=10, now=0)
    assert exc.value.code == "SPEECH_BAD_PRIORITY"

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.submit("c1", "p1", priority=-1, now=0)
    assert exc.value.code == "SPEECH_BAD_PRIORITY"

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.submit("c1", "p1", priority=3, duration_ms=0, now=0)
    assert exc.value.code == "SPEECH_BAD_DURATION"

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.submit("c1", "p1", priority=3, ttl_ms=-1, now=0)
    assert exc.value.code == "SPEECH_BAD_TTL"

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.submit("c1", "p1", priority=3, now=-5)
    assert exc.value.code == "SPEECH_BAD_TIME"

    assert scheduler.pending("c1") == []
    assert scheduler.current("c1", now=0) is None


def test_first_prompt_starts_playing_and_reports_remaining_time():
    scheduler = sp.SpeechPromptScheduler()
    result = scheduler.submit("c1", "p1", priority=3, duration_ms=1000, now=0)
    assert result["status"] == "playing"
    assert result["prompt_id"] == "p1"
    assert result["channel"] == "c1"
    assert result["started_at"] == 0

    now_playing = scheduler.current("c1", now=400)
    assert now_playing["prompt_id"] == "p1"
    assert now_playing["priority"] == 3
    assert now_playing["duration_ms"] == 1000
    assert now_playing["elapsed_ms"] == 400
    assert now_playing["remaining_ms"] == 600
    assert now_playing["deadline"] is None
    assert scheduler.pending("c1") == []
    assert scheduler.stats()["accepted"] == 1


def test_queue_orders_by_priority_then_arrival():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "p1", priority=3, duration_ms=10000, now=0)
    first = scheduler.submit("c1", "p2", priority=1, now=0)
    assert first["status"] == "queued"
    assert first["position"] == 0

    higher = scheduler.submit("c1", "p3", priority=5, now=0)
    assert higher["position"] == 0
    last = scheduler.submit("c1", "p4", priority=1, now=0)
    assert last["position"] == 2
    assert scheduler.pending("c1") == ["p3", "p2", "p4"]


def test_pending_capacity_rejects_without_mutating_queue():
    scheduler = sp.SpeechPromptScheduler(max_pending=2)
    scheduler.submit("c1", "p0", priority=5, duration_ms=10000, now=0)
    scheduler.submit("c1", "a", priority=1, now=0)
    scheduler.submit("c1", "b", priority=1, now=0)

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.submit("c1", "c", priority=1, now=0)
    assert exc.value.code == "SPEECH_QUEUE_FULL"
    assert scheduler.pending("c1") == ["a", "b"]
    assert scheduler.stats()["rejected"] == 1
    assert scheduler.stats()["accepted"] == 3


def test_backlog_budget_counts_current_remaining_and_pending():
    scheduler = sp.SpeechPromptScheduler(max_backlog_ms=5000)
    scheduler.submit("c1", "p1", priority=5, duration_ms=2000, now=0)
    scheduler.submit("c1", "p2", priority=4, duration_ms=2000, now=0)

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.submit("c1", "p3", priority=4, duration_ms=2000, now=0)
    assert exc.value.code == "SPEECH_BACKLOG_EXCEEDED"

    later = scheduler.submit("c1", "p4", priority=4, duration_ms=1000, now=1500)
    assert later["status"] == "queued"
    assert later["position"] == 1
    assert scheduler.stats()["rejected"] == 1


def test_duplicate_dedupe_key_merges_into_live_prompt():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "a", priority=2, duration_ms=1000,
                     dedupe_key="k", now=0)
    merged = scheduler.submit("c1", "b", priority=2, duration_ms=1000,
                              dedupe_key="k", now=0)
    assert merged["status"] == "merged"
    assert merged["prompt_id"] == "a"
    assert scheduler.queue_depth("c1") == {"pending": 0, "max_pending": 8,
                                           "playing": True}

    scheduler.submit("c1", "c", priority=2, duration_ms=1000,
                     dedupe_key="other", now=0)
    again = scheduler.submit("c1", "d", priority=2, duration_ms=1000,
                             dedupe_key="k", now=0)
    assert again["prompt_id"] == "a"

    scheduler.advance("c1", now=1000)
    fresh = scheduler.submit("c1", "e", priority=2, duration_ms=1000,
                             dedupe_key="k", now=1000)
    assert fresh["status"] == "queued"
    assert scheduler.pending("c1") == ["e"]
    assert scheduler.stats()["merged"] == 2


def test_high_priority_prompt_barges_in_on_current_playback():
    scheduler = sp.SpeechPromptScheduler(barge_in_priority=7)
    scheduler.submit("c1", "p1", priority=3, duration_ms=5000, now=0)
    result = scheduler.submit("c1", "p2", priority=9, duration_ms=1000, now=100)
    assert result["status"] == "playing"
    assert result["prompt_id"] == "p2"
    assert result["preempted"] == "p1"
    assert result["started_at"] == 100

    now_playing = scheduler.current("c1", now=100)
    assert now_playing["prompt_id"] == "p2"
    assert now_playing["remaining_ms"] == 1000
    assert scheduler.pending("c1") == []
    stats = scheduler.stats()
    assert stats["accepted"] == 2
    assert stats["preempted"] == 1
    assert stats["completed"] == 0


def test_prompt_below_barge_in_threshold_only_queues():
    scheduler = sp.SpeechPromptScheduler(barge_in_priority=7)
    scheduler.submit("c1", "p1", priority=5, duration_ms=5000, now=0)
    queued = scheduler.submit("c1", "p2", priority=6, duration_ms=800, now=0)
    assert queued["status"] == "queued"
    assert queued["position"] == 0

    urgent = scheduler.submit("c1", "p3", priority=7, duration_ms=900, now=0)
    assert urgent["status"] == "playing"
    assert urgent["preempted"] == "p1"
    assert scheduler.pending("c1") == ["p2"]
    assert scheduler.current("c1", now=0)["prompt_id"] == "p3"


def test_advance_completes_at_deadline_and_starts_next_prompt():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "p1", priority=3, duration_ms=1000, now=0)
    scheduler.submit("c1", "p2", priority=3, duration_ms=500, now=0)

    assert scheduler.advance("c1", now=999) == []
    assert scheduler.current("c1", now=999)["remaining_ms"] == 1

    events = scheduler.advance("c1", now=1000)
    assert [event["event"] for event in events] == ["completed", "started"]
    assert events[0]["prompt_id"] == "p1"
    assert events[1]["prompt_id"] == "p2"
    assert events[1]["started_at"] == 1000
    assert scheduler.current("c1", now=1000)["prompt_id"] == "p2"
    assert scheduler.stats()["completed"] == 1


def test_expired_prompt_is_dropped_before_it_can_start():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "p1", priority=3, duration_ms=1000, now=0)
    scheduler.submit("c1", "p2", priority=3, duration_ms=1000,
                     ttl_ms=500, now=0)
    scheduler.submit("c1", "p3", priority=3, duration_ms=700, now=0)

    early = scheduler.advance("c1", now=600)
    assert [event["event"] for event in early] == ["expired"]
    assert early[0]["prompt_id"] == "p2"
    assert scheduler.pending("c1") == ["p3"]

    started = scheduler.advance("c1", now=1000)
    assert [event["event"] for event in started] == ["completed", "started"]
    assert started[1]["prompt_id"] == "p3"

    scheduler.submit("c1", "p4", priority=3, duration_ms=400,
                     ttl_ms=0, now=1000)
    dropped = scheduler.advance("c1", now=1000)
    assert [event["event"] for event in dropped] == ["expired"]
    assert dropped[0]["prompt_id"] == "p4"
    assert scheduler.pending("c1") == []
    assert scheduler.stats()["expired"] == 2


def test_cancel_only_accepts_pending_prompts():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "p1", priority=5, duration_ms=5000, now=0)
    scheduler.submit("c1", "p2", priority=1, now=0)

    cancelled = scheduler.cancel("c1", "p2")
    assert cancelled["status"] == "cancelled"
    assert cancelled["prompt_id"] == "p2"
    assert scheduler.pending("c1") == []

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.cancel("c1", "p2")
    assert exc.value.code == "SPEECH_UNKNOWN_PROMPT"

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.cancel("c1", "p1")
    assert exc.value.code == "SPEECH_NOT_CANCELLABLE"

    with pytest.raises(sp.SpeechError) as exc:
        scheduler.cancel("c1", "ghost")
    assert exc.value.code == "SPEECH_UNKNOWN_PROMPT"
    assert scheduler.stats()["cancelled"] == 1


def test_snapshot_restore_resumes_current_and_pending_state():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "p1", priority=3, duration_ms=2000, now=0)
    scheduler.submit("c1", "p2", priority=5, duration_ms=800, now=0)

    state = scheduler.snapshot()
    assert state["version"] == 1
    scheduler.advance("c1", now=1500)

    restored = sp.SpeechPromptScheduler()
    restored.restore(state)
    resumed = restored.current("c1", now=200)
    assert resumed["prompt_id"] == "p1"
    assert resumed["elapsed_ms"] == 200
    assert resumed["remaining_ms"] == 1800
    assert restored.pending("c1") == ["p2"]

    events = restored.advance("c1", now=2000)
    assert [event["event"] for event in events] == ["completed", "started"]
    assert events[1]["prompt_id"] == "p2"
    assert restored.stats()["completed"] == 1

    state["channels"]["c1"]["pending"].clear()
    assert restored.pending("c1") == []


def test_corrupt_snapshot_is_rejected_without_state_change():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "p1", priority=5, duration_ms=5000, now=0)
    scheduler.submit("c1", "p2", priority=1, now=0)

    zero = {"accepted": 0, "merged": 0, "preempted": 0, "expired": 0,
            "completed": 0, "cancelled": 0, "rejected": 0}
    bad_states = [
        None,
        {},
        {"version": 2, "channels": {}, "stats": dict(zero)},
        {"version": 1, "channels": [], "stats": dict(zero)},
        {"version": 1, "channels": {"c1": {"current": "bad", "pending": [],
                                           "seq": 0}}, "stats": dict(zero)},
        {"version": 1, "channels": {}, "stats": {"accepted": "many"}},
    ]
    for bad in bad_states:
        with pytest.raises(sp.SpeechError) as exc:
            scheduler.restore(bad)
        assert exc.value.code == "SPEECH_SNAPSHOT_INVALID"

    assert scheduler.current("c1", now=0)["prompt_id"] == "p1"
    assert scheduler.pending("c1") == ["p2"]


def test_queue_depth_and_flush_control_backlog():
    scheduler = sp.SpeechPromptScheduler()
    scheduler.submit("c1", "p1", priority=5, duration_ms=5000, now=0)
    scheduler.submit("c1", "p2", priority=1, now=0)
    scheduler.submit("c1", "p3", priority=1, now=0)

    depth = scheduler.queue_depth("c1")
    assert depth == {"pending": 2, "max_pending": 8, "playing": True}

    flushed = scheduler.flush("c1")
    assert flushed["dropped"] == 2
    assert scheduler.pending("c1") == []
    assert scheduler.current("c1", now=0)["prompt_id"] == "p1"
    assert scheduler.flush("c1")["dropped"] == 0


def test_concurrent_dedupe_submissions_admit_exactly_one_prompt():
    scheduler = sp.SpeechPromptScheduler()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = scheduler.submit("c1", f"id-{index}", priority=4,
                                   duration_ms=1000, dedupe_key="same",
                                   now=0)
        except Exception as exc:  # pragma: no cover - surfaced via assertions
            out = {"status": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["merged"] * 7 + ["playing"]
    playing = [row for row in results if row["status"] == "playing"][0]
    assert scheduler.current("c1", now=0)["prompt_id"] == playing["prompt_id"]
    assert scheduler.pending("c1") == []
    stats = scheduler.stats()
    assert stats["accepted"] == 1
    assert stats["merged"] == 7
