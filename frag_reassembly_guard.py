"""IPv4/IPv6 fragment reassembly guard for edge routers."""

from __future__ import annotations

import threading


class FragError(ValueError):
    """Raised when a fragment or a table operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class FragGuard:
    """Track fragment sets per datagram and enforce anti-evasion limits."""

    def __init__(self, *, max_datagrams: int = 1024,
                 max_fragments_per_datagram: int = 64,
                 reassembly_timeout: int = 30,
                 min_fragment_payload: int = 8,
                 max_datagram_bytes: int = 65535,
                 auto_block_threshold: int = 2,
                 clock=None) -> None:
        self.max_datagrams = max_datagrams
        self.max_fragments_per_datagram = max_fragments_per_datagram
        self.reassembly_timeout = reassembly_timeout
        self.min_fragment_payload = min_fragment_payload
        self.max_datagram_bytes = max_datagram_bytes
        self.auto_block_threshold = auto_block_threshold
        self._clock = clock
        self._lock = threading.RLock()

    @staticmethod
    def datagram_key(fragment: dict) -> str:
        raise NotImplementedError

    def submit(self, fragment: dict, *, now=None) -> dict:
        raise NotImplementedError

    def pending_count(self) -> int:
        raise NotImplementedError

    def list_pending(self) -> list:
        raise NotImplementedError

    def flush(self, key: str) -> bool:
        raise NotImplementedError

    def block_source(self, ip: str, *, reason: str = "manual") -> dict:
        raise NotImplementedError

    def unblock_source(self, ip: str) -> bool:
        raise NotImplementedError

    def is_blocked(self, ip: str) -> bool:
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
