"""Guild event signup quota, waitlist and hold guard."""

from __future__ import annotations

import threading


class SignupError(ValueError):
    """Raised when a signup operation violates the event rules."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class EventSignupGuard:
    """Track event rosters, waitlists, holds and capacity changes."""

    def __init__(self, *, max_waitlist: int = 200, max_party: int = 5,
                 hold_ttl: int = 900, clock=None) -> None:
        self.max_waitlist = max_waitlist
        self.max_party = max_party
        self.hold_ttl = hold_ttl
        self._clock = clock
        self._lock = threading.RLock()

    def create_event(self, event_id: str, *, name: str, capacity: int,
                     start_at: str, signup_open_at: str,
                     signup_close_at: str | None = None) -> dict:
        raise NotImplementedError

    def open_signup(self, event_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def sign_up(self, event_id: str, player_id: str, *, party_size: int = 1,
                request_id: str | None = None, now=None) -> dict:
        raise NotImplementedError

    def cancel_signup(self, ticket_id: str, *, player_id: str, now=None) -> dict:
        raise NotImplementedError

    def check_in(self, ticket_id: str, *, player_id: str, now=None) -> dict:
        raise NotImplementedError

    def set_capacity(self, event_id: str, capacity: int, *, now=None) -> dict:
        raise NotImplementedError

    def lock_event(self, event_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def cancel_event(self, event_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def overview(self, event_id: str) -> dict:
        raise NotImplementedError

    def tickets(self, event_id: str, *, status: str | None = None) -> list:
        raise NotImplementedError

    def position(self, event_id: str, player_id: str) -> int:
        raise NotImplementedError

    def expire(self, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
