"""Pack material parameter blocks into a shared uniform buffer."""

from __future__ import annotations

import threading

TYPE_INFO = {
    "float": (4, 4),
    "int": (4, 4),
    "vec2": (8, 8),
    "vec3": (12, 16),
    "vec4": (16, 16),
    "mat4": (64, 16),
}
COMPONENTS = {"float": 1, "int": 1, "vec2": 2, "vec3": 3, "vec4": 4,
              "mat4": 16}


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


class PackError(ValueError):
    """Packing failure carrying a stable code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class UniformBufferPacker:
    """Assign aligned offsets to material blocks and dedupe equal payloads."""

    def __init__(self, *, capacity: int = 1024, max_slots: int = 64,
                 retention_frames: int = 3) -> None:
        self.capacity = capacity
        self.max_slots = max_slots
        self.retention_frames = retention_frames
        self._lock = threading.RLock()

    def define_material(self, material: str, *, params) -> dict:
        raise NotImplementedError

    def layout(self, material: str) -> dict:
        raise NotImplementedError

    def set_values(self, material: str, values: dict, *, frame: int) -> dict:
        raise NotImplementedError

    def pack(self, *, frame: int) -> dict:
        raise NotImplementedError

    def slot_of(self, material: str):
        raise NotImplementedError

    def release(self, material: str) -> bool:
        raise NotImplementedError

    def gc(self, *, frame: int) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
