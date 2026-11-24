"""Equipment durability wear and currency-charged repair ledger."""

from __future__ import annotations

import threading


class DurabilityError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DurabilityLedger:
    """Track per-item durability and price repairs from a currency balance."""

    def __init__(self, *, base_fee: int = 10, per_point: int = 5,
                 max_batch: int = 32, clock=None) -> None:
        self.base_fee = base_fee
        self.per_point = per_point
        self.max_batch = max_batch
        self._clock = clock
        self._lock = threading.RLock()

    def grant_currency(self, player: str, amount: int) -> int:
        raise NotImplementedError

    def balance(self, player: str) -> int:
        raise NotImplementedError

    def register(self, player: str, item_id: str, *, max_durability: int,
                 tier: int) -> dict:
        raise NotImplementedError

    def use(self, player: str, item_id: str, *, wear: int = 1) -> dict:
        raise NotImplementedError

    def durability(self, player: str, item_id: str) -> int:
        raise NotImplementedError

    def is_broken(self, player: str, item_id: str) -> bool:
        raise NotImplementedError

    def broken_items(self, player: str) -> list:
        raise NotImplementedError

    def repair_cost(self, player: str, item_id: str, *, points=None,
                    discount: int = 0) -> dict:
        raise NotImplementedError

    def repair(self, player: str, item_id: str, *, points=None,
               discount: int = 0, request_id: str | None = None) -> dict:
        raise NotImplementedError

    def repair_all(self, player: str, *, discount: int = 0,
                   request_id: str | None = None) -> dict:
        raise NotImplementedError

    def status(self, player: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
