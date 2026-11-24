"""Multi-resolution waveform peak index for an audio editor timeline."""

from __future__ import annotations

import threading


class WaveError(ValueError):
    """Raised when sample data, ranges or levels are invalid."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class WaveformPeakIndex:
    """Maintain min/max/RMS buckets across a bounded sample history."""

    def __init__(self, *, sample_rate: int = 48000, base_bucket: int = 256,
                 max_levels: int = 8,
                 max_samples: int = 48_000_000,
                 max_query_buckets: int = 4096) -> None:
        self.sample_rate = sample_rate
        self.base_bucket = base_bucket
        self.max_levels = max_levels
        self.max_samples = max_samples
        self.max_query_buckets = max_query_buckets
        self._lock = threading.RLock()

    def append(self, samples) -> dict:
        raise NotImplementedError

    def append_pcm16(self, data: bytes) -> dict:
        raise NotImplementedError

    def level_for(self, ms_per_pixel: float) -> int:
        raise NotImplementedError

    def query(self, start_ms: int, end_ms: int, *,
              ms_per_pixel: float) -> list:
        raise NotImplementedError

    def peaks(self, *, level: int) -> list:
        raise NotImplementedError

    def summary(self) -> dict:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
