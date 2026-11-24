import threading

import pytest

import render_graph_barrier_planner as rp


def planner(**kwargs):
    return rp.RenderGraphBarrierPlanner(**kwargs)


def simple_graph(**kwargs):
    graph = planner(**kwargs)
    graph.register_resource("color", size=16)
    graph.register_resource("depth", size=8)
    graph.register_resource("lit", size=4)
    graph.register_resource("final", imported=True, size=4)
    graph.add_pass("gbuffer", writes=[("color", "render_target"),
                                      ("depth", "depth_write")])
    graph.add_pass("shade", reads=[("color", "shader_read"),
                                   ("depth", "shader_read")],
                   writes=[("lit", "render_target")])
    graph.add_pass("tonemap", reads=[("lit", "shader_read")],
                   writes=[("final", "present")])
    graph.mark_output("final")
    return graph


def alias_graph(**kwargs):
    graph = planner(**kwargs)
    graph.register_resource("t1", size=4)
    graph.register_resource("t2", size=8)
    graph.register_resource("t3", size=2)
    graph.add_pass("p0", writes=[("t1", "render_target")])
    graph.add_pass("p1", reads=[("t1", "shader_read")],
                   writes=[("t2", "render_target")])
    graph.add_pass("p2", reads=[("t2", "shader_read")],
                   writes=[("t3", "render_target")])
    graph.mark_output("t3")
    return graph


def test_linear_chain_orders_passes_and_emits_barriers():
    plan = simple_graph().compile()
    assert plan["passes"] == ["gbuffer", "shade", "tonemap"]
    assert plan["barriers"]["gbuffer"] == [
        {"resource": "color", "from": "undefined", "to": "render_target"},
        {"resource": "depth", "from": "undefined", "to": "depth_write"},
    ]
    assert plan["barriers"]["shade"] == [
        {"resource": "color", "from": "render_target", "to": "shader_read"},
        {"resource": "depth", "from": "depth_write", "to": "shader_read"},
        {"resource": "lit", "from": "undefined", "to": "render_target"},
    ]
    assert plan["barriers"]["tonemap"] == [
        {"resource": "lit", "from": "render_target", "to": "shader_read"},
        {"resource": "final", "from": "undefined", "to": "present"},
    ]
    assert plan["barrier_count"] == 7
    assert plan["culled"] == []
    assert plan["lifetimes"] == {
        "color": {"first": 0, "last": 1},
        "depth": {"first": 0, "last": 1},
        "lit": {"first": 1, "last": 2},
    }


def test_repeated_reads_need_no_barrier_but_state_changes_do():
    graph = planner()
    graph.register_resource("hdr", size=8)
    graph.register_resource("out", imported=True, size=4)
    graph.add_pass("a", writes=[("hdr", "render_target")])
    graph.add_pass("b", reads=[("hdr", "shader_read")],
                   writes=[("out", "present")])
    graph.add_pass("c", reads=[("hdr", "shader_read")], side_effect=True)
    graph.add_pass("d", reads=[("hdr", "copy_src")], side_effect=True)
    graph.mark_output("out")
    plan = graph.compile()
    assert plan["passes"] == ["a", "b", "c", "d"]
    assert plan["barriers"]["b"] == [
        {"resource": "hdr", "from": "render_target", "to": "shader_read"},
        {"resource": "out", "from": "undefined", "to": "present"},
    ]
    assert plan["barriers"]["c"] == []
    assert plan["barriers"]["d"] == [
        {"resource": "hdr", "from": "shader_read", "to": "copy_src"},
    ]
    assert plan["barrier_count"] == 4


def test_declaration_errors_carry_stable_codes():
    graph = planner()
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.add_pass("ghost", reads=[("missing", "shader_read")])
    assert exc.value.code == "GRAPH_UNKNOWN_RESOURCE"

    graph.register_resource("r", size=4)
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.register_resource("r", size=4)
    assert exc.value.code == "GRAPH_DUPLICATE_RESOURCE"
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.register_resource("zero", size=0)
    assert exc.value.code == "GRAPH_INVALID_RESOURCE"
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.register_resource("mesh", kind="mesh")
    assert exc.value.code == "GRAPH_INVALID_RESOURCE"

    graph.add_pass("a", writes=[("r", "render_target")])
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.add_pass("a", side_effect=True)
    assert exc.value.code == "GRAPH_DUPLICATE_PASS"
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.add_pass("b", writes=[("r", "shader_read")])
    assert exc.value.code == "GRAPH_MULTIPLE_WRITERS"

    graph.register_resource("t", size=4)
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.add_pass("c", reads=[("t", "shader_read")],
                       writes=[("t", "present")])
    assert exc.value.code == "GRAPH_DUPLICATE_ACCESS"

    graph.register_resource("u", size=4)
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.add_pass("d", writes=[("u", "bogus_state")])
    assert exc.value.code == "GRAPH_UNSUPPORTED_STATE"


def test_reads_without_writer_are_rejected_unless_imported():
    graph = planner()
    graph.register_resource("albedo", size=4)
    graph.add_pass("sample", reads=[("albedo", "shader_read")],
                   side_effect=True)
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.compile()
    assert exc.value.code == "GRAPH_MISSING_WRITER"

    other = planner()
    other.register_resource("sky", imported=True)
    other.register_resource("out", imported=True)
    other.add_pass("sky_pass", reads=[("sky", "shader_read")],
                   writes=[("out", "present")])
    other.mark_output("out")
    assert other.compile()["passes"] == ["sky_pass"]


def test_dependency_cycles_are_reported_and_repairable():
    graph = planner()
    graph.register_resource("a", size=4)
    graph.register_resource("b", size=4)
    graph.register_resource("src", imported=True)
    graph.add_pass("pa", reads=[("b", "shader_read")],
                   writes=[("a", "render_target")])
    graph.add_pass("pb", reads=[("a", "shader_read")],
                   writes=[("b", "render_target")])
    graph.mark_output("a")
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.compile()
    assert exc.value.code == "GRAPH_CYCLE"

    graph.remove_pass("pb")
    graph.add_pass("pc", reads=[("src", "shader_read")],
                   writes=[("b", "render_target")])
    plan = graph.compile()
    assert plan["passes"] == ["pc", "pa"]
    assert plan["barriers"]["pc"] == [
        {"resource": "src", "from": "undefined", "to": "shader_read"},
        {"resource": "b", "from": "undefined", "to": "render_target"},
    ]
    assert plan["barriers"]["pa"] == [
        {"resource": "b", "from": "render_target", "to": "shader_read"},
        {"resource": "a", "from": "undefined", "to": "render_target"},
    ]


def test_unused_passes_are_culled_without_barriers():
    graph = planner()
    graph.register_resource("shadow", size=4)
    graph.register_resource("scene", imported=True, size=4)
    graph.register_resource("scratch", size=4)
    graph.add_pass("shadow_pass", writes=[("shadow", "render_target")])
    graph.add_pass("main", reads=[("shadow", "shader_read")],
                   writes=[("scene", "present")])
    graph.add_pass("debug", writes=[("scratch", "render_target")])
    graph.mark_output("scene")
    plan = graph.compile()
    assert plan["passes"] == ["shadow_pass", "main"]
    assert plan["culled"] == ["debug"]
    assert "debug" not in plan["barriers"]
    assert plan["barrier_count"] == 3
    assert graph.stats()["passes_culled"] == 1


def test_dead_chains_collapse_while_side_effects_survive():
    graph = planner()
    graph.register_resource("tmp_a", size=4)
    graph.register_resource("tmp_b", size=4)
    graph.register_resource("probe", size=4)
    graph.add_pass("first", writes=[("tmp_a", "render_target")])
    graph.add_pass("second", reads=[("tmp_a", "shader_read")],
                   writes=[("tmp_b", "render_target")])
    graph.add_pass("probe_pass", writes=[("probe", "render_target")],
                   side_effect=True)
    plan = graph.compile()
    assert plan["passes"] == ["probe_pass"]
    assert plan["culled"] == ["first", "second"]
    assert plan["barrier_count"] == 1


def test_aliasing_reuses_slots_for_disjoint_lifetimes():
    plan = alias_graph().compile()
    assert plan["lifetimes"] == {
        "t1": {"first": 0, "last": 1},
        "t2": {"first": 1, "last": 2},
        "t3": {"first": 2, "last": 2},
    }
    assert plan["aliases"] == [
        {"slot": 0, "resources": ["t1", "t3"], "size": 4},
        {"slot": 1, "resources": ["t2"], "size": 8},
    ]
    assert plan["peak_memory"] == 12


def test_memory_budget_is_enforced_after_aliasing():
    with pytest.raises(rp.RenderGraphError) as exc:
        alias_graph(memory_budget=11).compile()
    assert exc.value.code == "GRAPH_MEMORY_BUDGET"
    plan = alias_graph(memory_budget=12).compile()
    assert plan["peak_memory"] == 12


def test_per_pass_barrier_budget_is_enforced():
    graph = planner(max_barriers_per_pass=2)
    for name in ("x", "y", "z"):
        graph.register_resource(name, size=4)
    graph.add_pass("heavy", writes=[("x", "render_target"),
                                    ("y", "render_target"),
                                    ("z", "render_target")],
                   side_effect=True)
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.compile()
    assert exc.value.code == "GRAPH_BARRIER_BUDGET"

    relaxed = planner(max_barriers_per_pass=3)
    for name in ("x", "y", "z"):
        relaxed.register_resource(name, size=4)
    relaxed.add_pass("heavy", writes=[("x", "render_target"),
                                      ("y", "render_target"),
                                      ("z", "render_target")],
                     side_effect=True)
    assert relaxed.compile()["barrier_count"] == 3


def test_compile_is_idempotent_and_counted_once():
    graph = planner()
    graph.register_resource("r", size=4)
    graph.register_resource("q", size=4)
    graph.add_pass("a", writes=[("r", "render_target")], side_effect=True)
    first = graph.compile()
    second = graph.compile()
    assert first == second
    assert first is not second
    assert graph.stats() == {"compiles": 1, "passes_culled": 0,
                             "barriers_emitted": 1}

    graph.add_pass("b", writes=[("q", "render_target")], side_effect=True)
    graph.compile()
    assert graph.stats() == {"compiles": 2, "passes_culled": 0,
                             "barriers_emitted": 3}


def test_barriers_for_returns_defensive_copies_and_rejects_unknown():
    graph = alias_graph()
    graph.register_resource("junk", size=4)
    graph.add_pass("dead", writes=[("junk", "render_target")])
    graph.compile()
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.barriers_for("missing")
    assert exc.value.code == "GRAPH_UNKNOWN_PASS"
    with pytest.raises(rp.RenderGraphError) as exc:
        graph.barriers_for("dead")
    assert exc.value.code == "GRAPH_UNKNOWN_PASS"

    rows = graph.barriers_for("p0")
    rows.append({"resource": "injected", "from": "undefined", "to": "present"})
    assert graph.barriers_for("p0") == [
        {"resource": "t1", "from": "undefined", "to": "render_target"},
    ]


def test_snapshot_restore_roundtrip_keeps_plan_and_stats():
    graph = simple_graph()
    before = graph.compile()
    state = graph.snapshot()
    graph.register_resource("extra", size=4)
    graph.add_pass("late", writes=[("extra", "render_target")],
                   side_effect=True)
    assert graph.compile()["passes"] == ["gbuffer", "shade", "tonemap", "late"]

    restored = planner()
    restored.restore(state)
    assert restored.stats() == state["stats"]
    assert restored.compile() == before

    state["passes"].clear()
    state["resources"].clear()
    assert restored.compile() == before
    assert restored.stats() == {"compiles": 2, "passes_culled": 0,
                                "barriers_emitted": 14}


def test_corrupt_snapshot_is_rejected_without_touching_state():
    graph = simple_graph()
    before = graph.compile()
    empty_stats = {"compiles": 0, "passes_culled": 0, "barriers_emitted": 0}
    bad_states = [
        None,
        "not-a-snapshot",
        {},
        {"version": 2, "resources": [], "passes": [], "outputs": [],
         "stats": dict(empty_stats)},
        {"version": 1, "resources": "nope", "passes": [], "outputs": [],
         "stats": dict(empty_stats)},
        {"version": 1, "resources": [], "passes": [
            {"pass_id": "a", "reads": [["ghost", "shader_read"]],
             "writes": [], "side_effect": False}], "outputs": [],
         "stats": dict(empty_stats)},
        {"version": 1, "resources": [
            {"name": "r", "kind": "texture", "size": 0, "imported": False}],
         "passes": [], "outputs": [], "stats": dict(empty_stats)},
        {"version": 1, "resources": [], "passes": [],
         "outputs": ["ghost"], "stats": dict(empty_stats)},
    ]
    for bad in bad_states:
        with pytest.raises(rp.RenderGraphError) as exc:
            graph.restore(bad)
        assert exc.value.code == "GRAPH_SNAPSHOT_INVALID"
    assert graph.stats() == {"compiles": 1, "passes_culled": 0,
                             "barriers_emitted": 7}
    assert graph.compile() == before

    fresh = planner()
    fresh.restore({"version": 1, "resources": [], "passes": [],
                   "outputs": [], "stats": dict(empty_stats)})
    assert fresh.compile()["passes"] == []


def test_concurrent_compile_is_consistent_then_reset_clears():
    graph = alias_graph()
    barrier = threading.Barrier(8)
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            plan = graph.compile()
        except Exception as exc:  # pragma: no cover - surfaced below
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            results.append(plan)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 8
    assert all(plan == results[0] for plan in results)
    assert graph.stats()["compiles"] == 1

    graph.reset_stats()
    assert graph.stats() == {"compiles": 0, "passes_culled": 0,
                             "barriers_emitted": 0}
    graph.reset()
    assert graph.compile()["passes"] == []
    assert graph.stats() == {"compiles": 1, "passes_culled": 0,
                             "barriers_emitted": 0}
