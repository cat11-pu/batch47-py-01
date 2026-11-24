"""IGMP snooping membership guard for L2 multicast isolation (implementation skeleton)."""

from __future__ import annotations

import threading


class IgmpError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class IgmpSnoopGuard:
    """Track IGMPv1/v2/v3 memberships per VLAN and port with strict limits."""

    def __init__(self, *, group_timeout: int = 260, leave_window: int = 2,
                 max_groups_per_port: int = 32, max_groups_per_vlan: int = 256,
                 max_vlans: int = 64, clock=None) -> None:
        self.group_timeout = group_timeout
        self.leave_window = leave_window
        self.max_groups_per_port = max_groups_per_port
        self.max_groups_per_vlan = max_groups_per_vlan
        self.max_vlans = max_vlans
        self._clock = clock
        self._lock = threading.RLock()

    def add_vlan(self, vlan: int, *, querier: str | None = None,
                 fast_leave: bool = False) -> dict:
        raise NotImplementedError

    def set_querier(self, vlan: int, addr: str, *, now=None) -> dict:
        raise NotImplementedError

    def mark_router_port(self, vlan: int, port: str, *, router: bool = True) -> dict:
        raise NotImplementedError

    def join(self, vlan: int, port: str, group: str, *, version: int = 2,
             source: str | None = None, now=None) -> dict:
        raise NotImplementedError

    def leave(self, vlan: int, port: str, group: str, *, source: str | None = None,
              now=None) -> dict:
        raise NotImplementedError

    def members(self, vlan: int, group: str) -> list:
        raise NotImplementedError

    def flood_targets(self, vlan: int, group: str, *, now=None) -> list:
        raise NotImplementedError

    def deny_group(self, vlan: int, group: str) -> dict:
        raise NotImplementedError

    def allow_group(self, vlan: int, group: str) -> bool:
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
