import threading

import pytest

import crl_delta_merger as crl

NOW = "2026-11-24T10:00:00+00:00"
THIS = "2026-11-24T09:00:00+00:00"
NEXT = "2026-11-24T11:00:00+00:00"


def entry(serial, reason="key_compromise", revoked_at="2026-11-20T00:00:00+00:00"):
    return {"serial": serial, "reason": reason, "revoked_at": revoked_at}


def make(**kwargs):
    merger = crl.CrlDeltaMerger(**kwargs)
    merger.register_issuer("ca-a", key_id="key-a")
    return merger


def submit_base(merger, number=1, entries=None, this_update=THIS, next_update=NEXT):
    if entries is None:
        entries = [entry("AA01")]
    return merger.submit_base(
        "ca-a",
        number=number,
        this_update=this_update,
        next_update=next_update,
        entries=entries,
        signature_key="key-a",
    )


def submit_delta(merger, number=2, base_number=1, entries=None,
                 this_update=THIS, next_update=NEXT):
    if entries is None:
        entries = [entry("BB02")]
    return merger.submit_delta(
        "ca-a",
        base_number=base_number,
        number=number,
        this_update=this_update,
        next_update=next_update,
        entries=entries,
        signature_key="key-a",
    )


def test_issuer_registration_is_idempotent_and_detects_conflicts():
    merger = crl.CrlDeltaMerger()
    first = merger.register_issuer("ca-a", key_id="key-a", indirect=False)
    assert first == {"issuer": "ca-a", "key_id": "key-a", "indirect": False}
    again = merger.register_issuer("ca-a", key_id="key-a", indirect=False)
    assert again["key_id"] == "key-a"
    assert merger.list_issuers() == ["ca-a"]

    with pytest.raises(crl.CrlError) as exc:
        merger.register_issuer("ca-a", key_id="key-b")
    assert exc.value.code == "CRL_CONFLICT"

    with pytest.raises(crl.CrlError) as exc:
        merger.submit_base(
            "ca-missing",
            number=1,
            this_update=THIS,
            next_update=NEXT,
            entries=[entry("AA01")],
            signature_key="key-a",
        )
    assert exc.value.code == "CRL_UNKNOWN_ISSUER"


def test_base_crl_answers_revocation_queries_with_source():
    merger = make()
    result = submit_base(merger, entries=[entry("AA01"), entry("BB02", "superseded")])
    assert result["number"] == 1
    assert result["revoked"] == 2

    hit = merger.is_revoked("ca-a", "AA01", now=NOW)
    assert hit["revoked"] is True
    assert hit["reason"] == "key_compromise"
    assert hit["revoked_at"] == "2026-11-20T00:00:00+00:00"
    assert hit["source"] == "base"

    miss = merger.is_revoked("ca-a", "CC03", now=NOW)
    assert miss == {"revoked": False, "reason": None, "revoked_at": None, "source": None}

    view = merger.effective_crl("ca-a", now=NOW)
    assert view["number"] == 1
    assert view["delta_number"] is None
    assert view["count"] == 2
    assert view["revoked"]["BB02"]["reason"] == "superseded"


def test_duplicate_serial_within_one_crl_uses_last_entry():
    merger = make()
    submit_base(
        merger,
        entries=[
            entry("AA01", "superseded", "2026-11-01T00:00:00+00:00"),
            entry("AA01", "key_compromise", "2026-11-02T00:00:00+00:00"),
        ],
    )
    revoked = merger.is_revoked("ca-a", "AA01", now=NOW)
    assert revoked["reason"] == "key_compromise"
    assert revoked["revoked_at"] == "2026-11-02T00:00:00+00:00"
    assert merger.effective_crl("ca-a", now=NOW)["count"] == 1


def test_stale_base_number_is_rejected_without_state_change():
    merger = make()
    submit_base(merger, number=3, entries=[entry("AA03")])

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, number=3, entries=[entry("AA09")])
    assert exc.value.code == "CRL_STALE_NUMBER"

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, number=2, entries=[entry("AA08")])
    assert exc.value.code == "CRL_STALE_NUMBER"

    view = merger.effective_crl("ca-a", now=NOW)
    assert view["number"] == 3
    assert view["count"] == 1
    assert merger.is_revoked("ca-a", "AA09", now=NOW)["revoked"] is False

    assert submit_base(merger, number=4, entries=[entry("AA04")])["number"] == 4


def test_base_submission_validates_signature_window_and_entries():
    merger = make()
    with pytest.raises(crl.CrlError) as exc:
        merger.submit_base(
            "ca-a",
            number=1,
            this_update=THIS,
            next_update=NEXT,
            entries=[entry("AA01")],
            signature_key="key-rogue",
        )
    assert exc.value.code == "CRL_BAD_SIGNATURE"

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, this_update=NEXT, next_update=THIS)
    assert exc.value.code == "CRL_BAD_WINDOW"

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, number=0)
    assert exc.value.code == "CRL_BAD_CRL"

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, entries=[{"serial": "", "reason": "key_compromise",
                                      "revoked_at": "2026-11-20T00:00:00+00:00"}])
    assert exc.value.code == "CRL_BAD_ENTRY"

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, entries=[{"serial": "AA01", "reason": "because_i_said_so",
                                      "revoked_at": "2026-11-20T00:00:00+00:00"}])
    assert exc.value.code == "CRL_BAD_ENTRY"

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, entries=[{"serial": "AA01", "reason": "superseded",
                                      "revoked_at": "yesterday"}])
    assert exc.value.code == "CRL_BAD_ENTRY"

    assert merger.status("ca-a", now=NOW)["has_base"] is False
    assert merger.stats()["rejected"] == 6


def test_delta_extends_base_and_remove_from_crl_clears_entry():
    merger = make()
    submit_base(merger, entries=[entry("AA01")])
    result = submit_delta(merger, number=2, entries=[
        entry("BB02", "superseded", "2026-11-22T00:00:00+00:00"),
    ])
    assert result["base_number"] == 1
    assert result["number"] == 2

    delta_hit = merger.is_revoked("ca-a", "BB02", now=NOW)
    assert delta_hit["revoked"] is True
    assert delta_hit["source"] == "delta"
    assert merger.effective_crl("ca-a", now=NOW)["delta_number"] == 2

    submit_delta(
        merger,
        number=3,
        entries=[{"serial": "AA01", "reason": "remove_from_crl"}],
    )
    assert merger.is_revoked("ca-a", "AA01", now=NOW)["revoked"] is False
    assert merger.effective_crl("ca-a", now=NOW)["count"] == 1


def test_delta_requires_current_base_and_increasing_numbers():
    merger = make()
    with pytest.raises(crl.CrlError) as exc:
        submit_delta(merger)
    assert exc.value.code == "CRL_NO_BASE"

    submit_base(merger, number=1)
    with pytest.raises(crl.CrlError) as exc:
        submit_delta(merger, number=2, base_number=99)
    assert exc.value.code == "CRL_BASE_MISMATCH"

    submit_delta(merger, number=2)
    with pytest.raises(crl.CrlError) as exc:
        submit_delta(merger, number=2)
    assert exc.value.code == "CRL_STALE_NUMBER"

    with pytest.raises(crl.CrlError) as exc:
        submit_delta(merger, number=1)
    assert exc.value.code == "CRL_STALE_NUMBER"

    with pytest.raises(crl.CrlError) as exc:
        submit_delta(merger, number=3, this_update=NEXT, next_update=NEXT)
    assert exc.value.code == "CRL_BAD_WINDOW"


def test_replacement_base_drops_previous_deltas():
    merger = make()
    submit_base(merger, number=1, entries=[entry("AA01")])
    submit_delta(merger, number=2, entries=[entry("BB02")])
    submit_base(merger, number=2, entries=[entry("CC03")])

    view = merger.effective_crl("ca-a", now=NOW)
    assert view["number"] == 2
    assert view["delta_number"] is None
    assert view["count"] == 1
    assert merger.is_revoked("ca-a", "AA01", now=NOW)["revoked"] is False
    assert merger.is_revoked("ca-a", "BB02", now=NOW)["revoked"] is False
    assert merger.is_revoked("ca-a", "CC03", now=NOW)["revoked"] is True

    with pytest.raises(crl.CrlError) as exc:
        submit_delta(merger, number=3, base_number=1)
    assert exc.value.code == "CRL_BASE_MISMATCH"

    assert submit_delta(merger, number=3, base_number=2)["base_number"] == 2


def test_delta_retention_limit_is_enforced():
    merger = make(max_deltas=2)
    submit_base(merger, number=1)
    submit_delta(merger, number=2)
    submit_delta(merger, number=3)

    with pytest.raises(crl.CrlError) as exc:
        submit_delta(merger, number=4)
    assert exc.value.code == "CRL_DELTA_LIMIT"
    assert merger.effective_crl("ca-a", now=NOW)["delta_number"] == 3
    assert merger.stats()["deltas"] == 2


def test_expiry_boundary_and_expire_sweep():
    merger = make(clock_skew=300)
    submit_base(merger, number=1, next_update="2026-11-24T11:00:00+00:00")

    fresh = merger.is_revoked("ca-a", "AA01", now="2026-11-24T11:04:59+00:00")
    assert fresh["revoked"] is True

    with pytest.raises(crl.CrlError) as exc:
        merger.is_revoked("ca-a", "AA01", now="2026-11-24T11:05:00+00:00")
    assert exc.value.code == "CRL_EXPIRED"

    assert merger.expire(now="2026-11-24T11:04:59+00:00") == 0
    assert merger.expire(now="2026-11-24T11:05:00+00:00") == 1
    assert merger.status("ca-a", now=NOW)["has_base"] is False
    with pytest.raises(crl.CrlError) as exc:
        merger.is_revoked("ca-a", "AA01", now=NOW)
    assert exc.value.code == "CRL_NO_BASE"


def test_capacity_limit_rejects_crl_atomically():
    merger = make(max_serials=3)
    submit_base(merger, number=1, entries=[entry("S1"), entry("S2"), entry("S3")])

    with pytest.raises(crl.CrlError) as exc:
        submit_base(merger, number=2, entries=[entry("S4"), entry("S5"), entry("S6"), entry("S7")])
    assert exc.value.code == "CRL_CAPACITY"

    view = merger.effective_crl("ca-a", now=NOW)
    assert view["number"] == 1
    assert view["count"] == 3
    assert merger.stats()["bases"] == 1


def test_stats_and_reset_track_submissions_and_queries():
    merger = make()
    submit_base(merger, number=1)
    submit_delta(merger, number=2)
    merger.is_revoked("ca-a", "AA01", now=NOW)
    merger.is_revoked("ca-a", "ZZ99", now=NOW)
    merger.is_revoked("ca-a", "BB02", now=NOW)
    assert merger.stats() == {"bases": 1, "deltas": 1, "rejected": 0, "queries": 3, "hits": 2}

    with pytest.raises(crl.CrlError):
        submit_base(merger, number=1)
    assert merger.stats()["rejected"] == 1

    merger.reset_stats()
    assert merger.stats() == {"bases": 0, "deltas": 0, "rejected": 0, "queries": 0, "hits": 0}


def test_snapshot_is_detached_and_restore_round_trips():
    merger = make()
    submit_base(merger, number=1, entries=[entry("AA01")])
    submit_delta(merger, number=2, entries=[entry("BB02")])
    stats_before = merger.stats()
    state = merger.snapshot()
    assert state["version"] == 1

    state["stats"]["queries"] = 999
    state["issuers"]["ca-injected"] = {"key_id": "key-x", "indirect": False}
    assert merger.stats() == stats_before
    assert merger.list_issuers() == ["ca-a"]

    clean = merger.snapshot()
    submit_base(merger, number=2, entries=[entry("CC03")])

    restored = crl.CrlDeltaMerger()
    restored.restore(clean)
    assert restored.list_issuers() == ["ca-a"]
    assert restored.stats()["queries"] == 0
    assert restored.is_revoked("ca-a", "AA01", now=NOW)["revoked"] is True
    assert restored.is_revoked("ca-a", "BB02", now=NOW)["source"] == "delta"
    assert restored.is_revoked("ca-a", "CC03", now=NOW)["revoked"] is False
    assert restored.effective_crl("ca-a", now=NOW)["delta_number"] == 2
    assert restored.stats()["queries"] == 3


def test_corrupt_snapshot_is_rejected_and_state_survives():
    merger = make()
    submit_base(merger, number=1)
    before = merger.stats()
    bad_states = [
        None,
        {},
        {"version": 2, "issuers": {}, "bases": {}, "deltas": {}, "stats": {}},
        {"version": 1, "issuers": "nope", "bases": {}, "deltas": {}, "stats": {}},
        {"version": 1, "issuers": {}, "bases": [], "deltas": {}, "stats": {}},
        {"version": 1, "issuers": {}, "bases": {}, "deltas": {}, "stats": "nope"},
    ]
    for bad in bad_states:
        with pytest.raises(crl.CrlError) as exc:
            merger.restore(bad)
        assert exc.value.code == "CRL_SNAPSHOT_INVALID"

    assert merger.stats() == before
    assert merger.is_revoked("ca-a", "AA01", now=NOW)["revoked"] is True


def test_concurrent_base_submission_accepts_exactly_one():
    merger = make()
    submit_base(merger, number=1)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            submit_base(merger, number=2, entries=[entry("RACE")])
            outcome = "accepted"
        except crl.CrlError as exc:
            outcome = exc.code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["CRL_STALE_NUMBER"] * 7 + ["accepted"]
    assert merger.stats()["bases"] == 2
    assert merger.stats()["rejected"] == 7
    assert merger.effective_crl("ca-a", now=NOW)["number"] == 2
