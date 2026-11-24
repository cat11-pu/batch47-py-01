import copy
import threading

import pytest

import soft_particle_guard as sg


def make(**kwargs):
    params = dict(
        width=1920,
        height=1080,
        fade_distance=1.5,
        max_quads=4096,
        blend_layers=4,
        budget_units=2000000,
    )
    params.update(kwargs)
    return sg.SoftParticleBatch(**params)


def emit(batch, particle_id, *, size=1.0, depth=5.0, scene_depth=10.0,
         layer=0):
    return batch.emit(particle_id, position=(0.0, 0.0), size=size,
                      depth=depth, scene_depth=scene_depth, layer=layer)


def test_invalid_constructor_parameters():
    for override in ({"width": 0}, {"height": 0}, {"fade_distance": 0.0},
                     {"max_quads": 0}, {"blend_layers": 0},
                     {"budget_units": 0}):
        with pytest.raises(sg.ParticleError) as exc:
            make(**override)
        assert exc.value.code == "PT_INVALID_CONFIG"


def test_compute_fade_boundaries():
    batch = make()
    assert batch.compute_fade(5.0, 5.0) == 0.0
    assert batch.compute_fade(5.0, 4.0) == 0.0
    assert batch.compute_fade(5.0, 6.5) == pytest.approx(1.0)
    assert batch.compute_fade(5.0, 6.25) == pytest.approx(1.25 / 1.5)
    assert batch.compute_fade(0.0, 3.0) == pytest.approx(1.0)


def test_compute_fade_rejects_invalid_depth():
    batch = make()
    for depth, scene_depth in ((-1.0, 5.0), (5.0, -1.0), ("5", 6.0),
                               (5.0, "6")):
        with pytest.raises(sg.ParticleError) as exc:
            batch.compute_fade(depth, scene_depth)
        assert exc.value.code == "PT_INVALID_DEPTH"


def test_emit_validates_fields_and_stores_fade():
    batch = make()
    cases = [
        ("", (0, 0), 1.0, 5.0, 6.0, 0, "PT_INVALID_PARTICLE"),
        ("p", (0,), 1.0, 5.0, 6.0, 0, "PT_INVALID_POSITION"),
        ("p", (0, 0), 0.0, 5.0, 6.0, 0, "PT_INVALID_SIZE"),
        ("p", (0, 0), 1.0, -1.0, 6.0, 0, "PT_INVALID_DEPTH"),
        ("p", (0, 0), 1.0, 5.0, 6.0, -1, "PT_INVALID_LAYER"),
        ("p", (0, 0), 1.0, 5.0, 6.0, 4, "PT_INVALID_LAYER"),
    ]
    for pid, position, size, depth, scene, layer, code in cases:
        with pytest.raises(sg.ParticleError) as exc:
            batch.emit(pid, position=position, size=size, depth=depth,
                       scene_depth=scene, layer=layer)
        assert exc.value.code == code
    assert batch.capacity() == {"max_quads": 4096, "used": 0, "free": 4096}

    row = batch.emit("p", position=(10.0, 20.0), size=2.0, depth=5.0,
                     scene_depth=6.0, layer=1)
    assert row["fade"] == pytest.approx(1.0 / 1.5)
    assert row["visible"] is True
    assert row["pixels"] == 4
    assert row["layer"] == 1
    assert batch.get_particle("p")["position"] == (10.0, 20.0)
    assert batch.capacity()["used"] == 1


def test_duplicate_particle_is_rejected():
    batch = make()
    emit(batch, "p")
    with pytest.raises(sg.ParticleError) as exc:
        emit(batch, "p")
    assert exc.value.code == "PT_DUPLICATE"
    assert batch.stats()["emitted"] == 1


def test_capacity_is_enforced_and_reusable():
    batch = make(max_quads=1)
    emit(batch, "a")
    with pytest.raises(sg.ParticleError) as exc:
        emit(batch, "b")
    assert exc.value.code == "PT_CAPACITY"
    assert batch.capacity() == {"max_quads": 1, "used": 1, "free": 0}

    batch.update("a", size=3.0)
    batch.remove("a")
    assert batch.capacity()["used"] == 0
    emit(batch, "b")
    assert batch.capacity()["free"] == 0


def test_update_recomputes_fade_and_validates():
    batch = make()
    emit(batch, "p", size=2.0, depth=5.0, scene_depth=6.0, layer=0)
    row = batch.update("p", depth=5.5, size=3.0, layer=2)
    assert row["fade"] == pytest.approx(0.5 / 1.5)
    assert row["pixels"] == 9
    assert row["layer"] == 2
    assert row["visible"] is True

    with pytest.raises(sg.ParticleError) as exc:
        batch.update("missing", size=1.0)
    assert exc.value.code == "PT_UNKNOWN_PARTICLE"
    with pytest.raises(sg.ParticleError) as exc:
        batch.get_particle("missing")
    assert exc.value.code == "PT_UNKNOWN_PARTICLE"
    with pytest.raises(sg.ParticleError) as exc:
        batch.update("p", layer=9)
    assert exc.value.code == "PT_INVALID_LAYER"
    assert batch.stats()["updated"] == 1


def test_remove_returns_capacity():
    batch = make()
    emit(batch, "p", size=2.0)
    batch.remove("p")
    assert batch.capacity() == {"max_quads": 4096, "used": 0, "free": 4096}
    with pytest.raises(sg.ParticleError) as exc:
        batch.remove("p")
    assert exc.value.code == "PT_UNKNOWN_PARTICLE"
    assert batch.stats()["removed"] == 1


def test_sort_quads_by_layer_then_depth_then_id():
    batch = make()
    emit(batch, "b", size=3.0, depth=5.0, layer=0)
    emit(batch, "a", size=2.0, depth=5.0, layer=0)
    emit(batch, "c", size=1.0, depth=9.0, layer=0)
    emit(batch, "d", size=4.0, depth=7.0, layer=1)
    emit(batch, "z", size=99.0, depth=9.0, scene_depth=9.0, layer=0)
    assert batch.sort_quads() == ["c", "a", "b", "d"]


def test_plan_reports_overdraw_units():
    batch = make()
    emit(batch, "b", size=3.0, depth=5.0, layer=0)
    emit(batch, "a", size=2.0, depth=5.0, layer=0)
    emit(batch, "c", size=1.0, depth=9.0, layer=0)
    emit(batch, "d", size=4.0, depth=7.0, layer=1)
    emit(batch, "z", size=99.0, depth=9.0, scene_depth=9.0, layer=0)
    plan = batch.plan()
    assert plan["quads"] == 4
    assert plan["culled"] == 1
    assert plan["pixels"] == 30
    assert plan["layers"] == 2
    assert plan["work_units"] == 50
    assert plan["fade_distance"] == 1.5
    assert batch.stats()["plans"] == 1


def test_plan_budget_rejection_keeps_state():
    batch = make(budget_units=49)
    emit(batch, "b", size=3.0, depth=5.0, layer=0)
    emit(batch, "a", size=2.0, depth=5.0, layer=0)
    emit(batch, "c", size=1.0, depth=9.0, layer=0)
    emit(batch, "d", size=4.0, depth=7.0, layer=1)
    emit(batch, "z", size=99.0, depth=9.0, scene_depth=9.0, layer=0)
    before = batch.snapshot()
    with pytest.raises(sg.ParticleError) as exc:
        batch.plan()
    assert exc.value.code == "PT_OVERDRAW_BUDGET"
    assert batch.stats()["plans"] == 0
    assert batch.snapshot() == before

    plan = batch.plan(budget_units=50)
    assert plan["work_units"] == 50
    assert batch.stats()["plans"] == 1


def test_fade_distance_update_recomputes_stored_fade():
    batch = make()
    emit(batch, "p", size=2.0, depth=5.0, scene_depth=5.4)
    assert batch.get_particle("p")["fade"] == pytest.approx(0.4 / 1.5)
    batch.set_fade_distance(0.2)
    assert batch.get_particle("p")["fade"] == pytest.approx(1.0)
    emit(batch, "q", size=1.0, depth=5.0, scene_depth=5.1)
    assert batch.get_particle("q")["fade"] == pytest.approx(0.5)
    with pytest.raises(sg.ParticleError) as exc:
        batch.set_fade_distance(0.0)
    assert exc.value.code == "PT_INVALID_CONFIG"


def test_snapshot_restore_brings_back_particles():
    batch = make()
    emit(batch, "p1", size=2.0, depth=5.0, scene_depth=10.0, layer=0)
    emit(batch, "p2", size=3.0, depth=6.0, scene_depth=10.0, layer=1)
    batch.plan()
    state = batch.snapshot()

    batch.remove("p1")
    emit(batch, "p3", size=4.0, depth=7.0)
    restored = make()
    restored.restore(state)
    assert restored.sort_quads() == ["p1", "p2"]
    assert restored.capacity()["used"] == 2
    assert restored.stats()["plans"] == 1
    assert restored.get_particle("p1")["size"] == 2.0
    assert restored.snapshot()["fade_distance"] == 1.5


def test_corrupt_snapshot_is_rejected_without_mutation():
    batch = make()
    emit(batch, "p1", size=2.0, depth=5.0, scene_depth=10.0)
    state = batch.snapshot()
    restored = make()
    restored.restore(state)
    before = restored.snapshot()

    bad_states = []
    mutated = copy.deepcopy(state)
    mutated["version"] = 2
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["fade_distance"] = 0.0
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["particles"] = []
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    del mutated["particles"]["p1"]["size"]
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["particles"]["p1"]["layer"] = 9
    bad_states.append(mutated)
    mutated = copy.deepcopy(state)
    mutated["stats"] = {}
    bad_states.append(mutated)
    bad_states.append(None)
    bad_states.append({})

    for bad in bad_states:
        with pytest.raises(sg.ParticleError) as exc:
            restored.restore(bad)
        assert exc.value.code == "PT_SNAPSHOT_INVALID"
    assert restored.snapshot() == before


def test_concurrent_emit_respects_capacity():
    batch = make(max_quads=1)
    barrier = threading.Barrier(8)
    accepted = []
    rejected = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            row = batch.emit(f"p{index}", position=(0.0, 0.0), size=1.0,
                             depth=5.0, scene_depth=6.0, layer=0)
        except sg.ParticleError as exc:
            with lock:
                rejected.append(exc.code)
            return
        except Exception as exc:  # pragma: no cover - surfaced below
            with lock:
                rejected.append(repr(exc))
            return
        with lock:
            accepted.append(row)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert rejected == ["PT_CAPACITY"] * 7
    assert batch.capacity()["used"] == 1
    assert batch.stats()["emitted"] == 1
