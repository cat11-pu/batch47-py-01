import threading

import pytest

import acl_inherit_guard as ag

BASIC = [("user", None, "rwx"), ("group", None, "r-x"), ("other", None, "---")]


def make(**kwargs):
    return ag.AclInheritGuard(**kwargs)


def test_root_acl_is_initialized_and_mode_is_computed_from_entries():
    guard = make()
    row = guard.get_access("/")
    assert row["path"] == "/"
    assert row["kind"] == "dir"
    assert row["owner"] == "root"
    assert row["group"] == "root"
    assert row["entries"] == [("user", None, "rwx"),
                             ("group", None, "r-x"),
                             ("other", None, "r-x")]
    assert row["mask"] is None
    assert row["mode"] == "rwxr-xr-x"


def test_access_acl_requires_base_entries_and_valid_syntax():
    guard = make()
    guard.ensure_path("/srv", kind="dir", owner="ops", group="ops",
                      mode=0o750)
    cases = [
        ([("user", None, "rwx"), ("group", None, "r-x")],
         "ACL_INCOMPLETE"),
        ([("user", None, "rwx"), ("group", None, "r-x"),
          ("other", None, "rwq")], "ACL_INVALID_PERMS"),
        ([("user", None, "rwx"), ("group", None, "r-x"),
          ("other", None, "---"), ("acl", None, "rwx")], "ACL_INVALID_TAG"),
        ([("user", None, "rwx"), ("user", "alice", "rw-"),
          ("user", "alice", "r--"), ("group", None, "r-x"),
          ("other", None, "---")], "ACL_DUPLICATE"),
    ]
    for entries, code in cases:
        with pytest.raises(ag.AclError) as exc:
            guard.set_access("/srv", entries=entries)
        assert exc.value.code == code
    assert guard.get_access("/srv")["mode"] == "rwxr-x---"
    assert guard.stats() == {"created": 1, "updated": 0, "removed": 0,
                             "rejected": 4}


def test_mask_is_derived_from_group_class_and_can_be_set_explicitly():
    guard = make()
    guard.ensure_path("/proj", kind="dir", owner="pm", group="dev",
                      mode=0o770)
    derived = guard.set_access("/proj", entries=[
        ("user", None, "rwx"),
        ("user", "alice", "rwx"),
        ("group", None, "r-x"),
        ("group", "qa", "rw-"),
        ("other", None, "---"),
    ])
    assert derived["mask"] == "rw-"
    assert derived["mode"] == "rwxr-----"
    assert guard.effective("/proj", user="alice") == "rw-"
    assert guard.effective("/proj", user="bob", groups=("qa",)) == "rw-"
    assert guard.effective("/proj", user="pm") == "rwx"
    assert guard.effective("/proj", user="nobody") == "---"

    explicit = guard.set_access("/proj", entries=[
        ("user", None, "rwx"),
        ("user", "alice", "rwx"),
        ("group", None, "r-x"),
        ("group", "qa", "rw-"),
        ("mask", None, "r--"),
        ("other", None, "---"),
    ])
    assert explicit["mask"] == "r--"
    assert guard.effective("/proj", user="alice") == "r--"
    assert guard.effective("/proj", user="bob", groups=("qa",)) == "r--"
    assert guard.effective("/proj", user="pm") == "rwx"


def test_effective_permission_follows_owner_user_group_other_order():
    guard = make()
    guard.ensure_path("/x", kind="dir", owner="u1", group="g1", mode=0o777)
    guard.set_access("/x", entries=[
        ("user", None, "rwx"),
        ("user", "alice", "r--"),
        ("group", None, "r-x"),
        ("group", "qa", "-w-"),
        ("mask", None, "r-x"),
        ("other", None, "---"),
    ])
    assert guard.effective("/x", user="u1") == "rwx"
    assert guard.effective("/x", user="alice") == "r--"
    assert guard.effective("/x", user="bob", groups=("g1",)) == "r-x"
    assert guard.effective("/x", user="carol", groups=("qa",)) == "r-x"
    assert guard.effective("/x", user="dave", groups=("g1", "qa")) == "r-x"
    assert guard.effective("/x", user="zed") == "---"


def test_default_acl_can_only_be_set_on_directories():
    guard = make()
    guard.ensure_path("/share", kind="dir", owner="root", group="root",
                      mode=0o755)
    assert guard.get_default("/share") is None
    guard.create_child("/share", "file.txt", kind="file", mode=0o644,
                       owner="root", group="root")
    with pytest.raises(ag.AclError) as exc:
        guard.set_default("/share/file.txt", BASIC)
    assert exc.value.code == "ACL_NOT_DIR"

    row = guard.set_default("/share", BASIC)
    assert row == {"path": "/share", "entries": BASIC}
    assert guard.get_default("/share") == BASIC


def test_creation_without_default_acl_applies_umask():
    guard = make(umask=0o027)
    guard.ensure_path("/data", kind="dir", owner="root", group="root",
                      mode=0o777)
    file_row = guard.create_child("/data", "report.txt", kind="file",
                                  mode=0o666, owner="u", group="g")
    assert file_row["entries"] == [("user", None, "rw-"),
                                   ("group", None, "r--"),
                                   ("other", None, "---")]
    assert file_row["mask"] is None

    dir_row = guard.create_child("/data", "logs", kind="dir", mode=0o777,
                                 owner="u", group="g")
    assert dir_row["entries"] == [("user", None, "rwx"),
                                  ("group", None, "r-x"),
                                  ("other", None, "---")]
    assert guard.get_default("/data/logs") is None


def test_default_acl_inheritance_masks_each_class_by_requested_mode():
    guard = make()
    guard.ensure_path("/srv", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.set_default("/srv", [
        ("user", None, "rwx"),
        ("user", "alice", "rwx"),
        ("group", None, "r-x"),
        ("group", "qa", "rw-"),
        ("other", None, "r-x"),
    ])
    child = guard.create_child("/srv", "app.log", kind="file", mode=0o640,
                               owner="svc", group="ops")
    assert child["entries"] == [("user", None, "rw-"),
                                ("user", "alice", "rw-"),
                                ("group", None, "r--"),
                                ("group", "qa", "r--"),
                                ("mask", None, "r--"),
                                ("other", None, "---")]
    assert child["mask"] == "r--"
    assert child["owner"] == "svc"
    assert child["group"] == "ops"


def test_new_directory_copies_parent_default_but_file_does_not():
    guard = make()
    guard.ensure_path("/srv", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.set_default("/srv", [("user", None, "rwx"),
                               ("user", "alice", "rwx"),
                               ("group", None, "r-x"),
                               ("group", "qa", "rw-"),
                               ("other", None, "r-x")])
    normalized = [("user", None, "rwx"), ("user", "alice", "rwx"),
                  ("group", None, "r-x"), ("group", "qa", "rw-"),
                  ("mask", None, "rwx"), ("other", None, "r-x")]
    assert guard.get_default("/srv") == normalized
    child = guard.create_child("/srv", "logs", kind="dir", mode=0o750,
                               owner="svc", group="ops")
    assert child["entries"] == [("user", None, "rwx"),
                                ("user", "alice", "rwx"),
                                ("group", None, "r-x"),
                                ("group", "qa", "r--"),
                                ("mask", None, "r-x"),
                                ("other", None, "---")]
    assert guard.get_default("/srv/logs") == normalized

    file_row = guard.create_child("/srv", "app.log", kind="file",
                                  mode=0o640, owner="svc", group="ops")
    assert guard.get_default("/srv/app.log") is None
    assert file_row["kind"] == "file"


def test_create_child_validates_parent_name_kind_mode_and_existence():
    guard = make()
    with pytest.raises(ag.AclError) as exc:
        guard.create_child("/missing", "x")
    assert exc.value.code == "ACL_NOT_FOUND"

    guard.ensure_path("/srv", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.create_child("/srv", "plain", kind="file", mode=0o644,
                       owner="root", group="root")
    with pytest.raises(ag.AclError) as exc:
        guard.create_child("/srv/plain", "x")
    assert exc.value.code == "ACL_NOT_DIR"

    guard.create_child("/srv", "a", kind="dir", mode=0o755,
                       owner="root", group="root")
    with pytest.raises(ag.AclError) as exc:
        guard.create_child("/srv", "a")
    assert exc.value.code == "ACL_EXISTS"
    for bad_name in ("a/b", "..", ""):
        with pytest.raises(ag.AclError) as exc:
            guard.create_child("/srv", bad_name)
        assert exc.value.code == "ACL_INVALID_NAME"
    with pytest.raises(ag.AclError) as exc:
        guard.create_child("/srv", "sock", kind="socket")
    assert exc.value.code == "ACL_INVALID_KIND"
    with pytest.raises(ag.AclError) as exc:
        guard.create_child("/srv", "bad", mode=0o1000)
    assert exc.value.code == "ACL_INVALID_MODE"


def test_text_roundtrip_uses_canonical_line_order():
    guard = make()
    guard.ensure_path("/srv", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.set_access("/srv", entries=[
        ("mask", None, "r-x"),
        ("group", "qa", "rw-"),
        ("group", None, "r-x"),
        ("user", "alice", "r-x"),
        ("user", None, "rwx"),
        ("other", None, "---"),
    ])
    text = guard.to_text("/srv")
    assert text == ("user::rwx\nuser:alice:r-x\ngroup::r-x\n"
                    "group:qa:rw-\nmask::r-x\nother::---")

    clone = make()
    clone.ensure_path("/srv", kind="dir", owner="root", group="root",
                      mode=0o755)
    row = clone.from_text("/srv", text)
    assert row["entries"] == guard.get_access("/srv")["entries"]
    assert clone.get_access("/srv")["mask"] == "r-x"

    plain = make()
    plain.ensure_path("/srv", kind="dir", owner="root", group="root",
                      mode=0o755)
    plain.from_text("/srv", "user::rw-\ngroup::r--\nother::---")
    assert plain.get_access("/srv")["entries"] == [("user", None, "rw-"),
                                                   ("group", None, "r--"),
                                                   ("other", None, "---")]
    assert plain.get_access("/srv")["mask"] is None


def test_malformed_acl_text_is_rejected_without_partial_update():
    guard = make()
    guard.ensure_path("/srv", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.set_access("/srv", entries=BASIC)
    before = guard.get_access("/srv")
    bad_texts = [
        "user::rwx\ngroup::r-x",
        "user::rwx\ngroup::r-x\nother::rwz",
        "owner::rwx\ngroup::r-x\nother::---",
        "user::rwx\nuser::r-x\ngroup::r-x\nother::---",
        "user::rwx\ngroup::r-x\nother::---\nmask::r-x\nmask::rwx",
        "user::rwx\ngroup:r-x\nother::---",
    ]
    for text in bad_texts:
        with pytest.raises(ag.AclError) as exc:
            guard.from_text("/srv", text)
        assert exc.value.code == "ACL_INVALID_TEXT"
    assert guard.get_access("/srv") == before
    assert guard.stats()["updated"] == 1
    assert guard.stats()["rejected"] == 6


def test_remove_is_recursive_and_root_is_protected():
    guard = make()
    guard.ensure_path("/a", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.ensure_path("/a/b", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.ensure_path("/a/b/c", kind="file", owner="root", group="root",
                      mode=0o644)
    guard.ensure_path("/z", kind="file", owner="root", group="root",
                      mode=0o644)
    assert guard.remove("/a") == 3
    assert guard.list_paths() == ["/", "/z"]

    with pytest.raises(ag.AclError) as exc:
        guard.remove("/a")
    assert exc.value.code == "ACL_NOT_FOUND"
    with pytest.raises(ag.AclError) as exc:
        guard.remove("/")
    assert exc.value.code == "ACL_ROOT"
    assert guard.stats()["removed"] == 1


def test_path_listing_is_sorted_and_unknown_paths_are_reported():
    guard = make()
    guard.ensure_path("/b", kind="dir", owner="root", group="root",
                      mode=0o755)
    guard.ensure_path("/a", kind="dir", owner="root", group="root",
                      mode=0o755)
    assert guard.list_paths() == ["/", "/a", "/b"]

    with pytest.raises(ag.AclError) as exc:
        guard.effective("/nope", user="x")
    assert exc.value.code == "ACL_NOT_FOUND"
    with pytest.raises(ag.AclError) as exc:
        guard.get_access("/nope")
    assert exc.value.code == "ACL_NOT_FOUND"
    with pytest.raises(ag.AclError) as exc:
        guard.set_default("/nope", BASIC)
    assert exc.value.code == "ACL_NOT_FOUND"
    assert guard.stats()["rejected"] == 3


def test_snapshot_restore_carries_tree_defaults_umask_and_stats():
    guard = make()
    guard.ensure_path("/srv", kind="dir", owner="ops", group="ops",
                      mode=0o750)
    guard.set_default("/srv", [("user", None, "rwx"), ("group", None, "r-x"),
                               ("other", None, "---")])
    guard.create_child("/srv", "x", kind="file", mode=0o640, owner="u",
                       group="g")
    snap = guard.snapshot()
    snap["stats"]["created"] = 99
    assert guard.stats()["created"] == 2

    current = guard.snapshot()
    fresh = make(umask=0o077)
    fresh.restore(current)
    assert fresh.umask == 0o022
    assert fresh.get_default("/srv") == [("user", None, "rwx"),
                                         ("group", None, "r-x"),
                                         ("other", None, "---")]
    assert fresh.get_access("/srv/x")["owner"] == "u"
    assert fresh.get_access("/srv/x")["entries"] == [("user", None, "rw-"),
                                                     ("group", None, "r--"),
                                                     ("other", None, "---")]
    assert fresh.stats()["created"] == 2

    bad_version = dict(current)
    bad_version["version"] = 2
    bad_nodes = dict(current)
    bad_nodes["nodes"] = "not-a-list"
    no_root = dict(current)
    no_root["nodes"] = [node for node in current["nodes"]
                        if node["path"] != "/"]
    bad_stats = dict(current)
    bad_stats["stats"] = {"created": "many"}
    for state in (None, {}, bad_version, bad_nodes, no_root, bad_stats):
        with pytest.raises(ag.AclError) as exc:
            fresh.restore(state)
        assert exc.value.code == "ACL_SNAPSHOT_INVALID"
    assert fresh.get_access("/srv/x")["owner"] == "u"
    assert fresh.stats()["created"] == 2
    assert fresh.stats()["updated"] == 1


def test_concurrent_creation_of_the_same_child_has_one_winner():
    guard = make()
    guard.ensure_path("/logs", kind="dir", owner="root", group="root",
                      mode=0o777)
    barrier = threading.Barrier(6)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.create_child("/logs", "access.log", kind="file",
                                     mode=0o666, owner="app", group="app")
            result = ("ok", row)
        except ag.AclError as exc:
            result = ("err", exc.code)
        except Exception as exc:  # pragma: no cover - stub path only
            result = ("boom", type(exc).__name__)
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for item in outcomes if item[0] == "ok") == 1
    assert sorted(item[1] for item in outcomes
                  if item[0] == "err") == ["ACL_EXISTS"] * 5
    assert guard.stats()["created"] == 2
    assert guard.get_access("/logs/access.log")["owner"] == "app"
