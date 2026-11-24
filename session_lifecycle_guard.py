"""Web session lifecycle guard."""

from __future__ import annotations

import copy
import secrets
import threading
from datetime import datetime, timedelta, timezone


class SessionError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


_STAT_KEYS = ("created", "validated", "rejected", "revoked", "rotated")

_SESSION_FIELDS = ("user_id", "device_id", "ip", "auth_level", "pin_ip",
                   "created_at", "expires_at", "idle_expires_at")


def _parse_ts(value):
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        ts = datetime.fromisoformat(value)
    else:
        raise SessionError("SESSION_TIMESTAMP_INVALID", value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _format_ts(ts: datetime) -> str:
    return ts.isoformat()


class SessionLifecycleGuard:
    """Create, rotate, elevate, revoke and expire login sessions."""

    def __init__(self, *, idle_timeout: int = 900, absolute_timeout: int = 28800,
                 max_sessions_per_user: int = 5, clock=None) -> None:
        self.idle_timeout = idle_timeout
        self.absolute_timeout = absolute_timeout
        self.max_sessions_per_user = max_sessions_per_user
        self._clock = clock
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        self._issued_ids: set[str] = set()
        self._stats = {key: 0 for key in _STAT_KEYS}

    def _now(self, now) -> datetime:
        if now is not None:
            return _parse_ts(now)
        if self._clock is not None:
            return _parse_ts(self._clock())
        return datetime.now(timezone.utc)

    def _new_session_id(self, user_id: str) -> str:
        while True:
            session_id = "sess_" + secrets.token_hex(16)
            if user_id and user_id in session_id:
                continue
            if session_id in self._issued_ids:
                continue
            self._issued_ids.add(session_id)
            return session_id

    def _is_expired(self, record: dict, now: datetime) -> bool:
        return (now >= _parse_ts(record["expires_at"])
                or now >= _parse_ts(record["idle_expires_at"]))

    def _active_sessions(self, user_id: str, now: datetime) -> list:
        return [rec for rec in self._sessions.values()
                if rec["user_id"] == user_id and not self._is_expired(rec, now)]

    def _lookup(self, session_id: str, now: datetime) -> dict:
        record = self._sessions.get(session_id)
        if record is None:
            raise SessionError("SESSION_UNKNOWN", session_id)
        if now >= _parse_ts(record["expires_at"]):
            raise SessionError("SESSION_ABSOLUTE_TIMEOUT", session_id)
        if now >= _parse_ts(record["idle_expires_at"]):
            raise SessionError("SESSION_IDLE_TIMEOUT", session_id)
        return record

    def _touch(self, record: dict, now: datetime) -> None:
        record["idle_expires_at"] = _format_ts(
            now + timedelta(seconds=self.idle_timeout))

    def _public_view(self, session_id: str, record: dict) -> dict:
        return {
            "session_id": session_id,
            "user_id": record["user_id"],
            "auth_level": record["auth_level"],
            "created_at": record["created_at"],
            "expires_at": record["expires_at"],
            "idle_expires_at": record["idle_expires_at"],
        }

    def create_session(self, *, user_id: str, device_id: str, ip: str,
                       auth_level: int = 1, pin_ip: bool = False, now=None) -> dict:
        with self._lock:
            moment = self._now(now)
            if len(self._active_sessions(user_id, moment)) >= self.max_sessions_per_user:
                raise SessionError("SESSION_LIMIT_EXCEEDED", user_id)
            session_id = self._new_session_id(user_id)
            record = {
                "user_id": user_id,
                "device_id": device_id,
                "ip": ip,
                "auth_level": auth_level,
                "pin_ip": bool(pin_ip),
                "created_at": _format_ts(moment),
                "expires_at": _format_ts(
                    moment + timedelta(seconds=self.absolute_timeout)),
                "idle_expires_at": _format_ts(
                    moment + timedelta(seconds=self.idle_timeout)),
            }
            self._sessions[session_id] = record
            self._stats["created"] += 1
            return self._public_view(session_id, record)

    def validate(self, session_id: str, *, device_id: str | None = None,
                 ip: str | None = None, now=None) -> dict:
        with self._lock:
            moment = self._now(now)
            try:
                record = self._lookup(session_id, moment)
                if device_id is not None and device_id != record["device_id"]:
                    raise SessionError("SESSION_DEVICE_MISMATCH", session_id)
                if record["pin_ip"] and ip is not None and ip != record["ip"]:
                    raise SessionError("SESSION_IP_MISMATCH", session_id)
            except SessionError:
                self._stats["rejected"] += 1
                raise
            self._touch(record, moment)
            self._stats["validated"] += 1
            view = self._public_view(session_id, record)
            view["status"] = "ok"
            return view

    def rotate(self, session_id: str, *, now=None) -> dict:
        with self._lock:
            moment = self._now(now)
            record = self._lookup(session_id, moment)
            new_id = self._new_session_id(record["user_id"])
            del self._sessions[session_id]
            self._touch(record, moment)
            self._sessions[new_id] = record
            self._stats["rotated"] += 1
            return self._public_view(new_id, record)

    def elevate(self, session_id: str, *, auth_level: int, now=None) -> dict:
        with self._lock:
            moment = self._now(now)
            record = self._lookup(session_id, moment)
            if auth_level not in (2, 3) or auth_level <= record["auth_level"]:
                raise SessionError("SESSION_AUTH_LEVEL_INVALID", auth_level)
            record["auth_level"] = auth_level
            self._touch(record, moment)
            return self._public_view(session_id, record)

    def revoke(self, session_id: str, *, reason: str = "user") -> bool:
        with self._lock:
            if self._sessions.pop(session_id, None) is None:
                return False
            self._stats["revoked"] += 1
            return True

    def revoke_all(self, user_id: str, *, except_session: str | None = None) -> int:
        with self._lock:
            targets = [sid for sid, rec in self._sessions.items()
                       if rec["user_id"] == user_id and sid != except_session]
            for sid in targets:
                del self._sessions[sid]
            self._stats["revoked"] += len(targets)
            return len(targets)

    def list_sessions(self, user_id: str, *, now=None) -> list:
        with self._lock:
            moment = self._now(now)
            rows = []
            for sid, rec in self._sessions.items():
                if rec["user_id"] != user_id or self._is_expired(rec, moment):
                    continue
                rows.append({
                    "session_id": sid,
                    "user_id": rec["user_id"],
                    "device_id": rec["device_id"],
                    "auth_level": rec["auth_level"],
                    "created_at": rec["created_at"],
                    "expires_at": rec["expires_at"],
                    "idle_expires_at": rec["idle_expires_at"],
                })
            rows.sort(key=lambda row: row["session_id"])
            return rows

    def purge(self, now=None) -> int:
        with self._lock:
            moment = self._now(now)
            expired = [sid for sid, rec in self._sessions.items()
                       if self._is_expired(rec, moment)]
            for sid in expired:
                del self._sessions[sid]
            return len(expired)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "sessions": copy.deepcopy(self._sessions),
                "stats": dict(self._stats),
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            sessions, stats = self._validated_snapshot(state)
            self._sessions = sessions
            self._stats = stats
            self._issued_ids = set(sessions)

    @staticmethod
    def _validated_snapshot(state):
        def invalid():
            raise SessionError("SESSION_SNAPSHOT_INVALID", None)

        if not isinstance(state, dict):
            invalid()
        if state.get("version") != 1:
            invalid()
        sessions = state.get("sessions")
        stats = state.get("stats")
        if not isinstance(sessions, dict) or not isinstance(stats, dict):
            invalid()
        if any(key not in stats or not isinstance(stats[key], int)
               for key in _STAT_KEYS):
            invalid()
        for session_id, record in sessions.items():
            if not isinstance(session_id, str) or not isinstance(record, dict):
                invalid()
            if any(field not in record for field in _SESSION_FIELDS):
                invalid()
            for stamp in ("created_at", "expires_at", "idle_expires_at"):
                try:
                    _parse_ts(record[stamp])
                except (SessionError, ValueError, TypeError):
                    invalid()
        return copy.deepcopy(sessions), {key: int(stats[key]) for key in _STAT_KEYS}

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = {key: 0 for key in _STAT_KEYS}
