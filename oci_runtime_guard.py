"""Container runtime lifecycle guard (implementation skeleton)."""

from __future__ import annotations

import threading


class OciError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class OciRuntimeGuard:
    """Drive container lifecycle transitions and survive runtime restarts."""

    def __init__(self, *, max_containers: int = 64, stop_grace: int = 10,
                 event_limit: int = 100) -> None:
        self.max_containers = max_containers
        self.stop_grace = stop_grace
        self.event_limit = event_limit
        self._lock = threading.RLock()

    def create(self, container_id: str, *, bundle: str, argv=None, env=None,
               labels=None) -> dict:
        raise NotImplementedError

    def start(self, container_id: str) -> dict:
        raise NotImplementedError

    def exec_in(self, container_id: str, argv=None) -> dict:
        raise NotImplementedError

    def pause(self, container_id: str) -> dict:
        raise NotImplementedError

    def resume(self, container_id: str) -> dict:
        raise NotImplementedError

    def kill(self, container_id: str, signal: int | str = 15) -> dict:
        raise NotImplementedError

    def stop(self, container_id: str, *, timeout: int | None = None) -> dict:
        raise NotImplementedError

    def report_exit(self, container_id: str, code: int) -> dict:
        raise NotImplementedError

    def delete(self, container_id: str, *, force: bool = False) -> bool:
        raise NotImplementedError

    def state(self, container_id: str) -> dict:
        raise NotImplementedError

    def list_containers(self, *, label: str | None = None) -> list:
        raise NotImplementedError

    def tick(self, seconds: int = 1) -> int:
        raise NotImplementedError

    def events(self, limit: int | None = None) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
