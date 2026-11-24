"""Unicast reverse path forwarding guard for ingress source validation."""

from __future__ import annotations

import threading


class UrpfError(ValueError):
    """Raised when a route, mode or snapshot operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class UrpfGuard:
    """Validate packet sources against the routing table per interface."""

    def __init__(self, *, default_mode: str = "strict",
                 max_prefixes: int = 10000, clock=None) -> None:
        self.default_mode = default_mode
        self.max_prefixes = max_prefixes
        self._clock = clock
        self._lock = threading.RLock()

    def add_route(self, prefix: str, *, interface: str, metric: int = 100,
                  next_hop: str = "connected") -> dict:
        raise NotImplementedError

    def remove_route(self, prefix: str, *, interface: str | None = None) -> bool:
        raise NotImplementedError

    def route_lookup(self, ip: str) -> dict | None:
        raise NotImplementedError

    def set_interface_mode(self, interface: str, *, mode: str,
                           allow_asymmetric: bool = False) -> dict:
        raise NotImplementedError

    def interface_state(self, interface: str) -> dict:
        raise NotImplementedError

    def allow_source(self, prefix: str, *, interface: str | None = None,
                     expires_at=None) -> dict:
        raise NotImplementedError

    def remove_allow(self, prefix: str, *,
                     interface: str | None = None) -> bool:
        raise NotImplementedError

    def check(self, interface: str, *, src_ip: str, dst_ip: str,
              now=None) -> dict:
        raise NotImplementedError

    def purge(self, now=None) -> int:
        raise NotImplementedError

    def list_routes(self, limit: int = 100) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
