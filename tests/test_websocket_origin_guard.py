import threading

import pytest

import websocket_origin_guard as og

T0 = "2026-11-23T10:00:00+00:00"
APP = "https://app.example.com"
CORP = "https://corp.example.org"
KEY = "dGhlIHNhbXBsZSBub25jZQ=="


def make(**kwargs):
    guard = og.OriginGuard(**kwargs)
    guard.register_origin(APP)
    return guard


def hs(origin=None, *, ticket=None, upgrade="websocket", connection="Upgrade",
       version="13", key=KEY):
    headers = {
        "upgrade": upgrade,
        "connection": connection,
        "sec-websocket-version": version,
        "sec-websocket-key": key,
    }
    if origin is not None:
        headers["origin"] = origin
    if ticket is not None:
        headers["x-ws-ticket"] = ticket
    return headers


def hdr(base, extra):
    out = dict(base)
    out.update(extra)
    return out


def test_origin_registration_is_idempotent_and_rejects_conflicts():
    guard = og.OriginGuard()
    first = guard.register_origin("HTTPS://App.Example.COM:443")
    assert first == {
        "origin": APP,
        "allow_subdomains": False,
        "max_per_origin": 50,
    }
    again = guard.register_origin(APP, allow_subdomains=False, max_per_origin=50)
    assert again == first

    with pytest.raises(og.OriginError) as exc:
        guard.register_origin(APP, allow_subdomains=True)
    assert exc.value.code == "ORIGIN_CONFLICT"

    with pytest.raises(og.OriginError) as exc:
        guard.register_origin(APP, max_per_origin=10)
    assert exc.value.code == "ORIGIN_CONFLICT"

    with pytest.raises(og.OriginError) as exc:
        guard.register_origin("app.example.com")
    assert exc.value.code == "ORIGIN_INVALID"


def test_origin_normalization_canonicalizes_and_rejects_malformed():
    guard = og.OriginGuard()
    assert guard.normalize_origin("HTTPS://App.Example.COM:443") == APP
    assert guard.normalize_origin("http://app.example.com:80") == "http://app.example.com"
    assert guard.normalize_origin("https://app.example.com:8443") == "https://app.example.com:8443"
    assert guard.normalize_origin(" https://app.example.com/ ") == APP

    for raw in (
        "app.example.com",
        "https://app.example.com/x",
        "https://app.example.com?y=1",
        "https://user@app.example.com",
        "wss://app.example.com",
        "https://",
        "https://app.example.com:abc",
        "",
    ):
        with pytest.raises(og.OriginError) as exc:
            guard.normalize_origin(raw)
        assert exc.value.code == "ORIGIN_INVALID"

    for raw in ("null", "NULL"):
        with pytest.raises(og.OriginError) as exc:
            guard.normalize_origin(raw)
        assert exc.value.code == "ORIGIN_NULL"


def test_revoked_origin_is_closed_and_cannot_be_re_registered():
    guard = make()
    ticket = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]
    accepted = guard.handshake(
        hs(APP, ticket=ticket), peer="203.0.113.7", now=T0)
    assert accepted["status"] == "accepted"
    assert guard.origin_status(APP)["active"] == 1

    guard.revoke_origin("HTTPS://APP.EXAMPLE.COM:443")
    status = guard.origin_status(APP)
    assert status["revoked"] is True
    assert status["active"] == 0
    assert guard.stats()["closed"] == 1

    for operation in (
        lambda: guard.register_origin(APP),
        lambda: guard.issue_ticket(APP, subject="u2", now=T0),
        lambda: guard.revoke_origin(APP),
    ):
        with pytest.raises(og.OriginError) as exc:
            operation()
        assert exc.value.code == "ORIGIN_REVOKED"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(APP, ticket=ticket), peer="203.0.113.8", now=T0)
    assert exc.value.code == "WS_ORIGIN_REVOKED"

    with pytest.raises(og.OriginError) as exc:
        guard.revoke_origin("https://nobody.example.net")
    assert exc.value.code == "ORIGIN_UNKNOWN"


def test_ticket_issuance_validates_origin_and_subject():
    guard = og.OriginGuard()
    guard.register_origin(APP)

    with pytest.raises(og.OriginError) as exc:
        guard.issue_ticket("https://other.example.net", subject="u1", now=T0)
    assert exc.value.code == "ORIGIN_UNKNOWN"

    with pytest.raises(og.OriginError) as exc:
        guard.issue_ticket(APP, subject="", now=T0)
    assert exc.value.code == "ORIGIN_INVALID"

    with pytest.raises(og.OriginError) as exc:
        guard.issue_ticket(APP, subject="u1", ttl=0, now=T0)
    assert exc.value.code == "ORIGIN_INVALID"

    first = guard.issue_ticket(APP, subject="u1", ttl=60, now=T0)
    second = guard.issue_ticket(APP, subject="u1", now=T0)
    assert first["origin"] == APP
    assert first["subject"] == "u1"
    assert first["expires_at"] == "2026-11-23T10:01:00+00:00"
    assert first["ticket"] != second["ticket"]
    assert second["expires_at"] == "2026-11-23T10:01:00+00:00"
    assert guard.ticket_state(first["ticket"])["state"] == "issued"
    assert guard.stats()["issued"] == 2


def test_handshake_requires_websocket_upgrade_version_and_key():
    guard = make()
    ticket = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]
    base = hs(APP, ticket=ticket)

    cases = [
        (hdr(base, {"upgrade": "h2c"}), "WS_BAD_UPGRADE"),
        (hdr(base, {"connection": "keep-alive"}), "WS_BAD_UPGRADE"),
        (hdr(base, {"sec-websocket-version": "8"}), "WS_BAD_VERSION"),
        (hdr(base, {"sec-websocket-key": "abc"}), "WS_BAD_KEY"),
        (hdr(base, {"sec-websocket-key": "c2hvcnQ="}), "WS_BAD_KEY"),
    ]
    for headers, code in cases:
        with pytest.raises(og.OriginError) as exc:
            guard.handshake(headers, peer="203.0.113.7", now=T0)
        assert exc.value.code == code
    assert guard.ticket_state(ticket)["state"] == "issued"

    ok = guard.handshake(base, peer="203.0.113.7", now=T0)
    assert ok == {
        "status": "accepted",
        "ticket": ticket,
        "origin": APP,
        "subject": "u1",
        "peer": "203.0.113.7",
    }


def test_handshake_rejects_missing_null_and_malformed_origin():
    guard = make()
    ticket = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(ticket=ticket), peer="203.0.113.7", now=T0)
    assert exc.value.code == "WS_ORIGIN_MISSING"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs("null", ticket=ticket), peer="203.0.113.7", now=T0)
    assert exc.value.code == "WS_ORIGIN_NULL"

    for origin in (
        "https://user@app.example.com",
        "app.example.com",
        "https://app.example.com/path",
    ):
        with pytest.raises(og.OriginError) as exc:
            guard.handshake(hs(origin, ticket=ticket), peer="203.0.113.7", now=T0)
        assert exc.value.code == "WS_ORIGIN_INVALID"
    assert guard.ticket_state(ticket)["state"] == "issued"


def test_origin_matching_enforces_scheme_port_and_subdomain_boundaries():
    guard = make()
    guard.register_origin(CORP, allow_subdomains=True, max_per_origin=2)

    sub_ticket = guard.issue_ticket(
        "https://a.corp.example.org", subject="u1", now=T0)["ticket"]
    accepted = guard.handshake(
        hs("https://a.corp.example.org", ticket=sub_ticket),
        peer="203.0.113.7", now=T0)
    assert accepted["status"] == "accepted"
    assert accepted["origin"] == "https://a.corp.example.org"

    for origin in (
        "http://app.example.com",
        "https://app.example.com:8443",
        "https://app.example.com.attacker.net",
        "https://evil-app.example.com",
        "http://a.corp.example.org",
        "https://corp.example.org.attacker.net",
        "https://notcorp.example.org",
    ):
        with pytest.raises(og.OriginError) as exc:
            guard.handshake(hs(origin), peer="203.0.113.8", now=T0)
        assert exc.value.code == "WS_ORIGIN_DENIED"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs("https://a.b.corp.example.org"), peer="203.0.113.9", now=T0)
    assert exc.value.code == "WS_TICKET_MISSING"


def test_ticket_is_single_use_and_origin_bound():
    guard = make()
    guard.register_origin(CORP, allow_subdomains=True)
    ticket = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(CORP, ticket=ticket), peer="203.0.113.7", now=T0)
    assert exc.value.code == "WS_TICKET_ORIGIN_MISMATCH"
    assert guard.ticket_state(ticket)["state"] == "issued"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(APP), peer="203.0.113.7", now=T0)
    assert exc.value.code == "WS_TICKET_MISSING"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(APP, ticket="wst-nope"), peer="203.0.113.7", now=T0)
    assert exc.value.code == "WS_TICKET_UNKNOWN"

    assert guard.handshake(
        hs(APP, ticket=ticket), peer="203.0.113.7", now=T0)["status"] == "accepted"
    assert guard.ticket_state(ticket)["state"] == "used"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(APP, ticket=ticket), peer="203.0.113.9", now=T0)
    assert exc.value.code == "WS_TICKET_USED"
    assert guard.origin_status(APP)["active"] == 1


def test_expired_ticket_is_refused_and_expire_counts_once():
    guard = make()
    fresh = guard.issue_ticket(APP, subject="u1", ttl=60, now=T0)["ticket"]
    stale = guard.issue_ticket(APP, subject="u2", ttl=60, now=T0)["ticket"]

    early = guard.handshake(hs(APP, ticket=fresh), peer="203.0.113.7",
                            now="2026-11-23T10:00:59+00:00")
    assert early["status"] == "accepted"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(APP, ticket=stale), peer="203.0.113.8",
                        now="2026-11-23T10:01:00+00:00")
    assert exc.value.code == "WS_TICKET_EXPIRED"
    assert guard.ticket_state(stale)["state"] == "expired"

    other = guard.issue_ticket(APP, subject="u3", ttl=30, now=T0)["ticket"]
    assert guard.expire(now="2026-11-23T10:00:20+00:00") == 0
    assert guard.expire(now="2026-11-23T10:00:30+00:00") == 1
    assert guard.expire(now="2026-11-23T10:00:30+00:00") == 0
    assert guard.ticket_state(other)["state"] == "expired"
    assert guard.stats()["expired"] == 2


def test_per_origin_capacity_holds_unused_ticket_until_slot_frees():
    guard = make(max_per_origin=2)
    tickets = [guard.issue_ticket(APP, subject=f"u{i}", now=T0)["ticket"]
               for i in range(3)]

    first = guard.handshake(hs(APP, ticket=tickets[0]), peer="p1", now=T0)
    second = guard.handshake(hs(APP, ticket=tickets[1]), peer="p2", now=T0)
    assert first["status"] == "accepted"
    assert second["status"] == "accepted"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(APP, ticket=tickets[2]), peer="p3", now=T0)
    assert exc.value.code == "WS_ORIGIN_FULL"
    assert guard.ticket_state(tickets[2])["state"] == "issued"
    assert guard.origin_status(APP)["active"] == 2

    closed = guard.close(tickets[0])
    assert closed == {"ticket": tickets[0], "state": "closed", "freed": True}
    assert guard.origin_status(APP)["active"] == 1

    assert guard.handshake(
        hs(APP, ticket=tickets[2]), peer="p3", now=T0)["status"] == "accepted"
    assert guard.origin_status(APP)["active"] == 2


def test_global_capacity_limit_does_not_consume_tickets():
    guard = og.OriginGuard(max_connections=1)
    guard.register_origin(APP)
    guard.register_origin(CORP)
    app_ticket = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]
    corp_ticket = guard.issue_ticket(CORP, subject="u2", now=T0)["ticket"]

    assert guard.handshake(
        hs(APP, ticket=app_ticket), peer="p1", now=T0)["status"] == "accepted"

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(CORP, ticket=corp_ticket), peer="p2", now=T0)
    assert exc.value.code == "WS_CAPACITY"
    assert guard.ticket_state(corp_ticket)["state"] == "issued"

    guard.close(app_ticket)
    assert guard.handshake(
        hs(CORP, ticket=corp_ticket), peer="p2", now=T0)["status"] == "accepted"


def test_close_cancels_unused_and_frees_active_session_once():
    guard = make()
    unused = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]
    cancelled = guard.close(unused)
    assert cancelled == {"ticket": unused, "state": "cancelled", "freed": False}

    with pytest.raises(og.OriginError) as exc:
        guard.handshake(hs(APP, ticket=unused), peer="203.0.113.7", now=T0)
    assert exc.value.code == "WS_TICKET_USED"

    active = guard.issue_ticket(APP, subject="u2", now=T0)["ticket"]
    guard.handshake(hs(APP, ticket=active), peer="203.0.113.7", now=T0)
    assert guard.close(active)["freed"] is True
    assert guard.origin_status(APP)["active"] == 0

    with pytest.raises(og.OriginError) as exc:
        guard.close(active)
    assert exc.value.code == "WS_TICKET_CLOSED"

    with pytest.raises(og.OriginError) as exc:
        guard.close("wst-nope")
    assert exc.value.code == "WS_TICKET_UNKNOWN"
    assert guard.stats()["closed"] == 1


def test_snapshot_restore_preserves_policies_sessions_and_ticket_state():
    guard = og.OriginGuard()
    guard.register_origin(APP, max_per_origin=2)
    guard.register_origin(CORP, allow_subdomains=True, max_per_origin=1)
    t1 = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]
    t2 = guard.issue_ticket(APP, subject="u2", now=T0)["ticket"]
    pending = guard.issue_ticket(APP, subject="u3", now=T0)["ticket"]
    guard.handshake(hs(APP, ticket=t1), peer="p1", now=T0)

    state = guard.snapshot()
    assert state["version"] == 1
    assert state["stats"] == {"issued": 3, "accepted": 1, "rejected": 0,
                              "closed": 0, "expired": 0}

    guard.handshake(hs(APP, ticket=t2), peer="p2", now=T0)
    guard.close(t1)

    clone = og.OriginGuard()
    clone.restore(state)
    assert clone.origin_status(APP)["max_per_origin"] == 2
    assert clone.origin_status(APP)["active"] == 1
    assert clone.origin_status(CORP)["allow_subdomains"] is True
    assert clone.ticket_state(t1)["state"] == "used"
    assert clone.ticket_state(t2)["state"] == "issued"
    assert clone.ticket_state(pending)["state"] == "issued"
    assert clone.stats() == {"issued": 3, "accepted": 1, "rejected": 0,
                             "closed": 0, "expired": 0}

    assert clone.handshake(
        hs(APP, ticket=t2), peer="p2", now=T0)["status"] == "accepted"
    with pytest.raises(og.OriginError) as exc:
        clone.handshake(hs(APP, ticket=pending), peer="p3", now=T0)
    assert exc.value.code == "WS_ORIGIN_FULL"
    assert state["stats"]["accepted"] == 1


def test_corrupt_snapshot_is_rejected_without_touching_state():
    guard = make()
    ticket = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]
    guard.handshake(hs(APP, ticket=ticket), peer="p1", now=T0)

    bad_states = [
        None,
        {},
        {"version": 2, "policies": {}, "revoked": [], "tickets": {},
         "sessions": {}, "stats": {}},
        {"version": 1, "policies": [], "revoked": [], "tickets": {},
         "sessions": {}, "stats": {}},
        {"version": 1, "policies": {}, "revoked": APP, "tickets": {},
         "sessions": {}, "stats": {}},
        {"version": 1, "policies": {}, "revoked": [], "tickets": [],
         "sessions": {}, "stats": {}},
        {"version": 1, "policies": {}, "revoked": [], "tickets": {},
         "sessions": {}, "stats": {"issued": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(og.OriginError) as exc:
            guard.restore(bad)
        assert exc.value.code == "WS_SNAPSHOT_INVALID"

    assert guard.origin_status(APP)["active"] == 1
    assert guard.ticket_state(ticket)["state"] == "used"
    assert guard.stats() == {"issued": 1, "accepted": 1, "rejected": 0,
                             "closed": 0, "expired": 0}


def test_concurrent_handshakes_consume_a_ticket_exactly_once():
    guard = make()
    ticket = guard.issue_ticket(APP, subject="u1", now=T0)["ticket"]

    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            out = guard.handshake(hs(APP, ticket=ticket), peer="203.0.113.7", now=T0)
            row = {"status": out["status"]}
        except og.OriginError as exc:
            row = {"status": "rejected", "code": exc.code}
        with lock:
            results.append(row)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(row["status"] for row in results)
    assert statuses == ["accepted"] + ["rejected"] * 7
    codes = sorted(row["code"] for row in results if row["status"] == "rejected")
    assert codes == ["WS_TICKET_USED"] * 7
    assert guard.origin_status(APP)["active"] == 1
    assert guard.stats() == {"issued": 1, "accepted": 1, "rejected": 7,
                             "closed": 0, "expired": 0}
