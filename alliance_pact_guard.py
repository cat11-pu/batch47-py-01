"""Guild alliance pacts with weighted votes, quorum, notice periods and cooldowns."""

from __future__ import annotations

import threading


class PactError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AlliancePact:
    """Manage alliance proposals, weighted votes, activation, break notice and sweep."""

    def __init__(self, *, vote_window: int = 3600, break_notice: int = 600,
                 reform_cooldown: int = 1800, max_pacts_per_guild: int = 3,
                 quorum_ratio: float = 0.5) -> None:
        self.vote_window = vote_window
        self.break_notice = break_notice
        self.reform_cooldown = reform_cooldown
        self.max_pacts_per_guild = max_pacts_per_guild
        self.quorum_ratio = quorum_ratio
        self._lock = threading.RLock()

    def register_guild(self, guild_id: str, *, level: int, members: int) -> dict:
        raise NotImplementedError

    def propose(self, proposer: str, *, pact_id: str, kind: str, partners,
                now=None) -> dict:
        raise NotImplementedError

    def vote(self, guild_id: str, pact_id: str, *, approve, weight=None,
             now=None) -> dict:
        raise NotImplementedError

    def tally(self, pact_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def finalize(self, pact_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def break_pact(self, pact_id: str, *, guild_id: str, now=None) -> dict:
        raise NotImplementedError

    def sweep(self, *, now=None) -> list:
        raise NotImplementedError

    def status(self, pact_id: str) -> dict:
        raise NotImplementedError

    def pacts_of(self, guild_id: str) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
