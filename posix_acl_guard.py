"""POSIX ACL inheritance and access check guard (implementation skeleton)."""

from __future__ import annotations

import threading


class AclError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class PosixAclGuard:
    """Maintain a virtual file tree with access and default POSIX ACLs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def mkdir(self, path: str, *, mode: int = 0o755, uid: int = 0,
              gid: int = 0) -> dict:
        raise NotImplementedError

    def create_file(self, path: str, *, mode: int = 0o644, uid: int = 0,
                    gid: int = 0) -> dict:
        raise NotImplementedError

    def setfacl(self, path: str, entries, *, default: bool = False) -> dict:
        raise NotImplementedError

    def getfacl(self, path: str, *, default: bool = False) -> list:
        raise NotImplementedError

    def chmod(self, path: str, mode: int) -> dict:
        raise NotImplementedError

    def check_access(self, path: str, *, uid: int, gids=(), want: str = "r") -> bool:
        raise NotImplementedError

    def stat(self, path: str) -> dict:
        raise NotImplementedError

    def remove(self, path: str, *, recursive: bool = False) -> int:
        raise NotImplementedError

    def list_tree(self) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
