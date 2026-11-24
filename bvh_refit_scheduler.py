"""Top-level acceleration structure refit and rebuild scheduling."""

from __future__ import annotations

import threading


class RefitError(ValueError):
    """Scheduling failure carrying a stable code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BvhRefitScheduler:
    """Track scene instances and decide when to refit or rebuild the TLAS."""

    def __init__(self, *, rebuild_ratio: float = 0.5, frame_budget: int = 100,
                 max_instances: int = 512, refit_cost: int = 1,
                 rebuild_cost: int = 4) -> None:
        self.rebuild_ratio = rebuild_ratio
        self.frame_budget = frame_budget
        self.max_instances = max_instances
        self.refit_cost = refit_cost
        self.rebuild_cost = rebuild_cost
        self._lock = threading.RLock()

    def add_instance(self, instance_id: str, *, mesh_id: str, transform,
                     coverage: float = 0.0) -> dict:
        raise NotImplementedError

    def update_instance(self, instance_id: str, *, transform,
                        coverage: float | None = None) -> dict:
        raise NotImplementedError

    def set_mesh(self, instance_id: str, *, mesh_id: str) -> dict:
        raise NotImplementedError

    def remove_instance(self, instance_id: str) -> None:
        raise NotImplementedError

    def is_dirty(self, instance_id: str) -> bool:
        raise NotImplementedError

    def instance_state(self, instance_id: str) -> dict:
        raise NotImplementedError

    def pending(self) -> dict:
        raise NotImplementedError

    def compact(self) -> int:
        raise NotImplementedError

    def begin_frame(self, frame: int, *, budget: int | None = None) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
