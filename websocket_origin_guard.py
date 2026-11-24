"""WebSocket Origin validation and handshake ticket guard for the gateway."""

from __future__ import annotations

import threading

SNAPSHOT_VERSION = 1

STAT_KEYS = ("issued", "accepted", "rejected", "closed", "expired")


class OriginError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class OriginGuard:
    """Origin policy, handshake ticket and session quota ledger."""

    def __init__(self, *, max_connections: int = 500, ticket_ttl: int = 60,
                 max_per_origin: int = 50, clock=None) -> None:
        self.max_connections = max_connections
        self.ticket_ttl = ticket_ttl
        self.max_per_origin = max_per_origin
        self._clock = clock
        self._lock = threading.RLock()

    def register_origin(self, origin: str, *, allow_subdomains: bool = False,
                        max_per_origin: int | None = None) -> dict:
        raise NotImplementedError

    def revoke_origin(self, origin: str) -> None:
        raise NotImplementedError

    def normalize_origin(self, raw: str) -> str:
        raise NotImplementedError

    def issue_ticket(self, origin: str, *, subject: str,
                     ttl: int | None = None, now=None) -> dict:
        raise NotImplementedError

    def handshake(self, headers, *, peer: str, now=None) -> dict:
        raise NotImplementedError

    def close(self, ticket: str) -> dict:
        raise NotImplementedError

    def ticket_state(self, ticket: str) -> dict:
        raise NotImplementedError

    def origin_status(self, origin: str) -> dict:
        raise NotImplementedError

    def expire(self, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
