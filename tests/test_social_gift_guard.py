import copy
import threading
from datetime import datetime, timedelta

import pytest

import social_gift_guard as sg

NOW = "2026-11-24T10:00:00+00:00"
BASE = datetime.fromisoformat(NOW)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def setup(**kwargs):
    lab = sg.SocialGiftGuard(**kwargs)
    lab.register_gift("rose", name="rose", cost=100, intimacy=30)
    lab.register_gift("candy", name="candy", cost=50, intimacy=10)
    for player in ("alice", "bob", "carol"):
        lab.register_player(player, server="s1", wallet=1000)
    return lab


def test_catalogue_registration_is_idempotent_and_profiles_are_guarded():
    lab = sg.SocialGiftGuard()
    gift = lab.register_gift("rose", name="rose", cost=100, intimacy=30)
    assert gift == {"gift_id": "rose", "name": "rose", "cost": 100, "intimacy": 30}
    assert lab.register_gift("rose", name="rose", cost=100, intimacy=30) == gift
    with pytest.raises(sg.GiftError) as exc:
        lab.register_gift("rose", name="rose", cost=150, intimacy=30)
    assert exc.value.code == "GIFT_CONFLICT"

    player = lab.register_player("alice", server="s1", wallet=500)
    assert player == {"player_id": "alice", "server": "s1", "wallet": 500}
    assert lab.register_player("alice", server="s1", wallet=500) == player
    with pytest.raises(sg.GiftError) as exc:
        lab.register_player("alice", server="s2", wallet=500)
    assert exc.value.code == "GIFT_SERVER_MISMATCH"
    with pytest.raises(sg.GiftError) as exc:
        lab.register_player("alice", server="s1", wallet=600)
    assert exc.value.code == "GIFT_PROFILE_CONFLICT"

    with pytest.raises(sg.GiftError) as exc:
        lab.topup("alice", 0)
    assert exc.value.code == "GIFT_INVALID_AMOUNT"
    assert lab.topup("alice", 100)["wallet"] == 600
    assert lab.wallet("alice") == 600
    with pytest.raises(sg.GiftError) as exc:
        lab.wallet("ghost")
    assert exc.value.code == "GIFT_UNKNOWN_PLAYER"
    with pytest.raises(sg.GiftError) as exc:
        lab.register_gift("", name="bad", cost=10, intimacy=1)
    assert exc.value.code == "GIFT_INVALID_GIFT"


def test_send_charges_wallet_and_creates_pending_instance():
    lab = setup()
    instance = lab.send("alice", "bob", "rose", now=NOW)

    assert instance["instance_id"] == "g-1"
    assert instance["status"] == "pending"
    assert instance["sender"] == "alice"
    assert instance["recipient"] == "bob"
    assert instance["gift_id"] == "rose"
    assert instance["cost"] == 100
    assert instance["intimacy"] == 30
    assert instance["expires_at"] == at(3600)
    assert instance["claimed_at"] is None
    assert lab.wallet("alice") == 900
    assert lab.wallet("bob") == 1000
    pending = lab.pending("bob", now=NOW)
    assert [row["instance_id"] for row in pending] == ["g-1"]
    assert lab.pending("alice", now=NOW) == []
    assert lab.stats() == {"sent": 1, "claimed": 0, "expired": 0, "blocked": 0}


def test_request_id_makes_send_idempotent_and_blocks_reuse():
    lab = setup()
    first = lab.send("alice", "bob", "rose", now=NOW, request_id="req-1")
    again = lab.send("alice", "bob", "rose", now=NOW, request_id="req-1")
    assert again["instance_id"] == first["instance_id"]
    assert lab.wallet("alice") == 900
    assert lab.stats()["sent"] == 1

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "candy", now=NOW, request_id="req-1")
    assert exc.value.code == "GIFT_REQUEST_REUSED"
    assert len(lab.pending("bob", now=NOW)) == 1


def test_unknown_players_gifts_and_self_send_are_rejected():
    lab = setup()

    with pytest.raises(sg.GiftError) as exc:
        lab.send("ghost", "bob", "rose", now=NOW)
    assert exc.value.code == "GIFT_UNKNOWN_PLAYER"

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "ghost", "rose", now=NOW)
    assert exc.value.code == "GIFT_UNKNOWN_PLAYER"

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "ghost", now=NOW)
    assert exc.value.code == "GIFT_UNKNOWN_GIFT"

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "alice", "rose", now=NOW)
    assert exc.value.code == "GIFT_SELF"
    assert lab.stats()["sent"] == 0
    assert lab.wallet("alice") == 1000


def test_blocking_either_direction_stops_gifts_and_can_be_lifted():
    lab = setup()
    lab.block("bob", "alice")

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "rose", now=NOW)
    assert exc.value.code == "GIFT_BLOCKED"
    assert lab.wallet("alice") == 1000
    assert lab.pending("bob", now=NOW) == []
    assert lab.stats()["blocked"] == 1

    lab.unblock("bob", "alice")
    assert lab.send("alice", "bob", "rose", now=NOW)["status"] == "pending"
    with pytest.raises(sg.GiftError) as exc:
        lab.unblock("bob", "alice")
    assert exc.value.code == "GIFT_NOT_BLOCKED"

    lab.block("alice", "bob")
    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "rose", now=NOW)
    assert exc.value.code == "GIFT_BLOCKED"
    assert lab.stats()["blocked"] == 2

    with pytest.raises(sg.GiftError) as exc:
        lab.block("alice", "alice")
    assert exc.value.code == "GIFT_SELF_BLOCK"


def test_daily_send_limit_resets_on_utc_day_change():
    lab = setup(daily_send_limit=2, pair_daily_limit=10, gift_ttl=200000)
    lab.send("alice", "bob", "rose", now=NOW)
    lab.send("alice", "bob", "rose", now=NOW)

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "rose", now=NOW)
    assert exc.value.code == "GIFT_DAILY_LIMIT"
    assert lab.wallet("alice") == 800
    assert lab.stats()["sent"] == 2

    lab.send("alice", "bob", "rose", now=at(86400))
    assert lab.wallet("alice") == 700
    assert lab.stats()["sent"] == 3


def test_pair_limit_is_per_recipient_per_day():
    lab = setup(pair_daily_limit=2)
    lab.send("alice", "bob", "rose", now=NOW)
    lab.send("alice", "bob", "candy", now=NOW)

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "rose", now=NOW)
    assert exc.value.code == "GIFT_PAIR_LIMIT"
    assert lab.wallet("alice") == 850

    lab.send("alice", "carol", "rose", now=NOW)
    assert lab.wallet("alice") == 750
    assert lab.stats()["sent"] == 3


def test_recipient_box_capacity_frees_after_claim():
    lab = setup(max_pending=1)
    first = lab.send("alice", "bob", "rose", now=NOW)

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "candy", now=NOW)
    assert exc.value.code == "GIFT_BOX_FULL"
    assert lab.wallet("alice") == 900

    lab.claim("bob", first["instance_id"], now=NOW)
    assert lab.send("alice", "bob", "candy", now=NOW)["status"] == "pending"
    assert lab.wallet("alice") == 850


def test_insufficient_funds_does_not_create_instance():
    lab = sg.SocialGiftGuard()
    lab.register_gift("crown", name="crown", cost=500, intimacy=100)
    lab.register_player("alice", server="s1", wallet=120)
    lab.register_player("bob", server="s1", wallet=0)

    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "bob", "crown", now=NOW)
    assert exc.value.code == "GIFT_INSUFFICIENT_FUNDS"
    assert lab.wallet("alice") == 120
    assert lab.pending("bob", now=NOW) == []

    lab.topup("alice", 380)
    lab.send("alice", "bob", "crown", now=NOW)
    assert lab.wallet("alice") == 0
    assert lab.stats()["sent"] == 1


def test_claim_permissions_and_single_use():
    lab = setup()
    first = lab.send("alice", "bob", "rose", now=NOW)
    other = lab.send("alice", "carol", "rose", now=NOW)

    with pytest.raises(sg.GiftError) as exc:
        lab.claim("carol", first["instance_id"], now=NOW)
    assert exc.value.code == "GIFT_NOT_RECIPIENT"
    with pytest.raises(sg.GiftError) as exc:
        lab.claim("bob", "g-404", now=NOW)
    assert exc.value.code == "GIFT_UNKNOWN_INSTANCE"

    result = lab.claim("bob", first["instance_id"], now=NOW)
    assert result["instance_id"] == first["instance_id"]
    assert result["gift_id"] == "rose"
    assert result["intimacy"] == 30
    assert result["claimed_at"] == NOW
    assert lab.wallet("alice") == 800
    assert lab.wallet("bob") == 1000

    with pytest.raises(sg.GiftError) as exc:
        lab.claim("bob", first["instance_id"], now=NOW)
    assert exc.value.code == "GIFT_ALREADY_CLAIMED"
    assert [row["instance_id"] for row in lab.pending("bob", now=NOW)] == []
    assert [row["instance_id"] for row in lab.pending("carol", now=NOW)] == [
        other["instance_id"]]
    assert lab.stats()["claimed"] == 1


def test_intimacy_is_symmetric_accumulates_and_caps():
    lab = setup(max_intimacy=50)
    first = lab.send("alice", "bob", "rose", now=NOW)
    second = lab.send("bob", "alice", "rose", now=NOW)

    assert lab.claim("bob", first["instance_id"], now=NOW)["intimacy"] == 30
    assert lab.claim("alice", second["instance_id"], now=NOW)["intimacy"] == 50
    assert lab.intimacy("alice", "bob") == 50
    assert lab.intimacy("bob", "alice") == 50


def test_expired_gift_refunds_sender_once_and_cannot_be_claimed():
    lab = setup(gift_ttl=60)
    first = lab.send("alice", "bob", "rose", now=NOW)
    assert lab.purge(now=at(59)) == 0

    with pytest.raises(sg.GiftError) as exc:
        lab.claim("bob", first["instance_id"], now=at(60))
    assert exc.value.code == "GIFT_EXPIRED"
    assert lab.wallet("alice") == 1000
    assert lab.stats()["expired"] == 1

    with pytest.raises(sg.GiftError) as exc:
        lab.claim("bob", first["instance_id"], now=at(61))
    assert exc.value.code == "GIFT_EXPIRED"

    second = lab.send("alice", "bob", "rose", now=at(60))
    assert lab.purge(now=at(120)) == 1
    assert lab.wallet("alice") == 1000
    assert lab.pending("bob", now=at(120)) == []
    assert second["status"] == "pending"


def test_snapshot_restore_keeps_wallets_blocks_pending_and_intimacy():
    lab = setup()
    first = lab.send("alice", "bob", "rose", now=NOW)
    second = lab.send("bob", "alice", "rose", now=NOW)
    lab.claim("alice", second["instance_id"], now=NOW)
    lab.block("carol", "bob")
    state = lab.snapshot()

    lab.send("alice", "bob", "rose", now=at(10))
    lab.claim("bob", first["instance_id"], now=at(10))

    restored = sg.SocialGiftGuard()
    restored.restore(state)
    assert restored.wallet("alice") == 900
    assert restored.wallet("bob") == 900
    assert [row["instance_id"] for row in restored.pending("bob", now=at(10))] == [
        first["instance_id"]]
    assert restored.intimacy("alice", "bob") == 30
    assert restored.stats() == {"sent": 2, "claimed": 1, "expired": 0, "blocked": 0}

    with pytest.raises(sg.GiftError) as exc:
        restored.send("carol", "bob", "rose", now=at(10))
    assert exc.value.code == "GIFT_BLOCKED"
    assert restored.claim("bob", first["instance_id"], now=at(10))["intimacy"] == 60

    valid = copy.deepcopy(state)
    version_two = copy.deepcopy(valid)
    version_two["version"] = 2
    bad_gifts = copy.deepcopy(valid)
    bad_gifts["gifts"] = "nope"
    bad_instances = copy.deepcopy(valid)
    bad_instances["instances"] = "nope"
    bad_requests = copy.deepcopy(valid)
    bad_requests["requests"] = "nope"
    bad_stats = copy.deepcopy(valid)
    bad_stats["stats"] = "nope"

    guard = sg.SocialGiftGuard()
    guard.restore(state)
    for bad in (None, {}, version_two, bad_gifts, bad_instances,
                bad_requests, bad_stats):
        with pytest.raises(sg.GiftError) as exc:
            guard.restore(bad)
        assert exc.value.code == "GIFT_SNAPSHOT_INVALID"
    assert guard.wallet("alice") == 900
    assert guard.stats()["sent"] == 2


def test_stats_count_sent_claimed_expired_and_blocked_then_reset():
    lab = setup(gift_ttl=60)
    first = lab.send("alice", "bob", "rose", now=NOW)
    lab.claim("bob", first["instance_id"], now=NOW)
    lab.send("alice", "bob", "rose", now=at(10))
    assert lab.purge(now=at(70)) == 1
    lab.block("carol", "alice")
    with pytest.raises(sg.GiftError) as exc:
        lab.send("alice", "carol", "rose", now=at(70))
    assert exc.value.code == "GIFT_BLOCKED"

    assert lab.stats() == {"sent": 2, "claimed": 1, "expired": 1, "blocked": 1}
    lab.reset_stats()
    assert lab.stats() == {"sent": 0, "claimed": 0, "expired": 0, "blocked": 0}


def test_concurrent_claim_of_one_gift_succeeds_exactly_once():
    lab = setup()
    instance = lab.send("alice", "bob", "rose", now=NOW)
    barrier = threading.Barrier(8)
    accepted = []
    rejected = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = lab.claim("bob", instance["instance_id"], now=NOW)
        except Exception as exc:
            code = getattr(exc, "code", repr(exc))
            with lock:
                rejected.append(code)
            return
        with lock:
            accepted.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert rejected == ["GIFT_ALREADY_CLAIMED"] * 7
    assert lab.intimacy("alice", "bob") == 30
    assert lab.stats()["claimed"] == 1
