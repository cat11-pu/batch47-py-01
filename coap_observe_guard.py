"""CoAP observe, notification and block-wise transfer guard."""

from __future__ import annotations

import threading


class CoapError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CoapObserveGuard:
    """Track CoAP observe relations, notification freshness and block assembly."""

    def __init__(self, *, exchange_lifetime: int = 247,
                 max_observations: int = 32, max_relations: int = 8,
                 max_block_size: int = 1024, max_body_bytes: int = 65536,
                 max_pending: int = 4, clock=None) -> None:
        self.exchange_lifetime = exchange_lifetime
        self.max_observations = max_observations
        self.max_relations = max_relations
        self.max_block_size = max_block_size
        self.max_body_bytes = max_body_bytes
        self.max_pending = max_pending
        self._clock = clock
        self._lock = threading.RLock()

    def register_resource(self, path: str, *, max_age: int = 60) -> dict:
        raise NotImplementedError

    def observe(self, *, endpoint: str, token: str, path: str, now=None) -> dict:
        raise NotImplementedError

    def cancel_observe(self, *, endpoint: str, token: str, path: str) -> dict:
        raise NotImplementedError

    def notify(self, *, path: str, seq: int, payload_len: int, now=None) -> dict:
        raise NotImplementedError

    def ack(self, *, endpoint: str, token: str, path: str, seq: int,
            now=None) -> dict:
        raise NotImplementedError

    def describe(self, *, endpoint: str, token: str, path: str) -> dict:
        raise NotImplementedError

    def incoming(self, *, endpoint: str, message_id: int, token: str, payload,
                 block=None, now=None) -> dict:
        raise NotImplementedError

    def sweep(self, *, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
