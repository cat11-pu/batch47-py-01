"""公会招新申请、审批与名额流水线。"""

from __future__ import annotations

import threading


class RecruitError(ValueError):
    """招新流程中的业务错误，携带稳定的错误码。"""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class GuildRecruitPipeline:
    """管理公会招募开关、玩家申请、审批、冷却与名额。"""

    def __init__(self, *, daily_apply_limit=3, pending_limit=2,
                 application_ttl=1800, cooldown_seconds=600,
                 batch_limit=20, clock=None):
        self.daily_apply_limit = daily_apply_limit
        self.pending_limit = pending_limit
        self.application_ttl = application_ttl
        self.cooldown_seconds = cooldown_seconds
        self.batch_limit = batch_limit
        self._clock = clock
        self._lock = threading.RLock()

    def open_recruitment(self, guild_id, *, seats, min_level=1, tags=(),
                         closes_at=None):
        raise NotImplementedError

    def close_recruitment(self, guild_id, *, now=None):
        raise NotImplementedError

    def apply(self, guild_id, player_id, *, level, note="", now=None):
        raise NotImplementedError

    def withdraw(self, guild_id, player_id, *, now=None):
        raise NotImplementedError

    def decide(self, guild_id, player_id, *, reviewer, approve, now=None):
        raise NotImplementedError

    def decide_batch(self, guild_id, player_ids, *, reviewer, approve, now=None):
        raise NotImplementedError

    def pending(self, guild_id):
        raise NotImplementedError

    def roster(self, guild_id):
        raise NotImplementedError

    def is_pending(self, guild_id, player_id):
        raise NotImplementedError

    def expire(self, now=None):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError

    def reset_stats(self):
        raise NotImplementedError
