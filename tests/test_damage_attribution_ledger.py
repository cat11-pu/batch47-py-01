import threading

import pytest

import damage_attribution_ledger as dl


ZERO_DEALT = {"hp_damage": 0, "overkill": 0, "resisted": 0,
              "shield_absorbed": 0, "hits": 0, "kills": 0,
              "lifesteal": 0, "reflect_taken": 0}
ZERO_TAKEN = {"hp_damage": 0, "overkill": 0, "resisted": 0,
              "shield_absorbed": 0, "hits": 0}


def test_register_combatant_rejects_duplicates_bad_values_and_overflow():
    engine = dl.DamageAttributionLedger(max_combatants=1)
    first = engine.register_combatant(
        "tank", hp=1000, shield=100,
        resistances={"physical": 25, "fire": 10},
        reflect_percent=20, lifesteal_percent=5,
    )
    assert first == {"combatant_id": "tank", "hp": 1000, "max_hp": 1000,
                     "shield": 100, "resistances": {"fire": 10, "physical": 25},
                     "reflect_percent": 20, "lifesteal_percent": 5}
    with pytest.raises(dl.DamageError) as exc:
        engine.register_combatant("tank", hp=1000)
    assert exc.value.code == "DAMAGE_DUPLICATE_COMBATANT"
    with pytest.raises(dl.DamageError) as exc:
        engine.register_combatant("other", hp=10)
    assert exc.value.code == "DAMAGE_COMBATANT_LIMIT"

    bad_configs = [
        dict(hp=0),
        dict(hp=-5),
        dict(hp=10, shield=-1),
        dict(hp=10, reflect_percent=101),
        dict(hp=10, lifesteal_percent=-1),
        dict(hp=10, resistances={"physical": 91}),
        dict(hp=10, resistances={"": 10}),
    ]
    for payload in bad_configs:
        with pytest.raises(dl.DamageError) as exc:
            dl.DamageAttributionLedger().register_combatant("x", **payload)
        assert exc.value.code == "DAMAGE_BAD_COMBATANT"


def test_resistance_and_shield_updates_return_current_values():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("tank", hp=1000, resistances={"physical": 10})
    updated = engine.set_resistance("tank", element="physical", percent=40)
    assert updated == {"combatant_id": "tank", "element": "physical",
                       "percent": 40, "resistances": {"physical": 40}}
    assert engine.grant_shield("tank", 50) == {"combatant_id": "tank",
                                               "granted": 50, "shield": 50}
    assert engine.grant_shield("tank", 25)["shield"] == 75

    with pytest.raises(dl.DamageError) as exc:
        engine.set_resistance("tank", element="", percent=10)
    assert exc.value.code == "DAMAGE_BAD_AMOUNT"
    with pytest.raises(dl.DamageError) as exc:
        engine.set_resistance("tank", element="fire", percent=91)
    assert exc.value.code == "DAMAGE_BAD_AMOUNT"
    with pytest.raises(dl.DamageError) as exc:
        engine.grant_shield("tank", 0)
    assert exc.value.code == "DAMAGE_BAD_AMOUNT"
    with pytest.raises(dl.DamageError) as exc:
        engine.set_resistance("ghost", element="fire", percent=10)
    assert exc.value.code == "DAMAGE_UNKNOWN_COMBATANT"


def test_resistance_then_shield_absorption_on_a_normal_hit():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=1000, shield=300,
                              resistances={"physical": 25})
    result = engine.resolve_hit("attacker", "tank", raw=200)

    assert result["damage"] == 200
    assert result["resisted"] == 50
    assert result["shield_absorbed"] == 150
    assert result["hp_damage"] == 0
    assert result["overkill"] == 0
    assert result["defender_hp"] == 1000
    assert result["defender_shield"] == 150
    assert result["lethal"] is False
    assert result["duplicate"] is False
    assert engine.stats()["hits"] == 1
    assert engine.stats()["resisted"] == 50
    assert engine.stats()["shield_absorbed"] == 150
    assert engine.stats()["hp_damage"] == 0


def test_critical_hits_use_the_150_percent_multiplier_before_resistance():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=1000, resistances={"physical": 25})
    result = engine.resolve_hit("attacker", "tank", raw=100, crit=True)
    assert result["crit"] is True
    assert result["damage"] == 150
    assert result["resisted"] == 37
    assert result["hp_damage"] == 113
    assert result["defender_hp"] == 887
    assert result["overkill"] == 0


def test_overkill_is_capped_by_remaining_hp_and_dead_targets_cannot_be_healed():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=50)
    result = engine.resolve_hit("attacker", "tank", raw=200)
    assert result["hp_damage"] == 50
    assert result["overkill"] == 150
    assert result["defender_hp"] == 0
    assert result["lethal"] is True
    assert engine.damage_by_attacker("attacker")["overkill"] == 150
    assert engine.damage_by_attacker("attacker")["kills"] == 1

    with pytest.raises(dl.DamageError) as exc:
        engine.heal("tank", 10)
    assert exc.value.code == "DAMAGE_TARGET_DEAD"


def test_lifesteal_heals_up_to_max_hp_and_reports_overheal():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("vampire", hp=1000, lifesteal_percent=100)
    engine.register_combatant("tank", hp=1000)
    engine.register_combatant("bruiser", hp=1000)
    engine.resolve_hit("bruiser", "vampire", raw=500)
    assert engine.combatant_report("vampire")["hp"] == 500

    first = engine.resolve_hit("vampire", "tank", raw=200)
    assert first["hp_damage"] == 200
    assert first["lifesteal"] == 200
    assert first["healed"] == 200
    assert first["overheal"] == 0
    assert first["attacker_hp"] == 700
    second = engine.resolve_hit("vampire", "tank", raw=200)
    assert second["healed"] == 200
    third = engine.resolve_hit("vampire", "tank", raw=200)
    assert third["lifesteal"] == 200
    assert third["healed"] == 100
    assert third["overheal"] == 100
    assert third["attacker_hp"] == 1000
    assert engine.stats()["overheal"] == 100
    assert engine.damage_by_attacker("vampire")["lifesteal"] == 500
    assert engine.combatant_report("tank")["hp"] == 400


def test_reflect_damages_the_attacker_after_lifesteal():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("brawler", hp=300)
    engine.register_combatant("spiky", hp=1000, reflect_percent=50)
    first = engine.resolve_hit("brawler", "spiky", raw=200)
    assert first["hp_damage"] == 200
    assert first["reflect"] == 100
    assert first["reflect_damage"] == 100
    assert first["reflect_overkill"] == 0
    assert first["attacker_hp"] == 200

    second = engine.resolve_hit("brawler", "spiky", raw=2000)
    assert second["hp_damage"] == 800
    assert second["overkill"] == 1200
    assert second["reflect"] == 400
    assert second["reflect_damage"] == 200
    assert second["reflect_overkill"] == 200
    assert second["attacker_hp"] == 0
    assert second["lethal"] is True
    report = engine.combatant_report("brawler")
    assert report["damage_dealt"]["reflect_taken"] == 300


def test_hit_id_is_idempotent_and_cache_eviction_is_counted():
    engine = dl.DamageAttributionLedger(hit_cache=1)
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=1000)
    first = engine.resolve_hit("attacker", "tank", raw=100, hit_id="h1")
    assert first["duplicate"] is False
    repeat = engine.resolve_hit("attacker", "tank", raw=100, hit_id="h1")
    assert repeat["duplicate"] is True
    assert repeat["hp_damage"] == 100
    assert engine.combatant_report("tank")["hp"] == 900
    assert engine.stats()["hits"] == 1

    with pytest.raises(dl.DamageError) as exc:
        engine.resolve_hit("attacker", "tank", raw=101, hit_id="h1")
    assert exc.value.code == "DAMAGE_HIT_CONFLICT"
    assert exc.value.detail == {"hit_id": "h1"}

    engine.resolve_hit("attacker", "tank", raw=50, hit_id="h2")
    assert engine.stats()["hit_cache_evicted"] == 1
    again = engine.resolve_hit("attacker", "tank", raw=50, hit_id="h2")
    assert again["duplicate"] is True
    assert engine.stats()["hits"] == 2


def test_unknown_combatants_and_bad_arguments_are_rejected():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=500)
    for attacker, defender in (("ghost", "tank"), ("attacker", "ghost")):
        with pytest.raises(dl.DamageError) as exc:
            engine.resolve_hit(attacker, defender, raw=10)
        assert exc.value.code == "DAMAGE_UNKNOWN_COMBATANT"
    for raw in (0, -1, "10"):
        with pytest.raises(dl.DamageError) as exc:
            engine.resolve_hit("attacker", "tank", raw=raw)
        assert exc.value.code == "DAMAGE_BAD_AMOUNT"
    with pytest.raises(dl.DamageError) as exc:
        engine.resolve_hit("attacker", "tank", raw=10, element="")
    assert exc.value.code == "DAMAGE_BAD_AMOUNT"
    with pytest.raises(dl.DamageError) as exc:
        engine.heal("ghost", 10)
    assert exc.value.code == "DAMAGE_UNKNOWN_COMBATANT"
    with pytest.raises(dl.DamageError) as exc:
        engine.leaderboard(0)
    assert exc.value.code == "DAMAGE_BAD_LIMIT"
    with pytest.raises(dl.DamageError) as exc:
        engine.recent_hits(0)
    assert exc.value.code == "DAMAGE_BAD_LIMIT"


def test_heal_reports_applied_and_overheal():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=1000)
    engine.resolve_hit("attacker", "tank", raw=600)
    first = engine.heal("tank", 300)
    assert first == {"combatant_id": "tank", "healed": 300, "overheal": 0,
                     "hp": 700, "max_hp": 1000}
    second = engine.heal("tank", 800)
    assert second["healed"] == 300
    assert second["overheal"] == 500
    assert second["hp"] == 1000
    assert engine.stats()["heals"] == 2
    assert engine.stats()["overheal"] == 500


def test_damage_attribution_splits_by_attacker_and_element():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("mage", hp=500)
    engine.register_combatant("one", hp=1000)
    engine.register_combatant("two", hp=1000, resistances={"fire": 50})

    engine.resolve_hit("attacker", "one", raw=100)
    engine.resolve_hit("mage", "two", raw=300, element="fire", crit=True)
    engine.resolve_hit("attacker", "one", raw=50)

    assert engine.damage_by_attacker("attacker") == {
        "hp_damage": 150, "overkill": 0, "resisted": 0,
        "shield_absorbed": 0, "hits": 2, "kills": 0,
        "lifesteal": 0, "reflect_taken": 0,
    }
    assert engine.damage_by_attacker("mage")["hp_damage"] == 225
    assert engine.damage_by_attacker("mage")["resisted"] == 225
    assert engine.damage_by_element() == {
        "physical": {"hp_damage": 150, "hits": 2},
        "fire": {"hp_damage": 225, "hits": 1},
    }


def test_combatant_report_and_leaderboard_use_aggregated_damage():
    engine = dl.DamageAttributionLedger()
    for name in ("alpha", "bravo", "charlie"):
        engine.register_combatant(name, hp=500)
    engine.register_combatant("tank", hp=100000)
    engine.resolve_hit("alpha", "tank", raw=300)
    engine.resolve_hit("bravo", "tank", raw=500)
    engine.resolve_hit("charlie", "tank", raw=500)

    assert engine.leaderboard(2) == [
        {"attacker_id": "bravo", "hp_damage": 500, "kills": 0, "hits": 1},
        {"attacker_id": "charlie", "hp_damage": 500, "kills": 0, "hits": 1},
    ]
    report = engine.combatant_report("alpha")
    assert report["combatant_id"] == "alpha"
    assert report["hp"] == 500
    assert report["alive"] is True
    assert report["damage_dealt"] == dict(ZERO_DEALT, hp_damage=300, hits=1)
    assert report["damage_taken"] == dict(ZERO_TAKEN)

    tank = engine.combatant_report("tank")
    assert tank["damage_taken"] == {"hp_damage": 1300, "overkill": 0,
                                    "resisted": 0, "shield_absorbed": 0,
                                    "hits": 3}
    assert engine.damage_by_attacker("charlie")["hp_damage"] == 500


def test_event_retention_keeps_only_the_newest_hits():
    engine = dl.DamageAttributionLedger(max_events=2)
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=10000)
    for index, amount in enumerate((10, 20, 30), start=1):
        engine.resolve_hit("attacker", "tank", raw=amount, hit_id=f"h{index}")

    recent = engine.recent_hits(10)
    assert [row["hit_id"] for row in recent] == ["h3", "h2"]
    assert engine.recent_hits(1) == [recent[0]]
    assert engine.stats()["events_evicted"] == 1
    assert engine.stats()["hits"] == 3


def test_snapshot_restore_recovers_combatants_hits_and_aggregates():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=1000)
    engine.resolve_hit("attacker", "tank", raw=200, hit_id="h1")
    engine.heal("tank", 100)
    state = engine.snapshot()
    assert state["version"] == 1

    tampered = engine.snapshot()
    tampered["combatants"]["tank"]["hp"] = 1
    tampered["events"][0]["hp_damage"] = 9999
    assert engine.combatant_report("tank")["hp"] == 900

    engine.resolve_hit("attacker", "tank", raw=300, hit_id="h2")
    assert engine.combatant_report("tank")["hp"] == 600
    engine.restore(state)
    assert engine.combatant_report("tank")["hp"] == 900
    assert engine.stats()["hits"] == 1
    assert [row["hit_id"] for row in engine.recent_hits(10)] == ["h1"]
    replay = engine.resolve_hit("attacker", "tank", raw=200, hit_id="h1")
    assert replay["duplicate"] is True
    assert engine.combatant_report("tank")["hp"] == 900
    assert engine.damage_by_attacker("attacker")["hp_damage"] == 200

    bad_states = [
        None,
        {},
        dict(state, version=2),
        dict(state, combatants=[]),
        dict(state, dealt="nope"),
        dict(state, stats={"hits": -1, "hp_damage": 0, "overkill": 0,
                           "shield_absorbed": 0, "resisted": 0, "heals": 0,
                           "overheal": 0, "events_evicted": 0,
                           "hit_cache_evicted": 0}),
    ]
    for bad in bad_states:
        with pytest.raises(dl.DamageError) as exc:
            engine.restore(bad)
        assert exc.value.code == "DAMAGE_SNAPSHOT_INVALID"
    assert engine.combatant_report("tank")["hp"] == 900


def test_concurrent_hits_add_up_without_lost_damage():
    engine = dl.DamageAttributionLedger()
    engine.register_combatant("attacker", hp=500)
    engine.register_combatant("tank", hp=100000)
    barrier = threading.Barrier(8)
    results = []
    guard = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            row = engine.resolve_hit("attacker", "tank", raw=100,
                                     hit_id=f"race-{index}")
        except Exception as exc:  # pragma: no cover - surfaced below
            row = {"hp_damage": -1, "error": repr(exc)}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [row["hp_damage"] for row in results] == [100] * 8
    assert engine.combatant_report("tank")["hp"] == 99200
    assert engine.stats()["hits"] == 8
    assert engine.stats()["hp_damage"] == 800
    assert engine.leaderboard(1) == [{"attacker_id": "attacker",
                                      "hp_damage": 800, "kills": 0, "hits": 8}]
