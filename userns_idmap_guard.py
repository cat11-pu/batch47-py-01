"""User namespace UID/GID range allocation and reverse lookup guard."""

from __future__ import annotations

import threading

U32_MAX = 4294967294


class IdmapError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class UsernsIdmapGuard:
    """Allocate host ID ranges for user namespaces and map container IDs."""

    def __init__(self, *, host_id_min: int = 100000, host_id_max: int = 999999,
                 max_mappings: int = 64, max_count: int = 65536) -> None:
        self.host_id_min = host_id_min
        self.host_id_max = host_id_max
        self.max_mappings = max_mappings
        self.max_count = max_count
        self._lock = threading.RLock()

    def reserve(self, namespace: str, *, container_start: int,
                container_count: int, host_start: int | None = None,
                owner: str = "") -> dict:
        raise NotImplementedError

    def release(self, namespace: str) -> dict:
        raise NotImplementedError

    def assign(self, namespace: str, *, uid: int) -> int:
        raise NotImplementedError

    def lookup(self, host_id: int) -> dict | None:
        raise NotImplementedError

    def mappings(self) -> list:
        raise NotImplementedError

    def occupancy(self) -> dict:
        raise NotImplementedError

    def is_reserved(self, namespace: str) -> bool:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
