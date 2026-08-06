"""Estruturas leves para o pipeline de frames do worker.

O worker publica apenas o frame mais recente em um mailbox por camera para
evitar backlog quando a inferencia fica mais lenta que a captura.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Condition


@dataclass(slots=True)
class QueuedFrame:
    frame: object
    captured_at: datetime
    read_ms: float
    reason: str | None = None
    metadata: dict | None = None


class LatestFrameMailbox:
    """Buffer de 1 item que sempre preserva o frame mais recente."""

    def __init__(self, camera_id: int, worker_mode: str = "normal"):
        self.camera_id = int(camera_id)
        self.worker_mode = worker_mode
        self._condition = Condition()
        self._item: QueuedFrame | None = None
        self._closed = False
        self.dropped_count = 0
        self.put_count = 0
        self.get_count = 0

    def put_latest(self, item: QueuedFrame) -> None:
        with self._condition:
            if self._closed:
                return

            if self._item is not None:
                self.dropped_count += 1

            self._item = item
            self.put_count += 1
            self._condition.notify_all()

    def get_latest(self, timeout: float | None = None) -> QueuedFrame | None:
        with self._condition:
            if self._item is None and not self._closed:
                self._condition.wait(timeout=timeout)

            if self._item is None:
                return None

            item = self._item
            self._item = None
            self.get_count += 1
            return item

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def is_closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def is_empty(self) -> bool:
        with self._condition:
            return self._item is None
