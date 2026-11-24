"""TURN allocation, permission and channel guard."""

from __future__ import annotations

import threading


class TurnError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class TurnAllocationGuard:
    """Manage TURN allocations, peer permissions, channels and relay budgets."""

    def __init__(self, *, max_allocations: int = 64, max_permissions: int = 16,
                 max_channels: int = 16, lifetime: int = 600,
                 max_lifetime: int = 3600, permission_ttl: int = 300,
                 channel_ttl: int = 600, bandwidth_bytes: int = 1048576,
                 clock=None) -> None:
        self.max_allocations = max_allocations
        self.max_permissions = max_permissions
        self.max_channels = max_channels
        self.lifetime = lifetime
        self.max_lifetime = max_lifetime
        self.permission_ttl = permission_ttl
        self.channel_ttl = channel_ttl
        self.bandwidth_bytes = bandwidth_bytes
        self._clock = clock
        self._lock = threading.RLock()

    def add_user(self, username: str, *, realm: str) -> dict:
        raise NotImplementedError

    def allocate(self, *, username: str, realm: str, five_tuple,
                 requested_lifetime=None, now=None) -> dict:
        raise NotImplementedError

    def refresh(self, *, username: str, allocation_id: str, lifetime=None,
                now=None) -> dict:
        raise NotImplementedError

    def create_permission(self, *, username: str, allocation_id: str,
                          peer_ip: str, now=None) -> dict:
        raise NotImplementedError

    def bind_channel(self, *, username: str, allocation_id: str, peer_ip: str,
                     channel=None, now=None) -> dict:
        raise NotImplementedError

    def relay(self, *, username: str, allocation_id: str, peer_ip: str,
              size: int, now=None) -> dict:
        raise NotImplementedError

    def release(self, *, username: str, allocation_id: str) -> dict:
        raise NotImplementedError

    def sweep(self, *, now=None) -> int:
        raise NotImplementedError

    def describe(self, *, username: str, allocation_id: str, now=None) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
