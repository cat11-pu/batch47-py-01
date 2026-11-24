import threading

import pytest

import pidns_reaper_guard as rg


def make(**kwargs):
    guard = rg.PidnsReaperGuard(**kwargs)
    guard.register_container("c1", init_pid=1)
    return guard


def test_register_container_creates_init_and_is_idempotent():
    guard = rg.PidnsReaperGuard()
    row = guard.register_container("c1", init_pid=1)
    assert row == {"container_id": "c1", "init_pid": 1, "pids": 1}
    assert guard.register_container("c1", init_pid=1) == row
    assert guard.tree("c1") == [{"pid": 1, "ppid": 0, "comm": "init",
                                 "state": "running", "exit_status": None}]
    with pytest.raises(rg.ReaperError) as exc:
        guard.register_container("c1", init_pid=2)
    assert exc.value.code == "REAPER_CONFLICT"
    assert guard.stats()["registered"] == 1


def test_container_ids_and_init_pids_are_validated():
    guard = rg.PidnsReaperGuard()
    with pytest.raises(rg.ReaperError) as exc:
        guard.register_container("")
    assert exc.value.code == "REAPER_INVALID_CONTAINER"
    with pytest.raises(rg.ReaperError) as exc:
        guard.register_container(None)
    assert exc.value.code == "REAPER_INVALID_CONTAINER"
    with pytest.raises(rg.ReaperError) as exc:
        guard.register_container("c1", init_pid=0)
    assert exc.value.code == "REAPER_INVALID_PID"
    with pytest.raises(rg.ReaperError) as exc:
        guard.register_container("c1", init_pid=4194305)
    assert exc.value.code == "REAPER_INVALID_PID"
    assert guard.stats()["rejected"] == 4


def test_spawn_registers_processes_and_tree_is_sorted_by_pid():
    guard = make()
    row = guard.spawn("c1", pid=10, ppid=1, comm="worker")
    assert row == {"container_id": "c1", "pid": 10, "ppid": 1,
                   "state": "running"}
    guard.spawn("c1", pid=5, ppid=1)
    guard.spawn("c1", pid=7, ppid=10)
    assert [proc["pid"] for proc in guard.tree("c1")] == [1, 5, 7, 10]
    assert guard.tree("c1")[0]["comm"] == "init"
    assert guard.is_alive("c1", 10) is True
    assert guard.stats()["spawned"] == 3


def test_spawn_rejects_unknown_duplicate_and_dead_parents():
    guard = make()
    with pytest.raises(rg.ReaperError) as exc:
        guard.spawn("ghost", pid=2, ppid=1)
    assert exc.value.code == "REAPER_UNKNOWN_CONTAINER"
    with pytest.raises(rg.ReaperError) as exc:
        guard.spawn("c1", pid=0, ppid=1)
    assert exc.value.code == "REAPER_INVALID_PID"
    with pytest.raises(rg.ReaperError) as exc:
        guard.spawn("c1", pid=1, ppid=1)
    assert exc.value.code == "REAPER_PID_DUPLICATE"
    with pytest.raises(rg.ReaperError) as exc:
        guard.spawn("c1", pid=2, ppid=99)
    assert exc.value.code == "REAPER_NO_PARENT"

    guard.spawn("c1", pid=3, ppid=1)
    guard.exit("c1", pid=3)
    with pytest.raises(rg.ReaperError) as exc:
        guard.spawn("c1", pid=4, ppid=3)
    assert exc.value.code == "REAPER_NO_PARENT"
    assert guard.stats()["rejected"] == 5


def test_pid_budget_counts_zombies_until_they_are_reaped():
    guard = make(max_pids=3)
    guard.spawn("c1", pid=2, ppid=1)
    guard.spawn("c1", pid=3, ppid=1)
    with pytest.raises(rg.ReaperError) as exc:
        guard.spawn("c1", pid=4, ppid=1)
    assert exc.value.code == "REAPER_LIMIT"

    guard.exit("c1", pid=2)
    with pytest.raises(rg.ReaperError) as exc:
        guard.spawn("c1", pid=5, ppid=1)
    assert exc.value.code == "REAPER_LIMIT"

    guard.reap("c1")
    guard.spawn("c1", pid=6, ppid=1)
    assert guard.is_alive("c1", 6) is True
    assert guard.stats()["rejected"] == 2


def test_exit_turns_process_into_zombie_and_orphans_children():
    guard = make()
    guard.spawn("c1", pid=10, ppid=1, comm="worker")
    guard.spawn("c1", pid=11, ppid=10)
    guard.spawn("c1", pid=12, ppid=10)
    row = guard.exit("c1", pid=10, status=3)
    assert row == {"container_id": "c1", "pid": 10, "status": "exited",
                   "state": "zombie", "children_orphaned": 2}
    assert guard.is_alive("c1", 10) is False
    nodes = {proc["pid"]: proc for proc in guard.tree("c1")}
    assert nodes[11]["ppid"] == 0
    assert nodes[12]["ppid"] == 0
    assert nodes[10]["state"] == "zombie"
    assert nodes[10]["exit_status"] == 3
    assert nodes[10]["comm"] == "worker"
    assert guard.stats()["exited"] == 1


def test_exit_errors_are_distinguished_and_repeat_is_idempotent():
    guard = make()
    with pytest.raises(rg.ReaperError) as exc:
        guard.exit("c1", pid=99)
    assert exc.value.code == "REAPER_UNKNOWN_PID"
    with pytest.raises(rg.ReaperError) as exc:
        guard.exit("c1", pid=1)
    assert exc.value.code == "REAPER_INIT_PROTECTED"

    guard.spawn("c1", pid=2, ppid=1)
    assert guard.exit("c1", pid=2)["status"] == "exited"
    repeat = guard.exit("c1", pid=2)
    assert repeat == {"container_id": "c1", "pid": 2,
                      "status": "already_exited", "state": "zombie",
                      "children_orphaned": 0}
    assert guard.stats()["exited"] == 1
    with pytest.raises(rg.ReaperError) as exc:
        guard.exit("ghost", pid=1)
    assert exc.value.code == "REAPER_UNKNOWN_CONTAINER"
    assert guard.stats()["rejected"] == 3


def test_reap_returns_zombies_in_exit_order():
    guard = make()
    for pid in (20, 21, 22, 23):
        guard.spawn("c1", pid=pid, ppid=1)
    guard.exit("c1", pid=22, status=2)
    guard.exit("c1", pid=20, status=0)
    guard.exit("c1", pid=23, status=9)
    assert guard.reap("c1") == [
        {"pid": 22, "status": 2},
        {"pid": 20, "status": 0},
        {"pid": 23, "status": 9},
    ]
    assert [proc["pid"] for proc in guard.tree("c1")] == [1, 21]
    assert guard.reap("c1") == []
    assert guard.stats()["reaped"] == 3


def test_reap_limit_must_stay_within_the_configured_budget():
    guard = make(reap_budget=2)
    for pid in (30, 31, 32):
        guard.spawn("c1", pid=pid, ppid=1)
        guard.exit("c1", pid=pid)
    with pytest.raises(rg.ReaperError) as exc:
        guard.reap("c1", limit=0)
    assert exc.value.code == "REAPER_REAP_BUDGET"
    with pytest.raises(rg.ReaperError) as exc:
        guard.reap("c1", limit=3)
    assert exc.value.code == "REAPER_REAP_BUDGET"

    assert [row["pid"] for row in guard.reap("c1")] == [30, 31]
    assert guard.reap("c1", limit=2) == [{"pid": 32, "status": 0}]
    assert guard.stats()["reaped"] == 3
    assert guard.stats()["rejected"] == 2


def test_adopt_orphans_attaches_them_back_to_init():
    guard = make()
    guard.spawn("c1", pid=40, ppid=1)
    guard.spawn("c1", pid=41, ppid=40)
    guard.spawn("c1", pid=42, ppid=40)
    guard.spawn("c1", pid=43, ppid=1)
    guard.exit("c1", pid=40)
    assert guard.adopt_orphans("c1") == [41, 42]
    nodes = {proc["pid"]: proc for proc in guard.tree("c1")}
    assert nodes[41]["ppid"] == 1
    assert nodes[42]["ppid"] == 1
    assert nodes[43]["ppid"] == 1
    assert guard.adopt_orphans("c1") == []
    assert guard.stats()["rejected"] == 0


def test_signal_forwarding_respects_init_and_process_state():
    guard = make()
    guard.spawn("c1", pid=50, ppid=1)
    row = guard.signal("c1", pid=50, signum=15)
    assert row == {"container_id": "c1", "pid": 50, "signum": 15,
                   "forwarded": True}
    with pytest.raises(rg.ReaperError) as exc:
        guard.signal("c1", pid=50, signum=0)
    assert exc.value.code == "REAPER_INVALID_SIGNAL"
    with pytest.raises(rg.ReaperError) as exc:
        guard.signal("c1", pid=50, signum=65)
    assert exc.value.code == "REAPER_INVALID_SIGNAL"
    with pytest.raises(rg.ReaperError) as exc:
        guard.signal("c1", pid=1, signum=9)
    assert exc.value.code == "REAPER_INIT_PROTECTED"
    assert guard.signal("c1", pid=1, signum=10)["forwarded"] is True

    guard.exit("c1", pid=50)
    with pytest.raises(rg.ReaperError) as exc:
        guard.signal("c1", pid=50, signum=15)
    assert exc.value.code == "REAPER_PID_NOT_RUNNING"
    with pytest.raises(rg.ReaperError) as exc:
        guard.signal("c1", pid=77, signum=15)
    assert exc.value.code == "REAPER_UNKNOWN_PID"
    assert guard.stats()["signaled"] == 2
    assert guard.stats()["rejected"] == 5


def test_unregister_requires_force_for_live_processes():
    guard = make()
    guard.spawn("c1", pid=60, ppid=1)
    with pytest.raises(rg.ReaperError) as exc:
        guard.unregister_container("c1")
    assert exc.value.code == "REAPER_CONTAINER_BUSY"

    guard.exit("c1", pid=60)
    assert guard.unregister_container("c1") == {"container_id": "c1",
                                                "removed": 2}
    with pytest.raises(rg.ReaperError) as exc:
        guard.tree("c1")
    assert exc.value.code == "REAPER_UNKNOWN_CONTAINER"
    with pytest.raises(rg.ReaperError) as exc:
        guard.unregister_container("c1")
    assert exc.value.code == "REAPER_UNKNOWN_CONTAINER"

    forced = make()
    forced.spawn("c1", pid=70, ppid=1)
    assert forced.unregister_container("c1", force=True) == {
        "container_id": "c1", "removed": 2}


def test_snapshot_restore_keeps_zombie_order_and_budget():
    guard = make()
    guard.spawn("c1", pid=80, ppid=1)
    guard.spawn("c1", pid=81, ppid=1)
    guard.exit("c1", pid=81, status=5)
    guard.exit("c1", pid=80, status=6)
    snap = guard.snapshot()
    snap["stats"]["reaped"] = 99
    assert guard.stats()["reaped"] == 0

    current = guard.snapshot()
    fresh = rg.PidnsReaperGuard()
    fresh.restore(current)
    assert [proc["pid"] for proc in fresh.tree("c1")] == [1, 80, 81]
    assert [proc["state"] for proc in fresh.tree("c1")] == [
        "running", "zombie", "zombie"]
    assert fresh.reap("c1") == [{"pid": 81, "status": 5},
                                {"pid": 80, "status": 6}]
    assert fresh.stats()["reaped"] == 2

    bad_version = dict(current)
    bad_version["version"] = 2
    bad_containers = dict(current)
    bad_containers["containers"] = "not-containers"
    bad_max = dict(current)
    bad_max["max_pids"] = 0
    bad_stats = dict(current)
    bad_stats["stats"] = "not-stats"
    for state in (None, {}, bad_version, bad_containers, bad_max, bad_stats):
        with pytest.raises(rg.ReaperError) as exc:
            fresh.restore(state)
        assert exc.value.code == "REAPER_SNAPSHOT_INVALID"
    assert fresh.tree("c1") == [{"pid": 1, "ppid": 0, "comm": "init",
                                 "state": "running", "exit_status": None}]


def test_stats_are_exact_and_reset_clears_only_counters():
    guard = make()
    guard.spawn("c1", pid=90, ppid=1)
    guard.exit("c1", pid=90, status=0)
    guard.reap("c1")
    guard.signal("c1", pid=1, signum=10)
    with pytest.raises(rg.ReaperError) as exc:
        guard.exit("c1", pid=91)
    assert exc.value.code == "REAPER_UNKNOWN_PID"

    assert guard.stats() == {"registered": 1, "spawned": 1, "exited": 1,
                             "reaped": 1, "signaled": 1, "rejected": 1}
    guard.reset_stats()
    assert guard.stats() == {"registered": 0, "spawned": 0, "exited": 0,
                             "reaped": 0, "signaled": 0, "rejected": 0}
    assert guard.is_alive("c1", 1) is True


def test_concurrent_exit_and_spawn_have_exactly_one_transition():
    guard = make()
    guard.spawn("c1", pid=100, ppid=1)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def exiter():
        barrier.wait()
        try:
            out = guard.exit("c1", pid=100, status=7)
        except Exception as exc:  # pragma: no cover - stub path only
            out = {"status": "boom", "code": type(exc).__name__}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=exiter) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["already_exited"] * 7 + ["exited"]
    assert guard.stats()["exited"] == 1

    racer = make()
    gate = threading.Barrier(4)
    outcomes = []
    olock = threading.Lock()

    def spawner():
        gate.wait()
        try:
            result = ("ok", racer.spawn("c1", pid=200, ppid=1))
        except rg.ReaperError as exc:
            result = ("err", exc.code)
        except Exception as exc:  # pragma: no cover - stub path only
            result = ("boom", type(exc).__name__)
        with olock:
            outcomes.append(result)

    threads = [threading.Thread(target=spawner) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for item in outcomes if item[0] == "ok") == 1
    assert sorted(item[1] for item in outcomes
                  if item[0] == "err") == ["REAPER_PID_DUPLICATE"] * 3
    assert racer.stats()["spawned"] == 1
