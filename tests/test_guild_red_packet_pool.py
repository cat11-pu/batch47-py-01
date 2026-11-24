import threading

import pytest

import guild_red_packet_pool as pool

NOW = "2026-11-24T10:00:00+00:00"
LATER = "2026-11-24T10:01:00+00:00"


def make_pool(**kwargs):
    return pool.GuildRedPacketPool(**kwargs)


def guild_pool(**kwargs):
    box = make_pool(**kwargs)
    box.join_guild("gm", "g1")
    box.credit("gm", 100)
    for member in ("m1", "m2", "m3", "m4"):
        box.join_guild(member, "g1")
    return box


def create(box, packet_id="p1", **kwargs):
    params = {"guild": "g1", "sender": "gm", "total_coins": 10,
              "slots": 3, "now": NOW}
    params.update(kwargs)
    return box.create_packet(packet_id, **params)


def test_create_escrows_coins_and_reports_split_plan():
    box = guild_pool()
    created = create(box)
    assert created["state"] == "active"
    assert created["claimed"] == 0
    assert created["remaining"] == 3
    assert created["coins_left"] == 10
    assert created["base"] == 3
    assert created["extra"] == 1
    assert created["refunded"] == 0
    assert box.balance("gm") == 90
    assert box.stats()["created"] == 1


def test_create_validates_amounts_membership_and_balance():
    box = guild_pool()
    with pytest.raises(pool.PacketError) as exc:
        create(box, total_coins=2, slots=3)
    assert exc.value.code == "PACKET_INVALID"
    with pytest.raises(pool.PacketError) as exc:
        create(box, slots=0)
    assert exc.value.code == "PACKET_BAD_REQUEST"
    with pytest.raises(pool.PacketError) as exc:
        create(box, sender="outsider")
    assert exc.value.code == "PACKET_NOT_MEMBER"
    with pytest.raises(pool.PacketError) as exc:
        create(box, total_coins=200, slots=2)
    assert exc.value.code == "PACKET_INSUFFICIENT_BALANCE"
    with pytest.raises(pool.PacketError) as exc:
        box.credit("gm", -1)
    assert exc.value.code == "PACKET_BAD_REQUEST"
    assert box.balance("gm") == 100


def test_duplicate_create_is_idempotent_but_changed_payload_conflicts():
    box = guild_pool()
    create(box)
    duplicate = create(box)
    assert duplicate["status"] == "duplicate"
    assert box.balance("gm") == 90
    assert box.stats()["created"] == 1
    with pytest.raises(pool.PacketError) as exc:
        create(box, total_coins=12)
    assert exc.value.code == "PACKET_CONFLICT"


def test_claim_order_determines_the_remainder_shares():
    box = guild_pool()
    create(box)
    first = box.claim("p1", player="m1", now=NOW)
    second = box.claim("p1", player="m2", now=NOW)
    third = box.claim("p1", player="m3", now=NOW)
    assert [first["amount"], second["amount"], third["amount"]] == [4, 3, 3]
    assert [first["remaining"], second["remaining"], third["remaining"]] == [2, 1, 0]
    assert box.status("p1")["state"] == "exhausted"
    empty = box.claim("p1", player="m4", now=NOW)
    assert empty["status"] == "empty"
    assert empty["code"] == "PACKET_EMPTY"
    assert sum(box.balance(name) for name in ("m1", "m2", "m3")) == 10
    assert box.status("p1")["coins_left"] == 0


def test_sender_and_outsiders_cannot_claim():
    box = guild_pool()
    create(box)
    with pytest.raises(pool.PacketError) as exc:
        box.claim("p1", player="gm", now=NOW)
    assert exc.value.code == "PACKET_SENDER"
    with pytest.raises(pool.PacketError) as exc:
        box.claim("p1", player="stranger", now=NOW)
    assert exc.value.code == "PACKET_NOT_MEMBER"
    box.join_guild("stranger", "g1")
    assert box.claim("p1", player="stranger", now=NOW)["amount"] == 4


def test_repeat_claim_returns_the_same_share_without_crediting_again():
    box = guild_pool()
    create(box)
    assert box.claim("p1", player="m1", now=NOW)["amount"] == 4
    repeat = box.claim("p1", player="m1", now=NOW)
    assert repeat["status"] == "already_claimed"
    assert repeat["code"] == "PACKET_ALREADY_CLAIMED"
    assert repeat["amount"] == 4
    assert box.balance("m1") == 4
    assert box.stats()["claimed"] == 1


def test_claim_after_expiry_refunds_the_remainder_to_the_sender():
    box = guild_pool(ttl=60)
    create(box)
    box.claim("p1", player="m1", now=NOW)
    with pytest.raises(pool.PacketError) as exc:
        box.claim("p1", player="m2", now=LATER)
    assert exc.value.code == "PACKET_EXPIRED"
    assert box.balance("gm") == 96
    state = box.status("p1")
    assert state["state"] == "expired"
    assert state["coins_left"] == 0
    assert state["refunded"] == 6
    assert box.stats()["coins_refunded"] == 6

    with pytest.raises(pool.PacketError) as exc:
        box.claim("p1", player="m3", now=LATER)
    assert exc.value.code == "PACKET_EXPIRED"
    assert box.balance("gm") == 96


def test_expire_sweep_refunds_once_per_packet():
    box = guild_pool(ttl=60)
    create(box, "p1")
    create(box, "p2")
    assert box.balance("gm") == 80
    assert box.expire(now=LATER) == 2
    assert box.balance("gm") == 100
    assert box.expire(now=LATER) == 0
    assert box.status("p1")["state"] == "expired"
    assert box.status("p2")["state"] == "expired"
    assert box.stats()["expired"] == 2
    assert box.stats()["coins_refunded"] == 20


def test_revoke_refunds_only_before_the_first_claim():
    box = guild_pool()
    create(box, "p1")
    revoked = box.revoke("p1", sender="gm", now=NOW)
    assert revoked["state"] == "revoked"
    assert box.balance("gm") == 100
    assert revoked["refunded"] == 10
    assert box.revoke("p1", sender="gm", now=NOW)["state"] == "revoked"
    assert box.balance("gm") == 100
    assert box.stats()["revoked"] == 1
    with pytest.raises(pool.PacketError) as exc:
        box.claim("p1", player="m1", now=NOW)
    assert exc.value.code == "PACKET_REVOKED"

    create(box, "p2")
    box.claim("p2", player="m1", now=NOW)
    with pytest.raises(pool.PacketError) as exc:
        box.revoke("p2", sender="gm", now=NOW)
    assert exc.value.code == "PACKET_LOCKED"


def test_active_packet_capacity_is_released_by_exhaustion():
    box = guild_pool(max_packets=1)
    create(box, "p1", total_coins=3, slots=3)
    with pytest.raises(pool.PacketError) as exc:
        create(box, "p2", total_coins=3, slots=3)
    assert exc.value.code == "PACKET_CAPACITY"
    for member in ("m1", "m2", "m3"):
        box.claim("p1", player=member, now=NOW)
    assert create(box, "p2", total_coins=3, slots=3)["state"] == "active"


def test_listing_filters_by_guild_and_status_reports_claims():
    box = guild_pool()
    box.join_guild("gm2", "g2")
    box.credit("gm2", 50)
    create(box, "p1")
    create(box, "p2", guild="g2", sender="gm2", total_coins=6, slots=2)
    box.claim("p1", player="m1", now=NOW)
    assert [row["packet_id"] for row in box.list_packets()] == ["p1", "p2"]
    assert [row["packet_id"] for row in box.list_packets("g1")] == ["p1"]
    assert [row["packet_id"] for row in box.list_packets("g2")] == ["p2"]
    state = box.status("p1")
    assert state["claims"] == {"m1": 4}
    assert state["claimed"] == 1
    assert state["remaining"] == 2


def test_coins_are_conserved_across_claims_and_refunds():
    box = guild_pool(ttl=60)
    create(box)
    box.claim("p1", player="m1", now=NOW)
    box.expire(now=LATER)
    balances = box.balance("gm") + box.balance("m1")
    assert balances == 100
    state = box.status("p1")
    assert state["coins_left"] == 0
    assert state["refunded"] == 6


def test_snapshot_restore_keeps_claim_order_and_balances():
    box = guild_pool()
    create(box)
    box.claim("p1", player="m1", now=NOW)
    state = box.snapshot()
    box.claim("p1", player="m2", now=NOW)

    restored = make_pool()
    restored.restore(state)
    assert restored.balance("gm") == 90
    assert restored.status("p1")["claims"] == {"m1": 4}
    assert restored.status("p1")["coins_left"] == 6
    assert restored.claim("p1", player="m2", now=NOW)["amount"] == 3
    assert restored.claim("p1", player="m3", now=NOW)["amount"] == 3
    assert restored.status("p1")["state"] == "exhausted"
    with pytest.raises(pool.PacketError) as exc:
        restored.status("p2")
    assert exc.value.code == "PACKET_NOT_FOUND"


def test_corrupt_snapshot_is_refused_without_mutation():
    box = guild_pool()
    create(box)
    valid = box.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "balances": {}, "guilds": {}, "packets": [],
         "seq": 0, "stats": {}},
        {"version": 1, "balances": [], "guilds": {}, "packets": [],
         "seq": 0, "stats": {}},
        {"version": 1, "balances": {}, "guilds": {}, "packets": "nope",
         "seq": 0, "stats": {}},
        {"version": 1, "balances": {}, "guilds": {}, "packets": [],
         "seq": 0, "stats": []},
        {"version": 1, "balances": {}, "guilds": {},
         "packets": [{"packet_id": "x"}], "seq": 1, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(pool.PacketError) as exc:
            box.restore(bad)
        assert exc.value.code == "PACKET_SNAPSHOT_INVALID"
    assert box.balance("gm") == 90
    assert box.status("p1")["state"] == "active"
    box.restore(valid)


def test_concurrent_claims_split_the_packet_exactly_once():
    box = guild_pool()
    for member in ("m5", "m6"):
        box.join_guild(member, "g1")
    create(box)
    barrier = threading.Barrier(6)
    results = []
    guard = threading.Lock()

    def worker(member):
        barrier.wait()
        try:
            row = box.claim("p1", player=member, now=NOW)
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            row = {"status": "error", "code": repr(exc)}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker, args=(member,))
               for member in ("m1", "m2", "m3", "m4", "m5", "m6")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claims = [row for row in results if row["status"] == "claimed"]
    empties = [row for row in results if row["status"] == "empty"]
    assert len(claims) == 3
    assert len(empties) == 3
    assert sorted(row["amount"] for row in claims) == [3, 3, 4]
    assert box.status("p1")["coins_left"] == 0
    assert sum(box.balance(name) for name in
               ("m1", "m2", "m3", "m4", "m5", "m6")) == 10
    assert box.stats()["claimed"] == 3
    box.reset_stats()
    assert box.stats() == {"created": 0, "claimed": 0, "expired": 0,
                           "revoked": 0, "coins_claimed": 0,
                           "coins_refunded": 0, "rejected": 0}
