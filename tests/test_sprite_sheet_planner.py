import threading

import pytest

import sprite_sheet_planner as sp


def make(**kwargs):
    params = {"columns": 3, "rows": 2, "max_pages": 8, "max_sources": 4}
    params.update(kwargs)
    return sp.SpriteSheetPlanner(**params)


def test_constructor_rejects_invalid_grid_settings():
    for kwargs in ({"columns": 0}, {"rows": 0}, {"tile_width": 0},
                   {"max_pages": 0}, {"snap_ms": -1}):
        with pytest.raises(sp.SpriteError) as exc:
            sp.SpriteSheetPlanner(**kwargs)
        assert exc.value.code == "SPRITE_CONFIG_INVALID"

    capacity = sp.SpriteSheetPlanner().capacity()
    assert capacity["tiles_per_page"] == 100
    assert capacity["max_sources"] == 16
    assert capacity["pages_used"] == 0


def test_sources_are_idempotent_conflict_checked_and_removable():
    planner = make()
    added = planner.add_source("s1", duration_ms=10000, interval_ms=2500)
    assert added["source_id"] == "s1"
    assert added["created"] is True
    assert added["tiles_per_page"] == 6
    assert added["keyframes"] == []

    again = planner.add_source("s1", duration_ms=10000, interval_ms=2500)
    assert again["created"] is False

    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("s1", duration_ms=10000, interval_ms=1000)
    assert exc.value.code == "SPRITE_CONFLICT"

    for source_id in ("s2", "s3", "s4"):
        planner.add_source(source_id, duration_ms=5000, interval_ms=1000)
    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("s5", duration_ms=5000, interval_ms=1000)
    assert exc.value.code == "SPRITE_SOURCE_LIMIT"

    removed = planner.remove_source("s2")
    assert removed["removed"] is True
    planner.add_source("s5", duration_ms=5000, interval_ms=1000)

    with pytest.raises(sp.SpriteError) as exc:
        planner.plan("ghost")
    assert exc.value.code == "SPRITE_UNKNOWN_SOURCE"


def test_source_and_keyframe_validation():
    planner = make()
    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("bad", duration_ms=0, interval_ms=1000)
    assert exc.value.code == "SPRITE_BAD_SOURCE"

    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("bad", duration_ms=5000, interval_ms=0)
    assert exc.value.code == "SPRITE_BAD_SOURCE"

    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("bad", duration_ms=5000, interval_ms=1000,
                           keyframes=[100, 50])
    assert exc.value.code == "SPRITE_BAD_KEYFRAMES"

    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("bad", duration_ms=5000, interval_ms=1000,
                           keyframes=[0, 6000])
    assert exc.value.code == "SPRITE_BAD_KEYFRAMES"

    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("bad", duration_ms=5000, interval_ms=1000,
                           keyframes=[1.5])
    assert exc.value.code == "SPRITE_BAD_KEYFRAMES"

    with pytest.raises(sp.SpriteError) as exc:
        planner.add_source("bad", duration_ms=5000, interval_ms=1000,
                           keyframes=[100, 100])
    assert exc.value.code == "SPRITE_BAD_KEYFRAMES"


def test_plan_samples_the_range_and_fills_pages_row_major():
    planner = make()
    planner.add_source("s", duration_ms=10000, interval_ms=2500)
    planned = planner.plan("s")
    assert planned["range"] == [0, 10000]
    assert [tile["time_ms"] for tile in planned["tiles"]] == [0, 2500, 5000,
                                                              7500, 10000]
    assert planned["tile_count"] == 5
    assert planned["page_count"] == 1
    assert planned["tiles_per_page"] == 6
    assert planned["sheet_width"] == 480
    assert planned["sheet_height"] == 180

    planner.add_source("big", duration_ms=12000, interval_ms=1000)
    big = planner.plan("big")
    assert big["tile_count"] == 13
    assert big["page_count"] == 3
    assert (big["tiles"][5]["page"], big["tiles"][5]["row"],
            big["tiles"][5]["col"]) == (0, 1, 2)
    assert (big["tiles"][6]["page"], big["tiles"][6]["row"],
            big["tiles"][6]["col"]) == (1, 0, 0)
    assert (big["tiles"][12]["page"], big["tiles"][12]["row"],
            big["tiles"][12]["col"]) == (2, 0, 0)


def test_sampling_snaps_to_nearest_keyframe_within_window():
    planner = make(snap_ms=400)
    planner.add_source("s", duration_ms=4000, interval_ms=1000,
                       keyframes=[0, 900, 2100])
    planned = planner.plan("s")
    assert [tile["time_ms"] for tile in planned["tiles"]] == [0, 900, 2100,
                                                              3000, 4000]


def test_keyframe_ties_choose_earlier_time_and_duplicates_collapse():
    planner = make(snap_ms=1000)
    planner.add_source("s", duration_ms=3000, interval_ms=500,
                       keyframes=[1400, 1600])
    planned = planner.plan("s")
    assert [tile["time_ms"] for tile in planned["tiles"]] == [0, 1400, 1600,
                                                              3000]
    assert planned["tile_count"] == 4
    assert planned["page_count"] == 1


def test_range_must_be_inside_source_and_end_after_start():
    planner = make()
    planner.add_source("s", duration_ms=10000, interval_ms=2000)

    for start, end in ((-1, None), (0, 10001), (5000, 5000), (6000, 4000)):
        with pytest.raises(sp.SpriteError) as exc:
            planner.plan("s", start_ms=start, end_ms=end)
        assert exc.value.code == "SPRITE_RANGE_INVALID"

    subset = planner.plan("s", start_ms=2000, end_ms=6000)
    assert [tile["time_ms"] for tile in subset["tiles"]] == [2000, 4000, 6000]
    assert subset["range"] == [2000, 6000]


def test_single_plan_page_limit_is_enforced_before_anything_is_cached():
    planner = make(max_pages=1)
    planner.add_source("s", duration_ms=10000, interval_ms=1000)

    with pytest.raises(sp.SpriteError) as exc:
        planner.plan("s")
    assert exc.value.code == "SPRITE_PAGE_LIMIT"

    with pytest.raises(sp.SpriteError) as exc:
        planner.commit("s")
    assert exc.value.code == "SPRITE_PAGE_LIMIT"
    assert planner.active_plan("s") is None
    assert planner.capacity()["pages_used"] == 0
    assert planner.stats()["rejects"] == 2


def test_commit_tracks_generation_and_returns_detached_plan():
    planner = make()
    planner.add_source("s", duration_ms=10000, interval_ms=2500)
    committed = planner.commit("s")
    assert committed["generation"] == 1
    assert committed["tile_count"] == 5
    assert committed["page_count"] == 1

    active = planner.active_plan("s")
    assert active["generation"] == 1
    assert [tile["time_ms"] for tile in active["tiles"]] == [0, 2500, 5000,
                                                             7500, 10000]

    active["tiles"].clear()
    assert planner.active_plan("s")["tile_count"] == 5
    assert planner.stats()["commits"] == 1


def test_recommit_replaces_page_accounting_instead_of_adding():
    planner = make()
    planner.add_source("s", duration_ms=10000, interval_ms=2500)
    planner.commit("s")
    assert planner.capacity()["pages_used"] == 1

    second = planner.commit("s", start_ms=0, end_ms=5000)
    assert second["generation"] == 2
    assert second["tile_count"] == 3
    assert planner.capacity()["pages_used"] == 1
    active = planner.active_plan("s")
    assert [tile["time_ms"] for tile in active["tiles"]] == [0, 2500, 5000]
    assert planner.stats()["commits"] == 2


def test_global_page_budget_blocks_the_third_source():
    planner = make(max_pages=2)
    for source_id in ("a", "b", "c"):
        planner.add_source(source_id, duration_ms=6000, interval_ms=1000)

    assert planner.commit("a")["page_count"] == 2
    assert planner.capacity()["pages_used"] == 2

    with pytest.raises(sp.SpriteError) as exc:
        planner.commit("b")
    assert exc.value.code == "SPRITE_PAGE_LIMIT"
    assert planner.active_plan("b") is None
    assert planner.capacity()["pages_used"] == 2
    assert planner.active_plan("a")["tile_count"] == 7

    with pytest.raises(sp.SpriteError) as exc:
        planner.commit("c")
    assert exc.value.code == "SPRITE_PAGE_LIMIT"


def test_keyframe_updates_invalidate_active_plan_atomically():
    planner = make()
    planner.add_source("s", duration_ms=10000, interval_ms=2500)
    planner.commit("s")

    updated = planner.set_keyframes("s", [0, 2600, 5000])
    assert updated["invalidated"] is True
    assert updated["generation"] == 1
    assert planner.active_plan("s") is None
    assert planner.capacity()["pages_used"] == 0

    with pytest.raises(sp.SpriteError) as exc:
        planner.set_keyframes("s", [500, 500])
    assert exc.value.code == "SPRITE_BAD_KEYFRAMES"
    planned = planner.plan("s")
    assert [tile["time_ms"] for tile in planned["tiles"]] == [0, 2600, 5000,
                                                              7500, 10000]

    again = planner.invalidate("s")
    assert again["invalidated"] is False


def test_snapshot_restore_keeps_sources_keyframes_and_active_plans():
    planner = make()
    planner.add_source("s1", duration_ms=10000, interval_ms=2500)
    planner.add_source("s2", duration_ms=6000, interval_ms=1000)
    planner.commit("s1")

    state = planner.snapshot()
    assert state["version"] == 1

    planner.add_source("s3", duration_ms=5000, interval_ms=1000)
    planner.commit("s2")

    restored = make()
    restored.restore(state)
    active = restored.active_plan("s1")
    assert active["generation"] == 1
    assert active["tile_count"] == 5
    assert restored.active_plan("s2") is None
    assert restored.capacity()["pages_used"] == 1
    assert restored.capacity()["sources"] == 2
    assert restored.plan("s2")["tile_count"] == 7
    assert restored.stats()["commits"] == 1

    state["sources"]["s1"]["active"]["tiles"].clear()
    assert restored.active_plan("s1")["tile_count"] == 5
    with pytest.raises(sp.SpriteError) as exc:
        restored.plan("s3")
    assert exc.value.code == "SPRITE_UNKNOWN_SOURCE"


def test_corrupt_snapshot_is_rejected_and_state_survives():
    planner = make()
    planner.add_source("s", duration_ms=10000, interval_ms=2500)
    planner.commit("s")

    bad_states = [
        None,
        {},
        {"version": 2, "sources": {}, "stats": {"commits": 0, "rejects": 0}},
        {"version": 1, "sources": [],
         "stats": {"commits": 0, "rejects": 0}},
        {"version": 1,
         "sources": {"s": {"duration_ms": 10000, "interval_ms": 2500,
                           "keyframes": [], "generation": 1,
                           "active": "broken"}},
         "stats": {"commits": 0, "rejects": 0}},
        {"version": 1, "sources": {},
         "stats": {"commits": "many", "rejects": 0}},
    ]
    for bad in bad_states:
        with pytest.raises(sp.SpriteError) as exc:
            planner.restore(bad)
        assert exc.value.code == "SPRITE_SNAPSHOT_INVALID"

    assert planner.active_plan("s")["tile_count"] == 5
    assert planner.capacity()["pages_used"] == 1


def test_concurrent_commits_serialize_and_page_budget_stays_flat():
    planner = make()
    planner.add_source("s", duration_ms=10000, interval_ms=2500)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            outcome = planner.commit("s")["generation"]
        except Exception as exc:  # pragma: no cover - surfaced via assertions
            outcome = getattr(exc, "code", repr(exc))
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert planner.active_plan("s")["generation"] == 8
    assert planner.capacity()["pages_used"] == 1
    assert planner.stats()["commits"] == 8
