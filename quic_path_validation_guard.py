"""QUIC address validation and anti-amplification guard."""

from __future__ import annotations

import threading

SNAPSHOT_VERSION = 1

STAT_KEYS = ("connections_opened", "connections_closed", "tokens_issued",
             "tokens_rejected", "migrations", "zero_rtt_admitted")


class QuicError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class QuicPathValidationGuard:
    """Retry tokens, path validation and 3x amplification budget."""

    def __init__(self, *, secret: str, token_ttl: int = 600,
                 amplification_factor: int = 3, max_connections: int = 1000,
                 max_tickets: int = 1000, clock=None) -> None:
        self.secret = secret
        self.token_ttl = token_ttl
        self.amplification_factor = amplification_factor
        self.max_connections = max_connections
        self.max_tickets = max_tickets
        self._clock = clock
        self._lock = threading.RLock()

    def issue_token(self, *, peer: dict, original_dcid: str, now=None) -> dict:
        raise NotImplementedError

    def verify_token(self, token: str, *, peer: dict, now=None) -> dict:
        raise NotImplementedError

    def register_ticket(self, ticket_id: str, *, expires_at, now=None) -> dict:
        raise NotImplementedError

    def admit_0rtt(self, conn_id: str, *, ticket_id: str, now=None) -> dict:
        raise NotImplementedError

    def open_connection(self, conn_id: str, *, peer: dict, now=None) -> dict:
        raise NotImplementedError

    def record_received(self, conn_id: str, size: int, *, now=None) -> dict:
        raise NotImplementedError

    def check_send(self, conn_id: str, size: int, *, now=None) -> dict:
        raise NotImplementedError

    def record_sent(self, conn_id: str, size: int, *, now=None) -> dict:
        raise NotImplementedError

    def validate_path(self, conn_id: str, *, peer: dict, token: str | None = None,
                      now=None) -> dict:
        raise NotImplementedError

    def migrate(self, conn_id: str, *, peer: dict, now=None) -> dict:
        raise NotImplementedError

    def close_connection(self, conn_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def purge(self, now=None) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
