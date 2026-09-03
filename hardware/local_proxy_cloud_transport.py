"""Business-side cloud transport backed by the permanent local agent.

This adapter implements the same ``CloudTransport`` contract as the direct
OneNet adapter, but it never reads a device key, constructs a topic, or opens
an Internet socket.  Outbound calls finish only after the communication agent
has durably accepted the immutable event.  Inbound calls are invoked through
the authenticated business control socket.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import deque
from typing import Any, Callable, Optional

from cloud_transport import (
    CloudEvent,
    CloudEventPlatformResult,
    CloudServiceRequest,
    CloudServiceResponse,
)
from local_control import (
    LocalControlActionError,
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
)


logger = logging.getLogger("local-proxy-cloud-transport")

COMMUNICATION_PROTOCOL_NAME = "ecobin.communication.control"
DEFAULT_COMMUNICATION_SOCKET = "/run/ecobin/communication/control.sock"
MAX_PENDING_SERVICE_REPLIES = 256
MAX_RECENT_DELIVERY_IDENTITIES = 1024


class LocalProxyCloudTransport:
    """Replace direct MQTT with authenticated, durable local handoff."""

    def __init__(
        self,
        socket_path: str = DEFAULT_COMMUNICATION_SOCKET,
        *,
        client: LocalControlClient | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("proxy status poll interval must be positive")
        self._client = client or LocalControlClient(
            socket_path,
            protocol_name=COMMUNICATION_PROTOCOL_NAME,
        )
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._connected = False
        self._state_lock = threading.Lock()
        self._exit_flag = threading.Event()
        self._service_lock = threading.Lock()
        self._pending_service_replies: dict[
            str, tuple[str, dict[str, Any], Callable[[], None] | None]
        ] = {}
        self._completed_service_deliveries: set[str] = set()
        self._completed_service_order: deque[str] = deque()
        self._platform_result_lock = threading.Lock()
        self._delivered_platform_results: set[str] = set()
        self._delivered_platform_result_order: deque[str] = deque()

        self.on_service_request: Optional[Callable] = None
        self.on_legacy_command_received: Optional[Callable] = None
        self.on_event_transport_ack: Optional[Callable] = None
        self.on_event_platform_result: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._connected

    def connect(self) -> bool:
        """Confirm that the permanent agent owns a usable cloud session."""

        if self._exit_flag.is_set():
            return False
        return self._refresh_status()

    def disconnect(self) -> None:
        """Detach this business process without stopping the permanent agent."""

        self._exit_flag.set()
        self._set_connected(False)

    def run_forever(self) -> None:
        """Observe permanent-agent availability until business stops."""

        while not self._exit_flag.wait(self._poll_interval_seconds):
            self._refresh_status()

    def send_event(self, event: CloudEvent) -> bool:
        """Hand an immutable event to the permanent durable outbox."""

        if not isinstance(event, CloudEvent) or self._exit_flag.is_set():
            return False
        try:
            result = self._client.request(
                "SUBMIT_BUSINESS_EVENT",
                {
                    "eventUid": event.event_uid,
                    "eventType": event.event_type,
                    "params": dict(event.params),
                },
            )
        except (LocalControlUnavailable, LocalControlRemoteError, ValueError):
            logger.warning(
                "permanent communication agent did not accept event %s",
                event.event_uid,
            )
            self._set_connected(False)
            return False
        if (
            result.get("durableAccepted") is not True
            or result.get("disposition") not in {"ACCEPTED", "DUPLICATE"}
            or result.get("eventUid") != event.event_uid
        ):
            logger.error(
                "permanent communication agent returned an invalid event receipt"
            )
            return False
        callback = self.on_event_transport_ack
        if callback is not None:
            callback(event.event_uid)
        return True

    def deliver_service_request(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke the business handler and retain its post-reply callback."""

        expected = {
            "deliveryId",
            "requestId",
            "serviceId",
            "params",
            "receivedAt",
            "clockQuality",
        }
        _require_exact_fields(payload, expected, "cloud service delivery")
        request = CloudServiceRequest(
            delivery_id=payload["deliveryId"],
            request_id=payload["requestId"],
            service_id=payload["serviceId"],
            params=payload["params"],
            received_at=payload["receivedAt"],
            clock_quality=payload["clockQuality"],
        )
        digest = _service_request_sha256(request)
        with self._service_lock:
            cached = self._pending_service_replies.get(request.delivery_id)
            if cached is not None:
                if cached[0] != digest:
                    raise LocalControlActionError(
                        "IDEMPOTENCY_CONFLICT",
                        "cloud delivery identity was reused with different content",
                    )
                return {
                    "responseData": dict(cached[1]),
                    "afterReplyToken": request.delivery_id,
                }
            if request.delivery_id in self._completed_service_deliveries:
                raise LocalControlActionError(
                    "DELIVERY_ALREADY_COMPLETED",
                    "cloud delivery was already completed",
                )
            if len(self._pending_service_replies) >= MAX_PENDING_SERVICE_REPLIES:
                raise LocalControlActionError(
                    "BUSINESS_BUSY",
                    "too many cloud replies are awaiting completion",
                )

        handler = self.on_service_request
        if handler is None:
            raise LocalControlActionError(
                "BUSINESS_NOT_READY",
                "business cloud handler is unavailable",
            )
        response = handler(request)
        if not isinstance(response, CloudServiceResponse):
            raise LocalControlActionError(
                "BUSINESS_RESPONSE_INVALID",
                "business cloud handler returned an invalid response",
            )
        response_data = dict(response.data)
        with self._service_lock:
            # Only one local-control request is served at a time in the
            # current process, but keep this check explicit for future server
            # concurrency changes.
            existing = self._pending_service_replies.get(request.delivery_id)
            if existing is not None:
                if existing[0] != digest or existing[1] != response_data:
                    raise LocalControlActionError(
                        "IDEMPOTENCY_CONFLICT",
                        "cloud delivery completed with conflicting content",
                    )
            else:
                self._pending_service_replies[request.delivery_id] = (
                    digest,
                    response_data,
                    response.after_reply,
                )
        return {
            "responseData": response_data,
            "afterReplyToken": request.delivery_id,
        }

    def complete_service_reply(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Run business work only after OneNet reply publication was queued."""

        _require_exact_fields(
            payload,
            {"deliveryId"},
            "cloud service completion",
        )
        delivery_id = payload["deliveryId"]
        if not isinstance(delivery_id, str) or not delivery_id:
            raise LocalControlActionError(
                "REQUEST_INVALID",
                "deliveryId is invalid",
            )
        with self._service_lock:
            if delivery_id in self._completed_service_deliveries:
                return {"disposition": "DUPLICATE"}
            cached = self._pending_service_replies.pop(delivery_id, None)
        if cached is None:
            raise LocalControlActionError(
                "DELIVERY_UNKNOWN",
                "cloud delivery is not awaiting completion",
            )
        callback = cached[2]
        try:
            if callback is not None:
                callback()
        except Exception:
            # The durable command already exists.  Do not allow a wake-up
            # callback failure to make the communication agent re-interpret
            # that accepted command as new physical work.
            logger.exception("business post-reply callback failed")
        finally:
            with self._service_lock:
                _remember_identity(
                    delivery_id,
                    self._completed_service_deliveries,
                    self._completed_service_order,
                )
        return {"disposition": "COMPLETED"}

    def deliver_legacy_command(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_exact_fields(
            payload,
            {"deliveryId", "command"},
            "legacy cloud delivery",
        )
        delivery_id = payload["deliveryId"]
        command = payload["command"]
        if not isinstance(delivery_id, str) or not delivery_id:
            raise LocalControlActionError(
                "REQUEST_INVALID", "deliveryId is invalid"
            )
        if not isinstance(command, dict):
            raise LocalControlActionError(
                "REQUEST_INVALID", "legacy command must be an object"
            )
        handler = self.on_legacy_command_received
        if handler is None:
            raise LocalControlActionError(
                "BUSINESS_NOT_READY",
                "business legacy command handler is unavailable",
            )
        handler(command)
        return {"disposition": "DELIVERED"}

    def deliver_platform_result(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_exact_fields(
            payload,
            {"resultUid", "edgeEventSequence", "code"},
            "cloud event result",
        )
        result_uid = payload["resultUid"]
        if not isinstance(result_uid, str) or not result_uid:
            raise LocalControlActionError(
                "REQUEST_INVALID", "resultUid is invalid"
            )
        with self._platform_result_lock:
            if result_uid in self._delivered_platform_results:
                return {"disposition": "DUPLICATE"}
        result = CloudEventPlatformResult(
            edge_event_sequence=payload["edgeEventSequence"],
            code=payload["code"],
        )
        handler = self.on_event_platform_result
        if handler is None:
            raise LocalControlActionError(
                "BUSINESS_NOT_READY",
                "business event-result handler is unavailable",
            )
        handler(result)
        with self._platform_result_lock:
            _remember_identity(
                result_uid,
                self._delivered_platform_results,
                self._delivered_platform_result_order,
            )
        return {"disposition": "DELIVERED"}

    def _refresh_status(self) -> bool:
        try:
            status = self._client.request("GET_STATUS", {})
            usable = bool(
                status.get("component") == "COMMUNICATION_AGENT"
                and status.get("status") == "READY"
                and status.get("onenetOwnership") == "ENABLED"
                and status.get("cloudConnectionState") == "CONNECTED"
            )
        except (LocalControlUnavailable, LocalControlRemoteError, ValueError):
            usable = False
        self._set_connected(usable)
        return usable

    def _set_connected(self, connected: bool) -> None:
        with self._state_lock:
            changed = self._connected != connected
            self._connected = connected
        if not changed:
            return
        callback = self.on_connected if connected else self.on_disconnected
        if callback is not None:
            try:
                callback()
            except Exception:
                logger.exception("business cloud-state callback failed")


def _service_request_sha256(request: CloudServiceRequest) -> str:
    encoded = json.dumps(
        {
            "deliveryId": request.delivery_id,
            "requestId": request.request_id,
            "serviceId": request.service_id,
            "params": request.params,
            "receivedAt": request.received_at,
            "clockQuality": request.clock_quality,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_exact_fields(
    payload: object,
    expected: set[str],
    description: str,
) -> None:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise LocalControlActionError(
            "REQUEST_INVALID",
            f"{description} fields are invalid",
        )


def _remember_identity(
    identity: str,
    identities: set[str],
    order: deque[str],
) -> None:
    identities.add(identity)
    order.append(identity)
    while len(order) > MAX_RECENT_DELIVERY_IDENTITIES:
        identities.discard(order.popleft())


__all__ = [
    "COMMUNICATION_PROTOCOL_NAME",
    "DEFAULT_COMMUNICATION_SOCKET",
    "LocalProxyCloudTransport",
]
