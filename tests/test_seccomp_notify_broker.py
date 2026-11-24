import threading
from datetime import datetime, timedelta

import pytest

import seccomp_notify_broker as nb

T0 = "2026-11-24T10:00:00+00:00"
DEADLINE = "2026-11-24T10:00:00.500000+00:00"
LISTENER = "/run/scmp/listener"


def moment(value):
    return datetime.fromisoformat(value)


def make(**kwargs):
    broker = nb.SeccompNotifyBroker(**kwargs)
    broker.attach_target(4242)
    broker.register_listener(LISTENER, listener_fd=7)
    return broker


def notify(broker, notify_id=11, **kwargs):
    kwargs.setdefault("now", T0)
    return broker.submit_notification(
        notify_id,
        listener_path=LISTENER,
        syscall=257,
        pid=4242,
        **kwargs,
    )


def test_submit_records_pending_notification_and_continue_reply():
    broker = make()
    result = notify(broker)
    assert result["status"] == "pending"
    assert result["notify_id"] == 11
    assert result["listener"] == LISTENER
    assert result["syscall"] == 257
    assert result["pid"] == 4242
    assert result["pending"] == 1
    assert moment(result["deadline"]) == moment(T0) + timedelta(milliseconds=500)

    listed = broker.pending()
    assert len(listed) == 1
    assert listed[0]["notify_id"] == 11
    assert listed[0]["listener"] == LISTENER
    assert listed[0]["syscall"] == 257
    assert listed[0]["addfd_count"] == 0
    assert broker.listener_stats(LISTENER) == {
        "path": LISTENER,
        "submitted": 1,
        "replied": 0,
        "expired": 0,
        "cancelled": 0,
        "abandoned": 0,
        "pending": 1,
    }

    replied = broker.reply_continue(11, val=42)
    assert replied["status"] == "replied"
    assert replied["action"] == "continue"
    assert replied["notify_id"] == 11
    assert replied["val"] == 42
    assert broker.pending() == []
    assert broker.stats() == {
        "submitted": 1,
        "replied": 1,
        "expired": 0,
        "cancelled": 0,
        "abandoned": 0,
        "rejected": 0,
        "installed_fds": 0,
    }
    stats = broker.listener_stats(LISTENER)
    assert stats["replied"] == 1
    assert stats["pending"] == 0


def test_unknown_listener_target_and_duplicate_notify_id():
    broker = make()
    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.submit_notification(
            21, listener_path="/run/missing", syscall=257, pid=4242, now=T0
        )
    assert exc.value.code == "SECCOMP_UNKNOWN_LISTENER"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.submit_notification(
            22, listener_path=LISTENER, syscall=257, pid=9999, now=T0
        )
    assert exc.value.code == "SECCOMP_UNKNOWN_TARGET"

    notify(broker, 23)
    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 23)
    assert exc.value.code == "SECCOMP_DUPLICATE_NOTIFY"

    broker.reply_continue(23)
    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 23)
    assert exc.value.code == "SECCOMP_DUPLICATE_NOTIFY"

    broker.detach_target(4242)
    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 24)
    assert exc.value.code == "SECCOMP_TARGET_GONE"


def test_notify_id_and_argument_validation():
    broker = make()
    for bad in (0, -5, "11"):
        with pytest.raises(nb.SeccompBrokerError) as exc:
            broker.submit_notification(
                bad, listener_path=LISTENER, syscall=257, pid=4242, now=T0
            )
        assert exc.value.code == "SECCOMP_INVALID_NOTIFY_ID"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 11, args=(0, 1, 2, 3, 4, 5, 6))
    assert exc.value.code == "SECCOMP_INVALID_ARGUMENT"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 12, args=(0, 1, "2"))
    assert exc.value.code == "SECCOMP_INVALID_ARGUMENT"

    accepted = notify(broker, 13, args=(0, 1, 2, 3, 4, 5))
    assert accepted["status"] == "pending"
    assert [item["notify_id"] for item in broker.pending()] == [13]


def test_listener_registration_conflict_and_unregister():
    broker = make()
    again = broker.register_listener(LISTENER, listener_fd=7)
    assert again["path"] == LISTENER
    assert again["listener_fd"] == 7

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.register_listener(LISTENER, listener_fd=8)
    assert exc.value.code == "SECCOMP_LISTENER_CONFLICT"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.register_listener("", listener_fd=7)
    assert exc.value.code == "SECCOMP_INVALID_ARGUMENT"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.register_listener("/run/scmp/negative", listener_fd=-1)
    assert exc.value.code == "SECCOMP_INVALID_FD"

    notify(broker, 11)
    closed = broker.unregister_listener(LISTENER)
    assert closed["path"] == LISTENER
    assert closed["abandoned"] == 1
    assert broker.stats()["abandoned"] == 1

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.unregister_listener(LISTENER)
    assert exc.value.code == "SECCOMP_UNKNOWN_LISTENER"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 12)
    assert exc.value.code == "SECCOMP_UNKNOWN_LISTENER"


def test_addfd_assigns_lowest_available_descriptor():
    broker = make()
    notify(broker, 11)

    first = broker.reply_addfd(11, source_fd=5)
    assert first["status"] == "pending"
    assert first["action"] == "addfd"
    assert first["notify_id"] == 11
    assert first["installed_fd"] == 1000
    assert first["source_fd"] == 5
    assert first["addfd_count"] == 1

    second = broker.reply_addfd(
        11, source_fd=6, flags=nb.SeccompNotifyBroker.O_CLOEXEC
    )
    assert second["installed_fd"] == 1001
    assert second["flags"] == 1

    explicit = broker.reply_addfd(11, source_fd=7, target_fd=1010)
    assert explicit["installed_fd"] == 1010
    assert explicit["addfd_count"] == 3

    filled = broker.reply_addfd(11, source_fd=8)
    assert filled["installed_fd"] == 1002
    assert broker.stats()["installed_fds"] == 4
    assert broker.pending()[0]["addfd_count"] == 4

    done = broker.reply_continue(11)
    assert done["status"] == "replied"
    assert broker.stats()["installed_fds"] == 4


def test_addfd_validation_rejects_conflicts_and_bad_flags():
    broker = make()
    notify(broker, 11)
    broker.reply_addfd(11, source_fd=5, target_fd=1010)

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_addfd(11, source_fd=6, target_fd=1010)
    assert exc.value.code == "SECCOMP_FD_IN_USE"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_addfd(11, source_fd=-1)
    assert exc.value.code == "SECCOMP_INVALID_FD"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_addfd(11, source_fd=5, flags=0x4)
    assert exc.value.code == "SECCOMP_INVALID_FLAGS"

    assert broker.stats()["installed_fds"] == 1
    assert broker.pending()[0]["addfd_count"] == 1


def test_addfd_limit_keeps_notification_pending():
    broker = make(addfd_limit=2)
    notify(broker, 11)
    broker.reply_addfd(11, source_fd=5)
    broker.reply_addfd(11, source_fd=6)

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_addfd(11, source_fd=7)
    assert exc.value.code == "SECCOMP_ADDFD_LIMIT"

    assert [item["notify_id"] for item in broker.pending()] == [11]
    assert broker.pending()[0]["addfd_count"] == 2
    assert broker.stats()["installed_fds"] == 2
    assert broker.reply_continue(11)["status"] == "replied"


def test_error_reply_validates_errno_range():
    broker = make()
    notify(broker, 11)
    for bad in (0, 5000, -1):
        with pytest.raises(nb.SeccompBrokerError) as exc:
            broker.reply_error(11, errno=bad)
        assert exc.value.code == "SECCOMP_INVALID_ERRNO"

    result = broker.reply_error(11, errno=13)
    assert result["status"] == "replied"
    assert result["action"] == "error"
    assert result["errno"] == 13

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_error(11, errno=13)
    assert exc.value.code == "SECCOMP_ALREADY_REPLIED"


def test_reply_states_unknown_and_already_replied():
    broker = make()
    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_continue(9999)
    assert exc.value.code == "SECCOMP_UNKNOWN_NOTIFY"

    notify(broker, 11)
    broker.reply_continue(11, val=0)
    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_continue(11)
    assert exc.value.code == "SECCOMP_ALREADY_REPLIED"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_error(11, errno=1)
    assert exc.value.code == "SECCOMP_ALREADY_REPLIED"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_addfd(11, source_fd=5)
    assert exc.value.code == "SECCOMP_ALREADY_REPLIED"
    assert broker.stats()["replied"] == 1


def test_expire_releases_pending_notifications_and_fds():
    broker = make()
    notify(broker, 11)
    notify(broker, 12)
    broker.reply_addfd(11, source_fd=5)
    assert broker.expire(now=T0) == []

    expired = broker.expire(now=DEADLINE)
    assert [item["notify_id"] for item in expired] == [11, 12]
    assert [item["status"] for item in expired] == ["expired", "expired"]
    assert expired[0]["released_fds"] == 1
    assert expired[1]["released_fds"] == 0
    assert broker.pending() == []
    assert broker.stats()["expired"] == 2
    assert broker.stats()["installed_fds"] == 0

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.reply_continue(11)
    assert exc.value.code == "SECCOMP_NOTIFY_GONE"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.cancel(11)
    assert exc.value.code == "SECCOMP_NOTIFY_GONE"


def test_cancel_pending_notification_and_release_fds():
    broker = make()
    notify(broker, 11)
    broker.reply_addfd(11, source_fd=5)

    result = broker.cancel(11, reason="policy")
    assert result["status"] == "cancelled"
    assert result["notify_id"] == 11
    assert result["reason"] == "policy"
    assert result["released_fds"] == 1
    assert broker.stats()["cancelled"] == 1
    assert broker.stats()["installed_fds"] == 0

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.cancel(11)
    assert exc.value.code == "SECCOMP_NOTIFY_GONE"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.cancel(9999)
    assert exc.value.code == "SECCOMP_UNKNOWN_NOTIFY"


def test_detach_abandons_pending_and_enforces_target_limit():
    broker = nb.SeccompNotifyBroker(target_limit=1)
    broker.attach_target(4242)
    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.attach_target(5555)
    assert exc.value.code == "SECCOMP_TARGET_LIMIT"

    broker.register_listener(LISTENER, listener_fd=7)
    notify(broker, 11)
    notify(broker, 12)
    broker.reply_addfd(11, source_fd=5)

    result = broker.detach_target(4242)
    assert result == {
        "pid": 4242,
        "attached": False,
        "abandoned": 2,
        "released_fds": 1,
    }
    assert broker.pending() == []
    assert broker.stats()["abandoned"] == 2

    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 13)
    assert exc.value.code == "SECCOMP_TARGET_GONE"

    with pytest.raises(nb.SeccompBrokerError) as exc:
        broker.detach_target(4242)
    assert exc.value.code == "SECCOMP_UNKNOWN_TARGET"

    assert broker.attach_target(5555)["attached"] is True


def test_pending_capacity_and_timeout_sweep():
    broker = make(max_pending=2)
    notify(broker, 11)
    notify(broker, 12)

    with pytest.raises(nb.SeccompBrokerError) as exc:
        notify(broker, 13)
    assert exc.value.code == "SECCOMP_PENDING_FULL"
    assert broker.stats()["rejected"] == 1

    expired = broker.expire(now=DEADLINE)
    assert [item["notify_id"] for item in expired] == [11, 12]

    accepted = notify(broker, 13)
    assert accepted["status"] == "pending"
    assert accepted["pending"] == 1
    assert broker.stats()["submitted"] == 3


def test_snapshot_restore_preserves_pending_and_fd_allocations():
    broker = make()
    notify(broker, 11)
    broker.reply_addfd(11, source_fd=5)
    broker.reply_continue(11)
    notify(broker, 12)
    broker.reply_addfd(12, source_fd=6)
    snap = broker.snapshot()
    notify(broker, 13)

    clone = nb.SeccompNotifyBroker()
    with pytest.raises(nb.SeccompBrokerError) as exc:
        clone.restore({"targets": {}, "listeners": {}})
    assert exc.value.code == "SECCOMP_SNAPSHOT_INVALID"
    assert clone.pending() == []

    clone.restore(snap)
    assert [item["notify_id"] for item in clone.pending()] == [12]
    assert clone.pending()[0]["addfd_count"] == 1
    assert clone.stats()["installed_fds"] == 2

    installed = clone.reply_addfd(12, source_fd=8)
    assert installed["installed_fd"] == 1002
    assert clone.stats()["installed_fds"] == 3
    assert clone.reply_continue(12)["status"] == "replied"

    assert [item["notify_id"] for item in broker.pending()] == [12, 13]


def test_concurrent_reply_single_winner():
    broker = make()
    notify(broker, 11)
    barrier = threading.Barrier(8)
    successes = []
    failures = []

    def worker():
        try:
            barrier.wait(timeout=5)
            successes.append(broker.reply_continue(11, val=0))
        except nb.SeccompBrokerError as err:
            failures.append(err.code)
        except BaseException as err:  # noqa: BLE001
            failures.append(repr(err))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert len(failures) == 7
    assert failures.count("SECCOMP_ALREADY_REPLIED") == 7
    assert broker.stats()["replied"] == 1
    assert broker.pending() == []
