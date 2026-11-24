import copy
import threading
from datetime import datetime, timedelta

import pytest

import mentorship_bond_guard as mb

NOW = "2026-11-24T10:00:00+00:00"
BASE = datetime.fromisoformat(NOW)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def make_lab(**kwargs):
    return mb.MentorshipBond(**kwargs)


def register(lab, player_id, level=50, server="s1"):
    return lab.register_profile(player_id, level=level, server=server)


def start_bond(lab, mentor="m1", apprentice="a1", now=NOW):
    request = lab.apply(mentor, apprentice, now=now)
    return lab.accept(request["bond_id"], apprentice_id=apprentice, now=now)


def test_register_profile_is_idempotent_and_level_updates_are_separate():
    lab = make_lab()
    profile = register(lab, "m1", level=50)
    assert profile == {"player_id": "m1", "level": 50, "server": "s1"}
    assert register(lab, "m1", level=50) == profile

    with pytest.raises(mb.MentorshipError) as exc:
        register(lab, "m1", level=51)
    assert exc.value.code == "MENTOR_PROFILE_CONFLICT"

    with pytest.raises(mb.MentorshipError) as exc:
        register(lab, "m1", level=50, server="s2")
    assert exc.value.code == "MENTOR_SERVER_IMMUTABLE"

    updated = lab.update_level("m1", 55)
    assert updated["level"] == 55
    with pytest.raises(mb.MentorshipError) as exc:
        lab.update_level("ghost", 10)
    assert exc.value.code == "MENTOR_UNKNOWN_PLAYER"

    with pytest.raises(mb.MentorshipError) as exc:
        register(lab, "", level=10)
    assert exc.value.code == "MENTOR_INVALID_PROFILE"
    with pytest.raises(mb.MentorshipError) as exc:
        register(lab, "m2", level=0)
    assert exc.value.code == "MENTOR_INVALID_PROFILE"


def test_application_creates_pending_request_visible_to_both_sides():
    lab = make_lab()
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)

    request = lab.apply("m1", "a1", now=NOW)
    assert request["status"] == "pending"
    assert request["bond_id"] == "mb-1"
    assert request["mentor"] == "m1"
    assert request["apprentice"] == "a1"
    assert request["expires_at"] == at(86400)
    assert lab.pending_for("m1") == [request]
    assert lab.pending_for("a1") == [request]
    assert lab.stats()["applied"] == 1
    assert lab.bond_of("a1") is None


def test_apply_rejects_self_server_and_level_violations():
    lab = make_lab()
    register(lab, "m1", level=50, server="s1")
    register(lab, "a1", level=20, server="s1")
    register(lab, "a2", level=20, server="s2")
    register(lab, "m2", level=39, server="s1")
    register(lab, "a3", level=31, server="s1")

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m1", "m1", now=NOW)
    assert exc.value.code == "MENTOR_SELF_PAIR"

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m1", "a2", now=NOW)
    assert exc.value.code == "MENTOR_SERVER_MISMATCH"

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m2", "a1", now=NOW)
    assert exc.value.code == "MENTOR_LEVEL_INELIGIBLE"

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m1", "a3", now=NOW)
    assert exc.value.code == "MENTOR_LEVEL_INELIGIBLE"
    assert lab.stats()["applied"] == 0


def test_second_mentor_cannot_apply_while_apprentice_has_pending_request():
    lab = make_lab()
    register(lab, "m1", level=50)
    register(lab, "m2", level=50)
    register(lab, "a1", level=20)
    lab.apply("m1", "a1", now=NOW)

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m2", "a1", now=NOW)
    assert exc.value.code == "MENTOR_PENDING_EXISTS"
    assert lab.pending_for("m2") == []
    assert lab.stats()["applied"] == 1


def test_mentor_apprentice_capacity_is_one_accepts_until_free():
    lab = make_lab(max_apprentices=1)
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)
    register(lab, "a2", level=20)
    start_bond(lab, "m1", "a1")

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m1", "a2", now=NOW)
    assert exc.value.code == "MENTOR_CAPACITY"


def test_bound_players_cannot_enter_another_application():
    lab = make_lab()
    register(lab, "m1", level=50)
    register(lab, "m2", level=50)
    register(lab, "a1", level=20)
    register(lab, "a2", level=20)
    start_bond(lab, "m1", "a1")

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m1", "a2", now=NOW)
    assert exc.value.code == "MENTOR_ALREADY_BOUND"

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m2", "a1", now=NOW)
    assert exc.value.code == "MENTOR_ALREADY_BOUND"


def test_only_named_apprentice_can_accept_and_request_is_consumed():
    lab = make_lab()
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)
    register(lab, "a2", level=20)
    request = lab.apply("m1", "a1", now=NOW)

    with pytest.raises(mb.MentorshipError) as exc:
        lab.accept(request["bond_id"], apprentice_id="a2", now=NOW)
    assert exc.value.code == "MENTOR_NOT_APPRENTICE"

    with pytest.raises(mb.MentorshipError) as exc:
        lab.accept("mb-404", apprentice_id="a1", now=NOW)
    assert exc.value.code == "MENTOR_UNKNOWN_REQUEST"

    bond = lab.accept(request["bond_id"], apprentice_id="a1", now=NOW)
    assert bond["status"] == "active"
    assert lab.bond_of("m1")["bond_id"] == bond["bond_id"]
    assert lab.bond_of("a1")["bond_id"] == bond["bond_id"]
    assert lab.pending_for("m1") == []
    assert lab.stats()["accepted"] == 1

    with pytest.raises(mb.MentorshipError) as exc:
        lab.accept(request["bond_id"], apprentice_id="a1", now=NOW)
    assert exc.value.code == "MENTOR_UNKNOWN_REQUEST"


def test_request_expires_exactly_at_ttl_boundary():
    lab = make_lab(request_ttl=60)
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)
    request = lab.apply("m1", "a1", now=NOW)

    with pytest.raises(mb.MentorshipError) as exc:
        lab.accept(request["bond_id"], apprentice_id="a1", now=at(60))
    assert exc.value.code == "MENTOR_REQUEST_EXPIRED"
    assert lab.stats()["expired"] == 1
    assert lab.pending_for("a1", now=at(60)) == []

    with pytest.raises(mb.MentorshipError) as exc:
        lab.accept(request["bond_id"], apprentice_id="a1", now=at(61))
    assert exc.value.code == "MENTOR_UNKNOWN_REQUEST"


def test_decline_requires_participant_and_removes_request():
    lab = make_lab()
    register(lab, "m1", level=50)
    register(lab, "m2", level=50)
    register(lab, "a1", level=20)
    register(lab, "a2", level=20)
    first = lab.apply("m1", "a1", now=NOW)
    lab.apply("m2", "a2", now=NOW)

    with pytest.raises(mb.MentorshipError) as exc:
        lab.decline(first["bond_id"], player_id="m2", now=NOW)
    assert exc.value.code == "MENTOR_NOT_PARTICIPANT"

    result = lab.decline(first["bond_id"], player_id="m1", now=NOW)
    assert result["status"] == "declined"
    assert lab.stats()["declined"] == 1
    assert lab.pending_for("m1") == []
    with pytest.raises(mb.MentorshipError) as exc:
        lab.accept(first["bond_id"], apprentice_id="a1", now=NOW)
    assert exc.value.code == "MENTOR_UNKNOWN_REQUEST"


def test_progress_records_each_lesson_once_and_validates_points():
    lab = make_lab()
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)
    bond = start_bond(lab, "m1", "a1")

    row = lab.progress(bond["bond_id"], lesson="basics", points=30, now=NOW)
    assert row["lessons"] == ["basics"]
    assert row["bond_points"] == 30
    assert row["mentor_today"] == 30

    with pytest.raises(mb.MentorshipError) as exc:
        lab.progress(bond["bond_id"], lesson="basics", points=10, now=NOW)
    assert exc.value.code == "MENTOR_LESSON_DONE"

    for bad in (0, 101, True):
        with pytest.raises(mb.MentorshipError) as exc:
            lab.progress(bond["bond_id"], lesson="advanced", points=bad, now=NOW)
        assert exc.value.code == "MENTOR_INVALID_POINTS"

    with pytest.raises(mb.MentorshipError) as exc:
        lab.progress(bond["bond_id"], lesson="", points=10, now=NOW)
    assert exc.value.code == "MENTOR_INVALID_LESSON"

    row = lab.progress(bond["bond_id"], lesson="advanced", points=20, now=NOW)
    assert row["lessons"] == ["basics", "advanced"]
    assert row["bond_points"] == 50


def test_daily_mentor_points_cap_and_utc_reset():
    lab = make_lab(daily_point_cap=50)
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)
    bond = start_bond(lab, "m1", "a1")
    lab.progress(bond["bond_id"], lesson="first", points=40, now=NOW)

    with pytest.raises(mb.MentorshipError) as exc:
        lab.progress(bond["bond_id"], lesson="second", points=20, now=NOW)
    assert exc.value.code == "MENTOR_DAILY_CAP"
    current = lab.bond_of("a1")
    assert current["points"] == 40
    assert current["lessons"] == ["first"]

    row = lab.progress(bond["bond_id"], lesson="second", points=20, now=at(86400))
    assert row["mentor_today"] == 20
    assert row["bond_points"] == 60


def test_graduation_needs_lessons_and_level_then_closes_bond():
    lab = make_lab()
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)
    bond = start_bond(lab, "m1", "a1")

    with pytest.raises(mb.MentorshipError) as exc:
        lab.graduate(bond["bond_id"], now=NOW)
    assert exc.value.code == "MENTOR_NOT_READY"

    for index in range(3):
        lab.progress(bond["bond_id"], lesson=f"lesson-{index}", points=10, now=NOW)

    with pytest.raises(mb.MentorshipError) as exc:
        lab.graduate(bond["bond_id"], now=NOW)
    assert exc.value.code == "MENTOR_NOT_READY"

    lab.update_level("a1", 41)
    result = lab.graduate(bond["bond_id"], now=at(100))
    assert result["status"] == "graduated"
    assert result["lessons"] == ["lesson-0", "lesson-1", "lesson-2"]
    assert result["points"] == 30
    assert lab.bond_of("a1") is None
    assert lab.bond_of("m1") is None
    assert lab.stats()["graduated"] == 1

    with pytest.raises(mb.MentorshipError) as exc:
        lab.graduate(bond["bond_id"], now=at(101))
    assert exc.value.code == "MENTOR_BOND_CLOSED"


def test_dissolve_blocks_reapplication_until_cooldown_ends():
    lab = make_lab(cooldown_seconds=3600)
    register(lab, "m1", level=50)
    register(lab, "a1", level=20)
    register(lab, "a2", level=20)
    bond = start_bond(lab, "m1", "a1")

    with pytest.raises(mb.MentorshipError) as exc:
        lab.dissolve(bond["bond_id"], actor="stranger", now=NOW)
    assert exc.value.code == "MENTOR_NOT_PARTICIPANT"

    result = lab.dissolve(bond["bond_id"], actor="a1", now=NOW)
    assert result["status"] == "dissolved"
    assert result["cooldown_until"] == at(3600)
    assert lab.bond_of("a1") is None
    assert lab.stats()["dissolved"] == 1

    with pytest.raises(mb.MentorshipError) as exc:
        lab.apply("m1", "a2", now=at(3599))
    assert exc.value.code == "MENTOR_COOLDOWN"

    request = lab.apply("m1", "a2", now=at(3600))
    assert request["status"] == "pending"


def test_snapshot_restore_carries_bonds_requests_daily_and_cooldowns():
    lab = make_lab()
    for player in ("m1", "m2", "m3"):
        register(lab, player, level=50)
    for player in ("a1", "a2", "a3"):
        register(lab, player, level=20)
    first = start_bond(lab, "m1", "a1")
    lab.progress(first["bond_id"], lesson="l1", points=30, now=NOW)
    lab.progress(first["bond_id"], lesson="l2", points=20, now=NOW)
    second = start_bond(lab, "m2", "a2")
    lab.dissolve(second["bond_id"], actor="m2", now=NOW)
    lab.apply("m3", "a3", now=NOW)
    state = lab.snapshot()

    lab.progress(first["bond_id"], lesson="l3", points=10, now=at(10))
    lab.accept("mb-3", apprentice_id="a3", now=at(10))

    restored = mb.MentorshipBond()
    restored.restore(state)
    active = restored.bond_of("a1")
    assert active["points"] == 50
    assert active["lessons"] == ["l1", "l2"]
    assert restored.bond_of("m1")["bond_id"] == first["bond_id"]
    assert restored.bond_of("a2") is None
    assert len(restored.pending_for("m3", now=at(10))) == 1
    assert restored.stats() == {"applied": 3, "accepted": 2, "declined": 0,
                               "graduated": 0, "dissolved": 1, "expired": 0}

    with pytest.raises(mb.MentorshipError) as exc:
        restored.apply("m2", "a2", now=at(10))
    assert exc.value.code == "MENTOR_COOLDOWN"

    valid = copy.deepcopy(state)
    version_two = copy.deepcopy(valid)
    version_two["version"] = 2
    bad_profiles = copy.deepcopy(valid)
    bad_profiles["profiles"] = "nope"
    bad_bonds = copy.deepcopy(valid)
    bad_bonds["bonds"] = "nope"
    bad_requests = copy.deepcopy(valid)
    bad_requests["requests"] = "nope"
    bad_stats = copy.deepcopy(valid)
    bad_stats["stats"] = "nope"

    guard = mb.MentorshipBond()
    guard.restore(state)
    for bad in (None, {}, version_two, bad_profiles, bad_bonds,
                bad_requests, bad_stats):
        with pytest.raises(mb.MentorshipError) as exc:
            guard.restore(bad)
        assert exc.value.code == "MENTOR_SNAPSHOT_INVALID"
    assert guard.bond_of("a1")["points"] == 50
    assert guard.stats()["applied"] == 3


def test_concurrent_applications_for_one_apprentice_yield_one_request():
    lab = make_lab()
    register(lab, "a1", level=20)
    for index in range(8):
        register(lab, f"m{index}", level=50)
    barrier = threading.Barrier(8)
    accepted = []
    rejected = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            row = lab.apply(f"m{index}", "a1", now=NOW)
        except Exception as exc:
            code = getattr(exc, "code", repr(exc))
            with lock:
                rejected.append(code)
            return
        with lock:
            accepted.append(row)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert rejected == ["MENTOR_PENDING_EXISTS"] * 7
    assert lab.stats()["applied"] == 1
    assert len(lab.pending_for("a1")) == 1
