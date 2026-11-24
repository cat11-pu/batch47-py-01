"""Cash-on-delivery mailbox with custody, quotas and expiry return."""

from __future__ import annotations

import threading


class MailError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CodMailbox:
    """Hold attachments in custody, charge the fee on claim, return on expiry."""

    def __init__(self, *, quota: int = 50, ttl: int = 86400,
                 max_attachments: int = 5, batch_limit: int = 20,
                 clock=None) -> None:
        self.quota = quota
        self.ttl = ttl
        self.max_attachments = max_attachments
        self.batch_limit = batch_limit
        self._clock = clock
        self._lock = threading.RLock()

    def grant_currency(self, player: str, amount: int) -> int:
        raise NotImplementedError

    def balance(self, player: str) -> int:
        raise NotImplementedError

    def grant_items(self, player: str, item_id: str, count: int) -> dict:
        raise NotImplementedError

    def inventory(self, player: str) -> dict:
        raise NotImplementedError

    def send(self, sender: str, recipient: str, *, attachments=None,
             currency: int = 0, cod_fee: int = 0, mail_id: str | None = None,
             now=None) -> dict:
        raise NotImplementedError

    def mailbox(self, player: str, *, now=None) -> list:
        raise NotImplementedError

    def claim(self, mail_id: str, *, player: str, now=None,
              request_id: str | None = None) -> dict:
        raise NotImplementedError

    def claim_batch(self, player: str, mail_ids, *, now=None) -> list:
        raise NotImplementedError

    def expire(self, now=None) -> int:
        raise NotImplementedError

    def returned(self, player: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
