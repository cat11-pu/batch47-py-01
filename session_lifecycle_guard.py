"""Web session lifecycle guard."""

from __future__ import annotations

import threading


class SessionError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SessionLifecycleGuard:
    """Create, rotate, elevate, revoke and expire login sessions."""

    def __init__(self, *, idle_timeout: int = 900, absolute_timeout: int = 28800,
                 max_sessions_per_user: int = 5, clock=None) -> None:
        self.idle_timeout = idle_timeout
        self.absolute_timeout = absolute_timeout
        self.max_sessions_per_user = max_sessions_per_user
        self._clock = clock
        self._lock = threading.RLock()

    def create_session(self, *, user_id: str, device_id: str, ip: str,
                       auth_level: int = 1, pin_ip: bool = False, now=None) -> dict:
        raise NotImplementedError

    def validate(self, session_id: str, *, device_id: str | None = None,
                 ip: str | None = None, now=None) -> dict:
        raise NotImplementedError

    def rotate(self, session_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def elevate(self, session_id: str, *, auth_level: int, now=None) -> dict:
        raise NotImplementedError

    def revoke(self, session_id: str, *, reason: str = "user") -> bool:
        raise NotImplementedError

    def revoke_all(self, user_id: str, *, except_session: str | None = None) -> int:
        raise NotImplementedError

    def list_sessions(self, user_id: str, *, now=None) -> list:
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
