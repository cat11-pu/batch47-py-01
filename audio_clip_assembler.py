"""Audio clip assembly sessions with crossfade and gap planning."""

from __future__ import annotations

import threading


class AudioError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AudioClipAssembler:
    """Manage draft timelines built from audio clips, then commit them."""

    def __init__(self, *, sample_rate: int = 48000, max_clips: int = 64,
                 default_fade: int = 480, max_fade: int = 2400) -> None:
        self.sample_rate = sample_rate
        self.max_clips = max_clips
        self.default_fade = default_fade
        self.max_fade = max_fade
        self._lock = threading.RLock()

    def create_session(self, session_id: str, *, gap_samples: int = 0,
                       max_duration: int | None = None) -> dict:
        raise NotImplementedError

    def drop_session(self, session_id: str) -> None:
        raise NotImplementedError

    def add_clip(self, session_id: str, clip_id: str, *, samples: int,
                 fade_in: int | None = None, fade_out: int | None = None,
                 gap_after: int | None = None) -> dict:
        raise NotImplementedError

    def remove_clip(self, session_id: str, clip_id: str) -> dict:
        raise NotImplementedError

    def move_clip(self, session_id: str, clip_id: str, position: int) -> dict:
        raise NotImplementedError

    def preview(self, session_id: str) -> dict:
        raise NotImplementedError

    def commit(self, session_id: str) -> dict:
        raise NotImplementedError

    def reopen(self, session_id: str) -> dict:
        raise NotImplementedError

    def capacity(self, session_id: str) -> dict:
        raise NotImplementedError

    def status(self, session_id: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
