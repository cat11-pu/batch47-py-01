import threading

import pytest

import guild_recruit_pipeline as rp

T0 = "2026-11-24T10:00:00+00:00"


def pipeline(**kwargs):
    return rp.GuildRecruitPipeline(**kwargs)


def opened(**kwargs):
    pipe = pipeline(**kwargs)
    pipe.open_recruitment("g-1", seats=3, min_level=10, tags=("casual",))
    return pipe


def code_of(excinfo):
    return excinfo.value.code


def test_open_recruitment_and_apply_record_pending():
    pipe = pipeline()
    opened = pipe.open_recruitment("g-1", seats=3, min_level=10,
                                   tags=("casual", "weekend"))
    assert opened == {
        "guild_id": "g-1",
        "seats": 3,
        "min_level": 10,
        "tags": ["casual", "weekend"],
        "status": "open",
        "closes_at": None,
    }
    row = pipe.apply("g-1", "p-1", level=12, note="晚上在线", now=T0)
    assert row == {
        "status": "pending",
        "guild_id": "g-1",
        "player_id": "p-1",
        "level": 12,
        "expires_at": "2026-11-24T10:30:00+00:00",
    }
    assert pipe.is_pending("g-1", "p-1") is True
    assert pipe.pending("g-1") == [{
        "player_id": "p-1",
        "level": 12,
        "note": "晚上在线",
        "expires_at": "2026-11-24T10:30:00+00:00",
    }]
    assert pipe.roster("g-1") == []
    assert pipe.stats() == {"applied": 1, "approved": 0, "rejected": 0,
                            "withdrawn": 0, "expired": 0}


def test_duplicate_application_and_member_reapplication():
    pipe = opened()
    pipe.apply("g-1", "p-1", level=12, now=T0)
    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-1", "p-1", level=12, now=T0)
    assert code_of(exc) == "RECRUIT_DUPLICATE"
    assert pipe.stats()["applied"] == 1

    approved = pipe.decide("g-1", "p-1", reviewer="gm-1", approve=True, now=T0)
    assert approved == {"status": "approved", "guild_id": "g-1",
                        "player_id": "p-1", "members": 1}
    assert pipe.roster("g-1") == ["p-1"]
    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-1", "p-1", level=12, now=T0)
    assert code_of(exc) == "RECRUIT_ALREADY_MEMBER"


def test_unknown_guild_closed_and_duplicate_open_paths():
    pipe = opened()
    pipe.apply("g-1", "p-1", level=12, now=T0)

    with pytest.raises(rp.RecruitError) as exc:
        pipe.open_recruitment("g-1", seats=3, min_level=10, tags=("casual",))
    assert code_of(exc) == "RECRUIT_ALREADY_OPEN"
    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-missing", "p-9", level=20, now=T0)
    assert code_of(exc) == "RECRUIT_UNKNOWN_GUILD"
    with pytest.raises(rp.RecruitError) as exc:
        pipe.roster("g-missing")
    assert code_of(exc) == "RECRUIT_UNKNOWN_GUILD"
    with pytest.raises(rp.RecruitError) as exc:
        pipe.pending("g-missing")
    assert code_of(exc) == "RECRUIT_UNKNOWN_GUILD"
    with pytest.raises(rp.RecruitError) as exc:
        pipe.close_recruitment("g-missing")
    assert code_of(exc) == "RECRUIT_UNKNOWN_GUILD"

    closed = pipe.close_recruitment("g-1")
    assert closed == {"guild_id": "g-1", "status": "closed",
                      "dropped_pending": 1}
    assert pipe.is_pending("g-1", "p-1") is False

    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-1", "p-2", level=12, now=T0)
    assert code_of(exc) == "RECRUIT_CLOSED"

    reopened = pipe.open_recruitment("g-1", seats=2, min_level=10)
    assert reopened["seats"] == 2
    pipe.apply("g-1", "p-3", level=12, now=T0)
    pipe.close_recruitment("g-1")
    with pytest.raises(rp.RecruitError) as exc:
        pipe.decide("g-1", "p-3", reviewer="gm", approve=True, now=T0)
    assert code_of(exc) == "RECRUIT_CLOSED"
    with pytest.raises(rp.RecruitError) as exc:
        pipe.close_recruitment("g-1")
    assert code_of(exc) == "RECRUIT_CLOSED"


def test_level_below_minimum_is_refused():
    pipe = opened()
    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-1", "p-low", level=9, now=T0)
    assert code_of(exc) == "RECRUIT_LEVEL_LOW"
    assert exc.value.detail == {"required": 10, "level": 9}
    assert pipe.is_pending("g-1", "p-low") is False
    assert pipe.stats()["applied"] == 0

    assert pipe.apply("g-1", "p-ok", level=10, now=T0)["status"] == "pending"


def test_seat_capacity_blocks_apply_and_approval():
    pipe = pipeline()
    pipe.open_recruitment("g-2", seats=1, min_level=1)
    pipe.apply("g-2", "a", level=5, now=T0)
    pipe.apply("g-2", "b", level=5, now=T0)
    pipe.decide("g-2", "a", reviewer="gm", approve=True, now=T0)

    with pytest.raises(rp.RecruitError) as exc:
        pipe.decide("g-2", "b", reviewer="gm", approve=True, now=T0)
    assert code_of(exc) == "RECRUIT_FULL"
    assert pipe.is_pending("g-2", "b") is True

    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-2", "c", level=5, now=T0)
    assert code_of(exc) == "RECRUIT_FULL"

    assert pipe.decide("g-2", "b", reviewer="gm", approve=False,
                       now=T0)["status"] == "rejected"
    assert pipe.roster("g-2") == ["a"]


def test_pending_limit_spans_guilds():
    pipe = pipeline(pending_limit=2)
    for gid in ("g-1", "g-2", "g-3"):
        pipe.open_recruitment(gid, seats=5, min_level=1)
    pipe.apply("g-1", "p-1", level=5, now=T0)
    pipe.apply("g-2", "p-1", level=5, now=T0)

    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-3", "p-1", level=5, now=T0)
    assert code_of(exc) == "RECRUIT_PENDING_LIMIT"

    pipe.withdraw("g-1", "p-1", now=T0)
    assert pipe.apply("g-3", "p-1", level=5, now=T0)["status"] == "pending"
    assert pipe.is_pending("g-2", "p-1") is True


def test_daily_limit_resets_next_utc_day():
    pipe = pipeline(daily_apply_limit=2, pending_limit=5)
    for gid in ("g-1", "g-2", "g-3"):
        pipe.open_recruitment(gid, seats=5, min_level=1)
    pipe.apply("g-1", "p-1", level=5, now=T0)
    pipe.apply("g-2", "p-1", level=5, now=T0)

    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-3", "p-1", level=5, now=T0)
    assert code_of(exc) == "RECRUIT_DAILY_LIMIT"

    row = pipe.apply("g-3", "p-1", level=5, now="2026-11-25T10:00:00+00:00")
    assert row["expires_at"] == "2026-11-25T10:30:00+00:00"
    assert pipe.pending("g-3") == [{
        "player_id": "p-1",
        "level": 5,
        "note": "",
        "expires_at": "2026-11-25T10:30:00+00:00",
    }]


def test_rejection_starts_cooldown():
    pipe = pipeline(cooldown_seconds=600)
    pipe.open_recruitment("g-1", seats=5, min_level=1)
    pipe.apply("g-1", "p-1", level=5, now=T0)
    pipe.decide("g-1", "p-1", reviewer="gm", approve=False, now=T0)

    with pytest.raises(rp.RecruitError) as exc:
        pipe.apply("g-1", "p-1", level=5, now="2026-11-24T10:09:59+00:00")
    assert code_of(exc) == "RECRUIT_COOLDOWN"
    assert exc.value.detail == {"until": "2026-11-24T10:10:00+00:00"}

    assert pipe.apply("g-1", "p-1", level=5,
                      now="2026-11-24T10:10:00+00:00")["status"] == "pending"


def test_withdraw_frees_slot_without_cooldown():
    pipe = opened()
    pipe.apply("g-1", "p-1", level=12, now=T0)
    row = pipe.withdraw("g-1", "p-1", now=T0)
    assert row == {"status": "withdrawn", "guild_id": "g-1", "player_id": "p-1"}
    assert pipe.is_pending("g-1", "p-1") is False
    assert pipe.stats()["withdrawn"] == 1

    with pytest.raises(rp.RecruitError) as exc:
        pipe.withdraw("g-1", "p-1", now=T0)
    assert code_of(exc) == "RECRUIT_NOT_PENDING"

    assert pipe.apply("g-1", "p-1", level=12, now=T0)["status"] == "pending"
    assert pipe.stats()["applied"] == 2


def test_expired_application_is_refused_on_decide():
    pipe = pipeline(application_ttl=600)
    pipe.open_recruitment("g-1", seats=5, min_level=1)
    pipe.apply("g-1", "p-1", level=5, now=T0)

    with pytest.raises(rp.RecruitError) as exc:
        pipe.decide("g-1", "p-1", reviewer="gm", approve=True,
                    now="2026-11-24T10:10:00+00:00")
    assert code_of(exc) == "RECRUIT_EXPIRED"
    assert pipe.is_pending("g-1", "p-1") is False
    assert pipe.stats()["expired"] == 1
    assert pipe.stats()["approved"] == 0


def test_expire_sweeps_due_applications():
    pipe = pipeline(application_ttl=600)
    pipe.open_recruitment("g-1", seats=5, min_level=1)
    pipe.apply("g-1", "p-1", level=5, now=T0)
    pipe.apply("g-1", "p-2", level=6, now=T0)

    assert pipe.expire(now="2026-11-24T10:09:59+00:00") == 0
    assert pipe.expire(now="2026-11-24T10:10:00+00:00") == 2
    assert pipe.pending("g-1") == []
    assert pipe.stats()["expired"] == 2
    assert pipe.apply("g-1", "p-3", level=7,
                      now="2026-11-24T10:10:01+00:00")["status"] == "pending"


def test_decide_batch_reports_each_item_and_batch_cap():
    pipe = pipeline(batch_limit=3)
    pipe.open_recruitment("g-1", seats=5, min_level=1)
    for pid in ("a", "b", "c"):
        pipe.apply("g-1", pid, level=5, now=T0)

    rows = pipe.decide_batch("g-1", ["a", "b", "a"], reviewer="gm-1",
                             approve=True, now=T0)
    assert [row["status"] for row in rows] == ["approved", "approved", "skipped"]
    assert rows[2]["code"] == "RECRUIT_NOT_PENDING"
    assert [row.get("members") for row in rows[:2]] == [1, 2]
    assert pipe.roster("g-1") == ["a", "b"]
    assert pipe.is_pending("g-1", "c") is True

    with pytest.raises(rp.RecruitError) as exc:
        pipe.decide_batch("g-1", ["c", "d", "e", "f"], reviewer="gm-1",
                          approve=False, now=T0)
    assert code_of(exc) == "RECRUIT_BATCH_TOO_LARGE"
    assert pipe.is_pending("g-1", "c") is True

    rows = pipe.decide_batch("g-1", ["c", "ghost"], reviewer="gm-1",
                             approve=False, now=T0)
    assert [row["status"] for row in rows] == ["rejected", "skipped"]
    assert rows[1]["code"] == "RECRUIT_NOT_PENDING"
    assert pipe.stats()["rejected"] == 1


def test_concurrent_apply_accepts_exactly_once():
    pipe = pipeline(daily_apply_limit=10, pending_limit=10)
    pipe.open_recruitment("g-1", seats=5, min_level=1)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = pipe.apply("g-1", "p-race", level=5, now=T0)
        except rp.RecruitError as exc:
            result = exc.code
        except Exception as exc:  # pragma: no cover
            result = repr(exc)
        else:
            result = row["status"]
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["RECRUIT_DUPLICATE"] * 7 + ["pending"]
    assert pipe.stats()["applied"] == 1
    assert pipe.pending("g-1") == [{
        "player_id": "p-race",
        "level": 5,
        "note": "",
        "expires_at": "2026-11-24T10:30:00+00:00",
    }]


def test_concurrent_approval_respects_seats():
    pipe = pipeline()
    pipe.open_recruitment("g-9", seats=2, min_level=1)
    for pid in ("a", "b", "c", "d"):
        pipe.apply("g-9", pid, level=5, now=T0)
    barrier = threading.Barrier(4)
    outcomes = []
    lock = threading.Lock()

    def worker(player_id):
        barrier.wait()
        try:
            pipe.decide("g-9", player_id, reviewer="gm", approve=True, now=T0)
            result = "approved"
        except rp.RecruitError as exc:
            result = exc.code
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(pid,))
               for pid in ("a", "b", "c", "d")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["RECRUIT_FULL", "RECRUIT_FULL",
                                "approved", "approved"]
    assert len(pipe.roster("g-9")) == 2
    assert pipe.stats()["approved"] == 2


def test_snapshot_restore_and_corrupt_state():
    pipe = pipeline()
    pipe.open_recruitment("g-1", seats=5, min_level=10)
    pipe.apply("g-1", "a", level=12, now=T0)
    pipe.apply("g-1", "b", level=12, now=T0)
    pipe.decide("g-1", "b", reviewer="gm", approve=False, now=T0)
    state = pipe.snapshot()
    assert state["version"] == 1

    pipe.decide("g-1", "a", reviewer="gm", approve=True, now=T0)
    pipe.apply("g-1", "c", level=12, now=T0)

    restored = pipeline()
    restored.restore(state)
    assert restored.is_pending("g-1", "a") is True
    assert restored.roster("g-1") == []
    assert restored.stats()["approved"] == 0
    with pytest.raises(rp.RecruitError) as exc:
        restored.apply("g-1", "b", level=12,
                       now="2026-11-24T10:00:01+00:00")
    assert code_of(exc) == "RECRUIT_COOLDOWN"

    state["pending"].clear()
    state["guilds"]["g-1"]["seats"] = 99
    assert restored.is_pending("g-1", "a") is True
    assert restored.decide("g-1", "a", reviewer="gm", approve=True,
                           now=T0)["members"] == 1

    bad_states = [
        None,
        {},
        {"version": 2, "guilds": {}, "pending": [], "members": {},
         "cooldowns": [], "daily": [], "stats": {}},
        {"version": 1, "guilds": [], "pending": [], "members": {},
         "cooldowns": [], "daily": [], "stats": {}},
        {"version": 1, "guilds": {}, "pending": "nope", "members": {},
         "cooldowns": [], "daily": [], "stats": {}},
        {"version": 1, "guilds": {}, "pending": [], "members": {},
         "cooldowns": [], "daily": [], "stats": "nope"},
    ]
    for bad in bad_states:
        with pytest.raises(rp.RecruitError) as exc:
            restored.restore(bad)
        assert code_of(exc) == "RECRUIT_SNAPSHOT_INVALID"
    assert restored.roster("g-1") == ["a"]
    assert restored.stats()["approved"] == 1
