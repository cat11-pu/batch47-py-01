"""Input cadence forensics for detecting scripted combat input."""

from __future__ import annotations


class ForensicsError(ValueError):
    """Raised when a forensic session operation cannot be completed."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class InputMacroForensics:
    """Collect player input events, score cadence and keep player penalties."""

    def __init__(self, *, window_ms: int = 60000, min_samples: int = 12,
                 jitter_threshold_ms: int = 6, repeat_threshold: float = 0.85,
                 penalty_ttl_ms: int = 300000, max_sessions: int = 500,
                 max_events: int = 1024, batch_limit: int = 64,
                 clock=None) -> None:
        self.window_ms = window_ms
        self.min_samples = min_samples
        self.jitter_threshold_ms = jitter_threshold_ms
        self.repeat_threshold = repeat_threshold
        self.penalty_ttl_ms = penalty_ttl_ms
        self.max_sessions = max_sessions
        self.max_events = max_events
        self.batch_limit = batch_limit
        self._clock = clock

    def open_session(self, session_id: str, *, player_id: str,
                     device_class: str = "pc") -> dict:
        raise NotImplementedError

    def close_session(self, session_id: str, *, now_ms: int) -> dict:
        raise NotImplementedError

    def ingest(self, session_id: str, events, *, now_ms: int) -> dict:
        raise NotImplementedError

    def session_report(self, session_id: str) -> dict:
        raise NotImplementedError

    def flag_player(self, player_id: str, *, reason: str, now_ms: int,
                    ttl_ms: int | None = None) -> dict:
        raise NotImplementedError

    def is_penalized(self, player_id: str, *, now_ms: int) -> bool:
        raise NotImplementedError

    def clear_penalty(self, player_id: str) -> bool:
        raise NotImplementedError

    def player_summary(self, player_id: str, *, now_ms: int) -> dict:
        raise NotImplementedError

    def top_suspects(self, limit: int = 5) -> list:
        raise NotImplementedError

    def purge(self, now_ms: int) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
