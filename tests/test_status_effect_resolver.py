import threading

import pytest

import status_effect_resolver as sr


def resolver(**kwargs):
    return sr.StatusEffectResolver(**kwargs)


def test_define_effect_is_idempotent_and_conflicting_definition_is_refused():
    engine = resolver()
    payload = dict(name="灼烧", category="debuff", max_stacks=3,
                   duration_ms=6000, stack_policy="stack",
                   tick_interval_ms=2000, dispellable=True)
    first = engine.define_effect("burn", **payload)
    assert first["created"] is True
    assert first["effect_id"] == "burn"
    assert first["max_stacks"] == 3
    assert first["tick_interval_ms"] == 2000

    again = engine.define_effect("burn", **payload)
    assert again["created"] is False
    assert again["duration_ms"] == 6000

    with pytest.raises(sr.EffectError) as exc:
        engine.define_effect("burn", **dict(payload, duration_ms=7000))
    assert exc.value.code == "EFFECT_DEFINITION_CONFLICT"
    assert exc.value.detail == {"effect_id": "burn"}


def test_register_target_is_idempotent_and_enforces_target_limit():
    engine = resolver(max_targets=2)
    first = engine.register_target("boss", max_effects=3,
                                   immunity=("stun", "debuff"))
    assert first["created"] is True
    assert first["immunity"] == ["debuff", "stun"]
    assert first["max_effects"] == 3

    again = engine.register_target("boss", max_effects=3,
                                   immunity=("debuff", "stun"))
    assert again["created"] is False

    with pytest.raises(sr.EffectError) as exc:
        engine.register_target("boss", max_effects=4,
                               immunity=("debuff", "stun"))
    assert exc.value.code == "EFFECT_TARGET_CONFLICT"

    assert engine.register_target("add")["created"] is True
    with pytest.raises(sr.EffectError) as exc:
        engine.register_target("third")
    assert exc.value.code == "EFFECT_TARGET_LIMIT"


def test_refresh_policy_keeps_one_stack_and_extends_expiry():
    engine = resolver()
    engine.define_effect("regen", category="buff", max_stacks=5,
                         duration_ms=1000, stack_policy="refresh")
    engine.register_target("tank")

    first = engine.apply_effect("tank", "regen", now_ms=1000)
    assert first["status"] == "applied"
    assert first["stacks"] == 1
    assert first["expires_at_ms"] == 2000
    assert first["remaining_ms"] == 1000

    second = engine.apply_effect("tank", "regen", now_ms=1500)
    assert second["status"] == "refreshed"
    assert second["stacks"] == 1
    assert second["expires_at_ms"] == 2500
    assert engine.remaining_ms("tank", "regen", now_ms=2400) == 100

    assert engine.active_effects("tank", now_ms=2500) == []
    assert engine.remaining_ms("tank", "regen", now_ms=2500) == 0
    assert engine.stats()["applied"] == 1
    assert engine.stats()["refreshed"] == 1


def test_stack_policy_caps_stacks_and_refreshes_duration():
    engine = resolver()
    engine.define_effect("rage", max_stacks=3, duration_ms=1000,
                         stack_policy="stack")
    engine.register_target("tank")

    assert engine.apply_effect("tank", "rage", now_ms=1000)["stacks"] == 1
    second = engine.apply_effect("tank", "rage", now_ms=1200)
    assert (second["status"], second["stacks"]) == ("stacked", 2)
    assert second["expires_at_ms"] == 2200
    third = engine.apply_effect("tank", "rage", now_ms=1300)
    assert (third["status"], third["stacks"]) == ("stacked", 3)
    fourth = engine.apply_effect("tank", "rage", now_ms=1400)
    assert (fourth["status"], fourth["stacks"]) == ("capped", 3)
    assert fourth["expires_at_ms"] == 2400

    active = engine.active_effects("tank", now_ms=1400)
    assert len(active) == 1
    assert active[0]["stacks"] == 3
    assert engine.stats() == {"applied": 1, "refreshed": 0, "stacked": 2,
                              "capped": 1, "replaced": 0, "immune": 0,
                              "removed": 0, "dispelled": 0, "expired": 0}


def test_effect_capacity_blocks_new_effects_but_not_refresh():
    engine = resolver()
    engine.define_effect("short", duration_ms=100)
    engine.define_effect("long_a", duration_ms=5000)
    engine.define_effect("long_b", duration_ms=5000)
    engine.register_target("tank", max_effects=2)

    engine.apply_effect("tank", "short", now_ms=0)
    engine.apply_effect("tank", "long_a", now_ms=0)
    with pytest.raises(sr.EffectError) as exc:
        engine.apply_effect("tank", "long_b", now_ms=0)
    assert exc.value.code == "EFFECT_TARGET_FULL"
    assert exc.value.detail == {"target_id": "tank", "max_effects": 2}

    assert engine.apply_effect("tank", "short", now_ms=50)["status"] == "refreshed"
    at_expiry = engine.apply_effect("tank", "long_b", now_ms=200)
    assert at_expiry["status"] == "applied"
    ids = [row["effect_id"] for row in engine.active_effects("tank", now_ms=200)]
    assert ids == ["long_a", "long_b"]


def test_immunity_matches_effect_id_and_category():
    engine = resolver()
    engine.define_effect("stun", category="debuff", duration_ms=1000)
    engine.define_effect("poison", category="debuff", duration_ms=1000)
    engine.define_effect("haste", category="buff", duration_ms=1000)
    engine.register_target("warden", immunity=("stun", "debuff"))

    blocked_id = engine.apply_effect("warden", "stun", now_ms=10)
    assert blocked_id["status"] == "immune"
    blocked_cat = engine.apply_effect("warden", "poison", now_ms=20)
    assert blocked_cat["status"] == "immune"
    assert engine.apply_effect("warden", "haste", now_ms=30)["status"] == "applied"

    active = engine.active_effects("warden", now_ms=30)
    assert [row["effect_id"] for row in active] == ["haste"]
    assert engine.stats()["immune"] == 2
    assert engine.stats()["applied"] == 1


def test_exclusive_group_replaces_the_previous_effect():
    engine = resolver()
    engine.define_effect("battle_shout", category="buff", duration_ms=5000,
                         exclusive_group="shout")
    engine.define_effect("commanding_shout", category="buff", duration_ms=5000,
                         exclusive_group="shout")
    engine.define_effect("banner", category="buff", duration_ms=5000)
    engine.register_target("tank")

    engine.apply_effect("tank", "battle_shout", now_ms=1000)
    engine.apply_effect("tank", "banner", now_ms=1100)
    replaced = engine.apply_effect("tank", "commanding_shout", now_ms=1200)
    assert replaced["status"] == "applied"
    assert replaced["replaced"] == ["battle_shout"]

    ids = [row["effect_id"] for row in engine.active_effects("tank", now_ms=1200)]
    assert ids == ["banner", "commanding_shout"]
    assert engine.stats()["replaced"] == 1


def test_dispel_filters_category_and_uses_expiry_order():
    engine = resolver()
    engine.define_effect("burn", category="debuff", duration_ms=3000)
    engine.define_effect("poison", category="debuff", duration_ms=1000)
    engine.define_effect("regen", category="buff", duration_ms=2000)
    engine.define_effect("oath", category="buff", duration_ms=500,
                         dispellable=False)
    engine.register_target("tank")
    for effect_id in ("burn", "poison", "regen", "oath"):
        engine.apply_effect("tank", effect_id, now_ms=0)

    removed = engine.dispel("tank", category="debuff", now_ms=100)
    assert [row["effect_id"] for row in removed] == ["poison", "burn"]
    assert [row["effect_id"] for row in engine.active_effects("tank", now_ms=100)] == ["oath", "regen"]

    limited = engine.dispel("tank", limit=1, now_ms=200)
    assert [row["effect_id"] for row in limited] == ["regen"]
    assert engine.stats()["dispelled"] == 3
    assert engine.stats()["removed"] == 0


def test_tick_emits_due_events_in_time_then_id_order():
    engine = resolver()
    engine.define_effect("dot", category="debuff", duration_ms=10000,
                         tick_interval_ms=100)
    engine.define_effect("hot", category="buff", duration_ms=10000,
                         tick_interval_ms=150)
    engine.register_target("tank")
    engine.apply_effect("tank", "dot", now_ms=1000)
    engine.apply_effect("tank", "hot", now_ms=1000)

    events = engine.tick("tank", now_ms=1250)
    assert [(row["kind"], row["effect_id"], row["tick_at_ms"]) for row in events] == [
        ("tick", "dot", 1100),
        ("tick", "hot", 1150),
        ("tick", "dot", 1200),
    ]

    tie = engine.tick("tank", now_ms=1300)
    assert [(row["effect_id"], row["tick_at_ms"]) for row in tie] == [
        ("dot", 1300),
        ("hot", 1300),
    ]


def test_tick_burst_is_rejected_without_advancing_state():
    engine = resolver(tick_limit=5)
    engine.define_effect("dot", category="debuff", duration_ms=100000,
                         tick_interval_ms=100)
    engine.register_target("tank")
    engine.apply_effect("tank", "dot", now_ms=1000)

    with pytest.raises(sr.EffectError) as exc:
        engine.tick("tank", now_ms=1600)
    assert exc.value.code == "EFFECT_TICK_BURST"
    assert exc.value.detail == {"effect_id": "dot", "pending": 6}

    rescued = engine.tick("tank", now_ms=1550)
    assert len(rescued) == 5
    assert [row["tick_at_ms"] for row in rescued] == [1100, 1200, 1300, 1400, 1500]
    assert engine.active_effects("tank", now_ms=1550)[0]["next_tick_ms"] == 1600


def test_tick_stops_at_expiry_and_purge_counts_removals():
    engine = resolver()
    engine.define_effect("short", duration_ms=350, tick_interval_ms=100)
    engine.define_effect("other", duration_ms=100)
    engine.register_target("one")
    engine.register_target("two")
    engine.apply_effect("one", "short", now_ms=0)
    engine.apply_effect("two", "other", now_ms=0)

    events = engine.tick("one", now_ms=500)
    assert [(row["kind"], row.get("tick_at_ms"), row.get("expire_at_ms"))
            for row in events] == [
        ("tick", 100, None), ("tick", 200, None), ("tick", 300, None),
        ("expire", None, 350),
    ]
    assert engine.stats()["expired"] == 1
    assert engine.active_effects("one", now_ms=500) == []
    assert len(engine.active_effects("two", now_ms=99)) == 1
    assert engine.purge(100) == 1
    assert engine.stats()["expired"] == 2


def test_active_effects_are_sorted_and_exclude_expired():
    engine = resolver()
    engine.define_effect("z_effect", duration_ms=500)
    engine.define_effect("a_effect", duration_ms=1500)
    engine.define_effect("m_effect", duration_ms=500)
    engine.register_target("tank")
    for effect_id in ("z_effect", "a_effect", "m_effect"):
        engine.apply_effect("tank", effect_id, now_ms=0)

    active = engine.active_effects("tank", now_ms=100)
    assert [row["effect_id"] for row in active] == ["m_effect", "z_effect", "a_effect"]
    assert [row["remaining_ms"] for row in active] == [400, 400, 1400]
    later = engine.active_effects("tank", now_ms=600)
    assert [row["effect_id"] for row in later] == ["a_effect"]
    assert later[0]["remaining_ms"] == 900

    with pytest.raises(sr.EffectError) as exc:
        engine.active_effects("ghost", now_ms=0)
    assert exc.value.code == "EFFECT_UNKNOWN_TARGET"


def test_snapshot_restore_preserves_effects_and_is_deep_copied():
    engine = resolver()
    engine.define_effect("rage", max_stacks=3, duration_ms=1000,
                         stack_policy="stack")
    engine.register_target("tank")
    engine.apply_effect("tank", "rage", now_ms=1000)

    state = engine.snapshot()
    assert state["version"] == 1
    tampered = engine.snapshot()
    tampered["targets"]["tank"]["effects"]["rage"]["stacks"] = 99
    assert engine.active_effects("tank", now_ms=1000)[0]["stacks"] == 1

    engine.apply_effect("tank", "rage", now_ms=1100)
    assert engine.stats()["applied"] == 1
    assert engine.stats()["stacked"] == 1

    engine.restore(state)
    active = engine.active_effects("tank", now_ms=1000)
    assert len(active) == 1
    assert active[0]["stacks"] == 1
    assert engine.stats()["applied"] == 1
    assert engine.stats()["stacked"] == 0


def test_corrupt_snapshot_is_rejected_without_touching_state():
    engine = resolver()
    engine.define_effect("keep", duration_ms=5000)
    engine.register_target("tank")
    engine.apply_effect("tank", "keep", now_ms=0)

    good = engine.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, definitions="nope"),
        dict(good, targets=[]),
        dict(good, stats={"applied": -1, "refreshed": 0, "stacked": 0,
                          "capped": 0, "replaced": 0, "immune": 0,
                          "removed": 0, "dispelled": 0, "expired": 0}),
    ]
    for bad in bad_states:
        with pytest.raises(sr.EffectError) as exc:
            engine.restore(bad)
        assert exc.value.code == "EFFECT_SNAPSHOT_INVALID"

    assert [row["effect_id"] for row in engine.active_effects("tank", now_ms=0)] == ["keep"]
    assert engine.apply_effect("tank", "keep", now_ms=10)["status"] == "refreshed"


def test_concurrent_stacking_lands_exactly_at_the_cap():
    engine = resolver()
    engine.define_effect("rage", max_stacks=3, duration_ms=10000,
                         stack_policy="stack")
    engine.register_target("tank")
    barrier = threading.Barrier(8)
    results = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = engine.apply_effect("tank", "rage", now_ms=1000)
        except Exception as exc:  # pragma: no cover - surfaced below
            row = {"status": "error", "code": repr(exc)}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(row["stacks"] for row in results) == [1, 2, 3, 3, 3, 3, 3, 3]
    assert sorted(row["status"] for row in results) == (
        ["applied"] + ["capped"] * 5 + ["stacked"] * 2
    )
    assert engine.active_effects("tank", now_ms=1000)[0]["stacks"] == 3
    assert engine.stats()["applied"] == 1
    assert engine.stats()["stacked"] == 2
    assert engine.stats()["capped"] == 5
