import threading

import pytest

import release_signing_quorum as quorum

NOW = "2026-11-24T10:00:00+00:00"
LATER = "2026-11-24T10:10:00+00:00"


def dg(ch):
    return "sha256:" + ch * 64


def make(**kwargs):
    guard = quorum.SigningQuorum(**kwargs)
    guard.register_signer("alice", role="release-manager", weight=2)
    guard.register_signer("bob", role="security", weight=1)
    guard.register_signer("carol", role="engineer", weight=1)
    return guard


def open_proposal(guard, proposal_id="p1", **kwargs):
    kwargs.setdefault("digest", dg("a"))
    kwargs.setdefault("channel", "stable")
    kwargs.setdefault("now", NOW)
    return guard.open_proposal(proposal_id, artifact="payments-api", **kwargs)


def test_signer_registration_lifecycle_and_conflicts():
    guard = quorum.SigningQuorum()
    first = guard.register_signer("alice", role="release-manager", weight=2,
                                  keys=("SHA256:aa",))
    assert first == {"signer": "alice", "role": "release-manager", "weight": 2,
                     "keys": ["SHA256:aa"], "suspended": False}
    again = guard.register_signer("alice", role="release-manager", weight=2,
                                  keys=("SHA256:aa",))
    assert again["weight"] == 2

    with pytest.raises(quorum.QuorumError) as exc:
        guard.register_signer("alice", role="release-manager", weight=3)
    assert exc.value.code == "QUORUM_CONFLICT"

    with pytest.raises(quorum.QuorumError) as exc:
        guard.register_signer("dave", role="intern")
    assert exc.value.code == "QUORUM_BAD_ROLE"

    with pytest.raises(quorum.QuorumError) as exc:
        guard.register_signer("dave", role="engineer", weight=0)
    assert exc.value.code == "QUORUM_BAD_WEIGHT"

    suspended = guard.suspend_signer("alice", reason="left company")
    assert suspended["suspended"] is True
    assert guard.suspend_signer("alice", reason="left company")["suspended"] is True

    with pytest.raises(quorum.QuorumError) as exc:
        guard.suspend_signer("ghost", reason="nobody")
    assert exc.value.code == "QUORUM_UNKNOWN_SIGNER"

    assert guard.reinstate_signer("alice")["suspended"] is False
    with pytest.raises(quorum.QuorumError) as exc:
        guard.reinstate_signer("alice")
    assert exc.value.code == "QUORUM_NOT_SUSPENDED"


def test_open_proposal_validates_fields_and_duplicate_ids():
    guard = make()
    opened = open_proposal(guard, required_roles=("security",), min_weight=2)
    assert opened["status"] == "open"
    assert opened["required_roles"] == ["security"]
    assert opened["min_weight"] == 2

    with pytest.raises(quorum.QuorumError) as exc:
        open_proposal(guard)
    assert exc.value.code == "QUORUM_PROPOSAL_EXISTS"

    cases = [
        ({"digest": "sha256:zz"}, "QUORUM_BAD_DIGEST"),
        ({"channel": "canary"}, "QUORUM_BAD_CHANNEL"),
        ({"required_roles": ("security", "wizard")}, "QUORUM_BAD_ROLES"),
        ({"required_roles": ("security", "security")}, "QUORUM_BAD_ROLES"),
        ({"min_weight": 0}, "QUORUM_BAD_WEIGHT"),
        ({"expires_at": NOW}, "QUORUM_BAD_WINDOW"),
    ]
    for index, (kwargs, code) in enumerate(cases):
        with pytest.raises(quorum.QuorumError) as exc:
            open_proposal(guard, proposal_id=f"bad-{index}", **kwargs)
        assert exc.value.code == code

    with pytest.raises(quorum.QuorumError) as exc:
        guard.open_proposal("empty", artifact="", digest=dg("a"),
                            channel="stable", now=NOW)
    assert exc.value.code == "QUORUM_BAD_ARTIFACT"


def test_open_proposal_capacity_and_manual_close():
    guard = make(max_open=2)
    open_proposal(guard, "p1")
    open_proposal(guard, "p2")

    with pytest.raises(quorum.QuorumError) as exc:
        open_proposal(guard, "p3")
    assert exc.value.code == "QUORUM_CAPACITY"

    closed = guard.close_proposal("p1", reason="superseded", now=NOW)
    assert closed["status"] == "aborted"
    with pytest.raises(quorum.QuorumError) as exc:
        guard.close_proposal("p1", reason="again", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"

    assert open_proposal(guard, "p3")["status"] == "open"


def test_approval_accumulates_weight_once_per_signer():
    guard = make()
    open_proposal(guard, min_weight=3)

    first = guard.approve("p1", "alice", now=NOW)
    assert first["status"] == "approved"
    assert first["duplicate"] is False
    assert first["weight"] == 2
    assert first["approvals"] == 1

    duplicate = guard.approve("p1", "alice", now=NOW)
    assert duplicate["duplicate"] is True
    assert duplicate["weight"] == 2
    assert duplicate["approvals"] == 1

    second = guard.approve("p1", "bob", now=NOW)
    assert second["weight"] == 3
    assert second["approvals"] == 2
    assert guard.stats()["approved"] == 2


def test_closed_proposal_refuses_late_approvals():
    guard = make()
    open_proposal(guard, min_weight=1)
    guard.approve("p1", "alice", now=NOW)
    assert guard.finalize("p1", now=NOW)["status"] == "signed"

    with pytest.raises(quorum.QuorumError) as exc:
        guard.approve("p1", "bob", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"
    with pytest.raises(quorum.QuorumError) as exc:
        guard.finalize("p1", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"


def test_suspended_signers_do_not_count_towards_quorum():
    guard = make()
    open_proposal(guard, min_weight=3)
    guard.approve("p1", "alice", now=NOW)
    guard.approve("p1", "bob", now=NOW)
    guard.suspend_signer("bob", reason="credential leak")

    with pytest.raises(quorum.QuorumError) as exc:
        guard.finalize("p1", now=NOW)
    assert exc.value.code == "QUORUM_NOT_MET"

    guard.reinstate_signer("bob")
    signed = guard.finalize("p1", now=NOW)
    assert signed["weight"] == 3
    assert signed["approvals"] == 2


def test_rejection_is_terminal():
    guard = make()
    open_proposal(guard, min_weight=2)
    guard.approve("p1", "alice", now=NOW)
    rejected = guard.reject("p1", "bob", reason="digest mismatch", now=NOW)
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "digest mismatch"

    with pytest.raises(quorum.QuorumError) as exc:
        guard.approve("p1", "alice", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"
    with pytest.raises(quorum.QuorumError) as exc:
        guard.finalize("p1", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"
    with pytest.raises(quorum.QuorumError) as exc:
        guard.reject("p1", "bob", reason="again", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"
    assert guard.stats()["rejected"] == 1


def test_required_roles_must_each_be_represented():
    guard = make()
    open_proposal(guard, required_roles=("release-manager", "security"), min_weight=2)
    guard.approve("p1", "alice", now=NOW)
    guard.approve("p1", "carol", now=NOW)

    with pytest.raises(quorum.QuorumError) as exc:
        guard.finalize("p1", now=NOW)
    assert exc.value.code == "QUORUM_NOT_MET"

    guard.approve("p1", "bob", now=NOW)
    signed = guard.finalize("p1", now=NOW)
    assert signed["status"] == "signed"
    assert signed["signers"] == ["alice", "bob", "carol"]
    assert signed["weight"] == 4


def test_minimum_weight_must_be_reached():
    guard = make()
    open_proposal(guard, min_weight=5)
    guard.approve("p1", "alice", now=NOW)
    guard.approve("p1", "bob", now=NOW)

    with pytest.raises(quorum.QuorumError) as exc:
        guard.finalize("p1", now=NOW)
    assert exc.value.code == "QUORUM_NOT_MET"

    guard.register_signer("dave", role="engineer", weight=2)
    guard.approve("p1", "dave", now=NOW)
    signed = guard.finalize("p1", now=NOW)
    assert signed["weight"] == 5
    assert signed["approvals"] == 3


def test_revoked_approval_reduces_weight_and_can_be_recast():
    guard = make()
    open_proposal(guard, min_weight=3)
    with pytest.raises(quorum.QuorumError) as exc:
        guard.revoke_approval("p1", "alice", now=NOW)
    assert exc.value.code == "QUORUM_NOT_APPROVED"

    guard.approve("p1", "alice", now=NOW)
    guard.approve("p1", "bob", now=NOW)
    revoked = guard.revoke_approval("p1", "alice", now=NOW)
    assert revoked["weight"] == 1
    assert revoked["approvals"] == 1

    with pytest.raises(quorum.QuorumError) as exc:
        guard.finalize("p1", now=NOW)
    assert exc.value.code == "QUORUM_NOT_MET"

    guard.approve("p1", "alice", now=NOW)
    assert guard.finalize("p1", now=NOW)["weight"] == 3


def test_finalize_writes_history_and_closes_the_proposal():
    guard = make()
    open_proposal(guard, min_weight=1)
    guard.approve("p1", "alice", now=NOW)
    signed = guard.finalize("p1", now=NOW)
    assert signed["status"] == "signed"
    assert signed["signers"] == ["alice"]

    history = guard.history("p1", limit=10)
    assert [event["sequence"] for event in history] == [3, 2, 1]
    assert [event["action"] for event in history] == ["finalize", "approve", "open"]
    assert guard.history("p1", limit=2)[0]["sequence"] == 3

    open_proposal(guard, "p2", min_weight=1)
    assert guard.close_proposal("p2", reason="cancelled", now=NOW)["status"] == "aborted"
    with pytest.raises(quorum.QuorumError) as exc:
        guard.approve("p2", "alice", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"

    with pytest.raises(quorum.QuorumError) as exc:
        guard.history("ghost")
    assert exc.value.code == "QUORUM_UNKNOWN_PROPOSAL"
    with pytest.raises(quorum.QuorumError) as exc:
        guard.history("p1", limit=0)
    assert exc.value.code == "QUORUM_BAD_LIMIT"


def test_expiry_boundary_blocks_approval_and_finalize():
    guard = make()
    open_proposal(guard, "p1", min_weight=1, expires_at=LATER)
    guard.approve("p1", "alice", now="2026-11-24T10:09:59+00:00")
    with pytest.raises(quorum.QuorumError) as exc:
        guard.finalize("p1", now=LATER)
    assert exc.value.code == "QUORUM_PROPOSAL_EXPIRED"
    with pytest.raises(quorum.QuorumError) as exc:
        guard.approve("p1", "bob", now=LATER)
    assert exc.value.code == "QUORUM_PROPOSAL_EXPIRED"

    open_proposal(guard, "p2", min_weight=1, expires_at=LATER)
    guard.approve("p2", "alice", now="2026-11-24T10:09:59+00:00")
    signed = guard.finalize("p2", now="2026-11-24T10:09:59+00:00")
    assert signed["status"] == "signed"


def test_approval_slot_limit_per_signer():
    guard = make(max_approvals_per_signer=2)
    for proposal_id in ("p1", "p2", "p3"):
        open_proposal(guard, proposal_id, min_weight=1)

    guard.approve("p1", "alice", now=NOW)
    guard.approve("p2", "alice", now=NOW)
    with pytest.raises(quorum.QuorumError) as exc:
        guard.approve("p3", "alice", now=NOW)
    assert exc.value.code == "QUORUM_APPROVAL_LIMIT"

    guard.revoke_approval("p2", "alice", now=NOW)
    assert guard.approve("p3", "alice", now=NOW)["status"] == "approved"


def test_snapshot_restore_keeps_approvals_and_rejects_corruption():
    guard = make()
    open_proposal(guard, min_weight=2)
    guard.approve("p1", "alice", now=NOW)
    stats_before = guard.stats()
    state = guard.snapshot()
    assert state["version"] == 1

    state["stats"]["approved"] = 99
    state["signers"]["alice"]["weight"] = 5
    assert guard.stats() == stats_before

    clean = guard.snapshot()
    guard.approve("p1", "bob", now=NOW)
    assert guard.finalize("p1", now=NOW)["weight"] == 3

    restored = quorum.SigningQuorum()
    restored.restore(clean)
    assert restored.approve("p1", "bob", now=NOW)["weight"] == 3
    signed = restored.finalize("p1", now=NOW)
    assert signed["weight"] == 3
    assert restored.stats()["approved"] == 2

    before_bad = restored.stats()
    bad_states = [
        None,
        {},
        {"version": 2, "signers": {}, "proposals": {}, "stats": {}, "sequence": 0},
        {"version": 1, "signers": [], "proposals": {}, "stats": {}, "sequence": 0},
        {"version": 1, "signers": {}, "proposals": "nope", "stats": {}, "sequence": 0},
        {"version": 1, "signers": {}, "proposals": {}, "stats": "nope", "sequence": 0},
        {"version": 1, "signers": {}, "proposals": {}, "stats": {}, "sequence": "late"},
    ]
    for bad in bad_states:
        with pytest.raises(quorum.QuorumError) as exc:
            restored.restore(bad)
        assert exc.value.code == "QUORUM_SNAPSHOT_INVALID"

    assert restored.stats() == before_bad
    with pytest.raises(quorum.QuorumError) as exc:
        restored.finalize("p1", now=NOW)
    assert exc.value.code == "QUORUM_PROPOSAL_CLOSED"


def test_concurrent_approvals_and_finalize_are_serialized():
    guard = quorum.SigningQuorum()
    for index in range(8):
        guard.register_signer(f"s{index}", role="engineer", weight=1)
    open_proposal(guard, min_weight=8)

    barrier = threading.Barrier(8)
    approvals = []
    lock = threading.Lock()

    def approver(index):
        barrier.wait()
        out = guard.approve("p1", f"s{index}", now=NOW)
        with lock:
            approvals.append((out["weight"], out["approvals"]))

    threads = [threading.Thread(target=approver, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(approvals) == [(w, w) for w in range(1, 9)]

    barrier = threading.Barrier(8)
    outcomes = []

    def finalizer():
        barrier.wait()
        try:
            out = guard.finalize("p1", now=NOW)
            outcome = (out["status"], out["weight"])
        except quorum.QuorumError as exc:
            outcome = (exc.code, None)
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=finalizer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(("signed", 8)) == 1
    assert outcomes.count(("QUORUM_PROPOSAL_CLOSED", None)) == 7
    assert guard.stats()["signed"] == 1
