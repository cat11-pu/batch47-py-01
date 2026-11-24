import threading

import pytest

import userns_id_map_guard as ug


def test_namespace_creation_and_duplicate_ids():
    guard = ug.UsernsIdMapGuard()
    assert guard.create_namespace("ns-a")["status"] == "created"
    with pytest.raises(ug.UsernsError) as exc:
        guard.create_namespace("ns-a")
    assert exc.value.code == "USERNS_DUPLICATE"
    with pytest.raises(ug.UsernsError) as exc:
        guard.create_namespace("Bad_ID")
    assert exc.value.code == "USERNS_BAD_ID"
    with pytest.raises(ug.UsernsError) as exc:
        guard.create_namespace("ns-b", parent="ghost")
    assert exc.value.code == "USERNS_UNKNOWN_PARENT"

    info = guard.mapping_of("ns-a")
    assert info["uid"] == []
    assert info["gid"] == []
    assert info["setgroups"] == "allow"
    assert info["parent"] is None
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_uid_map("ghost", ["0 1000 1"])
    assert exc.value.code == "USERNS_UNKNOWN_NS"


def test_uid_map_parsing_rejects_bad_lines():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("n")
    bad_inputs = [
        None,
        "0 1000 1",
        [],
        [""],
        ["0 1000"],
        ["a b c"],
        ["0 1000 0"],
        ["-1 1000 1"],
        ["0 1000 1 2"],
        [1, 2, 3],
    ]
    for bad in bad_inputs:
        with pytest.raises(ug.UsernsError) as exc:
            guard.write_uid_map("n", bad)
        assert exc.value.code == "USERNS_MAP_SYNTAX"


def test_uid_map_extent_limit_and_range_overflow():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("n")
    lines = ["%d %d 1" % (i, 1000 + i) for i in range(6)]
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_uid_map("n", lines)
    assert exc.value.code == "USERNS_MAP_TOO_MANY"

    small = ug.UsernsIdMapGuard(max_extents=2)
    small.create_namespace("m")
    result = small.write_uid_map("m", ["0 1000 1", "1 1001 1"])
    assert result["extents"] == [[0, 1000, 1], [1, 1001, 1]]

    wide = ug.UsernsIdMapGuard()
    wide.create_namespace("w")
    assert wide.write_uid_map("w", ["0 0 4294967296"])["kind"] == "uid"
    wide.create_namespace("w2")
    with pytest.raises(ug.UsernsError) as exc:
        wide.write_uid_map("w2", ["0 0 4294967297"])
    assert exc.value.code == "USERNS_MAP_RANGE"


def test_uid_map_overlaps_inside_and_outside():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("inside")
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_uid_map("inside", ["0 1000 10", "5 2000 10"])
    assert exc.value.code == "USERNS_MAP_OVERLAP"

    guard.create_namespace("outside")
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_uid_map("outside", ["0 1000 10", "20 1005 10"])
    assert exc.value.code == "USERNS_MAP_OVERLAP"


def test_uid_map_is_write_once():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("n")
    guard.write_uid_map("n", ["0 1000 10"])
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_uid_map("n", ["0 2000 1"])
    assert exc.value.code == "USERNS_MAP_LOCKED"
    assert guard.mapping_of("n")["uid"] == [[0, 1000, 10]]
    assert guard.stats()["uid_writes"] == 1


def test_nested_map_must_stay_within_parent():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("parent")
    guard.write_uid_map("parent", ["0 1000 100"])
    guard.create_namespace("child", parent="parent")
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_uid_map("child", ["0 95 10"])
    assert exc.value.code == "USERNS_MAP_OUTSIDE_PARENT"
    assert guard.write_uid_map("child", ["0 50 10"])["extents"] == [[0, 50, 10]]


def test_compose_maps_through_parent_chain():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("root")
    guard.write_uid_map("root", ["0 1000 100"])
    guard.create_namespace("mid", parent="root")
    guard.write_uid_map("mid", ["0 10 10"])
    guard.create_namespace("leaf", parent="mid")
    guard.write_uid_map("leaf", ["0 5 5"])

    assert guard.compose("mid")["uid"] == [[0, 1010, 10]]
    assert guard.compose("leaf")["uid"] == [[0, 1015, 5]]
    assert guard.map_uid("leaf", 2) == 7


def test_translation_and_overflow_sentinels():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("n")
    with pytest.raises(ug.UsernsError) as exc:
        guard.map_uid("n", 0)
    assert exc.value.code == "USERNS_MAP_MISSING"

    guard.write_uid_map("n", ["0 1000 10"])
    assert guard.map_uid("n", 3) == 1003
    assert guard.map_uid("n", 1005, direction="in") == 5
    assert guard.map_uid("n", 10) == 65534
    assert guard.map_uid("n", 999, direction="in") == 65534
    assert guard.stats()["translations"] == 4
    assert guard.stats()["overflow_hits"] == 2


def test_translation_direction_and_range_validation():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("n")
    guard.write_uid_map("n", ["0 1000 10"])
    with pytest.raises(ug.UsernsError) as exc:
        guard.map_uid("n", 1, direction="sideways")
    assert exc.value.code == "USERNS_BAD_DIRECTION"
    for bad in (-1, 2 ** 32):
        with pytest.raises(ug.UsernsError) as exc:
            guard.map_uid("n", bad)
        assert exc.value.code == "USERNS_BAD_ID_VALUE"
    guard.create_namespace("g")
    with pytest.raises(ug.UsernsError) as exc:
        guard.map_gid("g", 1)
    assert exc.value.code == "USERNS_MAP_MISSING"


def test_gid_map_requires_setgroups_deny():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("n")
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_gid_map("n", ["0 1000 10"])
    assert exc.value.code == "USERNS_SETGROUPS_REQUIRED"

    with pytest.raises(ug.UsernsError) as exc:
        guard.setgroups("n", "maybe")
    assert exc.value.code == "USERNS_SETGROUPS_VALUE"

    assert guard.setgroups("n", "deny") == "deny"
    assert guard.setgroups("n", "deny") == "deny"
    assert guard.write_gid_map("n", ["0 1000 10"])["kind"] == "gid"
    with pytest.raises(ug.UsernsError) as exc:
        guard.setgroups("n", "allow")
    assert exc.value.code == "USERNS_SETGROUPS_LOCKED"


def test_gid_map_write_once_and_parent_rules():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("p")
    guard.setgroups("p", "deny")
    guard.write_gid_map("p", ["0 2000 50"])
    guard.create_namespace("c", parent="p")
    guard.setgroups("c", "deny")
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_gid_map("c", ["0 45 10"])
    assert exc.value.code == "USERNS_MAP_OUTSIDE_PARENT"
    assert guard.write_gid_map("c", ["0 40 10"])["extents"] == [[0, 40, 10]]
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_gid_map("c", ["0 10 1"])
    assert exc.value.code == "USERNS_MAP_LOCKED"


def test_subid_grants_and_quota_accounting():
    guard = ug.UsernsIdMapGuard()
    assert guard.grant_subid("alice", start=200000, count=100)["remaining"] == 100
    with pytest.raises(ug.UsernsError) as exc:
        guard.grant_subid("alice", start=200050, count=100)
    assert exc.value.code == "USERNS_SUBID_CONFLICT"

    guard.create_namespace("a1", owner="alice")
    guard.write_uid_map("a1", ["0 200000 10"])
    assert guard.subid_remaining("alice") == 90

    guard.create_namespace("a2", owner="alice")
    with pytest.raises(ug.UsernsError) as exc:
        guard.write_uid_map("a2", ["0 200090 20"])
    assert exc.value.code == "USERNS_SUBID_EXCEEDED"
    guard.write_uid_map("a2", ["0 200090 10"])
    assert guard.subid_remaining("alice") == 80
    assert guard.subid_remaining("bob") == 0


def test_destroy_namespace_requires_no_children():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("p")
    guard.create_namespace("c", parent="p")
    with pytest.raises(ug.UsernsError) as exc:
        guard.destroy_namespace("p")
    assert exc.value.code == "USERNS_HAS_CHILDREN"
    assert guard.destroy_namespace("c") is True
    assert guard.destroy_namespace("p") is True
    with pytest.raises(ug.UsernsError) as exc:
        guard.destroy_namespace("p")
    assert exc.value.code == "USERNS_UNKNOWN_NS"
    with pytest.raises(ug.UsernsError) as exc:
        guard.mapping_of("p")
    assert exc.value.code == "USERNS_UNKNOWN_NS"


def test_snapshot_restore_and_corrupt_state():
    guard = ug.UsernsIdMapGuard()
    guard.grant_subid("alice", start=300000, count=50)
    guard.grant_subid("alice", start=300000, count=50, kind="gid")
    guard.create_namespace("n", owner="alice")
    guard.write_uid_map("n", ["0 300000 10"])
    guard.setgroups("n", "deny")
    guard.write_gid_map("n", ["0 300000 10"])
    state = guard.snapshot()

    guard.create_namespace("extra")
    guard.write_uid_map("extra", ["0 400000 1"])
    assert guard.stats()["uid_writes"] == 2

    restored = ug.UsernsIdMapGuard()
    restored.restore(state)
    assert restored.mapping_of("n")["uid"] == [[0, 300000, 10]]
    assert restored.mapping_of("n")["setgroups"] == "deny"
    assert restored.map_uid("n", 5) == 300005
    assert restored.subid_remaining("alice") == 40
    with pytest.raises(ug.UsernsError) as exc:
        restored.mapping_of("extra")
    assert exc.value.code == "USERNS_UNKNOWN_NS"

    bad_states = [
        None,
        {},
        {"version": 2, "namespaces": {}, "subids": {}, "stats": {}},
        {"version": 1, "namespaces": [], "subids": {}, "stats": {}},
        {"version": 1, "namespaces": {}, "subids": {}, "stats": "nope"},
    ]
    for bad in bad_states:
        with pytest.raises(ug.UsernsError) as exc:
            restored.restore(bad)
        assert exc.value.code == "USERNS_SNAPSHOT_INVALID"
    assert restored.map_uid("n", 5) == 300005


def test_concurrent_uid_map_writes_single_winner():
    guard = ug.UsernsIdMapGuard()
    guard.create_namespace("race")
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            guard.write_uid_map("race", ["0 %d 1" % (1000 + index * 10)])
            result = "ok"
        except ug.UsernsError as exc:
            result = exc.code
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["USERNS_MAP_LOCKED"] * 7 + ["ok"]
    assert guard.mapping_of("race")["uid"] in [
        [[0, 1000 + i * 10, 1]] for i in range(8)
    ]
    assert guard.stats()["uid_writes"] == 1
