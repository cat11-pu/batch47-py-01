import copy
import threading

import pytest

import stencil_shadow_volume_guard as sg

VERTICES = [(1.0, 0.0, 0.0), (0.0, 5.0, 0.0), (0.0, 0.0, 5.0)]
SILHOUETTE = [(0, 1), (1, 2), (2, 0)]


def make(**kwargs):
    params = dict(stencil_bits=8, max_lights=4, edge_budget=50000)
    params.update(kwargs)
    return sg.ShadowVolumeStencil(**params)


def add_default_light(guard, light_id="l", **kwargs):
    params = dict(position=(0.0, 0.0, 0.0), radius=10.0)
    params.update(kwargs)
    return guard.add_light(light_id, **params)


def add_default_occluder(guard, occluder_id="o", **kwargs):
    params = dict(vertices=VERTICES, silhouette=SILHOUETTE)
    params.update(kwargs)
    return guard.add_occluder(occluder_id, **params)


def test_invalid_constructor_parameters():
    for override in ({"stencil_bits": 0}, {"stencil_bits": 9},
                     {"max_lights": 0}, {"edge_budget": 0}):
        with pytest.raises(sg.ShadowVolumeError) as exc:
            make(**override)
        assert exc.value.code == "SV_INVALID_CONFIG"


def test_add_light_is_idempotent_or_conflicting():
    guard = make()
    first = guard.add_light("l", position=(1, 2, 3), radius=4.0, priority=2)
    assert first["duplicate"] is False
    assert first["position"] == (1.0, 2.0, 3.0)
    assert first["radius"] == 4.0
    assert first["priority"] == 2

    again = guard.add_light("l", position=(1, 2, 3), radius=4.0, priority=2)
    assert again["duplicate"] is True
    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.add_light("l", position=(1, 2, 4), radius=4.0, priority=2)
    assert exc.value.code == "SV_CONFLICT"


def test_add_light_validates_inputs():
    guard = make()
    cases = [
        ("", (0, 0, 0), 4.0, 0),
        ("l", (0, 0), 4.0, 0),
        ("l", (0, 0, 0), 0.0, 0),
        ("l", (0, 0, 0), -1.0, 0),
        ("l", (0, 0, 0), 4.0, "high"),
    ]
    for light_id, position, radius, priority in cases:
        with pytest.raises(sg.ShadowVolumeError) as exc:
            guard.add_light(light_id, position=position, radius=radius,
                            priority=priority)
        assert exc.value.code == "SV_INVALID_LIGHT"
    assert guard.select_lights() == []


def test_remove_light_and_occluder_purge_volume_cache():
    guard = make()
    add_default_light(guard)
    add_default_occluder(guard)
    assert guard.build_volume("o", "l")["cached"] is False

    guard.remove_light("l")
    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.remove_light("l")
    assert exc.value.code == "SV_UNKNOWN_LIGHT"
    add_default_light(guard)
    assert guard.build_volume("o", "l")["cached"] is False

    guard.remove_occluder("o")
    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.remove_occluder("o")
    assert exc.value.code == "SV_UNKNOWN_OCCLUDER"
    add_default_occluder(guard)
    assert guard.build_volume("o", "l")["cached"] is False


def test_add_occluder_validates_geometry():
    guard = make()
    cases = [
        ("", VERTICES, SILHOUETTE),
        ("o", [(0, 0, 0), (1, 0, 0)], SILHOUETTE),
        ("o", [(0, 0, 0), (1, 0, 0), (1, 0)], SILHOUETTE),
        ("o", VERTICES, [(0, 1), (1, 3), (2, 0)]),
        ("o", VERTICES, [(0, 1), (1, 1), (2, 0)]),
        ("o", VERTICES, [(0, 1), (1, 2)]),
    ]
    for occluder_id, vertices, silhouette in cases:
        with pytest.raises(sg.ShadowVolumeError) as exc:
            guard.add_occluder(occluder_id, vertices=vertices,
                               silhouette=silhouette)
        assert exc.value.code == "SV_INVALID_OCCLUDER"

    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.add_occluder("o", vertices=VERTICES,
                           silhouette=[(0, 1), (1, 0), (2, 0)])
    assert exc.value.code == "SV_DUPLICATE_EDGE"

    assert add_default_occluder(guard)["edges"] == 3


def test_duplicate_occluder_id_conflicts():
    guard = make()
    add_default_occluder(guard)
    with pytest.raises(sg.ShadowVolumeError) as exc:
        add_default_occluder(guard)
    assert exc.value.code == "SV_CONFLICT"


def test_build_volume_rejects_unknown_ids():
    guard = make()
    add_default_light(guard)
    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.build_volume("missing", "l")
    assert exc.value.code == "SV_UNKNOWN_OCCLUDER"

    add_default_occluder(guard)
    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.build_volume("o", "missing")
    assert exc.value.code == "SV_UNKNOWN_LIGHT"


def test_build_volume_extrudes_along_light_rays():
    guard = make()
    add_default_light(guard, position=(0.0, 0.0, 0.0), radius=10.0)
    add_default_occluder(guard)
    volume = guard.build_volume("o", "l")
    assert volume["edges"] == 3
    assert volume["side_quads"] == 6
    assert volume["vertices"] == 3
    assert volume["zfail"] is False
    assert volume["cached"] is False
    expected = [(11.0, 0.0, 0.0), (0.0, 15.0, 0.0), (0.0, 0.0, 15.0)]
    for got, want in zip(volume["extruded"], expected):
        assert tuple(got) == pytest.approx(want)


def test_volume_cache_zfail_toggle_and_single_sided():
    guard = make()
    add_default_light(guard)
    add_default_occluder(guard)
    first = guard.build_volume("o", "l")
    assert first["cached"] is False
    second = guard.build_volume("o", "l")
    assert second["cached"] is True
    assert second["zfail"] is False

    guard.set_zfail(True)
    third = guard.build_volume("o", "l")
    assert third["cached"] is False
    assert third["zfail"] is True
    assert guard.stats()["built"] == 2
    assert guard.stats()["cached"] == 1

    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.set_zfail("yes")
    assert exc.value.code == "SV_INVALID_FLAG"

    guard.add_occluder("single", vertices=VERTICES, silhouette=SILHOUETTE,
                       two_sided=False)
    assert guard.build_volume("single", "l")["side_quads"] == 3


def test_light_inside_occluder_is_rejected():
    guard = make()
    guard.add_light("l", position=(1.0, 0.0, 0.0), radius=10.0)
    add_default_occluder(guard)
    with pytest.raises(sg.ShadowVolumeError) as exc:
        guard.build_volume("o", "l")
    assert exc.value.code == "SV_LIGHT_INSIDE"
    assert guard.stats()["built"] == 0


def test_select_lights_orders_by_priority_radius_and_id():
    guard = make(max_lights=3)
    guard.add_light("b", position=(0, 0, 0), radius=5.0, priority=1)
    guard.add_light("a", position=(0, 0, 0), radius=1.0, priority=2)
    guard.add_light("c", position=(0, 0, 0), radius=9.0, priority=1)
    guard.add_light("d", position=(0, 0, 0), radius=100.0, priority=0)
    assert guard.select_lights() == ["a", "c", "b"]

    guard.add_light("e", position=(0, 0, 0), radius=9.0, priority=1)
    assert guard.select_lights() == ["a", "c", "e"]


def test_stencil_operations_and_overflow():
    guard = make()
    add_default_light(guard)
    add_default_occluder(guard)
    ops = guard.stencil_ops("o", "l")
    assert ops["incr"] == 6
    assert ops["decr"] == 6
    assert ops["mask"] == 255
    assert ops["zfail"] is False
    assert ops["cap_front"] is True
    assert ops["cap_back"] is True

    narrow = make(stencil_bits=2)
    add_default_light(narrow)
    add_default_occluder(narrow)
    with pytest.raises(sg.ShadowVolumeError) as exc:
        narrow.stencil_ops("o", "l")
    assert exc.value.code == "SV_STENCIL_OVERFLOW"

    narrow.add_occluder("single", vertices=VERTICES, silhouette=SILHOUETTE,
                        two_sided=False)
    ops = narrow.stencil_ops("single", "l")
    assert ops["incr"] == 3
    assert ops["mask"] == 3


def test_plan_frame_counts_and_budget_rejection():
    def setup(**kwargs):
        guard = make(**kwargs)
        guard.add_light("l1", position=(0.0, 0.0, 0.0), radius=10.0,
                        priority=2)
        guard.add_light("l2", position=(100.0, 0.0, 0.0), radius=5.0,
                        priority=1)
        add_default_occluder(guard, "o1")
        guard.add_occluder("o2", vertices=[(0.0, 0.0, 0.0), (10.0, 10.0, 10.0),
                                           (10.0, 0.0, 0.0)],
                           silhouette=SILHOUETTE)
        return guard

    guard = setup()
    plan = guard.plan_frame()
    assert plan["lights"] == 2
    assert plan["volumes"] == 3
    assert plan["edges"] == 18
    assert plan["skipped"] == 1
    assert plan["work_units"] == 48
    assert plan["mask"] == 255
    assert plan["zfail"] is False
    assert guard.stats()["plans"] == 1

    tight = setup(edge_budget=47)
    before = tight.snapshot()
    with pytest.raises(sg.ShadowVolumeError) as exc:
        tight.plan_frame()
    assert exc.value.code == "SV_BUDGET_EXCEEDED"
    assert tight.stats()["plans"] == 0
    assert tight.snapshot() == before

    plan = tight.plan_frame(budget_edges=48)
    assert plan["work_units"] == 48
    assert tight.stats()["plans"] == 1


def test_snapshot_restore_and_corrupt_state():
    guard = make(max_lights=2)
    guard.add_light("l1", position=(0.0, 0.0, 0.0), radius=10.0, priority=2)
    guard.add_light("l2", position=(50.0, 0.0, 0.0), radius=6.0, priority=1)
    add_default_occluder(guard)
    guard.set_zfail(True)
    guard.plan_frame()
    state = guard.snapshot()

    guard.remove_light("l1")
    restored = make()
    restored.restore(state)
    assert restored.select_lights() == ["l1", "l2"]
    assert restored.stats()["plans"] == 1
    volume = restored.build_volume("o", "l2")
    assert volume["zfail"] is True
    assert volume["cached"] is False

    before = restored.snapshot()
    bad_states = []
    mutated = copy.deepcopy(state)
    mutated["version"] = 2
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["zfail"] = "yes"
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["lights"]["l1"]["radius"] = 0.0
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["occluders"]["o"]["silhouette"] = [(0, 1)]
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["stats"] = {}
    bad_states.append(mutated)
    bad_states.append(None)
    bad_states.append({})

    for bad in bad_states:
        with pytest.raises(sg.ShadowVolumeError) as exc:
            restored.restore(bad)
        assert exc.value.code == "SV_SNAPSHOT_INVALID"
    assert restored.snapshot() == before


def test_concurrent_volume_build_caches_once():
    guard = make()
    add_default_light(guard)
    add_default_occluder(guard)
    barrier = threading.Barrier(8)
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.build_volume("o", "l")
        except Exception as exc:  # pragma: no cover - surfaced below
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 8
    assert sum(1 for row in results if row["cached"] is False) == 1
    assert sum(1 for row in results if row["cached"] is True) == 7
    assert guard.stats()["built"] == 1
    assert guard.stats()["cached"] == 7

    guard.reset_stats()
    assert guard.stats() == {"built": 0, "cached": 0, "plans": 0}
