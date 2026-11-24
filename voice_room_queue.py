"""Voice room mic seats, waitlist priority and preemption queue."""

from __future__ import annotations

import threading


class VoiceError(ValueError):
    """Raised when a voice room operation is not allowed."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class VoiceRoomQueue:
    """Manage voice room seats, priority waitlists and seat handover."""

    def __init__(self, *, seat_ttl: int = 60, max_queue: int = 100,
                 max_seats: int = 16, clock=None) -> None:
        self.seat_ttl = seat_ttl
        self.max_queue = max_queue
        self.max_seats = max_seats
        self._clock = clock
        self._lock = threading.RLock()

    def create_room(self, room_id: str, *, name: str, owner: str,
                    seats: int) -> dict:
        raise NotImplementedError

    def close_room(self, room_id: str, *, by_user: str, now=None) -> dict:
        raise NotImplementedError

    def join_queue(self, room_id: str, user_id: str, *, priority: int = 0,
                   request_id: str | None = None, now=None) -> dict:
        raise NotImplementedError

    def leave_queue(self, room_id: str, user_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def leave_seat(self, room_id: str, user_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def set_seats(self, room_id: str, seats: int, *, by_user: str,
                  now=None) -> dict:
        raise NotImplementedError

    def preempt(self, room_id: str, target_id: str, *, by_user: str,
                now=None) -> dict:
        raise NotImplementedError

    def renew(self, room_id: str, user_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def mute(self, room_id: str, user_id: str, *, by_user: str,
             muted: bool = True, now=None) -> dict:
        raise NotImplementedError

    def seats(self, room_id: str) -> list:
        raise NotImplementedError

    def queue(self, room_id: str) -> list:
        raise NotImplementedError

    def describe_room(self, room_id: str) -> dict:
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
