"""Permanent OneNet routing between cloud transport and local processes."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cloud_transport import (
    CloudEvent,
    CloudEventPlatformResult,
    CloudServiceRequest,
    CloudServiceResponse,
    CloudTransport,
)
from communication_store import CommunicationStore
from local_control import (
    LocalControlActionError,
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
)


logger = logging.getLogger("communication-router")

BUSINESS_PROTOCOL_NAME = "ecobin.business.control"
DEFAULT_BUSINESS_SOCKET = "/run/ecobin/business/control.sock"
DEFAULT_PLATFORM_RESULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_SECONDS = 2.0


class CommunicationRouter:
    """Own one cloud connection and a durable outbound delivery loop."""

    def __init__(
        self,
        store: CommunicationStore,
        transport: CloudTransport,
        *,
        business_socket: str = DEFAULT_BUSINESS_SOCKET,
        business_client: LocalControlClient | None = None,
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        platform_result_timeout_seconds: float = (
            DEFAULT_PLATFORM_RESULT_TIMEOUT_SECONDS
        ),
        utc_now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if retry_seconds <= 0:
            raise ValueError("communication retry interval must be positive")
        if platform_result_timeout_seconds <= 0:
            raise ValueError("platform result timeout must be positive")
        self.store = store
        self.transport = transport
        self._business = business_client or LocalControlClient(
            business_socket,
            protocol_name=BUSINESS_PROTOCOL_NAME,
        )
        self._retry_seconds = float(retry_seconds)
        self._platform_result_timeout_seconds = float(
            platform_result_timeout_seconds
        )
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._exit_flag = threading.Event()
        self._wake = threading.Event()
        self._cloud_thread: threading.Thread | None = None
        self._outbound_thread: threading.Thread | None = None
        self._failure: BaseException | None = None
        self._inflight_lock = threading.Lock()
        self._inflight_deadlines: dict[str, float] = {}

        self.transport.on_service_request = self.handle_service_request
        self.transport.on_legacy_command_received = self.handle_legacy_command
        self.transport.on_event_transport_ack = self.handle_transport_ack
        self.transport.on_event_platform_result = self.handle_platform_result
        self.transport.on_connected = self.handle_connected
        self.transport.on_disconnected = self.handle_disconnected

    @property
    def connected(self) -> bool:
        return self.transport.connected

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    @property
    def is_running(self) -> bool:
        threads = (self._cloud_thread, self._outbound_thread)
        return all(thread is not None and thread.is_alive() for thread in threads)

    def start(self) -> None:
        if self.is_running:
            return
        self._exit_flag.clear()
        self._wake.clear()
        self._failure = None
        recovered = self.store.recover_proxy_sends()
        if recovered:
            logger.warning(
                "recovered %d interrupted permanent cloud send(s)", recovered
            )
        self._cloud_thread = threading.Thread(
            target=self._run_cloud,
            daemon=True,
            name="communication-cloud",
        )
        self._outbound_thread = threading.Thread(
            target=self._run_outbound,
            daemon=True,
            name="communication-outbound",
        )
        self._cloud_thread.start()
        self._outbound_thread.start()

    def stop(self) -> None:
        self._exit_flag.set()
        self._wake.set()
        try:
            self.transport.disconnect()
        except Exception:
            logger.exception("permanent cloud transport did not stop cleanly")
        for thread in (self._cloud_thread, self._outbound_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=5.0)
                if thread.is_alive():
                    raise RuntimeError(
                        "permanent communication router did not stop in time"
                    )
        self._cloud_thread = None
        self._outbound_thread = None

    def submit_business_event(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {
            "eventUid",
            "eventType",
            "params",
        }:
            raise LocalControlActionError(
                "REQUEST_INVALID",
                "business event fields are invalid",
            )
        try:
            result = self.store.submit_proxy_outbound_event(
                payload["eventUid"],
                payload["eventType"],
                payload["params"],
            )
        except (TypeError, ValueError) as error:
            raise LocalControlActionError(
                "REQUEST_INVALID",
                "business event is invalid",
            ) from error
        if result["disposition"] == "CONFLICT":
            raise LocalControlActionError(
                "IDEMPOTENCY_CONFLICT",
                "event identity was reused with different content",
            )
        self._wake.set()
        return {
            "eventUid": payload["eventUid"],
            "disposition": result["disposition"],
            "dispatchGeneration": result["dispatchGeneration"],
            "durableAccepted": True,
        }

    def handle_service_request(
        self,
        request: CloudServiceRequest,
    ) -> CloudServiceResponse:
        command_uid = _extract_command_uid(request.params)
        received = self.store.receive_proxy_inbound_command(
            command_uid,
            request.service_id,
            dict(request.params),
        )
        disposition = received["disposition"]
        if disposition == "CONFLICT":
            raise RuntimeError(
                "permanent inbound command identity conflicts with prior content"
            )
        if disposition == "DUPLICATE_BUSINESS_ACCEPTED":
            response_data = received["responseData"]
            if not isinstance(response_data, dict):
                raise RuntimeError(
                    "accepted permanent command has no durable business response"
                )
            return CloudServiceResponse(data=response_data)

        local_payload = {
            "deliveryId": request.delivery_id,
            "requestId": request.request_id,
            "serviceId": request.service_id,
            "params": dict(request.params),
            "receivedAt": request.received_at,
            "clockQuality": request.clock_quality,
        }
        delivered = self._business.request(
            "DELIVER_CLOUD_SERVICE_REQUEST",
            local_payload,
        )
        if set(delivered) != {"responseData", "afterReplyToken"}:
            raise RuntimeError("business returned a malformed cloud response")
        response_data = delivered["responseData"]
        token = delivered["afterReplyToken"]
        if not isinstance(response_data, dict) or token != request.delivery_id:
            raise RuntimeError("business returned a malformed cloud response")
        accepted = self.store.mark_proxy_inbound_business_accepted(
            command_uid,
            received["contentSha256"],
            response_data,
        )
        if accepted not in {"ACCEPTED", "DUPLICATE"}:
            raise RuntimeError(
                "permanent command acceptance could not be confirmed"
            )

        def after_reply() -> None:
            try:
                self._business.request(
                    "COMPLETE_CLOUD_SERVICE_REPLY",
                    {"deliveryId": token},
                )
            except (LocalControlUnavailable, LocalControlRemoteError, ValueError):
                # The business command is already durable.  Its consumer polls
                # the inbox as well as receiving this low-latency wake-up.
                logger.warning(
                    "business post-reply completion was not confirmed for %s",
                    command_uid,
                )

        return CloudServiceResponse(
            data=response_data,
            after_reply=after_reply,
        )

    def handle_legacy_command(self, payload: dict[str, Any]) -> None:
        command_uid = _extract_command_uid(payload)
        received = self.store.receive_proxy_inbound_command(
            command_uid,
            "legacyCommand",
            payload,
        )
        if received["disposition"] == "CONFLICT":
            raise RuntimeError("legacy command identity conflicts with prior content")
        if received["disposition"] == "DUPLICATE_BUSINESS_ACCEPTED":
            return
        delivery_id = str(uuid.uuid4())
        result = self._business.request(
            "DELIVER_LEGACY_CLOUD_COMMAND",
            {"deliveryId": delivery_id, "command": payload},
        )
        if result.get("disposition") not in {"DELIVERED", "DUPLICATE"}:
            raise RuntimeError("business did not accept legacy cloud delivery")
        accepted = self.store.mark_proxy_inbound_business_accepted(
            command_uid,
            received["contentSha256"],
            {"disposition": "DELIVERED"},
        )
        if accepted not in {"ACCEPTED", "DUPLICATE"}:
            raise RuntimeError("legacy command acceptance could not be confirmed")

    def handle_transport_ack(self, event_uid: str) -> None:
        disposition = self.store.acknowledge_proxy_transport(event_uid)
        if disposition == "CONFLICT":
            logger.error("cloud acknowledgement conflicts for event %s", event_uid)

    def handle_platform_result(
        self,
        result: CloudEventPlatformResult,
    ) -> None:
        stored = self.store.record_proxy_platform_result(
            result.edge_event_sequence,
            result.code,
        )
        if stored is None:
            logger.debug(
                "ignored platform result for non-durable telemetry sequence %s",
                result.edge_event_sequence,
            )
            return
        with self._inflight_lock:
            self._inflight_deadlines.pop(stored["eventUid"], None)
        self._wake.set()

    def handle_connected(self) -> None:
        self._wake.set()

    def handle_disconnected(self) -> None:
        try:
            self.store.recover_proxy_sends()
        except Exception:
            logger.exception("failed to recover interrupted cloud sends")
        with self._inflight_lock:
            self._inflight_deadlines.clear()
        self._wake.set()

    def _run_cloud(self) -> None:
        try:
            self.transport.run_forever()
        except BaseException as error:
            self._failure = error
            self._exit_flag.set()
            self._wake.set()
            logger.exception("permanent cloud transport stopped unexpectedly")

    def _run_outbound(self) -> None:
        try:
            while not self._exit_flag.is_set():
                self._expire_inflight_results()
                self._deliver_platform_results()
                progressed = False
                if self.transport.connected:
                    claimed = self.store.claim_next_proxy_outbound_event()
                    if claimed is not None:
                        progressed = True
                        with self._inflight_lock:
                            self._inflight_deadlines[claimed["eventUid"]] = (
                                self._monotonic()
                                + self._platform_result_timeout_seconds
                            )
                        queued = self.transport.send_event(
                            CloudEvent(
                                event_uid=claimed["eventUid"],
                                event_type=claimed["eventType"],
                                params=claimed["params"],
                            )
                        )
                        if not queued:
                            with self._inflight_lock:
                                self._inflight_deadlines.pop(
                                    claimed["eventUid"], None
                                )
                            self.store.fail_proxy_send(
                                claimed["eventUid"],
                                retry_not_before=(
                                    self._aware_utc_now()
                                    + timedelta(seconds=self._retry_seconds)
                                ),
                            )
                if progressed:
                    continue
                self._wake.wait(min(self._retry_seconds, 0.5))
                self._wake.clear()
        except BaseException as error:
            self._failure = error
            self._exit_flag.set()
            logger.exception("permanent outbound router stopped unexpectedly")

    def _deliver_platform_results(self) -> None:
        for result in self.store.list_undelivered_proxy_platform_results():
            if self._exit_flag.is_set():
                return
            try:
                response = self._business.request(
                    "DELIVER_CLOUD_EVENT_RESULT",
                    {
                        "resultUid": result["resultUid"],
                        "edgeEventSequence": result["edgeEventSequence"],
                        "code": result["code"],
                    },
                )
            except (LocalControlUnavailable, LocalControlRemoteError, ValueError):
                return
            if response.get("disposition") not in {"DELIVERED", "DUPLICATE"}:
                return
            self.store.mark_proxy_platform_result_delivered(
                result["resultUid"]
            )

    def _expire_inflight_results(self) -> None:
        now = self._monotonic()
        with self._inflight_lock:
            expired = [
                event_uid
                for event_uid, deadline in self._inflight_deadlines.items()
                if deadline <= now
            ]
            for event_uid in expired:
                self._inflight_deadlines.pop(event_uid, None)
        for event_uid in expired:
            self.store.fail_proxy_send(
                event_uid,
                retry_not_before=(
                    self._aware_utc_now()
                    + timedelta(seconds=self._retry_seconds)
                ),
                outcome="RESULT_UNKNOWN",
            )

    def _aware_utc_now(self) -> datetime:
        value = self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("communication router clock must be timezone-aware")
        return value.astimezone(timezone.utc)


def _extract_command_uid(value: object) -> str:
    """Extract the transport-level stable identity without decoding business."""

    found: set[str] = set()
    pending: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while pending:
        current, depth = pending.pop()
        visited += 1
        if visited > 4096 or depth > 32:
            raise ValueError("cloud command envelope is too complex")
        if isinstance(current, dict):
            for key, child in current.items():
                if key == "commandUid":
                    if not isinstance(child, str):
                        raise ValueError("commandUid must be a lowercase UUIDv4")
                    found.add(child)
                if isinstance(child, (dict, list)):
                    pending.append((child, depth + 1))
        elif isinstance(current, list):
            for child in current:
                if isinstance(child, (dict, list)):
                    pending.append((child, depth + 1))
    if not found and isinstance(value, dict):
        legacy_identity = value.get("id")
        if isinstance(legacy_identity, str):
            found.add(legacy_identity)
    if len(found) != 1:
        raise ValueError("cloud command requires one stable commandUid")
    command_uid = next(iter(found))
    try:
        parsed = uuid.UUID(command_uid)
    except (ValueError, AttributeError) as error:
        raise ValueError("commandUid must be a lowercase UUIDv4") from error
    if parsed.version != 4 or str(parsed) != command_uid:
        raise ValueError("commandUid must be a lowercase UUIDv4")
    return command_uid


__all__ = [
    "BUSINESS_PROTOCOL_NAME",
    "CommunicationRouter",
    "DEFAULT_BUSINESS_SOCKET",
]
