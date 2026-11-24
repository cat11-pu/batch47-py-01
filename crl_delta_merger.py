"""CRL base and delta merging guard (implementation skeleton)."""

from __future__ import annotations

import threading

REASONS = frozenset(
    {
        "key_compromise",
        "ca_compromise",
        "affiliation_changed",
        "superseded",
        "cessation_of_operation",
        "certificate_hold",
        "remove_from_crl",
        "unspecified",
    }
)


class CrlError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CrlDeltaMerger:
    """Merge base CRLs with delta CRLs and answer revocation queries."""

    def __init__(self, *, max_serials: int = 5000, clock_skew: int = 300,
                 max_deltas: int = 8, clock=None) -> None:
        self.max_serials = max_serials
        self.clock_skew = clock_skew
        self.max_deltas = max_deltas
        self._clock = clock
        self._lock = threading.RLock()

    def register_issuer(self, issuer: str, *, key_id: str,
                        indirect: bool = False) -> dict:
        raise NotImplementedError

    def list_issuers(self) -> list:
        raise NotImplementedError

    def submit_base(self, issuer: str, *, number: int, this_update: str,
                    next_update: str, entries, signature_key: str) -> dict:
        raise NotImplementedError

    def submit_delta(self, issuer: str, *, base_number: int, number: int,
                     this_update: str, next_update: str, entries,
                     signature_key: str) -> dict:
        raise NotImplementedError

    def is_revoked(self, issuer: str, serial: str, *, now) -> dict:
        raise NotImplementedError

    def effective_crl(self, issuer: str, *, now) -> dict:
        raise NotImplementedError

    def status(self, issuer: str, *, now) -> dict:
        raise NotImplementedError

    def expire(self, *, now) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
