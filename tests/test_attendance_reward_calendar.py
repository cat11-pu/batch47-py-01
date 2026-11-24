import threading
from datetime import datetime, timedelta, timezone

import pytest

import attendance_reward_calendar as att

BASE = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)


def at(day_number: int) -> str:
    return (BASE + timedelta(days=day_number - 1)).isoformat()


def day(day_number: int) -> str:
    return (BASE.date() + timedelta(days=day_number - 1)).isoformat()


def calendar(**kwargs):
    days = kwargs.get("cycle_days", 7)
    kwargs.setdefault("rewards", [{"item": f"day-{index + 1}",
                                   "count": 10 * (index + 1)}
                                  for index in range(days)])
    return att.AttendanceCalendar(**kwargs)


def test_first_check_in_starts_cycle_at_position_one():
    box = calendar()
    result = box.check_in("p1", now=at(1))
    assert result["status"] == "checked"
    assert result["date"] == day(1)
    assert result["streak"] == 1
    assert result["cycle_index"] == 0
    assert result["position"] == 1
    assert result["reward"] == {"item": "day-1", "count": 10}
    assert result["bonus"] is None

    state = box.status("p1", now=at(1))
    assert state["started"] is True
    assert state["checked_today"] is True
    assert state["days_checked"] == [day(1)]
    assert state["makeup_left"] == 2
    assert state["completed_cycles"] == 0
    assert state["next_reward"] == {"item": "day-1", "count": 10}


def test_same_day_check_in_is_reported_without_a_second_reward():
    box = calendar()
    box.check_in("p1", now=at(1))
    again = box.check_in("p1", now=at(1))
    assert again["status"] == "already_checked"
    assert again["code"] == "ATT_ALREADY_CHECKED"
    assert again["streak"] == 1
    assert box.stats()["check_ins"] == 1
    assert box.stats()["rejected"] == 1
    assert box.status("p1", now=at(1))["days_checked"] == [day(1)]


def test_consecutive_days_grow_streak_and_advance_position():
    box = calendar()
    results = [box.check_in("p1", now=at(n)) for n in (1, 2, 3)]
    assert [row["streak"] for row in results] == [1, 2, 3]
    assert [row["position"] for row in results] == [1, 2, 3]
    assert [row["reward"]["item"] for row in results] == [
        "day-1", "day-2", "day-3"]


def test_gap_resets_streak_until_makeup_bridges_it():
    box = calendar()
    box.check_in("p1", now=at(1))
    third = box.check_in("p1", now=at(3))
    assert third["streak"] == 1
    assert third["position"] == 3

    box.grant_makeup_cards("p1", 1)
    fixed = box.makeup("p1", day(2), now=at(3))
    assert fixed["status"] == "made_up"
    assert fixed["streak"] == 3
    assert fixed["reward"] == {"item": "day-2", "count": 20}
    assert fixed["makeup_cards"] == 0
    assert [row["date"] for row in box.history("p1")] == [
        day(3), day(2), day(1)]


def test_eighth_day_rolls_into_the_next_cycle():
    box = calendar()
    results = [box.check_in("p1", now=at(n)) for n in range(1, 9)]
    assert results[6]["cycle_index"] == 0
    assert results[6]["position"] == 7
    assert results[6]["bonus"] == {"item": "cycle_chest", "count": 1}
    assert results[7]["cycle_index"] == 1
    assert results[7]["position"] == 1
    assert results[7]["streak"] == 8
    assert results[7]["bonus"] is None


def test_cycle_bonus_is_granted_once_for_a_full_board():
    box = calendar()
    for n in range(1, 8):
        box.check_in("p1", now=at(n))
    assert box.stats()["bonuses"] == 1
    assert box.status("p1", now=at(7))["completed_cycles"] == 1
    repeat = box.check_in("p1", now=at(7))
    assert repeat["status"] == "already_checked"
    assert box.stats()["bonuses"] == 1
    assert box.stats()["check_ins"] == 7


def test_makeup_consumes_a_card_and_keeps_the_chain_alive():
    box = calendar()
    box.grant_makeup_cards("p1", 2)
    box.check_in("p1", now=at(1))
    box.check_in("p1", now=at(3))
    fixed = box.makeup("p1", day(2), now=at(3))
    assert fixed["makeup_cards"] == 1
    state = box.status("p1", now=at(3))
    assert state["makeup_cards"] == 1
    assert state["makeup_left"] == 1
    assert state["streak"] == 3
    assert box.stats()["makeups"] == 1
    assert box.check_in("p1", now=at(4))["streak"] == 4


def test_makeup_rejects_unstarted_future_duplicate_and_out_of_cycle():
    box = calendar()
    with pytest.raises(att.AttendanceError) as exc:
        box.makeup("newbie", day(1), now=at(2))
    assert exc.value.code == "ATT_NOT_STARTED"

    box.check_in("p1", now=at(1))
    with pytest.raises(att.AttendanceError) as exc:
        box.makeup("p1", day(1), now=at(1))
    assert exc.value.code == "ATT_MAKEUP_FUTURE"
    with pytest.raises(att.AttendanceError) as exc:
        box.makeup("p1", day(2), now=at(1))
    assert exc.value.code == "ATT_MAKEUP_FUTURE"
    box.check_in("p1", now=at(2))
    box.check_in("p1", now=at(3))
    with pytest.raises(att.AttendanceError) as exc:
        box.makeup("p1", day(2), now=at(3))
    assert exc.value.code == "ATT_ALREADY_CHECKED"

    with pytest.raises(att.AttendanceError) as exc:
        box.makeup("p1", day(4), now=at(7))
    assert exc.value.code == "ATT_NO_MAKEUP_CARD"

    short = calendar(cycle_days=3)
    short.check_in("p1", now=at(1))
    with pytest.raises(att.AttendanceError) as exc:
        short.makeup("p1", day(2), now=at(5))
    assert exc.value.code == "ATT_MAKEUP_OUT_OF_CYCLE"


def test_makeup_limit_is_counted_per_cycle():
    box = calendar(makeup_limit=1)
    box.grant_makeup_cards("p1", 3)
    box.check_in("p1", now=at(1))
    assert box.makeup("p1", day(2), now=at(4))["status"] == "made_up"
    with pytest.raises(att.AttendanceError) as exc:
        box.makeup("p1", day(3), now=at(4))
    assert exc.value.code == "ATT_MAKEUP_LIMIT"
    assert box.status("p1", now=at(4))["makeup_cards"] == 2


def test_makeup_card_grants_validate_and_respect_the_cap():
    box = calendar()
    with pytest.raises(att.AttendanceError) as exc:
        box.grant_makeup_cards("p1", 0)
    assert exc.value.code == "ATT_BAD_REQUEST"
    with pytest.raises(att.AttendanceError) as exc:
        box.grant_makeup_cards("p1", 6)
    assert exc.value.code == "ATT_CARD_LIMIT"
    assert box.grant_makeup_cards("p1", 5)["makeup_cards"] == 5
    with pytest.raises(att.AttendanceError) as exc:
        box.grant_makeup_cards("p1", 1)
    assert exc.value.code == "ATT_CARD_LIMIT"
    assert box.status("p1", now=at(1))["makeup_cards"] == 5


def test_status_reports_progress_across_cycle_boundary():
    box = calendar()
    box.check_in("p1", now=at(1))
    box.check_in("p1", now=at(2))
    state = box.status("p1", now=at(2))
    assert state["position"] == 2
    assert state["days_checked"] == [day(1), day(2)]
    assert state["streak"] == 2
    assert state["next_reward"] == {"item": "day-2", "count": 20}

    later = box.status("p1", now=at(8))
    assert later["cycle_index"] == 1
    assert later["position"] == 1
    assert later["days_checked"] == []
    assert later["next_reward"] == {"item": "day-1", "count": 10}


def test_history_is_newest_first_and_honours_limit():
    box = calendar()
    for n in (1, 2, 3):
        box.check_in("p1", now=at(n))
    rows = box.history("p1", limit=2)
    assert [row["date"] for row in rows] == [day(3), day(2)]
    assert [row["position"] for row in rows] == [3, 2]
    assert len(box.history("p1", limit=10)) == 3
    with pytest.raises(att.AttendanceError) as exc:
        box.history("p1", limit=0)
    assert exc.value.code == "ATT_BAD_REQUEST"


def test_snapshot_restore_preserves_chain_cards_and_bonus_flags():
    box = calendar()
    box.check_in("p1", now=at(1))
    box.check_in("p1", now=at(3))
    box.grant_makeup_cards("p1", 2)
    box.makeup("p1", day(2), now=at(3))
    state = box.snapshot()
    box.check_in("p1", now=at(4))

    restored = calendar()
    restored.restore(state)
    before = restored.status("p1", now=at(4))
    assert before["streak"] == 3
    assert before["checked_today"] is False
    assert before["makeup_cards"] == 1
    assert before["makeup_left"] == 1
    assert [row["date"] for row in restored.history("p1")] == [
        day(3), day(2), day(1)]
    assert restored.check_in("p1", now=at(4))["streak"] == 4
    assert box.status("p1", now=at(4))["checked_today"] is True


def test_corrupt_snapshot_is_refused_without_mutation():
    box = calendar()
    box.check_in("p1", now=at(1))
    bad_states = [
        None,
        {},
        {"version": 2, "players": {}, "stats": {}},
        {"version": 1, "players": [], "stats": {}},
        {"version": 1, "players": {}, "stats": []},
        {"version": 1, "players": {"p1": {"anchor": "not-a-date",
                                          "checked": [], "makeup_cards": 0,
                                          "makeup_used": {},
                                          "bonus_claimed": []}}, "stats": {}},
        {"version": 1, "players": {"p1": {"anchor": day(2),
                                          "checked": [day(2), day(2)],
                                          "makeup_cards": 0,
                                          "makeup_used": {},
                                          "bonus_claimed": []}}, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(att.AttendanceError) as exc:
            box.restore(bad)
        assert exc.value.code == "ATT_SNAPSHOT_INVALID"
    assert box.status("p1", now=at(1))["checked_today"] is True
    assert box.stats()["check_ins"] == 1


def test_concurrent_check_in_grants_one_reward():
    box = calendar()
    barrier = threading.Barrier(8)
    results = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = box.check_in("p1", now=at(1))
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            row = {"status": "error", "code": repr(exc)}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["already_checked"] * 7 + ["checked"]
    assert box.stats()["check_ins"] == 1
    assert box.stats()["rejected"] == 7
    box.reset_stats()
    assert box.stats() == {"check_ins": 0, "makeups": 0, "rewards": 0,
                           "bonuses": 0, "rejected": 0}
