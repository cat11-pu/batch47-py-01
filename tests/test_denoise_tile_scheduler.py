import copy
import threading

import pytest

import denoise_tile_scheduler as tl


def scheduler(**kwargs):
    return tl.DenoiseTileScheduler(**kwargs)


def grid(**kwargs):
    sched = scheduler(**kwargs)
    sched.configure_grid(64, 32)
    return sched


ALL_TILES = ["r0c0", "r0c1", "r0c2", "r0c3",
             "r1c0", "r1c1", "r1c2", "r1c3"]


def test_grid_configuration_ceilings_and_idempotence():
    sched = scheduler(tile_size=16)
    info = sched.configure_grid(64, 32)
    assert info == {"cols": 4, "rows": 2, "tiles": 8, "tile_size": 16}
    assert sched.configure_grid(64, 32) == info
    assert sched.configure_grid(63, 17) == info
    assert sched.configure_grid(32, 16) == {"cols": 2, "rows": 1,
                                            "tiles": 2, "tile_size": 16}
    for bad in (0, -3, 1.5, "64", True):
        with pytest.raises(tl.TileError) as exc:
            sched.configure_grid(bad, 32)
        assert exc.value.code == "TILE_INVALID_GRID"

    for kwargs in ({"tile_size": 0}, {"budget": 0}, {"min_samples": 0},
                   {"variance_threshold": 0.0}, {"history_frames": -1}):
        with pytest.raises(tl.TileError) as exc:
            scheduler(**kwargs)
        assert exc.value.code == "TILE_INVALID_CONFIG"


def test_reconfiguring_to_new_dimensions_drops_tile_state():
    sched = grid()
    sched.mark_tile("r1c3", variance=0.9, samples=1)
    assert sched.pending()["dirty"] == 1
    assert sched.configure_grid(32, 16)["tiles"] == 2
    with pytest.raises(tl.TileError) as exc:
        sched.tile_state("r1c3")
    assert exc.value.code == "TILE_UNKNOWN"
    assert sched.tile_state("r0c1")["samples"] == 0
    assert sched.pending() == {"eligible": 2, "dirty": 0,
                               "history_invalid": 0, "total": 2}


def test_scheduling_before_a_grid_is_configured_is_refused():
    sched = scheduler()
    with pytest.raises(tl.TileError) as exc:
        sched.mark_tile("r0c0", variance=0.1, samples=0)
    assert exc.value.code == "TILE_NO_GRID"
    with pytest.raises(tl.TileError) as exc:
        sched.plan()
    assert exc.value.code == "TILE_NO_GRID"
    with pytest.raises(tl.TileError) as exc:
        sched.add_region(0, 0, 0, 0, variance=0.1, samples=1)
    assert exc.value.code == "TILE_NO_GRID"
    with pytest.raises(tl.TileError) as exc:
        sched.tile_state("r0c0")
    assert exc.value.code == "TILE_NO_GRID"
    assert sched.pending() == {"eligible": 0, "dirty": 0,
                               "history_invalid": 0, "total": 0}


def test_tile_reports_validate_bounds_and_payloads():
    sched = grid()
    with pytest.raises(tl.TileError) as exc:
        sched.mark_tile("r9c0", variance=0.1, samples=0)
    assert exc.value.code == "TILE_UNKNOWN"
    with pytest.raises(tl.TileError) as exc:
        sched.mark_tile("tile-1", variance=0.1, samples=0)
    assert exc.value.code == "TILE_UNKNOWN"
    for bad in (-0.1, float("inf"), float("nan"), "0.4", True):
        with pytest.raises(tl.TileError) as exc:
            sched.mark_tile("r0c0", variance=bad, samples=0)
        assert exc.value.code == "TILE_INVALID_VARIANCE"
    for bad in (-1, 1.5, "3", True):
        with pytest.raises(tl.TileError) as exc:
            sched.mark_tile("r0c0", variance=0.4, samples=bad)
        assert exc.value.code == "TILE_INVALID_SAMPLES"
    with pytest.raises(tl.TileError) as exc:
        sched.mark_tile("r0c0", variance=0.4, samples=2, history_valid="yes")
    assert exc.value.code == "TILE_INVALID_HISTORY"


def test_repeating_an_identical_report_does_not_bump_the_generation():
    sched = grid()
    first = sched.mark_tile("r0c0", variance=0.5, samples=1)
    assert first == {"tile_id": "r0c0", "changed": True, "generation": 1,
                     "dirty": True}
    again = sched.mark_tile("r0c0", variance=0.5, samples=1)
    assert again == {"tile_id": "r0c0", "changed": False, "generation": 1,
                     "dirty": True}
    changed = sched.mark_tile("r0c0", variance=0.6, samples=1)
    assert changed == {"tile_id": "r0c0", "changed": True, "generation": 2,
                       "dirty": True}


def test_pending_counts_eligibility_sources():
    sched = grid()
    assert sched.pending() == {"eligible": 8, "dirty": 0,
                               "history_invalid": 0, "total": 8}
    sched.mark_tile("r0c0", variance=0.1, samples=10)
    sched.mark_tile("r0c1", variance=0.9, samples=10)
    sched.mark_tile("r0c2", variance=0.1, samples=10, history_valid=False)
    assert sched.pending() == {"eligible": 7, "dirty": 3,
                               "history_invalid": 1, "total": 8}


def test_plan_orders_by_variance_then_samples_then_id():
    sched = scheduler(budget=3)
    sched.configure_grid(64, 32)
    sched.mark_tile("r0c1", variance=0.9, samples=1)
    sched.mark_tile("r0c2", variance=0.5, samples=1)
    sched.mark_tile("r1c3", variance=0.5, samples=0)
    plan = sched.plan()
    assert plan["frame"] == 1
    assert plan["budget"] == 3
    assert plan["selected"] == ["r0c1", "r1c3", "r0c2"]
    assert plan["deferred"] == ["r0c0", "r0c3", "r1c0", "r1c1", "r1c2"]
    assert plan["dispatching"] == 3
    assert plan["history_reset"] is False
    assert plan["reason"] is None
    assert plan["groups"] == [
        {"group": 0, "row": 0, "tiles": ["r0c1"], "count": 1},
        {"group": 1, "row": 1, "tiles": ["r1c3"], "count": 1},
        {"group": 2, "row": 0, "tiles": ["r0c2"], "count": 1},
    ]


def test_horizontal_runs_merge_only_when_consecutive_in_dispatch_order():
    sched = scheduler(budget=4)
    sched.configure_grid(64, 32)
    for col, variance in enumerate((0.4, 0.3, 0.2, 0.1)):
        sched.mark_tile(f"r0c{col}", variance=variance, samples=2)
    plan = sched.plan()
    assert plan["selected"] == ["r0c0", "r0c1", "r0c2", "r0c3"]
    assert plan["groups"] == [
        {"group": 0, "row": 0,
         "tiles": ["r0c0", "r0c1", "r0c2", "r0c3"], "count": 4},
    ]

    other = scheduler(budget=3)
    other.configure_grid(64, 32)
    other.mark_tile("r0c0", variance=0.5, samples=2)
    other.mark_tile("r0c2", variance=0.4, samples=2)
    other.mark_tile("r0c3", variance=0.3, samples=2)
    plan = other.plan()
    assert plan["selected"] == ["r0c0", "r0c2", "r0c3"]
    assert plan["groups"] == [
        {"group": 0, "row": 0, "tiles": ["r0c0"], "count": 1},
        {"group": 1, "row": 0, "tiles": ["r0c2", "r0c3"], "count": 2},
    ]


def test_plan_clears_dirty_flags_only_for_dispatched_tiles():
    sched = scheduler(budget=1)
    sched.configure_grid(64, 32)
    sched.mark_tile("r0c0", variance=0.9, samples=1)
    sched.mark_tile("r0c1", variance=0.8, samples=1)
    plan = sched.plan()
    assert plan["selected"] == ["r0c0"]
    assert plan["deferred"][0] == "r0c1"

    state = sched.tile_state("r0c0")
    assert state["samples"] == 2
    assert state["dirty"] is False
    assert state["history_valid"] is True
    assert state["generation"] == 2
    assert state["last_frame"] == 1

    deferred = sched.tile_state("r0c1")
    assert deferred["samples"] == 1
    assert deferred["dirty"] is True
    assert deferred["generation"] == 1


def test_tiles_stop_being_eligible_once_samples_converge():
    sched = scheduler(budget=8, min_samples=2)
    sched.configure_grid(32, 16)
    sched.mark_tile("r0c0", variance=0.1, samples=0)
    sched.mark_tile("r0c1", variance=0.9, samples=0)
    first = sched.plan()
    assert first["dispatching"] == 2
    second = sched.plan()
    assert second["dispatching"] == 2
    assert sched.pending()["eligible"] == 1
    third = sched.plan()
    assert third["selected"] == ["r0c1"]
    fourth = sched.plan()
    assert fourth["selected"] == ["r0c1"]
    assert sched.stats()["dispatched"] == 6
    assert sched.stats()["plans"] == 4


def test_history_invalidation_forces_a_full_pass_once():
    sched = grid()
    sched.mark_tile("r0c0", variance=0.1, samples=10)
    sched.mark_tile("r0c1", variance=0.1, samples=10, history_valid=False)
    assert sched.invalidate_history("camera_cut") == 7
    assert sched.invalidate_history() == 0
    assert sched.pending()["history_invalid"] == 8
    plan = sched.plan()
    assert plan["history_reset"] is True
    assert plan["reason"] == "camera_cut"
    next_plan = sched.plan()
    assert next_plan["history_reset"] is False
    assert next_plan["reason"] is None
    with pytest.raises(tl.TileError) as exc:
        sched.invalidate_history("")
    assert exc.value.code == "TILE_INVALID_REASON"


def test_region_marks_inclusive_rectangle_and_rejects_bad_bounds():
    sched = grid()
    ids = sched.add_region(1, 0, 2, 1, variance=0.7, samples=0)
    assert ids == ["r0c1", "r0c2", "r1c1", "r1c2"]
    state = sched.tile_state("r0c1")
    assert state["variance"] == 0.7
    assert state["history_valid"] is False
    assert sched.pending()["dirty"] == 4
    for bounds in ((3, 0, 1, 1), (0, 1, 1, 0), (0, 0, 4, 0),
                   (-1, 0, 1, 1), (0, 0, 1, 2)):
        with pytest.raises(tl.TileError) as exc:
            sched.add_region(*bounds, variance=0.2, samples=1)
        assert exc.value.code == "TILE_INVALID_REGION"


def test_budget_limits_dispatch_and_stats_are_exact():
    sched = scheduler(budget=2)
    sched.configure_grid(64, 32)
    for col, variance in enumerate((0.9, 0.8, 0.7)):
        sched.mark_tile(f"r0c{col}", variance=variance, samples=10)
    plan = sched.plan()
    assert plan["dispatching"] == 2
    assert plan["selected"] == ["r0c0", "r0c1"]
    assert plan["deferred"][0] == "r0c2"
    assert len(plan["deferred"]) == 6
    assert len(plan["groups"]) == 1
    assert sched.stats() == {"plans": 1, "dispatched": 2, "groups": 1,
                             "resets": 0, "deferred": 6}
    sched.reset_stats()
    assert sched.stats() == {"plans": 0, "dispatched": 0, "groups": 0,
                             "resets": 0, "deferred": 0}


def test_snapshot_restore_continues_frame_and_pending_reset():
    sched = scheduler(budget=1)
    sched.configure_grid(64, 32)
    sched.mark_tile("r0c0", variance=0.9, samples=1)
    sched.mark_tile("r0c1", variance=0.5, samples=1)
    first = sched.plan()
    assert first["selected"] == ["r0c0"]
    assert first["deferred"][0] == "r0c1"
    sched.invalidate_history("lights_moved")
    state = sched.snapshot()

    clone = scheduler()
    clone.restore(state)
    assert clone.tile_state("r0c0") == sched.tile_state("r0c0")
    assert clone.pending() == sched.pending()
    assert clone.stats() == sched.stats()
    continued = clone.plan()
    assert continued["frame"] == first["frame"] + 1
    assert continued["history_reset"] is True
    assert continued["reason"] == "lights_moved"
    assert sched.plan()["frame"] == first["frame"] + 1

    state["tiles"].clear()
    assert clone.tile_state("r0c1")["variance"] == 0.5

    good = clone.snapshot()
    for mutate in (
        lambda s: s.pop("tiles"),
        lambda s: s["tiles"][0].update({"variance": -1.0}),
        lambda s: s["tiles"][0].update({"tile_id": "r9c9"}),
        lambda s: s["stats"].update({"plans": -1}),
        lambda s: s["options"].update({"budget": 0}),
        lambda s: s.update({"frame": -1}),
        lambda s: s["grid"].update({"cols": 3}),
    ):
        broken = copy.deepcopy(good)
        mutate(broken)
        with pytest.raises(tl.TileError) as exc:
            clone.restore(broken)
        assert exc.value.code == "TILE_SNAPSHOT_INVALID"
    assert clone.pending()["total"] == 8


def test_concurrent_marks_are_serialized_and_reset_clears_the_grid():
    sched = scheduler(budget=8)
    sched.configure_grid(64, 32)
    barrier = threading.Barrier(8)
    marked = []
    lock = threading.Lock()

    def worker(index):
        row, col = divmod(index, 4)
        barrier.wait()
        row_result = sched.mark_tile(f"r{row}c{col}", variance=0.5,
                                     samples=1)
        with lock:
            marked.append(row_result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(marked) == 8
    assert all(row["changed"] for row in marked)
    assert sched.pending()["dirty"] == 8
    plan = sched.plan()
    assert plan["dispatching"] == 8
    assert sorted(plan["selected"]) == sorted(ALL_TILES)
    assert sched.stats()["groups"] == 2

    barrier = threading.Barrier(8)
    same = []

    def twin():
        barrier.wait()
        row_result = sched.mark_tile("r0c0", variance=0.9, samples=1)
        with lock:
            same.append(row_result)

    threads = [threading.Thread(target=twin) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(same) == 8
    assert sum(1 for row in same if row["changed"]) == 1
    assert all(row["generation"] == 3 for row in same)

    sched.reset_stats()
    sched.reset()
    assert sched.stats() == {"plans": 0, "dispatched": 0, "groups": 0,
                             "resets": 0, "deferred": 0}
    with pytest.raises(tl.TileError) as exc:
        sched.plan()
    assert exc.value.code == "TILE_NO_GRID"
