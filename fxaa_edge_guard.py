"""Post-process luma edge detection, blend factor and resolve guard."""

from __future__ import annotations

import threading


class FxaaError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class FxaaEdge:
    """Classify luma edges, compute blend factors and resolve a luma grid."""

    def __init__(self, *, width: int, height: int, threshold: float = 0.1,
                 relative_threshold: float = 0.15,
                 subpixel_quality: float = 0.75, max_span: int = 8,
                 budget_units: int = 120000) -> None:
        self.width = width
        self.height = height
        self.threshold = threshold
        self.relative_threshold = relative_threshold
        self.subpixel_quality = subpixel_quality
        self.max_span = max_span
        self.budget_units = budget_units
        self._lock = threading.RLock()

    def luma(self, rgb) -> float:
        raise NotImplementedError

    def edge_contrast(self, grid, x: int, y: int) -> float:
        raise NotImplementedError

    def edge_class(self, grid, x: int, y: int) -> str:
        raise NotImplementedError

    def blend_factor(self, grid, x: int, y: int) -> float:
        raise NotImplementedError

    def detect_edges(self, grid) -> dict:
        raise NotImplementedError

    def resolve(self, grid) -> list:
        raise NotImplementedError

    def plan(self, grid, *, budget_units: int | None = None) -> dict:
        raise NotImplementedError

    def set_threshold(self, threshold: float, *, relative: float | None = None) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
