"""UTC attendance calendar with streaks, makeups and cycle bonuses."""

from __future__ import annotations

import threading


class AttendanceError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AttendanceCalendar:
    """Track per-player check-in chains across fixed length reward cycles."""

    def __init__(self, *, cycle_days: int = 7, makeup_limit: int = 2,
                 card_cap: int = 5, history_limit: int = 400,
                 rewards=None, clock=None) -> None:
        self.cycle_days = cycle_days
        self.makeup_limit = makeup_limit
        self.card_cap = card_cap
        self.history_limit = history_limit
        self._clock = clock
        self._lock = threading.RLock()

    def check_in(self, player: str, now=None) -> dict:
        raise NotImplementedError

    def makeup(self, player: str, day, now=None) -> dict:
        raise NotImplementedError

    def grant_makeup_cards(self, player: str, count: int) -> dict:
        raise NotImplementedError

    def status(self, player: str, now=None) -> dict:
        raise NotImplementedError

    def history(self, player: str, limit: int = 30) -> list:
        raise NotImplementedError

    def rewards_table(self) -> list:
        raise NotImplementedError

    def reset_player(self, player: str) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
