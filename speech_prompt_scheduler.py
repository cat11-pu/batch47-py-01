"""Speech prompt playback scheduler with priorities and barge-in."""

from __future__ import annotations

import threading


class SpeechError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SpeechPromptScheduler:
    """Queue spoken prompts and arbitrate which one may play."""

    def __init__(self, *, max_pending: int = 8, default_duration_ms: int = 1200,
                 barge_in_priority: int = 7, max_backlog_ms: int = 60000,
                 clock=None) -> None:
        self.max_pending = max_pending
        self.default_duration_ms = default_duration_ms
        self.barge_in_priority = barge_in_priority
        self.max_backlog_ms = max_backlog_ms
        self._clock = clock
        self._lock = threading.RLock()

    def submit(self, channel_id: str, prompt_id: str, *, priority: int,
               duration_ms: int | None = None, ttl_ms: int | None = None,
               dedupe_key: str | None = None, now: int | None = None) -> dict:
        raise NotImplementedError

    def cancel(self, channel_id: str, prompt_id: str) -> dict:
        raise NotImplementedError

    def current(self, channel_id: str, *, now: int | None = None) -> dict | None:
        raise NotImplementedError

    def pending(self, channel_id: str) -> list:
        raise NotImplementedError

    def advance(self, channel_id: str, *, now: int | None = None) -> list:
        raise NotImplementedError

    def queue_depth(self, channel_id: str) -> dict:
        raise NotImplementedError

    def flush(self, channel_id: str) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
