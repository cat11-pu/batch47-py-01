"""Release signing quorum guard (implementation skeleton)."""

from __future__ import annotations

import threading


class QuorumError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SigningQuorum:
    """Collect weighted role approvals before a release artifact may be signed."""

    def __init__(self, *, max_open: int = 20, max_approvals_per_signer: int = 5,
                 clock=None) -> None:
        self.max_open = max_open
        self.max_approvals_per_signer = max_approvals_per_signer
        self._clock = clock
        self._lock = threading.RLock()

    def register_signer(self, signer: str, *, role: str, weight: int = 1,
                        keys=()) -> dict:
        raise NotImplementedError

    def suspend_signer(self, signer: str, *, reason: str) -> dict:
        raise NotImplementedError

    def reinstate_signer(self, signer: str) -> dict:
        raise NotImplementedError

    def open_proposal(self, proposal_id: str, *, artifact: str, digest: str,
                      channel: str, required_roles=(), min_weight: int = 1,
                      expires_at=None, now=None) -> dict:
        raise NotImplementedError

    def approve(self, proposal_id: str, signer: str, *, now=None) -> dict:
        raise NotImplementedError

    def reject(self, proposal_id: str, signer: str, *, reason: str,
               now=None) -> dict:
        raise NotImplementedError

    def revoke_approval(self, proposal_id: str, signer: str, *, now=None) -> dict:
        raise NotImplementedError

    def finalize(self, proposal_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def close_proposal(self, proposal_id: str, *, reason: str, now=None) -> dict:
        raise NotImplementedError

    def history(self, proposal_id: str, *, limit: int = 50) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
