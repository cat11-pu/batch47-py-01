"""Tile scheduling for a ray-traced shadow denoiser."""

from __future__ import annotations

import threading


class TileError(ValueError):
    """Scheduling failure carrying a stable code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DenoiseTileScheduler:
    """Choose denoise tiles per frame, merge runs and track history validity."""

    def __init__(self, *, tile_size: int = 16, budget: int = 8,
                 min_samples: int = 4, variance_threshold: float = 0.25,
                 history_frames: int = 2) -> None:
        self.tile_size = tile_size
        self.budget = budget
        self.min_samples = min_samples
        self.variance_threshold = variance_threshold
        self.history_frames = history_frames
        self._lock = threading.RLock()

    def configure_grid(self, width: int, height: int) -> dict:
        raise NotImplementedError

    def mark_tile(self, tile_id: str, *, variance: float, samples: int,
                  history_valid: bool = True) -> dict:
        raise NotImplementedError

    def add_region(self, x0: int, y0: int, x1: int, y1: int, *,
                   variance: float, samples: int) -> list:
        raise NotImplementedError

    def invalidate_history(self, reason: str = "camera_cut") -> int:
        raise NotImplementedError

    def plan(self) -> dict:
        raise NotImplementedError

    def tile_state(self, tile_id: str) -> dict:
        raise NotImplementedError

    def pending(self) -> dict:
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
