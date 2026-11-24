"""Social gift exchange with daily quotas, expiry refunds and intimacy tracking."""

from __future__ import annotations

import threading


class GiftError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SocialGiftGuard:
    """Route player-to-player gifts with quotas, blocks and claim accounting."""

    def __init__(self, *, daily_send_limit: int = 20, pair_daily_limit: int = 3,
                 gift_ttl: int = 3600, max_pending: int = 500,
                 max_intimacy: int = 1000) -> None:
        self.daily_send_limit = daily_send_limit
        self.pair_daily_limit = pair_daily_limit
        self.gift_ttl = gift_ttl
        self.max_pending = max_pending
        self.max_intimacy = max_intimacy
        self._lock = threading.RLock()

    def register_gift(self, gift_id: str, *, name: str, cost: int, intimacy: int) -> dict:
        raise NotImplementedError

    def register_player(self, player_id: str, *, server: str, wallet: int = 0) -> dict:
        raise NotImplementedError

    def topup(self, player_id: str, amount: int) -> dict:
        raise NotImplementedError

    def wallet(self, player_id: str) -> int:
        raise NotImplementedError

    def block(self, player_id: str, blocked_id: str) -> dict:
        raise NotImplementedError

    def unblock(self, player_id: str, blocked_id: str) -> dict:
        raise NotImplementedError

    def send(self, sender_id: str, recipient_id: str, gift_id: str, *,
             now=None, request_id: str | None = None) -> dict:
        raise NotImplementedError

    def claim(self, recipient_id: str, instance_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def pending(self, recipient_id: str, *, now=None) -> list:
        raise NotImplementedError

    def purge(self, *, now=None) -> int:
        raise NotImplementedError

    def intimacy(self, first_id: str, second_id: str) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
