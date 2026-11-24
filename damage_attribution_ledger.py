"""Combat damage decomposition, attribution and hit idempotency ledger."""

from __future__ import annotations


class DamageError(ValueError):
    """Raised when a damage or healing operation cannot be completed."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DamageAttributionLedger:
    """Apply hits, split damage into components and aggregate attribution."""

    def __init__(self, *, max_combatants: int = 64, max_events: int = 1000,
                 hit_cache: int = 256) -> None:
        self.max_combatants = max_combatants
        self.max_events = max_events
        self.hit_cache = hit_cache

    def register_combatant(self, combatant_id: str, *, hp: int, shield: int = 0,
                           resistances=None, reflect_percent: int = 0,
                           lifesteal_percent: int = 0) -> dict:
        raise NotImplementedError

    def unregister_combatant(self, combatant_id: str) -> bool:
        raise NotImplementedError

    def set_resistance(self, combatant_id: str, *, element: str,
                       percent: int) -> dict:
        raise NotImplementedError

    def grant_shield(self, combatant_id: str, amount: int) -> dict:
        raise NotImplementedError

    def resolve_hit(self, attacker_id: str, defender_id: str, *, raw: int,
                    element: str = "physical", crit: bool = False,
                    hit_id: str | None = None) -> dict:
        raise NotImplementedError

    def heal(self, combatant_id: str, amount: int) -> dict:
        raise NotImplementedError

    def combatant_report(self, combatant_id: str) -> dict:
        raise NotImplementedError

    def damage_by_attacker(self, attacker_id: str) -> dict:
        raise NotImplementedError

    def damage_by_element(self) -> dict:
        raise NotImplementedError

    def leaderboard(self, limit: int = 5) -> list:
        raise NotImplementedError

    def recent_hits(self, limit: int = 10) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
