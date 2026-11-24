import copy
import threading

import pytest

import render_target_pool as targets


def pool(budget=1 << 20, frames=2):
    return targets.RenderTargetPool(budget_bytes=budget, frames_in_flight=frames)


def test_acquire_reports_key_bytes_and_lease_state():
    cache = pool()
    cache.begin_frame(1)
    acquired = cache.acquire(64, 64, tag="gbuffer")
    assert acquired == {
        "key": "64x64:rgba8:1",
        "id": 1,
        "width": 64,
        "height": 64,
        "format": "rgba8",
        "samples": 1,
        "bytes": 16384,
        "frame": 1,
        "tag": "gbuffer",
        "reused": False,
    }
    assert cache.usage() == {
        "budget": 1 << 20,
        "resident_bytes": 16384,
        "free_bytes": (1 << 20) - 16384,
        "entries": 1,
        "leased": 1,
        "pending": 0,
    }


def test_size_format_sample_and_configuration_validation():
    with pytest.raises(targets.RenderTargetError) as exc:
        targets.RenderTargetPool(budget_bytes=0)
    assert exc.value.code == "RT_INVALID_BUDGET"
    with pytest.raises(targets.RenderTargetError) as exc:
        targets.RenderTargetPool(frames_in_flight=0)
    assert exc.value.code == "RT_INVALID_FRAMES"
    with pytest.raises(targets.RenderTargetError) as exc:
        targets.RenderTargetPool(alignment=3)
    assert exc.value.code == "RT_INVALID_ALIGNMENT"

    cache = pool()
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64)
    assert exc.value.code == "RT_NO_FRAME"
    cache.begin_frame(1)
    for width, height in ((0, 64), (64, 0), (65, 64), (64, 65)):
        with pytest.raises(targets.RenderTargetError) as exc:
            cache.acquire(width, height)
        assert exc.value.code == "RT_INVALID_SIZE"
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64, format="rgb10a2")
    assert exc.value.code == "RT_UNKNOWN_FORMAT"
    for samples in (0, 3, 16):
        with pytest.raises(targets.RenderTargetError) as exc:
            cache.acquire(64, 64, samples=samples)
        assert exc.value.code == "RT_INVALID_SAMPLES"
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64, tag=9)
    assert exc.value.code == "RT_INVALID_TAG"
    assert cache.usage()["entries"] == 0


def test_byte_size_accounts_for_format_and_sample_count():
    cache = pool()
    cache.begin_frame(1)
    half = cache.acquire(32, 32, format="rgba16f")
    single = cache.acquire(8, 8, format="r8")
    multisample = cache.acquire(16, 16, format="rgba8", samples=4)
    depth = cache.acquire(64, 32, format="depth32f")
    assert half["bytes"] == 8192
    assert single["bytes"] == 64
    assert multisample["bytes"] == 4096
    assert depth["bytes"] == 8192
    assert half["key"] == "32x32:rgba16f:1"
    assert multisample["key"] == "16x16:rgba8:4"
    assert cache.usage() == {
        "budget": 1 << 20,
        "resident_bytes": 20544,
        "free_bytes": (1 << 20) - 20544,
        "entries": 4,
        "leased": 4,
        "pending": 0,
    }


def test_released_target_is_reused_after_frames_in_flight():
    cache = pool(frames=2)
    cache.begin_frame(1)
    first = cache.acquire(64, 64)
    released = cache.release(first["key"])
    assert released == {"key": "64x64:rgba8:1", "id": 1,
                        "frame": 1, "pending": True}

    assert cache.begin_frame(3) == {"frame": 3, "retired": 1,
                                   "resident_bytes": 16384}
    second = cache.acquire(64, 64, tag="again")
    assert second["reused"] is True
    assert second["id"] == first["id"]
    assert second["frame"] == 3
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_busy_pending_and_missing_targets_are_distinguished():
    cache = pool()
    cache.begin_frame(1)
    key = cache.acquire(64, 64)["key"]

    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64)
    assert exc.value.code == "RT_TARGET_BUSY"
    cache.release(key)
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64)
    assert exc.value.code == "RT_TARGET_PENDING"
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.release(key)
    assert exc.value.code == "RT_NOT_LEASED"
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.release("ghost")
    assert exc.value.code == "RT_UNKNOWN_TARGET"
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.state("ghost")
    assert exc.value.code == "RT_UNKNOWN_TARGET"


def test_budget_pressure_evicts_least_recently_used_available_target():
    cache = pool(budget=49152)
    cache.begin_frame(1)
    first = cache.acquire(64, 64)
    second = cache.acquire(128, 32)
    third = cache.acquire(32, 128)
    assert cache.usage()["resident_bytes"] == 49152
    for row in (first, second, third):
        cache.release(row["key"])
    assert cache.begin_frame(3)["retired"] == 3

    fourth = cache.acquire(256, 16)
    assert fourth["reused"] is False
    assert fourth["bytes"] == 16384
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.state(first["key"])
    assert exc.value.code == "RT_UNKNOWN_TARGET"
    assert cache.usage() == {"budget": 49152, "resident_bytes": 49152,
                             "free_bytes": 0, "entries": 3,
                             "leased": 1, "pending": 0}

    fifth = cache.acquire(16, 256)
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.state(second["key"])
    assert exc.value.code == "RT_UNKNOWN_TARGET"
    assert cache.state(third["key"])["leased"] is False
    assert cache.stats()["evicted"] == 2


def test_oversized_target_is_rejected_without_evicting_anything():
    cache = pool(budget=8000)
    cache.begin_frame(1)
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64)
    assert exc.value.code == "RT_BUDGET_EXCEEDED"
    assert cache.usage()["entries"] == 0

    small = cache.acquire(32, 32)
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64)
    assert exc.value.code == "RT_BUDGET_EXCEEDED"
    assert cache.state(small["key"])["leased"] is True
    assert cache.usage() == {"budget": 8000, "resident_bytes": 4096,
                             "free_bytes": 3904, "entries": 1,
                             "leased": 1, "pending": 0}


def test_frame_order_and_deferred_retirement_boundary():
    cache = pool(frames=2)
    cache.begin_frame(1)
    key = cache.acquire(64, 64)["key"]
    cache.release(key)

    assert cache.begin_frame(2) == {"frame": 2, "retired": 0,
                                   "resident_bytes": 16384}
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(64, 64)
    assert exc.value.code == "RT_TARGET_PENDING"
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.begin_frame(2)
    assert exc.value.code == "RT_FRAME_ORDER"

    assert cache.begin_frame(3) == {"frame": 3, "retired": 1,
                                   "resident_bytes": 16384}
    revived = cache.acquire(64, 64)
    assert revived["reused"] is True
    assert cache.usage()["pending"] == 0


def test_purge_drops_unleased_entries_only():
    cache = pool()
    cache.begin_frame(1)
    first = cache.acquire(64, 64)
    second = cache.acquire(128, 32)
    cache.release(first["key"])

    assert cache.purge() == {"dropped": 1, "bytes": 16384}
    assert cache.state(second["key"])["leased"] is True
    assert cache.usage() == {"budget": 1 << 20, "resident_bytes": 16384,
                             "free_bytes": (1 << 20) - 16384,
                             "entries": 1, "leased": 1, "pending": 0}
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.state(first["key"])
    assert exc.value.code == "RT_UNKNOWN_TARGET"

    cache.release(second["key"])
    assert cache.purge() == {"dropped": 1, "bytes": 16384}
    assert cache.purge() == {"dropped": 0, "bytes": 0}
    assert cache.usage()["entries"] == 0


def test_snapshot_restore_preserves_leases_pending_and_identity():
    cache = pool()
    cache.begin_frame(1)
    available = cache.acquire(64, 64)
    cache.release(available["key"])
    leased = cache.acquire(128, 32)
    cache.begin_frame(3)
    current = cache.acquire(32, 128)

    state = cache.snapshot()
    assert state["version"] == 1
    assert len(state["entries"]) == 3

    restored = pool()
    restored.restore(state)
    assert restored.usage() == {"budget": 1 << 20,
                                "resident_bytes": 16384 + 16384 + 16384,
                                "free_bytes": (1 << 20) - 49152,
                                "entries": 3, "leased": 2, "pending": 0}
    revived = restored.acquire(64, 64)
    assert revived["reused"] is True
    assert revived["id"] == available["id"]
    assert restored.state(leased["key"])["leased"] is True
    assert restored.state(current["key"])["frame"] == 3
    restored.release(leased["key"])
    assert restored.state(leased["key"])["pending_frame"] == 3


def test_corrupt_snapshot_is_rejected_without_state_change():
    cache = pool()
    cache.begin_frame(1)
    key = cache.acquire(64, 64)["key"]
    good = cache.snapshot()
    bad_states = [None, {}, {"version": 2}]
    broken = copy.deepcopy(good)
    broken["budget_bytes"] = 1234
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["entries"] = "bad"
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["entries"][0]["key"] = "wrong-key"
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["entries"][0]["bytes"] = 1
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["entries"][0]["pending_frame"] = 1
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["entries"].append(copy.deepcopy(broken["entries"][0]))
    bad_states.append(broken)

    for bad in bad_states:
        with pytest.raises(targets.RenderTargetError) as exc:
            cache.restore(bad)
        assert exc.value.code == "RT_SNAPSHOT_INVALID"
    assert cache.state(key)["leased"] is True
    assert cache.usage() == {"budget": 1 << 20, "resident_bytes": 16384,
                             "free_bytes": (1 << 20) - 16384,
                             "entries": 1, "leased": 1, "pending": 0}


def test_concurrent_acquisition_fills_exact_budget_without_aliasing():
    budget = 8 * 16384
    cache = pool(budget=budget, frames=4)
    cache.begin_frame(1)
    dims = [(64, 64), (128, 32), (32, 128), (256, 16),
            (16, 256), (512, 8), (8, 512), (1024, 4)]
    barrier = threading.Barrier(8)
    results = []
    errors = []
    lock = threading.Lock()

    def worker(width, height):
        barrier.wait()
        try:
            row = cache.acquire(width, height)
        except Exception as exc:  # pragma: no cover - reported via errors
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            results.append(row)

    threads = [threading.Thread(target=worker, args=shape) for shape in dims]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 8
    assert len({row["key"] for row in results}) == 8
    assert len({row["id"] for row in results}) == 8
    assert cache.usage() == {"budget": budget, "resident_bytes": budget,
                             "free_bytes": 0, "entries": 8,
                             "leased": 8, "pending": 0}
    with pytest.raises(targets.RenderTargetError) as exc:
        cache.acquire(2048, 4)
    assert exc.value.code == "RT_BUDGET_EXCEEDED"


def test_stats_track_hits_evictions_retirement_and_purge():
    cache = pool(budget=49152, frames=2)
    cache.begin_frame(1)
    first = cache.acquire(64, 64)
    cache.release(first["key"])
    cache.begin_frame(3)
    cache.acquire(64, 64)
    second = cache.acquire(128, 32)
    third = cache.acquire(32, 128)
    cache.release(second["key"])
    cache.begin_frame(5)
    cache.acquire(256, 16)
    cache.release(third["key"])
    cache.purge()

    assert cache.stats() == {
        "acquires": 5,
        "hits": 1,
        "misses": 4,
        "releases": 3,
        "evicted": 1,
        "retired": 2,
        "purged": 1,
    }
    assert cache.usage() == {"budget": 49152, "resident_bytes": 32768,
                             "free_bytes": 16384, "entries": 2,
                             "leased": 2, "pending": 0}
    cache.reset_stats()
    assert cache.stats() == {
        "acquires": 0,
        "hits": 0,
        "misses": 0,
        "releases": 0,
        "evicted": 0,
        "retired": 0,
        "purged": 0,
    }


def test_keys_separate_format_and_sample_variants():
    cache = pool()
    cache.begin_frame(1)
    plain = cache.acquire(64, 64)
    wide = cache.acquire(64, 64, format="rgba16f")
    multisample = cache.acquire(64, 64, samples=2)
    narrow = cache.acquire(32, 64)

    keys = [plain["key"], wide["key"], multisample["key"], narrow["key"]]
    assert keys == ["64x64:rgba8:1", "64x64:rgba16f:1",
                    "64x64:rgba8:2", "32x64:rgba8:1"]
    assert len(set(keys)) == 4
    assert [row["id"] for row in (plain, wide, multisample, narrow)] == [1, 2, 3, 4]
    assert wide["bytes"] == 32768
    assert multisample["bytes"] == 32768
    assert narrow["bytes"] == 8192
    assert cache.usage() == {"budget": 1 << 20,
                             "resident_bytes": 90112,
                             "free_bytes": (1 << 20) - 90112,
                             "entries": 4, "leased": 4, "pending": 0}


def test_usage_and_purge_follow_mixed_lease_lifecycles():
    cache = pool(budget=32768)
    cache.begin_frame(1)
    big = cache.acquire(64, 64)
    small = cache.acquire(32, 32)
    cache.release(big["key"])

    usage = cache.usage()
    assert usage == {"budget": 32768, "resident_bytes": 20480,
                     "free_bytes": 12288, "entries": 2,
                     "leased": 1, "pending": 1}
    assert usage["resident_bytes"] + usage["free_bytes"] == usage["budget"]

    assert cache.purge() == {"dropped": 1, "bytes": 16384}
    assert cache.usage() == {"budget": 32768, "resident_bytes": 4096,
                             "free_bytes": 28672, "entries": 1,
                             "leased": 1, "pending": 0}
    cache.release(small["key"])
    assert cache.purge() == {"dropped": 1, "bytes": 4096}
    assert cache.usage() == {"budget": 32768, "resident_bytes": 0,
                             "free_bytes": 32768, "entries": 0,
                             "leased": 0, "pending": 0}
