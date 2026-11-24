"""公会任务板的发布、认领、进度与结算。"""

from __future__ import annotations

import threading


class QuestError(ValueError):
    """任务板业务错误，携带稳定错误码。"""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class GuildQuestBoard:
    """维护公会任务、认领名额、进度上报与奖励结算。"""

    def __init__(self, *, max_open=20, max_active_per_member=2,
                 claim_ttl=1800, clock=None):
        self.max_open = max_open
        self.max_active_per_member = max_active_per_member
        self.claim_ttl = claim_ttl
        self._clock = clock
        self._lock = threading.RLock()

    def publish(self, guild_id, quest_id, *, title, slots=1, target=1,
                reward=100, min_level=1, requires=(), expires_at=None,
                now=None):
        raise NotImplementedError

    def claim(self, guild_id, quest_id, *, player_id, level=1, now=None):
        raise NotImplementedError

    def release(self, guild_id, quest_id, *, player_id, now=None):
        raise NotImplementedError

    def report_progress(self, guild_id, quest_id, *, player_id, amount=1,
                        now=None):
        raise NotImplementedError

    def complete(self, guild_id, quest_id, *, player_id, now=None):
        raise NotImplementedError

    def cancel(self, guild_id, quest_id, *, reason="", now=None):
        raise NotImplementedError

    def claims(self, guild_id, quest_id=None):
        raise NotImplementedError

    def quest(self, guild_id, quest_id):
        raise NotImplementedError

    def member_summary(self, guild_id, player_id):
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
