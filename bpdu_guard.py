"""STP BPDU guard for access, uplink and root-facing switch ports."""

from __future__ import annotations

import threading


class BpduError(ValueError):
    """Raised when a BPDU or a port operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BpduGuard:
    """Inspect inbound BPDUs and err-disable ports that threaten the topology."""

    def __init__(self, *, root_priority: int | None = None,
                 tc_threshold: int = 10, tc_window: int = 10,
                 err_disable_seconds: int = 300, max_err_disable: int = 2,
                 clock=None) -> None:
        self.root_priority = root_priority
        self.tc_threshold = tc_threshold
        self.tc_window = tc_window
        self.err_disable_seconds = err_disable_seconds
        self.max_err_disable = max_err_disable
        self._clock = clock
        self._lock = threading.RLock()

    def register_port(self, port: str, *, role: str = "access",
                      bpdu_guard=None, root_guard=None) -> dict:
        raise NotImplementedError

    def unregister_port(self, port: str) -> bool:
        raise NotImplementedError

    def set_root_bridge(self, bridge_id: str, *, priority: int) -> dict:
        raise NotImplementedError

    def receive_bpdu(self, port: str, bpdu: dict, *, now=None) -> dict:
        raise NotImplementedError

    def port_state(self, port: str, *, now=None) -> dict:
        raise NotImplementedError

    def is_err_disabled(self, port: str, *, now=None) -> bool:
        raise NotImplementedError

    def recover_port(self, port: str, *, now=None) -> bool:
        raise NotImplementedError

    def list_ports(self) -> list:
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
