import copy
import threading

import pytest

import depth_of_field_guard as sg


def make(**kwargs):
    params = dict(
        width=32,
        height=32,
        tile_size=16,
        focus_distance=10.0,
        aperture=2.0,
        coc_scale=10.0,
        max_coc_px=24.0,
        focus_speed=None,
        sample_count=8,
        budget_units=200000,
    )
    params.update(kwargs)
    return sg.DepthOfField(**params)


def test_invalid_constructor_parameters():
    bad_params = [
        {"width": 0},
        {"height": -1},
        {"tile_size": 0},
        {"focus_distance": 0.0},
        {"aperture": 0.0},
        {"coc_scale": 0.0},
        {"max_coc_px": 0.0},
        {"focus_speed": 0.0},
        {"sample_count": 0},
        {"budget_units": 0},
    ]
    for override in bad_params:
        with pytest.raises(sg.DofError) as exc:
            make(**override)
        assert exc.value.code == "DOF_INVALID_CONFIG"


def test_compute_coc_values():
    dof = make()
    assert dof.compute_coc(10.0) == pytest.approx(0.0)
    assert dof.compute_coc(20.0) == pytest.approx(10.0)
    assert dof.compute_coc(5.0) == pytest.approx(20.0)
    assert dof.compute_coc(100.0) == pytest.approx(18.0)
    assert dof.compute_coc(0.5) == pytest.approx(24.0)


def test_compute_coc_rejects_invalid_depth():
    dof = make()
    for bad in (0.0, -2.0, "10.0"):
        with pytest.raises(sg.DofError) as exc:
            dof.compute_coc(bad)
        assert exc.value.code == "DOF_INVALID_DEPTH"


def test_linearize_depth_matches_projection():
    dof = make()
    assert dof.linearize_depth(0.0) == pytest.approx(0.1)
    assert dof.linearize_depth(0.5) == pytest.approx(50.0 / 250.05)
    with pytest.raises(sg.DofError) as exc:
        dof.linearize_depth(1.0)
    assert exc.value.code == "DOF_INVALID_DEPTH"
    with pytest.raises(sg.DofError) as exc:
        dof.linearize_depth(-0.2)
    assert exc.value.code == "DOF_INVALID_DEPTH"
    with pytest.raises(sg.DofError) as exc:
        dof.linearize_depth(0.5, near=10.0, far=5.0)
    assert exc.value.code == "DOF_INVALID_RANGE"


def test_set_focus_instant_and_animated():
    instant = make()
    result = instant.set_focus(20.0)
    assert result["focus"] == 20.0
    assert result["target"] == 20.0
    assert result["moving"] is False
    assert result["speed"] is None

    smooth = make(focus_speed=2.0)
    result = smooth.set_focus(20.0)
    assert result["focus"] == 10.0
    assert result["target"] == 20.0
    assert result["moving"] is True
    assert result["speed"] == 2.0
    result = smooth.set_focus(30.0, speed=4.0)
    assert result["target"] == 30.0
    assert result["speed"] == 4.0

    with pytest.raises(sg.DofError) as exc:
        smooth.set_focus(0.0)
    assert exc.value.code == "DOF_INVALID_FOCUS"
    with pytest.raises(sg.DofError) as exc:
        smooth.set_focus(5.0, speed=0.0)
    assert exc.value.code == "DOF_INVALID_SPEED"


def test_update_focus_steps_without_overshoot():
    dof = make(focus_speed=2.0)
    dof.set_focus(30.0)
    assert dof.update_focus(0.5) == pytest.approx(11.0)
    assert dof.update_focus(100.0) == pytest.approx(30.0)
    assert dof.update_focus(1.0) == pytest.approx(30.0)
    assert dof.stats()["updates"] == 3
    assert dof.stats()["moves"] == 2

    with pytest.raises(sg.DofError) as exc:
        dof.update_focus(-0.5)
    assert exc.value.code == "DOF_INVALID_DT"
    with pytest.raises(sg.DofError) as exc:
        dof.update_focus("fast")
    assert exc.value.code == "DOF_INVALID_DT"


def test_aperture_changes_coc():
    dof = make()
    assert dof.compute_coc(20.0) == pytest.approx(10.0)
    dof.set_aperture(4.0)
    assert dof.compute_coc(20.0) == pytest.approx(20.0)
    with pytest.raises(sg.DofError) as exc:
        dof.set_aperture(0.0)
    assert exc.value.code == "DOF_INVALID_APERTURE"


def test_tile_depth_grid_validation():
    dof = make()
    summary = dof.set_tile_depths([10.0, 10.0, 20.0, 5.0])
    assert summary == {"tiles_x": 2, "tiles_y": 2, "tiles": 4,
                       "min_depth": 5.0, "max_depth": 20.0}
    with pytest.raises(sg.DofError) as exc:
        dof.set_tile_depths([10.0, 10.0, 20.0])
    assert exc.value.code == "DOF_GRID_MISMATCH"
    with pytest.raises(sg.DofError) as exc:
        dof.set_tile_depths([10.0, 10.0, 20.0, 5.0, 8.0])
    assert exc.value.code == "DOF_GRID_MISMATCH"
    with pytest.raises(sg.DofError) as exc:
        dof.set_tile_depths([10.0, 0.0, 20.0, 5.0])
    assert exc.value.code == "DOF_INVALID_DEPTH"


def test_plan_tiles_classifies_layers():
    dof = make()
    dof.set_tile_depths([10.0, 10.0, 20.0, 5.0])
    rows = dof.plan_tiles()
    assert [row["index"] for row in rows] == [0, 1, 2, 3]
    assert [row["layer"] for row in rows] == ["in_focus", "in_focus", "far", "near"]
    assert rows[0]["depth"] == 10.0
    assert rows[2]["coc"] == pytest.approx(10.0)
    assert rows[3]["coc"] == pytest.approx(20.0)


def test_plan_requires_tile_depths():
    dof = make()
    with pytest.raises(sg.DofError) as exc:
        dof.plan_tiles()
    assert exc.value.code == "DOF_NO_DEPTHS"
    with pytest.raises(sg.DofError) as exc:
        dof.plan()
    assert exc.value.code == "DOF_NO_DEPTHS"


def test_bokeh_kernel_ring_and_center():
    dof = make()
    kernel = dof.build_bokeh_kernel(8.0, samples=4)
    assert len(kernel) == 4
    assert [row["dx"] for row in kernel] == pytest.approx([4.0, 0.0, -4.0, 0.0], abs=1e-6)
    assert [row["dy"] for row in kernel] == pytest.approx([0.0, 4.0, 0.0, -4.0], abs=1e-6)
    assert [row["weight"] for row in kernel] == pytest.approx([0.25] * 4)
    assert sum(row["weight"] for row in kernel) == pytest.approx(1.0)

    center = dof.build_bokeh_kernel(0.0)
    assert center == [{"dx": 0.0, "dy": 0.0, "weight": 1.0}]
    assert dof.stats()["kernels"] == 2

    with pytest.raises(sg.DofError) as exc:
        dof.build_bokeh_kernel(-1.0)
    assert exc.value.code == "DOF_INVALID_COC"
    with pytest.raises(sg.DofError) as exc:
        dof.build_bokeh_kernel(4.0, samples=0)
    assert exc.value.code == "DOF_INVALID_SAMPLES"


def test_plan_reports_work_units():
    dof = make()
    dof.set_tile_depths([10.0, 10.0, 20.0, 5.0])
    plan = dof.plan()
    assert plan["tiles"] == 4
    assert plan["near"] == 1
    assert plan["far"] == 1
    assert plan["in_focus"] == 2
    assert plan["bokeh_tiles"] == 2
    assert plan["samples"] == 16
    assert plan["work_units"] == 56
    assert plan["focus"] == 10.0
    assert dof.stats()["plans"] == 1


def test_plan_budget_rejection_keeps_state():
    dof = make(budget_units=55)
    dof.set_tile_depths([10.0, 10.0, 20.0, 5.0])
    before = dof.snapshot()
    with pytest.raises(sg.DofError) as exc:
        dof.plan()
    assert exc.value.code == "DOF_BUDGET_EXCEEDED"
    assert dof.stats()["plans"] == 0
    assert dof.snapshot() == before

    plan = dof.plan(budget_units=56)
    assert plan["work_units"] == 56
    assert dof.stats()["plans"] == 1


def test_snapshot_restore_and_corrupt_state():
    dof = make(focus_speed=2.0)
    dof.set_focus(30.0)
    dof.update_focus(0.5)
    dof.set_aperture(4.0)
    dof.set_tile_depths([10.0, 10.0, 20.0, 5.0])
    dof.plan()
    state = dof.snapshot()

    dof.update_focus(10.0)
    restored = make()
    restored.restore(state)
    assert restored.stats() == {"updates": 1, "moves": 1, "grids": 1,
                                "kernels": 0, "plans": 1}
    assert restored.compute_coc(20.0) == pytest.approx(18.0)
    rows = restored.plan_tiles()
    assert [row["layer"] for row in rows] == ["near", "near", "far", "near"]
    assert restored.update_focus(0.0) == pytest.approx(11.0)

    before = restored.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "focus": 11.0, "target": 30.0, "speed": 2.0,
         "aperture": 4.0, "tile_depths": [10.0, 10.0, 20.0, 5.0],
         "stats": dict(before["stats"]), "last_plan": None},
        {"version": 1, "focus": 0.0, "target": 30.0, "speed": 2.0,
         "aperture": 4.0, "tile_depths": [10.0, 10.0, 20.0, 5.0],
         "stats": dict(before["stats"]), "last_plan": None},
        {"version": 1, "focus": 11.0, "target": 30.0, "speed": 2.0,
         "aperture": 4.0, "tile_depths": [10.0, 10.0, 20.0],
         "stats": dict(before["stats"]), "last_plan": None},
        {"version": 1, "focus": 11.0, "target": 30.0, "speed": 2.0,
         "aperture": 4.0, "tile_depths": [10.0, 10.0, 20.0, 5.0],
         "stats": {"updates": 1}, "last_plan": None},
    ]
    for bad in copy.deepcopy(bad_states):
        with pytest.raises(sg.DofError) as exc:
            restored.restore(bad)
        assert exc.value.code == "DOF_SNAPSHOT_INVALID"
    assert restored.snapshot() == before


def test_concurrent_focus_updates_are_serialized():
    dof = make(focus_speed=10.0)
    dof.set_focus(100.0)
    barrier = threading.Barrier(8)
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            value = dof.update_focus(0.1)
        except Exception as exc:  # pragma: no cover - surfaced below
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(results) == pytest.approx([11.0, 12.0, 13.0, 14.0,
                                             15.0, 16.0, 17.0, 18.0])
    assert dof.stats()["updates"] == 8
    assert dof.stats()["moves"] == 8

    dof.reset_stats()
    assert dof.stats() == {"updates": 0, "moves": 0, "grids": 0,
                           "kernels": 0, "plans": 0}
    assert dof.update_focus(0.0) == pytest.approx(18.0)
    assert dof.stats()["updates"] == 1
