"""Frame-scoped command buffer pool with recording limits and leak checks."""

from __future__ import annotations

import threading


STATES = ("idle", "recording", "submitted")


class CommandPoolError(ValueError):
    """Raised when a command pool operation is invalid."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CommandPool:
    """Rent, record, submit and recycle a fixed set of command buffers."""

    def __init__(self, *, size: int = 16, frames_in_flight: int = 2,
                 max_recorded_commands: int = 1024) -> None:
        self.size = size
        self.frames_in_flight = frames_in_flight
        self.max_recorded_commands = max_recorded_commands
        self._lock = threading.RLock()

    def begin_frame(self, frame_id: int) -> dict:
        raise NotImplementedError

    def acquire(self, *, label: str = "") -> dict:
        raise NotImplementedError

    def record(self, command_id: int, command: dict) -> int:
        raise NotImplementedError

    def submit(self, command_id: int) -> dict:
        raise NotImplementedError

    def reclaim(self, frame_id: int) -> int:
        raise NotImplementedError

    def state(self, command_id: int) -> dict:
        raise NotImplementedError

    def usage(self) -> dict:
        raise NotImplementedError

    def leak_check(self, current_frame: int | None = None) -> list[int]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
