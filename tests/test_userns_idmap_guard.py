import threading

import pytest

import userns_idmap_guard as ug


def make(**kwargs):
    return ug.UsernsIdmapGuard(**kwargs)


def test_reserve_auto_allocates_lowest_free_range_and_is_idempotent():
    guard = make()
    row = guard.reserve("tenant-a", container_start=0, container_count=65536)
    assert row["namespace"] == "tenant-a"
    assert row["host_start"] == 100000
    assert row["host_end"] == 100000 + 65536 - 1
    assert row["container_start"] == 0
    assert row["container_count"] == 65536
    assert row["idempotent"] is False

    again = guard.reserve("tenant-a", container_start=0, container_count=65536)
    assert again["idempotent"] is True
    assert again["host_start"] == 100000
    assert guard.occupancy() == {
        "used": 65536,
        "free": 999999 - 100000 + 1 - 65536,
        "mappings": 1,
    }
    assert guard.stats() == {
        "reserved": 1,
        "idempotent": 1,
        "released": 0,
        "assigned": 0,
        "rejected": 0,
    }


def test_explicit_host_start_maps_container_ids_at_edges():
    guard = make()
    row = guard.reserve("tenant-a", container_start=10, container_count=100,
                        host_start=500000, owner="team-a")
    assert row["host_start"] == 500000
    assert row["host_end"] == 500099
    assert row["owner"] == "team-a"
    assert guard.assign("tenant-a", uid=10) == 500000
    assert guard.assign("tenant-a", uid=109) == 500099
    assert guard.lookup(500099) == {"namespace": "tenant-a", "container_id": 109}


def test_host_start_outside_configured_window_is_rejected():
    guard = make()
    with pytest.raises(ug.IdmapError) as exc:
        guard.reserve("tenant-a", container_start=0, container_count=10,
                      host_start=99999)
    assert exc.value.code == "IDMAP_HOST_RANGE"

    with pytest.raises(ug.IdmapError) as exc:
        guard.reserve("tenant-b", container_start=0, container_count=10,
                      host_start=999995)
    assert exc.value.code == "IDMAP_HOST_RANGE"
    assert guard.mappings() == []
    assert guard.stats()["rejected"] == 2


def test_container_range_validation_and_per_mapping_count_cap():
    guard = make(max_count=10)
    for start, count in ((0, 0), (-1, 4), (4294967290, 16)):
        with pytest.raises(ug.IdmapError) as exc:
            guard.reserve("bad", container_start=start, container_count=count)
        assert exc.value.code == "IDMAP_RANGE_INVALID"

    with pytest.raises(ug.IdmapError) as exc:
        guard.reserve("bad", container_start=0, container_count=11)
    assert exc.value.code == "IDMAP_COUNT_TOO_LARGE"
    assert guard.mappings() == []
    assert guard.stats() == {
        "reserved": 0,
        "idempotent": 0,
        "released": 0,
        "assigned": 0,
        "rejected": 4,
    }


def test_overlapping_host_ranges_are_refused_but_adjacent_ranges_are_fine():
    guard = make()
    guard.reserve("alpha", container_start=0, container_count=100,
                  host_start=200000)
    with pytest.raises(ug.IdmapError) as exc:
        guard.reserve("beta", container_start=0, container_count=50,
                      host_start=200050)
    assert exc.value.code == "IDMAP_OVERLAP"
    assert [row["namespace"] for row in guard.mappings()] == ["alpha"]
    assert guard.is_reserved("beta") is False

    adjacent = guard.reserve("beta", container_start=0, container_count=50,
                             host_start=200100)
    assert adjacent["host_start"] == 200100
    assert guard.occupancy()["used"] == 150


def test_same_namespace_with_different_parameters_conflicts():
    guard = make()
    guard.reserve("alpha", container_start=0, container_count=100,
                  host_start=200000)
    with pytest.raises(ug.IdmapError) as exc:
        guard.reserve("alpha", container_start=0, container_count=200)
    assert exc.value.code == "IDMAP_CONFLICT"
    assert guard.occupancy()["mappings"] == 1
    assert guard.assign("alpha", uid=99) == 200099


def test_mapping_table_limit_blocks_new_namespaces_but_not_repeats():
    guard = make(max_mappings=2)
    guard.reserve("alpha", container_start=0, container_count=10)
    guard.reserve("beta", container_start=0, container_count=10)
    with pytest.raises(ug.IdmapError) as exc:
        guard.reserve("gamma", container_start=0, container_count=10)
    assert exc.value.code == "IDMAP_LIMIT"
    assert guard.is_reserved("gamma") is False

    repeat = guard.reserve("alpha", container_start=0, container_count=10)
    assert repeat["idempotent"] is True
    assert guard.occupancy()["mappings"] == 2


def test_assign_translates_container_ids_and_rejects_bad_requests():
    guard = make()
    guard.reserve("alpha", container_start=1000, container_count=100)
    assert guard.assign("alpha", uid=1000) == 100000
    assert guard.assign("alpha", uid=1099) == 100099

    with pytest.raises(ug.IdmapError) as exc:
        guard.assign("ghost", uid=1000)
    assert exc.value.code == "IDMAP_UNKNOWN_NAMESPACE"
    with pytest.raises(ug.IdmapError) as exc:
        guard.assign("alpha", uid=999)
    assert exc.value.code == "IDMAP_UID_OUT_OF_RANGE"
    with pytest.raises(ug.IdmapError) as exc:
        guard.assign("alpha", uid=1100)
    assert exc.value.code == "IDMAP_UID_OUT_OF_RANGE"
    assert guard.stats()["assigned"] == 2
    assert guard.stats()["rejected"] == 3


def test_lookup_covers_range_edges_and_returns_none_for_free_ids():
    guard = make()
    guard.reserve("alpha", container_start=0, container_count=100,
                  host_start=300000)
    assert guard.lookup(300000) == {"namespace": "alpha", "container_id": 0}
    assert guard.lookup(300099) == {"namespace": "alpha", "container_id": 99}
    assert guard.lookup(300100) is None
    assert guard.lookup(99999) is None
    assert guard.lookup(1000000) is None


def test_release_frees_the_slot_and_next_allocation_reuses_it():
    guard = make()
    guard.reserve("alpha", container_start=0, container_count=50)
    guard.reserve("beta", container_start=0, container_count=50)
    released = guard.release("alpha")
    assert released == {
        "namespace": "alpha",
        "host_start": 100000,
        "host_count": 50,
        "released": True,
    }
    assert guard.is_reserved("alpha") is False
    with pytest.raises(ug.IdmapError) as exc:
        guard.release("alpha")
    assert exc.value.code == "IDMAP_UNKNOWN_NAMESPACE"

    reused = guard.reserve("gamma", container_start=0, container_count=50)
    assert reused["host_start"] == 100000
    assert guard.occupancy()["used"] == 100
    assert guard.stats()["released"] == 1


def test_mappings_are_sorted_by_host_start():
    guard = make()
    guard.reserve("charlie", container_start=0, container_count=10,
                  host_start=400000)
    guard.reserve("alpha", container_start=0, container_count=10,
                  host_start=100000)
    guard.reserve("bravo", container_start=0, container_count=10,
                  host_start=250000)
    rows = guard.mappings()
    assert [row["namespace"] for row in rows] == ["alpha", "bravo", "charlie"]
    assert [row["host_start"] for row in rows] == [100000, 250000, 400000]
    assert [row["host_end"] for row in rows] == [100009, 250009, 400009]


def test_occupancy_tracks_used_and_free_ids_exactly():
    guard = make(host_id_min=200000, host_id_max=200999)
    assert guard.occupancy() == {"used": 0, "free": 1000, "mappings": 0}
    guard.reserve("alpha", container_start=0, container_count=400)
    guard.reserve("beta", container_start=0, container_count=100)
    assert guard.occupancy() == {"used": 500, "free": 500, "mappings": 2}
    guard.release("alpha")
    assert guard.occupancy() == {"used": 100, "free": 900, "mappings": 1}


def test_snapshot_is_a_deep_copy_and_does_not_track_later_writes():
    guard = make()
    guard.reserve("alpha", container_start=0, container_count=10)
    guard.assign("alpha", uid=3)
    snap = guard.snapshot()
    guard.reserve("beta", container_start=0, container_count=20)

    snap["mappings"].clear()
    snap["stats"]["assigned"] = 99
    assert guard.is_reserved("alpha") is True
    assert guard.is_reserved("beta") is True
    assert guard.assign("alpha", uid=3) == 100003
    assert guard.stats()["assigned"] == 2


def test_restore_rebuilds_mappings_and_stats_and_rejects_corruption():
    guard = make()
    guard.reserve("alpha", container_start=0, container_count=10,
                  host_start=500000)
    guard.reserve("beta", container_start=0, container_count=20,
                  host_start=600000)
    guard.assign("alpha", uid=5)
    snap = guard.snapshot()

    fresh = make()
    fresh.restore(snap)
    assert fresh.is_reserved("alpha") is True
    assert fresh.is_reserved("beta") is True
    assert fresh.assign("beta", uid=19) == 600019
    assert fresh.occupancy()["used"] == 30
    assert fresh.stats() == {
        "reserved": 2,
        "idempotent": 0,
        "released": 0,
        "assigned": 2,
        "rejected": 0,
    }

    corrupt = []
    corrupt.append(None)
    corrupt.append({})
    bad_version = dict(snap)
    bad_version["version"] = 2
    corrupt.append(bad_version)
    bad_mappings = dict(snap)
    bad_mappings["mappings"] = "not-a-list"
    corrupt.append(bad_mappings)
    overlap = dict(snap)
    clone = dict(snap["mappings"][0])
    clone["namespace"] = "clone"
    overlap["mappings"] = [snap["mappings"][0], clone]
    corrupt.append(overlap)
    bad_stats = dict(snap)
    bad_stats["stats"] = {"reserved": "many"}
    corrupt.append(bad_stats)

    for state in corrupt:
        with pytest.raises(ug.IdmapError) as exc:
            fresh.restore(state)
        assert exc.value.code == "IDMAP_SNAPSHOT_INVALID"
    assert [row["namespace"] for row in fresh.mappings()] == ["alpha", "beta"]
    assert fresh.occupancy()["used"] == 30
    assert fresh.stats()["reserved"] == 2
    assert fresh.stats()["assigned"] == 2


def test_concurrent_reserve_is_single_winner_and_overlap_race_is_exclusive():
    guard = make()
    barrier = threading.Barrier(8)
    rows = []
    lock = threading.Lock()

    def reserve_worker():
        barrier.wait()
        try:
            out = guard.reserve("shared", container_start=0,
                                container_count=100)
        except Exception as exc:  # pragma: no cover - stub path only
            out = {"idempotent": None, "boom": type(exc).__name__}
        with lock:
            rows.append(out)

    threads = [threading.Thread(target=reserve_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(rows) == 8
    assert sum(1 for row in rows if row["idempotent"] is False) == 1
    assert guard.occupancy()["used"] == 100
    assert guard.stats()["reserved"] == 1
    assert guard.stats()["idempotent"] == 7

    racer = make()
    gate = threading.Barrier(2)
    outcomes = []
    olock = threading.Lock()

    def grab(namespace):
        gate.wait()
        try:
            result = ("ok", racer.reserve(namespace, container_start=0,
                                          container_count=100,
                                          host_start=700000))
        except ug.IdmapError as exc:
            result = ("err", exc.code)
        except Exception as exc:  # pragma: no cover - stub path only
            result = ("boom", type(exc).__name__)
        with olock:
            outcomes.append(result)

    threads = [threading.Thread(target=grab, args=(ns,))
               for ns in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(item[0] for item in outcomes) == ["err", "ok"]
    assert [item[1] for item in outcomes if item[0] == "err"] == ["IDMAP_OVERLAP"]
    assert racer.occupancy()["used"] == 100
