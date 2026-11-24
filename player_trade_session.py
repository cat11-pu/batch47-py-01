"""Two-party trade sessions with locked offers and atomic settlement."""

from __future__ import annotations

import threading


class TradeError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class TradeSessionManager:
    """Coordinate offers, locks and confirmation for player-to-player trades."""

    def __init__(self, *, session_ttl: int = 300, max_open: int = 50,
                 clock=None) -> None:
        self.session_ttl = session_ttl
        self.max_open = max_open
        self._clock = clock
        self._lock = threading.RLock()

    def topup(self, player: str, *, coins: int = 0, items=None) -> dict:
        raise NotImplementedError

    def holdings(self, player: str) -> dict:
        raise NotImplementedError

    def open_session(self, player_a: str, player_b: str, *,
                     session_id: str | None = None, now=None) -> dict:
        raise NotImplementedError

    def offer(self, session_id: str, player: str, *, items=None, coins: int = 0,
              now=None) -> dict:
        raise NotImplementedError

    def lock(self, session_id: str, player: str, *, now=None) -> dict:
        raise NotImplementedError

    def confirm(self, session_id: str, player: str, *, now=None) -> dict:
        raise NotImplementedError

    def cancel(self, session_id: str, player: str, *, now=None) -> dict:
        raise NotImplementedError

    def status(self, session_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def expire(self, now=None) -> int:
        raise NotImplementedError

    def list_sessions(self, player=None, *, now=None) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
