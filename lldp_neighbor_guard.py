"""LLDP neighbour database guard for access switches (implementation skeleton)."""

from __future__ import annotations

import threading


class LldpError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class LldpNeighborGuard:
    """Track LLDP neighbours per port with trust policy, ageing and quarantine."""

    def __init__(self, *, ttl_scale: int = 1, max_neighbors: int = 64,
                 max_age: int = 120, max_ports: int = 64, clock=None) -> None:
        self.ttl_scale = ttl_scale
        self.max_neighbors = max_neighbors
        self.max_age = max_age
        self.max_ports = max_ports
        self._clock = clock
        self._lock = threading.RLock()

    def configure_port(self, port: str, *, trust: str = "untrusted",
                       max_neighbors: int | None = None,
                       allowed_system_names=None,
                       require_capability: str | None = None,
                       allowed_mgmt_networks=None) -> dict:
        raise NotImplementedError

    def receive(self, port: str, frame: dict, *, now=None) -> dict:
        raise NotImplementedError

    def neighbors(self, port: str) -> list:
        raise NotImplementedError

    def lookup(self, port: str, chassis_id: str) -> dict | None:
        raise NotImplementedError

    def quarantine(self, port: str, *, reason: str) -> dict:
        raise NotImplementedError

    def release(self, port: str) -> dict:
        raise NotImplementedError

    def is_quarantined(self, port: str) -> bool:
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
