"""Scoped build-cache isolation for shared CI runners."""
from __future__ import annotations
import threading

class CacheError(ValueError):

    def __init__(self, code: str, detail: object=None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

class CiCacheGuard:
    """Keep fork and pull-request builds from reading or poisoning trusted caches."""

    def __init__(self, *, max_entries: int=256, max_bytes: int=4 * 1024 * 1024, max_entry_bytes: int=1024 * 1024, default_ttl: int=3600, max_ttl: int=7 * 24 * 3600) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self.default_ttl = default_ttl
        self.max_ttl = max_ttl
        self._lock = threading.RLock()
        self._repositories: dict = {}
        self._scopes: dict = {}
        self._entries: dict = {}
        self._stats = {'put': 0, 'hit': 0, 'fallback': 0, 'evicted': 0, 'conflict': 0, 'rejected': 0}

    def register_repository(self, repo: str, *, protected_branches) -> dict:
        raise NotImplementedError()

    def open_scope(self, repo: str, *, ref: str, event: str, fork: bool=False) -> dict:
        raise NotImplementedError()

    def put(self, scope_id: str, *, key: str, data, digest: str | None=None, ttl: int | None=None, now=None) -> dict:
        raise NotImplementedError()

    def get(self, scope_id: str, *, key: str, now=None) -> dict | None:
        raise NotImplementedError()

    def resolve(self, scope_id: str, *, key: str, now=None) -> dict | None:
        raise NotImplementedError()

    def promote(self, scope_id: str, *, key: str, target_scope_id: str, now=None) -> dict:
        raise NotImplementedError()

    def close_scope(self, scope_id: str) -> int:
        raise NotImplementedError()

    def evict(self, now=None) -> int:
        raise NotImplementedError()

    def entries(self, scope_id: str | None=None) -> list:
        raise NotImplementedError()

    def snapshot(self) -> dict:
        raise NotImplementedError()

    def restore(self, state: dict) -> None:
        raise NotImplementedError()

    def stats(self) -> dict:
        raise NotImplementedError()

    def reset_stats(self) -> None:
        raise NotImplementedError()
