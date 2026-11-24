"""SYN flood admission control with stateless cookie challenges."""

from __future__ import annotations

import threading


class SynGuardError(ValueError):
    """Raised when a handshake operation is invalid or inconsistent."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SynAdmissionGuard:
    """Track half-open handshakes per listener and per source address."""

    def __init__(self, *, max_half_open: int = 256, per_source_limit: int = 8,
                 syn_timeout: int = 15, established_timeout: int = 300,
                 cookie_secret: bytes = b"syn-admission-default",
                 cookie_window: int = 60, clock=None) -> None:
        self.max_half_open = max_half_open
        self.per_source_limit = per_source_limit
        self.syn_timeout = syn_timeout
        self.established_timeout = established_timeout
        self.cookie_secret = cookie_secret
        self.cookie_window = cookie_window
        self._clock = clock
        self._lock = threading.RLock()

    def register_listener(self, port: int, *, backlog: int = 128) -> dict:
        raise NotImplementedError

    def unregister_listener(self, port: int) -> bool:
        raise NotImplementedError

    def syn(self, src_ip: str, src_port: int, dst_port: int, *, seq: int,
            now=None) -> dict:
        raise NotImplementedError

    def ack(self, src_ip: str, src_port: int, dst_port: int, *, ack: int,
            cookie=None, now=None) -> dict:
        raise NotImplementedError

    def reset(self, src_ip: str, src_port: int, dst_port: int, *,
              now=None) -> bool:
        raise NotImplementedError

    def half_open_count(self, dst_port: int | None = None) -> int:
        raise NotImplementedError

    def established_count(self, dst_port: int | None = None) -> int:
        raise NotImplementedError

    def is_established(self, src_ip: str, src_port: int, dst_port: int, *,
                       now=None) -> bool:
        raise NotImplementedError

    def block_source(self, ip: str, *, reason: str = "manual") -> dict:
        raise NotImplementedError

    def unblock_source(self, ip: str) -> bool:
        raise NotImplementedError

    def is_blocked(self, ip: str) -> bool:
        raise NotImplementedError

    def purge(self, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
