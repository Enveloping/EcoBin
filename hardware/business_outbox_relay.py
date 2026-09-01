"""Relay durable business events through a replaceable cloud transport."""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional

from cloud_transport import (
    CloudEvent,
    CloudEventPlatformResult,
    CloudTransport,
)


logger = logging.getLogger("business-outbox-relay")


class BusinessOutboxRelay:
    """Own event-outbox polling without leaking storage into the transport."""

    def __init__(
        self,
        store,
        transport: CloudTransport,
        *,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        self._store = store
        self._transport = transport
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._exit_flag = threading.Event()
        self._wake_event = threading.Event()
        self._relay_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def on_connected(self) -> None:
        """Resume due business delivery whenever cloud transport is usable."""

        self.start()
        try:
            self.relay_pending_events()
        except Exception:
            logger.exception("initial business outbox relay failed")

    def on_disconnected(self) -> None:
        """Return attempts with lost transport acknowledgements to the outbox."""

        try:
            self._store.recover_sending_events()
        except Exception:
            logger.exception("failed to recover sending business events")

    def start(self) -> None:
        if self._exit_flag.is_set():
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="business-outbox-relay",
        )
        self._thread.start()

    def stop(self) -> None:
        self._exit_flag.set()
        self._wake_event.set()
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)

    def wake(self) -> None:
        self._wake_event.set()

    def handle_transport_ack(self, event_uid: str) -> None:
        """Apply a transport ACK without treating it as business confirmation."""

        event = self._store.get_event(event_uid)
        if event is None:
            # Telemetry snapshots also use stable event identifiers but are
            # not rows in the durable business outbox.
            return
        if event["event_type"] == "BUSINESS_CONFIRMATION_RECEIPT":
            self._store.mark_control_receipt_published(event_uid)
        else:
            # A transport ACK only proves handoff to the cloud boundary.
            # Reliable facts remain pending until the backend sends its
            # explicit business confirmation.
            self._store.mark_event_pending_retry(event_uid)

    def handle_platform_result(
        self,
        result: CloudEventPlatformResult,
    ) -> None:
        """Persist a normalized cloud-platform result for one event."""

        event_uid = self._store.record_event_platform_reply(
            result.edge_event_sequence,
            result.code,
        )
        if event_uid and result.code not in (0, 200):
            logger.error(
                "cloud event rejected: event=%s code=%d",
                event_uid,
                result.code,
            )
        elif event_uid:
            logger.debug("cloud event accepted: event=%s", event_uid)
        elif result.code not in (0, 200):
            logger.warning(
                "cloud telemetry event rejected: sequence=%s code=%d",
                result.edge_event_sequence,
                result.code,
            )

    def relay_pending_events(self) -> None:
        """Submit due events once; retries remain governed by EdgeStore."""

        if not self._transport.connected:
            return
        with self._relay_lock:
            events = self._store.list_pending_events(limit=10)
            for event in events:
                if self._exit_flag.is_set():
                    return
                event_uid = event["event_uid"]
                try:
                    params = json.loads(event["payload_json"])
                    # Claim before handing bytes to the transport.  This
                    # closes the old window in which concurrent relay wakes
                    # could submit the same PENDING row twice, and ensures an
                    # extremely fast transport ACK always sees SENDING.
                    claimed = self._store.mark_event_sending(
                        event_uid,
                        None,
                    )
                    if not claimed:
                        continue
                    queued = self._transport.send_event(
                        CloudEvent(
                            event_uid=event_uid,
                            event_type=event["event_type"],
                            params=params,
                        )
                    )
                    if not queued:
                        self._store.mark_event_pending_retry(event_uid)
                except Exception as error:
                    logger.error("事件中继异常: %s", error)
                    self._store.mark_event_pending_retry(event_uid)

    def _run(self) -> None:
        while not self._exit_flag.is_set():
            self._wake_event.wait(self._poll_interval_seconds)
            self._wake_event.clear()
            if self._exit_flag.is_set():
                break
            if self._transport.connected:
                try:
                    self.relay_pending_events()
                except Exception:
                    logger.exception("business outbox relay loop failed")
