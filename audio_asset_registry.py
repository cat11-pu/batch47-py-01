"""Audio asset registry with WAV probing, dedupe and snapshots."""

from __future__ import annotations

import threading


class AssetError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AudioAssetRegistry:
    """Store uploaded WAV assets, probe their format and dedupe content."""

    def __init__(self, *, max_assets: int = 32, max_bytes: int = 1_000_000,
                 max_chunks: int = 16, allowed_sample_rates=None) -> None:
        self.max_assets = max_assets
        self.max_bytes = max_bytes
        self.max_chunks = max_chunks
        self.allowed_sample_rates = allowed_sample_rates
        self._lock = threading.RLock()

    def register(self, asset_id: str, data, *, tags=()) -> dict:
        raise NotImplementedError

    def get(self, asset_id: str) -> dict:
        raise NotImplementedError

    def remove(self, asset_id: str) -> dict:
        raise NotImplementedError

    def list_assets(self, *, tag: str | None = None) -> list:
        raise NotImplementedError

    def add_tag(self, asset_id: str, tag: str) -> dict:
        raise NotImplementedError

    def remove_tag(self, asset_id: str, tag: str) -> dict:
        raise NotImplementedError

    def verify(self, asset_id: str) -> dict:
        raise NotImplementedError

    def usage(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
