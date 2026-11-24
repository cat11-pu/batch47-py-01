import threading
from datetime import datetime, timedelta, timezone

import pytest

import frag_reassembly_guard as fr

BASE = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def frag(**overrides):
    base = {"src": "198.51.100.10", "dst": "203.0.113.5", "ident": 4242,
            "protocol": 17, "offset": 0, "payload_len": 8,
            "more_fragments": True, "digest": "a"}
    base.update(overrides)
    return base


def make_guard(**kwargs):
    params = dict(max_datagrams=8, max_fragments_per_datagram=8,
                  reassembly_timeout=30, min_fragment_payload=8,
                  max_datagram_bytes=1500, auto_block_threshold=2)
    params.update(kwargs)
    return fr.FragGuard(**params)


def test_out_of_order_fragments_assemble_once_and_bytes_are_exact():
    guard = make_guard()
    first = guard.submit(frag(offset=8, payload_len=8, more_fragments=False),
                         now=at(0))
    assert first["status"] == "buffered"
    assert first["fragments"] == 1
    assert guard.pending_count() == 1
    second = guard.submit(frag(offset=0, payload_len=8, more_fragments=True),
                          now=at(1))
    assert second["status"] == "complete"
    assert second["bytes"] == 16
    assert second["fragments"] == 2
    assert guard.pending_count() == 0
    assert guard.stats() == {"fragments": 2, "completed": 1, "duplicates": 0,
                             "violations": 0, "dropped": 0}


def test_gap_prevents_completion_until_middle_fragment_arrives():
    guard = make_guard()
    assert guard.submit(frag(offset=0), now=at(0))["status"] == "buffered"
    assert guard.submit(frag(offset=16, more_fragments=False),
                        now=at(1))["status"] == "buffered"
    assert guard.pending_count() == 1
    last = guard.submit(frag(offset=8), now=at(2))
    assert last["status"] == "complete"
    assert last["bytes"] == 24
    assert last["fragments"] == 3


def test_exact_duplicate_is_idempotent_but_digest_conflict_is_overlap():
    guard = make_guard()
    guard.submit(frag(offset=0, payload_len=8, digest="aa"), now=at(0))
    duplicate = guard.submit(frag(offset=0, payload_len=8, digest="aa"),
                             now=at(1))
    assert duplicate["status"] == "duplicate"
    assert duplicate["fragments"] == 1
    assert guard.stats()["duplicates"] == 1
    assert guard.stats()["fragments"] == 1
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=0, payload_len=8, digest="bb"), now=at(2))
    assert exc.value.code == "FRAG_OVERLAP"
    assert guard.stats()["violations"] == 1


def test_partial_overlap_is_refused():
    guard = make_guard()
    guard.submit(frag(offset=0, payload_len=8), now=at(0))
    guard.submit(frag(offset=8, payload_len=8), now=at(1))
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=0, payload_len=16), now=at(2))
    assert exc.value.code == "FRAG_OVERLAP"
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=8, payload_len=16, more_fragments=False),
                     now=at(3))
    assert exc.value.code == "FRAG_OVERLAP"


def test_offset_alignment_and_tiny_fragment_policy():
    guard = make_guard(auto_block_threshold=10)
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=12), now=at(0))
    assert exc.value.code == "FRAG_INVALID_OFFSET"
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=1496, payload_len=8, more_fragments=False),
                     now=at(0))
    assert exc.value.code == "FRAG_INVALID_OFFSET"
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=0, payload_len=4), now=at(0))
    assert exc.value.code == "FRAG_TOO_SMALL"
    final = guard.submit(frag(offset=0, payload_len=4, more_fragments=False),
                         now=at(0))
    assert final["status"] == "complete"
    assert final["bytes"] == 4
    assert guard.stats()["violations"] == 3


def test_fragment_count_limit_flushes_the_datagram():
    guard = make_guard(max_fragments_per_datagram=3)
    for offset in (0, 8, 16):
        guard.submit(frag(offset=offset), now=at(0))
    assert guard.pending_count() == 1
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=24), now=at(0))
    assert exc.value.code == "FRAG_TOO_MANY"
    assert guard.pending_count() == 0
    assert guard.stats()["violations"] == 1


def test_repeated_violations_auto_block_source():
    guard = make_guard(auto_block_threshold=2)
    for _ in range(2):
        with pytest.raises(fr.FragError) as exc:
            guard.submit(frag(offset=0, payload_len=4), now=at(0))
        assert exc.value.code == "FRAG_TOO_SMALL"
    assert guard.is_blocked("198.51.100.10") is True
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(), now=at(1))
    assert exc.value.code == "FRAG_SOURCE_BLOCKED"
    assert guard.stats()["dropped"] == 1
    assert guard.unblock_source("198.51.100.10") is True
    assert guard.unblock_source("198.51.100.10") is False
    assert guard.submit(frag(), now=at(2))["status"] == "buffered"


def test_datagram_capacity_and_expiry():
    guard = make_guard(max_datagrams=2, reassembly_timeout=30)
    guard.submit(frag(ident=1), now=at(0))
    guard.submit(frag(ident=2), now=at(0))
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(ident=3), now=at(1))
    assert exc.value.code == "FRAG_TABLE_FULL"
    assert guard.pending_count() == 2
    assert guard.expire(now=at(29)) == 0
    assert guard.expire(now=at(31)) == 2
    assert guard.pending_count() == 0
    assert guard.submit(frag(ident=3), now=at(31))["status"] == "buffered"


def test_fragment_after_completion_is_refused_until_retention_expires():
    guard = make_guard(auto_block_threshold=10, reassembly_timeout=30)
    done = guard.submit(frag(offset=0, more_fragments=False), now=at(0))
    assert done["status"] == "complete"
    assert done["bytes"] == 8
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=0, more_fragments=False), now=at(1))
    assert exc.value.code == "FRAG_AFTER_COMPLETE"
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(offset=8, more_fragments=False), now=at(2))
    assert exc.value.code == "FRAG_AFTER_COMPLETE"
    assert guard.expire(now=at(31)) == 0
    again = guard.submit(frag(offset=0, more_fragments=False), now=at(31))
    assert again["status"] == "complete"
    assert guard.stats()["completed"] == 2


def test_snapshot_restore_keeps_pending_and_blocklist():
    guard = make_guard()
    guard.submit(frag(ident=7), now=at(0))
    guard.block_source("203.0.113.99", reason="abuse")
    state = guard.snapshot()
    assert state["version"] == 1
    assert len(state["pending"]) == 1
    guard.submit(frag(ident=8), now=at(1))
    restored = fr.FragGuard()
    restored.restore(state)
    assert restored.pending_count() == 1
    assert [row["ident"] for row in restored.list_pending()] == [7]
    assert restored.is_blocked("203.0.113.99") is True
    assert restored.stats()["fragments"] == 1
    with pytest.raises(fr.FragError) as exc:
        restored.submit(frag(src="203.0.113.99", ident=9), now=at(2))
    assert exc.value.code == "FRAG_SOURCE_BLOCKED"


def test_corrupt_snapshot_is_rejected_without_state_change():
    guard = make_guard()
    guard.submit(frag(ident=11), now=at(0))
    bad_states = [
        None,
        {},
        {"version": 2, "pending": {}, "completed": {}, "blocked": {},
         "stats": {}},
        {"version": 1, "pending": [], "completed": {}, "blocked": {},
         "stats": {}},
        {"version": 1, "pending": {}, "completed": {}, "blocked": [],
         "stats": {}},
        {"version": 1, "pending": {}, "completed": {}, "blocked": {},
         "stats": "nope"},
    ]
    for state in bad_states:
        with pytest.raises(fr.FragError) as exc:
            guard.restore(state)
        assert exc.value.code == "FRAG_SNAPSHOT_INVALID"
    assert guard.pending_count() == 1


def test_pending_listing_and_flush_by_key():
    guard = make_guard()
    guard.submit(frag(ident=21), now=at(0))
    guard.submit(frag(ident=22), now=at(0))
    rows = guard.list_pending()
    assert [row["ident"] for row in rows] == [21, 22]
    key21 = guard.datagram_key(frag(ident=21))
    assert guard.flush(key21) is True
    assert guard.flush(key21) is False
    assert guard.pending_count() == 1
    key22 = guard.datagram_key(frag(ident=22))
    assert guard.flush(key22) is True
    assert guard.pending_count() == 0


def test_datagrams_are_isolated_by_source():
    guard = make_guard()
    done_a = guard.submit(frag(src="198.51.100.10", ident=5, offset=0,
                               more_fragments=False), now=at(0))
    assert done_a["status"] == "complete"
    first_b = guard.submit(frag(src="198.51.100.11", ident=5, offset=0),
                           now=at(1))
    assert first_b["status"] == "buffered"
    assert guard.pending_count() == 1
    second_b = guard.submit(frag(src="198.51.100.11", ident=5, offset=8,
                                 more_fragments=False), now=at(2))
    assert second_b["status"] == "complete"
    assert second_b["bytes"] == 16


def test_concurrent_fragments_complete_exactly_once():
    guard = make_guard()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(offset):
        item = frag(offset=offset, payload_len=8,
                    more_fragments=offset != 56, digest=f"d{offset}")
        barrier.wait()
        try:
            out = guard.submit(item, now=at(0))
        except Exception as exc:
            out = {"status": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(offset,))
               for offset in range(0, 64, 8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    statuses = sorted(row["status"] for row in results)
    assert statuses == ["buffered"] * 7 + ["complete"]
    assert guard.stats()["fragments"] == 8
    assert guard.stats()["completed"] == 1
    assert guard.pending_count() == 0


def test_invalid_fragments_are_rejected_and_stats_reset():
    guard = make_guard(auto_block_threshold=10)
    with pytest.raises(fr.FragError) as exc:
        guard.submit({}, now=at(0))
    assert exc.value.code == "FRAG_INVALID"
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(src="999.1.1.1"), now=at(0))
    assert exc.value.code == "FRAG_INVALID"
    with pytest.raises(fr.FragError) as exc:
        guard.submit(frag(protocol=999), now=at(0))
    assert exc.value.code == "FRAG_INVALID"
    guard.submit(frag(offset=0, payload_len=8), now=at(1))
    duplicate = guard.submit(frag(offset=0, payload_len=8), now=at(2))
    assert duplicate["status"] == "duplicate"
    assert guard.stats() == {"fragments": 1, "completed": 0, "duplicates": 1,
                             "violations": 0, "dropped": 0}
    guard.reset_stats()
    assert guard.stats() == {"fragments": 0, "completed": 0, "duplicates": 0,
                             "violations": 0, "dropped": 0}
