"""seccomp user-notification broker with descriptor passing."""

from __future__ import annotations

import threading


class SeccompBrokerError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SeccompNotifyBroker:
    """Broker for seccomp user notifications, replies and injected fds."""

    O_CLOEXEC = 0x1
    SETFD = 0x2

    def __init__(self, *, target_limit: int = 8, max_pending: int = 64,
                 addfd_limit: int = 4, timeout_ms: int = 500,
                 first_fd: int = 1000, clock=None) -> None:
        self.target_limit = target_limit
        self.max_pending = max_pending
        self.addfd_limit = addfd_limit
        self.timeout_ms = timeout_ms
        self.first_fd = first_fd
        self._clock = clock
        self._lock = threading.RLock()

    def attach_target(self, pid: int) -> dict:
        raise NotImplementedError

    def detach_target(self, pid: int) -> dict:
        raise NotImplementedError

    def register_listener(self, path: str, *, listener_fd: int) -> dict:
        raise NotImplementedError

    def unregister_listener(self, path: str) -> dict:
        raise NotImplementedError

    def submit_notification(self, notify_id: int, *, listener_path: str,
                            syscall: int, pid: int, args=(), flags: int = 0,
                            now=None) -> dict:
        raise NotImplementedError

    def reply_continue(self, notify_id: int, *, val: int = 0) -> dict:
        raise NotImplementedError

    def reply_error(self, notify_id: int, *, errno: int) -> dict:
        raise NotImplementedError

    def reply_addfd(self, notify_id: int, *, source_fd: int,
                    target_fd: int | None = None, flags: int = 0) -> dict:
        raise NotImplementedError

    def cancel(self, notify_id: int, *, reason: str = "cancelled") -> dict:
        raise NotImplementedError

    def expire(self, now=None) -> list:
        raise NotImplementedError

    def pending(self) -> list:
        raise NotImplementedError

    def listener_stats(self, path: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
