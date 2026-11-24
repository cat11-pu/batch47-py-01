import copy
import threading

import pytest

import coap_observe_guard as cg

NOW = "2026-11-23T10:00:00+00:00"
PATH = "/sensors/temp"
EP1 = "coap://node1"
EP2 = "coap://node2"
TOK1 = "aa"
TOK2 = "bb"
TOK3 = "cc"


def make(**kwargs):
    guard = cg.CoapObserveGuard(**kwargs)
    guard.register_resource(PATH, max_age=30)
    return guard


def observe(guard, endpoint=EP1, token=TOK1, path=PATH, now=NOW):
    return guard.observe(endpoint=endpoint, token=token, path=path, now=now)


def notify(guard, seq, payload_len=5, path=PATH, now=NOW):
    return guard.notify(path=path, seq=seq, payload_len=payload_len, now=now)


def test_register_resource_validates_path_and_max_age():
    guard = cg.CoapObserveGuard()
    created = guard.register_resource(PATH)
    assert created == {"path": PATH, "max_age": 60, "created": True}
    assert guard.register_resource(PATH)["created"] is False
    with pytest.raises(cg.CoapError) as exc:
        guard.register_resource(PATH, max_age=30)
    assert exc.value.code == "COAP_RESOURCE_CONFLICT"
    for bad in ("sensors", "/a//b", "/a b", "/a?x", "/a/", 5):
        with pytest.raises(cg.CoapError) as err:
            guard.register_resource(bad)
        assert err.value.code == "COAP_BAD_PATH"
    for bad_age in (-1, "60", True):
        with pytest.raises(cg.CoapError) as err2:
            guard.register_resource("/other", max_age=bad_age)
        assert err2.value.code == "COAP_BAD_MAX_AGE"


def test_observe_creates_relation_and_is_idempotent():
    guard = make()
    first = observe(guard)
    assert first == {"endpoint": EP1, "token": TOK1, "path": PATH,
                     "existing": False, "seq": 0, "pending": 0,
                     "max_age": 30}
    again = observe(guard)
    assert again["existing"] is True
    assert again["seq"] == 0
    assert guard.stats()["observed"] == 1


def test_observe_rejects_bad_token_endpoint_and_unknown_path():
    guard = make()
    for bad in ("", "a", "zz", "a" * 18, 5):
        with pytest.raises(cg.CoapError) as exc:
            observe(guard, token=bad)
        assert exc.value.code == "COAP_BAD_TOKEN"
    with pytest.raises(cg.CoapError) as exc2:
        observe(guard, path="/missing")
    assert exc2.value.code == "COAP_UNKNOWN_RESOURCE"
    with pytest.raises(cg.CoapError) as exc3:
        observe(guard, endpoint="")
    assert exc3.value.code == "COAP_BAD_ENDPOINT"
    assert guard.stats()["observed"] == 0


def test_relation_quota_per_endpoint_and_global_limit():
    guard = make(max_relations=2)
    observe(guard, token=TOK1)
    observe(guard, token=TOK2)
    with pytest.raises(cg.CoapError) as exc:
        observe(guard, token=TOK3)
    assert exc.value.code == "COAP_RELATION_QUOTA"

    global_guard = make(max_observations=2)
    observe(global_guard, endpoint=EP1, token=TOK1)
    observe(global_guard, endpoint=EP2, token=TOK2)
    with pytest.raises(cg.CoapError) as exc2:
        observe(global_guard, endpoint=EP1, token=TOK3)
    assert exc2.value.code == "COAP_OBSERVE_LIMIT"


def test_notify_delivers_to_sorted_endpoints():
    guard = make()
    observe(guard, endpoint=EP2, token=TOK2)
    observe(guard, endpoint=EP1, token=TOK1)
    result = notify(guard, 1, payload_len=7)
    assert result["path"] == PATH
    assert result["seq"] == 1
    assert result["payload_len"] == 7
    assert result["delivered"] == [EP1, EP2]
    assert result["suppressed"] == []
    described = guard.describe(endpoint=EP1, token=TOK1, path=PATH)
    assert described["seq"] == 1
    assert described["pending"] == [1]
    assert described["max_age"] == 30
    assert guard.stats()["notifications"] == 1


def test_stale_notification_rejected_without_state_change():
    guard = make()
    observe(guard)
    notify(guard, 1)
    with pytest.raises(cg.CoapError) as exc:
        notify(guard, 1)
    assert exc.value.code == "COAP_STALE_NOTIFICATION"
    for bad in (0, -1, "1", True):
        with pytest.raises(cg.CoapError) as err:
            notify(guard, bad)
        assert err.value.code == "COAP_BAD_SEQ"
    with pytest.raises(cg.CoapError) as exc2:
        notify(guard, 2, payload_len=-1)
    assert exc2.value.code == "COAP_BAD_PAYLOAD"
    with pytest.raises(cg.CoapError) as exc3:
        notify(guard, 2, payload_len=999999)
    assert exc3.value.code == "COAP_BODY_TOO_LARGE"

    notify(guard, 2, payload_len=3)
    described = guard.describe(endpoint=EP1, token=TOK1, path=PATH)
    assert described["seq"] == 2
    assert described["pending"] == [1, 2]
    assert guard.stats()["notifications"] == 2
    assert guard.stats()["rejected"] == 7


def test_pending_limit_suppresses_until_ack():
    guard = make(max_pending=2)
    observe(guard)
    notify(guard, 1)
    notify(guard, 2)
    third = notify(guard, 3)
    assert third["delivered"] == []
    assert third["suppressed"] == [EP1]
    assert guard.describe(endpoint=EP1, token=TOK1, path=PATH)["pending"] == [1, 2]
    assert guard.describe(endpoint=EP1, token=TOK1, path=PATH)["seq"] == 2
    assert guard.ack(endpoint=EP1, token=TOK1, path=PATH, seq=1)["pending"] == 1
    fourth = notify(guard, 4)
    assert fourth["delivered"] == [EP1]
    assert guard.describe(endpoint=EP1, token=TOK1, path=PATH)["pending"] == [2, 4]
    assert guard.stats()["suppressed"] == 1


def test_ack_requires_a_pending_sequence():
    guard = make()
    observe(guard)
    notify(guard, 1)
    with pytest.raises(cg.CoapError) as exc:
        guard.ack(endpoint=EP1, token=TOK1, path=PATH, seq=2)
    assert exc.value.code == "COAP_UNKNOWN_NOTIFICATION"
    with pytest.raises(cg.CoapError) as exc2:
        guard.ack(endpoint=EP1, token=TOK1, path=PATH, seq="1")
    assert exc2.value.code == "COAP_BAD_SEQ"
    with pytest.raises(cg.CoapError) as exc3:
        guard.ack(endpoint=EP2, token=TOK2, path=PATH, seq=1)
    assert exc3.value.code == "COAP_NO_RELATION"
    assert guard.ack(endpoint=EP1, token=TOK1, path=PATH, seq=1) == {
        "endpoint": EP1, "token": TOK1, "path": PATH, "acked": 1,
        "pending": 0}
    with pytest.raises(cg.CoapError) as exc4:
        guard.ack(endpoint=EP1, token=TOK1, path=PATH, seq=1)
    assert exc4.value.code == "COAP_UNKNOWN_NOTIFICATION"


def test_cancel_observe_drops_pending_and_relation():
    guard = make()
    observe(guard)
    notify(guard, 1)
    notify(guard, 2)
    cancelled = guard.cancel_observe(endpoint=EP1, token=TOK1, path=PATH)
    assert cancelled == {"endpoint": EP1, "token": TOK1, "path": PATH,
                         "cancelled": True, "pending": 2}
    with pytest.raises(cg.CoapError) as exc:
        guard.describe(endpoint=EP1, token=TOK1, path=PATH)
    assert exc.value.code == "COAP_NO_RELATION"
    with pytest.raises(cg.CoapError) as exc2:
        guard.cancel_observe(endpoint=EP1, token=TOK1, path=PATH)
    assert exc2.value.code == "COAP_NO_RELATION"
    assert notify(guard, 3)["delivered"] == []
    assert guard.stats()["cancelled"] == 1
    assert guard.stats()["notifications"] == 3


def test_incoming_complete_message_duplicate_and_limits():
    guard = make(max_body_bytes=4)
    first = guard.incoming(endpoint=EP1, message_id=1, token=TOK1,
                           payload=b"hey", now=NOW)
    assert first == {"status": "complete", "endpoint": EP1, "message_id": 1,
                     "received": 1, "body_len": 3, "body": b"hey"}
    duplicate = guard.incoming(endpoint=EP1, message_id=1, token=TOK1,
                               payload=b"hey", now=NOW)
    assert duplicate == {"status": "duplicate", "endpoint": EP1,
                         "message_id": 1, "original_status": "complete",
                         "received": 0}
    assert guard.stats()["duplicates"] == 1
    with pytest.raises(cg.CoapError) as exc:
        guard.incoming(endpoint=EP1, message_id=2, token=TOK1,
                       payload=b"12345", now=NOW)
    assert exc.value.code == "COAP_BODY_TOO_LARGE"
    with pytest.raises(cg.CoapError) as exc2:
        guard.incoming(endpoint=EP1, message_id=0, token=TOK1,
                       payload=b"ok", now=NOW)
    assert exc2.value.code == "COAP_BAD_MESSAGE_ID"
    with pytest.raises(cg.CoapError) as exc3:
        guard.incoming(endpoint=EP1, message_id=3, token=TOK1,
                       payload="text", now=NOW)
    assert exc3.value.code == "COAP_BAD_PAYLOAD"
    with pytest.raises(cg.CoapError) as exc4:
        guard.incoming(endpoint=EP1, message_id=4, token="zz",
                       payload=b"ok", now=NOW)
    assert exc4.value.code == "COAP_BAD_TOKEN"


def test_blockwise_reassembly_and_size_change():
    guard = make()
    first = guard.incoming(
        endpoint=EP1, message_id=10, token=TOK1, payload=b"a" * 16,
        block={"num": 0, "more": True, "size": 16}, now=NOW)
    assert first["status"] == "incomplete"
    assert first["received"] == 1
    assert first["body_len"] == 16
    second = guard.incoming(
        endpoint=EP1, message_id=11, token=TOK1, payload=b"b" * 16,
        block={"num": 1, "more": True, "size": 16}, now=NOW)
    assert second["received"] == 2
    assert second["body_len"] == 32
    final = guard.incoming(
        endpoint=EP1, message_id=12, token=TOK1, payload=b"cd",
        block={"num": 2, "more": False, "size": 16}, now=NOW)
    assert final["status"] == "complete"
    assert final["received"] == 3
    assert final["body"] == b"a" * 16 + b"b" * 16 + b"cd"
    assert guard.stats()["blockwise_completed"] == 1

    guard.incoming(endpoint=EP1, message_id=20, token=TOK2, payload=b"a" * 16,
                   block={"num": 0, "more": True, "size": 16}, now=NOW)
    with pytest.raises(cg.CoapError) as exc:
        guard.incoming(endpoint=EP1, message_id=21, token=TOK2, payload=b"b" * 32,
                       block={"num": 1, "more": False, "size": 32}, now=NOW)
    assert exc.value.code == "COAP_BLOCK_SIZE_CHANGED"


def test_blockwise_out_of_order_bad_size_and_body_limit():
    guard = make()
    with pytest.raises(cg.CoapError) as exc:
        guard.incoming(endpoint=EP1, message_id=1, token=TOK1, payload=b"a" * 16,
                       block={"num": 1, "more": True, "size": 16}, now=NOW)
    assert exc.value.code == "COAP_BLOCK_OUT_OF_ORDER"
    for block in (
        {"num": 0, "more": True, "size": 8},
        {"num": 0, "more": True, "size": 24},
        {"num": 0, "more": True, "size": 2048},
        {"num": 0, "more": "yes", "size": 16},
        {"num": "0", "more": True, "size": 16},
        "block",
    ):
        with pytest.raises(cg.CoapError) as err:
            guard.incoming(endpoint=EP1, message_id=2, token=TOK1,
                           payload=b"a" * 16, block=block, now=NOW)
        assert err.value.code == "COAP_BAD_BLOCK"
    with pytest.raises(cg.CoapError) as exc2:
        guard.incoming(endpoint=EP1, message_id=3, token=TOK1, payload=b"a" * 15,
                       block={"num": 0, "more": True, "size": 16}, now=NOW)
    assert exc2.value.code == "COAP_BAD_BLOCK"

    small = make(max_body_bytes=24)
    small.incoming(endpoint=EP1, message_id=20, token=TOK1, payload=b"a" * 16,
                   block={"num": 0, "more": True, "size": 16}, now=NOW)
    with pytest.raises(cg.CoapError) as exc3:
        small.incoming(endpoint=EP1, message_id=21, token=TOK1, payload=b"b" * 16,
                       block={"num": 1, "more": False, "size": 16}, now=NOW)
    assert exc3.value.code == "COAP_BODY_TOO_LARGE"
    restart = small.incoming(endpoint=EP1, message_id=22, token=TOK1,
                             payload=b"c" * 16,
                             block={"num": 0, "more": True, "size": 16}, now=NOW)
    assert restart["status"] == "incomplete"
    done = small.incoming(endpoint=EP1, message_id=23, token=TOK1,
                          payload=b"d" * 8,
                          block={"num": 1, "more": False, "size": 16}, now=NOW)
    assert done["status"] == "complete"
    assert done["body"] == b"c" * 16 + b"d" * 8


def test_duplicate_message_id_is_ignored_within_exchange_lifetime():
    guard = make(exchange_lifetime=247)
    guard.incoming(endpoint=EP1, message_id=1, token=TOK1, payload=b"one",
                   now=NOW)
    dup = guard.incoming(endpoint=EP1, message_id=1, token=TOK1, payload=b"one",
                         now="2026-11-23T10:01:40+00:00")
    assert dup["status"] == "duplicate"
    assert guard.stats()["duplicates"] == 1
    fresh = guard.incoming(endpoint=EP1, message_id=1, token=TOK1,
                           payload=b"one", now="2026-11-23T10:04:07+00:00")
    assert fresh["status"] == "complete"
    assert guard.stats()["duplicates"] == 1
    guard.incoming(endpoint=EP1, message_id=2, token=TOK1, payload=b"two",
                   now=NOW)
    assert guard.sweep(now="2026-11-23T10:04:07+00:00") == 1


def test_snapshot_restore_keeps_sequences_pending_and_partial_blocks():
    guard = make()
    observe(guard)
    notify(guard, 1)
    guard.incoming(endpoint=EP1, message_id=10, token=TOK1, payload=b"a" * 16,
                   block={"num": 0, "more": True, "size": 16}, now=NOW)
    state = guard.snapshot()

    notify(guard, 2)
    guard.incoming(endpoint=EP1, message_id=11, token=TOK1, payload=b"bb",
                   block={"num": 1, "more": False, "size": 16}, now=NOW)

    restored = cg.CoapObserveGuard()
    restored.restore(state)
    described = restored.describe(endpoint=EP1, token=TOK1, path=PATH)
    assert described["seq"] == 1
    assert described["pending"] == [1]
    delivered = restored.notify(path=PATH, seq=2, payload_len=5, now=NOW)
    assert delivered["delivered"] == [EP1]
    assert restored.ack(endpoint=EP1, token=TOK1, path=PATH, seq=1)["pending"] == 1
    completed = restored.incoming(endpoint=EP1, message_id=11, token=TOK1,
                                  payload=b"bb",
                                  block={"num": 1, "more": False, "size": 16},
                                  now=NOW)
    assert completed["status"] == "complete"
    assert completed["body"] == b"a" * 16 + b"bb"
    assert restored.stats()["notifications"] == 2
    assert restored.stats()["blockwise_completed"] == 1

    bad_version = copy.deepcopy(state)
    bad_version["version"] = 2
    bad_stats = copy.deepcopy(state)
    bad_stats["stats"] = {"observed": 1}
    bad_relations = copy.deepcopy(state)
    bad_relations["relations"] = {}
    bad_time = copy.deepcopy(state)
    bad_time["dedup"][0]["at"] = "not-a-time"
    bad_hex = copy.deepcopy(state)
    bad_hex["blockwise"][0]["body"] = "zz"
    bad_length = copy.deepcopy(state)
    bad_length["blockwise"][0]["body"] = "aa"
    for bad in (None, {}, bad_version, bad_stats, bad_relations, bad_time,
                bad_hex, bad_length):
        with pytest.raises(cg.CoapError) as err:
            restored.restore(bad)
        assert err.value.code == "COAP_SNAPSHOT_INVALID"
    assert restored.describe(endpoint=EP1, token=TOK1, path=PATH)["pending"] == [2]


def test_concurrent_observe_and_duplicate_ingest_are_atomic():
    guard = make()
    barrier = threading.Barrier(8)
    results = []
    failures = []
    lock = threading.Lock()

    def observer():
        barrier.wait()
        try:
            out = guard.observe(endpoint=EP1, token=TOK1, path=PATH, now=NOW)
        except Exception as exc:  # pragma: no cover
            with lock:
                failures.append(exc)
            return
        with lock:
            results.append(out)

    threads = [threading.Thread(target=observer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert sum(1 for row in results if row["existing"] is False) == 1
    assert sum(1 for row in results if row["existing"] is True) == 7
    assert guard.stats()["observed"] == 1

    barrier2 = threading.Barrier(8)
    ingested = []
    duplicates = []
    failures.clear()

    def sender():
        barrier2.wait()
        payload = b"hello"
        try:
            out = guard.incoming(endpoint=EP1, message_id=7, token=TOK1,
                                 payload=payload, now=NOW)
        except Exception as exc:  # pragma: no cover
            with lock:
                failures.append(exc)
            return
        with lock:
            if out["status"] == "complete":
                ingested.append(out)
            else:
                duplicates.append(out)

    threads = [threading.Thread(target=sender) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert len(ingested) == 1
    assert len(duplicates) == 7
    assert guard.stats()["duplicates"] == 7
