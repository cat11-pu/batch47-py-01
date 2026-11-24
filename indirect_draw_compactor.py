"""Batch submission records into indirect draw argument lists."""

from __future__ import annotations

import threading


class DrawError(ValueError):
    """Batch planning failure carrying a stable code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class IndirectDrawCompactor:
    """Merge compatible draw items and emit deterministic indirect batches."""

    def __init__(self, *, max_instances_per_batch: int = 8,
                 max_batches: int = 16, max_indices: int = 1000000,
                 max_pending: int = 256) -> None:
        self.max_instances_per_batch = max_instances_per_batch
        self.max_batches = max_batches
        self.max_indices = max_indices
        self.max_pending = max_pending
        self._lock = threading.RLock()

    def register_pipeline(self, name: str, *, index_count: int,
                          instanced: bool = True) -> dict:
        raise NotImplementedError

    def submit(self, draw_id: str, *, pipeline: str, material: str,
               instance_count: int = 1, transparent: bool = False,
               depth: float = 0.0) -> dict:
        raise NotImplementedError

    def flush(self) -> dict:
        raise NotImplementedError

    def pending(self) -> list:
        raise NotImplementedError

    def cancel(self, draw_id: str) -> bool:
        raise NotImplementedError

    def clear(self) -> int:
        raise NotImplementedError

    def describe_batch(self, index: int) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
