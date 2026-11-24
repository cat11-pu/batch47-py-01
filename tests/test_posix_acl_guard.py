import threading

import pytest

import posix_acl_guard as acl


def entries_of(facl, prefix):
    return [row for row in facl
            if row.startswith(prefix) and not row.startswith(prefix + ":")]


def test_new_file_gets_minimal_acl_and_mode():
    guard = acl.PosixAclGuard()
    guard.mkdir("/data", mode=0o750)
    info = guard.create_file("/data/report.txt", mode=0o640, uid=1000, gid=1000)
    assert info["mode"] == 0o640
    assert info["type"] == "file"
    assert guard.getfacl("/data/report.txt") == [
        "user::rw-",
        "group::r--",
        "other::---",
    ]
    assert guard.getfacl("/data") == ["user::rwx", "group::r-x", "other::---"]
    assert guard.stat("/data")["mode"] == 0o750
    assert guard.list_tree() == ["/data", "/data/report.txt"]


def test_default_acl_inherited_by_new_file():
    guard = acl.PosixAclGuard()
    guard.mkdir("/srv")
    guard.setfacl(
        "/srv",
        ["user::rwx", "user:1001:rwx", "group::r-x", "mask::rwx", "other::---"],
        default=True,
    )
    guard.create_file("/srv/app.conf", mode=0o644)
    assert guard.getfacl("/srv/app.conf") == [
        "user::rw-",
        "user:1001:rw-",
        "group::r--",
        "mask::rw-",
        "other::---",
    ]
    assert guard.stat("/srv/app.conf")["mode"] == 0o660


def test_default_acl_propagates_to_subdirectories():
    guard = acl.PosixAclGuard()
    guard.mkdir("/srv")
    guard.setfacl(
        "/srv",
        ["user::rwx", "user:1001:rwx", "group::r-x", "mask::rwx", "other::---"],
        default=True,
    )
    guard.mkdir("/srv/sub", mode=0o755)
    assert guard.getfacl("/srv/sub") == [
        "user::rwx",
        "user:1001:rwx",
        "group::r-x",
        "mask::rwx",
        "other::---",
    ]
    assert guard.stat("/srv/sub")["mode"] == 0o770
    assert guard.getfacl("/srv/sub", default=True) == [
        "user::rwx",
        "user:1001:rwx",
        "group::r-x",
        "mask::rwx",
        "other::---",
    ]
    guard.create_file("/srv/sub/f", mode=0o600)
    assert guard.getfacl("/srv/sub/f") == [
        "user::rw-",
        "user:1001:rw-",
        "group::---",
        "mask::rw-",
        "other::---",
    ]
    assert guard.stat("/srv/sub/f")["mode"] == 0o660


def test_setfacl_named_entry_creates_mask():
    guard = acl.PosixAclGuard()
    guard.create_file("/f", mode=0o644)
    out = guard.setfacl("/f", ["user:1001:rw-"])
    assert out["extended"] is True
    assert out["mode"] == 0o664
    assert guard.getfacl("/f") == [
        "user::rw-",
        "user:1001:rw-",
        "group::r--",
        "mask::rw-",
        "other::r--",
    ]


def test_named_user_access_is_masked():
    guard = acl.PosixAclGuard()
    guard.create_file("/f", mode=0o640, uid=1000, gid=1000)
    guard.setfacl("/f", ["user:1001:rw-", "mask::r--"])
    assert guard.check_access("/f", uid=1001, gids=(), want="r") is True
    assert guard.check_access("/f", uid=1001, gids=(), want="w") is False
    assert guard.check_access("/f", uid=1001, gids=(), want="rw") is False
    assert guard.stats()["denied"] == 2


def test_owning_group_and_named_group_union_then_mask():
    guard = acl.PosixAclGuard()
    guard.create_file("/f", mode=0o640, uid=1000, gid=2000)
    guard.setfacl("/f", ["group:3000:r--", "other::---"])
    assert guard.getfacl("/f") == [
        "user::rw-",
        "group::r--",
        "group:3000:r--",
        "mask::r--",
        "other::---",
    ]
    assert guard.check_access("/f", uid=5000, gids=(2000, 3000), want="r") is True
    assert guard.check_access("/f", uid=5000, gids=(3000,), want="r") is True
    assert guard.check_access("/f", uid=5000, gids=(4000,), want="r") is False


def test_owner_and_other_permissions_and_denied_accounting():
    guard = acl.PosixAclGuard()
    guard.create_file("/open", mode=0o604, uid=1000, gid=1000)
    assert guard.check_access("/open", uid=1000, gids=(), want="rw") is True
    assert guard.check_access("/open", uid=2000, gids=(), want="r") is True
    assert guard.check_access("/open", uid=2000, gids=(), want="w") is False
    assert guard.stats()["access_checks"] == 3
    assert guard.stats()["denied"] == 1


def test_chmod_recomputes_mask_but_keeps_named_entries():
    guard = acl.PosixAclGuard()
    guard.create_file("/f", mode=0o644, uid=1000, gid=1000)
    guard.setfacl("/f", ["user:1001:rwx"])
    assert guard.stat("/f")["mode"] == 0o674
    out = guard.chmod("/f", 0o640)
    assert out["mode"] == 0o640
    assert guard.getfacl("/f") == [
        "user::rw-",
        "user:1001:rwx",
        "group::r--",
        "mask::r--",
        "other::---",
    ]
    assert guard.check_access("/f", uid=1001, gids=(), want="w") is False


def test_setfacl_duplicates_and_syntax_errors():
    guard = acl.PosixAclGuard()
    guard.create_file("/f")
    cases = [
        (["user:1001:rw-", "user:1001:r--"], "ACL_DUPLICATE_ENTRY"),
        (["user:1001:rq"], "ACL_ENTRY_SYNTAX"),
        (["user:1001"], "ACL_ENTRY_SYNTAX"),
        (["mask::rwq"], "ACL_ENTRY_SYNTAX"),
        (["owner::rwx"], "ACL_BAD_TAG"),
        ("not-a-list", "ACL_ENTRY_SYNTAX"),
        (["user:abc:rw-"], "ACL_ENTRY_SYNTAX"),
    ]
    for entries, code in cases:
        with pytest.raises(acl.AclError) as exc:
            guard.setfacl("/f", entries)
        assert exc.value.code == code
    assert guard.getfacl("/f") == ["user::rw-", "group::r--", "other::r--"]


def test_default_acl_only_on_directories():
    guard = acl.PosixAclGuard()
    guard.create_file("/f")
    with pytest.raises(acl.AclError) as exc:
        guard.setfacl("/f", ["user:1001:r--"], default=True)
    assert exc.value.code == "ACL_NOT_DIRECTORY"
    with pytest.raises(acl.AclError) as exc:
        guard.getfacl("/f", default=True)
    assert exc.value.code == "ACL_NOT_DIRECTORY"
    guard.mkdir("/d")
    with pytest.raises(acl.AclError) as exc:
        guard.getfacl("/d", default=True)
    assert exc.value.code == "ACL_NO_DEFAULT"


def test_clearing_acl_restores_mode_bits():
    guard = acl.PosixAclGuard()
    guard.mkdir("/d")
    guard.setfacl(
        "/d",
        ["user::rwx", "user:1001:rwx", "group::r-x", "mask::rwx", "other::---"],
        default=True,
    )
    guard.create_file("/d/inherit", mode=0o644)
    guard.setfacl("/d", [], default=True)
    with pytest.raises(acl.AclError) as exc:
        guard.getfacl("/d", default=True)
    assert exc.value.code == "ACL_NO_DEFAULT"
    guard.create_file("/d/plain", mode=0o644)
    assert guard.getfacl("/d/plain") == ["user::rw-", "group::r--", "other::r--"]
    assert guard.getfacl("/d/inherit") == [
        "user::rw-",
        "user:1001:rw-",
        "group::r--",
        "mask::rw-",
        "other::---",
    ]

    guard.create_file("/f", mode=0o644)
    guard.setfacl("/f", ["user:1001:rw-"])
    assert guard.stat("/f")["mode"] == 0o664
    guard.setfacl("/f", [])
    assert guard.getfacl("/f") == ["user::rw-", "group::rw-", "other::r--"]
    assert guard.stat("/f")["mode"] == 0o664
    assert guard.stat("/f")["extended"] is False


def test_missing_paths_and_recursive_remove():
    guard = acl.PosixAclGuard()
    guard.mkdir("/a")
    guard.mkdir("/a/b")
    guard.create_file("/a/b/c")
    with pytest.raises(acl.AclError) as exc:
        guard.remove("/a")
    assert exc.value.code == "ACL_NOT_EMPTY"
    assert guard.remove("/a", recursive=True) == 3
    with pytest.raises(acl.AclError) as exc:
        guard.remove("/a")
    assert exc.value.code == "ACL_ENOENT"
    with pytest.raises(acl.AclError) as exc:
        guard.stat("/a")
    assert exc.value.code == "ACL_ENOENT"
    with pytest.raises(acl.AclError) as exc:
        guard.check_access("/a", uid=1)
    assert exc.value.code == "ACL_ENOENT"
    assert guard.list_tree() == []


def test_list_tree_and_stat_shapes():
    guard = acl.PosixAclGuard()
    guard.mkdir("/x")
    guard.mkdir("/x/y")
    guard.create_file("/x/y/z")
    guard.create_file("/top")
    assert guard.list_tree() == ["/top", "/x", "/x/y", "/x/y/z"]
    directory = guard.stat("/x")
    assert directory["type"] == "dir"
    assert directory["mode"] == 0o755
    assert directory["extended"] is False
    plain = guard.stat("/top")
    assert plain["type"] == "file"
    assert plain["mode"] == 0o644
    assert plain["uid"] == 0
    assert plain["gid"] == 0


def test_snapshot_restore_preserves_acls_and_defaults():
    guard = acl.PosixAclGuard()
    guard.mkdir("/srv")
    guard.setfacl(
        "/srv",
        ["user::rwx", "user:1001:rwx", "group::r-x", "mask::rwx", "other::---"],
        default=True,
    )
    guard.setfacl("/srv", ["user:1002:r--"])
    saved = guard.getfacl("/srv")
    saved_default = guard.getfacl("/srv", default=True)
    state = guard.snapshot()

    guard.chmod("/srv", 0o700)
    guard.remove("/srv", recursive=True)

    restored = acl.PosixAclGuard()
    restored.restore(state)
    assert restored.getfacl("/srv") == saved
    assert restored.getfacl("/srv", default=True) == saved_default
    assert restored.check_access("/srv", uid=1002, gids=(), want="r") is True
    assert restored.create_file("/srv/new", mode=0o600)["mode"] == 0o660

    bad_states = [
        None,
        {},
        {"version": 2, "nodes": {}, "stats": {}},
        {"version": 1, "nodes": [], "stats": {}},
        {"version": 1, "nodes": {}, "stats": "nope"},
    ]
    for bad in bad_states:
        with pytest.raises(acl.AclError) as exc:
            restored.restore(bad)
        assert exc.value.code == "ACL_SNAPSHOT_INVALID"
    assert restored.getfacl("/srv") == saved


def test_concurrent_setfacl_entries_are_serialized():
    guard = acl.PosixAclGuard()
    guard.create_file("/f", mode=0o644)
    barrier = threading.Barrier(8)
    errors = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            guard.setfacl("/f", ["user:%d:r--" % (1001 + index)])
        except Exception as exc:
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    facl = guard.getfacl("/f")
    assert entries_of(facl, "user:") == ["user:%d:r--" % i for i in range(1001, 1009)]
    assert "mask::r--" in facl
    assert guard.stats()["acl_sets"] == 8
