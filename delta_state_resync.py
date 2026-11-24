"""Authoritative battle state journal and reconnect delta resynchronisation."""

from __future__ import annotations


class ResyncError(ValueError):
    """Raised when a state or resync operation cannot be completed."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DeltaStateResync:
    """Keep the authoritative key/value state, its journal and client catch-up."""

    def __init__(self, *, max_journal: int = 256, max_keys: int = 10000,
                 max_patch_ops: int = 64) -> None:
        self.max_journal = max_journal
        self.max_keys = max_keys
        self.max_patch_ops = max_patch_ops

    def set_initial(self, state: dict) -> dict:
        raise NotImplementedError

    def commit(self, changes: dict, *, now_ms=None) -> dict:
        raise NotImplementedError

    def remove_keys(self, keys, *, now_ms=None) -> dict:
        raise NotImplementedError

    def resync(self, *, client_seq: int, max_ops=None) -> dict:
        raise NotImplementedError

    def get(self, key: str):
        raise NotImplementedError

    def state(self) -> dict:
        raise NotImplementedError

    def checksum(self) -> str:
        raise NotImplementedError

    def journal_span(self) -> dict:
        raise NotImplementedError

    def compact(self, keep_last: int) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
