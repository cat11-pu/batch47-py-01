import threading

import pytest

import equipment_durability_repair as dr


def ledger(**kwargs):
    l = dr.DurabilityLedger(**kwargs)
    l.grant_currency("p1", 1000)
    return l


def test_register_equipment_is_idempotent_and_conflict_aware():
    l = ledger()
    first = l.register("p1", "sword", max_durability=10, tier=1)
    assert first == {"player": "p1", "item_id": "sword", "max_durability": 10,
                     "tier": 1, "durability": 10}
    assert l.register("p1", "sword", max_durability=10, tier=1) == first
    with pytest.raises(dr.DurabilityError) as exc:
        l.register("p1", "sword", max_durability=12, tier=1)
    assert exc.value.code == "DUR_CONFLICT"
    for bad in ({"max_durability": 0, "tier": 1},
                {"max_durability": 10, "tier": 0},
                {"max_durability": 10, "tier": 6}):
        with pytest.raises(dr.DurabilityError) as exc:
            l.register("p1", "axe", **bad)
        assert exc.value.code == "DUR_INVALID"


def test_unknown_items_and_invalid_grants():
    l = ledger()
    with pytest.raises(dr.DurabilityError) as exc:
        l.durability("p1", "ghost")
    assert exc.value.code == "DUR_UNKNOWN_ITEM"
    with pytest.raises(dr.DurabilityError) as exc:
        l.use("p1", "ghost")
    assert exc.value.code == "DUR_UNKNOWN_ITEM"
    with pytest.raises(dr.DurabilityError) as exc:
        l.grant_currency("p1", -1)
    assert exc.value.code == "DUR_INVALID"


def test_use_wears_durability_down_and_breaks_at_zero():
    l = ledger()
    l.register("p1", "sword", max_durability=10, tier=2)
    out = l.use("p1", "sword")
    assert out == {"player": "p1", "item_id": "sword", "durability": 9,
                   "max_durability": 10, "broken": False}
    out = l.use("p1", "sword", wear=20)
    assert out["durability"] == 0
    assert out["broken"] is True
    assert l.durability("p1", "sword") == 0
    assert l.stats()["uses"] == 2
    assert l.stats()["wear"] == 21


def test_broken_items_reject_further_use():
    l = ledger()
    l.register("p1", "sword", max_durability=3, tier=1)
    l.register("p1", "axe", max_durability=5, tier=2)
    l.use("p1", "sword", wear=3)
    with pytest.raises(dr.DurabilityError) as exc:
        l.use("p1", "sword")
    assert exc.value.code == "DUR_BROKEN"
    assert l.is_broken("p1", "sword") is True
    assert l.is_broken("p1", "axe") is False
    assert l.broken_items("p1") == ["sword"]
    assert l.durability("p1", "sword") == 0


def test_use_rejects_non_positive_wear():
    l = ledger()
    l.register("p1", "sword", max_durability=10, tier=1)
    for bad in (0, -2):
        with pytest.raises(dr.DurabilityError) as exc:
            l.use("p1", "sword", wear=bad)
        assert exc.value.code == "DUR_INVALID"
    assert l.durability("p1", "sword") == 10


def test_repair_cost_applies_floor_fee():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 1000)
    l.register("p1", "sword", max_durability=10, tier=1)
    l.use("p1", "sword", wear=1)
    cost = l.repair_cost("p1", "sword")
    assert cost == {"item_id": "sword", "points": 1, "unit_price": 5,
                    "gross": 5, "discount": 0, "cost": 10}


def test_repair_cost_applies_discount_with_floor_division():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 1000)
    l.register("p1", "blade", max_durability=10, tier=3)
    l.use("p1", "blade", wear=9)
    cost = l.repair_cost("p1", "blade", discount=15)
    assert cost["points"] == 9
    assert cost["unit_price"] == 15
    assert cost["gross"] == 135
    assert cost["discount"] == 15
    assert cost["cost"] == 114
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair_cost("p1", "blade", discount=51)
    assert exc.value.code == "DUR_INVALID"


def test_repair_restores_full_durability_and_charges():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 1000)
    l.register("p1", "blade", max_durability=10, tier=2)
    l.use("p1", "blade", wear=4)
    out = l.repair("p1", "blade")
    assert out == {"player": "p1", "item_id": "blade", "points": 4,
                   "cost": 40, "durability": 10, "balance": 960}
    assert l.durability("p1", "blade") == 10
    assert l.stats()["repairs"] == 1
    assert l.stats()["spent"] == 40
    assert l.stats()["repaired_points"] == 4


def test_repair_points_must_be_within_missing_range():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 1000)
    l.register("p1", "blade", max_durability=10, tier=1)
    l.use("p1", "blade", wear=6)
    out = l.repair("p1", "blade", points=2)
    assert out["points"] == 2
    assert out["cost"] == 10
    assert out["durability"] == 6
    assert out["balance"] == 990
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair("p1", "blade", points=5)
    assert exc.value.code == "DUR_INVALID"
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair("p1", "blade", points=0)
    assert exc.value.code == "DUR_INVALID"
    l.repair("p1", "blade", points=4)
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair("p1", "blade")
    assert exc.value.code == "DUR_NOT_DAMAGED"
    l.use("p1", "blade", wear=20)
    broken = l.repair("p1", "blade")
    assert broken["points"] == 10
    assert broken["durability"] == 10
    assert l.balance("p1") == 920


def test_repair_without_funds_changes_nothing():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 10)
    l.register("p1", "blade", max_durability=10, tier=3)
    l.use("p1", "blade", wear=5)
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair("p1", "blade")
    assert exc.value.code == "DUR_INSUFFICIENT_FUNDS"
    assert l.durability("p1", "blade") == 5
    assert l.balance("p1") == 10
    assert l.stats()["repairs"] == 0


def test_repair_request_id_is_idempotent_and_conflicts_on_payload_change():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 1000)
    l.register("p1", "blade", max_durability=10, tier=1)
    l.use("p1", "blade", wear=5)
    first = l.repair("p1", "blade", request_id="rq-1")
    second = l.repair("p1", "blade", request_id="rq-1")
    assert second == first
    assert l.balance("p1") == 975
    assert l.stats()["repairs"] == 1
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair("p1", "blade", points=2, request_id="rq-1")
    assert exc.value.code == "DUR_REQUEST_CONFLICT"


def test_repair_all_is_atomic_and_sums_per_item_floor_costs():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 30)
    l.register("p1", "blade", max_durability=10, tier=1)
    l.register("p1", "axe", max_durability=10, tier=2)
    l.use("p1", "blade", wear=1)
    l.use("p1", "axe", wear=3)
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair_all("p1")
    assert exc.value.code == "DUR_INSUFFICIENT_FUNDS"
    assert l.balance("p1") == 30
    assert l.durability("p1", "blade") == 9
    assert l.durability("p1", "axe") == 7
    l.grant_currency("p1", 100)
    out = l.repair_all("p1")
    assert out == {"player": "p1", "repaired": 2, "cost": 40, "balance": 90}
    assert l.durability("p1", "blade") == 10
    assert l.durability("p1", "axe") == 10


def test_repair_all_batch_limit_and_empty_noop():
    l = dr.DurabilityLedger(base_fee=10, per_point=5, max_batch=2)
    l.grant_currency("p1", 1000)
    for name in ("a", "b", "c"):
        l.register("p1", name, max_durability=5, tier=1)
        l.use("p1", name, wear=1)
    with pytest.raises(dr.DurabilityError) as exc:
        l.repair_all("p1")
    assert exc.value.code == "DUR_BATCH_TOO_LARGE"
    empty = dr.DurabilityLedger(base_fee=10, per_point=5)
    empty.grant_currency("p2", 10)
    empty.register("p2", "a", max_durability=5, tier=1)
    assert empty.repair_all("p2") == {"player": "p2", "repaired": 0,
                                      "cost": 0, "balance": 10}


def test_concurrent_repair_charges_only_once():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 1000)
    l.register("p1", "blade", max_durability=10, tier=1)
    l.use("p1", "blade", wear=4)
    barrier = threading.Barrier(6)
    outcomes = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            l.repair("p1", "blade")
            row = "repaired"
        except dr.DurabilityError as exc:
            row = exc.code
        except Exception as exc:  # pragma: no cover
            row = repr(exc)
        with guard:
            outcomes.append(row)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("repaired") == 1
    assert outcomes.count("DUR_NOT_DAMAGED") == 5
    assert l.balance("p1") == 980
    assert l.stats()["repairs"] == 1


def test_snapshot_restore_and_corrupt_rejection():
    l = dr.DurabilityLedger(base_fee=10, per_point=5)
    l.grant_currency("p1", 1000)
    l.register("p1", "blade", max_durability=10, tier=2)
    l.use("p1", "blade", wear=4)
    snap = l.snapshot()
    assert snap["version"] == 1
    l.use("p1", "blade", wear=1)
    l.grant_currency("p1", 500)
    restored = dr.DurabilityLedger(base_fee=10, per_point=5)
    restored.restore(snap)
    assert restored.durability("p1", "blade") == 6
    assert restored.balance("p1") == 1000
    assert restored.stats()["uses"] == 1
    assert restored.status("p1") == {"player": "p1", "items": 1, "broken": 0,
                                     "durability_total": 6, "max_total": 10}
    snap["players"]["p1"]["balance"] = 1
    assert restored.balance("p1") == 1000
    bad_states = [
        None,
        {},
        {"version": 2, "players": {}, "stats": {}},
        {"version": 1,
         "players": {"p1": {"items": "bad", "balance": 1}},
         "stats": {}},
    ]
    for state in bad_states:
        with pytest.raises(dr.DurabilityError) as exc:
            restored.restore(state)
        assert exc.value.code == "DUR_SNAPSHOT_INVALID"
    assert restored.durability("p1", "blade") == 6
