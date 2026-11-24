import copy
import threading

import pytest

import bindless_handle_table as handles


GEN1 = 1 << 24


def table(capacity=4, frames=2):
    return handles.HandleTable(capacity=capacity, frames_in_flight=frames)


def test_insert_encodes_slot_and_generation_and_returns_copies():
    pool = table()
    first = pool.insert({"id": "albedo", "bytes": 128},
                        kind="texture", tag="material")
    second = pool.insert({"id": "vbo"}, kind="buffer")
    assert first == GEN1 | 0
    assert second == GEN1 | 1

    row = pool.resolve(first)
    assert row["index"] == 0
    assert row["generation"] == 1
    assert row["kind"] == "texture"
    assert row["tag"] == "material"
    assert row["resource"] == {"id": "albedo", "bytes": 128}
    row["resource"]["id"] = "mutated"
    assert pool.resolve(first)["resource"]["id"] == "albedo"
    assert pool.usage() == {"capacity": 4, "live": 2, "pending": 0,
                            "free": 2, "frame": 0}


def test_table_full_until_pending_slots_are_reclaimed():
    pool = table(capacity=2)
    first = pool.insert({"id": 1})
    pool.insert({"id": 2})
    with pytest.raises(handles.HandleError) as exc:
        pool.insert({"id": 3})
    assert exc.value.code == "HANDLE_TABLE_FULL"

    pool.retire(first)
    with pytest.raises(handles.HandleError) as exc:
        pool.insert({"id": 3})
    assert exc.value.code == "HANDLE_TABLE_FULL"

    assert pool.begin_frame(2) == {"frame": 2, "reclaimed": 1}
    replacement = pool.insert({"id": 3})
    assert replacement == (2 << 24) | 0
    assert pool.resolve(replacement)["resource"] == {"id": 3}
    with pytest.raises(handles.HandleError) as exc:
        pool.resolve(first)
    assert exc.value.code == "HANDLE_STALE"


def test_invalid_handle_forms_are_distinguished_from_stale_ones():
    pool = table()
    live = pool.insert({"id": "live"})
    for bad in (0, -1, "handle", True, 1 << 40, (1 << 24) | 99):
        with pytest.raises(handles.HandleError) as exc:
            pool.resolve(bad)
        assert exc.value.code == "HANDLE_INVALID"
        assert pool.contains(bad) is False
    with pytest.raises(handles.HandleError) as exc:
        pool.resolve((2 << 24) | 0)
    assert exc.value.code == "HANDLE_STALE"
    assert pool.contains(live) is True
    assert pool.stats()["stale_hits"] == 1


def test_retire_invalidates_handle_immediately_but_keeps_slot_reserved():
    pool = table()
    pool.begin_frame(10)
    handle = pool.insert({"id": "temp"}, kind="sampler", tag="shadow")
    released = pool.retire(handle)
    assert released == {"handle": handle, "index": 0, "frame": 10, "pending": True}
    with pytest.raises(handles.HandleError) as exc:
        pool.resolve(handle)
    assert exc.value.code == "HANDLE_STALE"
    assert pool.contains(handle) is False
    assert pool.usage() == {"capacity": 4, "live": 0, "pending": 1,
                            "free": 3, "frame": 10}


def test_reclaim_waits_for_frames_in_flight():
    pool = table(capacity=2, frames=2)
    pool.begin_frame(10)
    handle = pool.insert({"id": "frame-bound"})
    pool.retire(handle)

    assert pool.begin_frame(11)["reclaimed"] == 0
    assert pool.usage()["pending"] == 1
    assert pool.begin_frame(12)["reclaimed"] == 1
    assert pool.usage() == {"capacity": 2, "live": 0, "pending": 0,
                            "free": 2, "frame": 12}
    fresh = pool.insert({"id": "reused"})
    assert fresh == (2 << 24) | 0
    assert pool.resolve(fresh)["generation"] == 2


def test_reuse_bumps_generation_so_old_handle_stays_stale():
    pool = table(capacity=2, frames=1)
    pool.begin_frame(1)
    old = pool.insert({"id": "old"})
    pool.retire(old)
    pool.begin_frame(2)
    new = pool.insert({"id": "new"})
    assert new != old
    assert new == (2 << 24) | 0
    with pytest.raises(handles.HandleError) as exc:
        pool.resolve(old)
    assert exc.value.code == "HANDLE_STALE"
    assert pool.resolve(new)["resource"] == {"id": "new"}
    assert pool.stats()["reused"] == 1


def test_free_list_reuses_most_recently_retired_slots_first():
    pool = table(capacity=3, frames=1)
    pool.begin_frame(1)
    first = pool.insert({"id": 0})
    second = pool.insert({"id": 1})
    third = pool.insert({"id": 2})
    pool.retire(first)
    pool.retire(third)
    assert pool.begin_frame(2)["reclaimed"] == 2

    revived_third = pool.insert({"id": "third-again"})
    revived_first = pool.insert({"id": "first-again"})
    assert revived_third == (2 << 24) | 2
    assert revived_first == (2 << 24) | 0
    assert pool.resolve(second)["resource"] == {"id": 1}
    with pytest.raises(handles.HandleError) as exc:
        pool.insert({"id": "overflow"})
    assert exc.value.code == "HANDLE_TABLE_FULL"


def test_insert_validates_kind_tag_and_resource_without_consuming_slots():
    pool = table(capacity=1)
    with pytest.raises(handles.HandleError) as exc:
        pool.insert({"id": 1}, kind="mesh")
    assert exc.value.code == "HANDLE_INVALID_KIND"
    with pytest.raises(handles.HandleError) as exc:
        pool.insert({"id": 1}, tag=7)
    assert exc.value.code == "HANDLE_INVALID_TAG"
    with pytest.raises(handles.HandleError) as exc:
        pool.insert(["not", "a", "dict"])
    assert exc.value.code == "HANDLE_INVALID_RESOURCE"

    handle = pool.insert({"id": "ok"})
    assert handle == GEN1 | 0
    assert pool.usage()["live"] == 1


def test_snapshot_restore_keeps_live_pending_and_reuse_state():
    pool = table(capacity=4, frames=2)
    pool.begin_frame(1)
    alive = pool.insert({"id": "alive"}, kind="buffer")
    doomed = pool.insert({"id": "doomed"}, kind="texture")
    pool.retire(doomed)
    state = pool.snapshot()
    assert state["version"] == 1
    assert len(state["slots"]) == 2

    restored = table(capacity=4, frames=2)
    restored.restore(state)
    assert restored.resolve(alive)["resource"] == {"id": "alive"}
    with pytest.raises(handles.HandleError) as exc:
        restored.resolve(doomed)
    assert exc.value.code == "HANDLE_STALE"
    assert restored.usage() == {"capacity": 4, "live": 1, "pending": 1,
                                "free": 2, "frame": 1}

    fresh = restored.insert({"id": "fresh"})
    assert fresh == GEN1 | 2
    assert restored.begin_frame(3)["reclaimed"] == 1
    reused = restored.insert({"id": "reused"})
    assert reused == (2 << 24) | 1
    assert restored.resolve(reused)["resource"] == {"id": "reused"}


def test_corrupt_snapshot_is_rejected_without_state_change():
    pool = table()
    pool.begin_frame(1)
    handle = pool.insert({"id": "keep"})
    good = pool.snapshot()
    bad_states = [None, {}, {"version": 2}]
    broken = copy.deepcopy(good)
    broken["capacity"] = 8
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["slots"] = "bad"
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["free"] = [0, 0]
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["slots"][0]["generation"] = 0
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["slots"][0]["live"] = "yes"
    bad_states.append(broken)

    for bad in bad_states:
        with pytest.raises(handles.HandleError) as exc:
            pool.restore(bad)
        assert exc.value.code == "HANDLE_SNAPSHOT_INVALID"
    assert pool.resolve(handle)["resource"] == {"id": "keep"}
    assert pool.usage() == {"capacity": 4, "live": 1, "pending": 0,
                            "free": 3, "frame": 1}


def test_stats_accounting_and_reset():
    pool = table()
    first = pool.insert({"id": 1})
    pool.insert({"id": 2})
    pool.retire(first)
    with pytest.raises(handles.HandleError):
        pool.resolve(first)
    pool.begin_frame(2)
    third = pool.insert({"id": 3})
    pool.free_now(third)

    assert pool.stats() == {
        "inserted": 3,
        "retired": 1,
        "reclaimed": 1,
        "reused": 1,
        "stale_hits": 1,
        "freed_now": 1,
    }
    assert pool.usage() == {"capacity": 4, "live": 1, "pending": 0,
                            "free": 3, "frame": 2}
    pool.reset_stats()
    assert pool.stats() == {
        "inserted": 0,
        "retired": 0,
        "reclaimed": 0,
        "reused": 0,
        "stale_hits": 0,
        "freed_now": 0,
    }


def test_concurrent_inserts_allocate_unique_slots():
    pool = table(capacity=128)
    barrier = threading.Barrier(8)
    handles_out = []
    errors = []
    lock = threading.Lock()

    def worker(worker_id):
        barrier.wait()
        try:
            local = [pool.insert({"worker": worker_id, "seq": seq},
                                 kind="texture") for seq in range(8)]
        except Exception as exc:  # pragma: no cover - reported via errors
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            handles_out.extend(local)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(handles_out) == 64
    assert len(set(handles_out)) == 64
    for handle in handles_out:
        row = pool.resolve(handle)
        assert 0 <= row["index"] < 64
        assert row["generation"] == 1
    assert pool.usage()["live"] == 64
    assert pool.usage()["free"] == 64
    assert pool.stats()["inserted"] == 64


def test_frame_order_and_double_retire():
    pool = table()
    pool.begin_frame(5)
    handle = pool.insert({"id": "once"})
    with pytest.raises(handles.HandleError) as exc:
        pool.begin_frame(5)
    assert exc.value.code == "HANDLE_FRAME_ORDER"
    with pytest.raises(handles.HandleError) as exc:
        pool.begin_frame(4)
    assert exc.value.code == "HANDLE_FRAME_ORDER"

    pool.retire(handle)
    with pytest.raises(handles.HandleError) as exc:
        pool.retire(handle)
    assert exc.value.code == "HANDLE_STALE"
    assert pool.stats()["retired"] == 1
    assert pool.stats()["stale_hits"] == 1
    assert pool.usage()["pending"] == 1


def test_pending_slots_count_against_capacity_until_reclaimed():
    pool = table(capacity=2, frames=3)
    first = pool.insert({"id": "a"})
    second = pool.insert({"id": "b"})
    pool.retire(first)
    pool.retire(second)
    assert pool.usage() == {"capacity": 2, "live": 0, "pending": 2,
                            "free": 0, "frame": 0}

    with pytest.raises(handles.HandleError) as exc:
        pool.insert({"id": "c"})
    assert exc.value.code == "HANDLE_TABLE_FULL"
    assert pool.begin_frame(2)["reclaimed"] == 0
    assert pool.begin_frame(3)["reclaimed"] == 2
    assert pool.insert({"id": "c"}) == (2 << 24) | 1
    assert pool.insert({"id": "d"}) == (2 << 24) | 0


def test_contains_and_generation_of_follow_handle_lifecycle():
    pool = table(capacity=2)
    handle = pool.insert({"id": "g"})
    assert pool.contains(handle) is True
    assert pool.generation_of(handle) == 1

    pool.retire(handle)
    assert pool.contains(handle) is False
    with pytest.raises(handles.HandleError) as exc:
        pool.generation_of(handle)
    assert exc.value.code == "HANDLE_STALE"

    other = table(capacity=1)
    with pytest.raises(handles.HandleError) as exc:
        other.resolve(handle)
    assert exc.value.code == "HANDLE_INVALID"
    assert other.contains(handle) is False
