"""Guild research lab with contribution pool, dependency gates and boost budget."""

from __future__ import annotations

import threading


class ResearchError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class GuildResearchLab:
    """Track guild research nodes, member contributions and active projects."""

    def __init__(self, *, max_concurrent: int = 2, refund_ratio: float = 0.5,
                 daily_cap: int = 500, boost_budget: int = 600) -> None:
        self.max_concurrent = max_concurrent
        self.refund_ratio = refund_ratio
        self.daily_cap = daily_cap
        self.boost_budget = boost_budget
        self._lock = threading.RLock()

    def define_node(self, node_id: str, *, name: str, tier: int, cost: int,
                    duration: int, requires=()) -> dict:
        raise NotImplementedError

    def status(self, node_id: str) -> dict:
        raise NotImplementedError

    def contribute(self, member_id: str, amount: int, *, now=None) -> dict:
        raise NotImplementedError

    def pool(self) -> int:
        raise NotImplementedError

    def start(self, node_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def complete_due(self, *, now=None) -> list:
        raise NotImplementedError

    def boost(self, node_id: str, seconds: int, *, now=None) -> dict:
        raise NotImplementedError

    def cancel(self, node_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def completed(self) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
