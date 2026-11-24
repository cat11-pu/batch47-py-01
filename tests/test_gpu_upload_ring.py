import copy
import threading

import pytest

import gpu_upload_ring as ring_mod


def make_ring(**overrides):
    kwargs = {"capacity": 4096, "alignment": 256, "frames_in_flight": 2}
    kwargs.update(overrides)
    return ring_mod.UploadRing(**kwargs)


def test_allocate_aligns_and_assigns_monotonic_handles():
    ring = make_ring()
    ring.begin_frame(1)
    first = ring.allocate(100)
    second = ring.allocate(200)
    third = ring.allocate(10, alignment=512)

    assert [first["handle"], second["handle"], third["handle"]] == [1, 2, 3]
    assert first["offset"] == 0
    assert second["offset"] == 256
    assert third["offset"] == 512
    assert first["frame"] == 1
    assert third["alignment"] == 512
    usage = ring.usage()
    assert usage["used"] == 310
    assert usage["free"] == 3786
    assert usage["padding"] == 212
    assert usage["blocks"] == 3


def test_capacity_and_alignment_are_validated():
    with pytest.raises(ring_mod.UploadError) as exc:
        ring_mod.UploadRing(capacity=1000, alignment=256)
    assert exc.value.code == "RING_INVALID_CAPACITY"
    for bad in (0, 3, 8192):
        with pytest.raises(ring_mod.UploadError) as exc:
            ring_mod.UploadRing(alignment=bad)
        assert exc.value.code == "RING_INVALID_ALIGNMENT"
    with pytest.raises(ring_mod.UploadError) as exc:
        ring_mod.UploadRing(frames_in_flight=0)
    assert exc.value.code == "RING_INVALID_FRAMES"

    ring = make_ring()
    ring.begin_frame(1)
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.allocate(0)
    assert exc.value.code == "RING_INVALID_SIZE"
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.allocate(16, alignment=100)
    assert exc.value.code == "RING_INVALID_ALIGNMENT"


def test_write_read_roundtrip_and_partial_read():
    ring = make_ring()
    ring.begin_frame(1)
    block = ring.allocate(64)
    handle = block["handle"]

    assert ring.write(handle, b"vertex-data") == 11
    assert ring.read(handle, length=11) == b"vertex-data"
    assert ring.write(handle, b"XYZ", offset=4) == 3
    assert ring.read(handle, offset=4, length=3) == b"XYZ"
    assert ring.read(handle, length=7) == b"vertXYZ"
    assert ring.stats()["bytes_written"] == 14


def test_write_overflow_is_rejected_without_mutation():
    ring = make_ring()
    ring.begin_frame(1)
    handle = ring.allocate(8)["handle"]
    ring.write(handle, b"abcdefgh")

    with pytest.raises(ring_mod.UploadError) as exc:
        ring.write(handle, b"123456789")
    assert exc.value.code == "RING_WRITE_OVERFLOW"
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.write(handle, b"x", offset=8)
    assert exc.value.code == "RING_WRITE_OVERFLOW"
    assert ring.read(handle) == b"abcdefgh"


def test_ring_wraps_to_reuse_leading_space():
    ring = make_ring()
    ring.begin_frame(1)
    big = ring.allocate(3072)
    tail = ring.allocate(512)
    assert (big["offset"], tail["offset"]) == (0, 3072)

    ring.release(big["handle"])
    wrapped = ring.allocate(2048)
    assert wrapped["offset"] == 0
    follower = ring.allocate(1024)
    assert follower["offset"] == 2048
    assert ring.stats()["wraps"] == 1

    with pytest.raises(ring_mod.UploadError) as exc:
        ring.allocate(256)
    assert exc.value.code == "RING_OVER_CAPACITY"
    assert ring.usage()["used"] == 3584


def test_exhaustion_does_not_consume_handle():
    ring = make_ring(capacity=512)
    ring.begin_frame(1)
    first = ring.allocate(512)
    assert first["handle"] == 1
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.allocate(512)
    assert exc.value.code == "RING_OVER_CAPACITY"

    ring.release(first["handle"])
    replacement = ring.allocate(512)
    assert replacement["handle"] == 2
    assert replacement["offset"] == 0


def test_retire_frame_frees_only_that_frame():
    ring = make_ring(frames_in_flight=3)
    ring.begin_frame(1)
    old = ring.allocate(256)
    ring.begin_frame(2)
    kept = ring.allocate(256)

    assert ring.retire_frame(1) == 1
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.resolve(old["handle"])
    assert exc.value.code == "RING_STALE_HANDLE"
    assert ring.resolve(kept["handle"])["frame"] == 2
    assert ring.usage()["used"] == 256

    ring.begin_frame(3)
    late = ring.allocate(128)
    assert late["offset"] == 512
    assert ring.usage()["used"] == 384


def test_retire_order_and_idempotency():
    ring = make_ring(frames_in_flight=3)
    ring.begin_frame(1)
    ring.begin_frame(2)

    with pytest.raises(ring_mod.UploadError) as exc:
        ring.retire_frame(2)
    assert exc.value.code == "RING_FRAME_ORDER"
    assert ring.retire_frame(1) == 0
    assert ring.retire_frame(1) == 0
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.retire_frame(9)
    assert exc.value.code == "RING_UNKNOWN_FRAME"


def test_begin_frame_must_increase_and_auto_retires_oldest():
    ring = make_ring()
    ring.begin_frame(1)
    old = ring.allocate(256)
    ring.begin_frame(2)
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.begin_frame(1)
    assert exc.value.code == "RING_FRAME_ORDER"

    started = ring.begin_frame(3)
    assert started == {"frame": 3, "auto_retired": 1}
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.resolve(old["handle"])
    assert exc.value.code == "RING_STALE_HANDLE"
    assert ring.usage() == {
        "capacity": 4096,
        "used": 0,
        "free": 4096,
        "padding": 0,
        "blocks": 0,
        "frame": 3,
        "active_frames": [2, 3],
    }


def test_released_handle_is_stale_and_bad_handle_distinguished():
    ring = make_ring()
    ring.begin_frame(1)
    handle = ring.allocate(64)["handle"]
    ring.release(handle)

    with pytest.raises(ring_mod.UploadError) as exc:
        ring.write(handle, b"x")
    assert exc.value.code == "RING_STALE_HANDLE"
    with pytest.raises(ring_mod.UploadError) as exc:
        ring.resolve(9999)
    assert exc.value.code == "RING_BAD_HANDLE"

    replacement = ring.allocate(64)
    assert replacement["handle"] == 2
    assert ring.write(replacement["handle"], b"alive") == 5


def test_snapshot_restore_preserves_blocks_and_bytes():
    ring = make_ring()
    ring.begin_frame(1)
    first = ring.allocate(64)
    ring.write(first["handle"], b"ABCD")
    ring.begin_frame(2)
    second = ring.allocate(128)
    ring.write(second["handle"], b"EFGH", offset=4)

    state = ring.snapshot()
    assert state["version"] == 1
    assert len(state["blocks"]) == 2

    restored = make_ring()
    restored.restore(state)
    assert restored.read(first["handle"], length=4) == b"ABCD"
    assert restored.read(second["handle"], offset=4, length=4) == b"EFGH"
    assert restored.resolve(second["handle"])["offset"] == 256
    assert restored.usage()["used"] == 192
    assert restored.usage()["active_frames"] == [1, 2]

    fresh = restored.allocate(32)
    assert fresh["handle"] == 3
    assert fresh["offset"] == 512


def test_corrupt_snapshot_rejected_without_state_change():
    ring = make_ring()
    ring.begin_frame(1)
    handle = ring.allocate(32)["handle"]
    ring.write(handle, b"OK")
    good = ring.snapshot()

    bad_states = [None, {}, {"version": 2}]
    broken = copy.deepcopy(good)
    broken["blocks"] = "not-a-list"
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["blocks"][0]["data"] = "zz"
    bad_states.append(broken)
    broken = copy.deepcopy(good)
    broken["capacity"] = 8192
    bad_states.append(broken)

    for bad in bad_states:
        with pytest.raises(ring_mod.UploadError) as exc:
            ring.restore(bad)
        assert exc.value.code == "RING_SNAPSHOT_INVALID"
    assert ring.read(handle, length=2) == b"OK"
    assert ring.allocate(32)["handle"] == 2


def test_concurrent_allocations_do_not_overlap():
    ring = make_ring(capacity=16384)
    ring.begin_frame(1)
    barrier = threading.Barrier(8)
    handles = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            local = [ring.allocate(64)["handle"] for _ in range(8)]
        except Exception as exc:  # pragma: no cover - reported via errors
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            handles.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(handles) == 64
    assert len(set(handles)) == 64
    blocks = sorted((ring.resolve(handle) for handle in handles),
                    key=lambda row: row["offset"])
    for left, right in zip(blocks, blocks[1:]):
        assert left["offset"] + left["size"] <= right["offset"]
    assert all(row["offset"] % 256 == 0 for row in blocks)
    usage = ring.usage()
    assert usage["used"] == 4096
    assert usage["free"] == 12288
    assert usage["blocks"] == 64
    assert ring.stats()["allocations"] == 64


def test_stats_and_reset():
    ring = make_ring()
    ring.begin_frame(1)
    first = ring.allocate(256)["handle"]
    ring.write(first, b"12345678")
    ring.release(first)
    ring.begin_frame(2)
    ring.allocate(128)
    ring.begin_frame(3)
    assert ring.retire_frame(2) == 1

    assert ring.stats() == {
        "allocations": 2,
        "released": 1,
        "retired": 1,
        "bytes_written": 8,
        "wraps": 0,
    }
    ring.reset_stats()
    assert ring.stats() == {
        "allocations": 0,
        "released": 0,
        "retired": 0,
        "bytes_written": 0,
        "wraps": 0,
    }


def test_usage_invariant_after_mixed_operations():
    ring = make_ring()
    ring.begin_frame(1)
    first = ring.allocate(300)
    second = ring.allocate(100)
    assert (first["offset"], second["offset"]) == (0, 512)
    ring.release(first["handle"])
    ring.begin_frame(2)
    third = ring.allocate(1000)
    assert third["offset"] == 768
    assert ring.retire_frame(1) == 1

    usage = ring.usage()
    assert usage["used"] == 1000
    assert usage["free"] == 3096
    assert usage["used"] + usage["free"] == usage["capacity"]
    assert usage["padding"] == 368
    assert usage["blocks"] == 1

    fourth = ring.allocate(100)
    assert fourth["offset"] == 1792
    assert ring.usage()["used"] == 1100
    assert ring.usage()["used"] + ring.usage()["free"] == 4096
