"""Daily activity points with per-source caps and milestone chest claims."""

from __future__ import annotations

import threading


class ActivityError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ActivityChestTrack:
    """Accumulate UTC-day activity points and gate milestone chest claims."""

    def __init__(self, *, milestones=None, source_caps=None,
                 max_points_per_day: int = 100, clock=None) -> None:
        self._milestones = milestones
        self._source_caps = source_caps
        self.max_points_per_day = max_points_per_day
        self._clock = clock
        self._lock = threading.RLock()

    def register(self, player: str, *, now=None) -> dict:
        raise NotImplementedError

    def add_points(self, player: str, source: str, points: int, *,
                   now=None) -> dict:
        raise NotImplementedError

    def progress(self, player: str, *, now=None) -> dict:
        raise NotImplementedError

    def claim(self, player: str, threshold: int, *, now=None,
              request_id: str | None = None) -> dict:
        raise NotImplementedError

    def rollover(self, now=None) -> int:
        raise NotImplementedError

    def unclaimed(self, player: str, *, now=None) -> list:
        raise NotImplementedError

    def day_of(self, now=None) -> str:
        raise NotImplementedError

    def source_breakdown(self, player: str, *, now=None) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
