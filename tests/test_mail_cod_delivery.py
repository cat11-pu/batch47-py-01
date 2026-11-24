import threading

import pytest

import mail_cod_delivery as mc

BASE = 1000


def mailbox(**kwargs):
    box = mc.CodMailbox(**kwargs)
    box.grant_items("seller", "sword", 1)
    box.grant_items("seller", "potion", 10)
    box.grant_currency("buyer", 100)
    return box


def test_send_moves_attachments_into_custody_and_lists_mail():
    box = mailbox()
    mail = box.send("seller", "buyer", attachments={"sword": 1, "potion": 4},
                    cod_fee=30, mail_id="m-1", now=BASE)
    assert mail["mail_id"] == "m-1"
    assert mail["sender"] == "seller"
    assert mail["recipient"] == "buyer"
    assert mail["attachments"] == {"sword": 1, "potion": 4}
    assert mail["cod_fee"] == 30
    assert mail["created_at"] == BASE
    assert mail["expires_at"] == BASE + 86400
    assert mail["status"] == "unclaimed"
    assert box.inventory("seller") == {"potion": 6}
    listed = box.mailbox("buyer", now=BASE + 10)
    assert [row["mail_id"] for row in listed] == ["m-1"]
    assert box.stats()["sent"] == 1


def test_send_rejects_invalid_payloads():
    box = mailbox()
    with pytest.raises(mc.MailError) as exc:
        box.send("seller", "seller", attachments={"sword": 1}, now=BASE)
    assert exc.value.code == "MAIL_INVALID"
    with pytest.raises(mc.MailError) as exc:
        box.send("seller", "buyer", attachments={}, currency=0, now=BASE)
    assert exc.value.code == "MAIL_INVALID"
    with pytest.raises(mc.MailError) as exc:
        box.send("seller", "buyer", attachments={"sword": 1}, cod_fee=-5,
                 now=BASE)
    assert exc.value.code == "MAIL_INVALID"
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-1",
             now=BASE)
    with pytest.raises(mc.MailError) as exc:
        box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-1",
                 now=BASE + 1)
    assert exc.value.code == "MAIL_DUPLICATE"


def test_send_requires_sender_to_own_attachments():
    box = mailbox()
    with pytest.raises(mc.MailError) as exc:
        box.send("seller", "buyer", attachments={"sword": 2}, now=BASE)
    assert exc.value.code == "MAIL_INSUFFICIENT_ITEMS"
    assert box.mailbox("buyer", now=BASE) == []
    assert box.inventory("seller") == {"sword": 1, "potion": 10}


def test_quota_sweeps_expired_then_rejects():
    box = mailbox(quota=2, ttl=60)
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-1",
             now=BASE)
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-2",
             now=BASE + 10)
    with pytest.raises(mc.MailError) as exc:
        box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-3",
                 now=BASE + 20)
    assert exc.value.code == "MAIL_QUOTA"
    late = box.send("seller", "buyer", attachments={"potion": 1},
                    mail_id="m-4", now=BASE + 65)
    assert late["mail_id"] == "m-4"
    assert box.returned("seller") == {"potion": 1}
    assert [row["mail_id"] for row in box.mailbox("buyer", now=BASE + 65)] == [
        "m-2", "m-4"]


def test_claim_charges_cod_fee_and_transfers_attachments():
    box = mailbox()
    box.send("seller", "buyer", attachments={"sword": 1, "potion": 4},
             currency=5, cod_fee=30, mail_id="m-1", now=BASE)
    out = box.claim("m-1", player="buyer", now=BASE + 5)
    assert out["status"] == "claimed"
    assert out["items"] == {"sword": 1, "potion": 4}
    assert out["currency"] == 5
    assert out["fee"] == 30
    assert box.balance("buyer") == 75
    assert box.balance("seller") == 30
    assert box.inventory("buyer") == {"sword": 1, "potion": 4}
    assert box.mailbox("buyer", now=BASE + 5) == []
    assert box.stats()["claimed"] == 1


def test_claim_without_fee_funds_changes_nothing():
    box = mailbox()
    box.send("seller", "buyer", attachments={"potion": 5}, cod_fee=150,
             mail_id="m-1", now=BASE)
    with pytest.raises(mc.MailError) as exc:
        box.claim("m-1", player="buyer", now=BASE + 1)
    assert exc.value.code == "MAIL_INSUFFICIENT_FUNDS"
    assert box.balance("buyer") == 100
    assert box.inventory("buyer") == {}
    assert [row["mail_id"] for row in box.mailbox("buyer", now=BASE + 1)] == [
        "m-1"]
    box.grant_currency("buyer", 50)
    ok = box.claim("m-1", player="buyer", now=BASE + 2)
    assert ok["fee"] == 150
    assert box.balance("buyer") == 0


def test_claim_requires_recipient_and_known_mail():
    box = mailbox()
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-1",
             now=BASE)
    box.grant_currency("mallory", 100)
    with pytest.raises(mc.MailError) as exc:
        box.claim("m-1", player="mallory", now=BASE + 1)
    assert exc.value.code == "MAIL_NOT_RECIPIENT"
    with pytest.raises(mc.MailError) as exc:
        box.claim("m-404", player="buyer", now=BASE + 1)
    assert exc.value.code == "MAIL_UNKNOWN"


def test_second_claim_is_already_claimed_without_regrant():
    box = mailbox()
    box.send("seller", "buyer", attachments={"potion": 5}, cod_fee=20,
             mail_id="m-1", now=BASE)
    box.claim("m-1", player="buyer", now=BASE + 1)
    with pytest.raises(mc.MailError) as exc:
        box.claim("m-1", player="buyer", now=BASE + 2)
    assert exc.value.code == "MAIL_ALREADY_CLAIMED"
    assert box.inventory("buyer") == {"potion": 5}
    assert box.balance("buyer") == 80
    assert box.balance("seller") == 20
    assert box.stats()["claimed"] == 1


def test_claim_request_id_is_idempotent_and_conflicts_on_reuse():
    box = mailbox()
    box.send("seller", "buyer", attachments={"potion": 5}, cod_fee=20,
             mail_id="m-1", now=BASE)
    first = box.claim("m-1", player="buyer", now=BASE + 1, request_id="rq-1")
    second = box.claim("m-1", player="buyer", now=BASE + 2, request_id="rq-1")
    assert second == first
    assert box.balance("buyer") == 80
    assert box.balance("seller") == 20
    with pytest.raises(mc.MailError) as exc:
        box.claim("m-1", player="mallory", now=BASE + 3, request_id="rq-1")
    assert exc.value.code == "MAIL_REQUEST_CONFLICT"


def test_expiry_boundary_returns_attachments_to_sender():
    box = mailbox(ttl=600)
    box.send("seller", "buyer", attachments={"potion": 5}, currency=10,
             cod_fee=25, mail_id="m-1", now=BASE)
    with pytest.raises(mc.MailError) as exc:
        box.claim("m-1", player="buyer", now=BASE + 600)
    assert exc.value.code == "MAIL_EXPIRED"
    assert box.inventory("buyer") == {}
    assert box.balance("buyer") == 100
    assert box.returned("seller") == {"potion": 5}
    assert box.balance("seller") == 10
    assert box.stats()["expired"] == 1


def test_expire_counts_due_mails_and_hides_them():
    box = mailbox(ttl=100)
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-1",
             now=BASE)
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-2",
             now=BASE + 50)
    assert box.expire(now=BASE + 99) == 0
    assert box.expire(now=BASE + 100) == 1
    assert [row["mail_id"] for row in box.mailbox("buyer", now=BASE + 100)] == [
        "m-2"]
    assert box.expire(now=BASE + 150) == 1
    assert box.mailbox("buyer", now=BASE + 150) == []
    assert box.returned("seller") == {"potion": 2}
    assert box.stats()["expired"] == 2


def test_claim_batch_reports_rows_independently():
    box = mailbox(batch_limit=3)
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-1",
             now=BASE)
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-2",
             now=BASE + 1)
    box.claim("m-2", player="buyer", now=BASE + 2)
    rows = box.claim_batch("buyer", ["m-1", "m-404", "m-2"], now=BASE + 3)
    assert [row["status"] for row in rows] == ["claimed", "rejected", "rejected"]
    assert rows[1]["code"] == "MAIL_UNKNOWN"
    assert rows[2]["code"] == "MAIL_ALREADY_CLAIMED"
    with pytest.raises(mc.MailError) as exc:
        box.claim_batch("buyer", ["m-1", "m-2", "m-3", "m-4"], now=BASE + 3)
    assert exc.value.code == "MAIL_BATCH_TOO_LARGE"


def test_attachment_count_limit():
    box = mailbox(max_attachments=2)
    box.grant_items("seller", "gem", 1)
    with pytest.raises(mc.MailError) as exc:
        box.send("seller", "buyer", attachments={"sword": 1, "potion": 1,
                                                 "gem": 1}, now=BASE)
    assert exc.value.code == "MAIL_TOO_MANY_ATTACHMENTS"
    assert box.inventory("seller")["sword"] == 1


def test_concurrent_claim_charges_fee_once():
    box = mailbox()
    box.send("seller", "buyer", attachments={"potion": 5}, cod_fee=20,
             mail_id="m-1", now=BASE)
    barrier = threading.Barrier(6)
    results = []
    guard = threading.Lock()

    def worker():
        barrier.wait()
        try:
            box.claim("m-1", player="buyer", now=BASE + 1)
            row = "claimed"
        except mc.MailError as exc:
            row = exc.code
        except Exception as exc:  # pragma: no cover
            row = repr(exc)
        with guard:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("claimed") == 1
    assert results.count("MAIL_ALREADY_CLAIMED") == 5
    assert box.balance("buyer") == 80
    assert box.balance("seller") == 20
    assert box.inventory("buyer") == {"potion": 5}


def test_snapshot_restore_and_corrupt_rejection():
    box = mailbox()
    box.send("seller", "buyer", attachments={"potion": 5}, cod_fee=20,
             mail_id="m-1", now=BASE)
    box.claim("m-1", player="buyer", now=BASE + 1)
    box.send("seller", "buyer", attachments={"potion": 1}, mail_id="m-2",
             now=BASE + 2)
    snap = box.snapshot()
    assert snap["version"] == 1
    restored = mc.CodMailbox()
    restored.restore(snap)
    assert restored.balance("buyer") == 80
    assert restored.inventory("buyer") == {"potion": 5}
    assert [row["mail_id"] for row in restored.mailbox("buyer", now=BASE + 3)] == [
        "m-2"]
    snap["players"]["buyer"]["balance"] = 999999
    assert restored.balance("buyer") == 80
    bad_states = [
        None,
        {},
        {"version": 2, "players": {}, "mails": {}, "stats": {}},
        {"version": 1,
         "players": {"buyer": {"items": {}, "balance": "x", "returned": {}}},
         "mails": {}, "stats": {}},
    ]
    for state in bad_states:
        with pytest.raises(mc.MailError) as exc:
            restored.restore(state)
        assert exc.value.code == "MAIL_SNAPSHOT_INVALID"
    assert restored.balance("buyer") == 80
