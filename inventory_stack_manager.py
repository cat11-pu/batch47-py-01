"""Stack-aware inventory and warehouse transfers."""

from __future__ import annotations

import threading


class InventoryError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class InventoryStackManager:
    """Keep bag and warehouse slots with per-stack size limits."""

    def __init__(self, *, bag_slots: int = 20, warehouse_slots: int = 40,
                 stack_size: int = 99) -> None:
        self.bag_slots = bag_slots
        self.warehouse_slots = warehouse_slots
        self.stack_size = stack_size
        self._lock = threading.RLock()

    def grant(self, player: str, item_id: str, count: int, *,
              bound: bool = False) -> dict:
        raise NotImplementedError

    def remove(self, player: str, item_id: str, count: int, *, bound=None) -> dict:
        raise NotImplementedError

    def move_to_warehouse(self, player: str, item_id: str, count: int, *,
                          bound=None) -> dict:
        raise NotImplementedError

    def move_to_bag(self, player: str, item_id: str, count: int, *,
                    bound=None) -> dict:
        raise NotImplementedError

    def split_stack(self, player: str, slot_index: int, count: int) -> dict:
        raise NotImplementedError

    def merge_stacks(self, player: str, from_slot: int, to_slot: int) -> dict:
        raise NotImplementedError

    def discard(self, player: str, slot_index: int, count: int) -> dict:
        raise NotImplementedError

    def bag(self, player: str) -> list:
        raise NotImplementedError

    def warehouse(self, player: str) -> list:
        raise NotImplementedError

    def capacity(self, player: str) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
