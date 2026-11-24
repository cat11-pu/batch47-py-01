import threading

import pytest

import oci_runtime_guard as oci


def test_create_validates_id_bundle_and_duplicates():
    guard = oci.OciRuntimeGuard()
    for bad in ("", "-bad", "a" * 65):
        with pytest.raises(oci.OciError) as exc:
            guard.create(bad, bundle="/b")
        assert exc.value.code == "OCI_BAD_ID"
    for bundle in ("", None):
        with pytest.raises(oci.OciError) as exc:
            guard.create("ok", bundle=bundle)
        assert exc.value.code == "OCI_BAD_BUNDLE"

    state = guard.create("ok", bundle="/b", labels={"tier": "web"})
    assert state["status"] == "created"
    assert state["pid"] is None
    assert state["exit_code"] is None
    with pytest.raises(oci.OciError) as exc:
        guard.create("ok", bundle="/b")
    assert exc.value.code == "OCI_EXISTS"
    assert guard.create("Ok_1.x", bundle="/b2")["status"] == "created"


def test_capacity_limit_and_slot_release():
    guard = oci.OciRuntimeGuard(max_containers=2)
    guard.create("a", bundle="/a")
    guard.create("b", bundle="/b")
    with pytest.raises(oci.OciError) as exc:
        guard.create("c", bundle="/c")
    assert exc.value.code == "OCI_LIMIT"
    assert guard.delete("b") is True
    assert guard.create("c", bundle="/c")["status"] == "created"
    assert [row["id"] for row in guard.list_containers()] == ["a", "c"]
    assert guard.stats()["created"] == 3


def test_start_assigns_pids_and_rejects_wrong_state():
    guard = oci.OciRuntimeGuard()
    guard.create("c1", bundle="/b")
    assert guard.state("c1")["pid"] is None
    started = guard.start("c1")
    assert started["status"] == "running"
    assert started["pid"] == 1000

    guard.create("c2", bundle="/b")
    assert guard.start("c2")["pid"] == 1001
    with pytest.raises(oci.OciError) as exc:
        guard.start("c1")
    assert exc.value.code == "OCI_BAD_STATE"

    guard.kill("c1")
    with pytest.raises(oci.OciError) as exc:
        guard.start("c1")
    assert exc.value.code == "OCI_BAD_STATE"

    guard.delete("c1")
    guard.create("c1", bundle="/b")
    assert guard.start("c1")["pid"] == 1002


def test_pause_resume_state_machine():
    guard = oci.OciRuntimeGuard()
    guard.create("c", bundle="/b")
    with pytest.raises(oci.OciError) as exc:
        guard.pause("c")
    assert exc.value.code == "OCI_BAD_STATE"

    guard.start("c")
    assert guard.pause("c")["status"] == "paused"
    with pytest.raises(oci.OciError) as exc:
        guard.exec_in("c", ["sh"])
    assert exc.value.code == "OCI_BAD_STATE"
    with pytest.raises(oci.OciError) as exc:
        guard.pause("c")
    assert exc.value.code == "OCI_BAD_STATE"

    assert guard.resume("c")["status"] == "running"
    with pytest.raises(oci.OciError) as exc:
        guard.resume("c")
    assert exc.value.code == "OCI_BAD_STATE"


def test_exec_ids_are_per_container_and_monotonic():
    guard = oci.OciRuntimeGuard()
    guard.create("c1", bundle="/b")
    guard.start("c1")
    assert guard.exec_in("c1", ["sh", "-c", "true"])["exec_id"] == "c1-exec-1"
    assert guard.exec_in("c1", ["id"])["exec_id"] == "c1-exec-2"
    assert guard.state("c1")["execs"] == 2

    guard.create("c2", bundle="/b")
    guard.start("c2")
    assert guard.exec_in("c2", ["id"])["exec_id"] == "c2-exec-1"

    guard.kill("c1")
    with pytest.raises(oci.OciError) as exc:
        guard.exec_in("c1", ["id"])
    assert exc.value.code == "OCI_BAD_STATE"

    guard.create("c3", bundle="/b")
    guard.start("c3")
    with pytest.raises(oci.OciError) as exc:
        guard.exec_in("c3", [])
    assert exc.value.code == "OCI_BAD_ARGV"


def test_kill_signal_exit_codes():
    guard = oci.OciRuntimeGuard()
    guard.create("c1", bundle="/b")
    guard.start("c1")
    out = guard.kill("c1", "SIGKILL")
    assert out["status"] == "stopped"
    assert out["exit_code"] == 137
    with pytest.raises(oci.OciError) as exc:
        guard.kill("c1")
    assert exc.value.code == "OCI_BAD_STATE"

    guard.create("c2", bundle="/b")
    guard.start("c2")
    assert guard.kill("c2", 2)["exit_code"] == 130
    guard.create("c3", bundle="/b")
    guard.start("c3")
    assert guard.kill("c3", "SIGTERM")["exit_code"] == 143

    guard.create("c4", bundle="/b")
    guard.start("c4")
    for bad in ("SIGFOO", 0, 65):
        with pytest.raises(oci.OciError) as exc:
            guard.kill("c4", bad)
        assert exc.value.code == "OCI_BAD_SIGNAL"
    assert guard.state("c4")["status"] == "running"


def test_stop_deadline_and_forced_kill_on_tick():
    guard = oci.OciRuntimeGuard(stop_grace=10)
    guard.create("c", bundle="/b")
    guard.start("c")
    out = guard.stop("c", timeout=5)
    assert out["status"] == "stopping"
    assert out["deadline"] == 5
    assert guard.tick(4) == 0
    assert guard.state("c")["status"] == "stopping"
    again = guard.stop("c", timeout=99)
    assert again["deadline"] == 5
    assert guard.tick(1) == 1
    stopped = guard.state("c")
    assert stopped["status"] == "stopped"
    assert stopped["exit_code"] == 137
    assert guard.stats()["forced_kills"] == 1
    with pytest.raises(oci.OciError) as exc:
        guard.stop("c")
    assert exc.value.code == "OCI_BAD_STATE"

    guard.create("d", bundle="/b")
    guard.start("d")
    guard.tick(3)
    assert guard.stop("d")["deadline"] == 18


def test_report_exit_and_bad_codes():
    guard = oci.OciRuntimeGuard()
    guard.create("c", bundle="/b")
    guard.start("c")
    guard.stop("c")
    out = guard.report_exit("c", 7)
    assert out["status"] == "stopped"
    assert out["exit_code"] == 7
    with pytest.raises(oci.OciError) as exc:
        guard.report_exit("c", 0)
    assert exc.value.code == "OCI_BAD_STATE"

    guard.create("c2", bundle="/b")
    guard.start("c2")
    with pytest.raises(oci.OciError) as exc:
        guard.report_exit("c2", 300)
    assert exc.value.code == "OCI_BAD_EXIT_CODE"
    assert guard.state("c2")["status"] == "running"

    guard.create("c3", bundle="/b")
    with pytest.raises(oci.OciError) as exc:
        guard.report_exit("c3", 0)
    assert exc.value.code == "OCI_BAD_STATE"


def test_delete_requires_stopped_or_force():
    guard = oci.OciRuntimeGuard()
    guard.create("a", bundle="/b")
    assert guard.delete("a") is True
    with pytest.raises(oci.OciError) as exc:
        guard.delete("a")
    assert exc.value.code == "OCI_NOT_FOUND"

    guard.create("b", bundle="/b")
    guard.start("b")
    with pytest.raises(oci.OciError) as exc:
        guard.delete("b")
    assert exc.value.code == "OCI_BAD_STATE"
    assert guard.delete("b", force=True) is True
    with pytest.raises(oci.OciError) as exc:
        guard.state("b")
    assert exc.value.code == "OCI_NOT_FOUND"

    guard.create("c", bundle="/b")
    assert guard.delete("c") is True
    with pytest.raises(oci.OciError) as exc:
        guard.delete("ghost")
    assert exc.value.code == "OCI_NOT_FOUND"


def test_state_and_list_filter_and_sort():
    guard = oci.OciRuntimeGuard()
    guard.create("c2", bundle="/b", labels={"tier": "web"})
    guard.create("c1", bundle="/b", labels={"tier": "web"})
    guard.create("c3", bundle="/b", labels={"tier": "db"})
    guard.create("c0", bundle="/b")
    assert [row["id"] for row in guard.list_containers()] == ["c0", "c1", "c2", "c3"]
    assert [row["id"] for row in guard.list_containers(label="tier=web")] == ["c1", "c2"]
    assert [row["id"] for row in guard.list_containers(label="tier=db")] == ["c3"]
    info = guard.state("c0")
    assert info["status"] == "created"
    assert info["pid"] is None
    assert info["bundle"] == "/b"
    with pytest.raises(oci.OciError) as exc:
        guard.list_containers(label="bad")
    assert exc.value.code == "OCI_BAD_LABELS"


def test_unknown_container_errors():
    guard = oci.OciRuntimeGuard()
    calls = [
        lambda: guard.state("ghost"),
        lambda: guard.start("ghost"),
        lambda: guard.exec_in("ghost", ["x"]),
        lambda: guard.pause("ghost"),
        lambda: guard.resume("ghost"),
        lambda: guard.kill("ghost"),
        lambda: guard.stop("ghost"),
        lambda: guard.report_exit("ghost", 0),
        lambda: guard.delete("ghost"),
    ]
    for call in calls:
        with pytest.raises(oci.OciError) as exc:
            call()
        assert exc.value.code == "OCI_NOT_FOUND"


def test_events_ring_is_bounded_and_ordered():
    guard = oci.OciRuntimeGuard(event_limit=3)
    guard.create("c1", bundle="/b")
    guard.start("c1")
    guard.stop("c1", timeout=5)
    guard.tick(5)
    guard.delete("c1")
    assert [event["action"] for event in guard.events()] == [
        "delete",
        "forced_kill",
        "stop",
    ]
    assert [event["seq"] for event in guard.events()] == [5, 4, 3]
    assert [event["action"] for event in guard.events(2)] == ["delete", "forced_kill"]
    assert guard.events(0) == []
    with pytest.raises(oci.OciError) as exc:
        guard.events(-1)
    assert exc.value.code == "OCI_BAD_LIMIT"


def test_snapshot_restore_keeps_running_containers():
    guard = oci.OciRuntimeGuard()
    guard.create("c1", bundle="/b1")
    guard.start("c1")
    guard.exec_in("c1", ["run"])
    guard.create("c2", bundle="/b2")
    guard.start("c2")
    guard.stop("c2")
    guard.report_exit("c2", 3)
    state = guard.snapshot()
    guard.kill("c1")

    restored = oci.OciRuntimeGuard()
    restored.restore(state)
    assert restored.state("c1")["status"] == "running"
    assert restored.state("c1")["pid"] == 1000
    assert restored.state("c2")["status"] == "stopped"
    assert restored.state("c2")["exit_code"] == 3
    with pytest.raises(oci.OciError) as exc:
        restored.start("c1")
    assert exc.value.code == "OCI_BAD_STATE"
    assert restored.exec_in("c1", ["again"])["exec_id"] == "c1-exec-2"
    assert restored.delete("c2") is True
    assert restored.stats() == {
        "created": 2,
        "started": 2,
        "stopped": 1,
        "deleted": 1,
        "execs": 2,
        "forced_kills": 0,
    }


def test_corrupt_snapshot_rejected_without_mutation():
    guard = oci.OciRuntimeGuard()
    guard.create("c1", bundle="/b")
    guard.start("c1")
    bad_states = [
        None,
        {},
        {"version": 2, "containers": {}, "next_pid": 1000, "created_seq": 0,
         "now": 0, "events": [], "stats": {}},
        {"version": 1, "containers": [], "next_pid": 1000, "created_seq": 0,
         "now": 0, "events": [], "stats": {}},
        {"version": 1, "containers": {}, "next_pid": "x", "created_seq": 0,
         "now": 0, "events": [], "stats": {}},
        {"version": 1, "containers": {}, "next_pid": 1000, "created_seq": 0,
         "now": 0, "events": [], "stats": "nope"},
    ]
    for bad in bad_states:
        with pytest.raises(oci.OciError) as exc:
            guard.restore(bad)
        assert exc.value.code == "OCI_SNAPSHOT_INVALID"
    assert guard.state("c1")["status"] == "running"
    assert guard.stats()["started"] == 1


def test_concurrent_start_has_single_winner():
    guard = oci.OciRuntimeGuard()
    guard.create("c", bundle="/b")
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            guard.start("c")
            result = "ok"
        except oci.OciError as exc:
            result = exc.code
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["OCI_BAD_STATE"] * 7 + ["ok"]
    assert guard.state("c")["status"] == "running"
    assert guard.stats()["started"] == 1
    assert len(guard.events()) == 2
