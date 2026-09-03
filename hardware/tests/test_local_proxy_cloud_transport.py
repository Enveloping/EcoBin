from __future__ import annotations

import threading

from cloud_transport import CloudEvent, CloudServiceResponse, CloudTransport
from local_proxy_cloud_transport import LocalProxyCloudTransport


DELIVERY_UID = "10000000-0000-4000-8000-000000000001"
COMMAND_UID = "20000000-0000-4000-8000-000000000001"
EVENT_UID = "30000000-0000-4000-8000-000000000001"
RESULT_UID = "40000000-0000-4000-8000-000000000001"


class FakeClient:
    def __init__(self):
        self.calls = []
        self.status = {
            "component": "COMMUNICATION_AGENT",
            "status": "READY",
            "onenetOwnership": "ENABLED",
            "cloudConnectionState": "CONNECTED",
        }
        self.event_receipt = {
            "eventUid": EVENT_UID,
            "disposition": "ACCEPTED",
            "durableAccepted": True,
        }

    def request(self, action, payload):
        self.calls.append((action, payload))
        if action == "GET_STATUS":
            return dict(self.status)
        if action == "SUBMIT_BUSINESS_EVENT":
            return dict(self.event_receipt)
        raise AssertionError(action)


def test_proxy_implements_cloud_transport_without_cloud_credentials():
    proxy = LocalProxyCloudTransport(client=FakeClient())
    assert isinstance(proxy, CloudTransport)
    assert not hasattr(proxy, "device_key")
    assert not hasattr(proxy, "client_id")


def test_connect_and_poll_project_only_permanent_agent_usability():
    client = FakeClient()
    proxy = LocalProxyCloudTransport(client=client)
    transitions = []
    proxy.on_connected = lambda: transitions.append("connected")
    proxy.on_disconnected = lambda: transitions.append("disconnected")

    assert proxy.connect() is True
    assert proxy.connected is True
    assert transitions == ["connected"]

    client.status["cloudConnectionState"] = "DISCONNECTED"
    assert proxy.connect() is False
    assert proxy.connected is False
    assert transitions == ["connected", "disconnected"]


def test_outbound_event_is_acknowledged_only_after_durable_local_receipt():
    client = FakeClient()
    proxy = LocalProxyCloudTransport(client=client)
    acknowledgements = []
    proxy.on_event_transport_ack = acknowledgements.append
    event = CloudEvent(
        event_uid=EVENT_UID,
        event_type="DELIVERY_COMPLETE",
        params={"edgeEventSequence": 71, "eventUid": EVENT_UID},
    )

    assert proxy.send_event(event) is True
    assert client.calls[-1] == (
        "SUBMIT_BUSINESS_EVENT",
        {
            "eventUid": EVENT_UID,
            "eventType": "DELIVERY_COMPLETE",
            "params": {"edgeEventSequence": 71, "eventUid": EVENT_UID},
        },
    )
    assert acknowledgements == [EVENT_UID]

    client.event_receipt["durableAccepted"] = False
    assert proxy.send_event(event) is False
    assert acknowledgements == [EVENT_UID]


def test_service_reply_completion_preserves_after_reply_order_and_idempotency():
    proxy = LocalProxyCloudTransport(client=FakeClient())
    timeline = []
    handled = []

    def handle(request):
        handled.append(request)
        timeline.append("business-persisted")
        return CloudServiceResponse(
            data={"commandUid": COMMAND_UID, "receiptState": "ACCEPTED"},
            after_reply=lambda: timeline.append("business-woken"),
        )

    proxy.on_service_request = handle
    payload = {
        "deliveryId": DELIVERY_UID,
        "requestId": "onenet-request-1",
        "serviceId": "startDeliverySession",
        "params": {"commandUid": COMMAND_UID},
        "receivedAt": None,
        "clockQuality": "UNAVAILABLE",
    }

    first = proxy.deliver_service_request(payload)
    repeated = proxy.deliver_service_request(payload)
    assert first == repeated == {
        "responseData": {
            "commandUid": COMMAND_UID,
            "receiptState": "ACCEPTED",
        },
        "afterReplyToken": DELIVERY_UID,
    }
    assert len(handled) == 1
    assert timeline == ["business-persisted"]

    timeline.append("onenet-reply-queued")
    assert proxy.complete_service_reply({"deliveryId": DELIVERY_UID}) == {
        "disposition": "COMPLETED"
    }
    assert proxy.complete_service_reply({"deliveryId": DELIVERY_UID}) == {
        "disposition": "DUPLICATE"
    }
    assert timeline == [
        "business-persisted",
        "onenet-reply-queued",
        "business-woken",
    ]


def test_platform_result_is_delivered_once_for_same_permanent_result_uid():
    proxy = LocalProxyCloudTransport(client=FakeClient())
    results = []
    proxy.on_event_platform_result = results.append
    payload = {
        "resultUid": RESULT_UID,
        "edgeEventSequence": 71,
        "code": 200,
    }

    assert proxy.deliver_platform_result(payload) == {
        "disposition": "DELIVERED"
    }
    assert proxy.deliver_platform_result(payload) == {
        "disposition": "DUPLICATE"
    }
    assert len(results) == 1
    assert results[0].edge_event_sequence == 71
    assert results[0].code == 200


def test_run_forever_stops_without_stopping_permanent_agent():
    client = FakeClient()
    proxy = LocalProxyCloudTransport(
        client=client,
        poll_interval_seconds=0.001,
    )
    thread = threading.Thread(target=proxy.run_forever)
    thread.start()
    proxy.disconnect()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert all(action != "DISCONNECT" for action, _payload in client.calls)
