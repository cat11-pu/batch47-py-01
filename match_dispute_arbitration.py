"""赛事对局争议的申诉、裁判分配与改判。"""

from __future__ import annotations

import threading


class DisputeError(ValueError):
    """争议仲裁业务错误，携带稳定错误码。"""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class MatchDisputeArbitration:
    """登记对局结果，受理争议，组织裁判投票并落定改判。"""

    def __init__(self, *, appeal_window=3600, evidence_limit=5, panel_size=3,
                 sla_seconds=1800, max_open_per_match=2, clock=None):
        self.appeal_window = appeal_window
        self.evidence_limit = evidence_limit
        self.panel_size = panel_size
        self.sla_seconds = sla_seconds
        self.max_open_per_match = max_open_per_match
        self._clock = clock
        self._lock = threading.RLock()

    def open_match(self, match_id, *, winner, participants, now=None):
        raise NotImplementedError

    def submit_dispute(self, match_id, *, player_id, reason, evidence=(),
                       now=None):
        raise NotImplementedError

    def assign_reviewers(self, dispute_id, reviewers, *, now=None):
        raise NotImplementedError

    def cast_vote(self, dispute_id, *, reviewer, verdict, now=None):
        raise NotImplementedError

    def resolve(self, dispute_id, *, now=None):
        raise NotImplementedError

    def withdraw_dispute(self, dispute_id, *, player_id, now=None):
        raise NotImplementedError

    def escalate(self, dispute_id, *, reason, now=None):
        raise NotImplementedError

    def disputes(self, *, match_id=None, status=None):
        raise NotImplementedError

    def match_result(self, match_id):
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
