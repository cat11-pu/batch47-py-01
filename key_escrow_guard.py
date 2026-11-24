"""Quorum escrow for long-lived signing keys."""
from __future__ import annotations
import threading

class EscrowError(ValueError):

    def __init__(self, code: str, detail: object=None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

class KeyEscrowGuard:
    """Split signing keys across custodians and enforce quorum to rebuild them."""

    def __init__(self, *, max_secrets: int=64, max_share_bytes: int=256) -> None:
        self.max_secrets = max_secrets
        self.max_share_bytes = max_share_bytes
        self._lock = threading.RLock()
        self._records: dict = {}
        self._stats = {'created': 0, 'rotated': 0, 'retired': 0, 'reconstructed': 0, 'failed': 0}
        self._audit: list = []

    def create_escrow(self, secret_id: str, *, key_material, custodians, threshold: int=0) -> dict:
        raise NotImplementedError()

    def reconstruct(self, secret_id: str, *, shares_by_custodian) -> bytes:
        raise NotImplementedError()

    def verify_share(self, secret_id: str, *, custodian: str, share) -> bool:
        raise NotImplementedError()

    def rotate_escrow(self, secret_id: str, *, shares_by_custodian, custodians, threshold: int=0) -> dict:
        raise NotImplementedError()

    def retire_escrow(self, secret_id: str) -> dict:
        raise NotImplementedError()

    def status(self, secret_id: str) -> dict:
        raise NotImplementedError()

    def is_retired(self, secret_id: str) -> bool:
        raise NotImplementedError()

    def list_escrows(self) -> list:
        raise NotImplementedError()

    def audit(self, *, limit: int | None=None) -> list:
        raise NotImplementedError()

    def snapshot(self) -> dict:
        raise NotImplementedError()

    def restore(self, state: dict) -> None:
        raise NotImplementedError()

    def stats(self) -> dict:
        raise NotImplementedError()

    def reset_stats(self) -> None:
        raise NotImplementedError()
