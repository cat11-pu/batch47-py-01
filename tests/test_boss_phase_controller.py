import threading

import pytest

import boss_phase_controller as bp


PHASES = [
    {"phase": 1, "hp_threshold": 100,
     "abilities": [{"ability_id": "slam"},
                   {"ability_id": "roar", "cooldown_ms": 8000}]},
    {"phase": 2, "hp_threshold": 60,
     "abilities": [{"ability_id": "cleave", "cooldown_ms": 4000}]},
    {"phase": 3, "hp_threshold": 30,
     "abilities": [{"ability_id": "blast", "cooldown_ms": 0}]},
]


def boss(**kwargs):
    controller = bp.BossPhaseController(**kwargs)
    controller.define_phases([{**phase, "abilities": list(phase["abilities"])}
                              for phase in PHASES])
    return controller


def engaged(**kwargs):
    controller = boss(**kwargs)
    controller.engage(now_ms=1000, max_hp=1000)
    return controller


def test_define_phases_is_idempotent_and_validates_the_ladder():
    controller = bp.BossPhaseController()
    first = controller.define_phases(PHASES)
    assert first["created"] is True
    assert [row["phase"] for row in first["phases"]] == [1, 2, 3]
    assert controller.define_phases(PHASES)["created"] is False
    with pytest.raises(bp.PhaseError) as exc:
        controller.define_phases([
            {"phase": 1, "hp_threshold": 100, "abilities": []},
            {"phase": 2, "hp_threshold": 50, "abilities": []},
        ])
    assert exc.value.code == "PHASE_CONFLICT"

    bad_ladders = [
        [],
        [{"phase": 1, "hp_threshold": 90, "abilities": []}],
        [{"phase": 1, "hp_threshold": 100, "abilities": []},
         {"phase": 2, "hp_threshold": 60, "abilities": []},
         {"phase": 3, "hp_threshold": 60, "abilities": []}],
        [{"phase": 1, "hp_threshold": 100, "abilities": []},
         {"phase": 3, "hp_threshold": 50, "abilities": []}],
        [{"phase": 1, "hp_threshold": 100,
          "abilities": [{"ability_id": "slam"}]},
         {"phase": 2, "hp_threshold": 50,
          "abilities": [{"ability_id": "slam"}]}],
        [{"phase": 1, "hp_threshold": 100,
          "abilities": [{"ability_id": ""}]}],
        [{"phase": 1, "hp_threshold": 100,
          "abilities": [{"ability_id": "slam", "cooldown_ms": -1}]}],
        [{"phase": 1, "hp_threshold": 100, "abilities": "slam"}],
        [{"phase": 1, "hp_threshold": 0, "abilities": []}],
    ]
    for ladder in bad_ladders:
        with pytest.raises(bp.PhaseError) as exc:
            bp.BossPhaseController().define_phases(ladder)
        assert exc.value.code == "PHASE_BAD_DEFINITION"


def test_phases_cannot_be_redefined_after_engagement():
    controller = engaged()
    with pytest.raises(bp.PhaseError) as exc:
        controller.define_phases(PHASES)
    assert exc.value.code == "PHASE_LOCKED"
    assert controller.status(now_ms=1000)["phase"] == 1


def test_engagement_requires_phases_and_happens_only_once():
    empty = bp.BossPhaseController()
    with pytest.raises(bp.PhaseError) as exc:
        empty.engage(now_ms=1000, max_hp=1000)
    assert exc.value.code == "PHASE_NOT_READY"
    with pytest.raises(bp.PhaseError) as exc:
        empty.status(now_ms=1000)
    assert exc.value.code == "PHASE_NOT_READY"

    controller = boss()
    first = controller.engage(now_ms=1000, max_hp=1000)
    assert first["state"] == "active"
    assert first["hp"] == 1000
    assert first["hp_percent"] == 100
    assert first["phase"] == 1
    assert first["elapsed_ms"] == 0
    assert first["enrage"] == "none"
    assert first["casts"] == 0
    with pytest.raises(bp.PhaseError) as exc:
        controller.engage(now_ms=1100, max_hp=500)
    assert exc.value.code == "PHASE_ALREADY_ENGAGED"
    with pytest.raises(bp.PhaseError) as exc:
        boss().engage(now_ms=1000, max_hp=0)
    assert exc.value.code == "PHASE_BAD_AMOUNT"


def test_damage_crosses_phases_forward_only():
    controller = engaged()
    first = controller.damage(410, now_ms=1000)
    assert (first["hp"], first["hp_percent"], first["phase"]) == (590, 59, 2)
    assert first["transitions"] == [
        {"from": 1, "to": 2, "hp_percent": 59, "at_ms": 1000},
    ]
    second = controller.damage(300, now_ms=1500)
    assert (second["hp"], second["phase"]) == (290, 3)
    assert second["transitions"] == [
        {"from": 2, "to": 3, "hp_percent": 29, "at_ms": 1500},
    ]
    third = controller.damage(10, now_ms=2000)
    assert third["phase"] == 3
    assert third["transitions"] == []
    assert controller.stats()["damages"] == 3
    assert controller.stats()["phase_changes"] == 2

    leaper = engaged()
    jumped = leaper.damage(750, now_ms=1000)
    assert jumped["hp"] == 250
    assert jumped["phase"] == 3
    assert jumped["transitions"] == [
        {"from": 1, "to": 2, "hp_percent": 25, "at_ms": 1000},
        {"from": 2, "to": 3, "hp_percent": 25, "at_ms": 1000},
    ]


def test_casting_locks_old_phase_abilities_and_unlocks_new_ones():
    controller = engaged()
    with pytest.raises(bp.PhaseError) as exc:
        controller.cast("cleave", now_ms=1000)
    assert exc.value.code == "PHASE_ABILITY_LOCKED"
    cast = controller.cast("slam", now_ms=1000)
    assert cast == {"ability_id": "slam", "phase": 1, "cast_at_ms": 1000,
                    "ready_at_ms": 6000, "casts": 1}

    controller.damage(410, now_ms=2000)
    with pytest.raises(bp.PhaseError) as exc:
        controller.cast("slam", now_ms=2000)
    assert exc.value.code == "PHASE_ABILITY_LOCKED"
    cleave = controller.cast("cleave", now_ms=2000)
    assert cleave["ready_at_ms"] == 6000
    assert cleave["casts"] == 1
    assert controller.stats()["casts"] == 2


def test_cast_cooldowns_use_per_ability_override_and_boundary():
    controller = engaged()
    controller.cast("slam", now_ms=1000)
    with pytest.raises(bp.PhaseError) as exc:
        controller.cast("slam", now_ms=5999)
    assert exc.value.code == "PHASE_COOLDOWN"
    assert exc.value.detail == {"ability_id": "slam", "ready_at_ms": 6000}
    again = controller.cast("slam", now_ms=6000)
    assert again["ready_at_ms"] == 11000
    assert again["casts"] == 2

    roar = controller.cast("roar", now_ms=6000)
    assert roar["ready_at_ms"] == 14000
    with pytest.raises(bp.PhaseError) as exc:
        controller.cast("ghost", now_ms=6000)
    assert exc.value.code == "PHASE_UNKNOWN_ABILITY"
    assert controller.stats()["casts"] == 3


def test_available_abilities_follow_the_active_phase_and_cooldowns():
    controller = engaged()
    assert controller.available_abilities(now_ms=1000) == ["roar", "slam"]
    controller.cast("slam", now_ms=1000)
    assert controller.available_abilities(now_ms=1000) == ["roar"]
    controller.cast("roar", now_ms=1000)
    assert controller.available_abilities(now_ms=1000) == []

    controller.damage(410, now_ms=2000)
    assert controller.available_abilities(now_ms=2000) == ["cleave"]
    controller.damage(300, now_ms=3000)
    assert controller.available_abilities(now_ms=3000) == ["blast"]


def test_soft_and_hard_enrage_boundaries_are_inclusive():
    controller = engaged()
    assert controller.status(now_ms=600999)["enrage"] == "none"
    assert controller.status(now_ms=601000)["enrage"] == "soft"
    assert controller.status(now_ms=900999)["enrage"] == "soft"
    assert controller.status(now_ms=901000)["enrage"] == "hard"
    assert controller.status(now_ms=901000)["elapsed_ms"] == 900000


def test_zero_hp_finishes_the_encounter_and_blocks_further_actions():
    controller = engaged()
    result = controller.damage(1000, now_ms=1000)
    assert result["hp"] == 0
    assert result["hp_percent"] == 0
    assert result["phase"] == 3
    assert result["state"] == "dead"
    assert result["lethal"] is True
    assert len(result["transitions"]) == 2
    assert controller.stats()["wipes"] == 1

    dead = controller.status(now_ms=1000)
    assert dead["state"] == "dead"
    assert dead["phase"] == 3
    with pytest.raises(bp.PhaseError) as exc:
        controller.damage(1, now_ms=1100)
    assert exc.value.code == "PHASE_ENCOUNTER_OVER"
    with pytest.raises(bp.PhaseError) as exc:
        controller.cast("blast", now_ms=1100)
    assert exc.value.code == "PHASE_ENCOUNTER_OVER"
    assert controller.history(1)[0]["kind"] == "damage"


def test_set_hp_rejects_increases_and_delegates_lower_values():
    controller = engaged()
    same = controller.set_hp(1000, now_ms=1000)
    assert same["damage"] == 0
    assert same["transitions"] == []
    assert controller.stats()["damages"] == 0

    lowered = controller.set_hp(600, now_ms=1500)
    assert lowered["hp"] == 600
    assert lowered["hp_percent"] == 60
    assert lowered["phase"] == 2
    assert lowered["transitions"] == [
        {"from": 1, "to": 2, "hp_percent": 60, "at_ms": 1500},
    ]
    with pytest.raises(bp.PhaseError) as exc:
        controller.set_hp(700, now_ms=1600)
    assert exc.value.code == "PHASE_HP_INCREASE"
    assert controller.status(now_ms=1600)["hp"] == 600
    for bad in (-1, 1001):
        with pytest.raises(bp.PhaseError) as exc:
            controller.set_hp(bad, now_ms=1600)
        assert exc.value.code == "PHASE_BAD_AMOUNT"


def test_time_reversal_is_rejected_without_mutating_the_encounter():
    controller = engaged()
    controller.damage(100, now_ms=2000)
    assert controller.status(now_ms=2000)["hp"] == 900

    with pytest.raises(bp.PhaseError) as exc:
        controller.status(now_ms=1999)
    assert exc.value.code == "PHASE_TIME_REVERSED"
    with pytest.raises(bp.PhaseError) as exc:
        controller.damage(10, now_ms=1999)
    assert exc.value.code == "PHASE_TIME_REVERSED"
    with pytest.raises(bp.PhaseError) as exc:
        controller.cast("slam", now_ms=1999)
    assert exc.value.code == "PHASE_TIME_REVERSED"
    assert controller.status(now_ms=2000)["hp"] == 900
    assert controller.stats()["damages"] == 1

    controller.status(now_ms=2100)
    with pytest.raises(bp.PhaseError) as exc:
        controller.status(now_ms=2050)
    assert exc.value.code == "PHASE_TIME_REVERSED"


def test_reset_clears_the_encounter_and_bounds_history():
    controller = engaged(max_events=3)
    controller.damage(100, now_ms=2000)
    controller.cast("slam", now_ms=2000)
    reset = controller.reset_encounter(now_ms=3000)
    assert reset == {"reset": True, "at_ms": 3000}
    assert controller.stats()["resets"] == 1
    with pytest.raises(bp.PhaseError) as exc:
        controller.status(now_ms=3000)
    assert exc.value.code == "PHASE_NOT_READY"
    assert [row["kind"] for row in controller.history(10)] == [
        "reset", "cast", "damage",
    ]

    controller.engage(now_ms=4000, max_hp=1000)
    assert [row["kind"] for row in controller.history(10)] == [
        "engage", "reset", "cast",
    ]
    assert controller.status(now_ms=4000)["hp"] == 1000


def test_snapshot_restore_carries_phases_encounter_and_cooldowns():
    controller = engaged()
    controller.damage(410, now_ms=2000)
    controller.cast("cleave", now_ms=2000)
    state = controller.snapshot()
    assert state["version"] == 1

    tampered = controller.snapshot()
    tampered["encounter"]["hp"] = 1
    tampered["encounter"]["ready_at"]["cleave"] = 1
    assert controller.status(now_ms=2000)["hp"] == 590

    controller.damage(300, now_ms=3000)
    controller.cast("blast", now_ms=3000)
    controller.restore(state)
    status = controller.status(now_ms=2000)
    assert (status["hp"], status["phase"]) == (590, 2)
    with pytest.raises(bp.PhaseError) as exc:
        controller.cast("cleave", now_ms=2000)
    assert exc.value.code == "PHASE_COOLDOWN"
    assert controller.cast("cleave", now_ms=6000)["casts"] == 2
    assert controller.stats() == {"damages": 1, "phase_changes": 1,
                                  "casts": 2, "resets": 0, "wipes": 0}

    portable = bp.BossPhaseController()
    portable.restore(state)
    assert portable.status(now_ms=2000)["hp"] == 590
    assert portable.available_abilities(now_ms=2000) == []
    with pytest.raises(bp.PhaseError) as exc:
        portable.cast("blast", now_ms=2000)
    assert exc.value.code == "PHASE_ABILITY_LOCKED"


def test_corrupt_snapshot_is_rejected_without_touching_the_encounter():
    controller = engaged()
    controller.damage(410, now_ms=2000)
    good = controller.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, phases="nope"),
        dict(good, encounter=[]),
        dict(good, stats={"damages": -1, "phase_changes": 0, "casts": 0,
                          "resets": 0, "wipes": 0}),
    ]
    for bad in bad_states:
        with pytest.raises(bp.PhaseError) as exc:
            controller.restore(bad)
        assert exc.value.code == "PHASE_SNAPSHOT_INVALID"
    assert controller.status(now_ms=2000)["hp"] == 590
    assert controller.damage(10, now_ms=2100)["hp"] == 580


def test_concurrent_casts_of_one_ability_land_exactly_once():
    controller = engaged()
    barrier = threading.Barrier(8)
    results = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = controller.cast("slam", now_ms=1000)
        except Exception as exc:
            row = {"status": "error", "code": getattr(exc, "code", repr(exc))}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for row in results if row.get("status") != "error") == 1
    assert sorted(row.get("code") for row in results
                  if row.get("status") == "error") == ["PHASE_COOLDOWN"] * 7
    assert controller.stats()["casts"] == 1
    assert controller.available_abilities(now_ms=1000) == ["roar"]
