"""Macaroon caveat authorization guard."""

from __future__ import annotations

import threading


class MacaroonError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class MacaroonCaveatGuard:
    """Issue macaroon credentials, attenuate them and authorize offline."""

    def __init__(self, *, clock_skew: int = 60, max_caveats: int = 16,
                 max_tokens: int = 500, clock=None) -> None:
        self.clock_skew = clock_skew
        self.max_caveats = max_caveats
        self.max_tokens = max_tokens
        self._clock = clock
        self._lock = threading.RLock()

    def rotate_root_key(self, key_id: str, secret: bytes) -> dict:
        raise NotImplementedError

    def revoke_root_key(self, key_id: str) -> None:
        raise NotImplementedError

    def mint(self, *, location: str, identifier: str, caveats=(),
             key_id: str | None = None, now=None) -> dict:
        raise NotImplementedError

    def attenuate(self, token: dict, caveat: dict) -> dict:
        raise NotImplementedError

    def authorize(self, token: dict, *, context: dict | None = None,
                  discharges=(), now=None) -> dict:
        raise NotImplementedError

    def bind(self, token: dict, *, binding: str) -> dict:
        raise NotImplementedError

    def inspect(self, token: dict) -> dict:
        raise NotImplementedError

    def revoke(self, identifier: str) -> None:
        raise NotImplementedError

    def is_revoked(self, identifier: str) -> bool:
        raise NotImplementedError

    def purge(self, now=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
