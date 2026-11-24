import hashlib
import struct
import threading

import pytest

import audio_asset_registry as ar


def chunk(cid, payload):
    body = cid + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        body += b"\x00"
    return body


def wav_bytes(*, audio_format=1, channels=1, rate=8000, bits=16,
              frames=2000, extra=(), omit_fmt=False, omit_data=False,
              data_size=None, block_align=None):
    block = block_align if block_align is not None else channels * (bits // 8)
    payload = bytes(frames * block)
    if data_size is not None:
        payload = payload[:data_size].ljust(data_size, b"\x00")
    fmt_payload = struct.pack("<HHIIHH", audio_format, channels, rate,
                              rate * block, block, bits)
    parts = []
    for cid, body in extra:
        parts.append(chunk(cid, body))
    if not omit_fmt:
        parts.append(chunk(b"fmt ", fmt_payload))
    if not omit_data:
        parts.append(chunk(b"data", payload))
    body = b"WAVE" + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_constructor_rejects_invalid_limits():
    for kwargs in ({"max_assets": 0}, {"max_bytes": 0},
                   {"max_chunks": 0}, {"allowed_sample_rates": ()}):
        with pytest.raises(ar.AssetError) as exc:
            ar.AudioAssetRegistry(**kwargs)
        assert exc.value.code == "ASSET_CONFIG_INVALID"

    usage = ar.AudioAssetRegistry().usage()
    assert usage == {"assets": 0, "bytes": 0, "max_assets": 32,
                     "max_bytes": 1000000}


def test_mono_pcm_asset_is_probed_and_stored():
    registry = ar.AudioAssetRegistry()
    data = wav_bytes()
    result = registry.register("a1", data, tags=["music"])
    assert result["status"] == "registered"
    assert result["asset_id"] == "a1"
    assert result["canonical_id"] == "a1"
    assert result["duration_ms"] == 250
    assert result["frames"] == 2000
    assert result["sample_rate"] == 8000
    assert result["channels"] == 1
    assert result["bits"] == 16
    assert result["bytes"] == len(data) == 4044
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["tags"] == ["music"]

    stored = registry.get("a1")
    assert stored["duration_ms"] == 250
    assert stored["tags"] == ["music"]


def test_stereo_24_bit_asset_uses_block_align_for_duration():
    registry = ar.AudioAssetRegistry()
    result = registry.register(
        "wide", wav_bytes(channels=2, bits=24, rate=44100, frames=1600))
    assert result["channels"] == 2
    assert result["bits"] == 24
    assert result["sample_rate"] == 44100
    assert result["frames"] == 1600
    assert result["duration_ms"] == 36
    assert result["bytes"] == 44 + 9600


def test_non_bytes_and_broken_headers_are_refused():
    registry = ar.AudioAssetRegistry()
    with pytest.raises(ar.AssetError) as exc:
        registry.register("x", "not-a-byte-string")
    assert exc.value.code == "ASSET_BAD_BYTES"

    with pytest.raises(ar.AssetError) as exc:
        registry.register("x", b"RIFF")
    assert exc.value.code == "WAV_HEADER_INVALID"

    swapped = b"XXXX" + wav_bytes()[4:]
    with pytest.raises(ar.AssetError) as exc:
        registry.register("x", swapped)
    assert exc.value.code == "WAV_HEADER_INVALID"

    data = wav_bytes()
    oversized = data[:4] + struct.pack("<I", len(data) + 100) + data[8:]
    with pytest.raises(ar.AssetError) as exc:
        registry.register("x", oversized)
    assert exc.value.code == "WAV_TRUNCATED"


def test_missing_format_or_data_chunks_are_rejected():
    registry = ar.AudioAssetRegistry()
    with pytest.raises(ar.AssetError) as exc:
        registry.register("no-fmt", wav_bytes(omit_fmt=True))
    assert exc.value.code == "WAV_CHUNK_MISSING"

    with pytest.raises(ar.AssetError) as exc:
        registry.register("no-data", wav_bytes(omit_data=True))
    assert exc.value.code == "WAV_CHUNK_MISSING"


def test_unsupported_and_inconsistent_format_fields_are_refused():
    registry = ar.AudioAssetRegistry()
    cases = [
        (dict(audio_format=7), "WAV_UNSUPPORTED_FORMAT"),
        (dict(channels=0), "WAV_BAD_FORMAT"),
        (dict(channels=9), "WAV_BAD_FORMAT"),
        (dict(bits=12), "WAV_BAD_FORMAT"),
        (dict(block_align=3), "WAV_BAD_FORMAT"),
    ]
    for kwargs, code in cases:
        with pytest.raises(ar.AssetError) as exc:
            registry.register("bad", wav_bytes(**kwargs))
        assert exc.value.code == code
    assert registry.usage()["assets"] == 0


def test_sample_rate_allow_list_is_enforced():
    registry = ar.AudioAssetRegistry(allowed_sample_rates=(48000,))
    with pytest.raises(ar.AssetError) as exc:
        registry.register("low", wav_bytes(rate=8000))
    assert exc.value.code == "WAV_SAMPLE_RATE_REJECTED"

    accepted = registry.register("high", wav_bytes(rate=48000, frames=4800))
    assert accepted["duration_ms"] == 100


def test_payload_without_complete_frame_is_refused():
    registry = ar.AudioAssetRegistry()
    with pytest.raises(ar.AssetError) as exc:
        registry.register("zero", wav_bytes(frames=0))
    assert exc.value.code == "WAV_EMPTY_PAYLOAD"

    with pytest.raises(ar.AssetError) as exc:
        registry.register("partial", wav_bytes(frames=1, data_size=1))
    assert exc.value.code == "WAV_EMPTY_PAYLOAD"


def test_odd_sized_chunks_are_padded_and_truncation_is_detected():
    registry = ar.AudioAssetRegistry()
    padded = wav_bytes(extra=((b"LIST", b"abc"),))
    result = registry.register("odd", padded)
    assert result["duration_ms"] == 250

    fmt = chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16))
    body = fmt + b"data" + struct.pack("<I", 500) + b"\x00" * 10
    truncated = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body
    with pytest.raises(ar.AssetError) as exc:
        registry.register("cut", truncated)
    assert exc.value.code == "WAV_TRUNCATED"


def test_chunk_count_is_capped_while_unknown_chunks_stay_ignorable():
    limited = ar.AudioAssetRegistry(max_chunks=3)
    crowded = wav_bytes(extra=((b"JUNK", b"abcd"), (b"LIST", b"ab")))
    with pytest.raises(ar.AssetError) as exc:
        limited.register("crowded", crowded)
    assert exc.value.code == "WAV_CHUNK_LIMIT"

    lenient = ar.AudioAssetRegistry()
    accepted = lenient.register("crowded", crowded)
    assert accepted["duration_ms"] == 250


def test_identical_content_is_deduped_to_the_first_asset():
    registry = ar.AudioAssetRegistry()
    data = wav_bytes()
    first = registry.register("a", data)
    assert first["canonical_id"] == "a"

    duplicate = registry.register("b", data)
    assert duplicate["status"] == "duplicate"
    assert duplicate["asset_id"] == "b"
    assert duplicate["canonical_id"] == "a"

    usage = registry.usage()
    assert usage["assets"] == 1
    assert usage["bytes"] == len(data)
    assert registry.stats()["registered"] == 1
    assert registry.stats()["duplicates"] == 1

    with pytest.raises(ar.AssetError) as exc:
        registry.get("b")
    assert exc.value.code == "ASSET_UNKNOWN_ASSET"

    removed = registry.remove("a")
    assert removed["removed"] is True
    assert removed["bytes_freed"] == len(data)
    assert registry.usage()["assets"] == 0


def test_library_count_and_byte_budgets_are_both_enforced():
    one_slot = ar.AudioAssetRegistry(max_assets=1)
    one_slot.register("a", wav_bytes(frames=100))
    with pytest.raises(ar.AssetError) as exc:
        one_slot.register("b", wav_bytes(frames=101))
    assert exc.value.code == "ASSET_LIBRARY_FULL"
    assert one_slot.usage()["assets"] == 1
    assert one_slot.stats()["rejected"] == 1

    tight = ar.AudioAssetRegistry(max_assets=10, max_bytes=5000)
    tight.register("a", wav_bytes(frames=2000))
    with pytest.raises(ar.AssetError) as exc:
        tight.register("b", wav_bytes(frames=2001))
    assert exc.value.code == "ASSET_LIBRARY_FULL"
    assert tight.usage()["assets"] == 1


def test_tags_listing_and_removal_behave_idempotently():
    registry = ar.AudioAssetRegistry()
    registry.register("a", wav_bytes(frames=100), tags=["music"])
    registry.register("b", wav_bytes(frames=200), tags=["music"])
    assert registry.list_assets() == ["a", "b"]
    assert registry.list_assets(tag="music") == ["a", "b"]

    tagged = registry.add_tag("a", "sfx")
    assert tagged["tags"] == ["music", "sfx"]
    again = registry.add_tag("a", "sfx")
    assert again["tags"] == ["music", "sfx"]
    assert registry.list_assets(tag="sfx") == ["a"]

    removed = registry.remove_tag("a", "sfx")
    assert removed["tags"] == ["music"]
    with pytest.raises(ar.AssetError) as exc:
        registry.remove_tag("a", "sfx")
    assert exc.value.code == "ASSET_UNKNOWN_TAG"
    with pytest.raises(ar.AssetError) as exc:
        registry.add_tag("ghost", "x")
    assert exc.value.code == "ASSET_UNKNOWN_ASSET"
    with pytest.raises(ar.AssetError) as exc:
        registry.get("ghost")
    assert exc.value.code == "ASSET_UNKNOWN_ASSET"

    registry.remove("b")
    assert registry.list_assets() == ["a"]


def test_snapshot_restore_replays_probe_results_and_tags():
    registry = ar.AudioAssetRegistry()
    data = wav_bytes()
    registry.register("a", data, tags=["keep"])
    state = registry.snapshot()
    assert state["version"] == 1

    registry.register("b", wav_bytes(frames=300))
    registry.remove("a")

    restored = ar.AudioAssetRegistry()
    restored.restore(state)
    info = restored.get("a")
    assert info["duration_ms"] == 250
    assert info["tags"] == ["keep"]
    assert restored.list_assets() == ["a"]
    checked = restored.verify("a")
    assert checked["valid"] is True
    assert checked["sha256"] == hashlib.sha256(data).hexdigest()
    assert restored.stats()["registered"] == 1

    state["assets"].clear()
    assert restored.get("a")["asset_id"] == "a"

    victim = ar.AudioAssetRegistry()
    victim.register("keep", data)
    bad_states = [
        None,
        {},
        {"version": 2, "assets": {},
         "stats": {"registered": 0, "duplicates": 0, "removed": 0,
                   "rejected": 0}},
        {"version": 1, "assets": [],
         "stats": {"registered": 0, "duplicates": 0, "removed": 0,
                   "rejected": 0}},
        {"version": 1, "assets": {"x": {"data": "!!!not-base64", "tags": []}},
         "stats": {"registered": 0, "duplicates": 0, "removed": 0,
                   "rejected": 0}},
        {"version": 1, "assets": {},
         "stats": {"registered": "many", "duplicates": 0, "removed": 0,
                   "rejected": 0}},
    ]
    for bad in bad_states:
        with pytest.raises(ar.AssetError) as exc:
            victim.restore(bad)
        assert exc.value.code == "ASSET_SNAPSHOT_INVALID"

    assert victim.get("keep")["asset_id"] == "keep"
    assert victim.usage()["assets"] == 1


def test_concurrent_upload_of_same_bytes_registers_exactly_once():
    registry = ar.AudioAssetRegistry()
    data = wav_bytes()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = registry.register(f"id-{index}", data)
        except Exception as exc:  # pragma: no cover - surfaced via assertions
            out = {"status": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["duplicate"] * 7 + ["registered"]
    canonical = {row["canonical_id"] for row in results}
    registered_row = [row for row in results if row["status"] == "registered"][0]
    assert canonical == {registered_row["asset_id"]}
    assert registry.usage()["assets"] == 1
    assert registry.stats()["registered"] == 1
    assert registry.stats()["duplicates"] == 7
