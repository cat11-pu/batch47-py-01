import threading

import pytest

import inventory_stack_manager as inv

PLAYER = "p1"


def manager(**kwargs):
    return inv.InventoryStackManager(**kwargs)


def test_grant_fills_partial_stacks_before_opening_new_slots():
    box = manager(stack_size=10, bag_slots=3)
    first = box.grant(PLAYER, "ore", 7)
    assert first == {"item": "ore", "bound": False, "added": 7,
                     "slots": [0], "total": 7}
    second = box.grant(PLAYER, "ore", 6)
    assert second["slots"] == [0, 1]
    assert second["total"] == 13
    assert box.bag(PLAYER) == [
        {"slot": 0, "item": "ore", "count": 10, "bound": False},
        {"slot": 1, "item": "ore", "count": 3, "bound": False},
    ]


def test_grant_is_atomic_when_slots_run_out():
    box = manager(stack_size=5, bag_slots=1)
    box.grant(PLAYER, "ore", 5)
    with pytest.raises(inv.InventoryError) as exc:
        box.grant(PLAYER, "ore", 1)
    assert exc.value.code == "INV_NO_SPACE"
    assert box.bag(PLAYER)[0]["count"] == 5
    assert box.stats()["granted"] == 5
    assert box.stats()["rejected"] == 1


def test_bound_and_unbound_copies_never_share_a_stack():
    box = manager(stack_size=99)
    box.grant(PLAYER, "sword", 1, bound=False)
    box.grant(PLAYER, "sword", 2, bound=True)
    rows = box.bag(PLAYER)
    assert [(row["slot"], row["bound"], row["count"]) for row in rows] == [
        (0, False, 1), (1, True, 2)]


def test_remove_prefers_unbound_copies_and_leaves_state_on_shortfall():
    box = manager(stack_size=99)
    box.grant(PLAYER, "ore", 2, bound=False)
    box.grant(PLAYER, "ore", 3, bound=True)
    taken = box.remove(PLAYER, "ore", 4)
    assert taken["removed"] == 4
    assert taken["remaining"] == 1
    assert box.bag(PLAYER) == [
        {"slot": 1, "item": "ore", "count": 1, "bound": True}]

    with pytest.raises(inv.InventoryError) as exc:
        box.remove(PLAYER, "ore", 5)
    assert exc.value.code == "INV_INSUFFICIENT"
    assert box.bag(PLAYER)[0]["count"] == 1


def test_split_uses_next_free_slot_and_rejects_bad_counts():
    box = manager(stack_size=99, bag_slots=2)
    box.grant(PLAYER, "ore", 10)
    split = box.split_stack(PLAYER, 0, 4)
    assert split == {"from_slot": 0, "to_slot": 1, "item": "ore", "count": 4}
    assert box.bag(PLAYER) == [
        {"slot": 0, "item": "ore", "count": 6, "bound": False},
        {"slot": 1, "item": "ore", "count": 4, "bound": False},
    ]
    with pytest.raises(inv.InventoryError) as exc:
        box.split_stack(PLAYER, 1, 4)
    assert exc.value.code == "INV_INVALID_SPLIT"
    with pytest.raises(inv.InventoryError) as exc:
        box.split_stack(PLAYER, 9, 1)
    assert exc.value.code == "INV_BAD_REQUEST"

    tight = manager(stack_size=99, bag_slots=1)
    tight.grant(PLAYER, "ore", 10)
    with pytest.raises(inv.InventoryError) as exc:
        tight.split_stack(PLAYER, 0, 1)
    assert exc.value.code == "INV_NO_SPACE"


def test_merge_requires_matching_item_and_binding_and_respects_cap():
    box = manager(stack_size=10)
    box.grant(PLAYER, "ore", 10)
    box.split_stack(PLAYER, 0, 4)
    merged = box.merge_stacks(PLAYER, 1, 0)
    assert merged == {"from_slot": 1, "to_slot": 0, "item": "ore", "total": 10}
    with pytest.raises(inv.InventoryError) as exc:
        box.merge_stacks(PLAYER, 0, 0)
    assert exc.value.code == "INV_BAD_REQUEST"

    mixed = manager(stack_size=99)
    mixed.grant(PLAYER, "gem", 3, bound=False)
    mixed.grant(PLAYER, "gem", 2, bound=True)
    with pytest.raises(inv.InventoryError) as exc:
        mixed.merge_stacks(PLAYER, 0, 1)
    assert exc.value.code == "INV_INCOMPATIBLE"

    overflow = manager(stack_size=10)
    overflow.grant(PLAYER, "ore", 6)
    overflow.grant(PLAYER, "ore", 6)
    with pytest.raises(inv.InventoryError) as exc:
        overflow.merge_stacks(PLAYER, 1, 0)
    assert exc.value.code == "INV_STACK_OVERFLOW"


def test_move_to_warehouse_stacks_and_stops_at_capacity():
    box = manager(stack_size=5, bag_slots=3, warehouse_slots=1)
    box.grant(PLAYER, "ore", 8)
    moved = box.move_to_warehouse(PLAYER, "ore", 5)
    assert moved == {"item": "ore", "moved": 5, "bound": False,
                     "warehouse_total": 5}
    with pytest.raises(inv.InventoryError) as exc:
        box.move_to_warehouse(PLAYER, "ore", 3)
    assert exc.value.code == "INV_WAREHOUSE_FULL"
    assert box.warehouse(PLAYER)[0]["count"] == 5
    assert box.bag(PLAYER)[0]["count"] == 3


def test_move_to_bag_rejects_when_bag_has_no_room():
    box = manager(stack_size=10, bag_slots=1, warehouse_slots=1)
    box.grant(PLAYER, "ore", 10)
    box.move_to_warehouse(PLAYER, "ore", 10)
    box.grant(PLAYER, "ore", 10)
    with pytest.raises(inv.InventoryError) as exc:
        box.move_to_bag(PLAYER, "ore", 1)
    assert exc.value.code == "INV_NO_SPACE"
    assert box.warehouse(PLAYER)[0]["count"] == 10
    assert box.bag(PLAYER)[0]["count"] == 10


def test_discard_removes_units_and_frees_the_slot():
    box = manager(stack_size=99)
    box.grant(PLAYER, "ore", 5)
    assert box.discard(PLAYER, 0, 2) == {"slot": 0, "item": "ore",
                                         "discarded": 2}
    assert box.bag(PLAYER)[0]["count"] == 3
    box.discard(PLAYER, 0, 3)
    assert box.bag(PLAYER) == []
    with pytest.raises(inv.InventoryError) as exc:
        box.discard(PLAYER, 0, 1)
    assert exc.value.code == "INV_BAD_REQUEST"

    box.grant(PLAYER, "ore", 2)
    with pytest.raises(inv.InventoryError) as exc:
        box.discard(PLAYER, 0, 3)
    assert exc.value.code == "INV_INSUFFICIENT"
    assert box.bag(PLAYER)[0]["count"] == 2


def test_capacity_reports_both_stores():
    box = manager(bag_slots=4, warehouse_slots=6)
    assert box.capacity(PLAYER) == {"bag_used": 0, "bag_slots": 4,
                                    "warehouse_used": 0, "warehouse_slots": 6}
    box.grant(PLAYER, "ore", 1)
    box.grant(PLAYER, "herb", 1)
    box.move_to_warehouse(PLAYER, "ore", 1)
    assert box.capacity(PLAYER) == {"bag_used": 1, "bag_slots": 4,
                                    "warehouse_used": 1, "warehouse_slots": 6}


def test_snapshot_restore_keeps_both_stores_and_ignores_later_writes():
    box = manager(stack_size=10, bag_slots=3, warehouse_slots=3)
    box.grant(PLAYER, "ore", 5)
    box.move_to_warehouse(PLAYER, "ore", 2)
    state = box.snapshot()
    box.grant(PLAYER, "ore", 3)

    restored = manager(stack_size=10, bag_slots=3, warehouse_slots=3)
    restored.restore(state)
    assert restored.bag(PLAYER) == [
        {"slot": 0, "item": "ore", "count": 3, "bound": False}]
    assert restored.warehouse(PLAYER) == [
        {"slot": 0, "item": "ore", "count": 2, "bound": False}]
    assert restored.grant(PLAYER, "ore", 1)["total"] == 4
    assert box.grant(PLAYER, "ore", 1)["total"] == 7


def test_corrupt_snapshot_is_rejected_without_mutation():
    box = manager(stack_size=10)
    box.grant(PLAYER, "ore", 4)
    bad_states = [
        None,
        {},
        {"version": 2, "players": {}, "stats": {}},
        {"version": 1, "players": [], "stats": {}},
        {"version": 1, "players": {}, "stats": []},
        {"version": 1, "players": {PLAYER: {"bag": [{"slot": 0, "item": "ore",
                                                     "count": 11, "bound": False}],
                                            "warehouse": []}}, "stats": {}},
        {"version": 1, "players": {PLAYER: {"bag": [{"slot": 0, "item": "ore",
                                                     "count": 1, "bound": False},
                                                    {"slot": 0, "item": "ore",
                                                     "count": 1, "bound": False}],
                                            "warehouse": []}}, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(inv.InventoryError) as exc:
            box.restore(bad)
        assert exc.value.code == "INV_SNAPSHOT_INVALID"
    assert box.bag(PLAYER)[0]["count"] == 4


def test_concurrent_grants_never_exceed_slots_or_lose_units():
    box = manager(stack_size=10, bag_slots=2)
    barrier = threading.Barrier(20)
    errors = []

    def worker():
        barrier.wait()
        try:
            box.grant(PLAYER, "ore", 1)
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    rows = box.bag(PLAYER)
    assert len(rows) == 2
    assert [row["count"] for row in rows] == [10, 10]
    assert box.stats()["granted"] == 20


def test_concurrent_moves_conserve_the_total():
    box = manager(stack_size=99, bag_slots=2, warehouse_slots=5)
    box.grant(PLAYER, "ore", 50)
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        box.move_to_warehouse(PLAYER, "ore", 5)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    bag_total = sum(row["count"] for row in box.bag(PLAYER))
    store_total = sum(row["count"] for row in box.warehouse(PLAYER))
    assert (bag_total, store_total) == (0, 50)
    assert box.stats()["moved"] == 50


def test_stats_and_reset():
    box = manager(stack_size=10, bag_slots=4, warehouse_slots=3)
    box.grant(PLAYER, "ore", 6)
    box.grant(PLAYER, "ore", 6)
    box.remove(PLAYER, "ore", 3)
    box.move_to_warehouse(PLAYER, "ore", 4)
    box.grant(PLAYER, "gem", 3, bound=True)
    box.split_stack(PLAYER, 0, 1)
    box.merge_stacks(PLAYER, 1, 0)
    box.discard(PLAYER, 0, 1)
    with pytest.raises(inv.InventoryError):
        box.remove(PLAYER, "ore", 99)
    assert box.stats() == {"granted": 15, "removed": 3, "moved": 4,
                           "merged": 1, "split": 1, "discarded": 1,
                           "rejected": 1}
    box.reset_stats()
    assert box.stats() == {"granted": 0, "removed": 0, "moved": 0,
                           "merged": 0, "split": 0, "discarded": 0,
                           "rejected": 0}
