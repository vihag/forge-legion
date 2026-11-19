"""Background stream ticker for mock Splunk (single-threaded)."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.mock_splunk.store import SplunkStore

EmitCallback = Callable[[], None]


class StreamEngine:
    def __init__(self, store: SplunkStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._running = False

    def _interval_s(self) -> float:
        status = self._store.stream_status()
        if status.get("mode") == "script":
            return 60.0
        rate = int(status.get("rate_per_min", os.environ.get("STREAM_RATE_PER_MIN", "3")))
        if rate <= 0:
            return 60.0
        return 60.0 / rate

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._schedule()
            return True

    def stop(self) -> bool:
        with self._lock:
            was_running = self._running
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            return was_running

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _schedule(self) -> None:
        if not self._running:
            return
        interval = self._interval_s()
        self._timer = threading.Timer(interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        with self._lock:
            if not self._running:
                return
        self._store.emit_stream_tick()
        with self._lock:
            if self._running:
                self._schedule()