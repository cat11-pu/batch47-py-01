"""Bindless GPU resource handle table with generation-checked slots."""

from __future__ import annotations

import threading


INDEX_BITS = 24
INDEX_MASK = (1 << INDEX_BITS) - 1
MAX_GENERATION = (1 << (32 - INDEX_BITS)) - 1
KINDS = ("texture", "buffer", "sampler")


class HandleError(ValueError):
    """Raised when a bindless handle or table state is invalid."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class HandleTable:
    """Allocate generation-tagged handles and reclaim them after N frames."""

    def __init__(self, *, capacity: int = 64, frames_in_flight: int = 2) -> None:
        self.capacity = capacity
        self.frames_in_flight = frames_in_flight
        self._lock = threading.RLock()

    def insert(self, resource: dict, *, kind: str = "texture", tag: str = "") -> int:
        raise NotImplementedError

    def resolve(self, handle: int) -> dict:
        raise NotImplementedError

    def contains(self, handle: object) -> bool:
        raise NotImplementedError

    def generation_of(self, handle: object) -> int:
        raise NotImplementedError

    def retire(self, handle: int) -> dict:
        raise NotImplementedError

    def free_now(self, handle: int) -> dict:
        raise NotImplementedError

    def begin_frame(self, frame_id: int) -> dict:
        raise NotImplementedError

    def usage(self) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
