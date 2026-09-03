from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from cloud_transport import (
    CloudEventPlatformResult,
    CloudServiceRequest,
)
from communication_router import CommunicationRouter, _extract_command_uid
from communication_store import CommunicationStore
from local_control import LocalControlActionError


NOW = datetime(2030, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
COMMAND_UID = "10000000-0000-4000-8000-000000000001"
EVENT_UID = "20000000-0000-4000-8000-000000000001"


class FakeTransport:
    def __init__(self):
        self.connected = True
        self.sent = []
        self.send_result = True
        self.stopped = threading.Event()
        self.on_service_request = None
        self.on_legacy_command_received = None
        self.on_event_transport_ack = None
        self.on_event_platform_result = None
        self.on_connected = None
        self.on_disconnected = None

    def connect(self):
        return self.connected

    def disconnect(self):
        self.connected = False
        self.stopped.set()

    def run_forever(self):
        self.stopped.wait()

    def send_event(self, event):
        self.sent.append(event)
        return self.send_result


class FakeBusinessClient:
    def __init__(self):
        self.calls = []
        self.fail = False

    def request(self, action, payload):
        if self.fail:
            raise RuntimeError("business unavailable")
        self.calls.append((action, payload))
        if action == "DELIVER_CLOUD_SERVICE_REQUEST":
            return {
                "responseData": {
                    "commandUid": COMMAND_UID,
                    "receiptState": "ACCEPTED",
                },
                "afterReplyToken": payload["deliveryId"],
            }
        if action == "COMPLETE_CLOUD_SERVICE_REPLY":
            return {"disposition": "COMPLETED"}
        if action in {
            "DELIVER_LEGACY_CLOUD_COMMAND",
            "DELIVER_CLOUD_EVENT_RESULT",
        }:
            return {"disposition": "DELIVERED"}
        raise AssertionError(action)


@pytest.fixture
def store(tmp_path):
    value = CommunicationStore(
        tmp_path / "communication.db",
        utc_now=lambda: NOW,
    )
    value.initialize()
    try:
        yield value
    finally:
        value.close()


def _request(delivery="30000000-0000-4000-8000-000000000001"):
    return CloudServiceRequest(
        delivery_id=delivery,
        request_id="onenet-request-1",
        service_id="startDeliverySession",
        params={"scalarFields": {"commandUid": COMMAND_UID}},
        received_at=None,
        clock_quality="UNAVAILABLE",
    )


def test_inbound_command_is_persisted_before_business_and_reply_completion(store):
    transport = FakeTransport()
    business = FakeBusinessClient()
    router = CommunicationRouter(
        store,
        transport,
        business_client=business,
        utc_now=lambda: NOW,
    )

    response = router.handle_service_request(_request())
    persisted = store.get_inbound_command(COMMAND_UID)
    assert persisted["state"] == "BUSINESS_ACCEPTED"
    assert business.calls[0][0] == "DELIVER_CLOUD_SERVICE_REQUEST"
    assert response.data["receiptState"] == "ACCEPTED"
    assert len(business.calls) == 1

    response.after_reply()
    assert business.calls[-1] == (
        "COMPLETE_CLOUD_SERVICE_REPLY",
        {"deliveryId": _request().delivery_id},
    )


def test_durable_duplicate_reuses_response_without_reentering_business(store):
    transport = FakeTransport()
    business = FakeBusinessClient()
    router = CommunicationRouter(store, transport, business_client=business)
    first = router.handle_service_request(_request())
    first.after_reply()
    business.calls.clear()

    duplicate = router.handle_service_request(
        _request("30000000-0000-4000-8000-000000000002")
    )

    assert duplicate.data == first.data
    assert duplicate.after_reply is None
    assert business.calls == []


def test_same_command_uid_with_changed_service_fails_closed(store):
    router = CommunicationRouter(
        store,
        FakeTransport(),
        business_client=FakeBusinessClient(),
    )
    router.handle_service_request(_request())
    changed = CloudServiceRequest(
        delivery_id="30000000-0000-4000-8000-000000000002",
        request_id="onenet-request-2",
        service_id="startCleanOperation",
        params={"commandUid": COMMAND_UID},
        received_at=None,
        clock_quality="UNAVAILABLE",
    )

    with pytest.raises(RuntimeError, match="conflicts"):
        router.handle_service_request(changed)


def test_submit_send_result_and_business_delivery_are_separate(store):
    transport = FakeTransport()
    business = FakeBusinessClient()
    router = CommunicationRouter(
        store,
        transport,
        business_client=business,
    )
    params = {
        "eventUid": EVENT_UID,
        "edgeEventSequence": 77,
        "payload": {"workUid": "work-1"},
    }
    receipt = router.submit_business_event(
        {
            "eventUid": EVENT_UID,
            "eventType": "DELIVERY_COMPLETE",
            "params": params,
        }
    )
    assert receipt["durableAccepted"] is True
    assert transport.sent == []

    claimed = store.claim_next_proxy_outbound_event()
    assert claimed["eventUid"] == EVENT_UID
    router.handle_transport_ack(EVENT_UID)
    router.handle_platform_result(
        CloudEventPlatformResult(edge_event_sequence=77, code=200)
    )
    assert business.calls == []

    router._deliver_platform_results()
    assert business.calls[-1][0] == "DELIVER_CLOUD_EVENT_RESULT"
    assert business.calls[-1][1]["edgeEventSequence"] == 77
    assert store.get_status()["proxyUndeliveredPlatformResultCount"] == 0


def test_event_identity_conflict_is_a_stable_local_error(store):
    router = CommunicationRouter(
        store,
        FakeTransport(),
        business_client=FakeBusinessClient(),
    )
    payload = {
        "eventUid": EVENT_UID,
        "eventType": "DELIVERY_COMPLETE",
        "params": {"eventUid": EVENT_UID, "edgeEventSequence": 77},
    }
    router.submit_business_event(payload)
    payload["params"] = {
        "eventUid": EVENT_UID,
        "edgeEventSequence": 77,
        "changed": True,
    }
    with pytest.raises(LocalControlActionError) as raised:
        router.submit_business_event(payload)
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"


def test_command_uid_extraction_accepts_nested_wire_and_legacy_id():
    assert _extract_command_uid(
        {"scalarFields1": {"commandUid": COMMAND_UID}}
    ) == COMMAND_UID
    assert _extract_command_uid({"id": COMMAND_UID, "params": {}}) == COMMAND_UID
    with pytest.raises(ValueError, match="one stable"):
        _extract_command_uid({"params": {}})
    with pytest.raises(ValueError, match="lowercase UUIDv4"):
        _extract_command_uid({"commandUid": "not-a-uuid"})
