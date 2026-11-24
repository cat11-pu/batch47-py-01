"""SIP proxy transaction and dialog guard."""

from __future__ import annotations

import threading

SNAPSHOT_VERSION = 1

ALLOWED_METHODS = ("INVITE", "ACK", "BYE", "CANCEL", "OPTIONS", "INFO",
                   "REGISTER", "UPDATE")

BRANCH_COOKIE = "z9hG4bK"

STAT_KEYS = ("accepted", "retransmissions", "dialogs_opened",
             "dialogs_terminated")


class SipError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SipTransactionGuard:
    """Validate inbound requests, suppress retransmissions, track dialogs."""

    def __init__(self, *, max_forwards: int = 70, max_transactions: int = 256,
                 default_expires: int = 900, dialog_timeout: int = 1800,
                 transaction_ttl: int = 64, clock=None) -> None:
        self.max_forwards = max_forwards
        self.max_transactions = max_transactions
        self.default_expires = default_expires
        self.dialog_timeout = dialog_timeout
        self.transaction_ttl = transaction_ttl
        self._clock = clock
        self._lock = threading.RLock()

    def register_contact(self, aor: str, *, contact: str, expires: int | None = None,
                         now=None) -> dict:
        raise NotImplementedError

    def lookup_contact(self, aor: str, *, now=None):
        raise NotImplementedError

    def receive(self, message: dict, *, now=None) -> dict:
        raise NotImplementedError

    def respond(self, dialog_id: str, *, status: int, now=None) -> dict:
        raise NotImplementedError

    def cancel(self, dialog_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def ack(self, dialog_id: str, *, cseq: int, now=None) -> dict:
        raise NotImplementedError

    def terminate(self, dialog_id: str, *, now=None) -> dict:
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
