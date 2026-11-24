"""HTTP/2 request normalization and connection reuse guard for the edge proxy."""

from __future__ import annotations

import threading

SNAPSHOT_VERSION = 1

STAT_KEYS = ("forwarded", "rejected", "opened", "closed")

BACKEND_PROTOCOLS = ("http/1.1", "http/2")


class SmugglingError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class RequestSmugglingGuard:
    """Frame, connection and reuse ledger for HTTP/2 to HTTP/1.1 forwarding."""

    def __init__(self, *, max_header_bytes: int = 16384, max_headers: int = 64,
                 max_streams: int = 100, idle_ttl: int = 30, clock=None) -> None:
        self.max_header_bytes = max_header_bytes
        self.max_headers = max_headers
        self.max_streams = max_streams
        self.idle_ttl = idle_ttl
        self._clock = clock
        self._lock = threading.RLock()

    def register_backend(self, name: str, *, address: str,
                         protocol: str = "http/1.1",
                         max_streams: int | None = None) -> dict:
        raise NotImplementedError

    def open_connection(self, conn_id: str, *, backend: str, peer: str,
                        now=None) -> dict:
        raise NotImplementedError

    def normalize(self, headers, *, method: str = "GET", target: str = "/",
                  authority: str | None = None) -> dict:
        raise NotImplementedError

    def admit(self, conn_id: str, request: dict, *, now=None) -> dict:
        raise NotImplementedError

    def release(self, conn_id: str, *, clean: bool = True, now=None) -> dict:
        raise NotImplementedError

    def close_connection(self, conn_id: str, *, reason: str = "client",
                         now=None) -> dict:
        raise NotImplementedError

    def quarantine(self, conn_id: str, *, reason: str) -> dict:
        raise NotImplementedError

    def recycle(self, now=None) -> int:
        raise NotImplementedError

    def connection_state(self, conn_id: str) -> dict:
        raise NotImplementedError

    def backend_status(self, backend: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
