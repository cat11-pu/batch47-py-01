"""Mailbox attachment claiming and cash-on-delivery settlement."""

from __future__ import annotations

import threading


class MailError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class MailAttachmentService:
    """Track per-player mail slots, attachments and COD payments."""

    def __init__(self, *, slot_limit: int = 50, attachment_limit: int = 3,
                 batch_limit: int = 20, default_ttl: int = 86400,
                 clock=None) -> None:
        self.slot_limit = slot_limit
        self.attachment_limit = attachment_limit
        self.batch_limit = batch_limit
        self.default_ttl = default_ttl
        self._clock = clock
        self._lock = threading.RLock()

    def credit(self, player: str, amount: int) -> int:
        raise NotImplementedError

    def balance(self, player: str) -> int:
        raise NotImplementedError

    def deliver(self, mail_id: str, *, recipient: str, sender: str, subject: str,
                attachments, cod_fee: int = 0, expires_at=None, now=None) -> dict:
        raise NotImplementedError

    def claim(self, mail_id: str, *, player: str, now=None) -> dict:
        raise NotImplementedError

    def claim_all(self, *, player: str, now=None) -> dict:
        raise NotImplementedError

    def delete(self, mail_id: str, *, player: str, now=None) -> dict:
        raise NotImplementedError

    def status(self, mail_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def list_mailbox(self, *, player: str, now=None) -> list:
        raise NotImplementedError

    def purge_expired(self, now=None) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
