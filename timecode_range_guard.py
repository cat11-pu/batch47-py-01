"""SMPTE timecode conversion and marker-range guard for review clips."""

from __future__ import annotations

import threading


class TcError(ValueError):
    """Raised when a timecode, clip or marker rule is violated."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class TimecodeRangeGuard:
    """Convert drop-frame / non-drop timecode and keep clip marker ranges."""

    def __init__(self, *, max_clips: int = 32, marker_limit: int = 500,
                 max_duration_frames: int = 10_000_000) -> None:
        self.max_clips = max_clips
        self.marker_limit = marker_limit
        self.max_duration_frames = max_duration_frames
        self._lock = threading.RLock()

    def parse_timecode(self, text: str, *, fps: int, drop_frame: bool) -> dict:
        raise NotImplementedError

    def format_timecode(self, frames: int, *, fps: int,
                        drop_frame: bool) -> str:
        raise NotImplementedError

    def frame_to_ms(self, frames: int, *, fps_num: int, fps_den: int) -> int:
        raise NotImplementedError

    def ms_to_frame(self, ms: int, *, fps_num: int, fps_den: int) -> int:
        raise NotImplementedError

    def open_clip(self, clip_id: str, *, fps_num: int, fps_den: int,
                  start_timecode: str, drop_frame: bool = False,
                  duration_frames: int) -> dict:
        raise NotImplementedError

    def close_clip(self, clip_id: str) -> None:
        raise NotImplementedError

    def add_marker(self, clip_id: str, *, start: str, end: str,
                   label: str) -> dict:
        raise NotImplementedError

    def markers(self, clip_id: str) -> list:
        raise NotImplementedError

    def merge_markers(self, clip_id: str, *, gap_frames: int = 0) -> dict:
        raise NotImplementedError

    def trim_range(self, clip_id: str, *, start: str, end: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
