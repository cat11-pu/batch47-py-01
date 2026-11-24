"""Kernel module load allowlist, signature and dependency guard."""

from __future__ import annotations

import threading


class ModuleError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ModuleLoadGuard:
    """Admit, order and track kernel module loads with signature checks."""

    def __init__(self, *, allow_unsigned: bool = False,
                 max_loaded: int = 64) -> None:
        self.allow_unsigned = allow_unsigned
        self.max_loaded = max_loaded
        self._lock = threading.RLock()

    def trust_signer(self, signer: str, *, fingerprints,
                     priority: int = 0) -> dict:
        raise NotImplementedError

    def revoke_signer(self, signer: str) -> None:
        raise NotImplementedError

    def allow_module(self, name: str, *, signer: str | None = None,
                     fingerprint: str | None = None, depends=(),
                     taints=()) -> dict:
        raise NotImplementedError

    def deny_module(self, name: str, *, reason: str = "") -> dict:
        raise NotImplementedError

    def load(self, name: str, *, signature: dict | None = None) -> dict:
        raise NotImplementedError

    def unload(self, name: str, *, force: bool = False) -> dict:
        raise NotImplementedError

    def is_loaded(self, name: str) -> bool:
        raise NotImplementedError

    def loaded(self) -> list:
        raise NotImplementedError

    def taints(self) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
