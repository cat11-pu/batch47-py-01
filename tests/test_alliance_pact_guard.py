import copy
import threading
from datetime import datetime, timedelta

import pytest

import alliance_pact_guard as ap

NOW = "2026-11-24T10:00:00+00:00"
BASE = datetime.fromisoformat(NOW)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def make_lab(**kwargs):
    lab = ap.AlliancePact(**kwargs)
    for guild_id, level in (("g1", 10), ("g2", 20), ("g3", 30),
                            ("g4", 40), ("g5", 50)):
        lab.register_guild(guild_id, level=level, members=100)
    return lab


def propose(lab, pact_id="p1", proposer="g1", partners=("g1", "g2"),
            kind="trade", now=NOW):
    return lab.propose(proposer, pact_id=pact_id, kind=kind,
                       partners=partners, now=now)


def approve_pair(lab, pact_id="p1", now=NOW):
    lab.vote("g1", pact_id, approve=True, now=now)
    lab.vote("g2", pact_id, approve=True, now=now)
    return lab.finalize(pact_id, now=now)


def test_guild_registration_is_idempotent_and_validated():
    lab = ap.AlliancePact()
    guild = lab.register_guild("g1", level=10, members=100)
    assert guild == {"guild_id": "g1", "level": 10, "members": 100}
    assert lab.register_guild("g1", level=10, members=100) == guild

    with pytest.raises(ap.PactError) as exc:
        lab.register_guild("g1", level=11, members=100)
    assert exc.value.code == "PACT_GUILD_CONFLICT"

    with pytest.raises(ap.PactError) as exc:
        lab.register_guild("", level=10, members=100)
    assert exc.value.code == "PACT_INVALID_GUILD"
    with pytest.raises(ap.PactError) as exc:
        lab.register_guild("g2", level=0, members=100)
    assert exc.value.code == "PACT_INVALID_GUILD"
    with pytest.raises(ap.PactError) as exc:
        lab.register_guild("g2", level=10, members=0)
    assert exc.value.code == "PACT_INVALID_GUILD"


def test_proposal_starts_pending_with_sorted_partners_and_deadline():
    lab = make_lab()
    row = lab.propose("g1", pact_id="p1", kind="trade",
                      partners=("g2", "g1"), now=NOW)
    assert row["status"] == "pending"
    assert row["guilds"] == ["g1", "g2"]
    assert row["kind"] == "trade"
    assert row["proposer"] == "g1"
    assert row["deadline"] == at(3600)
    assert row["votes"] == {}

    assert lab.pacts_of("g1") == ["p1"]
    assert lab.pacts_of("g3") == []
    status = lab.status("p1")
    assert status["votes_count"] == 0
    assert status["partners"] == 2
    assert lab.stats()["proposed"] == 1


def test_proposal_structure_errors():
    lab = make_lab()
    with pytest.raises(ap.PactError) as exc:
        propose(lab, kind="war")
    assert exc.value.code == "PACT_UNKNOWN_KIND"

    with pytest.raises(ap.PactError) as exc:
        propose(lab, partners=("g1", "g1", "g2"))
    assert exc.value.code == "PACT_DUPLICATE_PARTNER"

    with pytest.raises(ap.PactError) as exc:
        propose(lab, proposer="g1", partners=("g2", "g3"))
    assert exc.value.code == "PACT_PROPOSER_MISSING"

    with pytest.raises(ap.PactError) as exc:
        propose(lab, partners=("g1",))
    assert exc.value.code == "PACT_TOO_FEW_PARTNERS"

    with pytest.raises(ap.PactError) as exc:
        propose(lab, partners=("g1", "ghost"))
    assert exc.value.code == "PACT_UNKNOWN_GUILD"

    with pytest.raises(ap.PactError) as exc:
        propose(lab, pact_id="")
    assert exc.value.code == "PACT_INVALID_PACT"

    with pytest.raises(ap.PactError) as exc:
        propose(lab, partners="g1g2")
    assert exc.value.code == "PACT_INVALID_PACT"
    assert lab.stats()["proposed"] == 0


def test_duplicate_pact_pair_and_capacity_are_rejected():
    lab = make_lab()
    propose(lab, "p1", "g1", ("g1", "g2"))

    with pytest.raises(ap.PactError) as exc:
        propose(lab, "p1", "g1", ("g1", "g3"))
    assert exc.value.code == "PACT_DUPLICATE"

    with pytest.raises(ap.PactError) as exc:
        propose(lab, "p2", "g1", ("g1", "g2"))
    assert exc.value.code == "PACT_ALREADY_EXISTS"

    tight = make_lab(max_pacts_per_guild=1)
    propose(tight, "p1", "g1", ("g1", "g2"))
    with pytest.raises(ap.PactError) as exc:
        propose(tight, "p2", "g1", ("g1", "g3"))
    assert exc.value.code == "PACT_CAPACITY"


def test_weighted_votes_accumulate_and_default_to_guild_level():
    lab = make_lab()
    propose(lab)

    first = lab.vote("g1", "p1", approve=True, now=NOW)
    assert first["weight"] == 10
    assert first["votes_count"] == 1
    assert first["partners"] == 2

    second = lab.vote("g2", "p1", approve=True, weight=5, now=NOW)
    assert second["weight"] == 5
    assert second["votes_count"] == 2

    tally = lab.tally("p1")
    assert tally["approve"] == 15
    assert tally["reject"] == 0
    assert tally["votes_count"] == 2
    assert tally["partners"] == 2
    assert lab.status("p1")["votes"]["g2"]["weight"] == 5
    assert lab.stats()["votes"] == 2


def test_vote_permissions_weight_bounds_and_single_vote():
    lab = make_lab()
    propose(lab)

    with pytest.raises(ap.PactError) as exc:
        lab.vote("g3", "p1", approve=True, now=NOW)
    assert exc.value.code == "PACT_NOT_PARTNER"

    with pytest.raises(ap.PactError) as exc:
        lab.vote("ghost", "p1", approve=True, now=NOW)
    assert exc.value.code == "PACT_UNKNOWN_GUILD"

    with pytest.raises(ap.PactError) as exc:
        lab.vote("g1", "missing", approve=True, now=NOW)
    assert exc.value.code == "PACT_UNKNOWN_PACT"

    for bad in (0, 11, True):
        with pytest.raises(ap.PactError) as exc:
            lab.vote("g1", "p1", approve=True, weight=bad, now=NOW)
        assert exc.value.code == "PACT_INVALID_WEIGHT"

    with pytest.raises(ap.PactError) as exc:
        lab.vote("g1", "p1", approve=1, now=NOW)
    assert exc.value.code == "PACT_INVALID_VOTE"

    lab.vote("g1", "p1", approve=True, now=NOW)
    with pytest.raises(ap.PactError) as exc:
        lab.vote("g1", "p1", approve=True, now=NOW)
    assert exc.value.code == "PACT_ALREADY_VOTED"


def test_vote_after_deadline_is_closed():
    lab = make_lab()
    propose(lab)
    lab.vote("g1", "p1", approve=True, now=at(3599))

    with pytest.raises(ap.PactError) as exc:
        lab.vote("g2", "p1", approve=True, now=at(3600))
    assert exc.value.code == "PACT_VOTE_CLOSED"
    assert lab.stats()["votes"] == 1


def test_finalize_requires_all_votes_early_but_extends_after_deadline():
    lab = make_lab()
    propose(lab)

    with pytest.raises(ap.PactError) as exc:
        lab.finalize("p1", now=NOW)
    assert exc.value.code == "PACT_VOTE_OPEN"

    lab.vote("g1", "p1", approve=True, now=NOW)
    with pytest.raises(ap.PactError) as exc:
        lab.finalize("p1", now=NOW)
    assert exc.value.code == "PACT_VOTE_OPEN"

    lab.vote("g2", "p1", approve=True, now=NOW)
    result = lab.finalize("p1", now=NOW)
    assert result["status"] == "active"
    assert result["reason"] is None
    assert result["approve"] == 30
    assert result["reject"] == 0
    assert result["votes_count"] == 2
    assert result["partners"] == 2
    assert result["finalized_at"] == NOW
    assert lab.status("p1")["status"] == "active"
    assert lab.stats()["finalized"] == 1
    assert lab.stats()["activated"] == 1


def test_finalize_without_quorum_is_rejected():
    lab = make_lab()
    propose(lab, "p1", "g1", ("g1", "g2", "g3"))
    lab.vote("g1", "p1", approve=True, now=NOW)

    result = lab.finalize("p1", now=at(3600))
    assert result["status"] == "rejected"
    assert result["reason"] == "quorum"
    assert result["votes_count"] == 1
    assert result["partners"] == 3
    assert lab.stats()["rejected"] == 1


def test_finalize_with_more_reject_weight_is_rejected_by_majority():
    lab = make_lab()
    propose(lab)
    lab.vote("g1", "p1", approve=True, now=NOW)
    lab.vote("g2", "p1", approve=False, now=NOW)

    result = lab.finalize("p1", now=NOW)
    assert result["status"] == "rejected"
    assert result["reason"] == "majority"
    assert result["approve"] == 10
    assert result["reject"] == 20


def test_finalized_pact_and_non_active_break_guards():
    lab = make_lab()
    propose(lab)
    approve_pair(lab, "p1")

    with pytest.raises(ap.PactError) as exc:
        lab.finalize("p1", now=NOW)
    assert exc.value.code == "PACT_ALREADY_FINALIZED"
    with pytest.raises(ap.PactError) as exc:
        lab.vote("g1", "p1", approve=True, now=NOW)
    assert exc.value.code == "PACT_ALREADY_FINALIZED"

    propose(lab, "p2", "g3", ("g3", "g4"))
    with pytest.raises(ap.PactError) as exc:
        lab.break_pact("p2", guild_id="g3", now=NOW)
    assert exc.value.code == "PACT_NOT_ACTIVE"
    with pytest.raises(ap.PactError) as exc:
        lab.break_pact("missing", guild_id="g3", now=NOW)
    assert exc.value.code == "PACT_UNKNOWN_PACT"
    with pytest.raises(ap.PactError) as exc:
        lab.break_pact("p1", guild_id="ghost", now=NOW)
    assert exc.value.code == "PACT_UNKNOWN_GUILD"
    with pytest.raises(ap.PactError) as exc:
        lab.break_pact("p1", guild_id="g3", now=NOW)
    assert exc.value.code == "PACT_NOT_PARTNER"


def test_break_notice_sweep_and_stats_accounting():
    lab = make_lab()
    propose(lab)
    approve_pair(lab, "p1")

    result = lab.break_pact("p1", guild_id="g1", now=at(100))
    assert result["status"] == "breaking"
    assert result["breaking_guild"] == "g1"
    assert result["effective_at"] == at(700)
    assert lab.sweep(now=at(699)) == []
    assert lab.status("p1")["status"] == "breaking"

    rows = lab.sweep(now=at(700))
    assert rows == [{"pact_id": "p1", "status": "broken",
                     "effective_at": at(700)}]
    assert lab.status("p1")["status"] == "broken"
    assert lab.pacts_of("g1") == ["p1"]
    assert lab.sweep(now=at(701)) == []
    assert lab.stats() == {"proposed": 1, "finalized": 1, "activated": 1,
                           "rejected": 0, "broken": 1, "votes": 2}

    lab.reset_stats()
    assert lab.stats() == {"proposed": 0, "finalized": 0, "activated": 0,
                           "rejected": 0, "broken": 0, "votes": 0}


def test_reform_cooldown_after_broken_pact_expires_at_boundary():
    lab = make_lab()
    propose(lab)
    approve_pair(lab, "p1")
    lab.break_pact("p1", guild_id="g1", now=NOW)
    lab.sweep(now=at(600))

    with pytest.raises(ap.PactError) as exc:
        propose(lab, "p2", "g1", ("g1", "g2"), now=at(2399))
    assert exc.value.code == "PACT_COOLDOWN"

    row = propose(lab, "p2", "g1", ("g1", "g2"), now=at(2400))
    assert row["status"] == "pending"
    assert lab.pacts_of("g1") == ["p1", "p2"]


def test_snapshot_restore_keeps_votes_statuses_and_cooldowns():
    lab = make_lab()
    propose(lab, "p1", "g1", ("g1", "g2"))
    lab.vote("g1", "p1", approve=True, now=NOW)

    propose(lab, "p2", "g3", ("g3", "g4"))
    lab.vote("g3", "p2", approve=True, now=NOW)
    lab.vote("g4", "p2", approve=True, now=NOW)
    lab.finalize("p2", now=NOW)

    propose(lab, "p3", "g4", ("g4", "g5"))
    lab.vote("g4", "p3", approve=True, now=NOW)
    lab.vote("g5", "p3", approve=True, now=NOW)
    lab.finalize("p3", now=NOW)
    lab.break_pact("p3", guild_id="g4", now=NOW)
    lab.sweep(now=at(600))
    state = lab.snapshot()

    lab.vote("g2", "p1", approve=True, now=at(10))

    restored = ap.AlliancePact()
    restored.restore(state)
    assert restored.status("p1")["votes_count"] == 1
    assert restored.status("p2")["status"] == "active"
    assert restored.status("p3")["status"] == "broken"
    with pytest.raises(ap.PactError) as exc:
        restored.vote("g1", "p1", approve=True, now=at(10))
    assert exc.value.code == "PACT_ALREADY_VOTED"
    with pytest.raises(ap.PactError) as exc:
        restored.propose("g4", pact_id="p4", kind="trade",
                         partners=("g4", "g5"), now=at(600))
    assert exc.value.code == "PACT_COOLDOWN"
    assert restored.stats() == {"proposed": 3, "finalized": 2, "activated": 2,
                                "rejected": 0, "broken": 1, "votes": 5}

    valid = copy.deepcopy(state)
    version_two = copy.deepcopy(valid)
    version_two["version"] = 2
    bad_guilds = copy.deepcopy(valid)
    bad_guilds["guilds"] = "nope"
    bad_pacts = copy.deepcopy(valid)
    bad_pacts["pacts"] = "nope"
    bad_broken = copy.deepcopy(valid)
    bad_broken["broken_at"] = "nope"
    bad_stats = copy.deepcopy(valid)
    bad_stats["stats"] = "nope"

    guard = ap.AlliancePact()
    guard.restore(state)
    for bad in (None, {}, version_two, bad_guilds, bad_pacts,
                bad_broken, bad_stats):
        with pytest.raises(ap.PactError) as exc:
            guard.restore(bad)
        assert exc.value.code == "PACT_SNAPSHOT_INVALID"
    assert guard.status("p1")["votes_count"] == 1
    assert guard.stats()["proposed"] == 3


def test_concurrent_finalize_activates_the_pact_once():
    lab = make_lab()
    propose(lab)
    lab.vote("g1", "p1", approve=True, now=NOW)
    lab.vote("g2", "p1", approve=True, now=NOW)
    barrier = threading.Barrier(8)
    accepted = []
    rejected = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = lab.finalize("p1", now=NOW)
        except Exception as exc:
            code = getattr(exc, "code", repr(exc))
            with lock:
                rejected.append(code)
            return
        with lock:
            accepted.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert rejected == ["PACT_ALREADY_FINALIZED"] * 7
    assert lab.status("p1")["status"] == "active"
    assert lab.stats()["finalized"] == 1
    assert lab.stats()["activated"] == 1
