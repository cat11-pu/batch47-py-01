import threading

import pytest

import audio_clip_assembler as ac


def test_constructor_rejects_invalid_limits():
    with pytest.raises(ac.AudioError) as exc:
        ac.AudioClipAssembler(sample_rate=0)
    assert exc.value.code == "AUDIO_CONFIG_INVALID"

    with pytest.raises(ac.AudioError) as exc:
        ac.AudioClipAssembler(max_clips=0)
    assert exc.value.code == "AUDIO_CONFIG_INVALID"

    with pytest.raises(ac.AudioError) as exc:
        ac.AudioClipAssembler(default_fade=600, max_fade=480)
    assert exc.value.code == "AUDIO_CONFIG_INVALID"

    assembler = ac.AudioClipAssembler()
    assert assembler.sample_rate == 48000
    assert assembler.max_clips == 64
    assert assembler.default_fade == 480
    assert assembler.max_fade == 2400


def test_session_creation_is_idempotent_and_conflicts_are_reported():
    assembler = ac.AudioClipAssembler()
    first = assembler.create_session("s1", gap_samples=240)
    assert first["session_id"] == "s1"
    assert first["gap_samples"] == 240
    assert first["max_duration"] is None
    assert first["created"] is True

    again = assembler.create_session("s1", gap_samples=240)
    assert again["created"] is False

    with pytest.raises(ac.AudioError) as exc:
        assembler.create_session("s1", gap_samples=480)
    assert exc.value.code == "AUDIO_CONFLICT"

    with pytest.raises(ac.AudioError) as exc:
        assembler.create_session("s1", gap_samples=240, max_duration=10000)
    assert exc.value.code == "AUDIO_CONFLICT"

    with pytest.raises(ac.AudioError) as exc:
        assembler.preview("missing")
    assert exc.value.code == "AUDIO_UNKNOWN_SESSION"


def test_add_clip_uses_default_fades_and_rejects_duplicates():
    assembler = ac.AudioClipAssembler()
    assembler.create_session("s1")
    added = assembler.add_clip("s1", "c1", samples=4800)
    assert added["index"] == 0
    assert added["samples"] == 4800
    assert added["fade_in"] == 480
    assert added["fade_out"] == 480
    assert added["gap_after"] is None
    assert added["clip_count"] == 1

    with pytest.raises(ac.AudioError) as exc:
        assembler.add_clip("s1", "c1", samples=100)
    assert exc.value.code == "AUDIO_DUPLICATE_CLIP"

    with pytest.raises(ac.AudioError) as exc:
        assembler.add_clip("s1", "c2", samples=0)
    assert exc.value.code == "AUDIO_BAD_PARAM"

    capacity = assembler.capacity("s1")
    assert capacity == {"max_clips": 64, "clip_count": 1, "remaining": 63}


def test_fade_lengths_are_bounded_by_samples_and_max_fade():
    assembler = ac.AudioClipAssembler()
    assembler.create_session("s1")

    with pytest.raises(ac.AudioError) as exc:
        assembler.add_clip("s1", "long", samples=100, fade_in=60)
    assert exc.value.code == "AUDIO_FADE_TOO_LONG"

    with pytest.raises(ac.AudioError) as exc:
        assembler.add_clip("s1", "neg", samples=1000, fade_in=-1)
    assert exc.value.code == "AUDIO_BAD_PARAM"

    with pytest.raises(ac.AudioError) as exc:
        assembler.add_clip("s1", "huge", samples=10000, fade_out=2500)
    assert exc.value.code == "AUDIO_FADE_TOO_LONG"

    ok = assembler.add_clip("s1", "half", samples=1000, fade_in=500)
    assert ok["fade_in"] == 500
    assert assembler.preview("s1")["clip_count"] == 1


def test_session_capacity_blocks_extra_clips_without_partial_writes():
    assembler = ac.AudioClipAssembler(max_clips=2, default_fade=0)
    assembler.create_session("s1")
    assembler.add_clip("s1", "a", samples=1000)
    assembler.add_clip("s1", "b", samples=1000)

    with pytest.raises(ac.AudioError) as exc:
        assembler.add_clip("s1", "c", samples=1000)
    assert exc.value.code == "AUDIO_SESSION_FULL"

    assert assembler.capacity("s1")["remaining"] == 0
    preview = assembler.preview("s1")
    assert preview["order"] == ["a", "b"]
    assert preview["clip_count"] == 2
    assert assembler.stats()["rejects"] == 1


def test_crossfade_seam_uses_smaller_fade_and_ignores_gap():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1", gap_samples=300)
    assembler.add_clip("s1", "a", samples=4800, fade_out=480, gap_after=240)
    assembler.add_clip("s1", "b", samples=4800, fade_in=480)

    preview = assembler.preview("s1")
    assert preview["duration_samples"] == 9120
    assert preview["duration_seconds"] == pytest.approx(0.19)
    assert preview["seams"] == [
        {"from": "a", "to": "b", "kind": "crossfade",
         "overlap_samples": 480, "gap_samples": 0},
    ]
    assert preview["committed"] is False
    assert preview["generation"] == 0


def test_gap_seam_uses_clip_override_then_session_default():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1", gap_samples=300)
    assembler.add_clip("s1", "a", samples=1000, fade_out=0)
    assembler.add_clip("s1", "b", samples=2000, fade_in=480)

    preview = assembler.preview("s1")
    assert preview["duration_samples"] == 3300
    assert preview["seams"] == [
        {"from": "a", "to": "b", "kind": "gap",
         "overlap_samples": 0, "gap_samples": 300},
    ]

    assembler.create_session("s2", gap_samples=300)
    assembler.add_clip("s2", "a", samples=1000, gap_after=100)
    assembler.add_clip("s2", "b", samples=2000)
    second = assembler.preview("s2")
    assert second["duration_samples"] == 3100
    assert second["seams"][0]["gap_samples"] == 100


def test_three_clip_timeline_mixes_crossfade_and_gap_seams():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1")
    assembler.add_clip("s1", "a", samples=1000, fade_out=100)
    assembler.add_clip("s1", "b", samples=2000, fade_in=150, gap_after=50)
    assembler.add_clip("s1", "c", samples=500, fade_in=0)

    preview = assembler.preview("s1")
    assert preview["order"] == ["a", "b", "c"]
    assert preview["duration_samples"] == 3450
    assert preview["seams"] == [
        {"from": "a", "to": "b", "kind": "crossfade",
         "overlap_samples": 100, "gap_samples": 0},
        {"from": "b", "to": "c", "kind": "gap",
         "overlap_samples": 0, "gap_samples": 50},
    ]


def test_duration_seconds_follow_session_sample_rate():
    fast = ac.AudioClipAssembler(sample_rate=44100, default_fade=0)
    fast.create_session("s1")
    fast.add_clip("s1", "a", samples=44100)
    assert fast.preview("s1")["duration_seconds"] == pytest.approx(1.0)

    slow = ac.AudioClipAssembler(sample_rate=8000, default_fade=0)
    slow.create_session("s1")
    slow.add_clip("s1", "a", samples=2000)
    assert slow.preview("s1")["duration_seconds"] == pytest.approx(0.25)


def test_clip_order_can_be_changed_and_clips_removed():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1")
    assembler.add_clip("s1", "a", samples=1000)
    assembler.add_clip("s1", "b", samples=2000)
    assembler.add_clip("s1", "c", samples=3000)

    moved = assembler.move_clip("s1", "c", 0)
    assert moved["index"] == 0
    assert moved["order"] == ["c", "a", "b"]
    assert assembler.preview("s1")["duration_samples"] == 6000

    removed = assembler.remove_clip("s1", "a")
    assert removed["removed"] == "a"
    assert removed["clip_count"] == 2
    preview = assembler.preview("s1")
    assert preview["order"] == ["c", "b"]
    assert preview["duration_samples"] == 5000

    with pytest.raises(ac.AudioError) as exc:
        assembler.remove_clip("s1", "nope")
    assert exc.value.code == "AUDIO_UNKNOWN_CLIP"

    with pytest.raises(ac.AudioError) as exc:
        assembler.move_clip("s1", "c", 5)
    assert exc.value.code == "AUDIO_BAD_POSITION"


def test_commit_freezes_session_until_reopened():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1")
    assembler.add_clip("s1", "a", samples=1000)
    assembler.add_clip("s1", "b", samples=2000)

    committed = assembler.commit("s1")
    assert committed["committed"] is True
    assert committed["generation"] == 1
    assert committed["duration_samples"] == 3000
    assert committed["clip_count"] == 2
    assert committed["order"] == ["a", "b"]
    assert assembler.status("s1")["committed"] is True

    with pytest.raises(ac.AudioError) as exc:
        assembler.add_clip("s1", "c", samples=1500)
    assert exc.value.code == "AUDIO_SESSION_COMMITTED"
    with pytest.raises(ac.AudioError) as exc:
        assembler.remove_clip("s1", "a")
    assert exc.value.code == "AUDIO_SESSION_COMMITTED"

    reopened = assembler.reopen("s1")
    assert reopened["committed"] is False
    assert reopened["generation"] == 1
    assembler.add_clip("s1", "c", samples=1500)
    second = assembler.commit("s1")
    assert second["generation"] == 2
    stats = assembler.stats()
    assert stats["commits"] == 2
    assert stats["reopens"] == 1


def test_commit_rejects_empty_and_oversized_timelines_without_freezing():
    empty = ac.AudioClipAssembler(default_fade=0)
    empty.create_session("s0")
    with pytest.raises(ac.AudioError) as exc:
        empty.commit("s0")
    assert exc.value.code == "AUDIO_EMPTY_TIMELINE"
    assert empty.status("s0")["committed"] is False

    capped = ac.AudioClipAssembler(default_fade=0)
    capped.create_session("s1", max_duration=1500)
    capped.add_clip("s1", "a", samples=1000)
    capped.add_clip("s1", "b", samples=1000)
    with pytest.raises(ac.AudioError) as exc:
        capped.commit("s1")
    assert exc.value.code == "AUDIO_DURATION_LIMIT"
    assert capped.status("s1")["committed"] is False

    capped.remove_clip("s1", "b")
    ok = capped.commit("s1")
    assert ok["duration_samples"] == 1000
    assert capped.stats()["commits"] == 1
    assert capped.stats()["rejects"] == 1


def test_snapshot_and_restore_preserve_sessions_and_commits():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1")
    assembler.create_session("s2", gap_samples=100)
    assembler.add_clip("s1", "a", samples=1000)
    assembler.add_clip("s1", "b", samples=2000)
    assembler.commit("s1")
    assembler.add_clip("s2", "x", samples=500)

    state = assembler.snapshot()
    assert state["version"] == 1

    assembler.reopen("s1")
    assembler.add_clip("s1", "c", samples=1500)
    assembler.add_clip("s2", "y", samples=700)

    restored = ac.AudioClipAssembler(default_fade=0)
    restored.restore(state)
    first = restored.preview("s1")
    assert first["order"] == ["a", "b"]
    assert first["committed"] is True
    assert first["generation"] == 1
    assert first["duration_samples"] == 3000
    second = restored.preview("s2")
    assert second["order"] == ["x"]
    assert second["duration_samples"] == 500
    assert restored.stats()["commits"] == 1

    state["sessions"]["s1"]["clips"].clear()
    assert restored.preview("s1")["clip_count"] == 2


def test_corrupt_snapshot_is_rejected_and_state_is_unchanged():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1")
    assembler.add_clip("s1", "keep", samples=1000)

    bad_states = [
        None,
        {},
        {"version": 2, "sessions": {},
         "stats": {"commits": 0, "reopens": 0, "rejects": 0}},
        {"version": 1, "sessions": [],
         "stats": {"commits": 0, "reopens": 0, "rejects": 0}},
        {"version": 1,
         "sessions": {"s1": {"gap_samples": 0, "max_duration": None,
                             "clips": "broken", "committed": False,
                             "generation": 0}},
         "stats": {"commits": 0, "reopens": 0, "rejects": 0}},
        {"version": 1, "sessions": {},
         "stats": {"commits": "many", "reopens": 0, "rejects": 0}},
    ]
    for bad in bad_states:
        with pytest.raises(ac.AudioError) as exc:
            assembler.restore(bad)
        assert exc.value.code == "AUDIO_SNAPSHOT_INVALID"

    preview = assembler.preview("s1")
    assert preview["clip_count"] == 1
    assert preview["order"] == ["keep"]
    assert preview["duration_samples"] == 1000


def test_concurrent_add_of_same_clip_id_accepts_exactly_once():
    assembler = ac.AudioClipAssembler(default_fade=0)
    assembler.create_session("s1")
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            assembler.add_clip("s1", "race", samples=1000)
            outcome = "ok"
        except Exception as exc:  # pragma: no cover - surfaced via assertions
            outcome = getattr(exc, "code", repr(exc))
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["AUDIO_DUPLICATE_CLIP"] * 7 + ["ok"]
    preview = assembler.preview("s1")
    assert preview["clip_count"] == 1
    assert preview["order"] == ["race"]
    assert assembler.stats()["rejects"] == 7
