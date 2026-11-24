import copy

import pytest

import render_frame_graph as fg


def graph(max_passes=64):
    return fg.FrameGraph(max_passes=max_passes)


def test_declaration_order_does_not_define_execution_order():
    builder = graph()
    builder.register_resource("depth", kind="texture", size=1024)
    builder.register_resource("color", kind="texture", size=2048)
    builder.add_pass("shade", reads=("depth",), writes=("color",))
    builder.add_pass("geometry", writes=("depth",))
    builder.mark_output("color")

    result = builder.compile()
    assert result["order"] == ["geometry", "shade"]
    assert result["culled"] == []
    assert result["versions"]["depth"] == 1
    assert result["versions"]["color"] == 1
    assert result["barriers"] == [
        {"resource": "depth", "pass": "geometry", "from": 0, "to": 1, "kind": "write"},
        {"resource": "depth", "pass": "shade", "from": 1, "to": 1, "kind": "read"},
        {"resource": "color", "pass": "shade", "from": 0, "to": 1, "kind": "write"},
    ]


def test_cycle_is_reported_and_graph_remains_usable():
    builder = graph()
    builder.register_resource("x")
    builder.register_resource("y")
    builder.add_pass("produce", reads=("y",), writes=("x",))
    builder.add_pass("consume", reads=("x",), writes=("y",))
    builder.mark_output("x")

    with pytest.raises(fg.FrameGraphError) as exc:
        builder.compile()
    assert exc.value.code == "FG_CYCLE"
    assert exc.value.detail == ["consume", "produce"]

    builder.remove_pass("consume")
    result = builder.compile()
    assert result["order"] == ["produce"]
    assert result["versions"] == {"x": 1, "y": 0}


def test_duplicate_names_are_rejected():
    builder = graph()
    builder.register_resource("color")
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.register_resource("color")
    assert exc.value.code == "FG_DUPLICATE_RESOURCE"

    builder.add_pass("paint", writes=("color",))
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.add_pass("paint", reads=("color",))
    assert exc.value.code == "FG_DUPLICATE_PASS"
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.add_pass("  ", reads=("color",))
    assert exc.value.code == "FG_INVALID_NAME"
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.remove_pass("absent")
    assert exc.value.code == "FG_UNKNOWN_PASS"


def test_unknown_resources_conflicts_and_bad_callbacks():
    builder = graph()
    builder.register_resource("color")
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.add_pass("paint", reads=("missing",))
    assert exc.value.code == "FG_UNKNOWN_RESOURCE"
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.add_pass("paint", reads=("color",), writes=("color",))
    assert exc.value.code == "FG_READ_WRITE_CONFLICT"
    assert exc.value.detail == ["color"]
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.add_pass("paint", writes=("color",), execute=42)
    assert exc.value.code == "FG_INVALID_CALLBACK"
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.mark_output("missing")
    assert exc.value.code == "FG_UNKNOWN_RESOURCE"


def test_write_creates_version_and_single_barrier():
    builder = graph()
    builder.register_resource("target", kind="texture", size=4096)
    builder.add_pass("clear", writes=("target",))
    builder.mark_output("target")

    result = builder.compile()
    assert result["order"] == ["clear"]
    assert result["versions"] == {"target": 1}
    assert result["barriers"] == [
        {"resource": "target", "pass": "clear", "from": 0, "to": 1, "kind": "write"},
    ]


def test_read_after_write_emits_transition_barrier():
    builder = graph()
    builder.register_resource("target", kind="texture")
    builder.register_resource("post", kind="texture")
    builder.add_pass("clear", writes=("target",))
    builder.add_pass("present", reads=("target",), writes=("post",))
    builder.mark_output("target", "post")

    result = builder.compile()
    assert result["order"] == ["clear", "present"]
    assert result["barriers"] == [
        {"resource": "target", "pass": "clear", "from": 0, "to": 1, "kind": "write"},
        {"resource": "target", "pass": "present", "from": 1, "to": 1, "kind": "read"},
        {"resource": "post", "pass": "present", "from": 0, "to": 1, "kind": "write"},
    ]
    assert result["versions"] == {"target": 1, "post": 1}


def test_unmarked_branch_is_culled():
    builder = graph()
    builder.register_resource("scene")
    builder.register_resource("debug")
    builder.add_pass("scene_pass", writes=("scene",))
    builder.add_pass("debug_pass", writes=("debug",))
    builder.mark_output("scene")

    result = builder.compile()
    assert result["order"] == ["scene_pass"]
    assert result["culled"] == ["debug_pass"]
    assert result["versions"] == {"scene": 1, "debug": 0}


def test_culling_keeps_transitive_producers_only():
    builder = graph()
    for name in ("depth", "color", "final", "overlay"):
        builder.register_resource(name, kind="texture")
    builder.add_pass("geometry", writes=("depth",))
    builder.add_pass("shade", reads=("depth",), writes=("color",))
    builder.add_pass("post", reads=("color",), writes=("final",))
    builder.add_pass("ui", writes=("overlay",))
    builder.mark_output("final")

    result = builder.compile()
    assert result["order"] == ["geometry", "shade", "post"]
    assert result["culled"] == ["ui"]
    assert result["barriers"] == [
        {"resource": "depth", "pass": "geometry", "from": 0, "to": 1, "kind": "write"},
        {"resource": "depth", "pass": "shade", "from": 1, "to": 1, "kind": "read"},
        {"resource": "color", "pass": "shade", "from": 0, "to": 1, "kind": "write"},
        {"resource": "color", "pass": "post", "from": 1, "to": 1, "kind": "read"},
        {"resource": "final", "pass": "post", "from": 0, "to": 1, "kind": "write"},
    ]


def test_empty_graph_and_missing_outputs_are_refused():
    builder = graph()
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.compile()
    assert exc.value.code == "FG_EMPTY_GRAPH"

    builder.register_resource("color")
    builder.add_pass("paint", writes=("color",))
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.compile()
    assert exc.value.code == "FG_NO_OUTPUT"

    builder.register_resource("orphan")
    builder.mark_output("orphan")
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.compile()
    assert exc.value.code == "FG_OUTPUT_UNPRODUCED"
    assert exc.value.detail == "orphan"


def test_pass_limit_is_enforced_and_freed_by_removal():
    builder = graph(max_passes=2)
    for name in ("a", "b", "c"):
        builder.register_resource(name)
    builder.add_pass("one", writes=("a",))
    builder.add_pass("two", writes=("b",))
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.add_pass("three", writes=("c",))
    assert exc.value.code == "FG_TOO_MANY_PASSES"

    builder.remove_pass("one")
    assert builder.add_pass("three", writes=("c",))["index"] == 1
    builder.mark_output("c")
    assert builder.compile()["order"] == ["three"]


def test_execute_runs_in_compiled_order_and_returns_results():
    builder = graph()
    builder.register_resource("depth")
    builder.register_resource("color")
    log = []

    def geometry(context):
        log.append(("geometry", context["frame"]))
        return "depth-written"

    def shade(context):
        log.append(("shade", context["frame"]))
        return "color-written"

    builder.add_pass("geometry", writes=("depth",), execute=geometry)
    builder.add_pass("shade", reads=("depth",), writes=("color",), execute=shade)
    builder.mark_output("color")
    builder.compile()

    results = builder.execute({"frame": 7})
    assert log == [("geometry", 7), ("shade", 7)]
    assert results == [
        {"pass": "geometry", "result": "depth-written"},
        {"pass": "shade", "result": "color-written"},
    ]
    assert builder.stats() == {"compiles": 1, "executions": 1,
                               "pass_runs": 2, "culled": 0}


def test_execute_requires_compilation_and_edits_invalidate_it():
    builder = graph()
    builder.register_resource("color")
    builder.register_resource("post")
    builder.add_pass("paint", writes=("color",))
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.execute()
    assert exc.value.code == "FG_NOT_COMPILED"

    builder.mark_output("color")
    builder.compile()
    assert builder.is_compiled() is True
    builder.add_pass("present", reads=("color",), writes=("post",))
    assert builder.is_compiled() is False
    with pytest.raises(fg.FrameGraphError) as exc:
        builder.execute()
    assert exc.value.code == "FG_NOT_COMPILED"

    builder.mark_output("post")
    assert builder.compile()["order"] == ["paint", "present"]


def test_callback_failure_is_wrapped_and_stops_execution():
    builder = graph()
    for name in ("a", "b", "c"):
        builder.register_resource(name)
    log = []

    def first(context):
        log.append("first")
        return 1

    def second(context):
        log.append("second")
        raise RuntimeError("boom")

    def third(context):
        log.append("third")
        return 3

    builder.add_pass("one", writes=("a",), execute=first)
    builder.add_pass("two", reads=("a",), writes=("b",), execute=second)
    builder.add_pass("three", reads=("b",), writes=("c",), execute=third)
    builder.mark_output("c")
    builder.compile()

    with pytest.raises(fg.FrameGraphError) as exc:
        builder.execute()
    assert exc.value.code == "FG_PASS_FAILED"
    assert exc.value.detail == {"pass": "two", "error": "boom"}
    assert log == ["first", "second"]
    assert builder.stats()["pass_runs"] == 2
    assert builder.stats()["executions"] == 0


def test_snapshot_restore_reproduces_topology_and_rejects_corruption():
    builder = graph()
    builder.register_resource("depth", kind="texture", size=512)
    builder.register_resource("color", kind="texture", size=1024)
    builder.add_pass("geometry", writes=("depth",))
    builder.add_pass("shade", reads=("depth",), writes=("color",))
    builder.mark_output("color")
    original = builder.compile()
    state = builder.snapshot()

    restored = graph()
    restored.restore(state)
    assert restored.is_compiled() is False
    rebuilt = restored.compile()
    assert rebuilt["order"] == original["order"]
    assert rebuilt["barriers"] == original["barriers"]
    assert rebuilt["versions"] == original["versions"]
    executed = restored.execute()
    assert [row["pass"] for row in executed] == ["geometry", "shade"]
    assert all(row["result"] is None for row in executed)

    victim = graph()
    victim.register_resource("a")
    victim.add_pass("only", writes=("a",))
    victim.mark_output("a")
    victim.compile()
    bad_states = [None, {}, {"version": 2}, {"version": 1, "max_passes": 1,
                                            "resources": "bad", "passes": [],
                                            "outputs": []}]
    broken = copy.deepcopy(state)
    broken["passes"][0]["writes"] = ["ghost"]
    bad_states.append(broken)
    broken = copy.deepcopy(state)
    broken["passes"].append(dict(broken["passes"][0]))
    bad_states.append(broken)
    for bad in bad_states:
        with pytest.raises(fg.FrameGraphError) as exc:
            victim.restore(bad)
        assert exc.value.code == "FG_SNAPSHOT_INVALID"
    assert victim.is_compiled() is True
    assert victim.execute()[0]["pass"] == "only"


def test_stats_are_cumulative_and_resettable():
    builder = graph()
    builder.register_resource("a")
    builder.register_resource("b")
    builder.add_pass("paint", writes=("a",))
    builder.add_pass("unused", writes=("b",))
    builder.mark_output("a")

    first = builder.compile()
    second = builder.compile()
    assert first == second
    assert builder.stats() == {"compiles": 2, "executions": 0,
                               "pass_runs": 0, "culled": 2}

    builder.execute()
    builder.execute()
    assert builder.stats() == {"compiles": 2, "executions": 2,
                               "pass_runs": 2, "culled": 2}
    builder.reset_stats()
    assert builder.stats() == {"compiles": 0, "executions": 0,
                               "pass_runs": 0, "culled": 0}
    builder.compile()
    assert builder.stats()["compiles"] == 1
    assert builder.stats()["culled"] == 1
