"""Periodically refresh the fixed-frame MCU's idle-screen QR URL."""
from __future__ import annotations

import hashlib
import logging
import math
import time
from collections.abc import Callable

logger = logging.getLogger("device-entry-url-refresh")

DEFAULT_RETRY_SECONDS = 5.0


class DeviceEntryUrlRefreshController:
    """Write the latest entry URL while the fixed-frame MCU is idle.

    ``poll`` runs in the existing command-consumer thread.  This keeps the A0
    write serialized with physical commands and avoids changing the MCU screen
    while a delivery or clean work slot is active.  ``url_provider`` is a
    replaceable boundary so a future implementation can generate a fresh,
    server-verifiable signed URL for every interval.
    """

    def __init__(
        self,
        store,
        uart_link,
        *,
        interval_seconds: float,
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        url_provider: Callable[[], str | None] | None = None,
    ):
        interval = float(interval_seconds)
        retry = float(retry_seconds)
        if (
            not math.isfinite(interval)
            or not math.isfinite(retry)
            or interval <= 0
            or retry <= 0
        ):
            raise ValueError(
                "device entry URL refresh intervals must be finite and positive"
            )
        self._store = store
        self._uart = uart_link
        self._interval_seconds = interval
        self._retry_seconds = retry
        self._monotonic = monotonic
        self._url_provider = url_provider or self._stored_url
        self._next_send_at: float | None = None

    @property
    def next_send_at(self) -> float | None:
        return self._next_send_at

    def poll(self) -> dict:
        if not getattr(self._uart, "compatibility_mode", False):
            self._next_send_at = None
            return self._outcome("DISABLED")

        now = self._monotonic()
        if self._next_send_at is None:
            self._next_send_at = now + self._interval_seconds
            logger.info(
                "periodic device entry URL refresh armed: interval=%.0fs",
                self._interval_seconds,
            )
            return self._outcome("ARMED")
        if now < self._next_send_at:
            return self._outcome("WAITING")

        if self._store.get_work_slot() is not None:
            self._schedule_retry(now)
            logger.debug(
                "periodic device entry URL refresh deferred because work is active"
            )
            return self._outcome("BUSY")
        if not getattr(self._uart, "is_open", False):
            self._schedule_retry(now)
            logger.debug(
                "periodic device entry URL refresh deferred because UART is closed"
            )
            return self._outcome("DISCONNECTED")

        try:
            url = self._url_provider()
        except Exception as error:
            self._schedule_retry(self._monotonic())
            logger.warning(
                "periodic device entry URL provider failed; "
                "retrying in %.0fs: %s",
                self._retry_seconds,
                error,
            )
            return self._outcome("RETRY_SCHEDULED", attempted=True)

        if url is None:
            self._next_send_at = now + self._interval_seconds
            logger.debug("no stored device entry URL available for periodic refresh")
            return self._outcome("NO_URL")

        try:
            url_sha256 = hashlib.sha256(url.encode("ascii")).hexdigest()
            self._uart.send_device_entry_url(url)
        except Exception as error:
            self._schedule_retry(self._monotonic())
            logger.warning(
                "periodic device entry URL UART write failed; "
                "retrying in %.0fs: %s",
                self._retry_seconds,
                error,
            )
            return self._outcome("RETRY_SCHEDULED", attempted=True)

        sent_at = self._monotonic()
        self._next_send_at = sent_at + self._interval_seconds
        logger.info(
            "periodic device entry URL written to UART (fire-and-forget): "
            "sha256=%s next_in=%.0fs",
            url_sha256,
            self._interval_seconds,
        )
        return self._outcome("SENT", attempted=True)

    def _stored_url(self) -> str | None:
        record = self._store.get_device_entry_url()
        return None if record is None else record["deviceEntryUrl"]

    def _schedule_retry(self, now: float) -> None:
        self._next_send_at = now + self._retry_seconds

    @staticmethod
    def _outcome(status: str, *, attempted: bool = False) -> dict:
        return {"attempted": attempted, "status": status}
