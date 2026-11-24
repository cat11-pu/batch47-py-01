"""Device authorization grant guard."""

from __future__ import annotations

import threading


class DeviceGrantError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DeviceAuthorizationGuard:
    """Track device codes, user codes and the polling lifecycle."""

    def __init__(self, *, poll_interval: int = 5, expires_in: int = 600,
                 max_pending: int = 200, clock=None) -> None:
        self.poll_interval = poll_interval
        self.expires_in = expires_in
        self.max_pending = max_pending
        self._clock = clock
        self._lock = threading.RLock()

    def register_client(self, client_id: str, *, scopes,
                        verification_uri: str) -> dict:
        raise NotImplementedError

    def start(self, *, client_id: str, scope: str,
              device_hint: str | None = None, now=None) -> dict:
        raise NotImplementedError

    def lookup_user_code(self, user_code: str, now=None) -> dict:
        raise NotImplementedError

    def approve(self, user_code: str, *, subject: str, now=None) -> dict:
        raise NotImplementedError

    def deny(self, user_code: str, *, reason: str, now=None) -> dict:
        raise NotImplementedError

    def poll(self, *, client_id: str, device_code: str, now=None) -> dict:
        raise NotImplementedError

    def cancel(self, device_code: str) -> bool:
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
