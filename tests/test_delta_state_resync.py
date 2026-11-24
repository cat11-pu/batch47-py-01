import threading

import pytest

import delta_state_resync as dr


def make(**kwargs):
    engine = dr.DeltaStateResync(**kwargs)
    engine.set_initial({"hp": 100, "mana": 50, "zone": "a"})
    return engine


def test_initial_state_can_only_be_installed_once():
    engine = dr.DeltaStateResync(max_keys=3)
    with pytest.raises(dr.ResyncError) as exc:
        engine.commit({"hp": 1})
    assert exc.value.code == "RESYNC_NOT_READY"

    first = engine.set_initial({"hp": 100, "mana": 50, "zone": "a"})
    assert first == {"created": True, "seq": 0, "keys": 3,
                     "checksum": engine.checksum()}
    with pytest.raises(dr.ResyncError) as exc:
        engine.set_initial({"hp": 1})
    assert exc.value.code == "RESYNC_ALREADY_INITIALIZED"

    with pytest.raises(dr.ResyncError) as exc:
        dr.DeltaStateResync(max_keys=2).set_initial({"a": 1, "b": 2, "c": 3})
    assert exc.value.code == "RESYNC_KEY_LIMIT"
    for bad in ({"": 1}, {"hp": [1]}, {"hp": None}):
        with pytest.raises(dr.ResyncError) as exc:
            dr.DeltaStateResync().set_initial(bad)
        assert exc.value.code == "RESYNC_BAD_STATE"


def test_commit_applies_sets_and_tombstones_and_exposes_get():
    engine = make()
    out = engine.commit({"hp": 80, "mana": None, "buff": "rage"})
    assert out["seq"] == 1
    assert out["applied"] == 3
    assert out["keys"] == 3
    assert out["checksum"] == engine.checksum()
    assert engine.state() == {"hp": 80, "zone": "a", "buff": "rage"}
    assert engine.get("hp") == 80
    with pytest.raises(dr.ResyncError) as exc:
        engine.get("mana")
    assert exc.value.code == "RESYNC_UNKNOWN_KEY"

    noop = engine.commit({"hp": 80, "ghost": None})
    assert (noop["seq"], noop["applied"]) == (2, 0)
    assert engine.journal_span() == {"oldest_seq": 1, "current_seq": 2, "size": 2}


def test_oversized_patch_is_rejected_without_consuming_a_sequence():
    engine = make(max_patch_ops=2)
    with pytest.raises(dr.ResyncError) as exc:
        engine.commit({"hp": 1, "mana": 2, "zone": "b"})
    assert exc.value.code == "RESYNC_PATCH_TOO_LARGE"
    assert exc.value.detail == {"size": 3, "limit": 2}
    assert engine.state() == {"hp": 100, "mana": 50, "zone": "a"}
    assert engine.journal_span()["current_seq"] == 0

    assert engine.commit({"hp": 90, "zone": "b"})["seq"] == 1


def test_key_limit_is_enforced_atomically():
    engine = make(max_keys=3)
    with pytest.raises(dr.ResyncError) as exc:
        engine.commit({"new": 1})
    assert exc.value.code == "RESYNC_KEY_LIMIT"
    assert engine.state() == {"hp": 100, "mana": 50, "zone": "a"}

    with pytest.raises(dr.ResyncError):
        engine.commit({"zone": "b", "new": 1})
    assert engine.get("zone") == "a"
    assert engine.journal_span()["current_seq"] == 0

    engine.commit({"mana": None})
    added = engine.commit({"new": 1})
    assert added["keys"] == 3
    assert engine.get("new") == 1


def test_delta_resync_returns_final_values_and_tombstones_in_key_order():
    engine = make()
    engine.commit({"hp": 90, "zone": "b"})
    engine.commit({"hp": 80, "mana": None})
    engine.commit({"buff": "haste"})

    delta = engine.resync(client_seq=1, max_ops=10)
    assert delta["mode"] == "delta"
    assert delta["base_seq"] == 1
    assert delta["seq"] == 3
    assert delta["op_count"] == 3
    assert delta["ops"] == [
        {"op": "set", "key": "buff", "value": "haste"},
        {"op": "set", "key": "hp", "value": 80},
        {"op": "del", "key": "mana"},
    ]
    assert delta["checksum"] == engine.checksum()

    from_zero = engine.resync(client_seq=0)
    assert [row["key"] for row in from_zero["ops"]] == ["buff", "hp", "mana", "zone"]
    assert from_zero["ops"][-1] == {"op": "set", "key": "zone", "value": "b"}


def test_resync_at_current_sequence_is_a_noop_and_future_is_refused():
    engine = make()
    engine.commit({"hp": 90})
    noop = engine.resync(client_seq=1)
    assert noop["mode"] == "noop"
    assert noop["ops"] == []
    assert noop["base_seq"] == 1
    assert noop["seq"] == 1
    assert noop["op_count"] == 0

    with pytest.raises(dr.ResyncError) as exc:
        engine.resync(client_seq=2)
    assert exc.value.code == "RESYNC_FUTURE_SEQ"
    with pytest.raises(dr.ResyncError) as exc:
        engine.resync(client_seq=-1)
    assert exc.value.code == "RESYNC_BAD_SEQ"
    assert engine.journal_span()["current_seq"] == 1


def test_journal_eviction_forces_a_full_snapshot_for_old_clients():
    engine = make(max_journal=2)
    engine.commit({"alpha": 1})
    engine.commit({"beta": 2})
    engine.commit({"gamma": 3})
    assert engine.journal_span() == {"oldest_seq": 2, "current_seq": 3, "size": 2}

    delta = engine.resync(client_seq=1)
    assert delta["mode"] == "delta"
    assert [row["key"] for row in delta["ops"]] == ["beta", "gamma"]

    full = engine.resync(client_seq=0)
    assert full["mode"] == "full"
    assert full["base_seq"] == 0
    assert [row["key"] for row in full["ops"]] == [
        "alpha", "beta", "gamma", "hp", "mana", "zone",
    ]
    assert engine.stats()["journal_evicted"] == 1


def test_compact_drops_old_journal_entries_and_reports_the_count():
    engine = make(max_journal=10)
    for index in range(1, 5):
        engine.commit({f"k{index}": index})
    assert engine.journal_span() == {"oldest_seq": 1, "current_seq": 4, "size": 4}

    assert engine.compact(2) == 2
    assert engine.journal_span() == {"oldest_seq": 3, "current_seq": 4, "size": 2}
    assert engine.resync(client_seq=2)["mode"] == "delta"
    assert engine.resync(client_seq=1)["mode"] == "full"

    assert engine.compact(0) == 2
    assert engine.journal_span() == {"oldest_seq": None, "current_seq": 4, "size": 0}
    assert engine.resync(client_seq=4)["mode"] == "noop"
    assert engine.resync(client_seq=3)["mode"] == "full"
    assert engine.compact(99) == 0

    with pytest.raises(dr.ResyncError) as exc:
        engine.compact(-1)
    assert exc.value.code == "RESYNC_BAD_COMPACT"


def test_explicit_max_ops_downgrades_a_large_delta_to_full():
    engine = make()
    engine.commit({"alpha": 1})
    engine.commit({"beta": 2})
    engine.commit({"gamma": 3})
    limited = engine.resync(client_seq=0, max_ops=2)
    assert limited["mode"] == "full"
    assert [row["key"] for row in limited["ops"]] == [
        "alpha", "beta", "gamma", "hp", "mana", "zone",
    ]
    enough = engine.resync(client_seq=0, max_ops=6)
    assert enough["mode"] == "delta"
    with pytest.raises(dr.ResyncError) as exc:
        engine.resync(client_seq=0, max_ops=-1)
    assert exc.value.code == "RESYNC_BAD_LIMIT"


def test_full_resync_carries_every_current_key_and_the_same_checksum():
    engine = make()
    engine.commit({"hp": 75, "mana": None})
    engine.commit({"shield": 12})
    full = engine.resync(client_seq=0, max_ops=1)
    assert full["mode"] == "full"
    assert full["seq"] == 2
    assert full["op_count"] == len(engine.state())
    assert full["ops"] == [
        {"op": "set", "key": "hp", "value": 75},
        {"op": "set", "key": "shield", "value": 12},
        {"op": "set", "key": "zone", "value": "a"},
    ]
    assert full["checksum"] == engine.checksum()


def test_snapshot_restore_rolls_back_state_journal_and_stats():
    engine = make()
    engine.commit({"hp": 90})
    engine.commit({"mana": 40})
    state = engine.snapshot()
    assert state["version"] == 1
    assert state["initialized"] is True

    tampered = engine.snapshot()
    tampered["state"]["hp"] = 1
    tampered["journal"][0]["changes"]["hp"] = 1
    assert engine.get("hp") == 90

    engine.commit({"hp": 70})
    engine.commit({"rage": 1})
    engine.restore(state)
    assert engine.get("hp") == 90
    assert engine.get("mana") == 40
    with pytest.raises(dr.ResyncError):
        engine.get("rage")
    assert engine.journal_span() == {"oldest_seq": 1, "current_seq": 2, "size": 2}
    assert engine.stats()["commits"] == 2
    assert engine.resync(client_seq=1)["ops"] == [
        {"op": "set", "key": "mana", "value": 40},
    ]


def test_corrupt_snapshot_is_rejected_without_touching_state():
    engine = make()
    engine.commit({"hp": 90})
    good = engine.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, initialized="yes"),
        dict(good, seq=-1),
        dict(good, state=[]),
        dict(good, journal="nope"),
        dict(good, stats={"commits": -1, "keys_written": 0, "keys_deleted": 0,
                          "resync_delta": 0, "resync_full": 0,
                          "journal_evicted": 0}),
    ]
    for bad in bad_states:
        with pytest.raises(dr.ResyncError) as exc:
            engine.restore(bad)
        assert exc.value.code == "RESYNC_SNAPSHOT_INVALID"

    assert engine.get("hp") == 90
    assert engine.journal_span()["current_seq"] == 1
    assert engine.commit({"hp": 80})["seq"] == 2


def test_remove_keys_skips_absent_keys_and_rejects_empty_input():
    engine = make()
    out = engine.remove_keys(["mana", "ghost"])
    assert out["seq"] == 1
    assert out["applied"] == 1
    assert out["keys"] == 2
    assert engine.resync(client_seq=0)["ops"] == [{"op": "del", "key": "mana"}]
    assert engine.stats()["keys_deleted"] == 1

    for bad in ([], ["ok", 3]):
        with pytest.raises(dr.ResyncError) as exc:
            engine.remove_keys(bad)
        assert exc.value.code == "RESYNC_BAD_PATCH"
    assert engine.journal_span()["current_seq"] == 1


def test_checksum_is_order_independent_and_changes_with_content():
    first = dr.DeltaStateResync()
    second = dr.DeltaStateResync()
    first.set_initial({"a": 1, "b": 2})
    second.set_initial({"b": 2, "a": 1})
    assert first.checksum() == second.checksum()
    assert len(first.checksum()) == 12

    first.commit({"a": 3, "b": 4})
    second.commit({"b": 4})
    second.commit({"a": 3})
    assert first.checksum() == second.checksum()
    assert first.state() == second.state()

    second.commit({"a": 5})
    assert first.checksum() != second.checksum()
    assert first.resync(client_seq=1)["checksum"] == first.checksum()


def test_concurrent_commits_do_not_lose_sequences_or_keys():
    engine = dr.DeltaStateResync()
    engine.set_initial({"base": 0})
    barrier = threading.Barrier(8)
    results = []
    guard = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            row = engine.commit({f"k{index}": index})
        except Exception as exc:  # pragma: no cover - surfaced below
            row = {"seq": -1, "error": repr(exc)}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(row["seq"] for row in results) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert sum(row["applied"] for row in results) == 8
    assert engine.journal_span() == {"oldest_seq": 1, "current_seq": 8, "size": 8}
    assert len(engine.state()) == 9
    assert engine.stats()["commits"] == 8
    assert engine.stats()["keys_written"] == 8
    assert engine.resync(client_seq=0)["op_count"] == 8
