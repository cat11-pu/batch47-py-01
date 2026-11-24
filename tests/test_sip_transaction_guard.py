import threading

import pytest

import sip_transaction_guard as sip

NOW = "2026-11-24T10:00:00+00:00"


def message(**overrides):
    base = {
        "call_id": "call-1",
        "method": "INVITE",
        "cseq": 1,
        "from_tag": "tag-a",
        "branch": "z9hG4bK-1",
        "max_forwards": 70,
    }
    base.update(overrides)
    return base


def guard_with_dialog(msg=None, **kwargs):
    guard = sip.SipTransactionGuard(**kwargs)
    opened = guard.receive(msg if msg is not None else message(), now=NOW)
    return guard, opened["dialog_id"]


def test_registration_binding_lifetime_and_removal():
    guard = sip.SipTransactionGuard()
    stored = guard.register_contact("sip:alice@example.com",
                                    contact="sip:alice@10.0.0.5:5060",
                                    expires=120, now=NOW)
    assert stored["expires"] == 120
    assert stored["expires_at"] == 1795514520
    found = guard.lookup_contact("sip:alice@example.com",
                                 now="2026-11-24T10:01:00+00:00")
    assert found["contact"] == "sip:alice@10.0.0.5:5060"
    assert found["expires_remaining"] == 60
    assert guard.lookup_contact("sip:alice@example.com",
                                now="2026-11-24T10:02:00+00:00") is None

    guard.register_contact("sip:alice@example.com",
                           contact="sip:alice@10.0.0.5:5060", now=NOW)
    assert guard.register_contact("sip:alice@example.com",
                                  contact="sip:alice@10.0.0.5:5060",
                                  expires=0, now=NOW) == {
        "aor": "sip:alice@example.com", "removed": True, "expires": 0}
    with pytest.raises(sip.SipError) as exc:
        guard.register_contact("sip:bob@example.com", contact="sip:bob@10.0.0.6",
                               expires=0, now=NOW)
    assert exc.value.code == "SIP_UNKNOWN_BINDING"
    with pytest.raises(sip.SipError) as exc:
        guard.register_contact("alice@example.com", contact="sip:x", now=NOW)
    assert exc.value.code == "SIP_BAD_AOR"
    with pytest.raises(sip.SipError) as exc:
        guard.register_contact("sip:alice@example.com", contact="http://x", now=NOW)
    assert exc.value.code == "SIP_BAD_CONTACT"
    for bad in (-1, 90000):
        with pytest.raises(sip.SipError) as exc:
            guard.register_contact("sip:alice@example.com", contact="sip:x",
                                   expires=bad, now=NOW)
        assert exc.value.code == "SIP_BAD_EXPIRES"


def test_purge_drops_expired_binding_entries():
    guard = sip.SipTransactionGuard()
    guard.register_contact("sip:alice@example.com", contact="sip:alice@10.0.0.5",
                           expires=10, now=NOW)
    assert guard.purge(now="2026-11-24T10:00:20+00:00") == {
        "dialogs": 0, "bindings": 1, "transactions": 0}
    assert guard.lookup_contact("sip:alice@example.com", now=NOW) is None


def test_request_validation_covers_headers_branch_and_forwards():
    guard = sip.SipTransactionGuard()
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(call_id=""), now=NOW)
    assert exc.value.code == "SIP_MALFORMED"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(method="PUBLISH"), now=NOW)
    assert exc.value.code == "SIP_METHOD_NOT_ALLOWED"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(branch="legacy-branch-1"), now=NOW)
    assert exc.value.code == "SIP_BAD_BRANCH"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(max_forwards=0), now=NOW)
    assert exc.value.code == "SIP_MAX_FORWARDS"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(max_forwards=71), now=NOW)
    assert exc.value.code == "SIP_MAX_FORWARDS"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(cseq=0), now=NOW)
    assert exc.value.code == "SIP_MALFORMED"
    payload = message()
    del payload["max_forwards"]
    with pytest.raises(sip.SipError) as exc:
        guard.receive(payload, now=NOW)
    assert exc.value.code == "SIP_MALFORMED"
    assert guard.stats()["accepted"] == 0


def test_invite_dialog_moves_from_early_to_confirmed():
    guard = sip.SipTransactionGuard()
    opened = guard.receive(message(), now=NOW)
    assert opened == {"status": "accepted", "dialog_id": "call-1/tag-a",
                      "cseq": 1, "state": "early"}
    ringing = guard.respond(opened["dialog_id"], status=180, now=NOW)
    assert ringing == {"dialog_id": "call-1/tag-a", "status": 180,
                       "state": "early", "final": False}
    ok = guard.respond(opened["dialog_id"], status=200, now=NOW)
    assert ok == {"dialog_id": "call-1/tag-a", "status": 200,
                  "state": "confirmed", "final": True}
    with pytest.raises(sip.SipError) as exc:
        guard.respond(opened["dialog_id"], status=200, now=NOW)
    assert exc.value.code == "SIP_ALREADY_CONFIRMED"


def test_retransmission_is_suppressed_and_new_branch_conflicts():
    guard = sip.SipTransactionGuard()
    first = guard.receive(message(), now=NOW)
    second = guard.receive(message(), now=NOW)
    assert first["status"] == "accepted"
    assert second == {"status": "retransmission", "dialog_id": "call-1/tag-a",
                      "cseq": 1, "state": "early"}
    assert guard.stats()["accepted"] == 1
    assert guard.stats()["retransmissions"] == 1
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(branch="z9hG4bK-2"), now=NOW)
    assert exc.value.code == "SIP_DUPLICATE_DIALOG"


def test_cseq_must_increase_per_call_id():
    guard = sip.SipTransactionGuard()
    guard.receive(message(call_id="call-2", cseq=5), now=NOW)
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(call_id="call-2", method="OPTIONS", cseq=5,
                              branch="z9hG4bK-2"), now=NOW)
    assert exc.value.code == "SIP_CSEQ_OUT_OF_ORDER"
    assert guard.receive(message(call_id="call-2", method="OPTIONS", cseq=6,
                                 branch="z9hG4bK-3"), now=NOW)["cseq"] == 6
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(call_id="call-2", method="OPTIONS", cseq=6,
                              branch="z9hG4bK-4"), now=NOW)
    assert exc.value.code == "SIP_CSEQ_OUT_OF_ORDER"


def test_ack_requires_confirmed_dialog_and_matching_cseq():
    guard, dialog_id = guard_with_dialog()
    with pytest.raises(sip.SipError) as exc:
        guard.ack(dialog_id, cseq=1, now=NOW)
    assert exc.value.code == "SIP_DIALOG_EARLY"
    guard.respond(dialog_id, status=200, now=NOW)
    with pytest.raises(sip.SipError) as exc:
        guard.ack(dialog_id, cseq=2, now=NOW)
    assert exc.value.code == "SIP_CSEQ_OUT_OF_ORDER"
    assert guard.ack(dialog_id, cseq=1, now=NOW) == {
        "dialog_id": dialog_id, "state": "confirmed", "acked": True}
    with pytest.raises(sip.SipError) as exc:
        guard.ack(dialog_id, cseq=1, now=NOW)
    assert exc.value.code == "SIP_DUPLICATE_ACK"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(method="ACK", branch="z9hG4bK-2"), now=NOW)
    assert exc.value.code == "SIP_DUPLICATE_ACK"


def test_cancel_only_works_before_final_response():
    guard, dialog_id = guard_with_dialog()
    assert guard.cancel(dialog_id, now=NOW) == {"dialog_id": dialog_id,
                                                "state": "cancelled"}
    with pytest.raises(sip.SipError) as exc:
        guard.respond(dialog_id, status=200, now=NOW)
    assert exc.value.code == "SIP_DIALOG_TERMINATED"
    with pytest.raises(sip.SipError) as exc:
        guard.cancel(dialog_id, now=NOW)
    assert exc.value.code == "SIP_DIALOG_TERMINATED"

    late = guard.receive(message(call_id="call-late"), now=NOW)
    guard.respond(late["dialog_id"], status=200, now=NOW)
    with pytest.raises(sip.SipError) as exc:
        guard.cancel(late["dialog_id"], now=NOW)
    assert exc.value.code == "SIP_CANCEL_TOO_LATE"


def test_bye_terminates_dialog_once():
    guard, dialog_id = guard_with_dialog(msg=message(call_id="call-6"))
    guard.respond(dialog_id, status=200, now=NOW)
    bye = guard.receive(message(call_id="call-6", method="BYE", cseq=2,
                                branch="z9hG4bK-bye"), now=NOW)
    assert bye["state"] == "terminated"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(call_id="call-6", method="BYE", cseq=3,
                              branch="z9hG4bK-bye2"), now=NOW)
    assert exc.value.code == "SIP_DIALOG_TERMINATED"
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(call_id="call-ghost", method="BYE", cseq=1,
                              branch="z9hG4bK-bye3"), now=NOW)
    assert exc.value.code == "SIP_UNKNOWN_DIALOG"


def test_respond_and_terminate_reject_unknown_or_final_dialogs():
    guard, dialog_id = guard_with_dialog(msg=message(call_id="call-7"))
    with pytest.raises(sip.SipError) as exc:
        guard.respond("call-7/ghost", status=200, now=NOW)
    assert exc.value.code == "SIP_UNKNOWN_DIALOG"
    with pytest.raises(sip.SipError) as exc:
        guard.terminate("call-7/ghost", now=NOW)
    assert exc.value.code == "SIP_UNKNOWN_DIALOG"
    assert guard.terminate(dialog_id, now=NOW) == {"dialog_id": dialog_id,
                                                   "state": "terminated"}
    with pytest.raises(sip.SipError) as exc:
        guard.terminate(dialog_id, now=NOW)
    assert exc.value.code == "SIP_DIALOG_TERMINATED"
    for bad in (99, 700, True, "200"):
        with pytest.raises(sip.SipError) as exc:
            guard.respond(dialog_id, status=bad, now=NOW)
        assert exc.value.code == "SIP_BAD_STATUS"


def test_transaction_table_is_bounded_and_expires():
    guard = sip.SipTransactionGuard(max_transactions=2, transaction_ttl=64)
    guard.receive(message(call_id="call-a", method="OPTIONS"), now=NOW)
    guard.receive(message(call_id="call-b", method="OPTIONS"), now=NOW)
    with pytest.raises(sip.SipError) as exc:
        guard.receive(message(call_id="call-c", method="OPTIONS"), now=NOW)
    assert exc.value.code == "SIP_TRANSACTION_OVERFLOW"
    assert guard.receive(message(call_id="call-a", method="OPTIONS"),
                         now=NOW)["status"] == "retransmission"
    later = "2026-11-24T10:01:05+00:00"
    assert guard.receive(message(call_id="call-c", method="OPTIONS",
                                 branch="z9hG4bK-late"), now=later)["status"] == "accepted"


def test_purge_removes_idle_dialogs_after_timeout():
    guard, dialog_id = guard_with_dialog(dialog_timeout=30,
                                         msg=message(call_id="call-8"))
    assert guard.purge(now="2026-11-24T10:00:29+00:00")["dialogs"] == 0
    assert guard.purge(now="2026-11-24T10:00:30+00:00") == {
        "dialogs": 1, "bindings": 0, "transactions": 0}
    with pytest.raises(sip.SipError) as exc:
        guard.respond(dialog_id, status=200, now=NOW)
    assert exc.value.code == "SIP_UNKNOWN_DIALOG"


def test_stats_track_accepts_replays_and_terminations():
    guard = sip.SipTransactionGuard()
    guard.receive(message(call_id="call-9"), now=NOW)
    guard.receive(message(call_id="call-9"), now=NOW)
    guard.receive(message(call_id="call-9", method="OPTIONS", cseq=2,
                          branch="z9hG4bK-opt"), now=NOW)
    guard.receive(message(call_id="call-9", method="BYE", cseq=3,
                          branch="z9hG4bK-bye"), now=NOW)
    assert guard.stats() == {"accepted": 3, "retransmissions": 1,
                             "dialogs_opened": 1, "dialogs_terminated": 1}
    guard.reset_stats()
    assert guard.stats() == {"accepted": 0, "retransmissions": 0,
                             "dialogs_opened": 0, "dialogs_terminated": 0}


def test_snapshot_restore_keeps_dialog_and_transaction_state():
    guard = sip.SipTransactionGuard()
    guard.register_contact("sip:alice@example.com", contact="sip:alice@10.0.0.5",
                           expires=600, now=NOW)
    opened = guard.receive(message(call_id="call-10"), now=NOW)
    dialog_id = opened["dialog_id"]
    guard.respond(dialog_id, status=200, now=NOW)
    state = guard.snapshot()
    guard.ack(dialog_id, cseq=1, now=NOW)

    restored = sip.SipTransactionGuard()
    restored.restore(state)
    assert restored.lookup_contact("sip:alice@example.com", now=NOW)["contact"] == \
        "sip:alice@10.0.0.5"
    assert restored.ack(dialog_id, cseq=1, now=NOW)["acked"] is True
    replayed = restored.receive(message(call_id="call-10"), now=NOW)
    assert replayed["status"] == "retransmission"
    with pytest.raises(sip.SipError) as exc:
        restored.receive(message(call_id="call-10", method="OPTIONS", cseq=1,
                                 branch="z9hG4bK-old"), now=NOW)
    assert exc.value.code == "SIP_CSEQ_OUT_OF_ORDER"

    state["dialogs"].clear()
    state["stats"]["accepted"] = 99
    assert restored.stats()["accepted"] == 1

    good_stats = {name: 0 for name in sip.STAT_KEYS}
    bad_states = [
        None,
        {},
        {"version": 2, "bindings": {}, "dialogs": {}, "transactions": {},
         "call_cseq": {}, "stats": good_stats},
        {"version": 1, "bindings": {}, "dialogs": "nope", "transactions": {},
         "call_cseq": {}, "stats": good_stats},
        {"version": 1, "bindings": {}, "dialogs": {}, "transactions": {},
         "call_cseq": {}, "stats": {"accepted": 0}},
        {"version": 1, "bindings": {}, "dialogs": {}, "transactions": [],
         "call_cseq": {}, "stats": good_stats},
    ]
    for bad in bad_states:
        with pytest.raises(sip.SipError) as exc:
            restored.restore(bad)
        assert exc.value.code == "SIP_SNAPSHOT_INVALID"
    assert restored.stats()["accepted"] == 1


def test_concurrent_retransmissions_produce_one_transaction():
    guard = sip.SipTransactionGuard()
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            value = guard.receive(message(call_id="call-race"), now=NOW)["status"]
        except Exception as exc:
            value = getattr(exc, "code", repr(exc))
        with lock:
            outcomes.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("accepted") == 1
    assert outcomes.count("retransmission") == 7
    assert guard.stats()["accepted"] == 1
    assert guard.stats()["retransmissions"] == 7
    assert guard.stats()["dialogs_opened"] == 1
