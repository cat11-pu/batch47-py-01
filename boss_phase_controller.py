"""Boss encounter phase transitions, ability gating and enrage timing."""

from __future__ import annotations


class PhaseError(ValueError):
    """Raised when an encounter phase operation cannot be completed."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BossPhaseController:
    """Drive a phased boss encounter from damage, time and ability casts."""

    def __init__(self, *, ability_cooldown_ms: int = 5000,
                 soft_enrage_ms: int = 600000,
                 hard_enrage_ms: int = 900000,
                 max_events: int = 500) -> None:
        self.ability_cooldown_ms = ability_cooldown_ms
        self.soft_enrage_ms = soft_enrage_ms
        self.hard_enrage_ms = hard_enrage_ms
        self.max_events = max_events

    def define_phases(self, phases) -> dict:
        raise NotImplementedError

    def engage(self, *, now_ms: int, max_hp: int) -> dict:
        raise NotImplementedError

    def damage(self, amount: int, *, now_ms: int) -> dict:
        raise NotImplementedError

    def set_hp(self, hp: int, *, now_ms: int) -> dict:
        raise NotImplementedError

    def cast(self, ability_id: str, *, now_ms: int) -> dict:
        raise NotImplementedError

    def available_abilities(self, *, now_ms: int) -> list:
        raise NotImplementedError

    def status(self, *, now_ms: int) -> dict:
        raise NotImplementedError

    def reset_encounter(self, *, now_ms: int) -> dict:
        raise NotImplementedError

    def history(self, limit: int = 20) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
