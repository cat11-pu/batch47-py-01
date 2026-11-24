import threading

import pytest

import guild_quest_board as qb

T0 = "2026-11-24T10:00:00+00:00"


def board(**kwargs):
    return qb.GuildQuestBoard(**kwargs)


def with_quest(**kwargs):
    board_obj = board(**kwargs)
    board_obj.publish("g-1", "q-1", title="采集狼牙", slots=2, target=2,
                      reward=100, min_level=10, now=T0)
    return board_obj


def code_of(excinfo):
    return excinfo.value.code


def test_publish_returns_open_quest_and_rejects_duplicate():
    board_obj = board()
    row = board_obj.publish("g-1", "q-1", title="采集狼牙", slots=2, target=3,
                            reward=120, min_level=10, now=T0)
    assert row == {
        "guild_id": "g-1",
        "quest_id": "q-1",
        "title": "采集狼牙",
        "slots": 2,
        "target": 3,
        "reward": 120,
        "min_level": 10,
        "requires": [],
        "status": "open",
        "expires_at": None,
    }
    assert board_obj.quest("g-1", "q-1") == {
        "guild_id": "g-1",
        "quest_id": "q-1",
        "title": "采集狼牙",
        "slots": 2,
        "target": 3,
        "reward": 120,
        "min_level": 10,
        "requires": [],
        "status": "open",
        "completed": 0,
        "claims": 0,
        "expires_at": None,
    }
    with pytest.raises(qb.QuestError) as exc:
        board_obj.publish("g-1", "q-1", title="重复任务", now=T0)
    assert code_of(exc) == "QUEST_DUPLICATE"
    assert board_obj.stats()["published"] == 1
    with pytest.raises(qb.QuestError) as exc:
        board_obj.quest("g-1", "q-missing")
    assert code_of(exc) == "QUEST_UNKNOWN_QUEST"


def test_publish_validates_spec_and_requires():
    board_obj = board()
    bad_specs = [
        dict(slots=0),
        dict(target=0),
        dict(reward=-1),
        dict(title=""),
        dict(expires_at=T0),
    ]
    for overrides in bad_specs:
        kwargs = dict(title="任务", slots=1, target=1, reward=10, min_level=1)
        kwargs.update(overrides)
        with pytest.raises(qb.QuestError) as exc:
            board_obj.publish("g-1", "q-bad", now=T0, **kwargs)
        assert code_of(exc) == "QUEST_BAD_SPEC"

    with pytest.raises(qb.QuestError) as exc:
        board_obj.publish("g-1", "q-2", title="后续", requires=("q-missing",),
                          now=T0)
    assert code_of(exc) == "QUEST_UNKNOWN_REQUIRES"
    with pytest.raises(qb.QuestError) as exc:
        board_obj.publish("g-1", "q-3", title="自依赖", requires=("q-3",),
                          now=T0)
    assert code_of(exc) == "QUEST_BAD_SPEC"
    assert board_obj.stats()["published"] == 0


def test_claim_checks_guild_quest_and_level():
    board_obj = with_quest()
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-missing", "q-1", player_id="p-1", level=10, now=T0)
    assert code_of(exc) == "QUEST_UNKNOWN_GUILD"
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-missing", player_id="p-1", level=10, now=T0)
    assert code_of(exc) == "QUEST_UNKNOWN_QUEST"
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-1", player_id="p-1", level=9, now=T0)
    assert code_of(exc) == "QUEST_LEVEL_LOW"

    row = board_obj.claim("g-1", "q-1", player_id="p-1", level=10, now=T0)
    assert row == {
        "status": "claimed",
        "guild_id": "g-1",
        "quest_id": "q-1",
        "player_id": "p-1",
        "progress": 0,
        "target": 2,
        "expires_at": "2026-11-24T10:30:00+00:00",
    }
    assert board_obj.claims("g-1", "q-1") == [{
        "guild_id": "g-1",
        "quest_id": "q-1",
        "player_id": "p-1",
        "progress": 0,
        "target": 2,
        "expires_at": "2026-11-24T10:30:00+00:00",
    }]
    assert board_obj.member_summary("g-1", "p-1")["active_claims"] == 1


def test_slots_and_release_free_capacity():
    board_obj = with_quest()
    board_obj.claim("g-1", "q-1", player_id="p-1", level=10, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-2", level=10, now=T0)
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-1", player_id="p-3", level=10, now=T0)
    assert code_of(exc) == "QUEST_FULL"

    assert board_obj.release("g-1", "q-1", player_id="p-1",
                             now=T0) == {"status": "released", "guild_id": "g-1",
                                         "quest_id": "q-1", "player_id": "p-1"}
    assert board_obj.claim("g-1", "q-1", player_id="p-3", level=10,
                           now=T0)["status"] == "claimed"
    assert [row["player_id"] for row in board_obj.claims("g-1", "q-1")] == ["p-2", "p-3"]
    assert board_obj.stats()["claimed"] == 3
    assert board_obj.stats()["released"] == 1


def test_duplicate_claim_and_member_active_limit():
    board_obj = board(max_active_per_member=2)
    for qid in ("q-1", "q-2", "q-3"):
        board_obj.publish("g-1", qid, title=qid, slots=2, target=1, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    assert code_of(exc) == "QUEST_ALREADY_CLAIMED"
    assert len(board_obj.claims("g-1", "q-1")) == 1

    board_obj.claim("g-1", "q-2", player_id="p-1", now=T0)
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-3", player_id="p-1", now=T0)
    assert code_of(exc) == "QUEST_MEMBER_LIMIT"
    board_obj.release("g-1", "q-1", player_id="p-1", now=T0)
    assert board_obj.claim("g-1", "q-3", player_id="p-1",
                           now=T0)["status"] == "claimed"


def test_requires_gate_blocks_claim_until_prerequisite_done():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="前置", slots=3, target=1, now=T0)
    board_obj.publish("g-1", "q-2", title="后续", slots=3, target=1,
                      requires=("q-1",), now=T0)
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-2", player_id="p-1", now=T0)
    assert code_of(exc) == "QUEST_REQUIRES_UNMET"
    assert exc.value.detail == {"quest_id": "q-1"}

    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.report_progress("g-1", "q-1", player_id="p-1", amount=1, now=T0)
    board_obj.complete("g-1", "q-1", player_id="p-1", now=T0)
    assert board_obj.claim("g-1", "q-2", player_id="p-1",
                           now=T0)["status"] == "claimed"


def test_progress_is_clamped_and_amount_validated():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="采集", slots=2, target=3, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    assert board_obj.report_progress("g-1", "q-1", player_id="p-1",
                                     amount=1, now=T0) == {
        "progress": 1, "target": 3, "clamped": False}
    assert board_obj.report_progress("g-1", "q-1", player_id="p-1",
                                     amount=5, now=T0) == {
        "progress": 3, "target": 3, "clamped": True}
    assert board_obj.report_progress("g-1", "q-1", player_id="p-1",
                                     amount=1, now=T0) == {
        "progress": 3, "target": 3, "clamped": True}
    with pytest.raises(qb.QuestError) as exc:
        board_obj.report_progress("g-1", "q-1", player_id="p-1", amount=0, now=T0)
    assert code_of(exc) == "QUEST_BAD_AMOUNT"
    with pytest.raises(qb.QuestError) as exc:
        board_obj.report_progress("g-1", "q-1", player_id="ghost", amount=1, now=T0)
    assert code_of(exc) == "QUEST_NOT_CLAIMANT"


def test_complete_requires_full_progress():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="采集", slots=2, target=2, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.report_progress("g-1", "q-1", player_id="p-1", amount=1, now=T0)
    with pytest.raises(qb.QuestError) as exc:
        board_obj.complete("g-1", "q-1", player_id="p-1", now=T0)
    assert code_of(exc) == "QUEST_INCOMPLETE"
    assert exc.value.detail == {"progress": 1, "target": 2}
    assert len(board_obj.claims("g-1", "q-1")) == 1


def test_complete_settles_once_and_closes_quest():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="采集", slots=1, target=1,
                      reward=120, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.report_progress("g-1", "q-1", player_id="p-1", amount=1, now=T0)
    row = board_obj.complete("g-1", "q-1", player_id="p-1", now=T0)
    assert row == {
        "status": "completed",
        "guild_id": "g-1",
        "quest_id": "q-1",
        "player_id": "p-1",
        "reward": 120,
        "completed": 1,
        "remaining": 0,
        "quest_status": "closed",
    }
    with pytest.raises(qb.QuestError) as exc:
        board_obj.complete("g-1", "q-1", player_id="p-1", now=T0)
    assert code_of(exc) == "QUEST_ALREADY_DONE"
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-1", player_id="p-2", now=T0)
    assert code_of(exc) == "QUEST_CLOSED"
    assert board_obj.member_summary("g-1", "p-1") == {
        "guild_id": "g-1",
        "player_id": "p-1",
        "active_claims": 0,
        "completed": 1,
        "reward_total": 120,
    }
    assert board_obj.stats()["completed"] == 1
    assert board_obj.stats()["reward_total"] == 120


def test_claim_ttl_expires_on_complete():
    board_obj = board(claim_ttl=600)
    board_obj.publish("g-1", "q-1", title="采集", slots=2, target=1, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.report_progress("g-1", "q-1", player_id="p-1", amount=1, now=T0)
    with pytest.raises(qb.QuestError) as exc:
        board_obj.complete("g-1", "q-1", player_id="p-1",
                           now="2026-11-24T10:10:00+00:00")
    assert code_of(exc) == "QUEST_CLAIM_EXPIRED"
    assert board_obj.claims("g-1", "q-1") == []
    assert board_obj.quest("g-1", "q-1")["status"] == "open"
    assert board_obj.stats()["expired_claims"] == 1
    assert board_obj.claim("g-1", "q-1", player_id="p-2",
                           now="2026-11-24T10:10:01+00:00")["status"] == "claimed"


def test_expire_sweeps_claim_ttl_and_quest_deadline():
    board_obj = board(claim_ttl=600)
    board_obj.publish("g-1", "q-1", title="长期", slots=2, target=5, now=T0)
    board_obj.publish("g-1", "q-2", title="限时", slots=2, target=5,
                      expires_at="2026-11-24T10:20:00+00:00", now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.claim("g-1", "q-2", player_id="p-2", now=T0)
    assert board_obj.expire(now="2026-11-24T10:09:59+00:00") == 0
    assert board_obj.expire(now="2026-11-24T10:20:00+00:00") == 2
    assert board_obj.claims("g-1", "q-1") == []
    assert board_obj.quest("g-1", "q-1")["status"] == "open"
    assert board_obj.quest("g-1", "q-2")["status"] == "expired"
    assert board_obj.stats()["expired_claims"] == 2


def test_cancel_drops_claims_and_blocks_completion():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="活动任务", slots=3, target=1, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-2", now=T0)
    row = board_obj.cancel("g-1", "q-1", reason="活动下线", now=T0)
    assert row == {"status": "cancelled", "guild_id": "g-1",
                   "quest_id": "q-1", "dropped_claims": 2}
    assert board_obj.claims("g-1", "q-1") == []
    with pytest.raises(qb.QuestError) as exc:
        board_obj.complete("g-1", "q-1", player_id="p-1", now=T0)
    assert code_of(exc) == "QUEST_CLOSED"
    with pytest.raises(qb.QuestError) as exc:
        board_obj.claim("g-1", "q-1", player_id="p-3", now=T0)
    assert code_of(exc) == "QUEST_CLOSED"
    with pytest.raises(qb.QuestError) as exc:
        board_obj.cancel("g-1", "q-1", now=T0)
    assert code_of(exc) == "QUEST_CLOSED"


def test_concurrent_claim_of_last_slot_has_one_winner():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="限一名额", slots=1, target=1, now=T0)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            row = board_obj.claim("g-1", "q-1", player_id=f"p-{index}", now=T0)
        except qb.QuestError as exc:
            result = exc.code
        else:
            result = row["status"]
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("claimed") == 1
    assert outcomes.count("QUEST_FULL") == 7
    assert len(board_obj.claims("g-1", "q-1")) == 1
    assert board_obj.stats()["claimed"] == 1


def test_concurrent_complete_settles_reward_once():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="结算", slots=1, target=1,
                      reward=80, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.report_progress("g-1", "q-1", player_id="p-1", amount=1, now=T0)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = board_obj.complete("g-1", "q-1", player_id="p-1", now=T0)
        except qb.QuestError as exc:
            result = exc.code
        else:
            result = row["status"]
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("completed") == 1
    assert outcomes.count("QUEST_ALREADY_DONE") == 7
    assert board_obj.stats()["completed"] == 1
    assert board_obj.stats()["reward_total"] == 80


def test_snapshot_restore_and_corrupt_state():
    board_obj = board()
    board_obj.publish("g-1", "q-1", title="采集", slots=2, target=1,
                      reward=50, now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.report_progress("g-1", "q-1", player_id="p-1", amount=1, now=T0)
    board_obj.complete("g-1", "q-1", player_id="p-1", now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-2", now=T0)
    state = board_obj.snapshot()
    assert state["version"] == 1

    board_obj.release("g-1", "q-1", player_id="p-2", now=T0)
    board_obj.claim("g-1", "q-1", player_id="p-3", now=T0)

    restored = board()
    restored.restore(state)
    assert restored.quest("g-1", "q-1")["completed"] == 1
    assert [row["player_id"] for row in restored.claims("g-1", "q-1")] == ["p-2"]
    assert restored.member_summary("g-1", "p-1")["reward_total"] == 50
    assert restored.stats()["completed"] == 1

    state["boards"]["g-1"][0]["claims"].clear()
    state["stats"]["completed"] = 99
    assert len(restored.claims("g-1", "q-1")) == 1
    assert restored.stats()["completed"] == 1

    assert restored.report_progress("g-1", "q-1", player_id="p-2",
                                    amount=1, now=T0)["progress"] == 1
    assert restored.complete("g-1", "q-1", player_id="p-2",
                             now=T0)["quest_status"] == "closed"

    bad_states = [
        None,
        {},
        {"version": 2, "boards": {}, "members": [], "stats": {}},
        {"version": 1, "boards": [], "members": [], "stats": {}},
        {"version": 1, "boards": {}, "members": "nope", "stats": {}},
        {"version": 1, "boards": {}, "members": [], "stats": []},
    ]
    for bad in bad_states:
        with pytest.raises(qb.QuestError) as exc:
            restored.restore(bad)
        assert code_of(exc) == "QUEST_SNAPSHOT_INVALID"
    assert restored.stats()["completed"] == 2
