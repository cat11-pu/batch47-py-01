"""POSIX ACL inheritance, mask computation and effective permission guard."""

from __future__ import annotations

import threading


class AclError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AclInheritGuard:
    """Keep access/default ACLs for a small tree and resolve permissions."""

    def __init__(self, *, umask: int = 0o022) -> None:
        self.umask = umask
        self._lock = threading.RLock()

    def ensure_path(self, path: str, *, kind: str = "dir", owner: str,
                    group: str, mode: int = 0o755) -> dict:
        raise NotImplementedError

    def set_access(self, path: str, *, owner: str | None = None,
                   group: str | None = None, entries) -> dict:
        raise NotImplementedError

    def set_default(self, path: str, entries) -> dict:
        raise NotImplementedError

    def get_access(self, path: str) -> dict:
        raise NotImplementedError

    def get_default(self, path: str) -> list | None:
        raise NotImplementedError

    def create_child(self, parent: str, name: str, *, kind: str = "file",
                     mode: int = 0o666, owner: str = "",
                     group: str = "") -> dict:
        raise NotImplementedError

    def effective(self, path: str, *, user: str, groups=()) -> str:
        raise NotImplementedError

    def to_text(self, path: str) -> str:
        raise NotImplementedError

    def from_text(self, path: str, text: str) -> dict:
        raise NotImplementedError

    def remove(self, path: str) -> int:
        raise NotImplementedError

    def list_paths(self) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
