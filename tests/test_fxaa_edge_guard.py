import copy
import threading

import pytest

import fxaa_edge_guard as sg

FLAT = [[0.5] * 5 for _ in range(5)]
VERTICAL = [[0.2, 0.2, 0.8, 0.8, 0.8] for _ in range(5)]
HORIZONTAL = [[0.2] * 5 for _ in range(3)] + [[0.8] * 5 for _ in range(2)]
CONTRAST = [
    [0.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.2, 0.0, 0.0],
    [0.0, 0.9, 0.5, 0.3, 0.0],
    [0.0, 0.0, 0.8, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 0.0],
]


def make(**kwargs):
    params = dict(
        width=5,
        height=5,
        threshold=0.1,
        relative_threshold=0.15,
        subpixel_quality=0.75,
        max_span=8,
        budget_units=120000,
    )
    params.update(kwargs)
    return sg.FxaaEdge(**params)


def test_invalid_constructor_parameters():
    for override in ({"width": 2}, {"height": 2}, {"threshold": 0.0},
                     {"threshold": 1.0}, {"relative_threshold": 0.0},
                     {"relative_threshold": 1.5}, {"subpixel_quality": -0.1},
                     {"subpixel_quality": 1.5}, {"max_span": 0},
                     {"budget_units": 0}):
        with pytest.raises(sg.FxaaError) as exc:
            make(**override)
        assert exc.value.code == "FX_INVALID_CONFIG"


def test_luma_weights_and_validation():
    fx = make()
    assert fx.luma((1.0, 0.0, 0.0)) == pytest.approx(0.299)
    assert fx.luma((0.0, 1.0, 0.0)) == pytest.approx(0.587)
    assert fx.luma((0.0, 0.0, 1.0)) == pytest.approx(0.114)
    assert fx.luma((1.0, 1.0, 1.0)) == pytest.approx(1.0)

    for bad in ((1.0, 0.0), (1.0, 0.0, 0.0, 0.0), (0.0, 0.0, -0.1),
                (0.0, 0.0, 1.1), "abc"):
        with pytest.raises(sg.FxaaError) as exc:
            fx.luma(bad)
        assert exc.value.code == "FX_INVALID_COLOR"


def test_edge_contrast_uses_four_neighbours():
    fx = make()
    assert fx.edge_contrast(CONTRAST, 2, 2) == pytest.approx(0.7)
    assert fx.edge_contrast(FLAT, 0, 0) == 0.0
    assert fx.edge_contrast(VERTICAL, 1, 2) == pytest.approx(0.6)


def test_grid_and_coordinate_validation():
    fx = make()
    with pytest.raises(sg.FxaaError) as exc:
        fx.detect_edges(FLAT[:4])
    assert exc.value.code == "FX_INVALID_GRID"
    with pytest.raises(sg.FxaaError) as exc:
        fx.detect_edges([row + [0.0] for row in FLAT])
    assert exc.value.code == "FX_INVALID_GRID"
    out_of_range = [row[:] for row in FLAT]
    out_of_range[2][2] = 1.5
    with pytest.raises(sg.FxaaError) as exc:
        fx.detect_edges(out_of_range)
    assert exc.value.code == "FX_INVALID_GRID"

    with pytest.raises(sg.FxaaError) as exc:
        fx.edge_contrast(FLAT, 5, 0)
    assert exc.value.code == "FX_INVALID_COORD"
    with pytest.raises(sg.FxaaError) as exc:
        fx.edge_contrast(FLAT, 0, -1)
    assert exc.value.code == "FX_INVALID_COORD"


def test_flat_grid_has_no_edges():
    fx = make()
    assert fx.edge_class(FLAT, 2, 2) == "flat"
    assert fx.edge_class(FLAT, 0, 0) == "flat"
    assert fx.blend_factor(FLAT, 2, 2) == 0.0
    result = fx.detect_edges(FLAT)
    assert result == {"width": 5, "height": 5, "flat": 25,
                      "horizontal": 0, "vertical": 0, "edges": 0,
                      "max_blend": 0.0}


def test_edge_orientation_classification():
    fx = make()
    assert fx.edge_class(VERTICAL, 1, 2) == "vertical"
    assert fx.edge_class(VERTICAL, 2, 2) == "vertical"
    assert fx.edge_class(HORIZONTAL, 2, 2) == "horizontal"
    assert fx.edge_class(HORIZONTAL, 2, 3) == "horizontal"


def test_blend_factor_scales_contrast():
    fx = make()
    assert fx.blend_factor(VERTICAL, 1, 2) == pytest.approx(0.75)
    assert fx.blend_factor(CONTRAST, 2, 2) == pytest.approx(0.75)

    low = [row[:] for row in FLAT]
    low[1][2] = 0.62
    assert fx.edge_class(low, 2, 2) == "horizontal"
    assert fx.blend_factor(low, 2, 2) == pytest.approx(0.1)


def test_detect_edges_counts_vertical_split():
    fx = make()
    result = fx.detect_edges(VERTICAL)
    assert result["flat"] == 15
    assert result["horizontal"] == 0
    assert result["vertical"] == 10
    assert result["edges"] == 10
    assert result["max_blend"] == pytest.approx(0.75)
    assert fx.stats()["detections"] == 1


def test_resolve_flat_grid_is_identity_copy():
    fx = make()
    output = fx.resolve(FLAT)
    assert output == FLAT
    assert output is not FLAT
    assert output[0] is not FLAT[0]
    assert fx.stats()["resolves"] == 1


def test_resolve_blends_edge_pixels_only():
    fx = make()
    source = [row[:] for row in VERTICAL]
    output = fx.resolve(source)
    assert output[2][1] == pytest.approx(0.3125)
    assert output[2][2] == pytest.approx(0.6875)
    assert output[2][0] == pytest.approx(0.2)
    assert output[2][4] == pytest.approx(0.8)
    assert source == VERTICAL
    assert fx.stats()["resolves"] == 1


def test_plan_reports_edge_work_units():
    fx = make()
    flat_plan = fx.plan(FLAT)
    assert flat_plan == {"width": 5, "height": 5, "edges": 0,
                         "work_units": 25, "threshold": 0.1}
    plan = fx.plan(VERTICAL)
    assert plan["edges"] == 10
    assert plan["work_units"] == 55
    assert fx.stats()["plans"] == 2


def test_plan_budget_rejection_keeps_state():
    fx = make(budget_units=54)
    before = fx.snapshot()
    with pytest.raises(sg.FxaaError) as exc:
        fx.plan(VERTICAL)
    assert exc.value.code == "FX_BUDGET_EXCEEDED"
    assert fx.stats()["plans"] == 0
    assert fx.snapshot() == before

    plan = fx.plan(VERTICAL, budget_units=55)
    assert plan["work_units"] == 55
    assert fx.stats()["plans"] == 1


def test_set_threshold_changes_detection_and_blend():
    fx = make()
    assert fx.detect_edges(VERTICAL)["edges"] == 10
    fx.set_threshold(0.65)
    assert fx.detect_edges(VERTICAL)["edges"] == 0

    fx.set_threshold(0.1, relative=0.6)
    assert fx.blend_factor(VERTICAL, 1, 2) == pytest.approx(0.625)

    for threshold, relative in ((0.0, None), (1.0, None), (0.5, 0.0),
                                (0.5, 1.5)):
        with pytest.raises(sg.FxaaError) as exc:
            fx.set_threshold(threshold, relative=relative)
        assert exc.value.code == "FX_INVALID_THRESHOLD"


def test_snapshot_restore_and_corrupt_state():
    fx = make()
    fx.set_threshold(0.2, relative=0.3)
    fx.detect_edges(FLAT)
    state = fx.snapshot()

    restored = make()
    restored.restore(state)
    assert restored.stats() == {"detections": 1, "resolves": 0, "plans": 0}
    assert restored.blend_factor(VERTICAL, 1, 2) == pytest.approx(0.75)
    assert restored.detect_edges(VERTICAL)["edges"] == 10
    assert restored.stats()["detections"] == 2

    before = restored.snapshot()
    bad_states = []
    mutated = copy.deepcopy(state)
    mutated["version"] = 2
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["threshold"] = 0.0
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["relative_threshold"] = 2.0
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["subpixel_quality"] = 2.0
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["max_span"] = 0
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["stats"] = {}
    bad_states.append(mutated)
    bad_states.append(None)
    bad_states.append({})

    for bad in bad_states:
        with pytest.raises(sg.FxaaError) as exc:
            restored.restore(bad)
        assert exc.value.code == "FX_SNAPSHOT_INVALID"
    assert restored.snapshot() == before


def test_concurrent_detection_counts_every_call():
    fx = make()
    barrier = threading.Barrier(8)
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            result = fx.detect_edges(VERTICAL)
        except Exception as exc:  # pragma: no cover - surfaced below
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 8
    assert all(result == results[0] for result in results)
    assert results[0]["edges"] == 10
    assert fx.stats()["detections"] == 8

    fx.reset_stats()
    assert fx.stats() == {"detections": 0, "resolves": 0, "plans": 0}
