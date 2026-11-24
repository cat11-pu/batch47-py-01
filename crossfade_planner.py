"""Crossfade playlist planner with sample-accurate envelopes and reflow."""

from __future__ import annotations

import threading


class CrossfadeError(ValueError):
    """Raised when a playlist entry or envelope request is invalid."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CrossfadePlanner:
    """Lay out playlist entries on a millisecond timeline with overlaps."""

    def __init__(self, *, sample_rate: int = 48000, max_entries: int = 256,
                 max_total_ms: int = 86_400_000) -> None:
        self.sample_rate = sample_rate
        self.max_entries = max_entries
        self.max_total_ms = max_total_ms
        self._lock = threading.RLock()

    def add_entry(self, entry_id: str, *, uri: str, duration_ms: int,
                  fade_in_ms: int = 0, fade_out_ms: int = 0) -> dict:
        raise NotImplementedError

    def remove_entry(self, entry_id: str) -> dict:
        raise NotImplementedError

    def trim_entry(self, entry_id: str, *, head_ms: int = 0,
                   tail_ms: int = 0) -> dict:
        raise NotImplementedError

    def entry(self, entry_id: str) -> dict:
        raise NotImplementedError

    def plan(self) -> dict:
        raise NotImplementedError

    def gain_at(self, entry_id: str, offset_ms: int) -> int:
        raise NotImplementedError

    def sample_bounds(self, entry_id: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
