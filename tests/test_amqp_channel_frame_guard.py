import threading

import pytest

import amqp_channel_frame_guard as am

NOW = "2026-11-24T10:00:00+00:00"


def make(**kwargs):
    guard = am.AmqpChannelFrameGuard(**kwargs)
    guard.open_connection("conn-1", now=NOW)
    return guard


def with_channel(**kwargs):
    guard = make(**kwargs)
    guard.open_channel("conn-1", channel_id=1, now=NOW)
    return guard


def test_connection_negotiation_rejects_out_of_range_values():
    guard = am.AmqpChannelFrameGuard()
    opened = guard.open_connection("conn-1", now=NOW)
    assert opened == {"conn_id": "conn-1", "frame_max": 131072,
                      "heartbeat": 60, "channels": []}
    narrowed = guard.open_connection("conn-2", frame_max=8192, heartbeat=15, now=NOW)
    assert narrowed["frame_max"] == 8192
    assert narrowed["heartbeat"] == 15
    with pytest.raises(am.AmqpError) as exc:
        guard.open_connection("conn-1", now=NOW)
    assert exc.value.code == "AMQP_DUPLICATE_CONNECTION"
    for bad_frame_max in (1024, 262144, "8192"):
        with pytest.raises(am.AmqpError) as exc:
            guard.open_connection("conn-x", frame_max=bad_frame_max, now=NOW)
        assert exc.value.code == "AMQP_BAD_FRAME_MAX"
    for bad_beat in (-1, 70000, True):
        with pytest.raises(am.AmqpError) as exc:
            guard.open_connection("conn-y", heartbeat=bad_beat, now=NOW)
        assert exc.value.code == "AMQP_BAD_HEARTBEAT"


def test_channel_allocation_prefers_lowest_free_slot():
    guard = make(channel_max=4)
    first = guard.open_channel("conn-1", now=NOW)
    second = guard.open_channel("conn-1", now=NOW)
    third = guard.open_channel("conn-1", now=NOW)
    assert [first["channel_id"], second["channel_id"], third["channel_id"]] == [1, 2, 3]
    guard.close_channel("conn-1", 2, now=NOW)
    reused = guard.open_channel("conn-1", now=NOW)
    assert reused["channel_id"] == 2
    assert reused["next_seq"] == 1
    with pytest.raises(am.AmqpError) as exc:
        guard.open_channel("conn-1", channel_id=2, now=NOW)
    assert exc.value.code == "AMQP_CHANNEL_IN_USE"
    with pytest.raises(am.AmqpError) as exc:
        guard.open_channel("conn-1", channel_id=0, now=NOW)
    assert exc.value.code == "AMQP_RESERVED_CHANNEL"
    with pytest.raises(am.AmqpError) as exc:
        guard.open_channel("conn-1", channel_id=9, now=NOW)
    assert exc.value.code == "AMQP_CHANNEL_LIMIT"
    guard.open_channel("conn-1", channel_id=4, now=NOW)
    with pytest.raises(am.AmqpError) as exc:
        guard.open_channel("conn-1", now=NOW)
    assert exc.value.code == "AMQP_CHANNEL_LIMIT"


def test_closing_a_channel_reports_unconfirmed_messages():
    guard = with_channel()
    for _ in range(3):
        guard.publish("conn-1", 1, exchange="amq.topic", routing_key="rk", body="x", now=NOW)
    guard.confirm("conn-1", 1, delivery_tag=1, now=NOW)
    closed = guard.close_channel("conn-1", 1, now=NOW)
    assert closed == {"closed": True, "channel_id": 1, "unacked": 2}
    with pytest.raises(am.AmqpError) as exc:
        guard.close_channel("conn-1", 1, now=NOW)
    assert exc.value.code == "AMQP_UNKNOWN_CHANNEL"


def test_frame_type_channel_and_size_rules():
    guard = with_channel(frame_max=8192)
    assert guard.frame("conn-1", channel_id=0, frame_type="heartbeat",
                       payload_size=0, now=NOW)["frame_type"] == "heartbeat"
    with pytest.raises(am.AmqpError) as exc:
        guard.frame("conn-1", channel_id=1, frame_type="heartbeat",
                    payload_size=0, now=NOW)
    assert exc.value.code == "AMQP_BAD_FRAME"
    with pytest.raises(am.AmqpError) as exc:
        guard.frame("conn-1", channel_id=0, frame_type="heartbeat",
                    payload_size=1, now=NOW)
    assert exc.value.code == "AMQP_BAD_FRAME"
    with pytest.raises(am.AmqpError) as exc:
        guard.frame("conn-1", channel_id=1, frame_type="properties",
                    payload_size=8, now=NOW)
    assert exc.value.code == "AMQP_BAD_FRAME"
    assert guard.frame("conn-1", channel_id=1, frame_type="body",
                       payload_size=8192, now=NOW)["payload_size"] == 8192
    with pytest.raises(am.AmqpError) as exc:
        guard.frame("conn-1", channel_id=1, frame_type="body",
                    payload_size=8193, now=NOW)
    assert exc.value.code == "AMQP_FRAME_TOO_LARGE"
    with pytest.raises(am.AmqpError) as exc:
        guard.frame("conn-1", channel_id=7, frame_type="method",
                    payload_size=1, now=NOW)
    assert exc.value.code == "AMQP_UNKNOWN_CHANNEL"


def test_body_assembly_checks_declared_and_actual_sizes():
    guard = with_channel(frame_max=8192, max_body_bytes=25)
    assert guard.assemble("conn-1", 1, parts=[10, 15], declared_size=25, now=NOW) == {
        "total": 25, "frames": 2}
    with pytest.raises(am.AmqpError) as exc:
        guard.assemble("conn-1", 1, parts=[10, 15], declared_size=26, now=NOW)
    assert exc.value.code == "AMQP_BODY_INCOMPLETE"
    with pytest.raises(am.AmqpError) as exc:
        guard.assemble("conn-1", 1, parts=[10, 15], declared_size=24, now=NOW)
    assert exc.value.code == "AMQP_BODY_TOO_LARGE"
    with pytest.raises(am.AmqpError) as exc:
        guard.assemble("conn-1", 1, parts=[9000], now=NOW)
    assert exc.value.code == "AMQP_FRAME_TOO_LARGE"
    with pytest.raises(am.AmqpError) as exc:
        guard.assemble("conn-1", 1, parts=[], now=NOW)
    assert exc.value.code == "AMQP_BAD_FRAME"
    with pytest.raises(am.AmqpError) as exc:
        guard.assemble("conn-1", 1, parts=[20, 10], declared_size=30, now=NOW)
    assert exc.value.code == "AMQP_BODY_TOO_LARGE"
    assert guard.assemble("conn-1", 1, parts=[5, 5], declared_size=10, now=NOW)["total"] == 10


def test_publish_assigns_monotonic_sequences_per_channel():
    guard = with_channel()
    first = guard.publish("conn-1", 1, exchange="amq.direct", routing_key="a", body="one", now=NOW)
    second = guard.publish("conn-1", 1, exchange="amq.direct", routing_key="b", body="two", now=NOW)
    third = guard.publish("conn-1", 1, exchange="amq.direct", routing_key="c", body="", now=NOW)
    assert [first["seq"], second["seq"], third["seq"]] == [1, 2, 3]
    assert third["unacked"] == 3
    assert guard.stats()["published"] == 3


def test_publish_validates_body_size_and_exchange():
    guard = with_channel(max_body_bytes=8)
    with pytest.raises(am.AmqpError) as exc:
        guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k",
                      body="123456789", now=NOW)
    assert exc.value.code == "AMQP_BODY_TOO_LARGE"
    assert guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k",
                         body="12345678", now=NOW)["seq"] == 1
    with pytest.raises(am.AmqpError) as exc:
        guard.publish("conn-1", 1, exchange="", routing_key="k", body="x", now=NOW)
    assert exc.value.code == "AMQP_BAD_EXCHANGE"
    with pytest.raises(am.AmqpError) as exc:
        guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k",
                      body=b"bytes", now=NOW)
    assert exc.value.code == "AMQP_BAD_BODY"


def test_confirms_accept_out_of_order_but_never_twice():
    guard = with_channel()
    for _ in range(3):
        guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k", body="x", now=NOW)
    out_of_order = guard.confirm("conn-1", 1, delivery_tag=2, now=NOW)
    assert out_of_order == {"confirmed": True, "delivery_tag": 2, "unacked": 2}
    with pytest.raises(am.AmqpError) as exc:
        guard.confirm("conn-1", 1, delivery_tag=2, now=NOW)
    assert exc.value.code == "AMQP_UNKNOWN_TAG"
    for bad_tag in (0, 4, -1):
        with pytest.raises(am.AmqpError) as exc:
            guard.confirm("conn-1", 1, delivery_tag=bad_tag, now=NOW)
        assert exc.value.code == "AMQP_UNKNOWN_TAG"
    assert guard.confirm("conn-1", 1, delivery_tag=3, now=NOW)["unacked"] == 1
    assert guard.confirm("conn-1", 1, delivery_tag=1, now=NOW)["unacked"] == 0


def test_heartbeat_timeout_fires_at_twice_the_interval():
    guard = make(heartbeat=60)
    assert guard.heartbeat("conn-1", now=NOW)["last_seen"] == 1795514400
    assert guard.check_timeout("conn-1", now="2026-11-24T10:01:59+00:00")["idle"] == 119
    with pytest.raises(am.AmqpError) as exc:
        guard.check_timeout("conn-1", now="2026-11-24T10:02:00+00:00")
    assert exc.value.code == "AMQP_HEARTBEAT_TIMEOUT"
    guard.heartbeat("conn-1", now="2026-11-24T10:02:00+00:00")
    assert guard.check_timeout("conn-1", now="2026-11-24T10:02:59+00:00")["expired"] is False

    silent = am.AmqpChannelFrameGuard()
    silent.open_connection("conn-0", heartbeat=0, now=NOW)
    assert silent.check_timeout("conn-0", now="2026-11-25T10:00:00+00:00") == {
        "expired": False, "idle": -1}


def test_unknown_and_closed_targets_are_refused():
    guard = make()
    with pytest.raises(am.AmqpError) as exc:
        guard.open_channel("ghost", now=NOW)
    assert exc.value.code == "AMQP_UNKNOWN_CONNECTION"
    guard.open_channel("conn-1", channel_id=1, now=NOW)
    guard.open_channel("conn-1", channel_id=2, now=NOW)
    closed = guard.close_connection("conn-1", now=NOW)
    assert closed == {"closed": True, "conn_id": "conn-1", "dropped_channels": 2}
    with pytest.raises(am.AmqpError) as exc:
        guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k",
                      body="x", now=NOW)
    assert exc.value.code == "AMQP_CONNECTION_CLOSED"
    with pytest.raises(am.AmqpError) as exc:
        guard.frame("ghost", channel_id=1, frame_type="body",
                    payload_size=1, now=NOW)
    assert exc.value.code == "AMQP_UNKNOWN_CONNECTION"


def test_stats_count_frames_channels_and_confirms():
    guard = make()
    guard.open_channel("conn-1", channel_id=1, now=NOW)
    guard.open_channel("conn-1", channel_id=2, now=NOW)
    guard.frame("conn-1", channel_id=1, frame_type="method", payload_size=12, now=NOW)
    guard.frame("conn-1", channel_id=0, frame_type="heartbeat", payload_size=0, now=NOW)
    guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k", body="a", now=NOW)
    guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k", body="b", now=NOW)
    guard.confirm("conn-1", 1, delivery_tag=1, now=NOW)
    assert guard.stats() == {"connections_opened": 1, "channels_opened": 2,
                             "frames": 2, "published": 2, "confirmed": 1}
    guard.reset_stats()
    assert guard.stats() == {"connections_opened": 0, "channels_opened": 0,
                             "frames": 0, "published": 0, "confirmed": 0}


def test_snapshot_restore_preserves_sequences_and_confirm_state():
    guard = with_channel()
    for _ in range(3):
        guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k", body="x", now=NOW)
    guard.confirm("conn-1", 1, delivery_tag=1, now=NOW)
    state = guard.snapshot()
    guard.publish("conn-1", 1, exchange="amq.direct", routing_key="k", body="y", now=NOW)

    restored = am.AmqpChannelFrameGuard()
    restored.restore(state)
    resumed = restored.publish("conn-1", 1, exchange="amq.direct", routing_key="k",
                               body="z", now=NOW)
    assert resumed["seq"] == 4
    assert restored.confirm("conn-1", 1, delivery_tag=2, now=NOW)["unacked"] == 2
    with pytest.raises(am.AmqpError) as exc:
        restored.confirm("conn-1", 1, delivery_tag=1, now=NOW)
    assert exc.value.code == "AMQP_UNKNOWN_TAG"

    state["connections"].clear()
    state["stats"]["published"] = 99
    assert restored.publish("conn-1", 1, exchange="amq.direct", routing_key="k",
                            body="w", now=NOW)["seq"] == 5

    good_stats = {name: 0 for name in am.STAT_KEYS}
    bad_states = [
        None,
        {},
        {"version": 2, "connections": {}, "stats": good_stats},
        {"version": 1, "connections": "nope", "stats": good_stats},
        {"version": 1, "connections": {}, "stats": {"published": 0}},
        {"version": 1, "connections": {"conn-1": {"frame_max": 1024}},
         "stats": good_stats},
    ]
    for bad in bad_states:
        with pytest.raises(am.AmqpError) as exc:
            restored.restore(bad)
        assert exc.value.code == "AMQP_SNAPSHOT_INVALID"
    assert restored.publish("conn-1", 1, exchange="amq.direct", routing_key="k",
                            body="v", now=NOW)["seq"] == 6


def test_connection_limit_and_purge_of_closed_connections():
    guard = am.AmqpChannelFrameGuard(max_connections=2)
    guard.open_connection("conn-1", now=NOW)
    guard.open_connection("conn-2", now=NOW)
    with pytest.raises(am.AmqpError) as exc:
        guard.open_connection("conn-3", now=NOW)
    assert exc.value.code == "AMQP_CONNECTION_LIMIT"
    guard.close_connection("conn-1", now=NOW)
    assert guard.purge(now=NOW) == {"connections": 1}
    assert guard.open_connection("conn-3", now=NOW)["conn_id"] == "conn-3"


def test_concurrent_publishes_get_unique_sequences():
    guard = with_channel()
    barrier = threading.Barrier(8)
    sequences = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        result = guard.publish("conn-1", 1, exchange="amq.direct",
                               routing_key="r%d" % index, body="payload", now=NOW)
        with lock:
            sequences.append(result["seq"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(sequences) == list(range(1, 9))
    assert guard.stats()["published"] == 8


def test_concurrent_channel_allocation_respects_channel_max():
    guard = make(channel_max=4)
    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            guard.open_channel("conn-1", now=NOW)
            value = "opened"
        except Exception as exc:
            value = getattr(exc, "code", repr(exc))
        with lock:
            outcomes.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("opened") == 4
    assert outcomes.count("AMQP_CHANNEL_LIMIT") == 4
    assert guard.stats()["channels_opened"] == 4
