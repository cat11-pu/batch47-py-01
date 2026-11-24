import threading

import pytest

import timecode_range_guard as tc


def review_guard(**kwargs):
    guard = tc.TimecodeRangeGuard(**kwargs)
    guard.open_clip(
        "clip-a",
        fps_num=25,
        fps_den=1,
        start_timecode="00:00:10:00",
        duration_frames=250,
    )
    return guard


def test_ndf_parse_and_format_roundtrip():
    guard = tc.TimecodeRangeGuard()
    parsed = guard.parse_timecode("01:02:03:04", fps=25, drop_frame=False)
    assert parsed == {"frames": 93079, "fps": 25, "drop_frame": False}
    assert guard.format_timecode(93079, fps=25, drop_frame=False) == "01:02:03:04"
    assert guard.format_timecode(0, fps=25, drop_frame=False) == "00:00:00:00"


def test_malformed_timecode_and_rate_mismatches_are_refused():
    guard = tc.TimecodeRangeGuard()
    for text in ("1:2:3:4", "00:00:60:00", "00:00:00:25", "aa:00:00:00"):
        with pytest.raises(tc.TcError) as exc:
            guard.parse_timecode(text, fps=25, drop_frame=False)
        assert exc.value.code in {"TC_FORMAT_INVALID", "TC_RATE_MISMATCH"}
    with pytest.raises(tc.TcError) as exc:
        guard.parse_timecode("00:00:00;00", fps=25, drop_frame=False)
    assert exc.value.code == "TC_FORMAT_INVALID"
    with pytest.raises(tc.TcError) as exc:
        guard.parse_timecode("00:00:00:00", fps=25, drop_frame=True)
    assert exc.value.code == "TC_FORMAT_INVALID"


def test_drop_frame_boundary_arithmetic():
    guard = tc.TimecodeRangeGuard()
    assert guard.parse_timecode("00:00:59;29", fps=30,
                                drop_frame=True)["frames"] == 1799
    assert guard.parse_timecode("00:01:00;02", fps=30,
                                drop_frame=True)["frames"] == 1800
    assert guard.format_timecode(1799, fps=30,
                                 drop_frame=True) == "00:00:59;29"
    assert guard.format_timecode(1800, fps=30,
                                 drop_frame=True) == "00:01:00;02"


def test_drop_frame_tenth_minute_does_not_skip_labels():
    guard = tc.TimecodeRangeGuard()
    assert guard.parse_timecode("00:10:00;00", fps=30,
                                drop_frame=True)["frames"] == 17982
    assert guard.format_timecode(17982, fps=30,
                                 drop_frame=True) == "00:10:00;00"
    assert guard.parse_timecode("00:20:00;00", fps=30,
                                drop_frame=True)["frames"] == 35964


def test_illegal_drop_frame_labels_are_refused():
    guard = tc.TimecodeRangeGuard()
    for text in ("00:01:00;00", "00:01:00;01", "00:19:00;00", "00:19:00;01"):
        with pytest.raises(tc.TcError) as exc:
            guard.parse_timecode(text, fps=30, drop_frame=True)
        assert exc.value.code == "TC_INVALID_DROP"
    assert guard.parse_timecode("00:10:00;00", fps=30,
                                drop_frame=True)["frames"] == 17982


def test_drop_frame_is_only_supported_for_30_and_60():
    guard = tc.TimecodeRangeGuard()
    for fps in (24, 25, 50):
        with pytest.raises(tc.TcError) as exc:
            guard.parse_timecode("00:00:00;00", fps=fps, drop_frame=True)
        assert exc.value.code == "TC_RATE_UNSUPPORTED"


def test_frame_and_millisecond_conversion_floors():
    guard = tc.TimecodeRangeGuard()
    assert guard.frame_to_ms(30, fps_num=30000, fps_den=1001) == 1001
    assert guard.ms_to_frame(1001, fps_num=30000, fps_den=1001) == 30
    assert guard.ms_to_frame(1000, fps_num=30000, fps_den=1001) == 29
    assert guard.frame_to_ms(0, fps_num=30000, fps_den=1001) == 0
    with pytest.raises(tc.TcError) as exc:
        guard.frame_to_ms(10, fps_num=30000, fps_den=0)
    assert exc.value.code == "TC_RATE_UNSUPPORTED"
    with pytest.raises(tc.TcError) as exc:
        guard.frame_to_ms(-1, fps_num=25, fps_den=1)
    assert exc.value.code == "TC_FRAME_INVALID"


def test_clip_registration_capacity_and_unknown_clip():
    guard = tc.TimecodeRangeGuard(max_clips=2)
    first = guard.open_clip("clip-a", fps_num=25, fps_den=1,
                            start_timecode="00:00:10:00",
                            duration_frames=250)
    assert first["start_frame"] == 250
    assert first["end_frame"] == 500
    assert first["nominal_fps"] == 25
    again = guard.open_clip("clip-a", fps_num=25, fps_den=1,
                            start_timecode="00:00:10:00",
                            duration_frames=250)
    assert again["duration_frames"] == 250
    with pytest.raises(tc.TcError) as exc:
        guard.open_clip("clip-a", fps_num=25, fps_den=1,
                        start_timecode="00:00:10:00", duration_frames=251)
    assert exc.value.code == "TC_CLIP_EXISTS"
    with pytest.raises(tc.TcError) as exc:
        guard.open_clip("clip-zero", fps_num=25, fps_den=1,
                        start_timecode="00:00:00:00", duration_frames=0)
    assert exc.value.code == "TC_DURATION_INVALID"
    guard.open_clip("clip-b", fps_num=25, fps_den=1,
                    start_timecode="00:00:00:00", duration_frames=25)
    with pytest.raises(tc.TcError) as exc:
        guard.open_clip("clip-c", fps_num=25, fps_den=1,
                        start_timecode="00:00:00:00", duration_frames=25)
    assert exc.value.code == "TC_CLIP_LIMIT"
    guard.close_clip("clip-b")
    with pytest.raises(tc.TcError) as exc:
        guard.markers("clip-b")
    assert exc.value.code == "TC_CLIP_UNKNOWN"


def test_marker_bounds_and_inverted_ranges():
    guard = review_guard()
    with pytest.raises(tc.TcError) as exc:
        guard.add_marker("clip-a", start="00:00:09:00", end="00:00:11:00",
                         label="intro")
    assert exc.value.code == "TC_RANGE_OUTSIDE"
    with pytest.raises(tc.TcError) as exc:
        guard.add_marker("clip-a", start="00:00:19:00", end="00:00:20:01",
                         label="tail")
    assert exc.value.code == "TC_RANGE_OUTSIDE"
    with pytest.raises(tc.TcError) as exc:
        guard.add_marker("clip-a", start="00:00:12:00", end="00:00:12:00",
                         label="empty")
    assert exc.value.code == "TC_RANGE_INVERTED"
    marker = guard.add_marker("clip-a", start="00:00:11:00",
                              end="00:00:12:00", label="speech")
    assert marker == {"label": "speech", "start": 25, "end": 50}


def test_duplicate_marker_is_idempotent_and_list_is_sorted():
    guard = review_guard()
    late = guard.add_marker("clip-a", start="00:00:15:00",
                            end="00:00:16:00", label="late")
    early = guard.add_marker("clip-a", start="00:00:11:00",
                             end="00:00:12:00", label="early")
    same = guard.add_marker("clip-a", start="00:00:11:00",
                            end="00:00:12:00", label="early")
    assert same == early
    listed = guard.markers("clip-a")
    assert [row["label"] for row in listed] == ["early", "late"]
    assert listed[0]["start"] == 25
    assert listed[1]["start"] == 125
    assert late["end"] == 150
    assert guard.stats()["markers_added"] == 2
    guard.reset_stats()
    assert guard.stats() == {
        "parsed": 0,
        "formatted": 0,
        "conversions": 0,
        "markers_added": 0,
        "markers_merged": 0,
        "trims": 0,
    }


def test_marker_limit_and_empty_label_are_refused():
    guard = review_guard(marker_limit=2)
    guard.add_marker("clip-a", start="00:00:11:00", end="00:00:12:00",
                     label="one")
    guard.add_marker("clip-a", start="00:00:13:00", end="00:00:14:00",
                     label="two")
    with pytest.raises(tc.TcError) as exc:
        guard.add_marker("clip-a", start="00:00:15:00", end="00:00:16:00",
                         label="three")
    assert exc.value.code == "TC_MARKER_LIMIT"
    other = review_guard()
    with pytest.raises(tc.TcError) as exc:
        other.add_marker("clip-a", start="00:00:11:00", end="00:00:12:00",
                         label="")
    assert exc.value.code == "TC_LABEL_INVALID"


def test_merge_markers_respects_label_and_gap_budget():
    guard = review_guard()
    guard.add_marker("clip-a", start="00:00:10:00", end="00:00:10:10",
                     label="speech")
    guard.add_marker("clip-a", start="00:00:10:12", end="00:00:10:20",
                     label="speech")
    guard.add_marker("clip-a", start="00:00:11:05", end="00:00:11:15",
                     label="speech")
    guard.add_marker("clip-a", start="00:00:10:05", end="00:00:10:15",
                     label="music")
    merged = guard.merge_markers("clip-a", gap_frames=2)
    assert merged["before"] == 4
    assert merged["after"] == 3
    speech = [row for row in merged["markers"] if row["label"] == "speech"]
    assert [(row["start"], row["end"]) for row in speech] == [(0, 20), (30, 40)]
    music = [row for row in merged["markers"] if row["label"] == "music"]
    assert len(music) == 1
    assert guard.markers("clip-a") == merged["markers"]
    assert guard.stats()["markers_merged"] == 1
    with pytest.raises(tc.TcError) as exc:
        guard.merge_markers("clip-a", gap_frames=-1)
    assert exc.value.code == "TC_MERGE_GAP_INVALID"


def test_trim_range_clamps_to_clip_bounds():
    guard = review_guard()
    trimmed = guard.trim_range("clip-a", start="00:00:08:00",
                               end="00:00:12:00")
    assert trimmed == {"start": 0, "end": 50, "duration_frames": 50}
    tail = guard.trim_range("clip-a", start="00:00:18:00",
                            end="00:00:22:00")
    assert tail == {"start": 200, "end": 250, "duration_frames": 50}
    with pytest.raises(tc.TcError) as exc:
        guard.trim_range("clip-a", start="00:00:09:00", end="00:00:10:00")
    assert exc.value.code == "TC_RANGE_OUTSIDE"
    with pytest.raises(tc.TcError) as exc:
        guard.trim_range("clip-a", start="00:00:12:00", end="00:00:11:00")
    assert exc.value.code == "TC_RANGE_INVERTED"
    assert guard.stats()["trims"] == 2


def test_snapshot_restore_and_corrupt_state():
    guard = review_guard()
    guard.add_marker("clip-a", start="00:00:11:00", end="00:00:12:00",
                     label="speech")
    state = guard.snapshot()
    guard.add_marker("clip-a", start="00:00:13:00", end="00:00:14:00",
                     label="later")

    restored = tc.TimecodeRangeGuard()
    restored.restore(state)
    assert [row["label"] for row in restored.markers("clip-a")] == ["speech"]
    assert restored.stats()["markers_added"] == 1
    later = restored.add_marker("clip-a", start="00:00:13:00",
                                end="00:00:14:00", label="later")
    assert later["start"] == 75
    assert state["clips"]["clip-a"]["markers"] == [
        {"label": "speech", "start": 25, "end": 50}
    ]

    for bad in (None, {}, {"version": 2, "clips": {}, "stats": {}},
                {"version": 1, "clips": "nope", "stats": {}},
                {"version": 1, "clips": {}, "stats": "nope"}):
        with pytest.raises(tc.TcError) as exc:
            restored.restore(bad)
        assert exc.value.code == "TC_SNAPSHOT_INVALID"
    assert len(restored.markers("clip-a")) == 2


def test_concurrent_equal_marker_is_added_once():
    guard = review_guard()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.add_marker("clip-a", start="00:00:11:00",
                                   end="00:00:12:00", label="race")
        except Exception as err:  # pragma: no cover - surfaced via assertion
            out = {"error": repr(err)}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(row.get("label") == "race" for row in results)
    assert len(guard.markers("clip-a")) == 1
    assert guard.stats()["markers_added"] == 1
