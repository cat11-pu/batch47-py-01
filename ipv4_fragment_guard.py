"""IPv4 fragment reassembly guard (implementation skeleton)."""

from __future__ import annotations

import threading


class FragmentError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class Ipv4ReassemblyGuard:
    """Reassemble inbound IPv4 fragments under strict overlap and size limits."""

    def __init__(self, *, max_datagram_bytes: int = 65535,
                 max_fragments: int = 64, timeout: int = 30,
                 max_pending: int = 128, clock=None) -> None:
        self.max_datagram_bytes = max_datagram_bytes
        self.max_fragments = max_fragments
        self.timeout = timeout
        self.max_pending = max_pending
        self._clock = clock
        self._lock = threading.RLock()

    def accept(self, fragment: dict, *, now=None) -> dict:
        raise NotImplementedError

    def datagram(self, src: str, dst: str, ident: int) -> dict | None:
        raise NotImplementedError

    def pending_count(self) -> int:
        raise NotImplementedError

    def pending_keys(self) -> list:
        raise NotImplementedError

    def bytes_held(self) -> int:
        raise NotImplementedError

    def expire(self, now=None) -> int:
        raise NotImplementedError

    def discard(self, src: str, dst: str, ident: int) -> bool:
        raise NotImplementedError

    def limit_source(self, prefix: str, *, max_pending: int) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
