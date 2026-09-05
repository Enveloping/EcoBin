from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

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


class FakeUpdaterClient:
    def __init__(self):
        self.calls = []

    def request(self, action, payload):
        self.calls.append((action, payload))
        assert action == "DELIVER_CLOUD_MAINTENANCE_REQUEST"
        return {
            "commandUid": COMMAND_UID,
            "receiptState": (
                "ACCEPTED" if len(self.calls) == 1 else "DUPLICATE_ACCEPTED"
            ),
            "errorCode": None,
        }


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


def test_business_update_is_routed_to_updater_and_grant_is_never_persisted(
    store,
):
    updater = FakeUpdaterClient()
    business = FakeBusinessClient()
    router = CommunicationRouter(
        store,
        FakeTransport(),
        business_client=business,
        updater_client=updater,
        enable_remote_business_update=True,
        authenticated_device_name="SN-TEST-0001",
        edge_boot_id=77,
    )
    params = {
        "scalarFields1": {
            "commandUid": COMMAND_UID,
            "issuedAt": "2030-01-02T03:04:05.678Z",
            "expiresAt": "2030-01-02T03:05:05.678Z",
        },
        "downloadGrant": {
            "authorizationSequence": 1,
            "url": "https://private.example/package?first-secret",
            "expiresAt": "2030-01-02T04:04:05.678Z",
        },
    }
    request = CloudServiceRequest(
        delivery_id="30000000-0000-4000-8000-000000000001",
        request_id="onenet-maintenance-1",
        service_id="startBusinessRuntimeUpdate",
        params=params,
        received_at=None,
        clock_quality="UNAVAILABLE",
    )

    first = router.handle_service_request(request)
    assert first.data["receiptState"] == 1
    assert business.calls == []
    with sqlite3.connect(store.path) as connection:
        persisted = connection.execute(
            "SELECT params_json FROM inbound_proxy_payload WHERE command_uid=?",
            (COMMAND_UID,),
        ).fetchone()[0]
    assert "downloadGrant" not in persisted
    assert "first-secret" not in persisted
    assert "issuedAt" not in persisted
    assert "expiresAt" not in persisted

    refreshed = dict(params)
    refreshed["downloadGrant"] = {
        **params["downloadGrant"],
        "authorizationSequence": 2,
        "url": "https://private.example/package?second-secret",
    }
    refreshed["scalarFields1"] = {
        **params["scalarFields1"],
        "issuedAt": "2030-01-02T03:06:05.678Z",
        "expiresAt": "2030-01-02T03:07:05.678Z",
    }
    duplicate = router.handle_service_request(
        CloudServiceRequest(
            delivery_id="30000000-0000-4000-8000-000000000002",
            request_id="onenet-maintenance-2",
            service_id="startBusinessRuntimeUpdate",
            params=refreshed,
            received_at=None,
            clock_quality="UNAVAILABLE",
        )
    )
    assert duplicate.data == first.data
    assert len(updater.calls) == 2
    assert updater.calls[-1][1]["authenticatedDeviceName"] == "SN-TEST-0001"
    assert updater.calls[-1][1]["params"]["downloadGrant"]["authorizationSequence"] == 2


def test_business_update_cancellation_is_always_routed_to_updater(store):
    updater = FakeUpdaterClient()
    business = FakeBusinessClient()
    router = CommunicationRouter(
        store,
        FakeTransport(),
        business_client=business,
        updater_client=updater,
        enable_remote_business_update=True,
        authenticated_device_name="SN-TEST-0001",
        edge_boot_id=77,
    )
    request = CloudServiceRequest(
        delivery_id="30000000-0000-4000-8000-000000000011",
        request_id="onenet-cancel-1",
        service_id="cancelBusinessRuntimeUpdate",
        params={
            "commandUid": COMMAND_UID,
            "issuedAt": "2030-01-02T03:04:05.678Z",
            "expiresAt": "2030-01-02T03:14:05.678Z",
        },
        received_at=None,
        clock_quality="UNAVAILABLE",
    )

    response = router.handle_service_request(request)

    assert response.data["receiptState"] == 1
    assert business.calls == []
    assert updater.calls[0][1]["serviceId"] == (
        "cancelBusinessRuntimeUpdate"
    )


def test_updater_event_gets_a_reserved_durable_sequence(store):
    router = CommunicationRouter(
        store,
        FakeTransport(),
        business_client=FakeBusinessClient(),
    )
    semantic = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "contracts/examples/onenet/business-runtime-update-progress.event.json"
        ).read_text(encoding="utf-8")
    )
    request = {
        "eventUid": semantic["eventUid"],
        "eventType": semantic["eventType"],
        "targetType": semantic["target"]["type"],
        "targetUid": semantic["target"]["uid"],
        "commandUid": semantic["commandUid"],
        "occurredAt": semantic["occurredAt"],
        "clockQuality": semantic["clockQuality"],
        "payload": semantic["payload"],
    }

    first = router.submit_updater_event(request)
    repeated = router.submit_updater_event(request)

    assert first["disposition"] == "ACCEPTED"
    assert repeated["disposition"] == "DUPLICATE"
    assert first["edgeEventSequence"] == 9_000_000_000_000
    assert repeated["edgeEventSequence"] == first["edgeEventSequence"]
    claimed = store.claim_next_proxy_outbound_event()
    assert claimed["params"]["edgeEventSequence"] == 9_000_000_000_000
    assert claimed["params"]["payloadSha256"] == semantic["payloadSha256"]


def test_actual_software_state_uses_the_same_durable_management_lane(store):
    router = CommunicationRouter(
        store,
        FakeTransport(),
        business_client=FakeBusinessClient(),
    )
    semantic = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "contracts/examples/onenet/device-software-state-reported.event.json"
        ).read_text(encoding="utf-8")
    )
    request = {
        "eventUid": semantic["eventUid"],
        "eventType": semantic["eventType"],
        "targetType": semantic["target"]["type"],
        "targetUid": semantic["target"]["uid"],
        "commandUid": semantic["commandUid"],
        "occurredAt": semantic["occurredAt"],
        "clockQuality": semantic["clockQuality"],
        "payload": semantic["payload"],
    }

    receipt = router.submit_updater_event(request)

    assert receipt["disposition"] == "ACCEPTED"
    assert receipt["edgeEventSequence"] == 9_000_000_000_000
    claimed = store.claim_next_proxy_outbound_event()
    assert claimed["eventType"] == "DEVICE_SOFTWARE_STATE_REPORTED"
    assert claimed["params"]["commandUid"] is None
    assert (
        claimed["params"]["payload"]["managementStateSequence"]
        == semantic["payload"]["managementStateSequence"]
    )


def test_business_update_routing_requires_authenticated_device_identity(store):
    with pytest.raises(ValueError, match="authenticated OneNet device name"):
        CommunicationRouter(
            store,
            FakeTransport(),
            business_client=FakeBusinessClient(),
            updater_client=FakeUpdaterClient(),
            enable_remote_business_update=True,
        )


def test_business_update_never_falls_back_to_business_when_routing_is_disabled(
    store,
):
    business = FakeBusinessClient()
    router = CommunicationRouter(
        store,
        FakeTransport(),
        business_client=business,
    )
    request = CloudServiceRequest(
        delivery_id="30000000-0000-4000-8000-000000000001",
        request_id="onenet-maintenance-1",
        service_id="startBusinessRuntimeUpdate",
        params={"scalarFields1": {"commandUid": COMMAND_UID}},
        received_at=None,
        clock_quality="UNAVAILABLE",
    )
    with pytest.raises(LocalControlActionError) as raised:
        router.handle_service_request(request)
    assert raised.value.code == "FEATURE_DISABLED"
    assert business.calls == []


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
