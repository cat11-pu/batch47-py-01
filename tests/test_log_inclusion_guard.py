import hashlib
import hmac
import threading

import pytest

import log_inclusion_guard as lg

KEY = "2f" * 32
LOG = "sigstore-main"
BASES = [b"alpha", b"beta", b"gamma", b"delta", b"epsilon"]


def lhash(data):
    return hashlib.sha256(b"\x00" + data).hexdigest()


def nhash(left, right):
    return hashlib.sha256(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def mth(values):
    if len(values) == 1:
        return lhash(values[0])
    k = 1 << ((len(values) - 1).bit_length() - 1)
    return nhash(mth(values[:k]), mth(values[k:]))


def audit_path(values, index):
    if len(values) == 1:
        return []
    k = 1 << ((len(values) - 1).bit_length() - 1)
    if index < k:
        return audit_path(values[:k], index) + [mth(values[k:])]
    return audit_path(values[k:], index - k) + [mth(values[:k])]


def sign(tree_size, root, log_id=LOG, key=KEY):
    message = f"checkpoint:v1\n{log_id}\n{tree_size}\n{root}".encode("utf-8")
    return hmac.new(bytes.fromhex(key), message, hashlib.sha256).hexdigest()


HASHES = [lhash(item) for item in BASES]


def make_guard(**kwargs):
    guard = lg.InclusionGuard(**kwargs)
    guard.register_log(LOG, key_hex=KEY)
    root = mth(BASES)
    accepted = guard.accept_checkpoint(LOG, tree_size=5, root_hash=root,
                                       signature=sign(5, root))
    assert accepted["updated"] is True
    return guard


def entry(index, entry_id, values=BASES):
    return dict(entry_id=entry_id, leaf_hash=lhash(values[index]),
                leaf_index=index, tree_size=len(values),
                audit_path=audit_path(values, index))


def test_register_log_is_idempotent_and_rejects_conflicts():
    guard = lg.InclusionGuard()
    info = guard.register_log(LOG, key_hex=KEY, max_tree_size=1024)
    assert info == {"log_id": LOG, "max_tree_size": 1024, "revoked": False,
                    "checkpoint": None, "created": True}
    assert "key_hex" not in info
    again = guard.register_log(LOG, key_hex=KEY, max_tree_size=1024)
    assert again["created"] is False

    with pytest.raises(lg.InclusionError) as exc:
        guard.register_log(LOG, key_hex="ff" * 32, max_tree_size=1024)
    assert exc.value.code == "TRANSLOG_CONFLICT"
    with pytest.raises(lg.InclusionError) as exc:
        guard.register_log("other", key_hex="not-hex")
    assert exc.value.code == "TRANSLOG_BAD_KEY"


def test_leaf_node_hash_and_checkpoint_message_follow_rfc6962():
    root = mth(BASES)
    assert lg.leaf_hash(b"alpha") == HASHES[0]
    assert lg.node_hash(HASHES[0], HASHES[1]) == nhash(HASHES[0], HASHES[1])
    assert lg.checkpoint_message(LOG, 5, root) == (
        f"checkpoint:v1\n{LOG}\n5\n{root}")


def test_checkpoint_signature_and_root_encoding_are_enforced():
    guard = lg.InclusionGuard()
    guard.register_log(LOG, key_hex=KEY)
    root = mth(BASES)
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_checkpoint(LOG, tree_size=5, root_hash=root,
                                signature="00" * 32)
    assert exc.value.code == "TRANSLOG_BAD_SIGNATURE"
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_checkpoint(LOG, tree_size=5, root_hash=root.upper(),
                                signature=sign(5, root.upper()))
    assert exc.value.code == "TRANSLOG_BAD_CHECKPOINT"

    first = guard.accept_checkpoint(LOG, tree_size=5, root_hash=root,
                                    signature=sign(5, root))
    assert first["updated"] is True
    assert guard.checkpoint(LOG) == {"tree_size": 5, "root_hash": root}
    second = guard.accept_checkpoint(LOG, tree_size=5, root_hash=root,
                                     signature=sign(5, root))
    assert second["updated"] is False
    assert guard.stats()["checkpoints"] == 1


def test_checkpoint_rejects_rollback_and_fork():
    guard = make_guard()
    root3 = mth(BASES[:3])
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_checkpoint(LOG, tree_size=3, root_hash=root3,
                                signature=sign(3, root3))
    assert exc.value.code == "TRANSLOG_ROLLBACK"

    fork_root = "ab" * 32
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_checkpoint(LOG, tree_size=5, root_hash=fork_root,
                                signature=sign(5, fork_root))
    assert exc.value.code == "TRANSLOG_FORK"
    assert guard.checkpoint(LOG)["tree_size"] == 5


def test_inclusion_accepts_every_index_and_rejects_bad_proofs():
    guard = make_guard()
    root = mth(BASES)
    for index in range(5):
        result = guard.verify_inclusion(LOG, leaf_hash=HASHES[index],
                                        leaf_index=index, tree_size=5,
                                        audit_path=audit_path(BASES, index),
                                        root_hash=root)
        assert result["verified"] is True
        assert result["leaf_index"] == index

    with pytest.raises(lg.InclusionError) as exc:
        guard.verify_inclusion(LOG, leaf_hash=HASHES[1], leaf_index=1,
                               tree_size=5, audit_path=audit_path(BASES, 0))
    assert exc.value.code == "TRANSLOG_INCLUSION_INVALID"
    with pytest.raises(lg.InclusionError) as exc:
        guard.verify_inclusion(LOG, leaf_hash=HASHES[0], leaf_index=5,
                               tree_size=5, audit_path=[])
    assert exc.value.code == "TRANSLOG_INCLUSION_INVALID"
    with pytest.raises(lg.InclusionError) as exc:
        guard.verify_inclusion(LOG, leaf_hash=HASHES[0], leaf_index=0,
                               tree_size=4, audit_path=audit_path(BASES[:4], 0))
    assert exc.value.code == "TRANSLOG_UNKNOWN_ROOT"
    with pytest.raises(lg.InclusionError) as exc:
        guard.verify_inclusion(LOG, leaf_hash=HASHES[0], leaf_index=0,
                               tree_size=5, audit_path=audit_path(BASES, 0),
                               root_hash=mth(BASES[:4]))
    assert exc.value.code == "TRANSLOG_UNKNOWN_ROOT"
    with pytest.raises(lg.InclusionError) as exc:
        guard.verify_inclusion(LOG, leaf_hash=HASHES[0], leaf_index=0,
                               tree_size=5, audit_path=[HASHES[0]] * 33)
    assert exc.value.code == "TRANSLOG_PROOF_TOO_LONG"


def test_accept_entry_replay_by_id_and_by_leaf():
    guard = make_guard()
    first = guard.accept_entry(LOG, **entry(0, "e-0"))
    assert first["status"] == "accepted"
    assert first["root_hash"] == mth(BASES)
    assert first["tree_size"] == 5

    second = guard.accept_entry(LOG, **entry(0, "e-0"))
    assert second["status"] == "replayed"
    assert second["code"] == "TRANSLOG_REPLAY"
    third = guard.accept_entry(LOG, **entry(0, "e-duplicate"))
    assert third["status"] == "replayed"
    assert guard.is_known("e-0") is True
    assert guard.is_known("e-duplicate") is False
    assert guard.stats() == {"accepted": 1, "replayed": 2, "rejected": 0,
                             "checkpoints": 1}
    assert guard.entries(LOG) == [{
        "entry_id": "e-0", "log_id": LOG, "leaf_hash": HASHES[0],
        "leaf_index": 0, "tree_size": 5}]


def test_accept_entry_requires_an_adopted_checkpoint():
    guard = lg.InclusionGuard()
    guard.register_log(LOG, key_hex=KEY)
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_entry(LOG, **entry(0, "e-0"))
    assert exc.value.code == "TRANSLOG_UNKNOWN_ROOT"
    assert guard.is_known("e-0") is False
    assert guard.stats()["rejected"] == 1
    assert guard.checkpoint(LOG) is None


def test_checkpoint_advance_needs_a_valid_anchor_proof():
    guard = make_guard()
    guard.accept_entry(LOG, **entry(2, "e-2"))
    extended = BASES + [b"zeta"]
    root6 = mth(extended)

    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_checkpoint(LOG, tree_size=6, root_hash=root6,
                                signature=sign(6, root6))
    assert exc.value.code == "TRANSLOG_ANCHOR_MISSING"

    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_checkpoint(LOG, tree_size=6, root_hash=root6,
                                signature=sign(6, root6),
                                anchor={"leaf_hash": HASHES[2], "leaf_index": 2,
                                        "audit_path": audit_path(BASES[:4], 2)})
    assert exc.value.code == "TRANSLOG_ANCHOR_INVALID"
    assert guard.checkpoint(LOG)["tree_size"] == 5

    updated = guard.accept_checkpoint(LOG, tree_size=6, root_hash=root6,
                                      signature=sign(6, root6),
                                      anchor={"leaf_hash": HASHES[2],
                                              "leaf_index": 2,
                                              "audit_path": audit_path(extended, 2)})
    assert updated["updated"] is True
    assert guard.checkpoint(LOG) == {"tree_size": 6, "root_hash": root6}


def test_capacity_limit_rejects_new_entries():
    guard = make_guard(max_entries=1)
    assert guard.accept_entry(LOG, **entry(0, "e-0"))["status"] == "accepted"
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_entry(LOG, **entry(1, "e-1"))
    assert exc.value.code == "TRANSLOG_CAPACITY_FULL"
    assert guard.is_known("e-1") is False
    assert guard.stats()["rejected"] == 1


def test_batch_reports_each_item_and_rejects_oversized_batches():
    guard = make_guard(batch_limit=3)
    results = guard.accept_batch(LOG, [
        entry(0, "b-0"),
        entry(0, "b-0"),
        {"entry_id": "b-bad", "leaf_hash": HASHES[1], "leaf_index": 1,
         "tree_size": 5, "audit_path": audit_path(BASES, 0)},
    ])
    assert [row["status"] for row in results] == ["accepted", "replayed", "rejected"]
    assert results[2]["code"] == "TRANSLOG_INCLUSION_INVALID"
    assert guard.stats()["accepted"] == 1
    assert guard.stats()["replayed"] == 1
    assert guard.stats()["rejected"] == 1

    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_batch(LOG, [entry(0, "x"), entry(1, "y"),
                                 entry(2, "z"), entry(3, "w")])
    assert exc.value.code == "TRANSLOG_BATCH_TOO_LARGE"


def test_concurrent_accept_of_one_entry_succeeds_once():
    guard = make_guard()
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.accept_entry(LOG, **entry(3, "race"))
        except lg.InclusionError as exc:
            out = {"status": "error", "code": exc.code}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(row["status"] for row in results) == ["accepted"] + ["replayed"] * 7
    assert guard.stats()["accepted"] == 1
    assert guard.stats()["replayed"] == 7


def test_revoked_log_blocks_all_operations():
    guard = make_guard()
    guard.revoke_log(LOG)
    root = mth(BASES)
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_checkpoint(LOG, tree_size=5, root_hash=root,
                                signature=sign(5, root))
    assert exc.value.code == "TRANSLOG_LOG_REVOKED"
    with pytest.raises(lg.InclusionError) as exc:
        guard.verify_inclusion(LOG, leaf_hash=HASHES[0], leaf_index=0,
                               tree_size=5, audit_path=audit_path(BASES, 0))
    assert exc.value.code == "TRANSLOG_LOG_REVOKED"
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_entry(LOG, **entry(1, "e-1"))
    assert exc.value.code == "TRANSLOG_LOG_REVOKED"
    with pytest.raises(lg.InclusionError) as exc:
        guard.checkpoint(LOG)
    assert exc.value.code == "TRANSLOG_LOG_REVOKED"
    with pytest.raises(lg.InclusionError) as exc:
        guard.accept_entry("missing", **entry(0, "e-0"))
    assert exc.value.code == "TRANSLOG_UNKNOWN_LOG"
    with pytest.raises(lg.InclusionError) as exc:
        guard.revoke_log("missing")
    assert exc.value.code == "TRANSLOG_UNKNOWN_LOG"


def test_snapshot_restore_keeps_registrations_and_replay_state():
    guard = make_guard()
    guard.accept_entry(LOG, **entry(0, "e-0"))
    state = guard.snapshot()
    state["logs"][LOG]["revoked"] = True
    assert guard.checkpoint(LOG)["tree_size"] == 5

    guard.accept_entry(LOG, **entry(1, "e-1"))
    restored = lg.InclusionGuard()
    restored.restore(guard.snapshot())
    assert restored.is_known("e-0") is True
    assert restored.is_known("e-1") is True
    assert restored.stats()["accepted"] == 2
    replay = restored.accept_entry(LOG, **entry(0, "e-0"))
    assert replay["status"] == "replayed"
    checked = restored.verify_inclusion(
        LOG, leaf_hash=HASHES[2], leaf_index=2, tree_size=5,
        audit_path=audit_path(BASES, 2))
    assert checked["verified"] is True


def test_corrupt_snapshot_is_refused_without_touching_state():
    guard = make_guard()
    guard.accept_entry(LOG, **entry(0, "e-0"))
    good = guard.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "logs": {}, "entries": {}, "leaf_index": {},
         "last_entry": {}, "stats": {}},
        {"version": 1, "logs": "x", "entries": {}, "leaf_index": {},
         "last_entry": {}, "stats": {"accepted": 0, "replayed": 0,
                                     "rejected": 0, "checkpoints": 0}},
        {"version": 1, "logs": {LOG: {"key_hex": "bad", "max_tree_size": 10,
                                      "revoked": False, "checkpoint": None}},
         "entries": {}, "leaf_index": {}, "last_entry": {},
         "stats": {"accepted": 0, "replayed": 0, "rejected": 0, "checkpoints": 0}},
        dict(good, stats={"accepted": 0}),
        dict(good, leaf_index={HASHES[0]: "missing"}),
    ]
    for bad in bad_states:
        with pytest.raises(lg.InclusionError) as exc:
            guard.restore(bad)
        assert exc.value.code == "TRANSLOG_SNAPSHOT_INVALID"
    assert guard.is_known("e-0") is True
    assert guard.checkpoint(LOG)["tree_size"] == 5


def test_stats_and_entry_listing_accounting():
    guard = make_guard()
    guard.accept_entry(LOG, **entry(0, "e-0"))
    guard.accept_entry(LOG, **entry(0, "e-0"))
    with pytest.raises(lg.InclusionError):
        guard.accept_entry(LOG, entry_id="bad", leaf_hash=HASHES[0],
                           leaf_index=0, tree_size=5,
                           audit_path=audit_path(BASES, 1))
    assert guard.stats() == {"accepted": 1, "replayed": 1, "rejected": 1,
                             "checkpoints": 1}
    guard.accept_entry(LOG, **entry(1, "e-1"))
    rows = guard.entries(LOG)
    assert [row["entry_id"] for row in rows] == ["e-0", "e-1"]
    assert guard.entries("other-log") == []
    guard.reset_stats()
    assert guard.stats() == {"accepted": 0, "replayed": 0, "rejected": 0,
                             "checkpoints": 0}
    assert guard.is_known("e-1") is True
