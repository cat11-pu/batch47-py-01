"""ARP binding inspection and spoofing guard for L2 access switches."""

from __future__ import annotations

import threading


class ArpGuardError(ValueError):
    """Raised when an ARP table operation or packet inspection is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ArpBindingGuard:
    """Keep per-VLAN IP/MAC bindings and validate inbound ARP packets."""

    def __init__(self, *, binding_ttl: int = 300, max_bindings: int = 4096,
                 violation_threshold: int = 3, quarantine_seconds: int = 300,
                 learning: bool = True, clock=None) -> None:
        self.binding_ttl = binding_ttl
        self.max_bindings = max_bindings
        self.violation_threshold = violation_threshold
        self.quarantine_seconds = quarantine_seconds
        self.learning = learning
        self._clock = clock
        self._lock = threading.RLock()

    def create_vlan(self, vlan_id: int, *, subnet: str, gateway_ip: str,
                    gateway_mac: str, uplink_port: str = "uplink") -> dict:
        raise NotImplementedError

    def remove_vlan(self, vlan_id: int) -> bool:
        raise NotImplementedError

    def bind_static(self, vlan_id: int, ip: str, mac: str, *,
                    port: str) -> dict:
        raise NotImplementedError

    def bind_dynamic(self, vlan_id: int, ip: str, mac: str, *, port: str,
                     now=None) -> dict:
        raise NotImplementedError

    def remove_binding(self, vlan_id: int, ip: str) -> bool:
        raise NotImplementedError

    def lookup(self, vlan_id: int, ip: str) -> dict | None:
        raise NotImplementedError

    def inspect(self, vlan_id: int, *, src_ip: str, src_mac: str,
                dst_ip: str, port: str, now=None) -> dict:
        raise NotImplementedError

    def quarantine_port(self, port: str, *, now=None,
                        reason: str = "manual") -> dict:
        raise NotImplementedError

    def release_port(self, port: str) -> bool:
        raise NotImplementedError

    def is_quarantined(self, port: str, *, now=None) -> bool:
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
