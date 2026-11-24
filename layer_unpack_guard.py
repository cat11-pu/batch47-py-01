"""Container image layer unpack guard (implementation skeleton)."""

from __future__ import annotations

import threading


class LayerError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class LayerUnpackGuard:
    """Validate image layer entries and apply them atomically."""

    def __init__(self, *, root: str = "/layers", max_entries: int = 10000,
                 max_bytes: int = 1 << 30, max_path_depth: int = 32) -> None:
        self.root = root
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_path_depth = max_path_depth
        self._lock = threading.RLock()

    def begin_layer(self, layer_id: str, *, digest: str | None = None) -> dict:
        raise NotImplementedError

    def add_entry(self, layer_id: str, entry: dict) -> dict:
        raise NotImplementedError

    def commit_layer(self, layer_id: str) -> dict:
        raise NotImplementedError

    def abort_layer(self, layer_id: str) -> bool:
        raise NotImplementedError

    def resolve_path(self, raw: str) -> str:
        raise NotImplementedError

    def list_files(self) -> list[str]:
        raise NotImplementedError

    def lookup(self, path: str) -> dict | None:
        raise NotImplementedError

    def layer_state(self, layer_id: str) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
