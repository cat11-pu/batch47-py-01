"""Combat status effect stacking, dispelling and periodic tick resolution."""

from __future__ import annotations


class EffectError(ValueError):
    """Raised when an effect operation cannot be completed."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class StatusEffectResolver:
    """Own effect definitions, combatant targets and the active effect ledger."""

    def __init__(self, *, tick_limit: int = 10, max_targets: int = 1000,
                 clock=None) -> None:
        self.tick_limit = tick_limit
        self.max_targets = max_targets
        self._clock = clock

    def define_effect(self, effect_id: str, *, name: str = "",
                      category: str = "buff", max_stacks: int = 1,
                      duration_ms: int = 1000, stack_policy: str = "refresh",
                      exclusive_group: str | None = None,
                      tick_interval_ms: int = 0,
                      dispellable: bool = True) -> dict:
        raise NotImplementedError

    def remove_effect_definition(self, effect_id: str) -> bool:
        raise NotImplementedError

    def register_target(self, target_id: str, *, max_effects: int = 8,
                        immunity=()) -> dict:
        raise NotImplementedError

    def unregister_target(self, target_id: str) -> bool:
        raise NotImplementedError

    def apply_effect(self, target_id: str, effect_id: str, *, now_ms: int,
                     source: str = "", power: int = 1) -> dict:
        raise NotImplementedError

    def remove_effect(self, target_id: str, effect_id: str, *,
                      now_ms: int | None = None, reason: str = "manual") -> dict:
        raise NotImplementedError

    def dispel(self, target_id: str, *, category: str | None = None,
               limit: int | None = None, now_ms: int) -> list:
        raise NotImplementedError

    def tick(self, target_id: str, *, now_ms: int) -> list:
        raise NotImplementedError

    def active_effects(self, target_id: str, *, now_ms: int) -> list:
        raise NotImplementedError

    def remaining_ms(self, target_id: str, effect_id: str, *, now_ms: int) -> int:
        raise NotImplementedError

    def purge(self, now_ms: int) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError
