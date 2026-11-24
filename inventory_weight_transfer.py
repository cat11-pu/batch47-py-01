"""Container capacity, stack accounting and stock transfer service."""

from __future__ import annotations

import threading


class InventoryError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class InventoryTransferService:
    """Register containers and item definitions, then move stock between them."""

    def __init__(self, *, max_batch: int = 50, clock=None) -> None:
        self.max_batch = max_batch
        self._clock = clock
        self._lock = threading.RLock()

    def create_container(self, container_id: str, *, slots: int,
                         weight_limit: int) -> dict:
        raise NotImplementedError

    def define_item(self, item_id: str, *, stack_limit: int = 1,
                    unit_weight: int = 1) -> dict:
        raise NotImplementedError

    def grant(self, container_id: str, item_id: str, count: int) -> dict:
        raise NotImplementedError

    def force_grant(self, container_id: str, item_id: str, count: int) -> dict:
        raise NotImplementedError

    def move(self, container_id: str, item_id: str, *, to_container: str,
             count: int, request_id: str | None = None) -> dict:
        raise NotImplementedError

    def move_batch(self, ops, *, request_id: str | None = None) -> dict:
        raise NotImplementedError

    def usage(self, container_id: str) -> dict:
        raise NotImplementedError

    def is_over_encumbered(self, container_id: str) -> bool:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
