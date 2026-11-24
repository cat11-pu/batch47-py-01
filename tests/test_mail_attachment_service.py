import threading

import pytest

import mail_attachment_service as ms

NOW = "2026-11-24T10:00:00+00:00"


def service(**kwargs):
    return ms.MailAttachmentService(**kwargs)


def items():
    return [{"item": "ore", "count": 3}, {"item": "herb", "count": 2}]


def deliver(service, mail_id="m1", **kwargs):
    params = {
        "recipient": "p1",
        "sender": "system",
        "subject": "supply drop",
        "attachments": items(),
        "now": NOW,
    }
    params.update(kwargs)
    return service.deliver(mail_id, **params)


def test_deliver_and_claim_returns_merged_stack():
    box = service()
    sent = deliver(box)
    assert sent["status"] == "delivered"
    assert sent["attachments"] == {"herb": 2, "ore": 3}
    assert sent["claimed"] is False

    claimed = box.claim("m1", player="p1", now=NOW)
    assert claimed == {
        "status": "claimed",
        "mail_id": "m1",
        "player": "p1",
        "items": {"herb": 2, "ore": 3},
        "cod_paid": 0,
    }
    assert box.status("m1")["claimed"] is True
    assert box.stats()["delivered"] == 1
    assert box.stats()["claimed"] == 1


def test_duplicate_delivery_is_idempotent_but_conflict_is_refused():
    box = service()
    first = deliver(box)
    again = deliver(box)
    assert again["status"] == "duplicate"
    assert again["mail_id"] == first["mail_id"]
    assert box.stats()["delivered"] == 1
    assert box.stats()["duplicates"] == 1

    with pytest.raises(ms.MailError) as exc:
        deliver(box, cod_fee=25)
    assert exc.value.code == "MAIL_CONFLICT"

    with pytest.raises(ms.MailError) as exc:
        deliver(box, recipient="p2")
    assert exc.value.code == "MAIL_CONFLICT"


def test_unknown_mail_and_foreign_owner_are_rejected():
    box = service()
    with pytest.raises(ms.MailError) as exc:
        box.claim("missing", player="p1", now=NOW)
    assert exc.value.code == "MAIL_NOT_FOUND"

    deliver(box)
    with pytest.raises(ms.MailError) as exc:
        box.claim("m1", player="p2", now=NOW)
    assert exc.value.code == "MAIL_NOT_OWNER"
    assert box.stats()["claimed"] == 0
    assert box.claim("m1", player="p1", now=NOW)["status"] == "claimed"


def test_mailbox_slots_are_freed_by_expiry_sweep():
    box = service(slot_limit=2, default_ttl=60)
    deliver(box, "m1")
    deliver(box, "m2")
    with pytest.raises(ms.MailError) as exc:
        deliver(box, "m3")
    assert exc.value.code == "MAILBOX_FULL"
    with pytest.raises(ms.MailError) as exc:
        box.claim("m3", player="p1", now=NOW)
    assert exc.value.code == "MAIL_NOT_FOUND"

    assert box.purge_expired(now="2026-11-24T10:01:00+00:00") == 2
    assert deliver(box, "m3")["status"] == "delivered"
    assert box.list_mailbox(player="p1", now=NOW)[0]["mail_id"] == "m3"


def test_expiry_boundary_is_inclusive_and_earlier_second_still_claims():
    box = service(default_ttl=60)
    deliver(box, "boundary")
    with pytest.raises(ms.MailError) as exc:
        box.claim("boundary", player="p1", now="2026-11-24T10:01:00+00:00")
    assert exc.value.code == "MAIL_EXPIRED"

    deliver(box, "just-before")
    ok = box.claim("just-before", player="p1",
                   now="2026-11-24T10:00:59+00:00")
    assert ok["status"] == "claimed"


def test_cod_shortfall_keeps_mail_pending_and_charges_once():
    box = service()
    box.credit("p1", 30)
    deliver(box, cod_fee=50)
    with pytest.raises(ms.MailError) as exc:
        box.claim("m1", player="p1", now=NOW)
    assert exc.value.code == "MAIL_COD_INSUFFICIENT"
    assert box.balance("p1") == 30
    assert box.status("m1")["claimed"] is False

    box.credit("p1", 20)
    paid = box.claim("m1", player="p1", now=NOW)
    assert paid["cod_paid"] == 50
    assert box.balance("p1") == 0
    again = box.claim("m1", player="p1", now=NOW)
    assert again["status"] == "already_claimed"
    assert box.balance("p1") == 0


def test_second_claim_does_not_regrant_and_unlocks_deletion():
    box = service()
    deliver(box)
    assert box.claim("m1", player="p1", now=NOW)["status"] == "claimed"
    replay = box.claim("m1", player="p1", now=NOW)
    assert replay["status"] == "already_claimed"
    assert replay["code"] == "MAIL_ALREADY_CLAIMED"
    assert box.stats()["claimed"] == 1
    assert box.stats()["duplicates"] == 1
    assert box.delete("m1", player="p1")["status"] == "deleted"


def test_attachment_validation_and_limit():
    box = service(attachment_limit=2)
    with pytest.raises(ms.MailError) as exc:
        deliver(box, attachments=[{"item": "a", "count": 1},
                                  {"item": "b", "count": 1},
                                  {"item": "c", "count": 1}])
    assert exc.value.code == "MAIL_TOO_MANY_ATTACHMENTS"

    with pytest.raises(ms.MailError) as exc:
        deliver(box, "bad-zero", attachments=[{"item": "a", "count": 0}])
    assert exc.value.code == "MAIL_BAD_ATTACHMENT"

    merged = deliver(box, "merged",
                     attachments=[{"item": "ore", "count": 2},
                                  {"item": "ore", "count": 3}])
    assert merged["attachments"] == {"ore": 5}


def test_claim_all_reports_each_mail_and_enforces_batch_limit():
    box = service(batch_limit=3)
    deliver(box, "m1")
    deliver(box, "m2", cod_fee=80)
    deliver(box, "m3")
    summary = box.claim_all(player="p1", now=NOW)
    assert [row["status"] for row in summary["results"]] == [
        "claimed", "rejected", "claimed"]
    assert summary["results"][1]["code"] == "MAIL_COD_INSUFFICIENT"
    assert summary["claimed"] == 2
    assert summary["rejected"] == 1
    assert box.stats()["claimed"] == 2

    deliver(box, "m4")
    deliver(box, "m5")
    deliver(box, "m6")
    with pytest.raises(ms.MailError) as exc:
        box.claim_all(player="p1", now=NOW)
    assert exc.value.code == "MAIL_BATCH_TOO_LARGE"


def test_delete_requires_claimed_or_empty_mail():
    box = service()
    deliver(box)
    with pytest.raises(ms.MailError) as exc:
        box.delete("m1", player="p1")
    assert exc.value.code == "MAIL_HAS_ATTACHMENTS"

    with pytest.raises(ms.MailError) as exc:
        box.delete("m1", player="p2")
    assert exc.value.code == "MAIL_NOT_OWNER"

    box.claim("m1", player="p1", now=NOW)
    assert box.delete("m1", player="p1")["status"] == "deleted"
    with pytest.raises(ms.MailError) as exc:
        box.delete("m1", player="p1")
    assert exc.value.code == "MAIL_NOT_FOUND"

    deliver(box, "empty", attachments=[])
    assert box.delete("empty", player="p1")["status"] == "deleted"


def test_mailbox_listing_orders_by_delivery_and_marks_state():
    box = service()
    deliver(box, "m1")
    deliver(box, "m2")
    deliver(box, "m3")
    box.claim("m2", player="p1", now=NOW)
    rows = box.list_mailbox(player="p1", now=NOW)
    assert [row["mail_id"] for row in rows] == ["m1", "m2", "m3"]
    assert [row["status"] for row in rows] == ["pending", "claimed", "pending"]

    assert box.purge_expired(now="2026-11-26T00:00:00+00:00") == 3
    assert box.list_mailbox(player="p1", now="2026-11-26T00:00:00+00:00") == []


def test_snapshot_restore_preserves_claims_balance_and_capacity():
    box = service()
    box.credit("p1", 100)
    deliver(box, "m1", cod_fee=40)
    box.claim("m1", player="p1", now=NOW)
    deliver(box, "m2")
    state = box.snapshot()
    deliver(box, "m3")
    box.claim("m2", player="p1", now=NOW)

    restored = service()
    restored.restore(state)
    assert restored.balance("p1") == 60
    assert restored.status("m1")["claimed"] is True
    assert restored.status("m2")["claimed"] is False
    with pytest.raises(ms.MailError) as exc:
        restored.status("m3")
    assert exc.value.code == "MAIL_NOT_FOUND"
    assert restored.claim("m1", player="p1", now=NOW)["status"] == "already_claimed"
    assert restored.claim("m2", player="p1", now=NOW)["status"] == "claimed"
    assert restored.stats()["delivered"] == 2
    assert restored.stats()["claimed"] == 2


def test_corrupt_snapshot_is_refused_without_mutating_state():
    box = service()
    deliver(box)
    box.claim("m1", player="p1", now=NOW)
    bad_states = [
        None,
        {},
        {"version": 2, "balances": {}, "mails": [], "seq": 0, "stats": {}},
        {"version": 1, "balances": [], "mails": [], "seq": 0, "stats": {}},
        {"version": 1, "balances": {}, "mails": "nope", "seq": 0, "stats": {}},
        {"version": 1, "balances": {}, "mails": [], "seq": 0, "stats": []},
        {"version": 1, "balances": {}, "mails": [{"mail_id": "x"}],
         "seq": 1, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(ms.MailError) as exc:
            box.restore(bad)
        assert exc.value.code == "MAIL_SNAPSHOT_INVALID"
    assert box.claim("m1", player="p1", now=NOW)["status"] == "already_claimed"


def test_concurrent_claim_settles_exactly_once():
    box = service()
    box.credit("p1", 100)
    deliver(box, cod_fee=100)
    barrier = threading.Barrier(8)
    results = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = box.claim("m1", player="p1", now=NOW)
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            row = {"status": "error", "code": repr(exc)}
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["already_claimed"] * 7 + ["claimed"]
    assert box.balance("p1") == 0
    assert box.stats()["claimed"] == 1
    assert box.stats()["duplicates"] == 7


def test_stats_and_purge_accounting():
    box = service(default_ttl=30)
    deliver(box, "m1")
    deliver(box, "m2")
    deliver(box, "m1")
    box.claim("m1", player="p1", now=NOW)
    with pytest.raises(ms.MailError):
        box.claim("m2", player="p2", now=NOW)
    stats = box.stats()
    assert stats["delivered"] == 2
    assert stats["claimed"] == 1
    assert stats["duplicates"] == 1
    assert stats["rejected"] == 1
    assert box.purge_expired(now="2026-11-24T10:00:31+00:00") == 2
    assert box.stats()["expired"] == 2
    assert box.purge_expired(now="2026-11-24T10:00:31+00:00") == 0
    box.reset_stats()
    assert box.stats() == {"delivered": 0, "claimed": 0, "duplicates": 0,
                           "expired": 0, "rejected": 0}
