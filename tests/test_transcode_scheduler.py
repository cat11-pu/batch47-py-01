import threading

import pytest

import transcode_scheduler as ts


def job(job_id, duration_ms=1000):
    return {"id": job_id, "duration_ms": duration_ms}


def scheduler(**kwargs):
    return ts.TranscodeScheduler(**kwargs)


def test_submit_validates_and_rejects_duplicate_ids():
    sched = scheduler()
    with pytest.raises(ts.TxError) as exc:
        sched.submit({"id": "", "duration_ms": 1000}, tenant="t1")
    assert exc.value.code == "TX_JOB_INVALID"
    with pytest.raises(ts.TxError) as exc:
        sched.submit({"id": "j1", "duration_ms": 0}, tenant="t1")
    assert exc.value.code == "TX_JOB_INVALID"
    first = sched.submit(job("j1"), tenant="t1", priority=2)
    assert first == {"job_id": "j1", "tenant": "t1", "priority": 2,
                     "attempts": 0, "state": "pending"}
    with pytest.raises(ts.TxError) as exc:
        sched.submit(job("j1"), tenant="t1")
    assert exc.value.code == "TX_DUPLICATE_JOB"
    assert len(sched.pending()) == 1


def test_batch_submit_is_all_or_nothing():
    sched = scheduler()
    rows = sched.submit_batch([job("a"), job("b")], tenant="t1", priority=1)
    assert [row["job_id"] for row in rows] == ["a", "b"]
    with pytest.raises(ts.TxError) as exc:
        sched.submit_batch([job("c"), {"id": "bad", "duration_ms": -1}],
                           tenant="t1")
    assert exc.value.code == "TX_JOB_INVALID"
    assert [row["job_id"] for row in sched.pending()] == ["a", "b"]
    with pytest.raises(ts.TxError) as exc:
        sched.submit_batch([job("d"), job("d")], tenant="t1")
    assert exc.value.code == "TX_DUPLICATE_JOB"
    assert sched.stats()["submitted"] == 2


def test_queue_and_batch_limits_are_enforced():
    sched = scheduler(queue_limit=2, batch_limit=2)
    sched.submit(job("a"), tenant="t1")
    sched.submit(job("b"), tenant="t1")
    with pytest.raises(ts.TxError) as exc:
        sched.submit(job("c"), tenant="t1")
    assert exc.value.code == "TX_QUEUE_FULL"
    with pytest.raises(ts.TxError) as exc:
        sched.submit_batch([job("d"), job("e"), job("f")], tenant="t1")
    assert exc.value.code == "TX_BATCH_TOO_LARGE"

    roomy = scheduler(queue_limit=3, batch_limit=5)
    roomy.submit(job("a"), tenant="t1")
    with pytest.raises(ts.TxError) as exc:
        roomy.submit_batch([job("b"), job("c"), job("d")], tenant="t1")
    assert exc.value.code == "TX_QUEUE_FULL"
    assert len(roomy.pending()) == 1


def test_acquire_follows_priority_then_submission_order():
    sched = scheduler()
    sched.submit(job("low"), tenant="t1", priority=0)
    sched.submit(job("high-1"), tenant="t1", priority=5)
    sched.submit(job("high-2"), tenant="t1", priority=5)
    order = [sched.acquire(worker=f"w{i}", now_ms=0)["job_id"]
             for i in range(3)]
    assert order == ["high-1", "high-2", "low"]


def test_total_slot_limit_returns_none_when_full():
    sched = scheduler(total_slots=2)
    for name in ("a", "b", "c"):
        sched.submit(job(name), tenant="t1")
    assert sched.acquire(worker="w1", now_ms=0)["job_id"] == "a"
    assert sched.acquire(worker="w2", now_ms=0)["job_id"] == "b"
    assert sched.acquire(worker="w3", now_ms=0) is None
    assert len(sched.running()) == 2
    assert sched.stats()["started"] == 2


def test_tenant_quota_is_applied_and_configurable():
    sched = scheduler(per_tenant_slots=1)
    sched.submit(job("a1"), tenant="t1")
    sched.submit(job("b1"), tenant="t1")
    sched.submit(job("c1"), tenant="t2")
    assert sched.acquire(worker="w1", now_ms=0)["job_id"] == "a1"
    assert sched.acquire(worker="w2", now_ms=0)["job_id"] == "c1"
    assert sched.acquire(worker="w3", now_ms=0) is None

    assert sched.set_quota("t1", slots=2) == {"tenant": "t1", "slots": 2}
    assert sched.acquire(worker="w4", now_ms=0)["job_id"] == "b1"
    for bad in (-1, 99):
        with pytest.raises(ts.TxError) as exc:
            sched.set_quota("t1", slots=bad)
        assert exc.value.code == "TX_QUOTA_INVALID"
    sched.set_quota("t1", slots=0)
    sched.submit(job("a2"), tenant="t1")
    assert sched.acquire(worker="w5", now_ms=0) is None


def test_complete_releases_slot_and_validates_lease_owner():
    sched = scheduler(total_slots=1)
    sched.submit(job("a"), tenant="t1")
    sched.acquire(worker="w1", now_ms=0)
    with pytest.raises(ts.TxError) as exc:
        sched.complete("a", worker="w2", now_ms=10)
    assert exc.value.code == "TX_LEASE_MISMATCH"
    with pytest.raises(ts.TxError) as exc:
        sched.complete("ghost", worker="w1", now_ms=10)
    assert exc.value.code == "TX_LEASE_UNKNOWN"
    done = sched.complete("a", worker="w1", now_ms=10)
    assert done == {"job_id": "a", "state": "completed", "duration_ms": 1000}
    assert sched.running() == []
    assert sched.stats()["completed"] == 1
    sched.submit(job("b"), tenant="t1")
    assert sched.acquire(worker="w1", now_ms=20)["job_id"] == "b"


def test_expired_lease_cannot_complete_and_reap_requeues():
    sched = scheduler(lease_ms=1000)
    sched.submit(job("a"), tenant="t1")
    sched.acquire(worker="w1", now_ms=0)
    with pytest.raises(ts.TxError) as exc:
        sched.complete("a", worker="w1", now_ms=1000)
    assert exc.value.code == "TX_LEASE_EXPIRED"
    report = sched.reap(1000)
    assert report == [{"job_id": "a", "outcome": "requeued", "attempts": 1}]
    pending = sched.pending()
    assert [(row["job_id"], row["attempts"]) for row in pending] == [("a", 1)]
    assert sched.acquire(worker="w2", now_ms=1001)["attempts"] == 1


def test_reap_marks_dead_after_attempt_budget():
    sched = scheduler(lease_ms=1000, max_attempts=2)
    sched.submit(job("a"), tenant="t1")
    sched.acquire(worker="w1", now_ms=0)
    assert sched.reap(1000)[0]["outcome"] == "requeued"
    sched.acquire(worker="w2", now_ms=1000)
    report = sched.reap(2000)
    assert report == [{"job_id": "a", "outcome": "dead", "attempts": 2}]
    assert sched.pending() == []
    assert sched.running() == []
    assert sched.stats()["dead"] == 1


def test_fail_retries_until_attempt_budget_then_dead():
    sched = scheduler(max_attempts=2)
    sched.submit(job("a"), tenant="t1")
    sched.submit(job("b"), tenant="t1")
    assert sched.acquire(worker="w1", now_ms=0)["job_id"] == "a"
    first = sched.fail("a", worker="w1", now_ms=10, retry=True)
    assert first == {"job_id": "a", "state": "retried", "attempts": 1}
    assert sched.acquire(worker="w1", now_ms=20)["job_id"] == "b"
    assert sched.acquire(worker="w2", now_ms=20)["job_id"] == "a"
    second = sched.fail("a", worker="w2", now_ms=30, retry=True)
    assert second == {"job_id": "a", "state": "dead", "attempts": 2}
    with pytest.raises(ts.TxError) as exc:
        sched.fail("b", worker="w9", now_ms=40, retry=False)
    assert exc.value.code == "TX_LEASE_MISMATCH"
    final = sched.fail("b", worker="w1", now_ms=40, retry=False)
    assert final == {"job_id": "b", "state": "failed", "attempts": 1}
    assert sched.stats()["failed"] == 1
    assert sched.stats()["retried"] == 1
    assert sched.stats()["dead"] == 1


def test_preempt_requeues_lowest_priority_first():
    sched = scheduler(total_slots=3, per_tenant_slots=3)
    sched.submit(job("p0"), tenant="t1", priority=0)
    sched.submit(job("p5"), tenant="t1", priority=5)
    sched.submit(job("p1"), tenant="t1", priority=1)
    for index in range(3):
        sched.acquire(worker=f"w{index}", now_ms=0)
    assert [row["job_id"] for row in sched.running()] == ["p0", "p1", "p5"]
    preempted = sched.preempt(tenant="t1", count=2, now_ms=10)
    assert preempted == ["p0", "p1"]
    assert [row["job_id"] for row in sched.pending()] == ["p1", "p0"]
    assert [row["job_id"] for row in sched.running()] == ["p5"]
    assert sched.stats()["preempted"] == 2
    with pytest.raises(ts.TxError) as exc:
        sched.preempt(tenant="t1", count=0, now_ms=10)
    assert exc.value.code == "TX_PREEMPT_INVALID"


def test_pending_and_running_views_are_ordered():
    sched = scheduler()
    sched.submit(job("later"), tenant="t2", priority=1)
    sched.submit(job("first"), tenant="t1", priority=9)
    pending = sched.pending()
    assert [(row["job_id"], row["tenant"], row["priority"], row["attempts"])
            for row in pending] == [("first", "t1", 9, 0),
                                    ("later", "t2", 1, 0)]
    leased = sched.acquire(worker="w1", now_ms=100)
    assert leased == {"job_id": "first", "tenant": "t1", "priority": 9,
                      "attempts": 0, "worker": "w1", "lease_expires": 30100}
    assert sched.running() == [{"job_id": "first", "tenant": "t1",
                                "worker": "w1", "priority": 9,
                                "lease_expires": 30100}]


def test_snapshot_restore_and_corrupt_state():
    sched = scheduler()
    sched.submit(job("a"), tenant="t1", priority=3)
    sched.submit(job("b"), tenant="t1")
    sched.acquire(worker="w1", now_ms=0)
    state = sched.snapshot()

    restored = scheduler()
    restored.restore(state)
    assert [row["job_id"] for row in restored.pending()] == ["b"]
    assert [row["job_id"] for row in restored.running()] == ["a"]
    assert restored.stats()["submitted"] == 2
    assert restored.stats()["started"] == 1
    done = restored.complete("a", worker="w1", now_ms=500)
    assert done["state"] == "completed"
    with pytest.raises(ts.TxError) as exc:
        restored.submit(job("a"), tenant="t1")
    assert exc.value.code == "TX_DUPLICATE_JOB"
    assert state["running"][0]["worker"] == "w1"

    for bad in (None, {}, {"version": 2, "pending": [], "running": [],
                           "stats": {}},
                {"version": 1, "pending": "nope", "running": [],
                 "stats": {}},
                {"version": 1, "pending": [], "running": [],
                 "stats": "nope"}):
        with pytest.raises(ts.TxError) as exc:
            restored.restore(bad)
        assert exc.value.code == "TX_SNAPSHOT_INVALID"
    assert [row["job_id"] for row in restored.pending()] == ["b"]


def test_concurrent_acquire_never_exceeds_slot_count():
    sched = scheduler(total_slots=3)
    for index in range(10):
        sched.submit(job(f"j{index}"), tenant="t1")
    barrier = threading.Barrier(10)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = sched.acquire(worker=f"w{index}", now_ms=0)
        except Exception:  # pragma: no cover - surfaced via assertion
            out = None
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    granted = [row for row in results if row is not None]
    assert len(granted) == 3
    assert len(results) == 10
    assert len(sched.running()) == 3
    assert sched.stats()["started"] == 3


def test_stats_and_reset():
    sched = scheduler()
    sched.submit(job("a"), tenant="t1")
    sched.submit(job("b"), tenant="t1")
    sched.acquire(worker="w1", now_ms=0)
    sched.complete("a", worker="w1", now_ms=10)
    sched.acquire(worker="w1", now_ms=20)
    sched.fail("b", worker="w1", now_ms=30, retry=False)
    assert sched.stats() == {
        "submitted": 2,
        "started": 2,
        "completed": 1,
        "failed": 1,
        "retried": 0,
        "dead": 0,
        "preempted": 0,
        "expired": 0,
    }
    sched.reset_stats()
    assert sched.stats() == {
        "submitted": 0,
        "started": 0,
        "completed": 0,
        "failed": 0,
        "retried": 0,
        "dead": 0,
        "preempted": 0,
        "expired": 0,
    }
