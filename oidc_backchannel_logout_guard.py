"""OIDC back-channel logout token guard."""

from __future__ import annotations

import threading

LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"


class OidcLogoutError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BackchannelLogoutGuard:
    """Validate logout tokens, deduplicate jti and revoke client sessions."""

    def __init__(self, *, clock_skew: int = 60, max_jti: int = 500,
                 session_capacity: int = 100, batch_limit: int = 20,
                 clock=None) -> None:
        self.clock_skew = clock_skew
        self.max_jti = max_jti
        self.session_capacity = session_capacity
        self.batch_limit = batch_limit
        self._clock = clock
        self._lock = threading.RLock()

    def register_client(self, client_id: str, *, issuer: str, audiences,
                        logout_endpoint: str | None = None) -> dict:
        raise NotImplementedError

    def trust_issuer(self, issuer: str, *, fingerprints,
                     allowed_algs=None) -> dict:
        raise NotImplementedError

    def revoke_issuer(self, issuer: str) -> None:
        raise NotImplementedError

    def start_session(self, session_id: str, *, client_id: str, subject: str,
                      sid: str | None = None) -> dict:
        raise NotImplementedError

    def validate_logout_token(self, token: dict, *, client_id: str,
                              now=None) -> dict:
        raise NotImplementedError

    def apply_logout(self, token: dict, *, client_id: str, now=None) -> dict:
        raise NotImplementedError

    def apply_logout_batch(self, tokens, *, client_id: str, now=None) -> list:
        raise NotImplementedError

    def is_session_active(self, session_id: str) -> bool:
        raise NotImplementedError

    def session_status(self, session_id: str) -> dict:
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
