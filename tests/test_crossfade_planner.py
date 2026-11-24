import threading

import pytest

import crossfade_planner as cp


def planner_with(*entries, **kwargs):
    planner = cp.CrossfadePlanner(**kwargs)
    for entry in entries:
        planner.add_entry(entry["entry_id"], uri=entry["uri"],
                          duration_ms=entry["duration_ms"],
                          fade_in_ms=entry.get("fade_in_ms", 0),
                          fade_out_ms=entry.get("fade_out_ms", 0))
    return planner


def entry(entry_id, duration_ms, fade_in_ms=0, fade_out_ms=0):
    return {"entry_id": entry_id, "uri": entry_id + ".wav",
            "duration_ms": duration_ms, "fade_in_ms": fade_in_ms,
            "fade_out_ms": fade_out_ms}


def test_plan_places_entries_sequentially_without_fades():
    planner = planner_with(entry("a", 5000), entry("b", 4000))
    result = planner.plan()
    assert result["total_ms"] == 9000
    assert result["crossfades"] == []
    assert [(row["entry_id"], row["start_ms"], row["end_ms"])
            for row in result["entries"]] == [("a", 0, 5000), ("b", 5000, 9000)]
    assert result["entries"][1]["duration_ms"] == 4000


def test_crossfade_uses_shorter_adjacent_fade():
    planner = planner_with(
        entry("a", 5000, fade_out_ms=2000),
        entry("b", 3000, fade_in_ms=1000, fade_out_ms=1500),
        entry("c", 2000, fade_in_ms=800),
    )
    result = planner.plan()
    assert result["total_ms"] == 8200
    assert [(row["entry_id"], row["start_ms"], row["end_ms"])
            for row in result["entries"]] == [
        ("a", 0, 5000), ("b", 4000, 7000), ("c", 6200, 8200)]
    assert [(row["left"], row["right"], row["start_ms"], row["end_ms"],
             row["overlap_ms"], row["overlap_samples"])
            for row in result["crossfades"]] == [
        ("a", "b", 4000, 5000, 1000, 48000),
        ("b", "c", 6200, 7000, 800, 38400),
    ]


def test_fade_in_gain_rises_linearly_in_permille():
    planner = planner_with(entry("a", 4000, fade_in_ms=500))
    assert planner.gain_at("a", 0) == 0
    assert planner.gain_at("a", 1) == 2
    assert planner.gain_at("a", 250) == 500
    assert planner.gain_at("a", 499) == 998
    assert planner.gain_at("a", 500) == 1000
    assert planner.gain_at("a", 4000) == 1000


def test_fade_out_gain_falls_and_ends_at_zero():
    planner = planner_with(entry("a", 5000, fade_out_ms=1000))
    assert planner.gain_at("a", 4000) == 1000
    assert planner.gain_at("a", 4500) == 500
    assert planner.gain_at("a", 4999) == 1
    assert planner.gain_at("a", 5000) == 0


def test_offset_outside_entry_or_unknown_entry_is_refused():
    planner = planner_with(entry("a", 1000))
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.gain_at("a", -1)
    assert exc.value.code == "CF_OFFSET_INVALID"
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.gain_at("a", 1001)
    assert exc.value.code == "CF_OFFSET_INVALID"
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.gain_at("missing", 0)
    assert exc.value.code == "CF_ENTRY_UNKNOWN"


def test_entry_validation_rules():
    planner = cp.CrossfadePlanner()
    planner.add_entry("a", uri="a.wav", duration_ms=5000)
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.add_entry("a", uri="other.wav", duration_ms=1000)
    assert exc.value.code == "CF_ENTRY_EXISTS"
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.add_entry("", uri="a.wav", duration_ms=1000)
    assert exc.value.code == "CF_ENTRY_INVALID"
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.add_entry("zero", uri="z.wav", duration_ms=0)
    assert exc.value.code == "CF_DURATION_INVALID"
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.add_entry("neg", uri="n.wav", duration_ms=1000, fade_in_ms=-1)
    assert exc.value.code == "CF_FADE_INVALID"
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.add_entry("long", uri="l.wav", duration_ms=1000,
                          fade_out_ms=1001)
    assert exc.value.code == "CF_FADE_INVALID"
    assert len(planner.plan()["entries"]) == 1


def test_entry_limit_and_budget_are_enforced_atomically():
    small = cp.CrossfadePlanner(max_entries=2)
    small.add_entry("a", uri="a.wav", duration_ms=1000)
    small.add_entry("b", uri="b.wav", duration_ms=1000)
    with pytest.raises(cp.CrossfadeError) as exc:
        small.add_entry("c", uri="c.wav", duration_ms=1000)
    assert exc.value.code == "CF_ENTRY_LIMIT"

    tight = cp.CrossfadePlanner(max_total_ms=6000)
    tight.add_entry("a", uri="a.wav", duration_ms=5000, fade_out_ms=500)
    with pytest.raises(cp.CrossfadeError) as exc:
        tight.add_entry("b", uri="b.wav", duration_ms=4000, fade_in_ms=500)
    assert exc.value.code == "CF_BUDGET_EXCEEDED"
    assert tight.plan()["total_ms"] == 5000
    assert [row["entry_id"] for row in tight.plan()["entries"]] == ["a"]


def test_trim_entry_recomputes_duration_and_clamps_fades():
    planner = planner_with(entry("a", 5000, fade_in_ms=1000, fade_out_ms=2000))
    trimmed = planner.trim_entry("a", head_ms=500, tail_ms=500)
    assert trimmed["duration_ms"] == 4000
    assert trimmed["fade_in_ms"] == 1000
    assert trimmed["fade_out_ms"] == 2000
    assert planner.plan()["total_ms"] == 4000

    shrunk = planner.trim_entry("a", head_ms=3500, tail_ms=400)
    assert shrunk["duration_ms"] == 100
    assert shrunk["fade_in_ms"] == 100
    assert shrunk["fade_out_ms"] == 100
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.trim_entry("a", head_ms=-1)
    assert exc.value.code == "CF_TRIM_INVALID"
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.trim_entry("a", head_ms=100)
    assert exc.value.code == "CF_TRIM_INVALID"


def test_remove_entry_reflows_neighbours():
    planner = planner_with(
        entry("a", 5000, fade_out_ms=2000),
        entry("b", 3000, fade_in_ms=500, fade_out_ms=500),
        entry("c", 2000, fade_in_ms=800),
    )
    assert planner.plan()["total_ms"] == 9000
    removed = planner.remove_entry("b")
    assert removed["entry_id"] == "b"
    result = planner.plan()
    assert result["total_ms"] == 6200
    assert [(row["entry_id"], row["start_ms"])
            for row in result["entries"]] == [("a", 0), ("c", 4200)]
    assert result["crossfades"][0]["overlap_ms"] == 800
    with pytest.raises(cp.CrossfadeError) as exc:
        planner.remove_entry("b")
    assert exc.value.code == "CF_ENTRY_UNKNOWN"


def test_sample_bounds_scale_with_sample_rate():
    planner = planner_with(
        entry("a", 5000, fade_in_ms=250, fade_out_ms=1000),
        entry("b", 1000),
        sample_rate=48000,
    )
    assert planner.sample_bounds("a") == {
        "start_sample": 0,
        "end_sample": 240000,
        "fade_in_samples": 12000,
        "fade_out_samples": 48000,
    }
    assert planner.sample_bounds("b") == {
        "start_sample": 240000,
        "end_sample": 288000,
        "fade_in_samples": 0,
        "fade_out_samples": 0,
    }


def test_empty_plan_and_single_entry_plan():
    planner = cp.CrossfadePlanner()
    empty = planner.plan()
    assert empty == {"total_ms": 0, "entries": [], "crossfades": []}
    planner.add_entry("only", uri="only.wav", duration_ms=1200)
    one = planner.plan()
    assert one["total_ms"] == 1200
    assert one["entries"] == [{
        "entry_id": "only",
        "start_ms": 0,
        "end_ms": 1200,
        "duration_ms": 1200,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
    }]


def test_plan_is_stable_across_repeated_reads():
    planner = planner_with(entry("a", 1000, fade_out_ms=200),
                           entry("b", 1000, fade_in_ms=200))
    first = planner.plan()
    second = planner.plan()
    assert first == second
    assert planner.stats()["plans"] == 2
    fetched = planner.entry("a")
    assert fetched["entry_id"] == "a"
    fetched["duration_ms"] = 1
    assert planner.plan()["entries"][0]["duration_ms"] == 1000


def test_snapshot_restore_and_corrupt_state():
    planner = planner_with(entry("a", 2000, fade_out_ms=300),
                           entry("b", 1500, fade_in_ms=300))
    state = planner.snapshot()
    planner.add_entry("c", uri="c.wav", duration_ms=1000)
    expected = state["entries"]

    restored = cp.CrossfadePlanner()
    restored.restore(state)
    assert [(row["entry_id"], row["start_ms"])
            for row in restored.plan()["entries"]] == [("a", 0), ("b", 1700)]
    assert restored.stats()["added"] == 2
    restored.add_entry("d", uri="d.wav", duration_ms=500)
    assert len(state["entries"]) == 2
    assert len(expected) == 2

    for bad in (None, {}, {"version": 2, "entries": [], "stats": {}},
                {"version": 1, "entries": "nope", "stats": {}},
                {"version": 1, "entries": [], "stats": "nope"}):
        with pytest.raises(cp.CrossfadeError) as exc:
            restored.restore(bad)
        assert exc.value.code == "CF_SNAPSHOT_INVALID"
    assert len(restored.plan()["entries"]) == 3


def test_concurrent_duplicate_entry_is_added_once():
    planner = cp.CrossfadePlanner()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = planner.add_entry("race", uri="race.wav", duration_ms=1200)
        except cp.CrossfadeError as err:
            out = {"code": err.code}
        except Exception as err:  # pragma: no cover - surfaced via assertion
            out = {"error": repr(err)}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for row in results if row.get("entry_id") == "race") == 1
    assert sum(1 for row in results if row.get("code") == "CF_ENTRY_EXISTS") == 7
    assert len(planner.plan()["entries"]) == 1
    assert planner.stats()["added"] == 1


def test_stats_and_reset():
    planner = planner_with(entry("a", 1000), entry("b", 1000))
    planner.trim_entry("a", head_ms=100)
    planner.remove_entry("b")
    planner.plan()
    assert planner.stats() == {
        "added": 2,
        "removed": 1,
        "trimmed": 1,
        "plans": 1,
    }
    planner.reset_stats()
    assert planner.stats() == {"added": 0, "removed": 0,
                               "trimmed": 0, "plans": 0}
    assert len(planner.plan()["entries"]) == 1
