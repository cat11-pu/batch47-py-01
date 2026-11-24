"""Token exchange delegation guard."""

from __future__ import annotations

import threading


class ExchangeError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DelegationExchangeGuard:
    """Narrow subject tokens and track delegation chains between services."""

    def __init__(self, *, max_delegation_depth: int = 3, max_jti: int = 1000,
                 ttl: int = 300, clock=None) -> None:
        self.max_delegation_depth = max_delegation_depth
        self.max_jti = max_jti
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.RLock()

    def register_issuer(self, issuer: str, *, audiences, scopes) -> dict:
        raise NotImplementedError

    def register_actor(self, actor_id: str, *, audiences, scopes) -> dict:
        raise NotImplementedError

    def register_resource(self, audience: str, *, scopes) -> dict:
        raise NotImplementedError

    def issue_subject_token(self, *, issuer: str, subject: str, audience: str,
                            scopes, jti: str | None = None,
                            not_before=None, expires_at=None, now=None) -> dict:
        raise NotImplementedError

    def issue_actor_token(self, actor_id: str, *, audience: str, scopes,
                          jti: str | None = None, not_before=None,
                          expires_at=None, now=None) -> dict:
        raise NotImplementedError

    def exchange(self, *, subject_token: dict, actor_token: dict | None = None,
                 audience: str, scopes, now=None) -> dict:
        raise NotImplementedError

    def introspect(self, token: dict, *, now=None) -> dict:
        raise NotImplementedError

    def revoke(self, jti: str) -> None:
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
