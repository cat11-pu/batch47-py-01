"""MPEG-TS transport clock, continuity counter and resynchronisation guard."""

from __future__ import annotations

import threading


class TsClockError(ValueError):
    """Raised whenever a transport stream invariant is violated."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class TransportClockGuard:
    """Track per-PID continuity counters and unwrap 33-bit timestamps."""

    def __init__(self, *, wrap_bits: int = 33, tick_hz: int = 90000,
                 max_jump_ms: int = 5000, max_streams: int = 64,
                 batch_limit: int = 256, cc_loss_budget: int = 8,
                 pids_per_stream: int = 32) -> None:
        self.wrap_bits = wrap_bits
        self.tick_hz = tick_hz
        self.max_jump_ms = max_jump_ms
        self.max_streams = max_streams
        self.batch_limit = batch_limit
        self.cc_loss_budget = cc_loss_budget
        self.pids_per_stream = pids_per_stream
        self._lock = threading.RLock()

    def open_stream(self, stream_id: str, *, program: int = 1) -> dict:
        raise NotImplementedError

    def close_stream(self, stream_id: str) -> None:
        raise NotImplementedError

    def push_packet(self, stream_id: str, packet: dict) -> dict:
        raise NotImplementedError

    def push_batch(self, stream_id: str, packets) -> list:
        raise NotImplementedError

    def stream_state(self, stream_id: str) -> dict:
        raise NotImplementedError

    def unwrap(self, stream_id: str, raw: int, *, kind: str = "pts") -> int:
        raise NotImplementedError

    def repair(self, stream_id: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
