"""Outbound routine webhook (fire-and-forget with bounded retries)."""

from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]


@dataclass(frozen=True)
class WebhookPayload:
    case_id: int
    alert_id: str
    severity: str
    route: str
    rule: str

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "alert_id": self.alert_id,
            "severity": self.severity,
            "route": self.route,
            "rule": self.rule,
        }


def _is_active() -> bool:
    flag = os.environ.get("ROUTINE_WEBHOOK_ENABLED", "0").strip()
    url = os.environ.get("ROUTINE_WEBHOOK_URL", "").strip()
    key = os.environ.get("ROUTINE_WEBHOOK_KEY", "").strip()
    return flag == "1" and bool(url) and bool(key)


def _disabled_reason_line() -> str:
    flag = os.environ.get("ROUTINE_WEBHOOK_ENABLED", "0").strip()
    url = os.environ.get("ROUTINE_WEBHOOK_URL", "").strip()
    key = os.environ.get("ROUTINE_WEBHOOK_KEY", "").strip()
    enabled = "1" if flag == "1" else "0"
    if flag != "1":
        return f"enabled={enabled}"
    if not url:
        return "url=empty"
    if not key:
        return "key=empty"
    return "enabled=1"


class RoutineWebhook:
    """Non-blocking delivery with at most one attempt series per case_id per process."""

    _MAX_QUEUE = 100
    _MAX_ATTEMPTS = 4
    _BACKOFF_S = (1.0, 2.0, 4.0)
    _TIMEOUT_S = 5.0

    def __init__(
        self,
        *,
        url: str,
        key: str,
        transport: httpx.BaseTransport | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._url = url
        self._key = key
        self._transport = transport
        self._sleep: SleepFn = sleep if sleep is not None else _default_sleep
        self._sent: set[int] = set()
        self._queue: queue.Queue[WebhookPayload | None] = queue.Queue(maxsize=self._MAX_QUEUE)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @classmethod
    def from_env(
        cls,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: SleepFn | None = None,
    ) -> RoutineWebhook | None:
        if not _is_active():
            logger.info("routine webhook: disabled (%s)", _disabled_reason_line())
            return None
        url = os.environ.get("ROUTINE_WEBHOOK_URL", "").strip()
        key = os.environ.get("ROUTINE_WEBHOOK_KEY", "").strip()
        return cls(url=url, key=key, transport=transport, sleep=sleep)

    def start(self) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._thread = threading.Thread(target=self._worker, name="routine-webhook", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=10.0)

    def enqueue(self, payload: WebhookPayload) -> None:
        if payload.case_id in self._sent:
            return
        self._sent.add(payload.case_id)
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            dropped = self._drop_oldest()
            if dropped is not None:
                logger.warning("routine webhook queue full, dropped case_id=%s", dropped.case_id)
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                logger.warning(
                    "routine webhook queue full, dropped case_id=%s",
                    payload.case_id,
                )

    def _drop_oldest(self) -> WebhookPayload | None:
        try:
            old = self._queue.get_nowait()
        except queue.Empty:
            return None
        if old is None:
            return None
        self._sent.discard(old.case_id)
        return old

    def _worker(self) -> None:
        client = httpx.Client(transport=self._transport, timeout=self._TIMEOUT_S)
        try:
            while not self._stop.is_set():
                try:
                    item = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                self._deliver(client, item)
        finally:
            if self._transport is None:
                client.close()

    def _deliver(self, client: httpx.Client, payload: WebhookPayload) -> None:
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        body = payload.to_json()
        attempt = 0
        while attempt < self._MAX_ATTEMPTS:
            attempt += 1
            status: int | Literal["transport"] = "transport"
            try:
                response = client.post(self._url, json=body, headers=headers)
                status = response.status_code
                if 200 <= status < 300:
                    logger.info(
                        "routine webhook delivered case_id=%s status=%s attempt=%s",
                        payload.case_id,
                        status,
                        attempt,
                    )
                    return
                if status == 429 or status >= 500:
                    if attempt < self._MAX_ATTEMPTS:
                        self._sleep(self._BACKOFF_S[min(attempt - 1, len(self._BACKOFF_S) - 1)])
                        continue
                    logger.warning(
                        "routine webhook failed case_id=%s status=%s attempt=%s",
                        payload.case_id,
                        status,
                        attempt,
                    )
                    return
                logger.warning(
                    "routine webhook failed case_id=%s status=%s attempt=%s",
                    payload.case_id,
                    status,
                    attempt,
                )
                return
            except httpx.HTTPError as exc:
                logger.warning(
                    "routine webhook transport case_id=%s attempt=%s exc=%s",
                    payload.case_id,
                    attempt,
                    type(exc).__name__,
                )
                if attempt < self._MAX_ATTEMPTS:
                    self._sleep(self._BACKOFF_S[min(attempt - 1, len(self._BACKOFF_S) - 1)])
                    continue
                return


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)