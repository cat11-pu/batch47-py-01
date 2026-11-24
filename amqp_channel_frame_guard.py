"""AMQP 0-9-1 frame, channel and publisher-confirm guard."""

from __future__ import annotations

import threading

SNAPSHOT_VERSION = 1

FRAME_TYPES = ("method", "header", "body", "heartbeat")

FRAME_MAX_FLOOR = 4096

STAT_KEYS = ("connections_opened", "channels_opened", "frames", "published",
             "confirmed")


class AmqpError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AmqpChannelFrameGuard:
    """Validate brokers frames and track confirms per channel."""

    def __init__(self, *, frame_max: int = 131072, channel_max: int = 32,
                 heartbeat: int = 60, max_body_bytes: int = 1048576,
                 max_connections: int = 1000, clock=None) -> None:
        self.frame_max = frame_max
        self.channel_max = channel_max
        self.default_heartbeat = heartbeat
        self.max_body_bytes = max_body_bytes
        self.max_connections = max_connections
        self._clock = clock
        self._lock = threading.RLock()

    def open_connection(self, conn_id: str, *, frame_max: int | None = None,
                        heartbeat: int | None = None, now=None) -> dict:
        raise NotImplementedError

    def close_connection(self, conn_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def purge(self, now=None) -> dict:
        raise NotImplementedError

    def open_channel(self, conn_id: str, *, channel_id: int | None = None,
                     now=None) -> dict:
        raise NotImplementedError

    def close_channel(self, conn_id: str, channel_id: int, *, now=None) -> dict:
        raise NotImplementedError

    def frame(self, conn_id: str, *, channel_id: int, frame_type: str,
              payload_size: int, now=None) -> dict:
        raise NotImplementedError

    def assemble(self, conn_id: str, channel_id: int, *, parts,
                 declared_size: int | None = None, now=None) -> dict:
        raise NotImplementedError

    def publish(self, conn_id: str, channel_id: int, *, exchange: str,
                routing_key: str, body: str, now=None) -> dict:
        raise NotImplementedError

    def confirm(self, conn_id: str, channel_id: int, *, delivery_tag: int,
                now=None) -> dict:
        raise NotImplementedError

    def heartbeat(self, conn_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def check_timeout(self, conn_id: str, *, now=None) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def reset_stats(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
