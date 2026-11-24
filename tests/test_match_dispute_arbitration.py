import threading

import pytest

import match_dispute_arbitration as da

T0 = "2026-11-24T10:00:00+00:00"


def arbiter(**kwargs):
    return da.MatchDisputeArbitration(**kwargs)


def with_match(arb=None, match_id="m-1"):
    arb = arb or arbiter()
    arb.open_match(match_id, winner="alice",
                   participants=("alice", "bob"), now=T0)
    return arb


def assign_three(arb, dispute_id="disp-1", now=T0):
    return arb.assign_reviewers(dispute_id, ["r-1", "r-2", "r-3"], now=now)


def code_of(excinfo):
    return excinfo.value.code


def test_open_match_records_result_and_rejects_duplicates():
    arb = arbiter()
    row = arb.open_match("m-1", winner="alice",
                         participants=("alice", "bob"), now=T0)
    assert row == {
        "match_id": "m-1",
        "winner": "alice",
        "participants": ["alice", "bob"],
        "status": "recorded",
        "opened_at": T0,
    }
    assert arb.match_result("m-1") == row
    with pytest.raises(da.DisputeError) as exc:
        arb.open_match("m-1", winner="bob", participants=("alice", "bob"),
                       now=T0)
    assert code_of(exc) == "DISPUTE_MATCH_EXISTS"
    for winner, participants in (("carol", ("alice", "bob")),
                                 ("alice", ("alice", "alice")),
                                 ("alice", ("alice",))):
        with pytest.raises(da.DisputeError) as exc:
            arb.open_match("m-bad", winner=winner, participants=participants,
                           now=T0)
        assert code_of(exc) == "DISPUTE_BAD_RESULT"
    assert arb.stats()["opened"] == 1
    with pytest.raises(da.DisputeError) as exc:
        arb.match_result("m-missing")
    assert code_of(exc) == "DISPUTE_UNKNOWN_MATCH"


def test_submit_requires_participant_and_reason():
    arb = with_match()
    with pytest.raises(da.DisputeError) as exc:
        arb.submit_dispute("m-missing", player_id="alice", reason="误判", now=T0)
    assert code_of(exc) == "DISPUTE_UNKNOWN_MATCH"
    with pytest.raises(da.DisputeError) as exc:
        arb.submit_dispute("m-1", player_id="carol", reason="误判", now=T0)
    assert code_of(exc) == "DISPUTE_NOT_PARTICIPANT"
    for reason in ("", "   "):
        with pytest.raises(da.DisputeError) as exc:
            arb.submit_dispute("m-1", player_id="alice", reason=reason, now=T0)
        assert code_of(exc) == "DISPUTE_BAD_REASON"


def test_appeal_window_boundary():
    arb = arbiter()
    arb.open_match("m-1", winner="alice",
                   participants=("alice", "bob"), now=T0)
    arb.open_match("m-2", winner="bob",
                   participants=("bob", "carol"), now=T0)
    assert arb.submit_dispute("m-1", player_id="alice", reason="越界",
                              now="2026-11-24T11:00:00+00:00")["status"] == "filed"
    with pytest.raises(da.DisputeError) as exc:
        arb.submit_dispute("m-2", player_id="bob", reason="太晚",
                           now="2026-11-24T11:00:01+00:00")
    assert code_of(exc) == "DISPUTE_WINDOW_CLOSED"


def test_evidence_limit_and_duplicate_submission():
    arb = with_match()
    too_much = tuple(f"clip-{i}.mp4" for i in range(6))
    with pytest.raises(da.DisputeError) as exc:
        arb.submit_dispute("m-1", player_id="alice", reason="误判",
                           evidence=too_much, now=T0)
    assert code_of(exc) == "DISPUTE_TOO_MUCH_EVIDENCE"
    row = arb.submit_dispute("m-1", player_id="alice", reason="误判",
                             evidence=("a.mp4", "b.mp4"), now=T0)
    assert row == {
        "status": "filed",
        "dispute_id": "disp-1",
        "match_id": "m-1",
        "player_id": "alice",
        "evidence_count": 2,
        "deadline": "2026-11-24T10:30:00+00:00",
    }
    with pytest.raises(da.DisputeError) as exc:
        arb.submit_dispute("m-1", player_id="alice", reason="再申诉", now=T0)
    assert code_of(exc) == "DISPUTE_DUPLICATE"
    assert arb.submit_dispute("m-1", player_id="bob", reason="对手视角",
                              now=T0)["dispute_id"] == "disp-2"


def test_open_dispute_limit_per_match():
    arb = arbiter(max_open_per_match=2)
    arb.open_match("m-1", winner="alice",
                   participants=("alice", "bob", "carol"), now=T0)
    arb.submit_dispute("m-1", player_id="alice", reason="a", now=T0)
    arb.submit_dispute("m-1", player_id="bob", reason="b", now=T0)
    with pytest.raises(da.DisputeError) as exc:
        arb.submit_dispute("m-1", player_id="carol", reason="c", now=T0)
    assert code_of(exc) == "DISPUTE_LIMIT"
    assert len(arb.disputes(match_id="m-1")) == 2


def test_assign_reviewers_panel_size_conflict_and_repeat():
    arb = with_match()
    arb.submit_dispute("m-1", player_id="alice", reason="误判", now=T0)

    with pytest.raises(da.DisputeError) as exc:
        arb.assign_reviewers("disp-1", ["r-1", "r-2"], now=T0)
    assert code_of(exc) == "DISPUTE_PANEL_SIZE"
    with pytest.raises(da.DisputeError) as exc:
        arb.assign_reviewers("disp-1", ["r-1", "r-2", "alice"], now=T0)
    assert code_of(exc) == "DISPUTE_REVIEWER_CONFLICT"

    row = assign_three(arb, "disp-1", now=T0)
    assert row == {
        "status": "reviewing",
        "dispute_id": "disp-1",
        "reviewers": ["r-1", "r-2", "r-3"],
        "deadline": "2026-11-24T10:30:00+00:00",
    }
    with pytest.raises(da.DisputeError) as exc:
        assign_three(arb, "disp-1", now=T0)
    assert code_of(exc) == "DISPUTE_ALREADY_ASSIGNED"
    assert arb.disputes()[0]["status"] == "reviewing"


def test_vote_requires_assigned_reviewer_and_valid_verdict():
    arb = with_match()
    arb.submit_dispute("m-1", player_id="alice", reason="误判", now=T0)
    assign_three(arb, "disp-1", now=T0)
    with pytest.raises(da.DisputeError) as exc:
        arb.cast_vote("disp-1", reviewer="r-9", verdict="uphold", now=T0)
    assert code_of(exc) == "DISPUTE_NOT_REVIEWER"
    with pytest.raises(da.DisputeError) as exc:
        arb.cast_vote("disp-1", reviewer="r-1", verdict="maybe", now=T0)
    assert code_of(exc) == "DISPUTE_BAD_VERDICT"
    row = arb.cast_vote("disp-1", reviewer="r-1", verdict="uphold", now=T0)
    assert row == {
        "status": "voted",
        "dispute_id": "disp-1",
        "reviewer": "r-1",
        "verdict": "uphold",
        "votes": 1,
        "remaining": 2,
    }


def test_duplicate_vote_is_refused():
    arb = with_match()
    arb.submit_dispute("m-1", player_id="alice", reason="误判", now=T0)
    assign_three(arb, "disp-1", now=T0)
    arb.cast_vote("disp-1", reviewer="r-1", verdict="uphold", now=T0)
    with pytest.raises(da.DisputeError) as exc:
        arb.cast_vote("disp-1", reviewer="r-1", verdict="overturn", now=T0)
    assert code_of(exc) == "DISPUTE_DUPLICATE_VOTE"
    assert arb.disputes()[0]["votes"] == 1


def test_resolve_waits_for_all_votes():
    arb = with_match()
    arb.submit_dispute("m-1", player_id="alice", reason="误判", now=T0)
    with pytest.raises(da.DisputeError) as exc:
        arb.resolve("disp-1", now=T0)
    assert code_of(exc) == "DISPUTE_VOTES_PENDING"
    assign_three(arb, "disp-1", now=T0)
    arb.cast_vote("disp-1", reviewer="r-1", verdict="overturn", now=T0)
    arb.cast_vote("disp-1", reviewer="r-2", verdict="overturn", now=T0)
    with pytest.raises(da.DisputeError) as exc:
        arb.resolve("disp-1", now=T0)
    assert code_of(exc) == "DISPUTE_VOTES_PENDING"
    assert exc.value.detail == {"dispute_id": "disp-1", "votes": 2}


def test_overturn_votes_flip_match_winner():
    arb = with_match()
    arb.submit_dispute("m-1", player_id="bob", reason="误判", now=T0)
    assign_three(arb, "disp-1", now=T0)
    for reviewer in ("r-1", "r-2", "r-3"):
        arb.cast_vote("disp-1", reviewer=reviewer, verdict="overturn", now=T0)
    row = arb.resolve("disp-1", now=T0)
    assert row == {
        "status": "resolved",
        "dispute_id": "disp-1",
        "outcome": "overturned",
        "match_id": "m-1",
        "winner": "bob",
    }
    result = arb.match_result("m-1")
    assert result["winner"] == "bob"
    assert result["status"] == "overturned"
    assert arb.stats()["resolved"] == 1
    assert arb.stats()["overturned"] == 1
    with pytest.raises(da.DisputeError) as exc:
        arb.resolve("disp-1", now=T0)
    assert code_of(exc) == "DISPUTE_ALREADY_RESOLVED"


def test_uphold_majority_keeps_match_winner():
    arb = with_match()
    arb.submit_dispute("m-1", player_id="bob", reason="误判", now=T0)
    assign_three(arb, "disp-1", now=T0)
    arb.cast_vote("disp-1", reviewer="r-1", verdict="uphold", now=T0)
    arb.cast_vote("disp-1", reviewer="r-2", verdict="overturn", now=T0)
    arb.cast_vote("disp-1", reviewer="r-3", verdict="uphold", now=T0)
    row = arb.resolve("disp-1", now=T0)
    assert row["outcome"] == "upheld"
    assert row["winner"] == "alice"
    assert arb.match_result("m-1")["status"] == "recorded"
    assert arb.stats()["overturned"] == 0


def test_withdraw_only_by_filer_before_assignment():
    arb = arbiter()
    arb.open_match("m-1", winner="alice",
                   participants=("alice", "bob"), now=T0)
    arb.submit_dispute("m-1", player_id="alice", reason="误判", now=T0)
    with pytest.raises(da.DisputeError) as exc:
        arb.withdraw_dispute("disp-1", player_id="bob", now=T0)
    assert code_of(exc) == "DISPUTE_NOT_FILER"
    assert arb.withdraw_dispute("disp-1", player_id="alice", now=T0) == {
        "status": "withdrawn", "dispute_id": "disp-1", "match_id": "m-1"}
    with pytest.raises(da.DisputeError) as exc:
        arb.withdraw_dispute("disp-1", player_id="alice", now=T0)
    assert code_of(exc) == "DISPUTE_LOCKED"

    arb.open_match("m-2", winner="alice",
                   participants=("alice", "bob"), now=T0)
    arb.submit_dispute("m-2", player_id="bob", reason="误判", now=T0)
    assign_three(arb, "disp-2", now=T0)
    with pytest.raises(da.DisputeError) as exc:
        arb.withdraw_dispute("disp-2", player_id="bob", now=T0)
    assert code_of(exc) == "DISPUTE_LOCKED"
    assert arb.stats()["withdrawn"] == 1


def test_expire_marks_filed_expired_and_reviewing_escalated():
    arb = arbiter()
    arb.open_match("m-1", winner="alice",
                   participants=("alice", "bob"), now=T0)
    arb.open_match("m-2", winner="alice",
                   participants=("alice", "bob"), now=T0)
    arb.submit_dispute("m-1", player_id="alice", reason="误判", now=T0)
    arb.submit_dispute("m-2", player_id="bob", reason="误判", now=T0)
    assign_three(arb, "disp-2", now=T0)
    arb.cast_vote("disp-2", reviewer="r-1", verdict="uphold", now=T0)

    assert arb.expire(now="2026-11-24T10:29:59+00:00") == 0
    assert arb.expire(now="2026-11-24T10:30:00+00:00") == 2
    rows = arb.disputes()
    assert [row["status"] for row in rows] == ["expired", "escalated"]
    assert arb.stats()["expired"] == 1
    assert arb.stats()["escalated"] == 1
    with pytest.raises(da.DisputeError) as exc:
        arb.cast_vote("disp-2", reviewer="r-2", verdict="uphold",
                      now="2026-11-24T10:30:01+00:00")
    assert code_of(exc) == "DISPUTE_NOT_REVIEWING"


def test_concurrent_submit_vote_and_resolve_are_single_winner():
    arb = with_match()
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def submit_worker():
        barrier.wait()
        try:
            row = arb.submit_dispute("m-1", player_id="alice", reason="误判",
                                     now=T0)
        except da.DisputeError as exc:
            result = exc.code
        else:
            result = row["status"]
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=submit_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("filed") == 1
    assert outcomes.count("DISPUTE_DUPLICATE") == 7

    assign_three(arb, "disp-1", now=T0)
    outcomes.clear()
    barrier = threading.Barrier(8)

    def vote_worker():
        barrier.wait()
        try:
            row = arb.cast_vote("disp-1", reviewer="r-1", verdict="overturn",
                                now=T0)
        except da.DisputeError as exc:
            result = exc.code
        else:
            result = row["status"]
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=vote_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("voted") == 1
    assert outcomes.count("DISPUTE_DUPLICATE_VOTE") == 7

    arb.cast_vote("disp-1", reviewer="r-2", verdict="overturn", now=T0)
    arb.cast_vote("disp-1", reviewer="r-3", verdict="overturn", now=T0)
    outcomes.clear()
    barrier = threading.Barrier(8)

    def resolve_worker():
        barrier.wait()
        try:
            row = arb.resolve("disp-1", now=T0)
        except da.DisputeError as exc:
            result = exc.code
        else:
            result = row["outcome"]
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=resolve_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("overturned") == 1
    assert outcomes.count("DISPUTE_ALREADY_RESOLVED") == 7
    assert arb.stats()["resolved"] == 1


def test_snapshot_restore_and_corrupt_state():
    arb = arbiter()
    arb.open_match("m-1", winner="alice",
                   participants=("alice", "bob"), now=T0)
    arb.open_match("m-2", winner="alice",
                   participants=("alice", "bob"), now=T0)
    arb.submit_dispute("m-1", player_id="alice", reason="a", now=T0)
    arb.submit_dispute("m-2", player_id="bob", reason="b", now=T0)
    assign_three(arb, "disp-2", now=T0)
    arb.cast_vote("disp-2", reviewer="r-1", verdict="uphold", now=T0)
    state = arb.snapshot()
    assert state["version"] == 1

    arb.cast_vote("disp-2", reviewer="r-2", verdict="overturn", now=T0)

    restored = arbiter()
    restored.restore(state)
    rows = restored.disputes()
    assert [row["dispute_id"] for row in rows] == ["disp-1", "disp-2"]
    assert [row["status"] for row in rows] == ["filed", "reviewing"]
    assert rows[1]["votes"] == 1
    assert restored.stats()["assigned"] == 1

    with pytest.raises(da.DisputeError) as exc:
        restored.cast_vote("disp-2", reviewer="r-1", verdict="overturn", now=T0)
    assert code_of(exc) == "DISPUTE_DUPLICATE_VOTE"

    restored.cast_vote("disp-2", reviewer="r-2", verdict="overturn", now=T0)
    restored.cast_vote("disp-2", reviewer="r-3", verdict="overturn", now=T0)
    assert restored.resolve("disp-2", now=T0)["outcome"] == "overturned"
    assert restored.match_result("m-2")["winner"] == "bob"
    assert restored.stats()["resolved"] == 1

    state["disputes"][1]["votes"].clear()
    state["stats"]["resolved"] = 99
    assert restored.disputes()[1]["votes"] == 3
    assert restored.stats()["resolved"] == 1

    bad_states = [
        None,
        {},
        {"version": 2, "matches": [], "disputes": [], "seq": 0, "stats": {}},
        {"version": 1, "matches": "nope", "disputes": [], "seq": 0, "stats": {}},
        {"version": 1, "matches": [], "disputes": [{"status": "bogus"}],
         "seq": 0, "stats": {}},
        {"version": 1, "matches": [], "disputes": [], "seq": 0, "stats": []},
    ]
    for bad in bad_states:
        with pytest.raises(da.DisputeError) as exc:
            restored.restore(bad)
        assert code_of(exc) == "DISPUTE_SNAPSHOT_INVALID"
    assert restored.stats()["resolved"] == 1
