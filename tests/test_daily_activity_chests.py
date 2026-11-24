import threading
from datetime import datetime, timezone

import pytest

import daily_activity_chests as ac


def ts(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


DAY1 = ts(2026, 11, 23, 23, 0, 0)
DAY1_LATE = ts(2026, 11, 23, 23, 59, 59)
DAY2 = ts(2026, 11, 24, 0, 0, 0)


def track(**kwargs):
    kwargs.setdefault("milestones", (30, 60, 90))
    t = ac.ActivityChestTrack(**kwargs)
    t.register("p1", now=DAY1)
    return t


def test_milestone_configuration_validation():
    for bad in ([], (60, 30), (30, 30), (0, 30), (30, 101)):
        with pytest.raises(ac.ActivityError) as exc:
            ac.ActivityChestTrack(milestones=bad)
        assert exc.value.code == "ACT_INVALID"
    too_many = tuple(range(10, 21))
    with pytest.raises(ac.ActivityError) as exc:
        ac.ActivityChestTrack(milestones=too_many)
    assert exc.value.code == "ACT_INVALID"
    with pytest.raises(ac.ActivityError) as exc:
        ac.ActivityChestTrack(milestones=(10, 20), max_points_per_day=0)
    assert exc.value.code == "ACT_INVALID"
    with pytest.raises(ac.ActivityError) as exc:
        ac.ActivityChestTrack(source_caps={"battle": -1})
    assert exc.value.code == "ACT_INVALID"
    ok = ac.ActivityChestTrack(milestones=(10, 20), max_points_per_day=50,
                               source_caps={"battle": 20})
    assert isinstance(ok, ac.ActivityChestTrack)


def test_register_is_idempotent_and_progress_starts_empty():
    t = ac.ActivityChestTrack(milestones=(30, 60, 90))
    first = t.register("p1", now=DAY1)
    assert first["player"] == "p1"
    assert first["day"] == t.day_of(DAY1)
    again = t.register("p1", now=DAY1)
    assert again["day"] == first["day"]
    progress = t.progress("p1", now=DAY1)
    assert progress == {"player": "p1", "day": "2026-11-23", "points": 0,
                        "claimed": [], "next": None}
    with pytest.raises(ac.ActivityError) as exc:
        t.progress("ghost", now=DAY1)
    assert exc.value.code == "ACT_UNKNOWN_PLAYER"


def test_add_points_updates_progress_and_breakdown():
    t = track()
    out = t.add_points("p1", "battle", 25, now=DAY1)
    assert out == {"player": "p1", "day": "2026-11-23", "points": 25,
                   "awarded": 25, "dropped": 0}
    t.add_points("p1", "quest", 20, now=DAY1)
    progress = t.progress("p1", now=DAY1)
    assert progress["points"] == 45
    assert progress["next"] == 30
    assert t.source_breakdown("p1", now=DAY1) == {"battle": 25, "quest": 20}
    assert t.stats()["awarded"] == 45


def test_source_cap_limits_awards():
    t = track(source_caps={"battle": 40})
    first = t.add_points("p1", "battle", 25, now=DAY1)
    second = t.add_points("p1", "battle", 25, now=DAY1)
    assert first["awarded"] == 25
    assert second["awarded"] == 15
    assert second["dropped"] == 10
    assert t.progress("p1", now=DAY1)["points"] == 40
    assert t.source_breakdown("p1", now=DAY1) == {"battle": 40}


def test_daily_total_cap_drops_excess():
    t = track()
    t.add_points("p1", "battle", 70, now=DAY1)
    out = t.add_points("p1", "quest", 50, now=DAY1)
    assert out["awarded"] == 30
    assert out["dropped"] == 20
    assert t.progress("p1", now=DAY1)["points"] == 100
    assert t.stats()["dropped"] == 20


def test_add_points_validates_inputs():
    t = track()
    for points in (0, -3, 101, True):
        with pytest.raises(ac.ActivityError) as exc:
            t.add_points("p1", "battle", points, now=DAY1)
        assert exc.value.code == "ACT_INVALID"
    with pytest.raises(ac.ActivityError) as exc:
        t.add_points("p1", "", 5, now=DAY1)
    assert exc.value.code == "ACT_INVALID"
    with pytest.raises(ac.ActivityError) as exc:
        t.add_points("ghost", "battle", 5, now=DAY1)
    assert exc.value.code == "ACT_UNKNOWN_PLAYER"


def test_claim_requires_known_threshold_and_enough_points():
    t = track()
    t.add_points("p1", "battle", 20, now=DAY1)
    with pytest.raises(ac.ActivityError) as exc:
        t.claim("p1", 45, now=DAY1)
    assert exc.value.code == "ACT_UNKNOWN_THRESHOLD"
    with pytest.raises(ac.ActivityError) as exc:
        t.claim("p1", 30, now=DAY1)
    assert exc.value.code == "ACT_INSUFFICIENT_POINTS"
    assert t.stats()["claimed"] == 0


def test_claims_must_follow_threshold_order():
    t = track()
    t.add_points("p1", "battle", 100, now=DAY1)
    with pytest.raises(ac.ActivityError) as exc:
        t.claim("p1", 90, now=DAY1)
    assert exc.value.code == "ACT_OUT_OF_ORDER"
    assert t.claim("p1", 30, now=DAY1) == {
        "player": "p1", "day": "2026-11-23", "threshold": 30,
        "status": "claimed"}
    with pytest.raises(ac.ActivityError) as exc:
        t.claim("p1", 30, now=DAY1)
    assert exc.value.code == "ACT_ALREADY_CLAIMED"
    assert t.claim("p1", 60, now=DAY1)["status"] == "claimed"
    progress = t.progress("p1", now=DAY1)
    assert progress["claimed"] == [30, 60]
    assert progress["next"] == 90
    assert t.stats()["claimed"] == 2


def test_utc_day_boundary_resets_points_and_claims():
    t = track()
    t.add_points("p1", "battle", 100, now=DAY1_LATE)
    t.claim("p1", 30, now=DAY1_LATE)
    before = t.progress("p1", now=DAY1_LATE)
    assert before["day"] == "2026-11-23"
    assert before["claimed"] == [30]
    after = t.progress("p1", now=DAY2)
    assert after["day"] == "2026-11-24"
    assert after["points"] == 0
    assert after["claimed"] == []
    assert t.stats()["expired_chests"] == 2
    assert t.stats()["rollovers"] == 1


def test_rollover_reports_reset_players_and_rejects_clock_rewind():
    t = track()
    t.register("p2", now=DAY1)
    t.add_points("p1", "battle", 10, now=DAY1)
    t.add_points("p2", "battle", 10, now=DAY1)
    assert t.rollover(now=DAY1) == 0
    assert t.rollover(now=DAY2) == 2
    assert t.progress("p1", now=DAY2)["points"] == 0
    with pytest.raises(ac.ActivityError) as exc:
        t.add_points("p1", "battle", 5, now=DAY1)
    assert exc.value.code == "ACT_CLOCK_BACKWARDS"


def test_unclaimed_lists_reached_but_unclaimed_milestones():
    t = track()
    t.add_points("p1", "battle", 100, now=DAY1)
    assert t.unclaimed("p1", now=DAY1) == [30, 60, 90]
    t.claim("p1", 30, now=DAY1)
    assert t.unclaimed("p1", now=DAY1) == [60, 90]
    t.add_points("p1", "battle", 1, now=DAY1)
    assert t.unclaimed("p1", now=DAY1) == [60, 90]


def test_claim_request_id_idempotency_and_conflict():
    t = track()
    t.add_points("p1", "battle", 100, now=DAY1)
    first = t.claim("p1", 30, now=DAY1, request_id="rq-1")
    second = t.claim("p1", 30, now=DAY1, request_id="rq-1")
    assert second == first
    assert t.stats()["claimed"] == 1
    with pytest.raises(ac.ActivityError) as exc:
        t.claim("p1", 60, now=DAY1, request_id="rq-1")
    assert exc.value.code == "ACT_REQUEST_CONFLICT"


def test_concurrent_adds_respect_daily_cap():
    t = track()
    barrier = threading.Barrier(20)
    guard = threading.Lock()
    errors = []

    def worker():
        barrier.wait()
        try:
            t.add_points("p1", "battle", 10, now=DAY1)
        except Exception as exc:  # pragma: no cover
            with guard:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert t.progress("p1", now=DAY1)["points"] == 100
    assert t.stats()["awarded"] == 100
    assert t.stats()["dropped"] == 100


def test_concurrent_claim_succeeds_once():
    t = track()
    t.add_points("p1", "battle", 100, now=DAY1)
    barrier = threading.Barrier(8)
    outcomes = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = t.claim("p1", 30, now=DAY1)["status"]
        except ac.ActivityError as exc:
            row = exc.code
        with guard:
            outcomes.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("claimed") == 1
    assert outcomes.count("ACT_ALREADY_CLAIMED") == 7
    assert t.stats()["claimed"] == 1


def test_snapshot_restore_and_corrupt_state():
    t = track()
    t.add_points("p1", "battle", 70, now=DAY1)
    t.claim("p1", 30, now=DAY1)
    snap = t.snapshot()
    assert snap["version"] == 1
    t.add_points("p1", "quest", 20, now=DAY1)
    restored = ac.ActivityChestTrack(milestones=(30, 60, 90))
    restored.restore(snap)
    progress = restored.progress("p1", now=DAY1)
    assert progress["points"] == 70
    assert progress["claimed"] == [30]
    assert restored.stats()["claimed"] == 1
    snap["players"]["p1"]["points"] = 0
    assert restored.progress("p1", now=DAY1)["points"] == 70
    bad_states = [
        None,
        {},
        {"version": 2, "players": {}, "stats": {}},
        {"version": 1,
         "players": {"p1": {"day": "2026-11-23", "points": "x",
                            "claimed": [], "sources": {}}},
         "stats": {}},
    ]
    for state in bad_states:
        with pytest.raises(ac.ActivityError) as exc:
            restored.restore(state)
        assert exc.value.code == "ACT_SNAPSHOT_INVALID"
    assert restored.progress("p1", now=DAY1)["points"] == 70
