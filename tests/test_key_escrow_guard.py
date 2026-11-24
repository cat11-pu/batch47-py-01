import hashlib
import threading

import pytest

import key_escrow_guard as eg

SECRET = b"\x00\x01escrowed-signing-key\xff\x10"

CUSTODIANS = ("alice", "bob", "carol", "dave", "erin")


def make_guard(**kwargs):
    return eg.KeyEscrowGuard(**kwargs)


def make_escrow(guard, secret_id="key-1", material=SECRET, custodians=CUSTODIANS,
                threshold=3):
    return guard.create_escrow(secret_id, key_material=material,
                               custodians=custodians, threshold=threshold)


def quorum(info, names):
    return {name: info["shares"][name] for name in names}


def test_create_escrow_exposes_digest_and_status():
    guard = make_guard()
    info = make_escrow(guard)
    assert info["secret_id"] == "key-1"
    assert info["version"] == 1
    assert info["threshold"] == 3
    assert sorted(info["shares"]) == list(CUSTODIANS)
    assert info["digest"] == hashlib.sha256(SECRET).hexdigest()
    assert info["size"] == len(SECRET)

    status = guard.status("key-1")
    assert status["custodians"] == list(CUSTODIANS)
    assert status["retired"] is False
    assert "shares" not in status
    assert guard.is_retired("key-1") is False
    assert guard.stats()["created"] == 1


def test_reconstruct_accepts_any_quorum_subset_and_is_repeatable():
    guard = make_guard()
    info = make_escrow(guard)
    for names in (("alice", "bob", "carol"),
                  ("bob", "dave", "erin"),
                  ("carol", "dave", "alice"),
                  CUSTODIANS):
        assert guard.reconstruct("key-1", shares_by_custodian=quorum(info, names)) == SECRET
    assert guard.reconstruct("key-1", shares_by_custodian=quorum(info, CUSTODIANS)) == SECRET
    assert guard.stats()["reconstructed"] == 5


def test_reconstruct_below_threshold_reports_quorum_short():
    guard = make_guard()
    info = make_escrow(guard)
    with pytest.raises(eg.EscrowError) as exc:
        guard.reconstruct("key-1", shares_by_custodian=quorum(info, ("alice", "bob")))
    assert exc.value.code == "ESCROW_QUORUM_SHORT"
    assert guard.stats()["failed"] == 1
    assert guard.stats()["reconstructed"] == 0


def test_create_rejects_invalid_threshold_and_custodian_sets():
    guard = make_guard()
    for threshold in (1, 6):
        with pytest.raises(eg.EscrowError) as exc:
            make_escrow(guard, threshold=threshold)
        assert exc.value.code == "ESCROW_THRESHOLD_INVALID"

    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, custodians=("alice",))
    assert exc.value.code == "ESCROW_CUSTODIAN_INVALID"

    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, custodians=("alice", "alice", "bob"))
    assert exc.value.code == "ESCROW_CUSTODIAN_DUPLICATE"

    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, custodians=("alice", "", "bob"))
    assert exc.value.code == "ESCROW_CUSTODIAN_INVALID"


def test_create_rejects_duplicate_secret_and_capacity():
    guard = make_guard(max_secrets=1)
    make_escrow(guard, secret_id="only")
    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, secret_id="only")
    assert exc.value.code == "ESCROW_DUPLICATE_SECRET"

    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, secret_id="second")
    assert exc.value.code == "ESCROW_CAPACITY_FULL"
    assert sorted(guard.list_escrows()[0]) == [
        "custodians", "digest", "retired", "secret_id", "size", "threshold", "version"]


def test_create_rejects_empty_and_oversized_key_material():
    guard = make_guard(max_share_bytes=8)
    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, material=b"")
    assert exc.value.code == "ESCROW_SECRET_INVALID"

    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, material=b"x" * 9)
    assert exc.value.code == "ESCROW_SECRET_TOO_LARGE"

    with pytest.raises(eg.EscrowError) as exc:
        make_escrow(guard, material="not-bytes")
    assert exc.value.code == "ESCROW_SECRET_INVALID"


def test_reconstruct_rejects_unknown_custodian_and_tampered_share():
    guard = make_guard()
    info = make_escrow(guard)
    with pytest.raises(eg.EscrowError) as exc:
        guard.reconstruct("key-1", shares_by_custodian={
            "alice": info["shares"]["alice"],
            "bob": info["shares"]["bob"],
            "mallory": info["shares"]["carol"],
        })
    assert exc.value.code == "ESCROW_CUSTODIAN_UNKNOWN"

    tampered = dict(quorum(info, ("alice", "bob", "carol")))
    tampered["alice"] = tampered["alice"][:-1] + (
        "0" if tampered["alice"][-1] != "0" else "1")
    with pytest.raises(eg.EscrowError) as exc:
        guard.reconstruct("key-1", shares_by_custodian=tampered)
    assert exc.value.code == "ESCROW_SHARE_INVALID"
    assert guard.reconstruct("key-1", shares_by_custodian=quorum(
        info, ("alice", "bob", "carol"))) == SECRET


def test_verify_share_distinguishes_current_tampered_and_unknown():
    guard = make_guard()
    info = make_escrow(guard)
    assert guard.verify_share("key-1", custodian="alice",
                              share=info["shares"]["alice"]) is True
    assert guard.verify_share("key-1", custodian="alice",
                              share="v1:1:00ff") is False
    assert guard.verify_share("key-1", custodian="alice",
                              share="garbage") is False
    with pytest.raises(eg.EscrowError) as exc:
        guard.verify_share("key-1", custodian="nobody",
                           share=info["shares"]["alice"])
    assert exc.value.code == "ESCROW_CUSTODIAN_UNKNOWN"


def test_rotate_replaces_custody_and_invalidates_old_shares():
    guard = make_guard()
    info = make_escrow(guard)
    rotated = guard.rotate_escrow(
        "key-1",
        shares_by_custodian=quorum(info, ("alice", "bob", "carol")),
        custodians=("alice", "bob", "frank", "grace"),
        threshold=2,
    )
    assert rotated["version"] == 2
    assert rotated["threshold"] == 2
    assert rotated["custodians"] == ["alice", "bob", "frank", "grace"]
    assert rotated["digest"] == info["digest"]
    assert guard.status("key-1")["version"] == 2
    assert guard.reconstruct("key-1", shares_by_custodian=quorum(
        rotated, ("frank", "grace"))) == SECRET

    with pytest.raises(eg.EscrowError) as exc:
        guard.reconstruct("key-1", shares_by_custodian=quorum(
            info, ("alice", "bob", "carol")))
    assert exc.value.code == "ESCROW_VERSION_MISMATCH"
    assert guard.verify_share("key-1", custodian="alice",
                              share=info["shares"]["alice"]) is False
    assert guard.verify_share("key-1", custodian="alice",
                              share=rotated["shares"]["alice"]) is True
    with pytest.raises(eg.EscrowError) as exc:
        guard.verify_share("key-1", custodian="carol",
                           share=info["shares"]["carol"])
    assert exc.value.code == "ESCROW_CUSTODIAN_UNKNOWN"
    assert guard.stats()["rotated"] == 1


def test_failed_rotation_keeps_previous_version_usable():
    guard = make_guard()
    info = make_escrow(guard)
    with pytest.raises(eg.EscrowError) as exc:
        guard.rotate_escrow(
            "key-1",
            shares_by_custodian=quorum(info, ("alice", "bob")),
            custodians=("alice", "bob", "carol"),
        )
    assert exc.value.code == "ESCROW_QUORUM_SHORT"
    assert guard.status("key-1")["version"] == 1
    assert guard.reconstruct("key-1", shares_by_custodian=quorum(
        info, ("alice", "bob", "carol"))) == SECRET


def test_retire_is_idempotent_and_shreds_custody():
    guard = make_guard()
    info = make_escrow(guard)
    first = guard.retire_escrow("key-1")
    assert first == {"secret_id": "key-1", "retired": True,
                     "already": False, "version": 1}
    second = guard.retire_escrow("key-1")
    assert second["already"] is True
    assert second["retired"] is True
    assert guard.status("key-1")["custodians"] == []
    assert guard.is_retired("key-1") is True
    with pytest.raises(eg.EscrowError) as exc:
        guard.reconstruct("key-1", shares_by_custodian=quorum(
            info, ("alice", "bob", "carol")))
    assert exc.value.code == "ESCROW_RETIRED"
    with pytest.raises(eg.EscrowError) as exc:
        guard.verify_share("key-1", custodian="alice",
                           share=info["shares"]["alice"])
    assert exc.value.code == "ESCROW_RETIRED"
    assert guard.stats()["retired"] == 1


def test_concurrent_rotation_has_exactly_one_winner():
    guard = make_guard()
    info = make_escrow(guard)
    old_shares = quorum(info, ("alice", "bob", "carol"))
    barrier = threading.Barrier(4)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            out = guard.rotate_escrow(
                "key-1", shares_by_custodian=dict(old_shares),
                custodians=("alice", "bob", "carol"),
                threshold=3)
        except eg.EscrowError as exc:
            out = exc.code
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [row for row in results if isinstance(row, dict)]
    assert len(winners) == 1
    assert sorted(row for row in results if isinstance(row, str)) == [
        "ESCROW_VERSION_MISMATCH"] * 3
    assert winners[0]["version"] == 2
    assert guard.status("key-1")["version"] == 2
    assert guard.reconstruct("key-1", shares_by_custodian=quorum(
        winners[0], ("alice", "bob", "carol"))) == SECRET


def test_snapshot_is_detached_and_restore_recovers_state():
    guard = make_guard()
    info = make_escrow(guard)
    guard.reconstruct("key-1", shares_by_custodian=quorum(
        info, ("alice", "bob", "carol")))
    state = guard.snapshot()
    state["records"]["key-1"]["threshold"] = 99
    state["stats"]["created"] = 999
    assert guard.status("key-1")["threshold"] == 3
    assert guard.stats()["created"] == 1

    fresh = make_guard()
    fresh.restore(guard.snapshot())
    assert fresh.status("key-1")["threshold"] == 3
    assert fresh.reconstruct("key-1", shares_by_custodian=quorum(
        info, ("alice", "dave", "erin"))) == SECRET
    assert fresh.stats()["reconstructed"] == 2
    assert fresh.audit()[-1]["event"] == "reconstructed"


def test_corrupt_snapshot_is_refused_without_touching_state():
    guard = make_guard()
    info = make_escrow(guard)
    bad_states = [
        None,
        [],
        {},
        {"version": 2, "records": {}, "stats": {}, "audit": []},
        {"version": 1, "records": "nope", "stats": {}, "audit": []},
        {"version": 1, "records": {}, "stats": {"created": 1}, "audit": []},
        {"version": 1, "records": {"key-1": {"secret_id": "other"}},
         "stats": {"created": 0, "rotated": 0, "retired": 0,
                   "reconstructed": 0, "failed": 0}, "audit": []},
    ]
    for bad in bad_states:
        with pytest.raises(eg.EscrowError) as exc:
            guard.restore(bad)
        assert exc.value.code == "ESCROW_SNAPSHOT_INVALID"
    assert guard.status("key-1")["version"] == 1
    assert guard.reconstruct("key-1", shares_by_custodian=quorum(
        info, ("alice", "bob", "carol"))) == SECRET


def test_audit_tracks_outcomes_and_reset_stats_zeroes_counters():
    guard = make_guard()
    info = make_escrow(guard)
    guard.reconstruct("key-1", shares_by_custodian=quorum(
        info, ("alice", "bob", "carol")))
    with pytest.raises(eg.EscrowError):
        guard.reconstruct("key-1", shares_by_custodian=quorum(info, ("alice", "bob")))
    guard.rotate_escrow("key-1", shares_by_custodian=quorum(
        info, ("alice", "bob", "carol")), custodians=CUSTODIANS)

    events = guard.audit()
    assert [row["event"] for row in events] == [
        "created", "reconstructed", "rejected", "rotated"]
    assert events[2]["code"] == "ESCROW_QUORUM_SHORT"
    assert guard.audit(limit=2) == events[-2:]
    assert guard.stats() == {"created": 1, "rotated": 1, "retired": 0,
                             "reconstructed": 1, "failed": 1}

    guard.reset_stats()
    assert guard.stats() == {"created": 0, "rotated": 0, "retired": 0,
                             "reconstructed": 0, "failed": 0}
    assert len(guard.audit()) == 4
