import struct
import threading

import pytest

import waveform_peak_index as wp


def index(**kwargs):
    return wp.WaveformPeakIndex(**kwargs)


def test_append_rejects_bad_samples_without_partial_write():
    idx = index(base_bucket=4)
    assert idx.append([1, -32768, 32767]) == {
        "appended": 3,
        "completed_buckets": 0,
        "total_samples": 3,
    }
    for bad in ([32768], [-32769], [1, "x"], [1.5]):
        with pytest.raises(wp.WaveError) as exc:
            idx.append(bad)
        assert exc.value.code == "WP_SAMPLE_INVALID"
    assert idx.summary()["samples"] == 3
    assert idx.peaks(level=0) == []


def test_base_buckets_record_min_and_max():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append([3, -5, 7, 1, 0, 0, 0, 0])
    assert idx.peaks(level=0) == [{"min": -5, "max": 7},
                                  {"min": 0, "max": 0}]
    summary = idx.summary()
    assert summary["samples"] == 8
    assert summary["completed"] == [2, 1, 0]


def test_query_returns_partial_tail_bucket_with_rms():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append([10, -2, 5, 0, 7])
    rows = idx.query(0, 5, ms_per_pixel=4)
    assert [(row["index"], row["start_sample"], row["end_sample"],
             row["min"], row["max"], row["rms"], row["complete"])
            for row in rows] == [
        (0, 0, 4, -2, 10, 5, True),
        (1, 4, 5, 7, 7, 7, False),
    ]


def test_pyramid_levels_aggregate_completed_buckets():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=4)
    idx.append(list(range(1, 17)))
    assert idx.summary()["completed"] == [4, 2, 1, 0]
    assert idx.peaks(level=2) == [{"min": 1, "max": 16}]
    assert idx.peaks(level=3) == []


def test_pcm16_append_decodes_little_endian():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    payload = struct.pack("<4h", 100, -200, 300, -400)
    result = idx.append_pcm16(payload)
    assert result["appended"] == 4
    assert result["total_samples"] == 4
    assert idx.peaks(level=0) == [{"min": -400, "max": 300}]
    with pytest.raises(wp.WaveError) as exc:
        idx.append_pcm16(b"\x00\x01\x02")
    assert exc.value.code == "WP_PCM_INVALID"
    with pytest.raises(wp.WaveError) as exc:
        idx.append_pcm16("not bytes")
    assert exc.value.code == "WP_PCM_INVALID"
    assert idx.summary()["samples"] == 4


def test_sample_budget_is_checked_before_writing():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3, max_samples=5)
    idx.append([1, 2, 3])
    with pytest.raises(wp.WaveError) as exc:
        idx.append([4, 5, 6])
    assert exc.value.code == "WP_BUDGET_EXCEEDED"
    assert idx.summary()["samples"] == 3
    assert idx.peaks(level=0) == []
    idx.append([4, 5])
    assert idx.summary()["samples"] == 5


def test_level_for_picks_coarsest_bucket_that_fits():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=5)
    with pytest.raises(wp.WaveError) as exc:
        idx.level_for(0)
    assert exc.value.code == "WP_RANGE_INVALID"
    assert idx.level_for(4) == 0
    assert idx.level_for(8) == 1
    assert idx.level_for(15.9) == 1
    assert idx.level_for(16) == 2
    assert idx.level_for(1000) == 4


def test_query_range_validation_and_clamping():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append(list(range(10)))
    for start, end in ((-1, 5), (5, 5), (6, 2)):
        with pytest.raises(wp.WaveError) as exc:
            idx.query(start, end, ms_per_pixel=4)
        assert exc.value.code == "WP_RANGE_INVALID"
    tail = idx.query(8, 100, ms_per_pixel=4)
    assert [(row["index"], row["start_sample"], row["end_sample"],
             row["min"], row["max"], row["complete"]) for row in tail] == [
        (2, 8, 10, 8, 9, False)
    ]
    assert idx.query(100, 200, ms_per_pixel=4) == []


def test_query_rejects_too_many_buckets():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3,
                max_query_buckets=3)
    idx.append(list(range(100)))
    with pytest.raises(wp.WaveError) as exc:
        idx.query(0, 40, ms_per_pixel=4)
    assert exc.value.code == "WP_QUERY_TOO_DENSE"


def test_peaks_reject_invalid_levels():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append([1, 2, 3, 4])
    for level in (-1, 3):
        with pytest.raises(wp.WaveError) as exc:
            idx.peaks(level=level)
        assert exc.value.code == "WP_LEVEL_INVALID"


def test_reset_clears_waveform_but_keeps_usable_state():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append([1, 2, 3, 4, 5])
    idx.reset()
    summary = idx.summary()
    assert summary["samples"] == 0
    assert summary["completed"] == [0, 0, 0]
    assert idx.peaks(level=0) == []
    idx.append([9, 8, 7, 6])
    assert idx.peaks(level=0) == [{"min": 6, "max": 9}]


def test_snapshot_restore_continues_open_bucket_and_rejects_corruption():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append([1, 2, 3, 4, 5, 6])
    state = idx.snapshot()
    idx.append([7, 8])

    restored = index(sample_rate=1000, base_bucket=4, max_levels=3)
    restored.restore(state)
    assert restored.summary()["samples"] == 6
    restored.append([9, 10])
    assert restored.summary()["samples"] == 8
    assert restored.peaks(level=0) == [{"min": 1, "max": 4},
                                       {"min": 5, "max": 10}]
    assert state["total"] == 6

    for bad in (None, {}, {"version": 2, "levels": [], "stats": {}},
                {"version": 1, "levels": "nope", "stats": {}},
                {"version": 1, "levels": [], "stats": "nope"}):
        with pytest.raises(wp.WaveError) as exc:
            restored.restore(bad)
        assert exc.value.code == "WP_SNAPSHOT_INVALID"
    assert restored.summary()["samples"] == 8


def test_open_bucket_accumulates_across_multiple_append_calls():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append([1, 2])
    idx.append([3, 4])
    idx.append([5])
    assert idx.peaks(level=0) == [{"min": 1, "max": 4}]
    tail = idx.query(4, 5, ms_per_pixel=4)
    assert [(row["min"], row["max"], row["complete"]) for row in tail] == [
        (5, 5, False)
    ]
    assert idx.summary()["samples"] == 5


def test_concurrent_appends_preserve_global_extremes():
    idx = index(sample_rate=48000, base_bucket=256, max_levels=2,
                max_samples=1_000_000)
    barrier = threading.Barrier(8)

    def worker(slot):
        value = slot * 1000 - 3000
        barrier.wait()
        try:
            idx.append([value] * 1000)
        except Exception:  # pragma: no cover - surfaced via assertion
            pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    completed = idx.peaks(level=0)
    assert len(completed) == 31
    assert min(row["min"] for row in completed) == -3000
    assert max(row["max"] for row in completed) == 4000
    summary = idx.summary()
    assert summary["samples"] == 8000
    assert summary["completed"] == [31, 15]


def test_stats_and_reset():
    idx = index(sample_rate=1000, base_bucket=4, max_levels=3)
    idx.append([1, 2, 3])
    idx.append_pcm16(struct.pack("<2h", 4, 5))
    idx.query(0, 5, ms_per_pixel=4)
    assert idx.stats() == {
        "appends": 1,
        "pcm_appends": 1,
        "samples_written": 5,
        "buckets_closed": 1,
        "queries": 1,
    }
    idx.reset_stats()
    assert idx.stats() == {
        "appends": 0,
        "pcm_appends": 0,
        "samples_written": 0,
        "buckets_closed": 0,
        "queries": 0,
    }
    assert idx.summary()["samples"] == 5
