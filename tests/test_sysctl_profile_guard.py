import threading

import pytest

import sysctl_profile_guard as sg


def bool_schema(guard, key, default="0"):
    return guard.register_schema(key, kind="bool", default=default)


def test_register_schema_validates_kind_and_bounds():
    guard = sg.SysctlProfileGuard()
    assert bool_schema(guard, "net.ipv4.ip_forward")["kind"] == "bool"
    with pytest.raises(sg.SysctlError) as exc:
        bool_schema(guard, "net.ipv4.ip_forward")
    assert exc.value.code == "SYSCTL_SCHEMA_EXISTS"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_schema("vm.swappiness", kind="float")
    assert exc.value.code == "SYSCTL_BAD_KIND"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_schema("vm.swappiness", kind="int")
    assert exc.value.code == "SYSCTL_BAD_RANGE"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_schema("vm.swappiness", kind="int", minimum=10, maximum=5)
    assert exc.value.code == "SYSCTL_BAD_RANGE"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_schema("net.core.somaxconn", kind="enum", allowed=())
    assert exc.value.code == "SYSCTL_BAD_SCHEMA"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_schema("net.core.somaxconn", kind="enum",
                              allowed=("a", "b"), default="c")
    assert exc.value.code == "SYSCTL_BAD_VALUE"


def test_register_profile_validates_name_keys_and_values():
    guard = sg.SysctlProfileGuard()
    bool_schema(guard, "net.ipv4.ip_forward")
    guard.register_schema("kernel.pid_max", kind="int", minimum=1024,
                          maximum=4194304, default="32768")
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("Bad Name", {"net.ipv4.ip_forward": "1"})
    assert exc.value.code == "SYSCTL_BAD_PROFILE"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("p", "not-a-dict")
    assert exc.value.code == "SYSCTL_BAD_ARGUMENT"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("p", {})
    assert exc.value.code == "SYSCTL_BAD_ARGUMENT"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("p", {"ghost.key": "1"})
    assert exc.value.code == "SYSCTL_UNKNOWN_KEY"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("p", {"net.ipv4.ip_forward": "yes"})
    assert exc.value.code == "SYSCTL_BAD_VALUE"
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("p", {"kernel.pid_max": 100})
    assert exc.value.code == "SYSCTL_BAD_VALUE"

    info = guard.register_profile(
        "base", {"net.ipv4.ip_forward": "1", "kernel.pid_max": "65536"})
    assert info["keys"] == 2
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("base", {"net.ipv4.ip_forward": "1"})
    assert exc.value.code == "SYSCTL_PROFILE_EXISTS"


def test_profile_limit_and_batch_key_limit():
    guard = sg.SysctlProfileGuard(max_profiles=2)
    for i in range(3):
        key = "test.key%d" % i
        bool_schema(guard, key)
        if i < 2:
            guard.register_profile("p%d" % i, {key: "1"})
    with pytest.raises(sg.SysctlError) as exc:
        guard.register_profile("p2", {"test.key2": "1"})
    assert exc.value.code == "SYSCTL_PROFILE_LIMIT"

    small = sg.SysctlProfileGuard(max_keys_per_apply=3)
    for i in range(4):
        bool_schema(small, "test.key%d" % i)
    with pytest.raises(sg.SysctlError) as exc:
        small.register_profile(
            "wide", {"test.key%d" % i: "1" for i in range(4)})
    assert exc.value.code == "SYSCTL_BATCH_TOO_LARGE"
    small.register_profile("three", {"test.key%d" % i: "1" for i in range(3)})
    small.register_profile("one", {"test.key3": "1"})
    with pytest.raises(sg.SysctlError) as exc:
        small.apply(["three", "one"])
    assert exc.value.code == "SYSCTL_BATCH_TOO_LARGE"
    assert small.apply("three")["changed"] == 3


def test_apply_sets_values_and_returns_generation():
    guard = sg.SysctlProfileGuard()
    bool_schema(guard, "net.ipv4.ip_forward")
    guard.register_schema("vm.swappiness", kind="int", minimum=0, maximum=100,
                          default="60")
    guard.register_profile(
        "base", {"net.ipv4.ip_forward": "1", "vm.swappiness": "10"})
    assert guard.get("net.ipv4.ip_forward") == "0"
    out = guard.apply(["base"])
    assert out == {
        "namespace": "host",
        "generation": 1,
        "applied": ["base"],
        "changed": 2,
    }
    assert guard.get("net.ipv4.ip_forward") == "1"
    assert guard.get("vm.swappiness") == "10"
    entry = guard.history()[0]
    assert entry["action"] == "apply"
    assert entry["generation"] == 1
    assert entry["changed"] == 2
    assert guard.stats()["applies"] == 1


def test_apply_is_atomic_on_conflict():
    guard = sg.SysctlProfileGuard()
    guard.register_schema("vm.swappiness", kind="int", minimum=0, maximum=100,
                          default="60")
    guard.register_profile("a", {"vm.swappiness": "10"})
    guard.register_profile("b", {"vm.swappiness": "20"})
    with pytest.raises(sg.SysctlError) as exc:
        guard.apply(["a", "b"])
    assert exc.value.code == "SYSCTL_CONFLICT"
    assert exc.value.detail == "vm.swappiness"
    assert guard.get("vm.swappiness") == "60"
    assert guard.history() == []
    assert guard.stats()["conflicts"] == 1

    out = guard.apply("a")
    assert out["generation"] == 1
    assert out["changed"] == 1


def test_conflict_with_previously_applied_profile():
    guard = sg.SysctlProfileGuard()
    guard.register_schema("vm.swappiness", kind="int", minimum=0, maximum=100,
                          default="60")
    guard.register_profile("a", {"vm.swappiness": "10"})
    guard.apply("a")
    guard.register_profile("c", {"vm.swappiness": "20"})
    with pytest.raises(sg.SysctlError) as exc:
        guard.apply("c")
    assert exc.value.code == "SYSCTL_CONFLICT"
    assert guard.get("vm.swappiness") == "10"

    guard.register_profile("d", {"vm.swappiness": "10"})
    out = guard.apply("d")
    assert out["changed"] == 0
    assert out["generation"] == 1
    assert len(guard.history()) == 1


def test_set_get_unset_manual_override_and_default():
    guard = sg.SysctlProfileGuard()
    bool_schema(guard, "net.ipv4.ip_forward")
    assert guard.get("net.ipv4.ip_forward") == "0"
    out = guard.set("net.ipv4.ip_forward", "1")
    assert out["key"] == "net.ipv4.ip_forward"
    assert out["value"] == "1"
    assert out["changed"] is True
    assert out["generation"] == 1
    assert guard.get("net.ipv4.ip_forward") == "1"

    again = guard.set("net.ipv4.ip_forward", "1")
    assert again["changed"] is False
    assert again["generation"] == 1
    assert guard.unset("net.ipv4.ip_forward") is True
    assert guard.get("net.ipv4.ip_forward") == "0"
    assert guard.unset("net.ipv4.ip_forward") is False

    with pytest.raises(sg.SysctlError) as exc:
        guard.set("net.ipv4.ip_forward", "2")
    assert exc.value.code == "SYSCTL_BAD_VALUE"
    with pytest.raises(sg.SysctlError) as exc:
        guard.set("ghost.key", "1")
    assert exc.value.code == "SYSCTL_UNKNOWN_KEY"
    with pytest.raises(sg.SysctlError) as exc:
        guard.get("ghost.key")
    assert exc.value.code == "SYSCTL_UNKNOWN_KEY"


def test_namespaces_are_isolated():
    guard = sg.SysctlProfileGuard()
    key = "net.ipv4.ip_forward"
    bool_schema(guard, key)
    guard.set(key, "1", namespace="host")
    guard.set(key, "0", namespace="tenant-a")
    assert guard.get(key, namespace="host") == "1"
    assert guard.get(key, namespace="tenant-a") == "0"

    guard.register_profile("on", {key: "1"})
    guard.apply("on", namespace="tenant-a")
    assert guard.get(key, namespace="tenant-a") == "1"
    assert guard.get(key, namespace="host") == "1"
    assert guard.drift("on", namespace="tenant-a") == []

    guard.set(key, "0", namespace="tenant-a")
    assert guard.drift("on", namespace="tenant-a") == [key]
    assert guard.drift("on", namespace="host") == []


def test_drift_reports_missing_and_changed_keys():
    guard = sg.SysctlProfileGuard()
    bool_schema(guard, "net.ipv4.ip_forward")
    guard.register_schema("vm.swappiness", kind="int", minimum=0, maximum=100,
                          default="60")
    guard.register_profile(
        "base", {"net.ipv4.ip_forward": "1", "vm.swappiness": "10"})
    guard.apply("base")
    assert guard.drift("base") == []
    guard.set("vm.swappiness", "99")
    assert guard.drift("base") == ["vm.swappiness"]
    guard.unset("vm.swappiness")
    assert guard.drift("base") == ["vm.swappiness"]
    guard.set("vm.swappiness", "10")
    assert guard.drift("base") == []


def test_apply_unknown_profile_and_empty_batch():
    guard = sg.SysctlProfileGuard()
    bool_schema(guard, "net.ipv4.ip_forward")
    guard.register_profile("base", {"net.ipv4.ip_forward": "1"})
    with pytest.raises(sg.SysctlError) as exc:
        guard.apply("ghost")
    assert exc.value.code == "SYSCTL_UNKNOWN_PROFILE"
    with pytest.raises(sg.SysctlError) as exc:
        guard.apply([])
    assert exc.value.code == "SYSCTL_EMPTY_APPLY"
    with pytest.raises(sg.SysctlError) as exc:
        guard.apply(123)
    assert exc.value.code == "SYSCTL_BAD_ARGUMENT"
    with pytest.raises(sg.SysctlError) as exc:
        guard.apply(["base", 5])
    assert exc.value.code == "SYSCTL_BAD_ARGUMENT"


def test_bad_key_and_namespace_validation():
    guard = sg.SysctlProfileGuard()
    key = "net.ipv4.ip_forward"
    bool_schema(guard, key)
    for bad_key in ("NoDots", "bad..key"):
        with pytest.raises(sg.SysctlError) as exc:
            guard.get(bad_key)
        assert exc.value.code == "SYSCTL_BAD_KEY"
    with pytest.raises(sg.SysctlError) as exc:
        guard.set(key, "1", namespace="Bad NS")
    assert exc.value.code == "SYSCTL_BAD_NAMESPACE"
    with pytest.raises(sg.SysctlError) as exc:
        guard.get(key, namespace="Bad NS")
    assert exc.value.code == "SYSCTL_BAD_NAMESPACE"


def test_rollback_restores_values_and_unknown_generation():
    guard = sg.SysctlProfileGuard()
    guard.register_schema("vm.swappiness", kind="int", minimum=0, maximum=100,
                          default="60")
    guard.register_profile("base", {"vm.swappiness": "10"})
    guard.apply("base")
    guard.set("vm.swappiness", "30")
    out = guard.rollback(1)
    assert out["generation"] == 3
    assert out["restored"] == 1
    assert guard.get("vm.swappiness") == "10"
    with pytest.raises(sg.SysctlError) as exc:
        guard.rollback(99)
    assert exc.value.code == "SYSCTL_UNKNOWN_GENERATION"
    assert guard.stats()["rollbacks"] == 1


def test_history_is_bounded_and_newest_first():
    guard = sg.SysctlProfileGuard(history_limit=3)
    guard.register_schema("vm.swappiness", kind="int", minimum=0, maximum=100,
                          default="60")
    guard.register_profile("base", {"vm.swappiness": "10"})
    guard.apply("base")
    guard.set("vm.swappiness", "20")
    guard.set("vm.swappiness", "30")
    guard.set("vm.swappiness", "40")
    history = guard.history()
    assert len(history) == 3
    assert [entry["generation"] for entry in history] == [4, 3, 2]
    assert [entry["action"] for entry in history] == ["set", "set", "set"]
    assert guard.history(1)[0]["generation"] == 4
    assert guard.history(0) == []
    with pytest.raises(sg.SysctlError) as exc:
        guard.history(-1)
    assert exc.value.code == "SYSCTL_BAD_LIMIT"


def test_snapshot_restore_and_corrupt_state():
    guard = sg.SysctlProfileGuard()
    guard.register_schema("vm.swappiness", kind="int", minimum=0, maximum=100,
                          default="60")
    guard.register_profile("base", {"vm.swappiness": "10"})
    guard.apply("base")
    state = guard.snapshot()
    guard.set("vm.swappiness", "90")

    restored = sg.SysctlProfileGuard()
    restored.restore(state)
    assert restored.get("vm.swappiness") == "10"
    assert restored.drift("base") == []
    assert restored.history()[0]["action"] == "apply"
    assert restored.apply("base")["changed"] == 0

    bad_states = [
        None,
        {},
        {"version": 2, "schemas": {}, "profiles": {}, "values": {},
         "applied": {}, "generation": 0, "snapshots": {}, "history": [],
         "stats": {}, "seq": 0},
        {"version": 1, "schemas": {}, "profiles": [], "values": {},
         "applied": {}, "generation": 0, "snapshots": {}, "history": [],
         "stats": {}, "seq": 0},
        {"version": 1, "schemas": {}, "profiles": {}, "values": "x",
         "applied": {}, "generation": 0, "snapshots": {}, "history": [],
         "stats": {}, "seq": 0},
        {"version": 1, "schemas": {}, "profiles": {}, "values": {},
         "applied": {}, "generation": 0, "snapshots": {}, "history": "x",
         "stats": {}, "seq": 0},
    ]
    for bad in bad_states:
        with pytest.raises(sg.SysctlError) as exc:
            restored.restore(bad)
        assert exc.value.code == "SYSCTL_SNAPSHOT_INVALID"
    assert restored.get("vm.swappiness") == "10"


def test_concurrent_apply_of_distinct_profiles():
    guard = sg.SysctlProfileGuard()
    for i in range(8):
        bool_schema(guard, "test.key%d" % i)
        guard.register_profile("p%d" % i, {"test.key%d" % i: "1"})
    barrier = threading.Barrier(8)
    errors = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            guard.apply("p%d" % index)
        except Exception as exc:
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for i in range(8):
        assert guard.get("test.key%d" % i) == "1"
    assert guard.stats()["applies"] == 8
    assert guard.history()[0]["generation"] == 8
