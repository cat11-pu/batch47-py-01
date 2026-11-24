"""Depth-of-field circle-of-confusion, tile layers and bokeh kernel guard."""

from __future__ import annotations

import threading


class DofError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DepthOfField:
    """Focus animation, CoC evaluation and tile-based bokeh budgeting."""

    def __init__(self, *, width: int, height: int, tile_size: int = 16,
                 focus_distance: float = 10.0, aperture: float = 2.0,
                 coc_scale: float = 10.0, max_coc_px: float = 24.0,
                 focus_speed: float | None = None, sample_count: int = 8,
                 budget_units: int = 200000) -> None:
        self.width = width
        self.height = height
        self.tile_size = tile_size
        self.focus_distance = focus_distance
        self.aperture = aperture
        self.coc_scale = coc_scale
        self.max_coc_px = max_coc_px
        self.focus_speed = focus_speed
        self.sample_count = sample_count
        self.budget_units = budget_units
        self._lock = threading.RLock()

    def set_focus(self, distance: float, *, speed: float | None = None) -> dict:
        raise NotImplementedError

    def update_focus(self, dt: float) -> float:
        raise NotImplementedError

    def compute_coc(self, depth: float) -> float:
        raise NotImplementedError

    def linearize_depth(self, raw: float, *, near: float = 0.1,
                        far: float = 500.0) -> float:
        raise NotImplementedError

    def set_aperture(self, aperture: float) -> None:
        raise NotImplementedError

    def set_tile_depths(self, depths) -> dict:
        raise NotImplementedError

    def plan_tiles(self) -> list:
        raise NotImplementedError

    def build_bokeh_kernel(self, coc_px: float, *, samples: int | None = None) -> list:
        raise NotImplementedError

    def plan(self, *, budget_units: int | None = None) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
