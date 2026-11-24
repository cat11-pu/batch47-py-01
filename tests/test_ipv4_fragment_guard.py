import threading

import pytest

import ipv4_fragment_guard as fg

NOW = "2026-11-23T10:00:00+00:00"
EMPTY_STATS = {"accepted": 0, "duplicates": 0, "completed": 0, "rejected": 0,
               "dropped": 0, "expired": 0}


def frag(**overrides):
    base = {
        "src": "10.0.0.1",
        "dst": "10.0.0.2",
        "id": 7,
        "protocol": 17,
        "offset": 0,
        "payload": "A" * 16,
        "mf": True,
    }
    base.update(overrides)
    return base


def make(**kwargs):
    return fg.Ipv4ReassemblyGuard(**kwargs)


def test_invalid_address_ident_and_protocol_are_rejected():
    guard = make()
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(src="10.0.0.999"), now=NOW)
    assert exc.value.code == "FRAG_INVALID_ADDRESS"
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(id=70000), now=NOW)
    assert exc.value.code == "FRAG_INVALID_ID"
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(protocol=0), now=NOW)
    assert exc.value.code == "FRAG_INVALID_PROTOCOL"
    assert guard.pending_count() == 0
    assert guard.stats()["rejected"] == 3


def test_bad_length_and_oversize_fragments_are_refused():
    guard = make(max_datagram_bytes=64)
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(payload="A" * 10, mf=True), now=NOW)
    assert exc.value.code == "FRAG_BAD_LENGTH"
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(payload="", mf=False), now=NOW)
    assert exc.value.code == "FRAG_BAD_LENGTH"
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(offset=8, payload="B" * 8, mf=False), now=NOW)
    assert exc.value.code == "FRAG_TOO_LARGE"
    assert guard.pending_count() == 0
    assert guard.stats()["rejected"] == 3


def test_partial_datagram_is_visible_then_completes():
    guard = make()
    first = guard.accept(frag(), now=NOW)
    assert first == {"status": "partial", "src": "10.0.0.1", "dst": "10.0.0.2",
                     "id": 7, "fragments": 1, "bytes": 16}

    view = guard.datagram("10.0.0.1", "10.0.0.2", 7)
    assert view == {"src": "10.0.0.1", "dst": "10.0.0.2", "id": 7,
                    "protocol": 17, "fragments": 1, "bytes": 16,
                    "total_len": None, "first_seen": NOW}
    assert guard.pending_count() == 1
    assert guard.pending_keys() == [("10.0.0.1", "10.0.0.2", 7)]
    assert guard.bytes_held() == 16

    done = guard.accept(frag(offset=2, payload="B" * 8, mf=False), now=NOW)
    assert done == {"status": "complete", "src": "10.0.0.1", "dst": "10.0.0.2",
                    "id": 7, "total_len": 24, "fragments": 2}
    assert guard.pending_count() == 0
    assert guard.bytes_held() == 0
    assert guard.datagram("10.0.0.1", "10.0.0.2", 7) is None
    assert guard.stats() == {"accepted": 2, "duplicates": 0, "completed": 1,
                             "rejected": 0, "dropped": 0, "expired": 0}


def test_out_of_order_fragments_still_complete():
    guard = make()
    tail = guard.accept(frag(offset=2, payload="B" * 8, mf=False), now=NOW)
    assert tail["status"] == "partial"
    assert guard.datagram("10.0.0.1", "10.0.0.2", 7)["total_len"] == 24
    done = guard.accept(frag(offset=0, payload="A" * 16, mf=True), now=NOW)
    assert done["status"] == "complete"
    assert done["total_len"] == 24
    assert guard.stats()["completed"] == 1


def test_gap_keeps_datagram_incomplete():
    guard = make()
    guard.accept(frag(offset=0, payload="A" * 8, mf=True), now=NOW)
    guard.accept(frag(offset=2, payload="C" * 8, mf=False), now=NOW)
    view = guard.datagram("10.0.0.1", "10.0.0.2", 7)
    assert view["fragments"] == 2
    assert view["bytes"] == 16
    assert view["total_len"] == 24
    assert guard.pending_count() == 1
    done = guard.accept(frag(offset=1, payload="B" * 8, mf=True), now=NOW)
    assert done["status"] == "complete"
    assert done["fragments"] == 3


def test_identical_fragment_is_an_idempotent_duplicate():
    guard = make()
    guard.accept(frag(), now=NOW)
    again = guard.accept(frag(), now=NOW)
    assert again == {"status": "duplicate", "src": "10.0.0.1", "dst": "10.0.0.2",
                     "id": 7, "fragments": 1, "bytes": 16}
    assert guard.bytes_held() == 16
    assert guard.stats() == {"accepted": 1, "duplicates": 1, "completed": 0,
                             "rejected": 0, "dropped": 0, "expired": 0}


def test_overlapping_fragments_with_conflicting_payload_are_rejected():
    guard = make()
    guard.accept(frag(), now=NOW)
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(payload="C" * 16), now=NOW)
    assert exc.value.code == "FRAG_OVERLAP"
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(offset=1, payload="B" * 8, mf=False), now=NOW)
    assert exc.value.code == "FRAG_OVERLAP"
    assert guard.pending_count() == 1
    assert guard.bytes_held() == 16
    assert guard.stats()["rejected"] == 2


def test_protocol_change_inside_one_datagram_is_rejected():
    guard = make()
    guard.accept(frag(), now=NOW)
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(protocol=6, offset=2, payload="B" * 8, mf=False), now=NOW)
    assert exc.value.code == "FRAG_PROTOCOL_MISMATCH"
    assert guard.datagram("10.0.0.1", "10.0.0.2", 7)["protocol"] == 17


def test_fragment_count_limit_drops_the_whole_datagram():
    guard = make(max_fragments=2)
    assert guard.accept(frag(offset=0, payload="A" * 8, mf=True), now=NOW)["status"] == "partial"
    assert guard.accept(frag(offset=1, payload="B" * 8, mf=True), now=NOW)["status"] == "partial"
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(offset=2, payload="C" * 8, mf=True), now=NOW)
    assert exc.value.code == "FRAG_LIMIT"
    assert guard.pending_count() == 0
    assert guard.bytes_held() == 0
    assert guard.stats()["dropped"] == 1
    assert guard.stats()["accepted"] == 2


def test_pending_table_capacity_and_discard():
    guard = make(max_pending=2)
    guard.accept(frag(id=1, offset=0, payload="A" * 8, mf=True), now=NOW)
    guard.accept(frag(id=2, offset=0, payload="A" * 8, mf=True), now=NOW)
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(id=3, offset=0, payload="A" * 8, mf=True), now=NOW)
    assert exc.value.code == "FRAG_TABLE_FULL"
    assert guard.pending_count() == 2
    assert guard.pending_keys() == [("10.0.0.1", "10.0.0.2", 1),
                                    ("10.0.0.1", "10.0.0.2", 2)]

    done = guard.accept(frag(id=1, offset=1, payload="B" * 8, mf=False), now=NOW)
    assert done["status"] == "complete"
    assert guard.discard("10.0.0.1", "10.0.0.2", 2) is True
    assert guard.discard("10.0.0.1", "10.0.0.2", 2) is False
    assert guard.pending_count() == 0


def test_source_prefix_limit_applies_only_to_new_datagrams():
    guard = make()
    assert guard.limit_source("10.0.0.0/8", max_pending=1) == {
        "prefix": "10.0.0.0/8", "max_pending": 1}
    guard.accept(frag(src="10.1.1.1", id=1, offset=0, payload="A" * 8, mf=True), now=NOW)
    with pytest.raises(fg.FragmentError) as exc:
        guard.accept(frag(src="10.2.2.2", id=2, offset=0, payload="A" * 8, mf=True), now=NOW)
    assert exc.value.code == "FRAG_SOURCE_LIMIT"
    ok = guard.accept(frag(src="192.0.2.7", id=3, offset=0, payload="A" * 8, mf=True), now=NOW)
    assert ok["status"] == "partial"
    assert guard.pending_count() == 2


def test_timeout_boundary_and_stats_reset():
    guard = make(timeout=30)
    guard.accept(frag(), now=NOW)
    assert guard.expire(now="2026-11-23T10:00:29+00:00") == 0
    assert guard.pending_count() == 1
    assert guard.expire(now="2026-11-23T10:00:30+00:00") == 1
    assert guard.pending_count() == 0
    assert guard.stats()["expired"] == 1
    guard.reset_stats()
    assert guard.stats() == EMPTY_STATS


def test_stale_datagram_is_reaped_and_restarted_on_accept():
    guard = make(timeout=30)
    guard.accept(frag(id=9, offset=0, payload="A" * 8, mf=True), now=NOW)
    restarted = guard.accept(frag(id=9, offset=1, payload="B" * 8, mf=False),
                             now="2026-11-23T10:00:31+00:00")
    assert restarted["status"] == "partial"
    assert restarted["fragments"] == 1
    view = guard.datagram("10.0.0.1", "10.0.0.2", 9)
    assert view["total_len"] == 16
    assert view["first_seen"] == "2026-11-23T10:00:31+00:00"
    assert guard.stats()["expired"] == 1
    assert guard.stats()["accepted"] == 2


def test_snapshot_restore_and_corrupt_state_rejected():
    guard = make()
    guard.accept(frag(id=11, offset=0, payload="A" * 16, mf=True), now=NOW)
    state = guard.snapshot()
    guard.accept(frag(id=11, offset=2, payload="B" * 8, mf=False), now=NOW)

    restored = fg.Ipv4ReassemblyGuard()
    restored.restore(state)
    view = restored.datagram("10.0.0.1", "10.0.0.2", 11)
    assert view["fragments"] == 1
    assert view["bytes"] == 16
    assert view["total_len"] is None
    done = restored.accept(frag(id=11, offset=2, payload="B" * 8, mf=False), now=NOW)
    assert done["status"] == "complete"
    assert done["total_len"] == 24
    assert guard.pending_count() == 0

    keeper = make()
    keeper.accept(frag(id=12, offset=0, payload="K" * 8, mf=True), now=NOW)
    bad_states = [
        None,
        {},
        {"version": 2, "pending": {}, "source_limits": {}, "stats": {},
         "max_datagram_bytes": 1, "max_fragments": 1, "timeout": 1,
         "max_pending": 1},
        dict(state, pending=[]),
        dict(state, stats="nope"),
    ]
    for bad in bad_states:
        with pytest.raises(fg.FragmentError) as exc:
            keeper.restore(bad)
        assert exc.value.code == "FRAG_SNAPSHOT_INVALID"
    assert keeper.pending_count() == 1
    assert keeper.datagram("10.0.0.1", "10.0.0.2", 12)["fragments"] == 1


def test_concurrent_fragments_complete_exactly_once():
    guard = make(max_fragments=16)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            row = guard.accept(frag(offset=index, payload=chr(65 + index) * 8,
                                    mf=index != 7), now=NOW)
            outcome = row["status"]
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            outcome = "error:" + type(exc).__name__
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["complete"] + ["partial"] * 7
    assert guard.pending_count() == 0
    assert guard.bytes_held() == 0
    assert guard.stats() == {"accepted": 8, "duplicates": 0, "completed": 1,
                             "rejected": 0, "dropped": 0, "expired": 0}
