import threading

import pytest

import release_promotion_guard as pg

T0 = "2026-11-24T09:00:00+00:00"
D1 = "a1" * 32
D2 = "b2" * 32
D3 = "c3" * 32
APPROVERS = ("alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi")


def make_guard(**kwargs):
    guard = pg.PromotionGuard(**kwargs)
    guard.register_environment("dev", rank=1, required_approvals=2)
    guard.register_environment("staging", rank=2, required_approvals=2)
    guard.register_environment("prod", rank=3, required_approvals=2)
    for name in APPROVERS:
        guard.authorize_approver(name, environments=("dev", "staging", "prod"))
    return guard


def open_ticket(guard, digest=D1, env="dev", now=T0, requested_by="release-bot"):
    return guard.open_ticket(digest, target_env=env, requested_by=requested_by,
                             now=now)


def approve(guard, ticket_id, *names, now=T0):
    for name in names:
        guard.approve(ticket_id, approver=name, now=now)


def deploy(guard, digest=D1, env="dev", now=T0):
    ticket_id = open_ticket(guard, digest, env, now)["ticket_id"]
    approve(guard, ticket_id, "alice", "bob", now=now)
    guard.promote(ticket_id, now=now)
    return ticket_id


def test_register_environment_is_idempotent_and_rejects_conflicts():
    guard = pg.PromotionGuard()
    info = guard.register_environment("dev", rank=1, required_approvals=2,
                                      allowed_approvers=("bob", "alice"))
    assert info == {"name": "dev", "rank": 1, "required_approvals": 2,
                    "allowed_approvers": ["alice", "bob"], "created": True}
    again = guard.register_environment("dev", rank=1, required_approvals=2,
                                       allowed_approvers=("alice", "bob"))
    assert again["created"] is False

    with pytest.raises(pg.PromotionError) as exc:
        guard.register_environment("dev", rank=1, required_approvals=3)
    assert exc.value.code == "PROMO_CONFLICT"
    with pytest.raises(pg.PromotionError) as exc:
        guard.register_environment("staging", rank=1)
    assert exc.value.code == "PROMO_RANK_CONFLICT"
    with pytest.raises(pg.PromotionError) as exc:
        guard.register_environment("qa", rank=0)
    assert exc.value.code == "PROMO_INVALID_RANK"
    with pytest.raises(pg.PromotionError) as exc:
        guard.register_environment("qa", rank=4, required_approvals=0)
    assert exc.value.code == "PROMO_INVALID_QUORUM"
    with pytest.raises(pg.PromotionError) as exc:
        guard.register_environment("qa", rank=4, allowed_approvers="alice")
    assert exc.value.code == "PROMO_INVALID_QUORUM"


def test_authorize_approver_is_idempotent_and_requires_known_environments():
    guard = make_guard()
    info = guard.authorize_approver("ivan", environments=("staging", "dev"))
    assert info == {"approver_id": "ivan", "environments": ["dev", "staging"],
                    "created": True}
    again = guard.authorize_approver("ivan", environments=("dev", "staging"))
    assert again["created"] is False
    with pytest.raises(pg.PromotionError) as exc:
        guard.authorize_approver("ivan", environments=("dev", "prod"))
    assert exc.value.code == "PROMO_CONFLICT"
    with pytest.raises(pg.PromotionError) as exc:
        guard.authorize_approver("judy", environments=("nowhere",))
    assert exc.value.code == "PROMO_UNKNOWN_ENVIRONMENT"
    with pytest.raises(pg.PromotionError) as exc:
        guard.authorize_approver("judy", environments="dev")
    assert exc.value.code == "PROMO_UNKNOWN_ENVIRONMENT"


def test_open_ticket_creates_pending_ticket_with_deadline():
    guard = make_guard()
    view = open_ticket(guard)
    assert view["created"] is True
    assert view["state"] == "pending"
    assert view["required"] == 2
    assert view["approvals"] == []
    assert view["artifact_digest"] == D1
    assert view["opened_at"] == T0
    assert view["deadline"] == "2026-11-24T10:00:00+00:00"
    stored = guard.ticket(view["ticket_id"])
    assert "created" not in stored
    assert stored["target_env"] == "dev"

    with pytest.raises(pg.PromotionError) as exc:
        guard.ticket("t-999")
    assert exc.value.code == "PROMO_UNKNOWN_TICKET"
    with pytest.raises(pg.PromotionError) as exc:
        guard.open_ticket("not-a-digest", target_env="dev",
                          requested_by="release-bot", now=T0)
    assert exc.value.code == "PROMO_INVALID_DIGEST"
    with pytest.raises(pg.PromotionError) as exc:
        guard.open_ticket(D2, target_env="qa", requested_by="release-bot", now=T0)
    assert exc.value.code == "PROMO_UNKNOWN_ENVIRONMENT"

    tight = make_guard(max_tickets=1)
    open_ticket(tight)
    with pytest.raises(pg.PromotionError) as exc:
        open_ticket(tight, D2)
    assert exc.value.code == "PROMO_CAPACITY_FULL"


def test_promotion_chain_requires_matching_predecessor_deployment():
    guard = make_guard()
    with pytest.raises(pg.PromotionError) as exc:
        open_ticket(guard, D1, env="staging")
    assert exc.value.code == "PROMO_CHAIN_MISMATCH"

    deploy(guard, D1, "dev")
    assert open_ticket(guard, D1, env="staging")["state"] == "pending"
    with pytest.raises(pg.PromotionError) as exc:
        open_ticket(guard, D2, env="staging")
    assert exc.value.code == "PROMO_CHAIN_MISMATCH"


def test_approve_enforces_identity_authorization_and_idempotency():
    guard = make_guard()
    ticket_id = open_ticket(guard, D1, requested_by="alice")["ticket_id"]
    with pytest.raises(pg.PromotionError) as exc:
        guard.approve(ticket_id, approver="alice", now=T0)
    assert exc.value.code == "PROMO_SELF_APPROVAL"
    with pytest.raises(pg.PromotionError) as exc:
        guard.approve(ticket_id, approver="mallory", now=T0)
    assert exc.value.code == "PROMO_APPROVER_UNAUTHORIZED"
    with pytest.raises(pg.PromotionError) as exc:
        guard.approve("t-404", approver="bob", now=T0)
    assert exc.value.code == "PROMO_UNKNOWN_TICKET"

    first = guard.approve(ticket_id, approver="bob", now=T0)
    assert first["state"] == "pending"
    assert first["already"] is False
    duplicate = guard.approve(ticket_id, approver="bob", now=T0)
    assert duplicate["already"] is True
    assert duplicate["approvals"] == ["bob"]

    restricted = pg.PromotionGuard()
    restricted.register_environment("dev", rank=1, required_approvals=1,
                                    allowed_approvers=("alice",))
    restricted.authorize_approver("bob", environments=("dev",))
    restricted.authorize_approver("alice", environments=("dev",))
    limited = restricted.open_ticket(D1, target_env="dev",
                                     requested_by="release-bot", now=T0)
    with pytest.raises(pg.PromotionError) as exc:
        restricted.approve(limited["ticket_id"], approver="bob", now=T0)
    assert exc.value.code == "PROMO_APPROVER_UNAUTHORIZED"
    assert restricted.approve(limited["ticket_id"], approver="alice",
                              now=T0)["state"] == "approved"


def test_approval_quorum_freezes_after_required_approvals():
    guard = make_guard()
    ticket_id = open_ticket(guard)["ticket_id"]
    one = guard.approve(ticket_id, approver="alice", now=T0)
    assert one["state"] == "pending"
    assert one["approvals"] == ["alice"]
    two = guard.approve(ticket_id, approver="bob", now=T0)
    assert two["state"] == "approved"
    assert two["approvals"] == ["alice", "bob"]
    assert guard.stats()["approved"] == 1

    extra = guard.approve(ticket_id, approver="carol", now=T0)
    assert extra["already"] is True
    assert extra["state"] == "approved"
    assert extra["approvals"] == ["alice", "bob"]


def test_promote_requires_approval_and_records_deployment_once():
    guard = make_guard()
    ticket_id = open_ticket(guard)["ticket_id"]
    with pytest.raises(pg.PromotionError) as exc:
        guard.promote(ticket_id, now=T0)
    assert exc.value.code == "PROMO_NOT_APPROVED"

    approve(guard, ticket_id, "alice", "bob")
    result = guard.promote(ticket_id, now=T0)
    assert result["state"] == "promoted"
    assert result["already"] is False
    assert guard.deployment("dev")["digest"] == D1
    assert guard.deployment("dev")["ticket_id"] == ticket_id
    assert guard.deployment("staging") is None

    again = guard.promote(ticket_id, now=T0)
    assert again["already"] is True
    assert again["promoted_at"] == result["promoted_at"]
    assert guard.stats()["promoted"] == 1
    assert len(guard.list_tickets()) == 1


def test_freeze_windows_block_promotion_and_reject_overlap():
    guard = make_guard()
    deploy(guard, D1, "dev")
    guard.freeze_environment("staging",
                             start="2026-11-24T09:20:00+00:00",
                             end="2026-11-24T09:40:00+00:00",
                             reason="quarter close")
    with pytest.raises(pg.PromotionError) as exc:
        guard.freeze_environment("staging",
                                 start="2026-11-24T09:30:00+00:00",
                                 end="2026-11-24T09:50:00+00:00",
                                 reason="overlap")
    assert exc.value.code == "PROMO_FREEZE_OVERLAP"
    with pytest.raises(pg.PromotionError) as exc:
        guard.freeze_environment("staging",
                                 start="2026-11-24T09:50:00+00:00",
                                 end="2026-11-24T09:40:00+00:00",
                                 reason="backwards")
    assert exc.value.code == "PROMO_INVALID_WINDOW"

    before = open_ticket(guard, D1, "staging", now="2026-11-24T09:10:00+00:00")["ticket_id"]
    approve(guard, before, "alice", "bob", now="2026-11-24T09:10:00+00:00")
    assert guard.promote(before, now="2026-11-24T09:15:00+00:00")["state"] == "promoted"

    during = open_ticket(guard, D1, "staging", now="2026-11-24T09:30:00+00:00")["ticket_id"]
    approve(guard, during, "alice", "bob", now="2026-11-24T09:30:00+00:00")
    with pytest.raises(pg.PromotionError) as exc:
        guard.promote(during, now="2026-11-24T09:35:00+00:00")
    assert exc.value.code == "PROMO_FROZEN"

    assert guard.clear_freezes("staging") == 1
    assert guard.promote(during, now="2026-11-24T09:35:00+00:00")["state"] == "promoted"


def test_hourly_promotion_rate_limit():
    guard = make_guard(max_promotions_per_hour=2)
    deploy(guard, D1, "dev", now="2026-11-24T09:00:00+00:00")
    deploy(guard, D2, "dev", now="2026-11-24T09:05:00+00:00")
    third = open_ticket(guard, D3, "dev", now="2026-11-24T09:10:00+00:00")["ticket_id"]
    approve(guard, third, "alice", "bob", now="2026-11-24T09:10:00+00:00")
    with pytest.raises(pg.PromotionError) as exc:
        guard.promote(third, now="2026-11-24T09:12:00+00:00")
    assert exc.value.code == "PROMO_RATE_LIMITED"
    assert guard.stats()["promoted"] == 2

    later = "2026-11-24T10:10:00+00:00"
    fourth = open_ticket(guard, D3, "dev", now=later)["ticket_id"]
    approve(guard, fourth, "alice", "bob", now=later)
    assert guard.promote(fourth, now=later)["state"] == "promoted"


def test_ticket_expiry_blocks_approval_and_promotion():
    guard = make_guard(ticket_ttl=60)
    expired = open_ticket(guard, D1, now=T0)["ticket_id"]
    with pytest.raises(pg.PromotionError) as exc:
        guard.approve(expired, approver="alice",
                      now="2026-11-24T09:01:01+00:00")
    assert exc.value.code == "PROMO_TICKET_EXPIRED"

    ticket_id = open_ticket(guard, D2, now=T0)["ticket_id"]
    approve(guard, ticket_id, "alice", "bob", now=T0)
    with pytest.raises(pg.PromotionError) as exc:
        guard.promote(ticket_id, now="2026-11-24T09:01:00+00:00")
    assert exc.value.code == "PROMO_TICKET_EXPIRED"
    assert guard.deployment("dev") is None


def test_stale_approvals_are_dropped_and_ticket_returns_to_pending():
    guard = make_guard(approval_ttl=60)
    ticket_id = open_ticket(guard, D1, now=T0)["ticket_id"]
    guard.approve(ticket_id, approver="alice", now=T0)
    guard.approve(ticket_id, approver="bob",
                  now="2026-11-24T09:00:30+00:00")
    with pytest.raises(pg.PromotionError) as exc:
        guard.promote(ticket_id, now="2026-11-24T09:01:40+00:00")
    assert exc.value.code == "PROMO_APPROVAL_STALE"
    stale = guard.ticket(ticket_id)
    assert stale["state"] == "pending"
    assert stale["approvals"] == []

    redo = "2026-11-24T09:01:50+00:00"
    approve(guard, ticket_id, "alice", "bob", now=redo)
    assert guard.promote(ticket_id, now="2026-11-24T09:02:00+00:00")["state"] == "promoted"


def test_rollback_restores_previous_deployment_and_needs_history():
    guard = make_guard()
    deploy(guard, D1, "dev", now=T0)
    deploy(guard, D2, "dev", now="2026-11-24T09:05:00+00:00")
    assert guard.deployment("dev")["digest"] == D2

    rolled = guard.rollback_environment("dev", actor="oncall",
                                        now="2026-11-24T09:10:00+00:00")
    assert rolled == {"name": "dev", "digest": D1, "ticket_id": "t-1",
                      "promoted_at": T0, "restored": True}
    assert guard.deployment("dev")["digest"] == D1
    with pytest.raises(pg.PromotionError) as exc:
        guard.rollback_environment("dev", actor="oncall")
    assert exc.value.code == "PROMO_NO_HISTORY"
    with pytest.raises(pg.PromotionError) as exc:
        guard.rollback_environment("prod", actor="oncall")
    assert exc.value.code == "PROMO_NO_HISTORY"
    with pytest.raises(pg.PromotionError) as exc:
        guard.rollback_environment("qa", actor="oncall")
    assert exc.value.code == "PROMO_UNKNOWN_ENVIRONMENT"


def test_concurrent_approvals_record_exactly_the_quorum():
    guard = pg.PromotionGuard()
    guard.register_environment("dev", rank=1, required_approvals=3)
    for name in APPROVERS:
        guard.authorize_approver(name, environments=("dev",))
    ticket_id = guard.open_ticket(D1, target_env="dev",
                                  requested_by="release-bot", now=T0)["ticket_id"]
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker(name):
        barrier.wait()
        try:
            out = guard.approve(ticket_id, approver=name, now=T0)
        except pg.PromotionError as exc:
            out = {"state": "error", "code": exc.code}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(name,))
               for name in APPROVERS]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    ticket = guard.ticket(ticket_id)
    assert ticket["state"] == "approved"
    assert len(ticket["approvals"]) == 3
    assert len(set(ticket["approvals"])) == 3
    assert sum(1 for row in results if row["already"] is False) == 3
    assert sum(1 for row in results if row["already"] is True) == 5
    assert guard.stats()["approved"] == 1


def test_concurrent_promotion_applies_once():
    guard = make_guard()
    ticket_id = open_ticket(guard)["ticket_id"]
    approve(guard, ticket_id, "alice", "bob")
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.promote(ticket_id, now=T0)
        except pg.PromotionError as exc:
            out = {"state": "error", "code": exc.code}
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for row in results if row["already"] is False) == 1
    assert sum(1 for row in results if row["already"] is True) == 7
    assert guard.stats()["promoted"] == 1
    history = guard.snapshot()["history"]["dev"]
    assert len(history) == 1
    assert guard.deployment("dev")["digest"] == D1


def test_snapshot_restore_and_corrupt_state_handling():
    guard = make_guard()
    deploy(guard, D1, "dev")
    staging = open_ticket(guard, D1, "staging")["ticket_id"]
    approve(guard, staging, "alice", "bob")
    state = guard.snapshot()
    state["environments"]["dev"]["rank"] = 99
    assert guard.snapshot()["environments"]["dev"]["rank"] == 1

    restored = make_guard()
    restored.restore(guard.snapshot())
    assert restored.deployment("dev")["digest"] == D1
    assert restored.ticket(staging)["state"] == "approved"
    assert restored.promote(staging, now=T0)["state"] == "promoted"
    assert restored.deployment("staging")["digest"] == D1

    good = guard.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2},
        dict(good, environments=[]),
        dict(good, stats={"opened": 1}),
        dict(good, tickets={"t-1": {"ticket_id": "t-2"}}),
        dict(good, deployments={"dev": {"digest": 5}}),
        dict(good, freezes={"missing": [{"start": 0, "end": 1}]}),
    ]
    for bad in bad_states:
        with pytest.raises(pg.PromotionError) as exc:
            guard.restore(bad)
        assert exc.value.code == "PROMO_SNAPSHOT_INVALID"
    assert guard.deployment("dev")["digest"] == D1
    assert guard.stats()["promoted"] == 1
    guard.reset_stats()
    assert guard.stats() == {"opened": 0, "approved": 0, "promoted": 0,
                             "rolled_back": 0, "rejected": 0}
