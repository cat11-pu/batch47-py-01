"""Refresh token rotation with family-based reuse detection."""

from __future__ import annotations

import threading

DEFAULT_GRACE_SECONDS = 30
DEFAULT_MAX_CHAIN = 50
STAT_KEYS = ("rotated", "grace_hits", "reused", "revoked", "rejected")


class RefreshError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class RefreshRotationGuard:
    """Issue, rotate and revoke refresh tokens inside per-login families."""

    def __init__(self, *, grace_seconds: int = DEFAULT_GRACE_SECONDS,
                 max_chain: int = DEFAULT_MAX_CHAIN, clock=None) -> None:
        self.grace_seconds = grace_seconds
        self.max_chain = max_chain
        self._clock = clock
        self._lock = threading.RLock()

    def register_client(self, client_id: str, *, scopes,
                        refresh_ttl: int = 3600,
                        grace_seconds: int | None = None) -> dict:
        raise NotImplementedError

    def issue(self, client_id: str, *, subject: str, scopes, now=None) -> dict:
        raise NotImplementedError

    def rotate(self, refresh_token: str, *, client_id: str, scopes=None,
               now=None) -> dict:
        raise NotImplementedError

    def revoke_token(self, refresh_token: str, *,
                     reason: str = "manual") -> None:
        raise NotImplementedError

    def revoke_family(self, family_id: str, *,
                      reason: str = "manual") -> int:
        raise NotImplementedError

    def inspect(self, refresh_token: str) -> dict:
        raise NotImplementedError

    def family_tokens(self, family_id: str) -> list:
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
