"""OpenID Federation trust chain resolution and verification."""

from __future__ import annotations

import threading

DEFAULT_MAX_CHAIN_LENGTH = 5
STAT_KEYS = ("resolved", "verified", "revoked", "rejected")


class FederationError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class FederationTrustGuard:
    """Store anchors, entity configurations and subordinate statements."""

    def __init__(self, *, max_chain_length: int = DEFAULT_MAX_CHAIN_LENGTH,
                 clock=None) -> None:
        self.max_chain_length = max_chain_length
        self._clock = clock
        self._lock = threading.RLock()

    def add_trust_anchor(self, entity_id: str, *, jwks_fingerprint: str,
                         expires_at: str, now=None) -> dict:
        raise NotImplementedError

    def add_entity_configuration(self, entity_id: str, *, issuer: str,
                                 jwks_fingerprint: str, authority_hints=None,
                                 metadata=None, expires_at: str,
                                 now=None) -> dict:
        raise NotImplementedError

    def add_subordinate_statement(self, issuer: str, *, subject: str,
                                  jwks_fingerprint: str, metadata=None,
                                  expires_at: str, now=None) -> dict:
        raise NotImplementedError

    def resolve_chain(self, entity_id: str, *, now=None) -> list:
        raise NotImplementedError

    def verify_entity(self, entity_id: str, *, expected_fingerprint: str,
                      now=None) -> dict:
        raise NotImplementedError

    def trusted_entities(self, now=None) -> list:
        raise NotImplementedError

    def revoke_entity(self, entity_id: str, *, cascade: bool = True,
                      reason: str = "manual") -> int:
        raise NotImplementedError

    def is_revoked(self, entity_id: str) -> bool:
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
