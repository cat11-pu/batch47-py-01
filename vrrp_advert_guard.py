"""VRRP advertisement guard for first-hop redundancy groups (implementation skeleton)."""

from __future__ import annotations

import threading


class VrrpError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class VrrpAdvertGuard:
    """Validate VRRP adverts, elect masters and track preemption per group."""

    def __init__(self, *, version: int = 2, default_advert_interval: int = 1,
                 master_down_factor: int = 3, default_preempt_delay: int = 0,
                 clock=None) -> None:
        self.version = version
        self.default_advert_interval = default_advert_interval
        self.master_down_factor = master_down_factor
        self.default_preempt_delay = default_preempt_delay
        self._clock = clock
        self._lock = threading.RLock()

    def create_group(self, vrid: int, *, interface: str, vip: str, priority: int,
                     preempt: bool = True, preempt_delay: int | None = None,
                     advert_interval: int | None = None,
                     auth=None) -> dict:
        raise NotImplementedError

    def receive(self, advert: dict, *, now=None) -> dict:
        raise NotImplementedError

    def group_state(self, vrid: int, *, now=None) -> dict:
        raise NotImplementedError

    def groups(self) -> list:
        raise NotImplementedError

    def takeover(self, vrid: int, *, now=None) -> dict:
        raise NotImplementedError

    def step_down(self, vrid: int, *, now=None) -> dict:
        raise NotImplementedError

    def set_priority(self, vrid: int, priority: int) -> dict:
        raise NotImplementedError

    def block_router(self, src: str) -> None:
        raise NotImplementedError

    def unblock_router(self, src: str) -> bool:
        raise NotImplementedError

    def flush(self, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
