from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cloud_transport import (
    MAX_CLOUD_SERVICE_PARAMS_BYTES,
    CloudEvent,
    CloudEventPlatformResult,
    CloudServiceRequest,
    CloudServiceResponse,
    CloudTransport,
)


def test_service_request_snapshots_bounded_raw_params() -> None:
    original = {"target": {"uid": "work-1"}, "ports": [1, 2]}
    request = CloudServiceRequest(
        delivery_id="delivery-1",
        request_id="onenet-request-1",
        service_id="startDelivery",
        params=original,
        received_at="2026-09-02T01:02:03.004Z",
        clock_quality="SYNCED",
    )

    original["target"]["uid"] = "changed"

    assert request.params == {
        "target": {"uid": "work-1"},
        "ports": [1, 2],
    }
    with pytest.raises(FrozenInstanceError):
        request.service_id = "changed"  # type: ignore[misc]


def test_service_request_rejects_oversized_params() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        CloudServiceRequest(
            delivery_id="delivery-1",
            request_id="request-1",
            service_id="service-1",
            params={"value": "x" * MAX_CLOUD_SERVICE_PARAMS_BYTES},
            received_at=None,
            clock_quality="UNAVAILABLE",
        )


def test_service_request_rejects_synced_clock_without_timestamp() -> None:
    with pytest.raises(ValueError, match="require received_at"):
        CloudServiceRequest(
            delivery_id="delivery-1",
            request_id="request-1",
            service_id="service-1",
            params={},
            received_at=None,
            clock_quality="SYNCED",
        )


def test_service_response_exposes_data_and_after_reply_callback() -> None:
    calls: list[str] = []
    response = CloudServiceResponse(
        data={"acceptance": "ACCEPTED"},
        after_reply=lambda: calls.append("replied"),
    )

    assert response.data == {"acceptance": "ACCEPTED"}
    assert response.after_reply is not None
    response.after_reply()
    assert calls == ["replied"]


def test_cloud_event_ack_boundary_uses_event_uid() -> None:
    class FakeTransport:
        def __init__(self) -> None:
            self.on_service_request = None
            self.on_legacy_command_received = None
            self.on_event_transport_ack = None
            self.on_event_platform_result = None
            self.on_connected = None
            self.on_disconnected = None
            self._connected = False
            self.events: list[CloudEvent] = []

        @property
        def connected(self) -> bool:
            return self._connected

        def connect(self) -> bool:
            self._connected = True
            return True

        def disconnect(self) -> None:
            self._connected = False

        def run_forever(self) -> None:
            return None

        def send_event(self, event: CloudEvent) -> bool:
            self.events.append(event)
            if self.on_event_transport_ack is not None:
                self.on_event_transport_ack(event.event_uid)
            return True

    transport = FakeTransport()
    acked: list[str] = []
    transport.on_event_transport_ack = acked.append
    event = CloudEvent(
        event_uid="event-1",
        event_type="DELIVERY_COMPLETED",
        params={"payload": {"workUid": "work-1"}},
    )

    assert isinstance(transport, CloudTransport)
    assert transport.send_event(event) is True
    assert transport.events == [event]
    assert acked == ["event-1"]


def test_platform_result_uses_sequence_and_integer_code_only() -> None:
    result = CloudEventPlatformResult(
        edge_event_sequence=42,
        code=200,
    )

    assert result.edge_event_sequence == 42
    assert result.code == 200

    with pytest.raises(ValueError, match="13-digit"):
        CloudEventPlatformResult(edge_event_sequence=0, code=200)
    with pytest.raises(ValueError, match="integer"):
        CloudEventPlatformResult(edge_event_sequence=42, code=True)
