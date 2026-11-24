import copy
import math
import threading

import pytest

import motion_blur_guard as mb


def make(**kwargs):
    params = dict(
        width=64,
        height=32,
        tile_size=16,
        max_blur_px=32.0,
        sample_count=3,
        cut_threshold=96.0,
        teleport_scale=4.0,
        budget_units=200000,
    )
    params.update(kwargs)
    return mb.MotionBlur(**params)


def test_begin_frame_enforces_monotonic_ids():
    blur = make()
    with pytest.raises(mb.MotionBlurError) as exc:
        blur.begin_frame(frame_id=-1)
    assert exc.value.code == "MB_INVALID_FRAME"

    first = blur.begin_frame(frame_id=4)
    assert first["new"] is True
    assert first["duplicate"] is False
    assert first["objects"] == 0

    same = blur.begin_frame(frame_id=4)
    assert same["new"] is False
    assert same["duplicate"] is True

    with pytest.raises(mb.MotionBlurError) as exc:
        blur.begin_frame(frame_id=2)
    assert exc.value.code == "MB_FRAME_REGRESSION"
    assert blur.stats()["frames"] == 1


def test_camera_cut_and_forced_reset_clear_frame():
    blur = make()
    blur.begin_frame(frame_id=1)
    blur.submit_object("a", screen_delta=(4.0, 0.0), depth=5.0,
                       screen_rect=(0, 0, 16, 16))
    blur.submit_object("b", screen_delta=(0.0, 4.0), depth=5.0,
                       screen_rect=(16, 0, 16, 16))

    cut = blur.begin_frame(frame_id=2, camera_delta=(200.0, 0.0))
    assert cut["cut"] is True
    assert cut["objects"] == 0
    assert blur.stats()["cuts"] == 1

    still = blur.begin_frame(frame_id=3, camera_delta=(0.0, 0.0))
    assert still["cut"] is False
    forced = blur.begin_frame(frame_id=4, reset=True)
    assert forced["cut"] is True
    assert blur.stats()["cuts"] == 2
    assert blur.stats()["frames"] == 4


def test_submit_validates_frame_rect_depth_and_vector():
    blur = make()
    with pytest.raises(mb.MotionBlurError) as exc:
        blur.submit_object("a", screen_delta=(1.0, 0.0), depth=1.0,
                           screen_rect=(0, 0, 8, 8))
    assert exc.value.code == "MB_NO_FRAME"

    blur.begin_frame(frame_id=1)
    cases = [
        ("MB_INVALID_OBJECT", "", (1.0, 0.0), 1.0, (0, 0, 8, 8)),
        ("MB_INVALID_RECT", "a", (1.0, 0.0), 1.0, (60, 0, 16, 16)),
        ("MB_INVALID_RECT", "a", (1.0, 0.0), 1.0, (0, 0, 0, 8)),
        ("MB_INVALID_DEPTH", "a", (1.0, 0.0), 0.0, (0, 0, 8, 8)),
        ("MB_INVALID_VECTOR", "a", (1.0, 0.0, 0.0), 1.0, (0, 0, 8, 8)),
    ]
    for expected, object_id, delta, depth, rect in cases:
        with pytest.raises(mb.MotionBlurError) as exc:
            blur.submit_object(object_id, screen_delta=delta, depth=depth,
                               screen_rect=rect)
        assert exc.value.code == expected
    assert blur.stats()["objects"] == 0


def test_teleport_velocity_is_clamped_to_zero():
    blur = make()
    blur.begin_frame(frame_id=1)
    row = blur.submit_object("jump", screen_delta=(200.0, 0.0), depth=4.0,
                             screen_rect=(0, 0, 16, 16))
    assert row["teleport"] is True
    assert row["velocity"] == (0.0, 0.0)
    assert row["pixels"] == 0.0
    assert blur.stats()["teleports"] == 1
    assert blur.classify_tiles()["active_tiles"] == 0
    assert blur.classify_tiles()["max_tile"] is None

    edge = blur.submit_object("edge", screen_delta=(128.0, 0.0), depth=4.0,
                              screen_rect=(16, 0, 16, 16))
    assert edge["teleport"] is False
    assert edge["pixels"] == pytest.approx(32.0)
    assert blur.stats()["teleports"] == 1


def test_clamp_vector_respects_strength_limit():
    blur = make()
    assert blur.clamp_vector((30.0, 40.0)) == pytest.approx((19.2, 25.6))
    blur.set_strength(0.5)
    assert blur.clamp_vector((30.0, 40.0)) == pytest.approx((9.6, 12.8))
    assert blur.clamp_vector((3.0, 4.0)) == pytest.approx((3.0, 4.0))
    with pytest.raises(mb.MotionBlurError) as exc:
        blur.clamp_vector((1.0, 2.0, 3.0))
    assert exc.value.code == "MB_INVALID_VECTOR"


def test_strength_range_and_submission_scaling():
    blur = make()
    for bad in (-0.1, 1.01, "x", None):
        with pytest.raises(mb.MotionBlurError) as exc:
            blur.set_strength(bad)
        assert exc.value.code == "MB_STRENGTH_RANGE"

    blur.set_strength(0.5)
    blur.begin_frame(frame_id=1)
    row = blur.submit_object("scaled", screen_delta=(10.0, -4.0), depth=2.0,
                             screen_rect=(0, 0, 8, 8))
    assert row["velocity"] == pytest.approx((5.0, -2.0))
    assert row["pixels"] == pytest.approx(math.hypot(5.0, 2.0))


def test_duplicate_object_updates_in_place():
    blur = make()
    blur.begin_frame(frame_id=1)
    first = blur.submit_object("dup", screen_delta=(8.0, 0.0), depth=3.0,
                               screen_rect=(0, 0, 16, 16))
    second = blur.submit_object("dup", screen_delta=(16.0, 0.0), depth=3.0,
                                screen_rect=(0, 0, 16, 16))
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["velocity"] == pytest.approx((16.0, 0.0))
    assert blur.stats()["objects"] == 1
    assert blur.classify_tiles()["max_px"] == pytest.approx(16.0)


def test_classify_tiles_counts_coverage():
    blur = make()
    blur.begin_frame(frame_id=1)
    blur.submit_object("wide", screen_delta=(8.0, 0.0), depth=5.0,
                       screen_rect=(0, 0, 32, 16))
    result = blur.classify_tiles()
    assert result["tiles_x"] == 4
    assert result["tiles_y"] == 2
    assert result["total_tiles"] == 8
    assert result["active_tiles"] == 2
    assert result["max_px"] == pytest.approx(8.0)
    assert result["max_tile"] == [0, 0]


def test_classify_tiles_scan_order_is_stable():
    blur = make()
    blur.begin_frame(frame_id=1)
    blur.submit_object("z", screen_delta=(10.0, 0.0), depth=5.0,
                       screen_rect=(32, 16, 16, 16))
    blur.submit_object("a", screen_delta=(10.0, 0.0), depth=5.0,
                       screen_rect=(0, 16, 16, 16))
    result = blur.classify_tiles()
    assert result["active_tiles"] == 2
    assert result["max_px"] == pytest.approx(10.0)
    assert result["max_tile"] == [0, 1]


def test_kernel_spans_clamped_velocity():
    blur = make()
    blur.begin_frame(frame_id=1)
    blur.submit_object("k", screen_delta=(10.0, 0.0), depth=1.0,
                       screen_rect=(0, 0, 16, 16))
    kernel = blur.build_kernel(0, 0)
    assert len(kernel) == 3
    assert [row["dx"] for row in kernel] == pytest.approx([-5.0, 0.0, 5.0])
    assert [row["dy"] for row in kernel] == pytest.approx([0.0, 0.0, 0.0])
    assert [row["weight"] for row in kernel] == pytest.approx([1 / 3] * 3)
    assert sum(row["weight"] for row in kernel) == pytest.approx(1.0)
    assert blur.stats()["kernels"] == 1

    with pytest.raises(mb.MotionBlurError) as exc:
        blur.build_kernel(4, 0)
    assert exc.value.code == "MB_INVALID_TILE"
    with pytest.raises(mb.MotionBlurError) as exc:
        blur.build_kernel(0, 2)
    assert exc.value.code == "MB_INVALID_TILE"


def test_kernel_for_inactive_tile_is_identity():
    blur = make()
    blur.begin_frame(frame_id=1)
    assert blur.build_kernel(0, 0) == [{"dx": 0.0, "dy": 0.0, "weight": 1.0}]

    blur.submit_object("jump", screen_delta=(1000.0, 0.0), depth=2.0,
                       screen_rect=(0, 0, 16, 16))
    assert blur.build_kernel(0, 0) == [{"dx": 0.0, "dy": 0.0, "weight": 1.0}]


def test_encode_work_units_and_stats_reset():
    blur = make()
    blur.begin_frame(frame_id=1)
    blur.submit_object("a", screen_delta=(8.0, 0.0), depth=5.0,
                       screen_rect=(0, 0, 16, 16))
    blur.submit_object("b", screen_delta=(8.0, 0.0), depth=5.0,
                       screen_rect=(16, 0, 16, 16))
    plan = blur.encode()
    assert plan["frame_id"] == 1
    assert plan["tiles"] == 2
    assert plan["objects"] == 2
    assert plan["samples"] == 6
    assert plan["work_units"] == 26
    assert plan["cut"] is False
    assert blur.stats()["encodes"] == 1

    blur.reset_stats()
    assert blur.stats() == {"frames": 0, "objects": 0, "teleports": 0,
                            "cuts": 0, "kernels": 0, "encodes": 0}
    assert blur.classify_tiles()["active_tiles"] == 2


def test_encode_budget_rejection_keeps_state():
    blur = make(budget_units=25)
    blur.begin_frame(frame_id=1)
    blur.submit_object("a", screen_delta=(8.0, 0.0), depth=5.0,
                       screen_rect=(0, 0, 16, 16))
    blur.submit_object("b", screen_delta=(8.0, 0.0), depth=5.0,
                       screen_rect=(16, 0, 16, 16))
    with pytest.raises(mb.MotionBlurError) as exc:
        blur.encode()
    assert exc.value.code == "MB_BUDGET_EXCEEDED"
    assert blur.stats()["encodes"] == 0

    plan = blur.encode(budget_units=26)
    assert plan["work_units"] == 26
    assert blur.stats()["encodes"] == 1


def test_snapshot_restore_and_corrupt_state():
    blur = make()
    blur.begin_frame(frame_id=1)
    blur.submit_object("a", screen_delta=(8.0, 0.0), depth=5.0,
                       screen_rect=(0, 0, 16, 16))
    blur.encode()
    state = blur.snapshot()
    assert state["version"] == 1
    assert state["frame_id"] == 1

    blur.begin_frame(frame_id=2)
    blur.submit_object("b", screen_delta=(4.0, 0.0), depth=5.0,
                       screen_rect=(0, 0, 8, 8))
    assert blur.stats()["frames"] == 2

    restored = make()
    restored.restore(state)
    assert restored.stats() == {"frames": 1, "objects": 1, "teleports": 0,
                                "cuts": 0, "kernels": 0, "encodes": 1}
    assert restored.classify_tiles()["active_tiles"] == 1
    duplicate = restored.submit_object("a", screen_delta=(1.0, 0.0), depth=5.0,
                                       screen_rect=(0, 0, 8, 8))
    assert duplicate["duplicate"] is True
    assert restored.stats()["objects"] == 1
    assert restored.begin_frame(frame_id=1)["duplicate"] is True

    before = restored.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "frame_id": 1, "strength": 1.0, "last_cut": False,
         "objects": {}, "stats": dict(before["stats"])},
        {"version": 1, "frame_id": 1, "strength": 1.5, "last_cut": False,
         "objects": {}, "stats": dict(before["stats"])},
        {"version": 1, "frame_id": 1, "strength": 1.0, "last_cut": False,
         "objects": [], "stats": dict(before["stats"])},
        {"version": 1, "frame_id": 1, "strength": 1.0, "last_cut": False,
         "objects": {}, "stats": {"frames": 1}},
        {"version": 1, "frame_id": "one", "strength": 1.0, "last_cut": False,
         "objects": {}, "stats": dict(before["stats"])},
    ]
    for bad in copy.deepcopy(bad_states):
        with pytest.raises(mb.MotionBlurError) as exc:
            restored.restore(bad)
        assert exc.value.code == "MB_SNAPSHOT_INVALID"
    assert restored.snapshot() == before


def test_concurrent_begin_frame_admits_one_frame():
    blur = make()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = blur.begin_frame(frame_id=7)
        except Exception as exc:  # pragma: no cover - surfaced below
            out = {"new": "error", "code": repr(exc)}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for row in results if row.get("new") is True) == 1
    assert sum(1 for row in results if row.get("duplicate") is True) == 7
    assert blur.stats()["frames"] == 1
