import threading

import pytest

import inventory_weight_transfer as iw


def service(**kwargs):
    svc = iw.InventoryTransferService(**kwargs)
    svc.define_item("ore", stack_limit=10, unit_weight=2)
    svc.define_item("potion", stack_limit=5, unit_weight=1)
    svc.create_container("bag", slots=6, weight_limit=100)
    svc.create_container("stash", slots=8, weight_limit=1000)
    svc.create_container("vault", slots=200, weight_limit=1000)
    return svc


def test_container_registration_is_idempotent_and_rejects_conflicts():
    svc = iw.InventoryTransferService()
    first = svc.create_container("bag", slots=4, weight_limit=50)
    assert first == {"container_id": "bag", "slots": 4, "weight_limit": 50}
    assert svc.create_container("bag", slots=4, weight_limit=50) == first
    with pytest.raises(iw.InventoryError) as exc:
        svc.create_container("bag", slots=5, weight_limit=50)
    assert exc.value.code == "INV_CONFLICT"
    with pytest.raises(iw.InventoryError) as exc:
        svc.create_container("broken", slots=0, weight_limit=50)
    assert exc.value.code == "INV_INVALID"


def test_item_definition_conflicts_and_unknown_references():
    svc = iw.InventoryTransferService()
    svc.create_container("bag", slots=4, weight_limit=50)
    defined = svc.define_item("ore", stack_limit=10, unit_weight=2)
    assert defined == {"item_id": "ore", "stack_limit": 10, "unit_weight": 2}
    assert svc.define_item("ore", stack_limit=10, unit_weight=2) == defined
    with pytest.raises(iw.InventoryError) as exc:
        svc.define_item("ore", stack_limit=9, unit_weight=2)
    assert exc.value.code == "INV_ITEM_CONFLICT"
    with pytest.raises(iw.InventoryError) as exc:
        svc.grant("bag", "ghost", 1)
    assert exc.value.code == "INV_UNKNOWN_ITEM"
    with pytest.raises(iw.InventoryError) as exc:
        svc.grant("vault", "ore", 1)
    assert exc.value.code == "INV_UNKNOWN_CONTAINER"


def test_grant_updates_slots_and_weight_usage():
    svc = service()
    result = svc.grant("bag", "ore", 12)
    assert result == {
        "container_id": "bag",
        "slots": 6,
        "slots_used": 2,
        "slots_free": 4,
        "weight": 24,
        "weight_limit": 100,
        "weight_free": 76,
        "over_by": 0,
        "over_encumbered": False,
    }
    svc.grant("bag", "potion", 5)
    usage = svc.usage("bag")
    assert usage["slots_used"] == 3
    assert usage["slots_free"] == 3
    assert usage["weight"] == 29
    assert usage["weight_free"] == 71


def test_grant_rejects_slot_overflow_without_state_change():
    svc = service()
    svc.grant("bag", "potion", 25)
    assert svc.usage("bag")["slots_used"] == 5
    with pytest.raises(iw.InventoryError) as exc:
        svc.grant("bag", "ore", 11)
    assert exc.value.code == "INV_SLOT_FULL"
    assert svc.usage("bag")["slots_used"] == 5
    assert svc.usage("bag")["weight"] == 25


def test_weight_limit_and_force_grant_over_encumbrance():
    svc = service()
    svc.grant("bag", "ore", 40)
    with pytest.raises(iw.InventoryError) as exc:
        svc.grant("bag", "ore", 11)
    assert exc.value.code == "INV_WEIGHT_EXCEEDED"
    usage = svc.force_grant("bag", "ore", 11)
    assert usage["weight"] == 102
    assert usage["over_by"] == 2
    assert usage["weight_free"] == 0
    assert usage["over_encumbered"] is True
    assert svc.is_over_encumbered("bag") is True


def test_move_merges_stacks_and_recomputes_slots():
    svc = service()
    svc.grant("bag", "ore", 15)
    svc.grant("stash", "ore", 8)
    result = svc.move("bag", "ore", to_container="stash", count=7)
    assert result["item_id"] == "ore"
    assert result["count"] == 7
    assert result["from"]["weight"] == 16
    assert result["from"]["slots_used"] == 1
    assert result["to"]["weight"] == 30
    assert result["to"]["slots_used"] == 2
    assert svc.usage("stash")["weight"] == 30


def test_move_rejects_insufficient_source_atomically():
    svc = service()
    svc.grant("bag", "ore", 3)
    with pytest.raises(iw.InventoryError) as exc:
        svc.move("bag", "ore", to_container="stash", count=4)
    assert exc.value.code == "INV_INSUFFICIENT"
    assert svc.usage("bag")["weight"] == 6
    assert svc.usage("stash")["weight"] == 0
    assert svc.stats()["moves"] == 0


def test_move_rejects_destination_weight_overflow():
    svc = service()
    svc.grant("bag", "ore", 50)
    svc.grant("vault", "ore", 495)
    with pytest.raises(iw.InventoryError) as exc:
        svc.move("bag", "ore", to_container="vault", count=6)
    assert exc.value.code == "INV_WEIGHT_EXCEEDED"
    assert svc.usage("bag")["weight"] == 100
    assert svc.usage("vault")["weight"] == 990


def test_move_argument_validation():
    svc = service()
    svc.grant("bag", "ore", 5)
    for bad in (0, -1):
        with pytest.raises(iw.InventoryError) as exc:
            svc.move("bag", "ore", to_container="stash", count=bad)
        assert exc.value.code == "INV_INVALID"
    with pytest.raises(iw.InventoryError) as exc:
        svc.move("bag", "ore", to_container="bag", count=1)
    assert exc.value.code == "INV_INVALID"
    with pytest.raises(iw.InventoryError) as exc:
        svc.usage("ghost")
    assert exc.value.code == "INV_UNKNOWN_CONTAINER"


def test_batch_move_applies_all_operations():
    svc = service()
    svc.grant("bag", "ore", 20)
    svc.grant("bag", "potion", 10)
    result = svc.move_batch(
        [
            {"from": "bag", "to": "stash", "item_id": "ore", "count": 5},
            {"from": "bag", "to": "stash", "item_id": "potion", "count": 4},
        ],
        request_id="batch-1",
    )
    assert result["count"] == 2
    assert result["moved_units"] == 9
    assert [row["item_id"] for row in result["results"]] == ["ore", "potion"]
    assert svc.usage("stash")["weight"] == 14
    assert svc.stats()["batches"] == 1
    assert svc.stats()["moves"] == 2
    assert svc.stats()["moved_units"] == 9


def test_batch_move_rolls_back_everything_on_failure():
    svc = service()
    svc.grant("bag", "ore", 10)
    with pytest.raises(iw.InventoryError) as exc:
        svc.move_batch(
            [
                {"from": "bag", "to": "stash", "item_id": "ore", "count": 4},
                {"from": "bag", "to": "stash", "item_id": "ore", "count": 100},
            ]
        )
    assert exc.value.code == "INV_INSUFFICIENT"
    assert svc.usage("bag")["weight"] == 20
    assert svc.usage("stash")["weight"] == 0
    assert svc.stats()["rejected"] == 1
    assert svc.stats()["batches"] == 0


def test_batch_limits_and_request_conflicts():
    svc = service(max_batch=2)
    svc.grant("bag", "ore", 20)
    ops = [{"from": "bag", "to": "stash", "item_id": "ore", "count": 1}
           for _ in range(3)]
    with pytest.raises(iw.InventoryError) as exc:
        svc.move_batch(ops)
    assert exc.value.code == "INV_BATCH_TOO_LARGE"
    first = svc.move("bag", "ore", to_container="stash", count=2,
                     request_id="req-1")
    assert first["count"] == 2
    with pytest.raises(iw.InventoryError) as exc:
        svc.move("bag", "ore", to_container="stash", count=3,
                 request_id="req-1")
    assert exc.value.code == "INV_REQUEST_CONFLICT"
    assert svc.usage("stash")["weight"] == 4


def test_request_id_replay_returns_cached_result_without_moving_again():
    svc = service()
    svc.grant("bag", "ore", 20)
    first = svc.move("bag", "ore", to_container="stash", count=5,
                     request_id="dup-1")
    second = svc.move("bag", "ore", to_container="stash", count=5,
                      request_id="dup-1")
    assert second == first
    assert svc.usage("stash")["weight"] == 10
    assert svc.usage("bag")["weight"] == 30
    assert svc.stats()["moves"] == 1


def test_concurrent_moves_of_last_units_succeed_once():
    svc = service()
    svc.grant("bag", "ore", 1)
    barrier = threading.Barrier(6)
    outcomes = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = svc.move("bag", "ore", to_container="stash", count=1)
            row = ("ok", out["count"])
        except iw.InventoryError as exc:
            row = ("err", exc.code)
        except Exception as exc:  # pragma: no cover
            row = ("boom", repr(exc))
        with guard:
            outcomes.append(row)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [row[0] for row in outcomes].count("ok") == 1
    errors = sorted({code for kind, code in outcomes if kind == "err"})
    assert errors == ["INV_INSUFFICIENT"]
    assert svc.usage("stash")["weight"] == 2
    assert svc.usage("bag")["weight"] == 0


def test_snapshot_restore_is_deep_and_corrupt_state_is_rejected():
    svc = service()
    svc.grant("bag", "ore", 10)
    snap = svc.snapshot()
    assert snap["version"] == 1
    svc.grant("stash", "potion", 5)
    restored = iw.InventoryTransferService()
    restored.restore(snap)
    assert restored.usage("bag")["weight"] == 20
    assert restored.usage("stash")["weight"] == 0
    snap["containers"]["bag"]["slots"] = 999
    assert restored.usage("bag")["slots"] == 6
    bad_states = [
        None,
        {"version": 2, "items": {}, "containers": {}, "stats": {}},
        {"version": 1, "items": {},
         "containers": {"x": {"slots": 0, "weight_limit": 10, "items": {}}},
         "stats": {}},
    ]
    for state in bad_states:
        with pytest.raises(iw.InventoryError) as exc:
            restored.restore(state)
        assert exc.value.code == "INV_SNAPSHOT_INVALID"
    assert restored.usage("bag")["weight"] == 20
