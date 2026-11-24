"""CAS single sign-on ticket guard."""

from __future__ import annotations

import threading


class CasError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CasTicketGuard:
    """Issue and validate CAS ticket-granting and service tickets."""

    def __init__(self, *, ticket_ttl: int = 120, tgt_ttl: int = 7200,
                 max_tickets: int = 1000, max_proxy_depth: int = 2,
                 clock=None) -> None:
        self.ticket_ttl = ticket_ttl
        self.tgt_ttl = tgt_ttl
        self.max_tickets = max_tickets
        self.max_proxy_depth = max_proxy_depth
        self._clock = clock
        self._lock = threading.RLock()

    def register_service(self, service_id: str, *, url_pattern: str,
                         allow_proxy: bool = False) -> dict:
        raise NotImplementedError

    def issue_tgt(self, *, principal: str, attributes=None,
                  renewable: bool = True, now=None) -> dict:
        raise NotImplementedError

    def issue_service_ticket(self, tgt: str, *, service: str,
                             renew: bool = False, now=None) -> dict:
        raise NotImplementedError

    def validate_service_ticket(self, ticket: str, *, service: str,
                                now=None) -> dict:
        raise NotImplementedError

    def issue_proxy_granting_ticket(self, tgt: str, *, service: str,
                                    now=None) -> dict:
        raise NotImplementedError

    def issue_proxy_ticket(self, grant: str, *, target_service: str,
                           now=None) -> dict:
        raise NotImplementedError

    def validate_proxy_ticket(self, ticket: str, *, target_service: str,
                              now=None) -> dict:
        raise NotImplementedError

    def destroy_tgt(self, tgt: str) -> bool:
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
