"""HTTP/1.1 message framing guard for edge proxies."""

from __future__ import annotations

import threading


class FramingError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class FramingGuard:
    """Track inbound HTTP/1.1 connections and frame each request exactly once."""

    def __init__(self, *, max_header_bytes: int = 8192,
                 max_body_bytes: int = 1048576, max_pipeline: int = 16,
                 clock=None) -> None:
        self.max_header_bytes = max_header_bytes
        self.max_body_bytes = max_body_bytes
        self.max_pipeline = max_pipeline
        self._clock = clock
        self._lock = threading.RLock()

    def begin_connection(self, conn_id: str, *, peer: str) -> dict:
        raise NotImplementedError

    def close_connection(self, conn_id: str) -> None:
        raise NotImplementedError

    def feed(self, conn_id: str, data) -> list:
        raise NotImplementedError

    def buffered_bytes(self, conn_id: str) -> int:
        raise NotImplementedError

    def outstanding(self, conn_id: str) -> int:
        raise NotImplementedError

    def complete_response(self, conn_id: str, *, status: int) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
