"""Render graph pass ordering, aliasing and barrier planning."""

from __future__ import annotations

import threading

ALLOWED_STATES = (
    "undefined",
    "render_target",
    "depth_write",
    "shader_read",
    "copy_src",
    "copy_dst",
    "present",
)
ALLOWED_KINDS = ("texture", "buffer")


class RenderGraphError(ValueError):
    """Validation failure carrying a stable code and optional detail."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class RenderGraphBarrierPlanner:
    """Collect a frame graph, cull dead passes and emit barrier lists."""

    def __init__(self, *, max_barriers_per_pass: int = 8,
                 memory_budget: int = 4096) -> None:
        self.max_barriers_per_pass = max_barriers_per_pass
        self.memory_budget = memory_budget
        self._lock = threading.RLock()

    def register_resource(self, name: str, *, kind: str = "texture",
                          size: int = 1, imported: bool = False) -> dict:
        raise NotImplementedError

    def add_pass(self, pass_id: str, *, reads=(), writes=(),
                 side_effect: bool = False) -> dict:
        raise NotImplementedError

    def mark_output(self, name: str) -> None:
        raise NotImplementedError

    def remove_pass(self, pass_id: str) -> None:
        raise NotImplementedError

    def compile(self) -> dict:
        raise NotImplementedError

    def barriers_for(self, pass_id: str) -> list:
        raise NotImplementedError

    def lifetimes(self) -> dict:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
