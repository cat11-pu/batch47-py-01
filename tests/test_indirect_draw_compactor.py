import copy
import threading

import pytest

import indirect_draw_compactor as dc


def compactor(**kwargs):
    return dc.IndirectDrawCompactor(**kwargs)


def setup(**kwargs):
    c = compactor(**kwargs)
    c.register_pipeline("opaque", index_count=36)
    c.register_pipeline("transparent", index_count=6)
    return c


def test_pipeline_registration_is_idempotent_and_guarded():
    c = compactor()
    record = c.register_pipeline("main", index_count=36)
    assert record == {"name": "main", "index_count": 36, "instanced": True}
    assert c.register_pipeline("main", index_count=36) == record
    with pytest.raises(dc.DrawError) as exc:
        c.register_pipeline("main", index_count=12)
    assert exc.value.code == "DRAW_PIPELINE_CONFLICT"
    with pytest.raises(dc.DrawError) as exc:
        c.register_pipeline("main", index_count=36, instanced=False)
    assert exc.value.code == "DRAW_PIPELINE_CONFLICT"
    with pytest.raises(dc.DrawError) as exc:
        c.register_pipeline("", index_count=36)
    assert exc.value.code == "DRAW_INVALID_PIPELINE"
    with pytest.raises(dc.DrawError) as exc:
        c.register_pipeline("broken", index_count=0)
    assert exc.value.code == "DRAW_INVALID_PIPELINE"
    with pytest.raises(dc.DrawError) as exc:
        c.submit("a", pipeline="missing", material="m")
    assert exc.value.code == "DRAW_UNKNOWN_PIPELINE"


def test_submit_validates_counts_materials_and_depths():
    c = setup()
    with pytest.raises(dc.DrawError) as exc:
        c.submit("a", pipeline="opaque", material="m", instance_count=0)
    assert exc.value.code == "DRAW_INVALID_INSTANCE_COUNT"
    with pytest.raises(dc.DrawError) as exc:
        c.submit("a", pipeline="opaque", material="m", instance_count=9)
    assert exc.value.code == "DRAW_INVALID_INSTANCE_COUNT"
    with pytest.raises(dc.DrawError) as exc:
        c.submit("a", pipeline="opaque", material="")
    assert exc.value.code == "DRAW_INVALID_MATERIAL"
    with pytest.raises(dc.DrawError) as exc:
        c.submit("a", pipeline="opaque", material="m", depth=float("inf"))
    assert exc.value.code == "DRAW_INVALID_DEPTH"
    with pytest.raises(dc.DrawError) as exc:
        c.submit("a", pipeline="opaque", material="m", depth="near")
    assert exc.value.code == "DRAW_INVALID_DEPTH"

    item = c.submit("a", pipeline="opaque", material="mat",
                    instance_count=2, depth=0.25)
    assert item == {
        "draw_id": "a", "pipeline": "opaque", "material": "mat",
        "instance_count": 2, "transparent": False, "depth": 0.25,
        "indices": 72,
    }
    with pytest.raises(dc.DrawError) as exc:
        c.submit("a", pipeline="opaque", material="mat")
    assert exc.value.code == "DRAW_DUPLICATE_DRAW"

    tiny = setup(max_pending=1)
    tiny.submit("only", pipeline="opaque", material="m")
    with pytest.raises(dc.DrawError) as exc:
        tiny.submit("overflow", pipeline="opaque", material="m")
    assert exc.value.code == "DRAW_PENDING_LIMIT"


def test_single_batch_accumulates_counts_and_indices():
    c = setup()
    c.submit("a", pipeline="opaque", material="m", instance_count=1)
    c.submit("b", pipeline="opaque", material="m", instance_count=2)
    c.submit("c", pipeline="opaque", material="m", instance_count=3)
    out = c.flush()
    assert out == {
        "batches": [{
            "pipeline": "opaque", "material": "m",
            "draws": ["a", "b", "c"], "instance_count": 6,
            "indices": 216, "transparent": False, "index": 0,
        }],
        "batch_count": 1, "draws": 3, "instances": 6, "indices": 216,
        "switches": 0,
    }
    assert c.pending() == []
    assert c.stats() == {"submitted": 3, "batched": 3, "batches": 1,
                         "cancelled": 0, "flushes": 1}


def test_merge_stops_at_the_instance_cap():
    c = setup(max_instances_per_batch=2)
    for name in ("a", "b", "c", "d", "e"):
        c.submit(name, pipeline="opaque", material="m")
    out = c.flush()
    assert [batch["instance_count"] for batch in out["batches"]] == [2, 2, 1]
    assert [batch["draws"] for batch in out["batches"]] == [
        ["a", "b"], ["c", "d"], ["e"]]
    assert out["switches"] == 0
    assert out["batch_count"] == 3


def test_keys_that_are_not_adjacent_stay_separate():
    c = setup()
    c.submit("a", pipeline="opaque", material="red")
    c.submit("b", pipeline="opaque", material="blue")
    c.submit("c", pipeline="opaque", material="red")
    out = c.flush()
    assert [batch["material"] for batch in out["batches"]] == [
        "red", "blue", "red"]
    assert out["batch_count"] == 3
    assert out["switches"] == 2


def test_transparent_items_follow_opaque_and_sort_back_to_front():
    c = setup()
    c.submit("solid", pipeline="opaque", material="wall",
             instance_count=2)
    c.submit("near", pipeline="transparent", material="glass-a",
             transparent=True, depth=0.3)
    c.submit("far", pipeline="transparent", material="glass-b",
             transparent=True, depth=0.9)
    c.submit("mid", pipeline="transparent", material="glass-c",
             transparent=True, depth=0.5)
    out = c.flush()
    assert [batch["draws"] for batch in out["batches"]] == [
        ["solid"], ["far"], ["mid"], ["near"]]
    assert [batch["transparent"] for batch in out["batches"]] == [
        False, True, True, True]
    assert out["instances"] == 5
    assert out["switches"] == 3


def test_transparent_batches_never_merge_their_instances():
    c = setup()
    c.submit("veil", pipeline="transparent", material="glass",
             instance_count=3, transparent=True, depth=0.5)
    c.submit("mist", pipeline="transparent", material="glass",
             instance_count=2, transparent=True, depth=0.4)
    out = c.flush()
    assert out["batch_count"] == 2
    assert out["batches"][0]["draws"] == ["veil"]
    assert out["batches"][0]["instance_count"] == 3
    assert out["batches"][0]["indices"] == 18
    assert out["batches"][1]["draws"] == ["mist"]
    assert out["instances"] == 5


def test_index_budget_aborts_flush_and_keeps_pending_items():
    c = setup(max_indices=100)
    c.submit("a", pipeline="opaque", material="m")
    c.submit("b", pipeline="opaque", material="m", instance_count=2)
    with pytest.raises(dc.DrawError) as exc:
        c.flush()
    assert exc.value.code == "DRAW_INDEX_BUDGET"
    assert len(c.pending()) == 2
    assert c.stats() == {"submitted": 2, "batched": 0, "batches": 0,
                         "cancelled": 0, "flushes": 0}

    assert c.cancel("b") is True
    out = c.flush()
    assert out["indices"] == 36
    assert out["draws"] == 1


def test_batch_budget_aborts_flush_without_losing_items():
    c = setup(max_batches=1)
    c.submit("a", pipeline="opaque", material="red")
    c.submit("b", pipeline="opaque", material="blue")
    with pytest.raises(dc.DrawError) as exc:
        c.flush()
    assert exc.value.code == "DRAW_BATCH_LIMIT"
    assert len(c.pending()) == 2
    assert c.stats()["flushes"] == 0
    assert c.stats()["batched"] == 0


def test_second_flush_without_items_is_empty_but_counted():
    c = setup()
    c.submit("a", pipeline="opaque", material="m")
    first = c.flush()
    assert first["batch_count"] == 1
    second = c.flush()
    assert second == {"batches": [], "batch_count": 0, "draws": 0,
                      "instances": 0, "indices": 0, "switches": 0}
    assert c.stats() == {"submitted": 1, "batched": 1, "batches": 1,
                         "cancelled": 0, "flushes": 2}


def test_cancel_only_affects_pending_submissions():
    c = setup()
    c.submit("a", pipeline="opaque", material="m")
    c.submit("b", pipeline="opaque", material="m")
    assert c.cancel("a") is True
    assert c.cancel("a") is False
    out = c.flush()
    assert out["batches"][0]["draws"] == ["b"]
    assert c.cancel("b") is False
    assert c.pending() == []
    assert c.stats()["cancelled"] == 1


def test_describe_batch_returns_defensive_copies():
    c = setup()
    c.submit("a", pipeline="opaque", material="m")
    out = c.flush()
    assert c.describe_batch(0) == out["batches"][0]
    row = c.describe_batch(0)
    row["draws"].append("injected")
    row["material"] = "hacked"
    assert c.describe_batch(0)["draws"] == ["a"]
    assert c.describe_batch(0)["material"] == "m"
    for bad in (1, -1, "0", True):
        with pytest.raises(dc.DrawError) as exc:
            c.describe_batch(bad)
        assert exc.value.code == "DRAW_UNKNOWN_BATCH"


def test_snapshot_restore_keeps_pending_batches_and_stats():
    c = setup()
    c.submit("a", pipeline="opaque", material="m", instance_count=2)
    c.submit("t", pipeline="transparent", material="glass",
             transparent=True, depth=0.8)
    out = c.flush()
    c.submit("b", pipeline="opaque", material="m")
    state = c.snapshot()

    clone = compactor()
    clone.restore(state)
    assert clone.pending() == c.pending()
    assert clone.describe_batch(0) == out["batches"][0]
    assert clone.stats() == c.stats()

    state["pending"].clear()
    state["pipelines"].clear()
    assert clone.pending()[0]["draw_id"] == "b"
    assert clone.describe_batch(0)["draws"] == ["a"]

    continued = clone.flush()
    assert continued["batch_count"] == 1
    assert continued["batches"][0]["draws"] == ["b"]


def test_corrupt_snapshot_is_rejected_without_losing_state():
    c = setup()
    c.submit("keep", pipeline="opaque", material="m")
    good = c.snapshot()
    bad_states = [None, "not-a-snapshot", {}, {"version": 2}]
    for mutate in (
        lambda s: s.pop("pending"),
        lambda s: s.update({"stats": {"submitted": -1, "batched": 0,
                                      "batches": 0, "cancelled": 0,
                                      "flushes": 0}}),
        lambda s: s["pending"][0].update({"pipeline": "ghost"}),
        lambda s: s["pending"][0].update({"instance_count": 0}),
        lambda s: s["pipelines"][0].update({"index_count": 0}),
    ):
        broken = copy.deepcopy(good)
        mutate(broken)
        bad_states.append(broken)

    for bad in bad_states:
        with pytest.raises(dc.DrawError) as exc:
            c.restore(bad)
        assert exc.value.code == "DRAW_SNAPSHOT_INVALID"
    assert len(c.pending()) == 1
    out = c.flush()
    assert out["batches"][0]["draws"] == ["keep"]


def test_concurrent_submissions_accept_one_duplicate_and_all_unique():
    c = setup(max_pending=64)
    barrier = threading.Barrier(8)
    accepted = []
    codes = []
    lock = threading.Lock()

    def duplicate_worker(index):
        barrier.wait()
        try:
            c.submit("dup", pipeline="opaque", material=f"m{index}")
        except dc.DrawError as exc:
            with lock:
                codes.append(exc.code)
            return
        with lock:
            accepted.append(index)

    threads = [threading.Thread(target=duplicate_worker, args=(i,))
               for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert sorted(codes) == ["DRAW_DUPLICATE_DRAW"] * 7
    assert len(c.pending()) == 1

    for index in range(8):
        c.submit(f"unique-{index}", pipeline="opaque", material="shared")
    assert len(c.pending()) == 9
    out = c.flush()
    assert out["draws"] == 9
    assert out["instances"] == 9
    assert c.stats()["submitted"] == 9
