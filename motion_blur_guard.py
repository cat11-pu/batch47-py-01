"""Camera motion blur velocity, tile radius and reconstruction kernel guard."""

from __future__ import annotations

import threading


class MotionBlurError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class MotionBlur:
    """Track per-frame screen velocities and build a tile-limited blur kernel."""

    def __init__(self, *, width: int, height: int, tile_size: int = 16,
                 max_blur_px: float = 32.0, sample_count: int = 8,
                 cut_threshold: float = 96.0, teleport_scale: float = 4.0,
                 budget_units: int = 200000) -> None:
        self.width = width
        self.height = height
        self.tile_size = tile_size
        self.max_blur_px = max_blur_px
        self.sample_count = sample_count
        self.cut_threshold = cut_threshold
        self.teleport_scale = teleport_scale
        self.budget_units = budget_units
        self._lock = threading.RLock()

    def begin_frame(self, *, frame_id: int, camera_delta=(0.0, 0.0),
                    reset: bool = False) -> dict:
        raise NotImplementedError

    def submit_object(self, object_id: str, *, screen_delta, depth: float,
                      screen_rect, priority: int = 0) -> dict:
        raise NotImplementedError

    def clamp_vector(self, vector) -> tuple:
        raise NotImplementedError

    def cut_detected(self, camera_delta) -> bool:
        raise NotImplementedError

    def set_strength(self, strength: float) -> None:
        raise NotImplementedError

    def classify_tiles(self) -> dict:
        raise NotImplementedError

    def build_kernel(self, tile_x: int, tile_y: int) -> list:
        raise NotImplementedError

    def encode(self, *, budget_units: int | None = None) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
