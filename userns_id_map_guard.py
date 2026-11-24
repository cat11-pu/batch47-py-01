"""User namespace id mapping guard (implementation skeleton)."""

from __future__ import annotations

import threading


class UsernsError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class UsernsIdMapGuard:
    """Track namespace trees, write-once id maps and subordinate id budgets."""

    def __init__(self, *, overflow_uid: int = 65534, overflow_gid: int = 65534,
                 max_extents: int = 5, max_depth: int = 8) -> None:
        self.overflow_uid = overflow_uid
        self.overflow_gid = overflow_gid
        self.max_extents = max_extents
        self.max_depth = max_depth
        self._lock = threading.RLock()

    def create_namespace(self, ns_id: str, *, parent: str | None = None,
                         owner: str | None = None) -> dict:
        raise NotImplementedError

    def write_uid_map(self, ns_id: str, lines) -> dict:
        raise NotImplementedError

    def write_gid_map(self, ns_id: str, lines) -> dict:
        raise NotImplementedError

    def setgroups(self, ns_id: str, value: str) -> str:
        raise NotImplementedError

    def map_uid(self, ns_id: str, uid: int, *, direction: str = "out") -> int:
        raise NotImplementedError

    def map_gid(self, ns_id: str, gid: int, *, direction: str = "out") -> int:
        raise NotImplementedError

    def mapping_of(self, ns_id: str) -> dict:
        raise NotImplementedError

    def compose(self, ns_id: str) -> dict:
        raise NotImplementedError

    def grant_subid(self, owner: str, *, start: int, count: int,
                    kind: str = "uid") -> dict:
        raise NotImplementedError

    def subid_remaining(self, owner: str, *, kind: str = "uid") -> int:
        raise NotImplementedError

    def destroy_namespace(self, ns_id: str) -> bool:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
