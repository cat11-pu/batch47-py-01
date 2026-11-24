"""Append-only transparency log acceptance for signed artifacts."""
from __future__ import annotations
import threading

class InclusionError(ValueError):

    def __init__(self, code: str, detail: object=None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

def leaf_hash(data: bytes) -> str:
    raise NotImplementedError()

def node_hash(left: str, right: str) -> str:
    raise NotImplementedError()

def checkpoint_message(log_id: str, tree_size: int, root_hash: str) -> str:
    raise NotImplementedError()

class InclusionGuard:
    """Accept signed tree heads and verify artifact leaves against them."""

    def __init__(self, *, max_entries: int=512, batch_limit: int=50, max_audit_path: int=32) -> None:
        self.max_entries = max_entries
        self.batch_limit = batch_limit
        self.max_audit_path = max_audit_path
        self._lock = threading.RLock()
        self._logs: dict = {}
        self._entries: dict = {}
        self._leaf_index: dict = {}
        self._last_entry: dict = {}
        self._stats = {'accepted': 0, 'replayed': 0, 'rejected': 0, 'checkpoints': 0}

    def register_log(self, log_id: str, *, key_hex: str, max_tree_size: int=1 << 32) -> dict:
        raise NotImplementedError()

    def revoke_log(self, log_id: str) -> None:
        raise NotImplementedError()

    def accept_checkpoint(self, log_id: str, *, tree_size: int, root_hash: str, signature: str, anchor=None) -> dict:
        raise NotImplementedError()

    def verify_inclusion(self, log_id: str, *, leaf_hash: str, leaf_index: int, tree_size: int, audit_path, root_hash: str | None=None) -> dict:
        raise NotImplementedError()

    def accept_entry(self, log_id: str, *, entry_id: str, leaf_hash: str, leaf_index: int, tree_size: int, audit_path) -> dict:
        raise NotImplementedError()

    def accept_batch(self, log_id: str, entries) -> list:
        raise NotImplementedError()

    def is_known(self, entry_id: str) -> bool:
        raise NotImplementedError()

    def checkpoint(self, log_id: str) -> dict | None:
        raise NotImplementedError()

    def entries(self, log_id: str | None=None) -> list:
        raise NotImplementedError()

    def snapshot(self) -> dict:
        raise NotImplementedError()

    def restore(self, state: dict) -> None:
        raise NotImplementedError()

    def stats(self) -> dict:
        raise NotImplementedError()

    def reset_stats(self) -> None:
        raise NotImplementedError()
