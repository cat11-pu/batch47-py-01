import threading

import pytest

import layer_unpack_guard as lg


def entry(name, type_="file", **extra):
    data = {"name": name, "type": type_}
    data.update(extra)
    return data


def test_resolve_path_rejects_absolute_and_parent_segments():
    guard = lg.LayerUnpackGuard()
    assert guard.resolve_path("a/b") == "a/b"
    assert guard.resolve_path("./a//b") == "a/b"
    cases = [
        ("/etc/passwd", "LAYER_PATH_ABSOLUTE"),
        ("../x", "LAYER_PATH_TRAVERSAL"),
        ("a/../../b", "LAYER_PATH_TRAVERSAL"),
        ("", "LAYER_PATH_INVALID"),
    ]
    for raw, code in cases:
        with pytest.raises(lg.LayerError) as exc:
            guard.resolve_path(raw)
        assert exc.value.code == code


def test_resolve_path_depth_limit():
    guard = lg.LayerUnpackGuard(max_path_depth=3)
    assert guard.resolve_path("a/b/c") == "a/b/c"
    assert guard.resolve_path("a/./b/c") == "a/b/c"
    with pytest.raises(lg.LayerError) as exc:
        guard.resolve_path("a/b/c/d")
    assert exc.value.code == "LAYER_PATH_TOO_DEEP"


def test_begin_layer_lifecycle_and_idempotent_replay():
    guard = lg.LayerUnpackGuard()
    assert guard.begin_layer("base", digest="sha256:aaa")["status"] == "staging"
    with pytest.raises(lg.LayerError) as exc:
        guard.begin_layer("base")
    assert exc.value.code == "LAYER_DUPLICATE_LAYER"

    guard.add_entry("base", entry("etc/app.conf", size=12))
    result = guard.commit_layer("base")
    assert result["status"] == "applied"
    assert result["written"] == 1
    assert result["removed"] == 0
    assert guard.list_files() == ["etc/app.conf"]
    assert guard.lookup("etc/app.conf")["size"] == 12
    assert guard.stats()["layers_applied"] == 1

    again = guard.begin_layer("base", digest="sha256:aaa")
    assert again["status"] == "already_applied"
    with pytest.raises(lg.LayerError) as exc:
        guard.begin_layer("base", digest="sha256:bbb")
    assert exc.value.code == "LAYER_DUPLICATE_LAYER"
    with pytest.raises(lg.LayerError) as exc:
        guard.abort_layer("base")
    assert exc.value.code == "LAYER_ALREADY_COMMITTED"


def test_symlink_absolute_and_escaping_targets_refused():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("links")
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("links", entry("etc-link", "symlink", target="/etc"))
    assert exc.value.code == "LAYER_SYMLINK_ESCAPE"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("links", entry("a/up", "symlink", target="../../x"))
    assert exc.value.code == "LAYER_SYMLINK_ESCAPE"

    assert guard.add_entry("links", entry("a/link", "symlink", target="../b"))["status"] == "staged"
    assert guard.add_entry("links", entry("a/rel", "symlink", target="sub/f"))["status"] == "staged"
    assert guard.layer_state("links")["entries"] == 2


def test_symlink_loop_is_detected():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("loop")
    guard.add_entry("loop", entry("l1", "symlink", target="l2"))
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("loop", entry("l2", "symlink", target="l1"))
    assert exc.value.code == "LAYER_SYMLINK_LOOP"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("loop", entry("self", "symlink", target="self"))
    assert exc.value.code == "LAYER_SYMLINK_LOOP"


def test_hardlink_requires_existing_regular_file_in_layer():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("base")
    guard.add_entry("base", entry("bin/tool", size=5, mode=0o755))
    guard.add_entry("base", entry("bin/link", "symlink", target="tool"))
    guard.commit_layer("base")

    guard.begin_layer("top")
    assert guard.add_entry("top", entry("bin/tool2", "hardlink", target="bin/tool"))["status"] == "staged"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("top", entry("bin/missing", "hardlink", target="bin/nope"))
    assert exc.value.code == "LAYER_HARDLINK_MISSING"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("top", entry("bin/dirlink", "hardlink", target="bin"))
    assert exc.value.code == "LAYER_HARDLINK_INVALID"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("top", entry("bin/symlink2", "hardlink", target="bin/link"))
    assert exc.value.code == "LAYER_HARDLINK_INVALID"

    guard.add_entry("top", entry("stage-src", size=2))
    assert guard.add_entry("top", entry("stage-link", "hardlink", target="stage-src"))["status"] == "staged"
    result = guard.commit_layer("top")
    assert result["written"] == 3
    assert guard.lookup("bin/tool2")["type"] == "file"
    assert guard.lookup("bin/tool2")["size"] == 5


def test_unsupported_entry_types_and_filesystem_nodes_refused():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("bad")
    for kind in ("device", "fifo", "char"):
        with pytest.raises(lg.LayerError) as exc:
            guard.add_entry("bad", entry("nodes/x", kind))
        assert exc.value.code == "LAYER_UNSUPPORTED_TYPE"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("bad", {"name": "plain"})
    assert exc.value.code == "LAYER_ENTRY_INVALID"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("bad", {"name": 42, "type": "file"})
    assert exc.value.code == "LAYER_ENTRY_INVALID"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("bad", "not-a-dict")
    assert exc.value.code == "LAYER_ENTRY_INVALID"


def test_setuid_and_setgid_modes_refused():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("modes")
    for mode in (0o4755, 0o2755, 0o1777):
        with pytest.raises(lg.LayerError) as exc:
            guard.add_entry("modes", entry("priv/bin", mode=mode))
        assert exc.value.code == "LAYER_UNSAFE_MODE"
    assert guard.layer_state("modes")["entries"] == 0
    guard.add_entry("modes", entry("safe/bin", mode=0o755))
    assert guard.layer_state("modes")["entries"] == 1


def test_entry_and_byte_quotas_do_not_leak_on_failure():
    guard = lg.LayerUnpackGuard(max_entries=2, max_bytes=10)
    guard.begin_layer("counted")
    guard.add_entry("counted", entry("f1", size=4))
    guard.add_entry("counted", entry("f2", size=4))
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("counted", entry("f3", size=1))
    assert exc.value.code == "LAYER_ENTRY_LIMIT"
    guard.abort_layer("counted")

    guard.begin_layer("sized")
    guard.add_entry("sized", entry("g1", size=6))
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("sized", entry("g2", size=5))
    assert exc.value.code == "LAYER_SIZE_LIMIT"
    guard.add_entry("sized", entry("g3", size=4))

    stats = guard.stats()
    assert stats["entries_rejected"] == 2
    assert stats["layers_aborted"] == 1
    assert stats["bytes_written"] == 0


def test_whiteout_removes_lower_layer_entry():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("base")
    guard.add_entry("base", entry("conf", "dir", mode=0o755))
    guard.add_entry("base", entry("conf/app.conf", size=10))
    guard.add_entry("base", entry("conf/keep.conf", size=3))
    assert guard.commit_layer("base")["written"] == 3

    guard.begin_layer("patch")
    guard.add_entry("patch", entry("conf/.wh.app.conf", "whiteout"))
    result = guard.commit_layer("patch")
    assert result["written"] == 0
    assert result["removed"] == 1
    assert guard.list_files() == ["conf/keep.conf"]
    assert guard.lookup("conf/app.conf") is None
    assert guard.lookup("conf/.wh.app.conf") is None
    assert guard.stats()["whiteouts_applied"] == 1


def test_opaque_whiteout_clears_directory():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("base")
    guard.add_entry("base", entry("var", "dir", mode=0o755))
    guard.add_entry("base", entry("var/a", size=1))
    guard.add_entry("base", entry("var/b", size=1))
    guard.add_entry("base", entry("var/sub", "dir", mode=0o755))
    guard.add_entry("base", entry("var/sub/c", size=1))
    guard.add_entry("base", entry("other", "dir", mode=0o755))
    guard.add_entry("base", entry("other/x", size=1))
    assert guard.commit_layer("base")["written"] == 7

    guard.begin_layer("clean")
    guard.add_entry("clean", entry("var/.wh..wh..opq", "whiteout"))
    result = guard.commit_layer("clean")
    assert result["removed"] == 4
    assert guard.list_files() == ["other/x"]
    assert guard.lookup("var")["type"] == "dir"
    assert guard.lookup("var/sub") is None


def test_duplicate_and_ancestor_conflicts_in_layer():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("dup")
    guard.add_entry("dup", entry("a/b", size=1))
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("dup", entry("a/b", size=2))
    assert exc.value.code == "LAYER_DUPLICATE_ENTRY"
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("dup", entry("a", size=1))
    assert exc.value.code == "LAYER_PATH_CONFLICT"
    guard.add_entry("dup", entry("x", size=1))
    with pytest.raises(lg.LayerError) as exc:
        guard.add_entry("dup", entry("x/y", size=1))
    assert exc.value.code == "LAYER_PARENT_NOT_DIR"


def test_abort_discards_staging_without_touching_view():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("base")
    guard.add_entry("base", entry("keep.txt", size=7))
    guard.commit_layer("base")

    guard.begin_layer("tmp")
    guard.add_entry("tmp", entry("drop.txt", size=7))
    assert guard.abort_layer("tmp") is True
    assert guard.list_files() == ["keep.txt"]
    with pytest.raises(lg.LayerError) as exc:
        guard.layer_state("tmp")
    assert exc.value.code == "LAYER_UNKNOWN_LAYER"
    assert guard.stats()["layers_aborted"] == 1


def test_snapshot_restore_and_corrupt_state():
    guard = lg.LayerUnpackGuard()
    guard.begin_layer("base")
    guard.add_entry("base", entry("a.txt", size=4))
    guard.commit_layer("base")
    state = guard.snapshot()

    guard.begin_layer("second")
    guard.add_entry("second", entry("b.txt", size=2))
    guard.commit_layer("second")

    restored = lg.LayerUnpackGuard()
    restored.restore(state)
    assert restored.list_files() == ["a.txt"]
    assert restored.stats()["layers_applied"] == 1
    assert restored.stats()["bytes_written"] == 4

    restored.begin_layer("third")
    restored.add_entry("third", entry("c.txt", size=1))
    restored.commit_layer("third")
    assert restored.list_files() == ["a.txt", "c.txt"]

    bad_states = [
        None,
        {},
        {"version": 2, "view": {}, "layers": {}, "stats": {}},
        {"version": 1, "view": [], "layers": {}, "stats": {}},
        {"version": 1, "view": {}, "layers": {}, "stats": "nope"},
    ]
    for bad in bad_states:
        with pytest.raises(lg.LayerError) as exc:
            restored.restore(bad)
        assert exc.value.code == "LAYER_SNAPSHOT_INVALID"
    assert restored.list_files() == ["a.txt", "c.txt"]


def test_concurrent_layer_commits_keep_all_entries():
    guard = lg.LayerUnpackGuard()
    barrier = threading.Barrier(8)
    errors = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            layer_id = "layer-%d" % index
            guard.begin_layer(layer_id)
            guard.add_entry(layer_id, entry("d%d/f%d" % (index, index), size=index + 1))
            guard.commit_layer(layer_id)
        except Exception as exc:
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert guard.list_files() == sorted("d%d/f%d" % (i, i) for i in range(8))
    assert guard.stats()["layers_applied"] == 8
    assert guard.stats()["bytes_written"] == sum(i + 1 for i in range(8))
