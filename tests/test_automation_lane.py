import threading

import pytest

import automation_lane as al


def make(**kwargs):
    lane = al.AutomationLane(**kwargs)
    lane.create_lane("vol")
    return lane


def test_constructor_and_lane_creation_validate_configuration():
    with pytest.raises(al.MixError) as exc:
        al.AutomationLane(curve="bezier")
    assert exc.value.code == "MIX_CONFIG_INVALID"

    with pytest.raises(al.MixError) as exc:
        al.AutomationLane(min_value=5.0, max_value=1.0)
    assert exc.value.code == "MIX_CONFIG_INVALID"

    lane = al.AutomationLane()
    created = lane.create_lane("vol")
    assert created["created"] is True
    assert created["curve"] == "linear"
    assert created["default_value"] == 1.0

    again = lane.create_lane("vol")
    assert again["created"] is False

    with pytest.raises(al.MixError) as exc:
        lane.create_lane("vol", default_value=0.5)
    assert exc.value.code == "MIX_CONFLICT"

    with pytest.raises(al.MixError) as exc:
        lane.create_lane("")
    assert exc.value.code == "MIX_BAD_PARAM"

    with pytest.raises(al.MixError) as exc:
        lane.value_at("ghost", 0)
    assert exc.value.code == "MIX_UNKNOWN_LANE"


def test_point_validation_covers_time_value_shape_and_mode():
    lane = make()
    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(-1, 1.0)])
    assert exc.value.code == "MIX_BAD_TIME"

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(0, 20.0)])
    assert exc.value.code == "MIX_BAD_VALUE"

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(0, float("nan"))])
    assert exc.value.code == "MIX_BAD_VALUE"

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(0, "loud")])
    assert exc.value.code == "MIX_BAD_VALUE"

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(0, 1.0, 2.0)])
    assert exc.value.code == "MIX_BAD_POINT"

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(0, 1.0)], mode="overwrite")
    assert exc.value.code == "MIX_BAD_MODE"

    assert lane.lane_info("vol")["point_count"] == 0


def test_merge_sorts_points_and_counts_writes_and_overwrites():
    lane = make()
    merged = lane.apply("vol", [(1000, 0.5), (0, 0.0)])
    assert merged["written"] == 2
    assert merged["overwritten"] == 0
    assert merged["point_count"] == 2
    assert lane.value_at("vol", 0) == 0.0
    assert lane.value_at("vol", 500) == 0.25
    assert lane.value_at("vol", 1000) == 0.5

    replaced = lane.apply("vol", [(1000, 0.8)])
    assert replaced["written"] == 0
    assert replaced["overwritten"] == 1
    assert replaced["point_count"] == 2
    assert lane.value_at("vol", 1000) == 0.8

    collapsed = lane.apply("vol", [(500, 0.1), (500, 0.2)])
    assert collapsed["written"] == 1
    assert collapsed["point_count"] == 3
    assert lane.value_at("vol", 500) == 0.2
    assert lane.stats() == {"writes": 3, "overwrites": 1, "rejects": 0}


def test_point_budget_is_hard_and_batches_stay_atomic():
    lane = al.AutomationLane(max_points=3)
    lane.create_lane("vol")
    lane.apply("vol", [(10, 0.1), (20, 0.2), (30, 0.3)])

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(40, 0.4)])
    assert exc.value.code == "MIX_LANE_FULL"
    assert lane.lane_info("vol")["point_count"] == 3

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(50, 0.5), (60, 0.6)])
    assert exc.value.code == "MIX_LANE_FULL"
    assert lane.lane_info("vol")["point_count"] == 3
    assert lane.value_at("vol", 40) == pytest.approx(0.3)
    assert lane.stats()["rejects"] == 2


def test_linear_interpolation_and_clamping():
    lane = make()
    lane.apply("vol", [(1000, 0.0), (3000, 2.0)])
    assert lane.value_at("vol", 2000) == pytest.approx(1.0)
    assert lane.value_at("vol", 0) == 0.0
    assert lane.value_at("vol", 5000) == 2.0
    assert lane.value_at("vol", 1000) == 0.0
    assert lane.value_at("vol", 3000) == 2.0


def test_step_curve_holds_the_previous_value_and_per_lane_override():
    stepper = al.AutomationLane(curve="step")
    stepper.create_lane("gate")
    stepper.apply("gate", [(1000, 0.0), (3000, 2.0)])
    assert stepper.value_at("gate", 999) == 0.0
    assert stepper.value_at("gate", 1000) == 0.0
    assert stepper.value_at("gate", 2999) == 0.0
    assert stepper.value_at("gate", 3000) == 2.0
    assert stepper.value_at("gate", 3500) == 2.0

    stepper.create_lane("fade", curve="linear")
    stepper.apply("fade", [(1000, 0.0), (3000, 2.0)])
    assert stepper.value_at("fade", 2000) == pytest.approx(1.0)


def test_replace_mode_is_atomic_and_clears_previous_points():
    lane = make()
    lane.apply("vol", [(0, 0.0), (1000, 1.0)])

    with pytest.raises(al.MixError) as exc:
        lane.apply("vol", [(2000, 2.0), (3000, "bad")])
    assert exc.value.code == "MIX_BAD_VALUE"
    assert lane.lane_info("vol")["point_count"] == 2

    replaced = lane.apply("vol", [(5000, 3.0), (4000, 2.5)], mode="replace")
    assert replaced["point_count"] == 2
    assert replaced["written"] == 2
    assert replaced["overwritten"] == 0
    assert lane.value_at("vol", 0) == 2.5
    assert lane.value_at("vol", 4500) == pytest.approx(2.75)


def test_sample_walks_the_inclusive_range():
    lane = make()
    lane.apply("vol", [(0, 0.0), (1000, 10.0)])
    assert lane.sample("vol", 0, 1000, 250) == pytest.approx(
        [0.0, 2.5, 5.0, 7.5, 10.0])
    assert lane.sample("vol", 0, 900, 400) == pytest.approx([0.0, 4.0, 8.0])

    for start, end, step in ((0, 1000, 0), (1000, 0, 100), (-1, 100, 10)):
        with pytest.raises(al.MixError) as exc:
            lane.sample("vol", start, end, step)
        assert exc.value.code == "MIX_BAD_RANGE"


def test_clear_range_removes_interior_points_and_keeps_neighbors():
    lane = make()
    lane.apply("vol", [(0, 1.0), (1000, 2.0), (2000, 3.0), (3000, 4.0)])
    cleared = lane.clear_range("vol", 500, 2000)
    assert cleared["cleared"] == 2
    assert cleared["point_count"] == 2
    assert lane.value_at("vol", 1500) == pytest.approx(2.5)

    edge = lane.clear_range("vol", 0, 0)
    assert edge["cleared"] == 1
    assert edge["point_count"] == 1
    assert lane.value_at("vol", 0) == 4.0

    with pytest.raises(al.MixError) as exc:
        lane.clear_range("vol", 2000, 1000)
    assert exc.value.code == "MIX_BAD_RANGE"


def test_remove_point_and_lane_inventory():
    lane = al.AutomationLane()
    lane.create_lane("b")
    lane.create_lane("a")
    assert lane.list_lanes() == ["a", "b"]

    lane.apply("a", [(1000, 2.0), (3000, 6.0), (2000, 4.0)])
    info = lane.lane_info("a")
    assert info["point_count"] == 3
    assert info["first_time"] == 1000
    assert info["last_time"] == 3000
    assert info["min_value"] == 2.0
    assert info["max_value"] == 6.0
    assert info["curve"] == "linear"

    removed = lane.remove_point("a", 1000)
    assert removed["removed"] is True
    assert removed["point_count"] == 2
    missing = lane.remove_point("a", 9999)
    assert missing["removed"] is False
    assert missing["point_count"] == 2

    lane.drop_lane("b")
    assert lane.list_lanes() == ["a"]
    with pytest.raises(al.MixError) as exc:
        lane.value_at("b", 0)
    assert exc.value.code == "MIX_UNKNOWN_LANE"


def test_beat_and_millisecond_conversions_round_predictably():
    assert al.beats_to_ms(1.5, bpm=120) == 750
    assert al.beats_to_ms(0.333, bpm=120) == 167
    assert al.beats_to_ms(0, bpm=120) == 0
    assert al.ms_to_beats(750, bpm=120) == 1.25
    assert al.ms_to_beats(1, bpm=120) == 0.002

    with pytest.raises(al.MixError) as exc:
        al.beats_to_ms(1, bpm=0)
    assert exc.value.code == "MIX_BAD_TEMPO"
    with pytest.raises(al.MixError) as exc:
        al.beats_to_ms(-1, bpm=120)
    assert exc.value.code == "MIX_BAD_TIME"
    with pytest.raises(al.MixError) as exc:
        al.ms_to_beats(-5, bpm=120)
    assert exc.value.code == "MIX_BAD_TIME"
    with pytest.raises(al.MixError) as exc:
        al.ms_to_beats(10, bpm=-1)
    assert exc.value.code == "MIX_BAD_TEMPO"


def test_snapshot_restore_keeps_lanes_curves_and_counters():
    lane = al.AutomationLane()
    lane.create_lane("vol")
    lane.create_lane("pan", curve="step", default_value=0.5)
    lane.apply("vol", [(0, 0.0), (1000, 1.0)])

    state = lane.snapshot()
    assert state["version"] == 1

    lane.apply("vol", [(0, 2.0)])
    lane.create_lane("extra")

    restored = al.AutomationLane()
    restored.restore(state)
    assert restored.list_lanes() == ["pan", "vol"]
    assert restored.value_at("vol", 0) == 0.0
    assert restored.value_at("vol", 500) == pytest.approx(0.5)
    info = restored.lane_info("pan")
    assert info["curve"] == "step"
    assert info["default_value"] == 0.5
    assert restored.stats() == {"writes": 2, "overwrites": 0, "rejects": 0}

    state["lanes"]["vol"]["points"].clear()
    assert restored.lane_info("vol")["point_count"] == 2


def test_corrupt_snapshot_is_rejected_without_touching_lanes():
    lane = make()
    lane.apply("vol", [(0, 1.0)])
    zero = {"writes": 0, "overwrites": 0, "rejects": 0}
    bad_states = [
        None,
        {},
        {"version": 2, "lanes": {}, "stats": dict(zero)},
        {"version": 1, "lanes": [], "stats": dict(zero)},
        {"version": 1,
         "lanes": {"vol": {"curve": "bezier", "default_value": 1.0,
                           "points": []}},
         "stats": dict(zero)},
        {"version": 1,
         "lanes": {"vol": {"curve": "linear", "default_value": 1.0,
                           "points": [[0, "loud"]]}},
         "stats": dict(zero)},
        {"version": 1, "lanes": {}, "stats": {"writes": "many"}},
    ]
    for bad in bad_states:
        with pytest.raises(al.MixError) as exc:
            lane.restore(bad)
        assert exc.value.code == "MIX_SNAPSHOT_INVALID"

    assert lane.value_at("vol", 0) == 1.0
    assert lane.lane_info("vol")["point_count"] == 1


def test_empty_lane_returns_default_value():
    lane = al.AutomationLane()
    lane.create_lane("empty", default_value=-6.0)
    assert lane.value_at("empty", 12345) == -6.0
    info = lane.lane_info("empty")
    assert info["point_count"] == 0
    assert info["first_time"] is None
    assert info["last_time"] is None
    assert info["min_value"] is None
    assert info["max_value"] is None
    assert lane.list_lanes() == ["empty"]


def test_concurrent_applies_keep_points_sorted_and_collapse_same_time():
    lane = make()
    first_barrier = threading.Barrier(8)
    lock = threading.Lock()
    errors = []

    def writer(index, barrier, time_ms):
        barrier.wait()
        try:
            lane.apply("vol", [(time_ms, float(index))])
        except Exception as exc:  # pragma: no cover - surfaced via assertions
            with lock:
                errors.append(getattr(exc, "code", repr(exc)))

    threads = [threading.Thread(target=writer, args=(i, first_barrier, i * 100))
               for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    info = lane.lane_info("vol")
    assert info["point_count"] == 8
    assert info["first_time"] == 0
    assert info["last_time"] == 700

    second_barrier = threading.Barrier(8)
    same_time = [threading.Thread(target=writer,
                                  args=(i, second_barrier, 5000))
                 for i in range(8)]
    for thread in same_time:
        thread.start()
    for thread in same_time:
        thread.join()

    assert errors == []
    assert lane.lane_info("vol")["point_count"] == 9
    assert lane.value_at("vol", 5000) in {float(i) for i in range(8)}
    stats = lane.stats()
    assert stats["writes"] == 9
    assert stats["overwrites"] == 7
