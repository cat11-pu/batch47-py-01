import threading

import pytest

import player_trade_escrow as te

BASE = 1000


def escrow(**kwargs):
    e = te.TradeEscrow(**kwargs)
    e.grant_items("alice", "sword", 1)
    e.grant_items("alice", "potion", 10)
    e.grant_items("bob", "shield", 1)
    e.grant_items("bob", "potion", 5)
    e.grant_currency("alice", 500)
    e.grant_currency("bob", 200)
    return e


def test_open_trade_rejects_self_partner_and_reused_ids():
    e = escrow()
    with pytest.raises(te.TradeError) as exc:
        e.open_trade("t-self", initiator="alice", partner="alice", now=BASE)
    assert exc.value.code == "TRADE_SELF"
    opened = e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    assert opened == {
        "trade_id": "t-1",
        "initiator": "alice",
        "partner": "bob",
        "status": "open",
        "expires_at": BASE + 300,
    }
    with pytest.raises(te.TradeError) as exc:
        e.open_trade("t-1", initiator="bob", partner="alice", now=BASE)
    assert exc.value.code == "TRADE_DUPLICATE"


def test_offer_escrows_items_and_currency():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    out = e.offer("t-1", "alice", items={"sword": 1, "potion": 4},
                  currency=150, now=BASE)
    assert out["items"] == {"sword": 1, "potion": 4}
    assert out["currency"] == 150
    assert out["status"] == "open"
    assert e.inventory("alice") == {"potion": 6}
    assert e.balance("alice") == 350
    assert e.status("t-1", now=BASE)["confirmed"] == []


def test_replacing_offer_releases_previous_escrow_first():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, currency=100, now=BASE)
    e.offer("t-1", "alice", items={"potion": 10}, currency=200, now=BASE)
    assert e.inventory("alice") == {"sword": 1}
    assert e.balance("alice") == 300
    status = e.status("t-1", now=BASE)
    assert status["offers"]["alice"] == {"items": {"potion": 10}, "currency": 200}


def test_offer_rejects_items_beyond_inventory():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    with pytest.raises(te.TradeError) as exc:
        e.offer("t-1", "alice", items={"potion": 11}, now=BASE)
    assert exc.value.code == "TRADE_INSUFFICIENT_ITEMS"
    assert e.inventory("alice") == {"sword": 1, "potion": 10}
    e.offer("t-1", "alice", items={"sword": 1}, now=BASE)
    e.open_trade("t-2", initiator="alice", partner="bob", now=BASE)
    with pytest.raises(te.TradeError) as exc:
        e.offer("t-2", "alice", items={"sword": 1}, now=BASE)
    assert exc.value.code == "TRADE_INSUFFICIENT_ITEMS"


def test_offer_rejects_currency_beyond_balance():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    with pytest.raises(te.TradeError) as exc:
        e.offer("t-1", "alice", items={}, currency=501, now=BASE)
    assert exc.value.code == "TRADE_INSUFFICIENT_CURRENCY"
    assert e.balance("alice") == 500


def test_offer_limits_items_and_currency():
    e = escrow(max_items=2, max_currency=100)
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    with pytest.raises(te.TradeError) as exc:
        e.offer("t-1", "alice",
                items={"sword": 1, "potion": 1, "gem": 1}, now=BASE)
    assert exc.value.code == "TRADE_OFFER_TOO_LARGE"
    with pytest.raises(te.TradeError) as exc:
        e.offer("t-1", "alice", items={"sword": 1}, currency=101, now=BASE)
    assert exc.value.code == "TRADE_CURRENCY_LIMIT"


def test_offer_requires_participant_and_known_trade():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.grant_items("carol", "sword", 1)
    with pytest.raises(te.TradeError) as exc:
        e.offer("t-1", "carol", items={"sword": 1}, now=BASE)
    assert exc.value.code == "TRADE_NOT_PARTICIPANT"
    with pytest.raises(te.TradeError) as exc:
        e.confirm("t-404", "alice", now=BASE)
    assert exc.value.code == "TRADE_UNKNOWN"


def test_offer_changes_clear_both_confirmations():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, now=BASE)
    e.offer("t-1", "bob", items={"shield": 1}, now=BASE)
    waiting = e.confirm("t-1", "alice", now=BASE)
    assert waiting == {"trade_id": "t-1", "status": "waiting",
                       "confirmed": ["alice"]}
    e.offer("t-1", "bob", items={"shield": 1, "potion": 2}, now=BASE)
    assert e.status("t-1", now=BASE)["confirmed"] == []


def test_both_confirms_swap_offers_exactly_once():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, currency=100, now=BASE)
    e.offer("t-1", "bob", items={"shield": 1}, currency=50, now=BASE)
    e.confirm("t-1", "alice", now=BASE)
    done = e.confirm("t-1", "bob", now=BASE)
    assert done["status"] == "completed"
    assert done["received"]["alice"] == {"items": {"shield": 1}, "currency": 50}
    assert done["received"]["bob"] == {"items": {"sword": 1}, "currency": 100}
    assert e.inventory("alice") == {"potion": 10, "shield": 1}
    assert e.inventory("bob") == {"potion": 5, "sword": 1}
    assert e.balance("alice") == 450
    assert e.balance("bob") == 250
    with pytest.raises(te.TradeError) as exc:
        e.confirm("t-1", "alice", now=BASE)
    assert exc.value.code == "TRADE_STATE"
    assert e.stats()["completed"] == 1


def test_cancel_returns_escrow_once():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, currency=100, now=BASE)
    assert e.cancel("t-1", player="bob", now=BASE) == {
        "trade_id": "t-1", "status": "cancelled"}
    assert e.inventory("alice") == {"sword": 1, "potion": 10}
    assert e.balance("alice") == 500
    with pytest.raises(te.TradeError) as exc:
        e.cancel("t-1", player="alice", now=BASE)
    assert exc.value.code == "TRADE_STATE"
    assert e.stats()["cancelled"] == 1


def test_empty_trade_cannot_complete():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.confirm("t-1", "alice", now=BASE)
    with pytest.raises(te.TradeError) as exc:
        e.confirm("t-1", "bob", now=BASE)
    assert exc.value.code == "TRADE_EMPTY"
    status = e.status("t-1", now=BASE)
    assert status["status"] == "open"
    assert status["confirmed"] == []
    assert e.stats()["completed"] == 0


def test_expired_trade_releases_escrow_and_refuses_operations():
    e = escrow(ttl=60)
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, now=BASE)
    assert e.expire(now=BASE + 59) == 0
    assert e.expire(now=BASE + 60) == 1
    assert e.inventory("alice") == {"sword": 1, "potion": 10}
    assert e.status("t-1", now=BASE + 60)["status"] == "expired"
    with pytest.raises(te.TradeError) as exc:
        e.offer("t-1", "alice", items={"potion": 1}, now=BASE + 61)
    assert exc.value.code == "TRADE_EXPIRED"
    assert e.stats()["expired"] == 1


def test_concurrent_confirms_complete_trade_once():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, now=BASE)
    e.offer("t-1", "bob", items={"shield": 1}, now=BASE)
    barrier = threading.Barrier(2)
    outcomes = []
    guard = threading.Lock()

    def worker(player):
        barrier.wait()
        try:
            row = e.confirm("t-1", player, now=BASE)["status"]
        except te.TradeError as exc:
            row = exc.code
        with guard:
            outcomes.append(row)

    threads = [threading.Thread(target=worker, args=(name,))
               for name in ("alice", "bob")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["completed", "waiting"]
    assert e.inventory("alice") == {"potion": 10, "shield": 1}
    assert e.inventory("bob") == {"potion": 5, "sword": 1}
    assert e.stats()["completed"] == 1


def test_snapshot_restore_preserves_trades_and_escrow():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, currency=100, now=BASE)
    snap = e.snapshot()
    assert snap["version"] == 1
    e.cancel("t-1", now=BASE)
    assert e.inventory("alice") == {"sword": 1, "potion": 10}
    restored = te.TradeEscrow()
    restored.restore(snap)
    assert restored.inventory("alice") == {"potion": 10}
    assert restored.balance("alice") == 400
    assert restored.status("t-1", now=BASE)["offers"]["alice"] == {
        "items": {"sword": 1}, "currency": 100}
    assert restored.cancel("t-1", now=BASE)["status"] == "cancelled"
    assert restored.inventory("alice") == {"sword": 1, "potion": 10}
    assert restored.balance("alice") == 500


def test_corrupt_snapshot_is_rejected_without_mutating_state():
    e = escrow()
    e.open_trade("t-1", initiator="alice", partner="bob", now=BASE)
    e.offer("t-1", "alice", items={"sword": 1}, currency=100, now=BASE)
    bad_states = [
        None,
        {},
        {"version": 2, "players": {}, "trades": {}, "stats": {}},
        {"version": 1,
         "players": {"alice": {"items": "nope", "balance": 0}},
         "trades": {}, "stats": {}},
    ]
    for state in bad_states:
        with pytest.raises(te.TradeError) as exc:
            e.restore(state)
        assert exc.value.code == "TRADE_SNAPSHOT_INVALID"
    assert e.inventory("alice") == {"potion": 10}
    assert e.balance("alice") == 400
