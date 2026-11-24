"""Shared HTTP cache guard: freshness windows, Vary binding, single-flight fills."""

from __future__ import annotations

import threading

SNAPSHOT_VERSION = 1

STAT_KEYS = ("hits", "misses", "stales", "stores", "evictions")

CACHEABLE_STATUS = frozenset({200, 203, 204, 300, 301, 404, 410})


class HttpCacheError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class HttpSharedCacheGuard:
    """Cache policy enforcer for a shared edge cache."""

    def __init__(self, *, max_entries: int = 1000, max_object_bytes: int = 1_000_000,
                 default_ttl: int = 60, stale_window: int = 30, clock=None) -> None:
        self.max_entries = max_entries
        self.max_object_bytes = max_object_bytes
        self.default_ttl = default_ttl
        self.stale_window = stale_window
        self._clock = clock
        self._lock = threading.RLock()

    def register_origin(self, origin: str, *, vary_headers=(),
                        allow_authorized: bool = False,
                        max_ttl: int = 3600) -> dict:
        raise NotImplementedError

    def store(self, request: dict, response: dict, *, now=None) -> dict:
        raise NotImplementedError

    def lookup(self, request: dict, *, now=None) -> dict:
        raise NotImplementedError

    def revalidate(self, request: dict, *, now=None, etag=None) -> dict:
        raise NotImplementedError

    def invalidate(self, origin: str, *, path_prefix: str = "/", now=None) -> int:
        raise NotImplementedError

    def purge(self, now=None) -> int:
        raise NotImplementedError

    def begin_fill(self, request: dict) -> dict:
        raise NotImplementedError

    def end_fill(self, request: dict, *, token: str) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
