"""Build cache scope isolation guard (implementation skeleton)."""

from __future__ import annotations

import threading


class CacheScopeError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BuildCacheGuard:
    """Keep CI build caches scoped to refs, bounded in size, and restart safe."""

    def __init__(self, *, max_entries: int = 1000, max_bytes: int = 10_000_000,
                 default_ttl: int = 3600, clock=None) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.default_ttl = default_ttl
        self._clock = clock
        self._lock = threading.RLock()

    def register_project(self, project: str, *, default_branch: str,
                         visibility: str = "private") -> dict:
        raise NotImplementedError

    def describe_project(self, project: str) -> dict:
        raise NotImplementedError

    def put(self, project: str, key: str, *, scope: str, size: int,
            digest: str, ttl: int | None = None, now=None) -> dict:
        raise NotImplementedError

    def get(self, project: str, key: str, *, scope: str, now=None) -> dict:
        raise NotImplementedError

    def restore_lookup(self, project: str, prefix: str, *, scope: str,
                       limit: int = 5, now=None) -> list:
        raise NotImplementedError

    def invalidate(self, project: str, *, key: str | None = None,
                   scope: str | None = None) -> int:
        raise NotImplementedError

    def purge_expired(self, *, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
