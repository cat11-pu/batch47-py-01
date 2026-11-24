"""Mix parameter automation lanes with interpolation and snapshots."""

from __future__ import annotations

import threading


class MixError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def beats_to_ms(beats, *, bpm) -> int:
    raise NotImplementedError


def ms_to_beats(ms, *, bpm) -> float:
    raise NotImplementedError


class AutomationLane:
    """Store automation breakpoints per lane and evaluate them over time."""

    def __init__(self, *, curve: str = "linear", max_points: int = 1000,
                 min_value: float = -60.0, max_value: float = 12.0) -> None:
        self.curve = curve
        self.max_points = max_points
        self.min_value = min_value
        self.max_value = max_value
        self._lock = threading.RLock()

    def create_lane(self, lane_id: str, *, default_value: float = 1.0,
                    curve: str | None = None) -> dict:
        raise NotImplementedError

    def drop_lane(self, lane_id: str) -> dict:
        raise NotImplementedError

    def apply(self, lane_id: str, points, *, mode: str = "merge") -> dict:
        raise NotImplementedError

    def remove_point(self, lane_id: str, time_ms: int) -> dict:
        raise NotImplementedError

    def value_at(self, lane_id: str, time_ms: int) -> float:
        raise NotImplementedError

    def sample(self, lane_id: str, start_ms: int, end_ms: int,
               step_ms: int) -> list:
        raise NotImplementedError

    def clear_range(self, lane_id: str, start_ms: int, end_ms: int) -> dict:
        raise NotImplementedError

    def lane_info(self, lane_id: str) -> dict:
        raise NotImplementedError

    def list_lanes(self) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
