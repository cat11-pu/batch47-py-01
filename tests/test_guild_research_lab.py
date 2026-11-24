import copy
import threading
from datetime import datetime, timedelta

import pytest

import guild_research_lab as rl

NOW = "2026-11-24T10:00:00+00:00"
BASE = datetime.fromisoformat(NOW)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def make_lab(**kwargs):
    kwargs.setdefault("daily_cap", 100000)
    return rl.GuildResearchLab(**kwargs)


def add(lab, node_id, *, tier=1, cost=100, duration=60, requires=()):
    return lab.define_node(node_id, name="project-" + node_id, tier=tier,
                           cost=cost, duration=duration, requires=requires)


def fund(lab, amount=1000, member="gm", now=NOW):
    return lab.contribute(member, amount, now=now)


def test_define_and_status_show_available_node():
    lab = make_lab()
    row = add(lab, "farming", tier=1, cost=200, duration=120)

    assert row["node_id"] == "farming"
    assert row["requires"] == []
    status = lab.status("farming")
    assert status["state"] == "available"
    assert status["cost"] == 200
    assert status["tier"] == 1
    assert status["missing_requires"] == []

    with pytest.raises(rl.ResearchError) as exc:
        lab.status("ghost")
    assert exc.value.code == "RESEARCH_UNKNOWN_NODE"

    with pytest.raises(rl.ResearchError) as exc:
        lab.start("ghost", now=NOW)
    assert exc.value.code == "RESEARCH_UNKNOWN_NODE"


def test_duplicate_definition_is_idempotent_but_conflict_is_rejected():
    lab = make_lab()
    first = add(lab, "smithing", cost=100, duration=60)
    again = add(lab, "smithing", cost=100, duration=60)
    assert again == first

    with pytest.raises(rl.ResearchError) as exc:
        add(lab, "smithing", cost=150, duration=60)
    assert exc.value.code == "RESEARCH_NODE_CONFLICT"
    assert lab.status("smithing")["cost"] == 100


def test_self_dependency_is_rejected_and_missing_prereq_locks_node():
    lab = make_lab()
    with pytest.raises(rl.ResearchError) as exc:
        add(lab, "loop", requires=("loop",))
    assert exc.value.code == "RESEARCH_CYCLE"

    add(lab, "base", cost=100, duration=60)
    add(lab, "child", cost=100, duration=60, requires=("base",))
    status = lab.status("child")
    assert status["state"] == "locked"
    assert status["missing_requires"] == ["base"]

    with pytest.raises(rl.ResearchError) as exc:
        lab.start("child", now=NOW)
    assert exc.value.code == "RESEARCH_PREREQ_MISSING"
    assert exc.value.detail == ["base"]


def test_contributions_update_member_totals_and_pool():
    lab = make_lab()
    first = lab.contribute("alice", 300, now=NOW)
    assert first["member_id"] == "alice"
    assert first["amount"] == 300
    assert first["member_total"] == 300
    assert first["today_total"] == 300
    assert first["pool"] == 300
    assert first["day"] == "2026-11-24"

    second = lab.contribute("alice", 200, now=NOW)
    assert second["member_total"] == 500
    assert second["today_total"] == 500

    other = lab.contribute("bob", 400, now=NOW)
    assert other["member_total"] == 400
    assert other["pool"] == 900
    assert lab.pool() == 900
    assert lab.stats()["contributed"] == 900


def test_invalid_contribution_amounts_leave_pool_untouched():
    lab = make_lab()
    for bad in (0, -5, 2.5, True):
        with pytest.raises(rl.ResearchError) as exc:
            lab.contribute("alice", bad, now=NOW)
        assert exc.value.code == "RESEARCH_INVALID_AMOUNT"

    with pytest.raises(rl.ResearchError) as exc:
        lab.contribute("", 100, now=NOW)
    assert exc.value.code == "RESEARCH_INVALID_MEMBER"
    assert lab.pool() == 0
    assert lab.stats()["contributed"] == 0


def test_daily_cap_blocks_excess_and_resets_on_utc_day_change():
    lab = make_lab(daily_cap=500)
    lab.contribute("alice", 400, now=NOW)

    with pytest.raises(rl.ResearchError) as exc:
        lab.contribute("alice", 200, now=NOW)
    assert exc.value.code == "RESEARCH_DAILY_CAP"
    assert lab.pool() == 400

    next_day = lab.contribute("alice", 200, now=at(86400))
    assert next_day["today_total"] == 200
    assert next_day["member_total"] == 600
    assert next_day["day"] == "2026-11-25"
    assert lab.pool() == 600


def test_prerequisite_completion_unlocks_dependent_node():
    lab = make_lab()
    add(lab, "base", tier=1, cost=100, duration=60)
    add(lab, "advanced", tier=2, cost=150, duration=60, requires=("base",))
    fund(lab, 500)

    with pytest.raises(rl.ResearchError) as exc:
        lab.start("advanced", now=NOW)
    assert exc.value.code == "RESEARCH_PREREQ_MISSING"

    started = lab.start("base", now=NOW)
    assert started["state"] == "active"
    assert started["pool"] == 400
    assert lab.complete_due(now=at(59)) == []
    rows = lab.complete_due(now=at(60))
    assert [row["node_id"] for row in rows] == ["base"]
    assert rows[0]["tier"] == 1

    assert lab.status("advanced")["state"] == "available"
    lab.start("advanced", now=at(60))
    assert lab.status("advanced")["state"] == "active"


def test_start_with_insufficient_points_changes_nothing():
    lab = make_lab()
    add(lab, "expensive", cost=500, duration=60)
    lab.contribute("gm", 100, now=NOW)

    with pytest.raises(rl.ResearchError) as exc:
        lab.start("expensive", now=NOW)
    assert exc.value.code == "RESEARCH_INSUFFICIENT_POINTS"
    assert lab.pool() == 100
    assert lab.status("expensive")["state"] == "available"
    assert lab.stats()["started"] == 0


def test_concurrency_limit_blocks_extra_project_until_one_finishes():
    lab = make_lab(max_concurrent=1)
    add(lab, "first", cost=100, duration=60)
    add(lab, "second", cost=100, duration=60)
    fund(lab, 500)

    lab.start("first", now=NOW)
    with pytest.raises(rl.ResearchError) as exc:
        lab.start("second", now=NOW)
    assert exc.value.code == "RESEARCH_CONCURRENCY_LIMIT"
    assert lab.status("second")["state"] == "available"

    lab.complete_due(now=at(60))
    started = lab.start("second", now=at(60))
    assert started["state"] == "active"


def test_repeated_start_of_active_or_completed_node_is_refused():
    lab = make_lab()
    add(lab, "solo", cost=100, duration=60)
    fund(lab, 500)
    lab.start("solo", now=NOW)

    with pytest.raises(rl.ResearchError) as exc:
        lab.start("solo", now=NOW)
    assert exc.value.code == "RESEARCH_ALREADY_ACTIVE"

    lab.complete_due(now=at(60))
    with pytest.raises(rl.ResearchError) as exc:
        lab.start("solo", now=at(60))
    assert exc.value.code == "RESEARCH_ALREADY_COMPLETED"

    with pytest.raises(rl.ResearchError) as exc:
        lab.cancel("solo", now=at(60))
    assert exc.value.code == "RESEARCH_ALREADY_COMPLETED"


def test_completion_order_follows_finish_time_and_is_idempotent():
    lab = make_lab(max_concurrent=2)
    add(lab, "short", tier=1, cost=100, duration=60)
    add(lab, "long", tier=2, cost=100, duration=120)
    fund(lab, 500)
    lab.start("short", now=NOW)
    lab.start("long", now=NOW)

    assert lab.complete_due(now=at(59)) == []
    first = lab.complete_due(now=at(60))
    assert [row["node_id"] for row in first] == ["short"]
    assert first[0]["completed_at"] == at(60)
    assert lab.complete_due(now=at(60)) == []

    second = lab.complete_due(now=at(120))
    assert [row["node_id"] for row in second] == ["long"]
    assert second[0]["tier"] == 2
    assert lab.completed() == ["short", "long"]
    assert lab.stats()["completed"] == 2


def test_cancel_refunds_floor_half_and_allows_restart():
    lab = make_lab()
    add(lab, "costly", cost=101, duration=60)
    fund(lab, 300)
    lab.start("costly", now=NOW)
    assert lab.pool() == 199

    result = lab.cancel("costly", now=NOW)
    assert result["refunded"] == 50
    assert result["pool"] == 249
    assert result["state"] == "available"
    assert lab.stats()["cancelled"] == 1
    assert lab.stats()["refunded"] == 50

    with pytest.raises(rl.ResearchError) as exc:
        lab.cancel("costly", now=NOW)
    assert exc.value.code == "RESEARCH_NOT_ACTIVE"

    restarted = lab.start("costly", now=NOW)
    assert restarted["state"] == "active"
    assert lab.pool() == 148


def test_boost_respects_budget_and_moves_finish_time():
    lab = make_lab(boost_budget=100)
    add(lab, "slow", cost=100, duration=300)
    fund(lab, 500)
    lab.start("slow", now=NOW)

    with pytest.raises(rl.ResearchError) as exc:
        lab.boost("slow", -5, now=NOW)
    assert exc.value.code == "RESEARCH_INVALID_AMOUNT"

    boosted = lab.boost("slow", 60, now=NOW)
    assert boosted["boosted_seconds"] == 60
    assert boosted["remaining_seconds"] == 240
    assert boosted["budget_left"] == 40

    with pytest.raises(rl.ResearchError) as exc:
        lab.boost("slow", 80, now=NOW)
    assert exc.value.code == "RESEARCH_BOOST_BUDGET"
    assert lab.status("slow")["finish_at"] == at(240)

    assert lab.complete_due(now=at(239)) == []
    assert [row["node_id"] for row in lab.complete_due(now=at(240))] == ["slow"]


def test_snapshot_restore_keeps_pool_active_and_daily_state():
    lab = make_lab()
    add(lab, "base", cost=100, duration=120)
    add(lab, "child", cost=100, duration=60, requires=("base",))
    fund(lab, 300)
    lab.start("base", now=NOW)
    state = lab.snapshot()
    lab.complete_due(now=at(120))

    restored = rl.GuildResearchLab()
    restored.restore(state)
    assert restored.pool() == 200
    assert restored.status("base")["state"] == "active"
    assert restored.status("child")["state"] == "locked"
    assert restored.completed() == []
    assert restored.stats()["contributed"] == 300
    assert restored.stats()["started"] == 1

    rows = restored.complete_due(now=at(120))
    assert [row["node_id"] for row in rows] == ["base"]
    assert restored.status("child")["state"] == "available"

    valid = copy.deepcopy(state)
    version_two = copy.deepcopy(valid)
    version_two["version"] = 2
    bad_nodes = copy.deepcopy(valid)
    bad_nodes["nodes"] = "nope"
    bad_active = copy.deepcopy(valid)
    bad_active["active"] = "nope"
    bad_stats = copy.deepcopy(valid)
    bad_stats["stats"] = "nope"
    bad_daily = copy.deepcopy(valid)
    bad_daily["daily"] = "nope"

    for bad in (None, {}, version_two, bad_nodes, bad_active, bad_stats, bad_daily):
        with pytest.raises(rl.ResearchError) as exc:
            restored.restore(bad)
        assert exc.value.code == "RESEARCH_SNAPSHOT_INVALID"
    assert restored.pool() == 200
    assert restored.completed() == ["base"]


def test_concurrent_contributions_stop_exactly_at_daily_cap():
    lab = make_lab(daily_cap=500)
    barrier = threading.Barrier(10)
    accepted = []
    rejected = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = lab.contribute("gm", 100, now=NOW)
        except Exception as exc:
            code = getattr(exc, "code", repr(exc))
            with lock:
                rejected.append(code)
            return
        with lock:
            accepted.append(row)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 5
    assert rejected == ["RESEARCH_DAILY_CAP"] * 5
    assert lab.pool() == 500
    assert lab.stats()["contributed"] == 500
