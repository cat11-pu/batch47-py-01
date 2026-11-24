import copy
import threading

import pytest

import cas_ticket_guard as cg

NOW = "2026-11-24T11:00:00+00:00"
PORTAL = "https://portal.example.edu/app"
WIKI = "https://wiki.example.edu/view"
MAIL = "https://mail.example.edu/inbox"
LMS = "https://lms.example.edu/course"


def make(**kwargs):
    guard = cg.CasTicketGuard(**kwargs)
    guard.register_service("portal", url_pattern="https://portal.example.edu/*")
    guard.register_service("wiki", url_pattern="https://wiki.example.edu/*",
                           allow_proxy=True)
    guard.register_service("mail", url_pattern="https://mail.example.edu/*",
                           allow_proxy=True)
    guard.register_service("lms", url_pattern="https://lms.example.edu/*",
                           allow_proxy=True)
    return guard


def tgt_for(guard, principal="alice", **overrides):
    args = {"principal": principal, "now": NOW}
    args.update(overrides)
    return guard.issue_tgt(**args)


def test_tgt_and_service_ticket_happy_path():
    guard = make()
    tgt = tgt_for(guard, attributes={"dept": "cs"})
    assert tgt["tgt"].startswith("TGT-")
    assert tgt["principal"] == "alice"
    assert tgt["renewable"] is True
    assert tgt["expires_at"] == "2026-11-24T13:00:00+00:00"

    ticket = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    assert ticket["ticket"].startswith("ST-")
    assert ticket["service"] == PORTAL
    assert ticket["renew"] is False
    assert ticket["expires_at"] == "2026-11-24T11:02:00+00:00"

    out = guard.validate_service_ticket(ticket["ticket"], service=PORTAL, now=NOW)
    assert out["status"] == "ok"
    assert out["principal"] == "alice"
    assert out["attributes"] == {"dept": "cs"}
    assert out["proxies"] == []
    assert guard.stats() == {"tgt_issued": 1, "st_issued": 1, "validated": 1,
                             "rejected": 0, "proxy_issued": 0}


def test_service_registration_patterns_and_conflicts():
    guard = make()
    same = guard.register_service("portal", url_pattern="https://portal.example.edu/*")
    assert same["service_id"] == "portal"
    assert same["allow_proxy"] is False
    with pytest.raises(cg.CasError) as exc:
        guard.register_service("portal", url_pattern="https://other.example.edu/*")
    assert exc.value.code == "CAS_SERVICE_CONFLICT"

    guard.register_service("intra", url_pattern="https://intra.example.edu/sso")
    tgt = tgt_for(guard, principal="bob")
    exact = guard.issue_service_ticket(tgt["tgt"], service="https://intra.example.edu/sso",
                                       now=NOW)
    assert exact["service"] == "https://intra.example.edu/sso"
    with pytest.raises(cg.CasError) as exc:
        guard.issue_service_ticket(tgt["tgt"], service="https://intra.example.edu/sso/",
                                   now=NOW)
    assert exc.value.code == "CAS_UNKNOWN_SERVICE"
    with pytest.raises(cg.CasError) as exc:
        guard.issue_service_ticket(tgt["tgt"], service="https://evil.example.edu/app",
                                   now=NOW)
    assert exc.value.code == "CAS_UNKNOWN_SERVICE"
    with pytest.raises(cg.CasError) as exc:
        guard.issue_service_ticket(tgt["tgt"],
                                   service="https://portal.example.edu.evil/app", now=NOW)
    assert exc.value.code == "CAS_UNKNOWN_SERVICE"


def test_service_mismatch_does_not_consume_the_ticket():
    guard = make()
    tgt = tgt_for(guard)
    ticket = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)

    with pytest.raises(cg.CasError) as exc:
        guard.validate_service_ticket(ticket["ticket"], service=WIKI, now=NOW)
    assert exc.value.code == "CAS_SERVICE_MISMATCH"
    assert guard.stats()["validated"] == 0
    assert guard.stats()["rejected"] == 1

    out = guard.validate_service_ticket(ticket["ticket"], service=PORTAL, now=NOW)
    assert out["status"] == "ok"
    with pytest.raises(cg.CasError) as exc:
        guard.validate_service_ticket(ticket["ticket"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TICKET_ALREADY_USED"
    assert guard.stats() == {"tgt_issued": 1, "st_issued": 1, "validated": 1,
                             "rejected": 2, "proxy_issued": 0}


def test_service_ticket_is_single_use():
    guard = make()
    tgt = tgt_for(guard)
    ticket = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    assert guard.validate_service_ticket(ticket["ticket"], service=PORTAL,
                                         now=NOW)["status"] == "ok"
    with pytest.raises(cg.CasError) as exc:
        guard.validate_service_ticket(ticket["ticket"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TICKET_ALREADY_USED"
    assert guard.stats()["validated"] == 1
    assert guard.stats()["rejected"] == 1


def test_unknown_and_expired_tickets_are_rejected():
    guard = make()
    with pytest.raises(cg.CasError) as exc:
        guard.validate_service_ticket("ST-1-deadbeef", service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TICKET_UNKNOWN"

    tgt = tgt_for(guard)
    ticket = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    with pytest.raises(cg.CasError) as exc:
        guard.validate_service_ticket(ticket["ticket"], service=PORTAL,
                                      now="2026-11-24T11:02:00+00:00")
    assert exc.value.code == "CAS_TICKET_EXPIRED"
    with pytest.raises(cg.CasError) as exc:
        guard.validate_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TICKET_UNKNOWN"


def test_tgt_expiry_blocks_new_tickets_but_not_issued_ones():
    guard = make(tgt_ttl=60)
    tgt = tgt_for(guard)
    ticket = guard.issue_service_ticket(tgt["tgt"], service=PORTAL,
                                        now="2026-11-24T11:00:59+00:00")
    with pytest.raises(cg.CasError) as exc:
        guard.issue_service_ticket(tgt["tgt"], service=PORTAL,
                                   now="2026-11-24T11:01:00+00:00")
    assert exc.value.code == "CAS_TGT_EXPIRED"
    assert guard.validate_service_ticket(ticket["ticket"], service=PORTAL,
                                         now="2026-11-24T11:01:01+00:00")["status"] == "ok"


def test_renew_requires_a_renewable_tgt():
    guard = make()
    plain = tgt_for(guard, renewable=False)
    with pytest.raises(cg.CasError) as exc:
        guard.issue_service_ticket(plain["tgt"], service=PORTAL, renew=True, now=NOW)
    assert exc.value.code == "CAS_RENEW_NOT_ALLOWED"

    normal = guard.issue_service_ticket(plain["tgt"], service=PORTAL, now=NOW)
    assert normal["renew"] is False
    renewable = tgt_for(guard, principal="carl")
    renewed = guard.issue_service_ticket(renewable["tgt"], service=PORTAL,
                                         renew=True, now=NOW)
    assert renewed["renew"] is True
    out = guard.validate_service_ticket(renewed["ticket"], service=PORTAL, now=NOW)
    assert out["renew"] is True


def test_destroy_tgt_blocks_new_tickets_and_its_proxy_grants():
    guard = make()
    tgt = tgt_for(guard)
    grant = guard.issue_proxy_granting_ticket(tgt["tgt"], service=WIKI, now=NOW)
    assert guard.destroy_tgt(tgt["tgt"]) is True
    assert guard.destroy_tgt(tgt["tgt"]) is False

    with pytest.raises(cg.CasError) as exc:
        guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TGT_UNKNOWN"
    with pytest.raises(cg.CasError) as exc:
        guard.issue_proxy_ticket(grant["pgt"], target_service=LMS, now=NOW)
    assert exc.value.code == "CAS_PROXY_GRANT_UNKNOWN"


def test_proxy_ticket_flow_records_the_proxy_chain():
    guard = make()
    tgt = tgt_for(guard)
    grant = guard.issue_proxy_granting_ticket(tgt["tgt"], service=WIKI, now=NOW)
    assert grant["pgt"].startswith("PGT-")
    assert grant["pgt_iou"].startswith("PGTIOU-")
    assert grant["service"] == WIKI

    ticket = guard.issue_proxy_ticket(grant["pgt"], target_service=LMS, now=NOW)
    assert ticket["ticket"].startswith("PT-")
    assert ticket["service"] == LMS
    assert ticket["depth"] == 2
    out = guard.validate_proxy_ticket(ticket["ticket"], target_service=LMS, now=NOW)
    assert out["status"] == "ok"
    assert out["principal"] == "alice"
    assert out["proxies"] == [WIKI]
    assert guard.stats() == {"tgt_issued": 1, "st_issued": 0, "validated": 1,
                             "rejected": 0, "proxy_issued": 2}


def test_proxy_requires_flag_and_respects_depth_limit():
    guard = make(max_proxy_depth=2)
    tgt = tgt_for(guard)
    with pytest.raises(cg.CasError) as exc:
        guard.issue_proxy_granting_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_PROXY_NOT_ALLOWED"

    grant = guard.issue_proxy_granting_ticket(tgt["tgt"], service=WIKI, now=NOW)
    hop = guard.issue_proxy_ticket(grant["pgt"], target_service=LMS, now=NOW)
    with pytest.raises(cg.CasError) as exc:
        guard.issue_proxy_ticket(hop["ticket"], target_service=MAIL, now=NOW)
    assert exc.value.code == "CAS_PROXY_CHAIN_TOO_DEEP"

    with pytest.raises(cg.CasError) as exc:
        guard.validate_proxy_ticket(hop["ticket"], target_service=MAIL, now=NOW)
    assert exc.value.code == "CAS_SERVICE_MISMATCH"
    out = guard.validate_proxy_ticket(hop["ticket"], target_service=LMS, now=NOW)
    assert out["proxies"] == [WIKI]


def test_ticket_store_capacity_and_purge():
    guard = make(max_tickets=3, ticket_ttl=30)
    tgt = tgt_for(guard)
    first = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    second = guard.issue_service_ticket(tgt["tgt"], service=WIKI, now=NOW)
    with pytest.raises(cg.CasError) as exc:
        guard.issue_service_ticket(tgt["tgt"], service=LMS, now=NOW)
    assert exc.value.code == "CAS_STORE_FULL"
    assert guard.stats()["st_issued"] == 2

    assert guard.purge(now="2026-11-24T11:00:29+00:00") == 0
    assert guard.purge(now="2026-11-24T11:00:30+00:00") == 2
    third = guard.issue_service_ticket(tgt["tgt"], service=LMS,
                                       now="2026-11-24T11:00:31+00:00")
    assert third["service"] == LMS
    with pytest.raises(cg.CasError) as exc:
        guard.validate_service_ticket(first["ticket"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TICKET_UNKNOWN"
    assert second["service"] == WIKI


def test_attributes_are_returned_as_detached_copies():
    guard = make()
    source = {"groups": ["eng"], "level": 1}
    tgt = tgt_for(guard, principal="erin", attributes=source)
    source["groups"].append("ops")
    assert tgt["attributes"] == {"groups": ["eng"], "level": 1}

    tgt["attributes"]["groups"].clear()
    first = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    second = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    out = guard.validate_service_ticket(first["ticket"], service=PORTAL, now=NOW)
    assert out["attributes"] == {"groups": ["eng"], "level": 1}

    out["attributes"]["groups"].append("root")
    again = guard.validate_service_ticket(second["ticket"], service=PORTAL, now=NOW)
    assert again["attributes"] == {"groups": ["eng"], "level": 1}


def test_snapshot_restore_keeps_services_tickets_and_use_state():
    guard = make()
    good = tgt_for(guard)
    doomed = tgt_for(guard, principal="frank")
    guard.destroy_tgt(doomed["tgt"])
    ticket = guard.issue_service_ticket(good["tgt"], service=PORTAL, now=NOW)

    state = guard.snapshot()
    pristine = copy.deepcopy(state)
    state["tickets"].clear()
    assert guard.stats()["st_issued"] == 1

    assert guard.validate_service_ticket(ticket["ticket"], service=PORTAL,
                                         now=NOW)["status"] == "ok"

    restored = cg.CasTicketGuard()
    restored.restore(pristine)
    with pytest.raises(cg.CasError) as exc:
        restored.issue_service_ticket(doomed["tgt"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TGT_UNKNOWN"

    out = restored.validate_service_ticket(ticket["ticket"], service=PORTAL, now=NOW)
    assert out["status"] == "ok"
    assert restored.stats() == {"tgt_issued": 2, "st_issued": 1, "validated": 1,
                                "rejected": 0, "proxy_issued": 0}
    with pytest.raises(cg.CasError) as exc:
        restored.validate_service_ticket(ticket["ticket"], service=PORTAL, now=NOW)
    assert exc.value.code == "CAS_TICKET_ALREADY_USED"

    fresh = restored.issue_service_ticket(good["tgt"], service=PORTAL, now=NOW)
    assert fresh["service"] == PORTAL


def test_corrupt_snapshot_leaves_live_state_untouched():
    guard = make()
    tgt = tgt_for(guard)
    ticket = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    bad_states = [
        None,
        {},
        {"version": 2, "services": {}, "tgts": {}, "tickets": {}, "stats": {}},
        {"version": 1, "services": [], "tgts": {}, "tickets": {}, "stats": {}},
        {"version": 1, "services": {}, "tgts": {}, "tickets": {},
         "stats": {"tgt_issued": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(cg.CasError) as exc:
            guard.restore(bad)
        assert exc.value.code == "CAS_SNAPSHOT_INVALID"

    assert guard.stats()["st_issued"] == 1
    assert guard.validate_service_ticket(ticket["ticket"], service=PORTAL,
                                         now=NOW)["status"] == "ok"


def test_concurrent_validation_accepts_exactly_once():
    guard = make()
    tgt = tgt_for(guard)
    ticket = guard.issue_service_ticket(tgt["tgt"], service=PORTAL, now=NOW)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            row = guard.validate_service_ticket(ticket["ticket"], service=PORTAL, now=NOW)
        except Exception as exc:  # pragma: no cover - surfaced through assertions
            row = {"status": "error", "code": getattr(exc, "code", repr(exc))}
        with lock:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["error"] * 7 + ["ok"]
    assert [row["code"] for row in results if row["status"] == "error"] == \
        ["CAS_TICKET_ALREADY_USED"] * 7
    assert guard.stats()["validated"] == 1
    assert guard.stats()["rejected"] == 7
