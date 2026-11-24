"""Step-up authentication assurance policy guard."""

from __future__ import annotations

import threading

DEFAULT_MAX_AGE = 900
DEFAULT_IDLE_TIMEOUT = 1800
DEFAULT_MAX_SESSIONS = 5
STAT_KEYS = ("evaluated", "allowed", "denied", "stepped_up", "revoked", "rejected")


class StepUpError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class StepUpAssuranceGuard:
    """Track sessions, assurance levels and resource step-up policies."""

    def __init__(self, *, default_max_age: int = DEFAULT_MAX_AGE,
                 idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
                 max_sessions_per_subject: int = DEFAULT_MAX_SESSIONS,
                 clock=None) -> None:
        self.default_max_age = default_max_age
        self.idle_timeout = idle_timeout
        self.max_sessions_per_subject = max_sessions_per_subject
        self._clock = clock
        self._lock = threading.RLock()

    def define_acr_level(self, acr: str, *, rank: int) -> dict:
        raise NotImplementedError

    def register_resource(self, resource: str, *, required_acr: str,
                          max_age: int | None = None,
                          allowed_amr=None) -> dict:
        raise NotImplementedError

    def register_session(self, session_id: str, *, subject: str, acr: str,
                         amr, auth_time: str, now=None) -> dict:
        raise NotImplementedError

    def evaluate(self, session_id: str, *, resource: str,
                 now=None) -> dict:
        raise NotImplementedError

    def step_up(self, session_id: str, *, acr: str, amr=None,
                auth_time=None, now=None) -> dict:
        raise NotImplementedError

    def touch(self, session_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def rotate_session(self, session_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def revoke_session(self, session_id: str, *,
                       reason: str = "manual") -> None:
        raise NotImplementedError

    def list_sessions(self, subject: str) -> list:
        raise NotImplementedError

    def purge(self, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
