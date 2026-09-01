"""Transport-neutral contracts between edge business code and the cloud.

This module intentionally contains no OneNet topic construction, MQTT SDK
types, device credentials, or broker delivery identifiers.  A direct OneNet
adapter and a future local communication proxy can both implement the same
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable, Literal, Mapping, Protocol, TypeAlias, runtime_checkable


ClockQuality: TypeAlias = Literal["SYNCED", "ESTIMATED", "UNAVAILABLE"]
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# An inbound service invocation must be small enough to validate and persist
# without allowing an untrusted cloud message to consume unbounded memory.
MAX_CLOUD_SERVICE_PARAMS_BYTES = 64 * 1024
MAX_CLOUD_JSON_DEPTH = 32

AfterReply: TypeAlias = Callable[[], None]
CloudServiceHandler: TypeAlias = Callable[
    ["CloudServiceRequest"], "CloudServiceResponse"
]
CloudEventTransportAckHandler: TypeAlias = Callable[[str], None]
CloudConnectionHandler: TypeAlias = Callable[[], None]
CloudLegacyCommandHandler: TypeAlias = Callable[[dict[str, JsonValue]], None]


def _require_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")


def _copy_json_object(
    value: object,
    *,
    field_name: str,
    maximum_bytes: int | None = None,
) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")

    plain_value = dict(value)
    _validate_json_value(plain_value, field_name=field_name, depth=0)
    try:
        encoded = json.dumps(
            plain_value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must contain JSON values") from error
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        raise ValueError(
            f"{field_name} exceeds {maximum_bytes} encoded bytes"
        )

    # The JSON round trip gives the receiver a private tree.  Mutating a
    # broker decoder's original dict after delivery cannot alter the request
    # or event already handed to business code.
    copied = json.loads(encoded.decode("utf-8"))
    if not isinstance(copied, dict):  # Defensive; the root was checked above.
        raise ValueError(f"{field_name} must be a JSON object")
    return copied


def _validate_json_value(
    value: object,
    *,
    field_name: str,
    depth: int,
) -> None:
    if depth > MAX_CLOUD_JSON_DEPTH:
        raise ValueError(
            f"{field_name} exceeds JSON depth {MAX_CLOUD_JSON_DEPTH}"
        )
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        # json.dumps(..., allow_nan=False) performs the finite-value check.
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(
                item,
                field_name=field_name,
                depth=depth + 1,
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            _validate_json_value(
                item,
                field_name=field_name,
                depth=depth + 1,
            )
        return
    raise ValueError(f"{field_name} must contain JSON values")


@dataclass(frozen=True, slots=True)
class CloudServiceRequest:
    """One transport delivery of a cloud service invocation.

    ``delivery_id`` identifies this local delivery attempt and is distinct
    from OneNet's stable request identifier.  ``received_at`` is wall-clock
    evidence sampled together with ``clock_quality``; consumers must only
    treat it as authoritative when the quality is ``SYNCED``.
    """

    delivery_id: str
    request_id: str
    service_id: str
    params: Mapping[str, JsonValue]
    received_at: str | None
    clock_quality: ClockQuality

    def __post_init__(self) -> None:
        _require_identifier(self.delivery_id, "delivery_id")
        _require_identifier(self.request_id, "request_id")
        _require_identifier(self.service_id, "service_id")
        if self.clock_quality not in {
            "SYNCED",
            "ESTIMATED",
            "UNAVAILABLE",
        }:
            raise ValueError("clock_quality is invalid")
        if self.received_at is not None:
            _require_identifier(self.received_at, "received_at")
        if self.clock_quality == "SYNCED" and self.received_at is None:
            raise ValueError("SYNCED requests require received_at")
        object.__setattr__(
            self,
            "params",
            _copy_json_object(
                self.params,
                field_name="params",
                maximum_bytes=MAX_CLOUD_SERVICE_PARAMS_BYTES,
            ),
        )


@dataclass(frozen=True, slots=True)
class CloudServiceResponse:
    """Business response data plus work to run after the reply is queued."""

    data: Mapping[str, JsonValue]
    after_reply: AfterReply | None = None

    def __post_init__(self) -> None:
        if self.after_reply is not None and not callable(self.after_reply):
            raise ValueError("after_reply must be callable")
        object.__setattr__(
            self,
            "data",
            _copy_json_object(self.data, field_name="data"),
        )


@dataclass(frozen=True, slots=True)
class CloudEvent:
    """Complete business event submitted through a cloud transport."""

    event_uid: str
    event_type: str
    params: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_identifier(self.event_uid, "event_uid")
        _require_identifier(self.event_type, "event_type")
        object.__setattr__(
            self,
            "params",
            _copy_json_object(self.params, field_name="params"),
        )


@dataclass(frozen=True, slots=True)
class CloudEventPlatformResult:
    """Transport-neutral OneNet acceptance result for one event sequence."""

    edge_event_sequence: int
    code: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.edge_event_sequence, bool)
            or not isinstance(self.edge_event_sequence, int)
            or not 1 <= self.edge_event_sequence <= 9_999_999_999_999
        ):
            raise ValueError(
                "edge_event_sequence must fit a 13-digit transport id"
            )
        if isinstance(self.code, bool) or not isinstance(self.code, int):
            raise ValueError("code must be an integer")


CloudEventPlatformResultHandler: TypeAlias = Callable[
    [CloudEventPlatformResult], None
]


@runtime_checkable
class CloudTransport(Protocol):
    """Stable cloud boundary consumed by the edge runtime."""

    on_service_request: CloudServiceHandler | None
    on_legacy_command_received: CloudLegacyCommandHandler | None
    on_event_transport_ack: CloudEventTransportAckHandler | None
    on_event_platform_result: CloudEventPlatformResultHandler | None
    on_connected: CloudConnectionHandler | None
    on_disconnected: CloudConnectionHandler | None

    @property
    def connected(self) -> bool:
        """Whether the transport currently has a usable cloud session."""
        ...

    def connect(self) -> bool:
        """Start or restore the cloud session."""
        ...

    def disconnect(self) -> None:
        """Stop the cloud session and release transport resources."""
        ...

    def run_forever(self) -> None:
        """Maintain the transport until it is explicitly disconnected."""
        ...

    def send_event(self, event: CloudEvent) -> bool:
        """Queue an event; return only whether transport accepted the send."""
        ...


__all__ = [
    "AfterReply",
    "ClockQuality",
    "CloudConnectionHandler",
    "CloudEvent",
    "CloudEventPlatformResult",
    "CloudEventPlatformResultHandler",
    "CloudEventTransportAckHandler",
    "CloudLegacyCommandHandler",
    "CloudServiceHandler",
    "CloudServiceRequest",
    "CloudServiceResponse",
    "CloudTransport",
    "JsonScalar",
    "JsonValue",
    "MAX_CLOUD_JSON_DEPTH",
    "MAX_CLOUD_SERVICE_PARAMS_BYTES",
]
