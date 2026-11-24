import copy
import threading

import pytest

import bvh_refit_scheduler as bv


def scheduler(**kwargs):
    return bv.BvhRefitScheduler(**kwargs)


def T(value):
    return (float(value),) * 12


def make(**kwargs):
    sched = scheduler(frame_budget=kwargs.pop("frame_budget", 100), **kwargs)
    sched.add_instance("a", mesh_id="m1", transform=T(1), coverage=0.9)
    sched.add_instance("b", mesh_id="m1", transform=T(2), coverage=0.5)
    sched.add_instance("c", mesh_id="m2", transform=T(3), coverage=0.5)
    sched.add_instance("d", mesh_id="m2", transform=T(4), coverage=0.1)
    return sched


def test_config_and_instance_registration_are_validated():
    for kwargs in ({"rebuild_ratio": 0}, {"rebuild_ratio": 1.2},
                   {"frame_budget": 0}, {"max_instances": 0},
                   {"refit_cost": 0}, {"rebuild_cost": 0}):
        with pytest.raises(bv.RefitError) as exc:
            scheduler(**kwargs)
        assert exc.value.code == "REFIT_INVALID_CONFIG"

    sched = scheduler(max_instances=2)
    record = sched.add_instance(
        "a", mesh_id="m1",
        transform=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0], coverage=0.5)
    assert record == {
        "instance_id": "a", "mesh_id": "m1",
        "transform": (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                      0.0, 0.0, 1.0, 0.0),
        "coverage": 0.5, "version": 1, "dirty": True,
        "topology_dirty": True,
    }
    with pytest.raises(bv.RefitError) as exc:
        sched.add_instance("a", mesh_id="m1", transform=T(0))
    assert exc.value.code == "REFIT_DUPLICATE_INSTANCE"
    sched.add_instance("b", mesh_id="m1", transform=T(0))
    with pytest.raises(bv.RefitError) as exc:
        sched.add_instance("c", mesh_id="m1", transform=T(0))
    assert exc.value.code == "REFIT_INSTANCE_LIMIT"

    for bad in ((1.0,) * 11, "not-a-matrix", (1.0,) * 11 + (float("nan"),),
                (1.0,) * 11 + (True,)):
        with pytest.raises(bv.RefitError) as exc:
            scheduler().add_instance("x", mesh_id="m", transform=bad)
        assert exc.value.code == "REFIT_INVALID_TRANSFORM"
    for coverage in (-0.1, 1.1):
        with pytest.raises(bv.RefitError) as exc:
            scheduler().add_instance("x", mesh_id="m", transform=T(0),
                                     coverage=coverage)
        assert exc.value.code == "REFIT_INVALID_COVERAGE"
    with pytest.raises(bv.RefitError) as exc:
        scheduler().add_instance("x", mesh_id="", transform=T(0))
    assert exc.value.code == "REFIT_INVALID_MESH"


def test_updates_report_changes_and_skip_identical_transforms():
    sched = scheduler()
    sched.add_instance("a", mesh_id="m", transform=T(1), coverage=0.4)
    sched.begin_frame(1)
    assert sched.update_instance("a", transform=T(1)) == {
        "instance_id": "a", "changed": False, "version": 1, "dirty": False,
    }
    assert sched.is_dirty("a") is False

    assert sched.update_instance("a", transform=T(2)) == {
        "instance_id": "a", "changed": True, "version": 2, "dirty": True,
    }
    assert sched.is_dirty("a") is True

    again = sched.update_instance("a", transform=[2.0] * 12, coverage=0.4)
    assert again == {"instance_id": "a", "changed": False, "version": 2,
                     "dirty": True}


def test_unknown_and_removed_instances_are_rejected():
    sched = scheduler()
    with pytest.raises(bv.RefitError) as exc:
        sched.update_instance("ghost", transform=T(0))
    assert exc.value.code == "REFIT_UNKNOWN_INSTANCE"
    with pytest.raises(bv.RefitError) as exc:
        sched.set_mesh("ghost", mesh_id="m")
    assert exc.value.code == "REFIT_UNKNOWN_INSTANCE"
    with pytest.raises(bv.RefitError) as exc:
        sched.remove_instance("ghost")
    assert exc.value.code == "REFIT_UNKNOWN_INSTANCE"
    with pytest.raises(bv.RefitError) as exc:
        sched.instance_state("ghost")
    assert exc.value.code == "REFIT_UNKNOWN_INSTANCE"
    assert sched.is_dirty("ghost") is False

    sched.add_instance("a", mesh_id="m", transform=T(1))
    sched.remove_instance("a")
    with pytest.raises(bv.RefitError) as exc:
        sched.update_instance("a", transform=T(2))
    assert exc.value.code == "REFIT_UNKNOWN_INSTANCE"
    assert sched.is_dirty("a") is False


def test_mesh_swap_marks_topology_dirty():
    sched = scheduler()
    sched.add_instance("a", mesh_id="m1", transform=T(1))
    sched.begin_frame(1)
    same = sched.set_mesh("a", mesh_id="m1")
    assert same == {"instance_id": "a", "changed": False, "version": 1,
                    "topology_dirty": False}
    swapped = sched.set_mesh("a", mesh_id="m2")
    assert swapped == {"instance_id": "a", "changed": True, "version": 2,
                       "topology_dirty": True}
    state = sched.instance_state("a")
    assert state["mesh_id"] == "m2"
    assert state["dirty"] is True
    assert state["topology_dirty"] is True
    with pytest.raises(bv.RefitError) as exc:
        sched.set_mesh("a", mesh_id="")
    assert exc.value.code == "REFIT_INVALID_MESH"


def test_pending_lists_dirty_instances_by_priority():
    sched = make()
    pending = sched.pending()
    assert pending == {"dirty": ["a", "b", "c", "d"], "count": 4,
                       "topology": ["a", "b", "c", "d"], "instances": 4}
    sched.begin_frame(1)
    assert sched.pending() == {"dirty": [], "count": 0, "topology": [],
                               "instances": 4}


def test_rebuild_when_dirty_ratio_reaches_threshold():
    sched = make()
    sched.begin_frame(1)
    sched.reset_stats()
    sched.update_instance("b", transform=T(20))
    sched.update_instance("d", transform=T(40))
    plan = sched.begin_frame(2)
    assert plan == {"frame": 2, "mode": "rebuild",
                    "order": ["a", "b", "c", "d"], "units": 16,
                    "deferred": [], "live_instances": 4}
    assert sched.pending()["dirty"] == []
    assert sched.pending()["topology"] == []
    assert sched.stats() == {"frames": 1, "refits": 0, "rebuilds": 1,
                             "deferred": 0}


def test_single_dirty_instance_is_refit_in_place():
    sched = make()
    sched.begin_frame(1)
    sched.reset_stats()
    sched.update_instance("a", transform=T(9))
    plan = sched.begin_frame(2)
    assert plan == {"frame": 2, "mode": "refit", "order": ["a"], "units": 1,
                    "deferred": [], "live_instances": 4}
    assert sched.stats() == {"frames": 1, "refits": 1, "rebuilds": 0,
                             "deferred": 0}


def test_budget_forces_refits_and_carries_the_rest_over():
    sched = make()
    sched.begin_frame(1)
    sched.reset_stats()
    for name in ("a", "b", "c", "d"):
        sched.update_instance(name, transform=T(50))
    plan = sched.begin_frame(2, budget=3)
    assert plan == {"frame": 2, "mode": "refit",
                    "order": ["a", "b", "c"], "units": 3,
                    "deferred": ["d"], "live_instances": 4}
    assert sched.stats() == {"frames": 1, "refits": 3, "rebuilds": 0,
                             "deferred": 1}

    carried = sched.begin_frame(3, budget=3)
    assert carried["order"] == ["d"]
    assert carried["deferred"] == []
    assert carried["units"] == 1


def test_budget_smaller_than_one_refit_defers_everything():
    sched = make(refit_cost=2)
    sched.begin_frame(1)
    sched.reset_stats()
    sched.update_instance("a", transform=T(9))
    sched.update_instance("b", transform=T(9))
    plan = sched.begin_frame(2, budget=1)
    assert plan == {"frame": 2, "mode": "refit", "order": [], "units": 0,
                    "deferred": ["a", "b"], "live_instances": 4}
    assert sched.stats() == {"frames": 1, "refits": 0, "rebuilds": 0,
                             "deferred": 2}
    assert sched.is_dirty("a") is True


def test_topology_change_blocks_refit_until_a_rebuild_runs():
    sched = make()
    sched.begin_frame(1)
    sched.reset_stats()
    sched.set_mesh("a", mesh_id="m9")
    sched.update_instance("b", transform=T(7))
    plan = sched.begin_frame(2, budget=2)
    assert plan["mode"] == "refit"
    assert plan["order"] == ["b"]
    assert plan["deferred"] == ["a"]
    assert plan["units"] == 1

    rebuilt = sched.begin_frame(3)
    assert rebuilt["mode"] == "rebuild"
    assert rebuilt["order"] == ["a", "b", "c", "d"]
    assert rebuilt["units"] == 16
    assert sched.instance_state("a")["topology_dirty"] is False
    assert sched.pending()["dirty"] == []


def test_frame_numbers_must_advance():
    sched = scheduler()
    sched.add_instance("a", mesh_id="m", transform=T(0))
    for bad in (0, -1, 1.5, "2", True):
        with pytest.raises(bv.RefitError) as exc:
            sched.begin_frame(bad)
        assert exc.value.code == "REFIT_INVALID_FRAME"

    first = sched.begin_frame(1)
    assert first["mode"] == "rebuild"
    assert first["order"] == ["a"]
    with pytest.raises(bv.RefitError) as exc:
        sched.begin_frame(1)
    assert exc.value.code == "REFIT_FRAME_ORDER"
    with pytest.raises(bv.RefitError) as exc:
        sched.begin_frame(3, budget=0)
    assert exc.value.code == "REFIT_INVALID_BUDGET"
    idle = sched.begin_frame(3)
    assert idle == {"frame": 3, "mode": "none", "order": [], "units": 0,
                    "deferred": [], "live_instances": 1}


def test_removed_instances_hold_their_slot_until_compacted():
    sched = make()
    sched.begin_frame(1)
    sched.remove_instance("b")
    assert sched.pending()["instances"] == 3
    with pytest.raises(bv.RefitError) as exc:
        sched.add_instance("b", mesh_id="m1", transform=T(1))
    assert exc.value.code == "REFIT_DUPLICATE_INSTANCE"
    assert sched.compact() == 1
    assert sched.compact() == 0
    revived = sched.add_instance("b", mesh_id="m1", transform=T(1),
                                 coverage=0.5)
    assert revived["version"] == 1
    assert revived["topology_dirty"] is True
    assert sched.pending()["instances"] == 4


def test_snapshot_restore_keeps_dirty_state_and_frame_position():
    sched = make()
    sched.begin_frame(1)
    sched.reset_stats()
    sched.update_instance("a", transform=T(9))
    sched.update_instance("b", transform=T(9))
    first = sched.begin_frame(2, budget=1)
    assert first["order"] == ["a"]
    assert first["deferred"] == ["b"]

    state = sched.snapshot()
    clone = scheduler()
    clone.restore(state)
    assert clone.pending() == sched.pending()
    assert clone.pending()["dirty"] == ["b"]
    assert clone.stats() == sched.stats()
    with pytest.raises(bv.RefitError) as exc:
        clone.begin_frame(2)
    assert exc.value.code == "REFIT_FRAME_ORDER"

    continued = clone.begin_frame(3)
    assert continued["mode"] == "refit"
    assert continued["order"] == ["b"]

    state["instances"].clear()
    assert clone.instance_state("b")["dirty"] is False
    assert clone.instance_state("a")["version"] == 2


def test_corrupt_snapshot_is_rejected_without_touching_state():
    sched = make()
    sched.begin_frame(1)
    before = sched.pending()
    before_stats = sched.stats()
    good = sched.snapshot()

    bad_states = [None, [], {}, {"version": 2}, ]
    for mutate in (
        lambda s: s.pop("instances"),
        lambda s: s.update({"frame": -1}),
        lambda s: s.update({"stats": {"frames": -1, "refits": 0,
                                      "rebuilds": 0, "deferred": 0}}),
        lambda s: s["instances"].append({"instance_id": "z"}),
        lambda s: s["options"].update({"refit_cost": 0}),
    ):
        broken = copy.deepcopy(good)
        mutate(broken)
        bad_states.append(broken)

    for bad in bad_states:
        with pytest.raises(bv.RefitError) as exc:
            sched.restore(bad)
        assert exc.value.code == "REFIT_SNAPSHOT_INVALID"
    assert sched.pending() == before
    assert sched.stats() == before_stats

    clone = scheduler()
    clone.restore(good)
    assert clone.pending()["instances"] == 4


def test_concurrent_updates_apply_exactly_once():
    sched = scheduler()
    sched.add_instance("shared", mesh_id="m", transform=T(0))
    sched.begin_frame(1)
    barrier = threading.Barrier(8)
    outcomes = []
    failures = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            result = sched.update_instance("shared", transform=T(5))
        except Exception as exc:  # pragma: no cover - surfaced below
            with lock:
                failures.append(repr(exc))
            return
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(outcomes) == 8
    assert sum(1 for row in outcomes if row["changed"]) == 1
    assert all(row["version"] == 2 for row in outcomes)
    assert sched.instance_state("shared")["version"] == 2

    for index in range(8):
        sched.add_instance(f"extra-{index}", mesh_id="m", transform=T(index))
    assert sched.pending()["instances"] == 9
    assert sched.pending()["count"] == 9
