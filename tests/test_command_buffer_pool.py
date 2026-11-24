import copy
import threading

import pytest

import command_buffer_pool as pools


def pool(size=4, frames=2, limit=8):
    return pools.CommandPool(size=size, frames_in_flight=frames,
                             max_recorded_commands=limit)


def test_acquire_returns_recording_buffers_with_stable_ids():
    ring = pool(size=4)
    started = ring.begin_frame(1)
    assert started == {"frame": 1, "open_frames": 1}
    first = ring.acquire(label="shadow")
    second = ring.acquire()
    assert first == {"id": 1, "frame": 1, "label": "shadow",
                     "state": "recording", "commands": 0}
    assert second["id"] == 2
    assert ring.usage() == {"size": 4, "allocated": 2, "free": 2,
                            "recording": 2, "submitted": 0,
                            "frame": 1, "open_frames": 1}


def test_configuration_and_handle_validation():
    with pytest.raises(pools.CommandPoolError) as exc:
        pools.CommandPool(size=0)
    assert exc.value.code == "POOL_INVALID_SIZE"
    with pytest.raises(pools.CommandPoolError) as exc:
        pools.CommandPool(frames_in_flight=0)
    assert exc.value.code == "POOL_INVALID_FRAMES"
    with pytest.raises(pools.CommandPoolError) as exc:
        pools.CommandPool(max_recorded_commands=0)
    assert exc.value.code == "POOL_INVALID_LIMIT"

    ring = pool(size=2)
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.acquire()
    assert exc.value.code == "POOL_NO_FRAME"
    ring.begin_frame(1)
    for bad in (0, 99, True, "one"):
        with pytest.raises(pools.CommandPoolError) as exc:
            ring.state(bad)
        assert exc.value.code == "POOL_BAD_HANDLE"
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.acquire(label=7)
    assert exc.value.code == "POOL_INVALID_LABEL"


def test_record_appends_copies_and_reports_count():
    ring = pool()
    ring.begin_frame(1)
    command_id = ring.acquire()["id"]
    command = {"op": "draw", "args": {"count": 3}}
    assert ring.record(command_id, command) == 1
    assert ring.record(command_id, {"op": "barrier"}) == 2
    command["args"]["count"] = 99

    state = ring.state(command_id)
    assert state["commands"] == 2
    stored = ring.snapshot()["slots"][command_id - 1]["commands"]
    assert stored[0]["args"]["count"] == 3
    assert stored[1] == {"op": "barrier"}


def test_record_limit_rejects_without_shrinking_commands():
    ring = pool(limit=2)
    ring.begin_frame(1)
    command_id = ring.acquire()["id"]
    ring.record(command_id, {"op": "one"})
    ring.record(command_id, {"op": "two"})
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.record(command_id, {"op": "three"})
    assert exc.value.code == "POOL_RECORD_LIMIT"
    assert ring.state(command_id)["commands"] == 2
    assert ring.stats()["recorded"] == 2


def test_state_machine_rejects_recording_after_submit_and_double_submit():
    ring = pool()
    ring.begin_frame(1)
    command_id = ring.acquire()["id"]
    ring.record(command_id, {"op": "dispatch"})
    submitted = ring.submit(command_id)
    assert submitted == {"id": command_id, "frame": 1, "commands": 1}

    with pytest.raises(pools.CommandPoolError) as exc:
        ring.record(command_id, {"op": "late"})
    assert exc.value.code == "POOL_BAD_STATE"
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.submit(command_id)
    assert exc.value.code == "POOL_BAD_STATE"

    ring.reclaim(1)
    assert ring.state(command_id)["state"] == "idle"
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.record(command_id, {"op": "idle"})
    assert exc.value.code == "POOL_BAD_STATE"


def test_reclaim_recycles_buffers_for_the_current_frame():
    ring = pool(size=2)
    ring.begin_frame(1)
    first = ring.acquire()["id"]
    ring.record(first, {"op": "blit"})
    ring.submit(first)
    ring.begin_frame(2)
    second = ring.acquire()["id"]
    assert second == 2

    assert ring.reclaim(1) == 1
    reused = ring.acquire()
    assert reused["id"] == 1
    assert reused["frame"] == 2
    assert reused["state"] == "recording"
    assert ring.usage() == {"size": 2, "allocated": 2, "free": 0,
                            "recording": 2, "submitted": 0,
                            "frame": 2, "open_frames": 1}


def test_exhaustion_is_recoverable_after_reclaim():
    ring = pool(size=2)
    ring.begin_frame(1)
    first = ring.acquire()
    second = ring.acquire()
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.acquire()
    assert exc.value.code == "POOL_EXHAUSTED"

    ring.submit(first["id"])
    ring.submit(second["id"])
    assert ring.reclaim(1) == 2
    ring.begin_frame(2)
    replacement = ring.acquire()
    assert replacement["id"] == 1
    assert ring.usage() == {"size": 2, "allocated": 1, "free": 1,
                            "recording": 1, "submitted": 0,
                            "frame": 2, "open_frames": 1}


def test_reclaim_is_refused_while_any_buffer_is_recording():
    ring = pool(size=3)
    ring.begin_frame(1)
    busy = ring.acquire()["id"]
    ready = ring.acquire()["id"]
    ring.record(busy, {"op": "draw"})
    ring.submit(ready)

    with pytest.raises(pools.CommandPoolError) as exc:
        ring.reclaim(1)
    assert exc.value.code == "POOL_BUFFER_BUSY"
    assert exc.value.detail == [busy]
    assert ring.state(busy)["state"] == "recording"
    assert ring.state(ready)["state"] == "submitted"
    assert ring.usage()["open_frames"] == 1

    ring.submit(busy)
    assert ring.reclaim(1) == 2
    assert ring.usage()["free"] == 3


def test_reclaim_order_unknown_frames_and_idempotency():
    ring = pool(size=2)
    ring.begin_frame(1)
    ring.begin_frame(2)
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.reclaim(2)
    assert exc.value.code == "POOL_FRAME_ORDER"
    assert ring.reclaim(1) == 0
    assert ring.reclaim(1) == 0
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.reclaim(3)
    assert exc.value.code == "POOL_UNKNOWN_FRAME"
    assert ring.reclaim(2) == 0
    assert ring.reclaim(2) == 0


def test_frame_budget_limits_unreclaimed_frames():
    ring = pool(size=2, frames=2)
    ring.begin_frame(1)
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.begin_frame(1)
    assert exc.value.code == "POOL_FRAME_ORDER"
    ring.begin_frame(2)
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.begin_frame(3)
    assert exc.value.code == "POOL_FRAME_BUDGET"
    assert ring.usage()["frame"] == 2
    assert ring.usage()["open_frames"] == 2

    assert ring.reclaim(1) == 0
    assert ring.begin_frame(3) == {"frame": 3, "open_frames": 2}
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.begin_frame(4)
    assert exc.value.code == "POOL_FRAME_BUDGET"


def test_snapshot_restore_keeps_recording_and_submitted_work():
    ring = pool(size=4, frames=2, limit=3)
    ring.begin_frame(1)
    recording = ring.acquire(label="main")["id"]
    ring.record(recording, {"op": "draw"})
    ring.record(recording, {"op": "draw-indexed"})
    submitted = ring.acquire(label="copy")["id"]
    ring.record(submitted, {"op": "copy-buffer"})
    ring.submit(submitted)
    ring.begin_frame(2)
    current = ring.acquire(label="post")["id"]

    state = ring.snapshot()
    assert state["version"] == 1
    assert len(state["slots"]) == 4

    restored = pool(size=4, frames=2, limit=3)
    restored.restore(state)
    assert restored.state(recording)["state"] == "recording"
    assert restored.state(recording)["commands"] == 2
    assert restored.state(submitted)["state"] == "submitted"
    assert restored.state(current)["frame"] == 2
    assert restored.usage() == {"size": 4, "allocated": 3, "free": 1,
                                "recording": 2, "submitted": 1,
                                "frame": 2, "open_frames": 2}

    with pytest.raises(pools.CommandPoolError) as exc:
        restored.reclaim(1)
    assert exc.value.code == "POOL_BUFFER_BUSY"
    restored.submit(recording)
    assert restored.reclaim(1) == 2
    reused = restored.acquire()
    assert reused["id"] == 1
    assert reused["frame"] == 2


def test_corrupt_snapshot_is_rejected_without_state_change():
    ring = pool(size=3, frames=2)
    ring.begin_frame(1)
    command_id = ring.acquire()["id"]
    ring.record(command_id, {"op": "keep"})
    good = ring.snapshot()
    bad_states = [None, {}, {"version": 2}]
    broken = copy.deepcopy(good)
    broken["size"] = 8
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["slots"] = "bad"
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["free"] = [99]
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["open_frames"] = [1, 1]
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["slots"][0]["frame"] = None
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["slots"][2]["state"] = "submitted"
    bad_states.append(broken)

    for bad in bad_states:
        with pytest.raises(pools.CommandPoolError) as exc:
            ring.restore(bad)
        assert exc.value.code == "POOL_SNAPSHOT_INVALID"
    assert ring.state(command_id) == {"id": 1, "frame": 1, "label": "",
                                      "state": "recording", "commands": 1}
    assert ring.usage()["allocated"] == 1


def test_leak_check_flags_old_unreclaimed_frames_only():
    ring = pool(size=4, frames=2)
    ring.begin_frame(1)
    first = ring.acquire()["id"]
    ring.submit(first)
    ring.begin_frame(2)
    second = ring.acquire()["id"]
    ring.submit(second)

    assert ring.leak_check() == []
    assert ring.leak_check(5) == [first, second]
    assert ring.reclaim(1) == 1
    assert ring.leak_check(5) == [second]
    assert ring.reclaim(2) == 1
    assert ring.leak_check(5) == []


def test_concurrent_acquire_never_hands_out_duplicate_buffers():
    ring = pool(size=8, frames=4)
    ring.begin_frame(1)
    barrier = threading.Barrier(8)
    buffers = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            buffer = ring.acquire()
        except Exception as exc:  # pragma: no cover - reported via errors
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            buffers.append(buffer["id"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(buffers) == list(range(1, 9))
    assert ring.usage() == {"size": 8, "allocated": 8, "free": 0,
                            "recording": 8, "submitted": 0,
                            "frame": 1, "open_frames": 1}
    with pytest.raises(pools.CommandPoolError) as exc:
        ring.acquire()
    assert exc.value.code == "POOL_EXHAUSTED"


def test_stats_and_usage_invariants():
    ring = pool(size=3, frames=2)
    ring.begin_frame(1)
    first = ring.acquire()["id"]
    ring.record(first, {"op": "a"})
    ring.record(first, {"op": "b"})
    ring.submit(first)
    ring.begin_frame(2)
    second = ring.acquire()["id"]
    ring.submit(second)
    ring.reclaim(1)
    third = ring.acquire()["id"]

    assert ring.stats() == {"acquired": 3, "submitted": 2,
                            "reclaimed": 1, "recorded": 2, "frames": 2}
    usage = ring.usage()
    assert usage == {"size": 3, "allocated": 2, "free": 1,
                     "recording": 1, "submitted": 1,
                     "frame": 2, "open_frames": 1}
    assert usage["allocated"] + usage["free"] == usage["size"]
    assert usage["recording"] + usage["submitted"] <= usage["allocated"]
    ring.reset_stats()
    assert ring.stats() == {"acquired": 0, "submitted": 0,
                            "reclaimed": 0, "recorded": 0, "frames": 0}
