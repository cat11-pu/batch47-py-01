"""Persistent staging upload ring for per-frame GPU resource data."""

from __future__ import annotations

import threading


MAX_ALIGNMENT = 4096


class UploadError(ValueError):
    """Raised when an upload operation violates a ring invariant."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class UploadRing:
    """Bounded staging ring with frame-based retirement of allocations."""

    def __init__(self, *, capacity: int = 1 << 20, alignment: int = 256,
                 frames_in_flight: int = 2) -> None:
        self.capacity = capacity
        self.alignment = alignment
        self.frames_in_flight = frames_in_flight
        self._lock = threading.RLock()

    def begin_frame(self, frame_id: int) -> dict:
        raise NotImplementedError

    def allocate(self, size: int, *, alignment: int | None = None,
                 tag: str = "") -> dict:
        raise NotImplementedError

    def release(self, handle: int) -> dict:
        raise NotImplementedError

    def retire_frame(self, frame_id: int) -> int:
        raise NotImplementedError

    def write(self, handle: int, data: bytes, *, offset: int = 0) -> int:
        raise NotImplementedError

    def read(self, handle: int, *, offset: int = 0, length: int | None = None) -> bytes:
        raise NotImplementedError

    def resolve(self, handle: int) -> dict:
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
