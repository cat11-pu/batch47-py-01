"""Render frame graph compiler with pass culling and barrier planning."""

from __future__ import annotations

import threading


class FrameGraphError(ValueError):
    """Raised when a frame graph declaration cannot be compiled or run."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class FrameGraph:
    """Declare logical resources and passes, compile them, then execute."""

    def __init__(self, *, max_passes: int = 64) -> None:
        self.max_passes = max_passes
        self._lock = threading.RLock()

    def register_resource(self, name: str, *, kind: str = "buffer",
                          size: int = 0) -> dict:
        raise NotImplementedError

    def add_pass(self, name: str, *, reads=(), writes=(), execute=None) -> dict:
        raise NotImplementedError

    def remove_pass(self, name: str) -> None:
        raise NotImplementedError

    def mark_output(self, *resource_names) -> list[str]:
        raise NotImplementedError

    def is_compiled(self) -> bool:
        raise NotImplementedError

    def compile(self) -> dict:
        raise NotImplementedError

    def execute(self, context=None) -> list[dict]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
