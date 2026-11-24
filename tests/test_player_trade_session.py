import threading

import pytest

import player_trade_session as trade

NOW = "2026-11-24T10:00:00+00:00"


def manager(**kwargs):
    return trade.TradeSessionManager(**kwargs)


def pair(**kwargs):
    box = manager(**kwargs)
    box.topup("alice", coins=100, items=[{"item": "ore", "count": 5},
                                         {"item": "herb", "count": 2}])
    box.topup("bob", coins=40, items=[{"item": "gem", "count": 3}])
    return box


def test_topup_and_holdings_are_independent_copies():
    box = manager()
    assert box.topup("alice", coins=25,
                     items={"ore": 3}) == {"coins": 25, "items": {"ore": 3}}
    snapshot = box.holdings("alice")
    snapshot["coins"] = 999
    snapshot["items"]["ore"] = 999
    assert box.holdings("alice") == {"coins": 25, "items": {"ore": 3}}


def test_open_session_rejects_self_trade_and_duplicate_ids():
    box = manager()
    with pytest.raises(trade.TradeError) as exc:
        box.open_session("alice", "alice", now=NOW)
    assert exc.value.code == "TRADE_SELF"

    first = box.open_session("alice", "bob", session_id="s-1", now=NOW)
    assert first["state"] == "open"
    assert first["session_id"] == "s-1"
    with pytest.raises(trade.TradeError) as exc:
        box.open_session("carol", "dave", session_id="s-1", now=NOW)
    assert exc.value.code == "TRADE_DUPLICATE"
    assert box.stats()["opened"] == 1


def test_busy_participants_and_open_session_capacity():
    box = manager()
    box.open_session("alice", "bob", now=NOW)
    with pytest.raises(trade.TradeError) as exc:
        box.open_session("alice", "carol", now=NOW)
    assert exc.value.code == "TRADE_BUSY"

    limited = manager(max_open=1)
    limited.open_session("carol", "dave", now=NOW)
    with pytest.raises(trade.TradeError) as exc:
        limited.open_session("erin", "frank", now=NOW)
    assert exc.value.code == "TRADE_CAPACITY"


def test_offer_validates_participants_and_payload():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    with pytest.raises(trade.TradeError) as exc:
        box.offer("missing", "alice", coins=1)
    assert exc.value.code == "TRADE_NOT_FOUND"
    with pytest.raises(trade.TradeError) as exc:
        box.offer("s-1", "carol", coins=1)
    assert exc.value.code == "TRADE_NOT_PARTICIPANT"
    with pytest.raises(trade.TradeError) as exc:
        box.offer("s-1", "alice", coins=-1)
    assert exc.value.code == "TRADE_BAD_REQUEST"
    with pytest.raises(trade.TradeError) as exc:
        box.offer("s-1", "alice", items={f"i{n}": 1 for n in range(11)})
    assert exc.value.code == "TRADE_BAD_REQUEST"

    ok = box.offer("s-1", "alice", items=[{"item": "ore", "count": 2}],
                   coins=5)
    assert ok["offers"]["alice"] == {"items": {"ore": 2}, "coins": 5}
    assert ok["state"] == "open"


def test_offer_change_clears_locks_and_confirmations():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    box.offer("s-1", "alice", coins=10)
    box.offer("s-1", "bob", coins=5)
    box.lock("s-1", "alice")
    box.lock("s-1", "bob")
    box.confirm("s-1", "alice")
    assert box.status("s-1")["confirmed"] == ["alice"]

    changed = box.offer("s-1", "alice", coins=20)
    assert changed["state"] == "open"
    assert changed["locked"] == []
    assert changed["confirmed"] == []
    with pytest.raises(trade.TradeError) as exc:
        box.confirm("s-1", "alice")
    assert exc.value.code == "TRADE_NOT_READY"


def test_lock_requires_an_offer_and_confirm_requires_both_locks():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    with pytest.raises(trade.TradeError) as exc:
        box.lock("s-1", "alice")
    assert exc.value.code == "TRADE_NOT_READY"

    box.offer("s-1", "alice")
    box.offer("s-1", "bob")
    box.lock("s-1", "alice")
    with pytest.raises(trade.TradeError) as exc:
        box.confirm("s-1", "alice")
    assert exc.value.code == "TRADE_NOT_READY"
    assert box.status("s-1")["state"] == "open"

    locked = box.lock("s-1", "bob")
    assert locked["state"] == "locked"
    assert locked["locked"] == ["alice", "bob"]
    assert box.confirm("s-1", "alice")["state"] == "locked"
    done = box.confirm("s-1", "bob")
    assert done["state"] == "completed"
    assert box.stats()["completed"] == 1


def test_completed_trade_moves_items_and_coins_exactly_once():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    box.offer("s-1", "alice", items=[{"item": "ore", "count": 3}], coins=30)
    box.offer("s-1", "bob", items=[{"item": "gem", "count": 1}], coins=10)
    box.lock("s-1", "alice")
    box.lock("s-1", "bob")
    box.confirm("s-1", "alice")
    result = box.confirm("s-1", "bob")
    assert result["state"] == "completed"
    assert box.holdings("alice") == {
        "coins": 80, "items": {"gem": 1, "herb": 2, "ore": 2}}
    assert box.holdings("bob") == {
        "coins": 60, "items": {"gem": 2, "ore": 3}}
    assert box.stats() == {"opened": 1, "completed": 1, "cancelled": 0,
                           "expired": 0, "rejected": 0}


def test_failed_settlement_unlocks_and_can_be_retried():
    box = manager()
    box.topup("alice", coins=10)
    box.topup("bob", coins=0)
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    box.offer("s-1", "alice", coins=50)
    box.offer("s-1", "bob")
    box.lock("s-1", "alice")
    box.lock("s-1", "bob")
    box.confirm("s-1", "alice")
    with pytest.raises(trade.TradeError) as exc:
        box.confirm("s-1", "bob")
    assert exc.value.code == "TRADE_INSUFFICIENT"
    failed = box.status("s-1")
    assert failed["state"] == "open"
    assert failed["locked"] == []
    assert failed["confirmed"] == []
    assert box.stats()["rejected"] == 1

    box.topup("alice", coins=40)
    box.lock("s-1", "alice")
    box.lock("s-1", "bob")
    box.confirm("s-1", "alice")
    assert box.confirm("s-1", "bob")["state"] == "completed"
    assert box.holdings("alice")["coins"] == 0
    assert box.holdings("bob")["coins"] == 50


def test_repeat_confirm_after_completion_is_idempotent():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    box.offer("s-1", "alice", coins=5)
    box.offer("s-1", "bob", coins=5)
    box.lock("s-1", "alice")
    box.lock("s-1", "bob")
    box.confirm("s-1", "alice")
    box.confirm("s-1", "bob")
    before = box.holdings("alice")
    again = box.confirm("s-1", "alice")
    assert again["state"] == "completed"
    assert box.holdings("alice") == before
    assert box.stats()["completed"] == 1


def test_cancel_is_idempotent_and_closes_the_session():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    cancelled = box.cancel("s-1", "alice")
    assert cancelled["state"] == "cancelled"
    assert box.cancel("s-1", "bob")["state"] == "cancelled"
    assert box.stats()["cancelled"] == 1
    for action in (
        lambda: box.offer("s-1", "alice", coins=1),
        lambda: box.lock("s-1", "alice"),
        lambda: box.confirm("s-1", "alice"),
    ):
        with pytest.raises(trade.TradeError) as exc:
            action()
        assert exc.value.code == "TRADE_CLOSED"
    with pytest.raises(trade.TradeError) as exc:
        box.cancel("s-1", "carol")
    assert exc.value.code == "TRADE_NOT_PARTICIPANT"


def test_session_expiry_is_detected_lazily_and_by_sweep():
    box = pair(session_ttl=60)
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    assert box.status("s-1", now="2026-11-24T10:00:59+00:00")["state"] == "open"
    stale = box.status("s-1", now="2026-11-24T10:01:00+00:00")
    assert stale["state"] == "expired"
    with pytest.raises(trade.TradeError) as exc:
        box.confirm("s-1", "alice", now="2026-11-24T10:01:00+00:00")
    assert exc.value.code == "TRADE_EXPIRED"

    box.open_session("carol", "dave", session_id="s-2", now=NOW)
    assert box.expire(now="2026-11-24T10:01:00+00:00") == 1
    assert box.expire(now="2026-11-24T10:01:00+00:00") == 0
    assert box.stats()["expired"] == 2


def test_list_sessions_filters_by_player_and_keeps_creation_order():
    box = manager()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    box.open_session("carol", "dave", session_id="s-2", now=NOW)
    assert [row["session_id"] for row in box.list_sessions()] == ["s-1", "s-2"]
    assert [row["session_id"] for row in box.list_sessions("alice")] == ["s-1"]
    assert [row["session_id"] for row in box.list_sessions("carol")] == ["s-2"]


def test_snapshot_restore_keeps_locked_state_and_holdings():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    box.offer("s-1", "alice", items=[{"item": "ore", "count": 2}])
    box.offer("s-1", "bob", items=[{"item": "gem", "count": 1}])
    box.lock("s-1", "alice")
    state = box.snapshot()
    box.lock("s-1", "bob")
    box.confirm("s-1", "alice")
    box.confirm("s-1", "bob")

    restored = manager()
    restored.restore(state)
    assert restored.holdings("alice")["items"]["ore"] == 5
    stuck = restored.status("s-1")
    assert stuck["state"] == "open"
    assert stuck["locked"] == ["alice"]
    restored.lock("s-1", "bob")
    restored.confirm("s-1", "alice")
    assert restored.confirm("s-1", "bob")["state"] == "completed"
    assert restored.holdings("alice")["items"] == {"gem": 1, "herb": 2,
                                                   "ore": 3}
    with pytest.raises(trade.TradeError) as exc:
        restored.status("trade-1")
    assert exc.value.code == "TRADE_NOT_FOUND"


def test_corrupt_snapshot_is_refused_without_mutation():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    valid = box.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "holdings": {}, "sessions": [], "seq": 0, "stats": {}},
        {"version": 1, "holdings": [], "sessions": [], "seq": 0, "stats": {}},
        {"version": 1, "holdings": {}, "sessions": "nope", "seq": 0, "stats": {}},
        {"version": 1, "holdings": {}, "sessions": [], "seq": 0, "stats": []},
        {"version": 1, "holdings": {}, "sessions": [{"session_id": "x"}],
         "seq": 1, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(trade.TradeError) as exc:
            box.restore(bad)
        assert exc.value.code == "TRADE_SNAPSHOT_INVALID"
    assert box.holdings("alice")["coins"] == 100
    assert box.status("s-1")["state"] == "open"
    box.restore(valid)


def test_concurrent_confirmation_settles_the_swap_once():
    box = pair()
    box.open_session("alice", "bob", session_id="s-1", now=NOW)
    box.offer("s-1", "alice", coins=20)
    box.offer("s-1", "bob", coins=10)
    box.lock("s-1", "alice")
    box.lock("s-1", "bob")
    barrier = threading.Barrier(8)
    errors = []
    guard = threading.Lock()

    def worker(player):
        barrier.wait()
        try:
            box.confirm("s-1", player)
        except trade.TradeError as exc:
            with guard:
                errors.append(exc.code)

    threads = [threading.Thread(target=worker, args=(player,))
               for player in ["alice"] * 4 + ["bob"] * 4]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(errors) <= {"TRADE_ALREADY_CONFIRMED", "TRADE_NOT_READY"}
    assert box.status("s-1")["state"] == "completed"
    assert box.holdings("alice")["coins"] == 90
    assert box.holdings("bob")["coins"] == 50
    assert box.stats()["completed"] == 1
    assert box.stats()["rejected"] == 0
