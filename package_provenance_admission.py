"""Package provenance and dependency-confusion admission guard (implementation skeleton)."""

from __future__ import annotations

import threading


class ProvenanceError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ProvenanceAdmission:
    """Decide which registry may supply a pinned artifact and keep an admission ledger."""

    def __init__(self, *, max_publications: int = 10000,
                 max_ledger: int = 200) -> None:
        self.max_publications = max_publications
        self.max_ledger = max_ledger
        self._lock = threading.RLock()

    def register_registry(self, name: str, *, ecosystem: str, priority: int,
                          trusted: bool = True) -> dict:
        raise NotImplementedError

    def revoke_registry(self, name: str) -> None:
        raise NotImplementedError

    def claim_scope(self, ecosystem: str, scope: str, *, registry: str) -> dict:
        raise NotImplementedError

    def register_project(self, project: str, *, policy: str = "pinned") -> dict:
        raise NotImplementedError

    def publish(self, ecosystem: str, name: str, version: str, *, registry: str,
                digest: str, yanked: bool = False) -> dict:
        raise NotImplementedError

    def yank(self, ecosystem: str, name: str, version: str, *, registry: str) -> dict:
        raise NotImplementedError

    def pin(self, project: str, ecosystem: str, name: str, *, version: str,
            digest: str) -> dict:
        raise NotImplementedError

    def resolve(self, project: str, ecosystem: str, name: str, version: str, *,
                now=None) -> dict:
        raise NotImplementedError

    def admit(self, project: str, ecosystem: str, name: str, version: str, *,
              now=None) -> dict:
        raise NotImplementedError

    def ledger(self, project: str, *, limit: int = 50) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
