"""Transient render target cache with byte budget and deferred reuse."""

from __future__ import annotations

import threading


FORMAT_BYTES = {
    "r8": 1,
    "rg8": 2,
    "rgba8": 4,
    "rgba16f": 8,
    "r32f": 4,
    "depth32f": 4,
}


class RenderTargetError(ValueError):
    """Raised when a render target request is invalid."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class RenderTargetPool:
    """Cache matching render targets and evict them under a byte budget."""

    def __init__(self, *, budget_bytes: int = 1 << 24, frames_in_flight: int = 2,
                 alignment: int = 4) -> None:
        self.budget_bytes = budget_bytes
        self.frames_in_flight = frames_in_flight
        self.alignment = alignment
        self._lock = threading.RLock()

    def acquire(self, width: int, height: int, *, format: str = "rgba8",
                samples: int = 1, tag: str = "") -> dict:
        raise NotImplementedError

    def release(self, key: str) -> dict:
        raise NotImplementedError

    def begin_frame(self, frame_id: int) -> dict:
        raise NotImplementedError

    def purge(self) -> dict:
        raise NotImplementedError

    def state(self, key: str) -> dict:
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
