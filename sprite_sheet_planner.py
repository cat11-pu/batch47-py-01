"""Sprite sheet planning for video preview thumbnails."""

from __future__ import annotations

import threading


class SpriteError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SpriteSheetPlanner:
    """Plan tile grids and pages for sampled video preview frames."""

    def __init__(self, *, tile_width: int = 160, tile_height: int = 90,
                 columns: int = 10, rows: int = 10, max_pages: int = 8,
                 max_sources: int = 16, snap_ms: int = 1000) -> None:
        self.tile_width = tile_width
        self.tile_height = tile_height
        self.columns = columns
        self.rows = rows
        self.max_pages = max_pages
        self.max_sources = max_sources
        self.snap_ms = snap_ms
        self._lock = threading.RLock()

    def add_source(self, source_id: str, *, duration_ms: int,
                   interval_ms: int, keyframes=()) -> dict:
        raise NotImplementedError

    def remove_source(self, source_id: str) -> dict:
        raise NotImplementedError

    def set_keyframes(self, source_id: str, keyframes) -> dict:
        raise NotImplementedError

    def plan(self, source_id: str, *, start_ms: int = 0,
             end_ms: int | None = None) -> dict:
        raise NotImplementedError

    def commit(self, source_id: str, *, start_ms: int = 0,
               end_ms: int | None = None) -> dict:
        raise NotImplementedError

    def active_plan(self, source_id: str) -> dict | None:
        raise NotImplementedError

    def invalidate(self, source_id: str) -> dict:
        raise NotImplementedError

    def capacity(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
