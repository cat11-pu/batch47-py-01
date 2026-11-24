"""Instance metadata credential broker (implementation skeleton)."""

from __future__ import annotations

import threading


class ImdsError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ImdsCredentialBroker:
    """Broker short-lived role credentials for workloads behind a metadata endpoint."""

    def __init__(self, *, token_ttl: int = 21600, renew_window: int = 900,
                 max_renewals: int = 3, clock=None) -> None:
        self.token_ttl = token_ttl
        self.renew_window = renew_window
        self.max_renewals = max_renewals
        self._clock = clock
        self._lock = threading.RLock()

    def register_role(self, role: str, *, arn: str, max_sessions: int = 10,
                      session_ttl: int = 3600, max_ttl: int = 43200) -> dict:
        raise NotImplementedError

    def register_node(self, node: str, *, hop_limit: int = 1) -> dict:
        raise NotImplementedError

    def allow_role(self, node: str, role: str) -> dict:
        raise NotImplementedError

    def rotate_token(self, node: str, *, now=None) -> dict:
        raise NotImplementedError

    def issue_session(self, role: str, *, node: str, token: str, hops: int,
                      ttl: int | None = None, now=None) -> dict:
        raise NotImplementedError

    def renew_session(self, session_id: str, *, ttl: int | None = None,
                      now=None) -> dict:
        raise NotImplementedError

    def revoke_session(self, session_id: str, *, reason: str) -> dict:
        raise NotImplementedError

    def validate(self, session_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def sweep(self, *, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
