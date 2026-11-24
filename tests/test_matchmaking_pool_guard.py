import copy
import threading
from datetime import datetime, timedelta

import pytest

import matchmaking_pool_guard as mm

NOW = "2026-11-24T10:00:00+00:00"
BASE = datetime.fromisoformat(NOW)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def make_pool(**kwargs):
    pool = mm.MatchmakingPool(**kwargs)
    pool.register_queue("ranked", team_size=2, max_latency=100)
    return pool


def party(party_id, rating=1000, latency=30, members=None):
    return {
        "party_id": party_id,
        "members": members if members is not None else (party_id + "-a", party_id + "-b"),
        "rating": rating,
        "latency": latency,
    }


def enter(pool, party_id, now=NOW, **kwargs):
    return pool.enqueue("ranked", now=now, **party(party_id, **kwargs))


def test_enqueue_returns_ticket_and_identical_repeat_is_idempotent():
    pool = make_pool()
    ticket = enter(pool, "alpha")

    assert ticket["status"] == "queued"
    assert ticket["queue_id"] == "ranked"
    assert ticket["party_id"] == "alpha"
    assert ticket["members"] == ["alpha-a", "alpha-b"]
    assert ticket["rating"] == 1000
    assert ticket["latency"] == 30
    assert pool.stats() == {"enqueued": 1, "matches": 0, "cancelled": 0, "expired": 0}

    again = enter(pool, "alpha")
    assert again["ticket_id"] == ticket["ticket_id"]
    assert pool.stats()["enqueued"] == 1
    assert len(pool.pending("ranked", now=NOW)) == 1


def test_repeat_party_with_different_roster_is_refused():
    pool = make_pool()
    first = enter(pool, "alpha")

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.enqueue("ranked", now=NOW,
                     party_id="alpha", members=("alpha-a", "alpha-c"),
                     rating=1000, latency=30)
    assert exc.value.code == "MM_DUPLICATE_PARTY"
    assert pool.get_ticket("ranked", "alpha")["ticket_id"] == first["ticket_id"]
    assert pool.stats()["enqueued"] == 1


def test_queue_registration_is_idempotent_but_conflicts_are_rejected():
    pool = mm.MatchmakingPool()
    created = pool.register_queue("ranked", team_size=2, max_latency=100)
    assert created == {"queue_id": "ranked", "team_size": 2, "max_latency": 100}
    assert pool.register_queue("ranked", team_size=2, max_latency=100) == created

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.register_queue("ranked", team_size=2, max_latency=120)
    assert exc.value.code == "MM_QUEUE_CONFLICT"

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.enqueue("casual", now=NOW, **party("ghost"))
    assert exc.value.code == "MM_UNKNOWN_QUEUE"

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.register_queue("bad", team_size=0, max_latency=50)
    assert exc.value.code == "MM_INVALID_QUEUE"


def test_member_already_queued_elsewhere_is_busy():
    pool = make_pool()
    enter(pool, "alpha", members=("hero-1", "hero-2"))

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.enqueue("ranked", now=NOW, party_id="beta",
                     members=("hero-2", "hero-3"), rating=1000, latency=30)
    assert exc.value.code == "MM_MEMBER_BUSY"
    assert pool.get_ticket("ranked", "beta") is None
    assert pool.stats()["enqueued"] == 1


def test_roster_and_latency_boundaries_are_validated():
    pool = make_pool()

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.enqueue("ranked", now=NOW, party_id="short",
                     members=("only-one",), rating=1000, latency=20)
    assert exc.value.code == "MM_INVALID_PARTY"

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.enqueue("ranked", now=NOW, party_id="twins",
                     members=("same", "same"), rating=1000, latency=20)
    assert exc.value.code == "MM_INVALID_PARTY"

    with pytest.raises(mm.MatchmakingError) as exc:
        enter(pool, "slow", latency=101)
    assert exc.value.code == "MM_LATENCY_TOO_HIGH"
    assert pool.stats()["enqueued"] == 0

    assert enter(pool, "exact", latency=100)["status"] == "queued"


def test_pool_capacity_frees_after_cancel():
    pool = make_pool(max_pool=2)
    enter(pool, "alpha")
    enter(pool, "beta")

    with pytest.raises(mm.MatchmakingError) as exc:
        enter(pool, "gamma")
    assert exc.value.code == "MM_POOL_FULL"
    assert pool.get_ticket("ranked", "gamma") is None

    pool.cancel("ranked", "alpha", now=NOW)
    assert enter(pool, "gamma")["status"] == "queued"
    assert pool.stats() == {"enqueued": 3, "matches": 0, "cancelled": 1, "expired": 0}


def test_drain_pairs_in_fifo_order_with_rating_gap():
    pool = make_pool()
    enter(pool, "alpha", rating=1000)
    enter(pool, "beta", rating=1050)
    enter(pool, "gamma", rating=1600)
    enter(pool, "delta", rating=1620)

    matches = pool.drain("ranked", now=NOW, limit=2)
    assert len(matches) == 2
    assert matches[0]["parties"] == ["alpha", "beta"]
    assert matches[0]["rating_gap"] == 50
    assert matches[1]["parties"] == ["gamma", "delta"]
    assert matches[1]["rating_gap"] == 20
    assert pool.pending("ranked", now=NOW) == []
    assert pool.stats()["matches"] == 2


def test_rating_window_widens_with_wait_time():
    pool = make_pool(initial_rating_window=20, widen_per_second=10,
                     max_rating_window=100)
    enter(pool, "alpha", rating=1000)
    enter(pool, "beta", rating=1060)

    assert pool.drain("ranked", now=NOW) == []
    assert pool.drain("ranked", now=at(3)) == []
    matches = pool.drain("ranked", now=at(4))
    assert len(matches) == 1
    assert matches[0]["parties"] == ["alpha", "beta"]


def test_rating_window_is_capped_and_waiting_forever_does_not_help():
    pool = make_pool(initial_rating_window=20, widen_per_second=100,
                     max_rating_window=50, ticket_ttl=100000)
    enter(pool, "alpha", rating=1000)
    enter(pool, "beta", rating=1200)

    assert pool.drain("ranked", now=at(1000)) == []
    remaining = pool.pending("ranked", now=at(1000))
    assert [row["party_id"] for row in remaining] == ["alpha", "beta"]
    assert pool.stats()["matches"] == 0


def test_latency_gap_blocks_pairing_even_when_rating_matches():
    pool = make_pool(max_latency_gap=10)
    enter(pool, "alpha", rating=1000, latency=20)
    enter(pool, "beta", rating=1000, latency=40)

    assert pool.drain("ranked", now=NOW) == []
    assert len(pool.pending("ranked", now=NOW)) == 2

    pool.cancel("ranked", "beta", now=NOW)
    enter(pool, "gamma", rating=1010, latency=25)
    matches = pool.drain("ranked", now=NOW)
    assert len(matches) == 1
    assert matches[0]["parties"] == ["alpha", "gamma"]


def test_cancel_removes_ticket_and_double_cancel_fails():
    pool = make_pool()
    enter(pool, "alpha")
    enter(pool, "beta")

    result = pool.cancel("ranked", "alpha", now=NOW)
    assert result["status"] == "cancelled"
    assert result["party_id"] == "alpha"
    assert pool.get_ticket("ranked", "alpha") is None

    with pytest.raises(mm.MatchmakingError) as exc:
        pool.cancel("ranked", "alpha", now=NOW)
    assert exc.value.code == "MM_UNKNOWN_TICKET"

    pool.cancel("ranked", "beta", now=NOW)
    assert pool.drain("ranked", now=NOW) == []
    assert pool.stats() == {"enqueued": 2, "matches": 0, "cancelled": 2, "expired": 0}


def test_ticket_ttl_expires_at_boundary_and_party_may_requeue():
    pool = make_pool(ticket_ttl=60)
    enter(pool, "alpha")

    assert pool.pending("ranked", now=at(59)) != []
    assert pool.drain("ranked", now=at(60)) == []
    assert pool.get_ticket("ranked", "alpha") is None
    assert pool.stats() == {"enqueued": 1, "matches": 0, "cancelled": 0, "expired": 1}

    again = enter(pool, "alpha", now=at(60))
    assert again["status"] == "queued"
    assert pool.stats()["enqueued"] == 2


def test_drain_limit_keeps_remaining_parties_in_order():
    pool = make_pool()
    enter(pool, "alpha", rating=1000)
    enter(pool, "beta", rating=1010)
    enter(pool, "gamma", rating=2000)
    enter(pool, "delta", rating=2010)

    first = pool.drain("ranked", now=NOW, limit=1)
    assert [row["parties"] for row in first] == [["alpha", "beta"]]
    remaining = [row["party_id"] for row in pool.pending("ranked", now=NOW)]
    assert remaining == ["gamma", "delta"]

    second = pool.drain("ranked", now=NOW, limit=1)
    assert [row["parties"] for row in second] == [["gamma", "delta"]]
    assert pool.pending("ranked", now=NOW) == []
    assert pool.drain("ranked", now=NOW) == []


def test_snapshot_restore_carries_queue_tickets_and_counters():
    pool = make_pool()
    enter(pool, "alpha", rating=1000)
    enter(pool, "beta", rating=1010)
    state = pool.snapshot()
    enter(pool, "gamma", rating=3000)

    restored = mm.MatchmakingPool()
    restored.restore(state)
    assert restored.get_ticket("ranked", "alpha")["party_id"] == "alpha"
    assert restored.get_ticket("ranked", "gamma") is None
    assert restored.stats() == {"enqueued": 2, "matches": 0, "cancelled": 0, "expired": 0}

    matches = restored.drain("ranked", now=NOW)
    assert [row["parties"] for row in matches] == [["alpha", "beta"]]
    assert restored.stats()["matches"] == 1

    valid = copy.deepcopy(state)
    corrupt = [None, {}]
    version_two = copy.deepcopy(valid)
    version_two["version"] = 2
    corrupt.append(version_two)
    bad_tickets = copy.deepcopy(valid)
    bad_tickets["tickets"] = "nope"
    corrupt.append(bad_tickets)
    bad_stats = copy.deepcopy(valid)
    bad_stats["stats"] = "nope"
    corrupt.append(bad_stats)

    guard = mm.MatchmakingPool()
    guard.restore(state)
    for bad in corrupt:
        with pytest.raises(mm.MatchmakingError) as exc:
            guard.restore(bad)
        assert exc.value.code == "MM_SNAPSHOT_INVALID"
    assert guard.get_ticket("ranked", "alpha") is not None
    assert guard.stats()["enqueued"] == 2


def test_concurrent_enqueue_of_same_party_creates_one_ticket():
    pool = make_pool()
    barrier = threading.Barrier(8)
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = enter(pool, "race")
        except Exception as exc:  # pragma: no cover - reported through errors
            with lock:
                errors.append(repr(exc))
            return
        with lock:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 8
    assert len({row["ticket_id"] for row in results}) == 1
    assert pool.stats()["enqueued"] == 1
    assert len(pool.pending("ranked", now=NOW)) == 1
