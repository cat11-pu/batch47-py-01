"""SIP REGISTER authentication and contact binding guard."""

from __future__ import annotations

import threading


class SipError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SipRegistrationGuard:
    """Validate REGISTER digests and keep per-AOR contact bindings."""

    def __init__(self, *, min_expires: int = 60, max_expires: int = 3600,
                 default_expires: int = 300, max_contacts: int = 8,
                 nonce_ttl: int = 300, clock=None) -> None:
        self.min_expires = min_expires
        self.max_expires = max_expires
        self.default_expires = default_expires
        self.max_contacts = max_contacts
        self.nonce_ttl = nonce_ttl
        self._clock = clock
        self._lock = threading.RLock()

    def add_realm(self, realm: str, *, algorithm: str = "MD5",
                  qop: str = "auth") -> dict:
        raise NotImplementedError

    def add_user(self, aor: str, *, password: str) -> dict:
        raise NotImplementedError

    def issue_nonce(self, realm: str, *, now=None) -> dict:
        raise NotImplementedError

    def submit_register(self, request: dict, *, now=None) -> dict:
        raise NotImplementedError

    def query_bindings(self, aor: str) -> list:
        raise NotImplementedError

    def expire_bindings(self, *, now=None) -> int:
        raise NotImplementedError

    def remove_binding(self, aor: str, *, contact: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
