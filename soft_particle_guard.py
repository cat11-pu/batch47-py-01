"""Soft particle billboard depth fade, layer sorting and overdraw budget guard."""

from __future__ import annotations

import threading


class ParticleError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SoftParticleBatch:
    """Track billboard quads, depth fade and per-frame overdraw planning."""

    def __init__(self, *, width: int, height: int, fade_distance: float = 1.5,
                 max_quads: int = 4096, blend_layers: int = 4,
                 budget_units: int = 2000000) -> None:
        self.width = width
        self.height = height
        self.fade_distance = fade_distance
        self.max_quads = max_quads
        self.blend_layers = blend_layers
        self.budget_units = budget_units
        self._lock = threading.RLock()

    def emit(self, particle_id: str, *, position, size: float, depth: float,
             scene_depth: float, layer: int = 0) -> dict:
        raise NotImplementedError

    def update(self, particle_id: str, *, depth: float | None = None,
               scene_depth: float | None = None, size: float | None = None,
               layer: int | None = None) -> dict:
        raise NotImplementedError

    def remove(self, particle_id: str) -> None:
        raise NotImplementedError

    def get_particle(self, particle_id: str) -> dict:
        raise NotImplementedError

    def compute_fade(self, depth: float, scene_depth: float) -> float:
        raise NotImplementedError

    def set_fade_distance(self, distance: float) -> None:
        raise NotImplementedError

    def sort_quads(self) -> list:
        raise NotImplementedError

    def plan(self, *, budget_units: int | None = None) -> dict:
        raise NotImplementedError

    def capacity(self) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
