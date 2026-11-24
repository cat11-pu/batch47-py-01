"""Stencil shadow volume registration, extrusion and frame budgeting guard."""

from __future__ import annotations

import threading


class ShadowVolumeError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ShadowVolumeStencil:
    """Manage lights, occluders, extruded stencil volumes and per-frame plans."""

    def __init__(self, *, stencil_bits: int = 8, max_lights: int = 4,
                 edge_budget: int = 50000) -> None:
        self.stencil_bits = stencil_bits
        self.max_lights = max_lights
        self.edge_budget = edge_budget
        self._lock = threading.RLock()

    def add_light(self, light_id: str, *, position, radius: float,
                  priority: int = 0) -> dict:
        raise NotImplementedError

    def remove_light(self, light_id: str) -> None:
        raise NotImplementedError

    def add_occluder(self, occluder_id: str, *, vertices, silhouette,
                     two_sided: bool = True) -> dict:
        raise NotImplementedError

    def remove_occluder(self, occluder_id: str) -> None:
        raise NotImplementedError

    def build_volume(self, occluder_id: str, light_id: str) -> dict:
        raise NotImplementedError

    def select_lights(self) -> list:
        raise NotImplementedError

    def stencil_ops(self, occluder_id: str, light_id: str) -> dict:
        raise NotImplementedError

    def set_zfail(self, enabled: bool) -> None:
        raise NotImplementedError

    def plan_frame(self, *, budget_edges: int | None = None) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
