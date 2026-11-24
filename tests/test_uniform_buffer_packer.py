import copy
import threading

import pytest

import uniform_buffer_packer as ub


def packer(**kwargs):
    return ub.UniformBufferPacker(**kwargs)


def vec4_material(p, name):
    return p.define_material(name, params=[("color", "vec4", 1)])


def test_layout_uses_std140_style_alignment():
    p = packer()
    layout = p.define_material("surface", params=[
        ("tint", "vec4", 1),
        ("roughness", "float", 1),
        ("weights", "float", 4),
        ("transform", "mat4", 1),
    ])
    assert layout == {
        "material": "surface",
        "size": 160,
        "params": [
            {"name": "tint", "type": "vec4", "count": 1, "offset": 0},
            {"name": "roughness", "type": "float", "count": 1, "offset": 16},
            {"name": "weights", "type": "float", "count": 4, "offset": 32},
            {"name": "transform", "type": "mat4", "count": 1, "offset": 96},
        ],
        "offsets": {"tint": 0, "roughness": 16, "weights": 32,
                    "transform": 96},
    }
    assert p.layout("surface") == layout


def test_material_definitions_are_validated_and_idempotent():
    p = packer()
    first = p.define_material("m", params=[("x", "vec4", 1)])
    assert p.define_material("m", params=[("x", "vec4", 1)]) == first
    with pytest.raises(ub.PackError) as exc:
        p.define_material("m", params=[("x", "vec3", 1)])
    assert exc.value.code == "PACK_MATERIAL_CONFLICT"
    with pytest.raises(ub.PackError) as exc:
        p.define_material("bad", params=[("x", "quat", 1)])
    assert exc.value.code == "PACK_UNSUPPORTED_TYPE"
    with pytest.raises(ub.PackError) as exc:
        p.define_material("bad", params=[])
    assert exc.value.code == "PACK_INVALID_LAYOUT"
    with pytest.raises(ub.PackError) as exc:
        p.define_material("bad", params=[("x", "vec4", 1),
                                         ("x", "float", 1)])
    assert exc.value.code == "PACK_DUPLICATE_PARAM"
    with pytest.raises(ub.PackError) as exc:
        p.define_material("bad", params=[("x", "vec4", 0)])
    assert exc.value.code == "PACK_INVALID_LAYOUT"
    with pytest.raises(ub.PackError) as exc:
        p.define_material("bad", params=[("x", "vec4")])
    assert exc.value.code == "PACK_INVALID_LAYOUT"
    with pytest.raises(ub.PackError) as exc:
        p.define_material("", params=[("x", "vec4", 1)])
    assert exc.value.code == "PACK_INVALID_LAYOUT"
    with pytest.raises(ub.PackError) as exc:
        p.layout("ghost")
    assert exc.value.code == "PACK_UNKNOWN_MATERIAL"


def test_values_are_checked_against_the_declared_shape():
    p = packer()
    p.define_material("m", params=[("v", "vec4", 1), ("k", "int", 1),
                                   ("w", "float", 2)])
    with pytest.raises(ub.PackError) as exc:
        p.set_values("ghost", {"v": [0, 0, 0, 0]}, frame=0)
    assert exc.value.code == "PACK_UNKNOWN_MATERIAL"
    with pytest.raises(ub.PackError) as exc:
        p.set_values("m", [1, 2, 3], frame=0)
    assert exc.value.code == "PACK_VALUE_SHAPE"
    with pytest.raises(ub.PackError) as exc:
        p.set_values("m", {"v": [0, 0, 0, 0], "w": [0, 0]}, frame=0)
    assert exc.value.code == "PACK_MISSING_PARAM"
    with pytest.raises(ub.PackError) as exc:
        p.set_values("m", {"v": [0, 0, 0, 0], "k": 1, "w": [0, 0],
                           "z": 1}, frame=0)
    assert exc.value.code == "PACK_UNKNOWN_PARAM"
    for bad in (
        {"v": [0, 0, 0], "k": 1, "w": [0, 0]},
        {"v": [0, 0, 0, 0], "k": 1.5, "w": [0, 0]},
        {"v": [0, 0, 0, 0], "k": True, "w": [0, 0]},
        {"v": [0, 0, 0, 0], "k": 1, "w": 0.0},
    ):
        with pytest.raises(ub.PackError) as exc:
            p.set_values("m", bad, frame=0)
        assert exc.value.code == "PACK_VALUE_SHAPE"
    with pytest.raises(ub.PackError) as exc:
        p.set_values("m", {"v": [0, 0, 0, 0], "k": 1, "w": [0, 0]},
                     frame=-1)
    assert exc.value.code == "PACK_INVALID_FRAME"


def test_value_updates_track_versions_and_dirty_flags():
    p = packer()
    p.define_material("m", params=[("v", "vec4", 1), ("k", "int", 1)])
    assert p.set_values("m", {"v": [1, 2, 3, 4], "k": 2}, frame=1) == {
        "material": "m", "changed": True, "version": 1, "dirty": True,
    }
    assert p.set_values("m", {"v": (1.0, 2.0, 3.0, 4.0), "k": 2},
                        frame=2) == {
        "material": "m", "changed": False, "version": 1, "dirty": True,
    }
    p.pack(frame=3)
    assert p.set_values("m", {"v": [1, 2, 3, 4], "k": 3}, frame=4) == {
        "material": "m", "changed": True, "version": 2, "dirty": True,
    }
    assert p.set_values("m", {"v": [1, 2, 3, 4], "k": 3}, frame=5) == {
        "material": "m", "changed": False, "version": 2, "dirty": True,
    }


def test_pack_allocates_aligned_offsets_in_material_order():
    p = packer()
    p.define_material("zeta", params=[("c", "vec4", 1)])
    p.define_material("alpha", params=[("m", "mat4", 1)])
    p.set_values("zeta", {"c": [0, 0, 0, 1]}, frame=0)
    p.set_values("alpha", {"m": [0] * 16}, frame=0)
    out = p.pack(frame=0)
    assert out["writes"] == [
        {"material": "alpha", "offset": 0, "size": 64},
        {"material": "zeta", "offset": 64, "size": 16},
    ]
    assert out["slots"] == 2
    assert out["used_bytes"] == 80
    assert out["deduplicated"] == []


def test_identical_payloads_share_one_block_and_split_on_divergence():
    p = packer()
    vec4_material(p, "tint_a")
    vec4_material(p, "tint_b")
    p.set_values("tint_a", {"color": [1, 0, 0, 1]}, frame=0)
    p.set_values("tint_b", {"color": [1, 0, 0, 1]}, frame=0)
    out = p.pack(frame=0)
    assert out["writes"] == [{"material": "tint_a", "offset": 0,
                              "size": 16}]
    assert out["deduplicated"] == ["tint_b"]
    assert out["slots"] == 1
    assert out["used_bytes"] == 16
    assert p.slot_of("tint_b") == {"material": "tint_b", "offset": 0,
                                   "size": 16, "version": 1}

    p.set_values("tint_b", {"color": [0, 1, 0, 1]}, frame=0)
    out = p.pack(frame=0)
    assert out["writes"] == [{"material": "tint_b", "offset": 16,
                              "size": 16}]
    assert out["slots"] == 2
    assert out["used_bytes"] == 32
    assert p.slot_of("tint_a")["offset"] == 0


def test_packing_without_changes_writes_nothing():
    p = packer()
    vec4_material(p, "m")
    p.set_values("m", {"color": [0, 0, 0, 1]}, frame=0)
    first = p.pack(frame=0)
    assert first["writes"] == [{"material": "m", "offset": 0,
                                "size": 16}]
    second = p.pack(frame=1)
    assert second == {"frame": 1, "writes": [], "deduplicated": [],
                      "slots": 1, "used_bytes": 16}
    assert p.stats() == {"packs": 2, "uploads": 1, "deduplicated": 0,
                         "evicted": 0, "limit_errors": 0}
    assert p.slot_of("m")["offset"] == 0


def test_capacity_exhaustion_aborts_atomically_and_recovers_after_gc():
    p = packer(capacity=32)
    for name in ("a", "b", "c"):
        vec4_material(p, name)
    p.set_values("a", {"color": [1, 0, 0, 0]}, frame=0)
    p.set_values("b", {"color": [0, 1, 0, 0]}, frame=0)
    p.pack(frame=0)
    p.set_values("c", {"color": [0, 0, 1, 0]}, frame=0)
    with pytest.raises(ub.PackError) as exc:
        p.pack(frame=0)
    assert exc.value.code == "PACK_CAPACITY"
    assert p.slot_of("c") is None
    assert p.stats()["limit_errors"] == 1

    assert p.release("b") is True
    assert p.gc(frame=0) == 0
    assert p.slot_of("a")["offset"] == 0
    out = p.pack(frame=0)
    assert out["writes"] == [{"material": "c", "offset": 16,
                              "size": 16}]
    assert out["used_bytes"] == 32


def test_slot_limit_aborts_and_dedupe_recovers():
    p = packer(max_slots=1)
    vec4_material(p, "a")
    vec4_material(p, "b")
    p.set_values("a", {"color": [1, 0, 0, 0]}, frame=0)
    p.set_values("b", {"color": [0, 1, 0, 0]}, frame=0)
    with pytest.raises(ub.PackError) as exc:
        p.pack(frame=0)
    assert exc.value.code == "PACK_SLOT_LIMIT"
    assert p.slot_of("a") is None
    assert p.stats()["limit_errors"] == 1

    p.set_values("b", {"color": [1, 0, 0, 0]}, frame=1)
    out = p.pack(frame=1)
    assert out["writes"] == [{"material": "a", "offset": 0, "size": 16}]
    assert out["deduplicated"] == ["b"]
    assert out["slots"] == 1


def test_release_drops_the_block_but_keeps_the_hole_until_gc():
    p = packer()
    vec4_material(p, "m")
    p.set_values("m", {"color": [1, 1, 1, 1]}, frame=0)
    p.pack(frame=0)
    assert p.slot_of("m") == {"material": "m", "offset": 0, "size": 16,
                              "version": 1}
    assert p.release("m") is True
    assert p.release("m") is False
    assert p.slot_of("m") is None

    out = p.pack(frame=0)
    assert out["slots"] == 0
    assert out["used_bytes"] == 16
    assert p.gc(frame=0) == 0
    assert p.pack(frame=0)["used_bytes"] == 0

    again = p.set_values("m", {"color": [1, 1, 1, 1]}, frame=1)
    assert again == {"material": "m", "changed": True, "version": 1,
                     "dirty": True}
    out = p.pack(frame=1)
    assert out["writes"] == [{"material": "m", "offset": 0, "size": 16}]
    with pytest.raises(ub.PackError) as exc:
        p.release("ghost")
    assert exc.value.code == "PACK_UNKNOWN_MATERIAL"
    with pytest.raises(ub.PackError) as exc:
        p.slot_of("ghost")
    assert exc.value.code == "PACK_UNKNOWN_MATERIAL"


def test_gc_evicts_only_past_the_retention_window_and_compacts():
    p = packer(retention_frames=2)
    for name in ("a", "b", "c"):
        vec4_material(p, name)
    p.set_values("a", {"color": [1, 0, 0, 0]}, frame=1)
    p.set_values("b", {"color": [0, 1, 0, 0]}, frame=1)
    p.set_values("c", {"color": [0, 0, 1, 0]}, frame=5)
    p.pack(frame=5)
    assert p.slot_of("a")["offset"] == 0
    assert p.slot_of("c")["offset"] == 32

    assert p.gc(frame=3) == 0
    assert p.slot_of("a") is not None
    assert p.gc(frame=4) == 2
    assert p.slot_of("a") is None
    assert p.slot_of("b") is None
    assert p.slot_of("c")["offset"] == 0
    assert p.pack(frame=5)["used_bytes"] == 16
    assert p.stats()["evicted"] == 2


def test_evicted_material_is_uploaded_again_from_version_one():
    p = packer(retention_frames=1)
    vec4_material(p, "m")
    p.set_values("m", {"color": [0, 0, 0, 1]}, frame=1)
    p.pack(frame=1)
    assert p.gc(frame=3) == 1
    assert p.slot_of("m") is None

    again = p.set_values("m", {"color": [2, 2, 2, 2]}, frame=4)
    assert again == {"material": "m", "changed": True, "version": 1,
                     "dirty": True}
    out = p.pack(frame=4)
    assert out["writes"] == [{"material": "m", "offset": 0, "size": 16}]
    assert out["used_bytes"] == 16


def test_stats_count_uploads_dedupes_and_evictions():
    p = packer()
    vec4_material(p, "tint_a")
    vec4_material(p, "tint_b")
    p.set_values("tint_a", {"color": [1, 0, 0, 1]}, frame=0)
    p.set_values("tint_b", {"color": [1, 0, 0, 1]}, frame=0)
    p.pack(frame=0)
    p.pack(frame=1)
    assert p.stats() == {"packs": 2, "uploads": 1, "deduplicated": 1,
                         "evicted": 0, "limit_errors": 0}
    p.reset_stats()
    assert p.stats() == {"packs": 0, "uploads": 0, "deduplicated": 0,
                         "evicted": 0, "limit_errors": 0}
    assert p.gc(frame=10) == 2
    assert p.stats() == {"packs": 0, "uploads": 0, "deduplicated": 0,
                         "evicted": 2, "limit_errors": 0}


def test_snapshot_restore_keeps_offsets_values_and_stats():
    p = packer()
    vec4_material(p, "a")
    p.define_material("b", params=[("m", "mat4", 1)])
    p.set_values("a", {"color": [1, 0, 0, 1]}, frame=0)
    p.set_values("b", {"m": [0] * 16}, frame=0)
    p.pack(frame=0)
    p.set_values("a", {"color": [0, 0, 1, 1]}, frame=1)
    p.pack(frame=1)
    assert p.slot_of("a")["offset"] == 80
    assert p.slot_of("b")["offset"] == 16

    state = p.snapshot()
    clone = packer()
    clone.restore(state)
    assert clone.slot_of("a") == p.slot_of("a")
    assert clone.slot_of("b") == p.slot_of("b")
    assert clone.layout("b") == p.layout("b")
    assert clone.stats() == p.stats()

    assert clone.set_values("b", {"m": [1] * 16}, frame=2)["changed"] is True
    out = clone.pack(frame=2)
    assert out["writes"] == [{"material": "b", "offset": 96, "size": 64}]
    assert p.slot_of("b")["offset"] == 16

    state["materials"].clear()
    state["values"].clear()
    assert clone.layout("a")["size"] == 16
    assert clone.slot_of("a")["offset"] == 80

    good = clone.snapshot()
    for mutate in (
        lambda s: s.pop("blocks"),
        lambda s: s["assignments"].update({"a": 99}),
        lambda s: s["stats"].update({"packs": -1}),
        lambda s: s["blocks"][0].update({"offset": 3}),
        lambda s: s["values"]["a"].update({"values": [[1.0]]}),
    ):
        broken = copy.deepcopy(good)
        mutate(broken)
        with pytest.raises(ub.PackError) as exc:
            clone.restore(broken)
        assert exc.value.code == "PACK_SNAPSHOT_INVALID"
    assert clone.slot_of("a")["offset"] == 80


def test_concurrent_value_updates_are_not_lost():
    p = packer()
    for index in range(8):
        vec4_material(p, f"m{index}")
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        row = p.set_values(f"m{index}", {"color": [index, 0, 0, 1]},
                           frame=0)
        with lock:
            results.append(row)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert all(row["changed"] for row in results)
    out = p.pack(frame=0)
    assert out["slots"] == 8
    assert out["used_bytes"] == 128
    assert sorted(row["material"] for row in out["writes"]) == [
        f"m{i}" for i in range(8)]

    vec4_material(p, "shared")
    p.set_values("shared", {"color": [9, 9, 9, 9]}, frame=1)
    p.pack(frame=1)
    barrier = threading.Barrier(8)
    same = []

    def twin():
        barrier.wait()
        row = p.set_values("shared", {"color": [1, 1, 1, 1]}, frame=2)
        with lock:
            same.append(row)

    threads = [threading.Thread(target=twin) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(same) == 8
    assert sum(1 for row in same if row["changed"]) == 1
    assert all(row["version"] == 2 for row in same)
    assert p.slot_of("shared")["version"] == 2
