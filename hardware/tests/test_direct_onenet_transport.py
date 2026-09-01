import base64
import json
import threading

import pytest

import direct_onenet_transport as transport_module
from cloud_transport import CloudEvent, CloudServiceResponse
from direct_onenet_transport import (
    MAX_INBOUND_MESSAGE_BYTES,
    DirectOneNetTransport,
)


class FakeExitEvent:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)

    def set(self):
        self.stopped = True


class FakePahoClient:
    def __init__(self):
        self.loop_stop_calls = 0
        self.disconnect_calls = 0

    def loop_stop(self):
        self.loop_stop_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1


class PublishInfo:
    def __init__(self, *, mid=1, rc=0, published=False):
        self.mid = mid
        self.rc = rc
        self._published = published

    def is_published(self):
        return self._published


class LifecyclePahoClient:
    def __init__(self, *, auto_connect=True, publish_mid=1):
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_publish = None
        self.on_subscribe = None
        self.auto_connect = auto_connect
        self.publish_mid = publish_mid
        self.connect_async_calls = 0
        self.reconnect_calls = 0
        self.loop_start_calls = 0
        self.loop_stop_calls = 0
        self.disconnect_calls = 0
        self.delay = None
        self.publishes = []
        self.subscriptions = []

    def reconnect_delay_set(self, min_delay, max_delay):
        self.delay = (min_delay, max_delay)

    def username_pw_set(self, username, password):
        assert username == "product"
        assert password

    def connect_async(self, host, port, keepalive):
        self.connect_async_calls += 1

    def reconnect(self):
        self.reconnect_calls += 1
        self.on_connect(
            self,
            None,
            {"session_present": 1},
            0,
            None,
        )

    def loop_start(self):
        self.loop_start_calls += 1
        if self.auto_connect and self.connect_async_calls == 1:
            self.on_connect(
                self,
                None,
                {"session_present": 1},
                0,
                None,
            )

    def loop_stop(self):
        self.loop_stop_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1

    def subscribe(self, topic, qos):
        self.subscriptions.append((topic, qos))
        return (0, len(self.subscriptions))

    def publish(self, topic, payload, qos):
        self.publishes.append((topic, json.loads(payload), qos))
        return PublishInfo(mid=self.publish_mid)


class ImmediateTimeoutEvent:
    def clear(self):
        pass

    def wait(self, seconds):
        assert seconds == 8
        return False

    def set(self):
        pass


class Message:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


def make_transport(monkeypatch, paho=None):
    paho = paho or LifecyclePahoClient()
    monkeypatch.setattr(
        transport_module.mqtt,
        "Client",
        lambda *args, **kwargs: paho,
    )
    transport = DirectOneNetTransport(
        product_id="product",
        device_name="device",
        device_key=base64.b64encode(b"device-key").decode(),
    )
    return transport, paho


def test_run_forever_retries_initial_mqtt_connection():
    transport = DirectOneNetTransport.__new__(DirectOneNetTransport)
    transport._connected = False
    transport._exit_flag = FakeExitEvent()
    transport._network_loop_started = True
    transport.client = FakePahoClient()
    attempts = []

    def connect():
        attempts.append(len(attempts) + 1)
        if len(attempts) == 3:
            transport._connected = True
            transport._exit_flag.set()
            return True
        return False

    transport.connect = connect

    transport.run_forever()

    assert attempts == [1, 2, 3]
    assert transport._exit_flag.waits == [1, 2]
    assert transport.client.loop_stop_calls == 1
    assert transport.client.disconnect_calls == 1


def test_reconnect_reuses_one_paho_network_loop(monkeypatch):
    transport, paho = make_transport(monkeypatch)
    transport._publish_online = lambda: None
    connections = []
    disconnections = []
    mqtt_states = []
    transport.on_connected = lambda: connections.append("connected")
    transport.on_disconnected = (
        lambda: disconnections.append("disconnected")
    )
    transport.on_mqtt_state_observed = (
        lambda session, reason: mqtt_states.append((session, reason))
    )

    assert transport.connect()
    transport._on_disconnect(paho, None, None, 7, None)
    assert transport.connect()

    assert paho.delay == (1, 30)
    assert paho.connect_async_calls == 1
    assert paho.reconnect_calls == 1
    assert paho.loop_start_calls == 1
    assert paho.loop_stop_calls == 0
    assert connections == ["connected", "connected"]
    assert disconnections == ["disconnected"]
    assert mqtt_states == [(True, 0), (False, 7), (True, 0)]
    assert not hasattr(transport, "_store")


def test_connect_subscribes_to_one_thing_topic_tree(monkeypatch):
    transport, paho = make_transport(monkeypatch)
    transport._publish_online = lambda: None

    assert transport.connect()

    assert ("$sys/product/device/thing/#", 1) in paho.subscriptions
    thing_topics = {
        topic
        for topic, _qos in paho.subscriptions
        if "/thing/" in topic
    }
    assert thing_topics == {"$sys/product/device/thing/#"}


def test_connect_timeout_keeps_paho_network_loop_running(monkeypatch):
    transport, paho = make_transport(
        monkeypatch,
        LifecyclePahoClient(auto_connect=False),
    )
    transport._connect_event = ImmediateTimeoutEvent()

    assert transport.connect() is False

    assert transport._network_loop_started is True
    assert paho.connect_async_calls == 1
    assert paho.loop_start_calls == 1
    assert paho.loop_stop_calls == 0


def test_service_message_crosses_transport_as_raw_bounded_request(
    monkeypatch,
):
    transport, paho = make_transport(monkeypatch)
    requests = []
    after_reply_publish_counts = []

    def handle(request):
        requests.append(request)
        return CloudServiceResponse(
            data={"receiptState": 1},
            after_reply=lambda: after_reply_publish_counts.append(
                len(paho.publishes)
            ),
        )

    transport.on_service_request = handle
    wire_params = {
        "opaqueBusinessField": {
            "workUid": "work-1",
            "unknownToTransport": True,
        }
    }
    message = Message(
        "$sys/product/device/thing/service/startDelivery/invoke",
        json.dumps(
            {"id": "request-1", "params": wire_params}
        ).encode(),
    )

    transport._on_message(paho, None, message)

    assert len(requests) == 1
    request = requests[0]
    assert request.request_id == "request-1"
    assert request.service_id == "startDelivery"
    assert request.params == wire_params
    assert request.delivery_id
    reply_topic, reply, qos = paho.publishes[-1]
    assert reply_topic.endswith(
        "/thing/service/startDelivery/invoke_reply"
    )
    assert reply == {
        "id": "request-1",
        "code": 200,
        "msg": "accepted",
        "data": {"receiptState": 1},
    }
    assert qos == 1
    # The durable business wake-up runs only after the reply was queued.
    assert after_reply_publish_counts == [1]


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"id": "", "params": {}}).encode(),
        json.dumps(
            {
                "id": "request-oversized-params",
                "params": {"value": "x" * (64 * 1024)},
            }
        ).encode(),
        b"[]",
        b"\xff",
    ],
    ids=[
        "blank-request-id",
        "oversized-params",
        "non-object-json",
        "invalid-utf8",
    ],
)
def test_invalid_service_message_never_reaches_business_handler(
    monkeypatch,
    payload,
):
    transport, paho = make_transport(monkeypatch)
    handled = []
    transport.on_service_request = handled.append
    message = Message(
        "$sys/product/device/thing/service/startDelivery/invoke",
        payload,
    )

    transport._on_message(paho, None, message)

    assert handled == []
    assert paho.publishes == []


def test_transport_rejects_message_over_raw_packet_limit(monkeypatch):
    transport, paho = make_transport(monkeypatch)
    handled = []
    transport.on_service_request = handled.append
    message = Message(
        "$sys/product/device/thing/service/startDelivery/invoke",
        b"x" * (MAX_INBOUND_MESSAGE_BYTES + 1),
    )

    transport._on_message(paho, None, message)

    assert handled == []
    assert paho.publishes == []


def test_event_post_reply_is_normalized_before_crossing_transport_boundary(
    monkeypatch,
):
    transport, paho = make_transport(monkeypatch)
    results = []
    transport.on_event_platform_result = results.append

    transport._on_message(
        paho,
        None,
        Message(
            "$sys/product/device/thing/event/post/reply",
            json.dumps({"id": "42", "code": 200}).encode(),
        ),
    )
    transport._on_message(
        paho,
        None,
        Message(
            "$sys/product/device/thing/event/post/reply",
            json.dumps({"id": "not-a-sequence", "code": 200}).encode(),
        ),
    )

    assert len(results) == 1
    assert results[0].edge_event_sequence == 42
    assert results[0].code == 200


def test_event_puback_exposes_stable_event_uid_not_paho_mid(monkeypatch):
    transport, paho = make_transport(
        monkeypatch,
        LifecyclePahoClient(publish_mid=41),
    )
    monkeypatch.setattr(
        transport_module,
        "encode_event_post",
        lambda event_type, params: {
            "id": "1",
            "params": {event_type: params},
        },
    )
    acknowledged = []
    transport.on_event_transport_ack = acknowledged.append
    event = CloudEvent(
        event_uid="business-event-uid",
        event_type="DELIVERY_COMPLETE",
        params={"payload": {"workUid": "work-1"}},
    )

    assert transport.send_event(event) is True
    assert acknowledged == []

    transport._on_publish(paho, None, 41, 0, None)
    transport._on_publish(paho, None, 41, 0, None)

    assert acknowledged == ["business-event-uid"]
    assert 41 not in transport._mid_to_event_uid


def test_event_puback_entering_before_publish_returns_is_not_lost(
    monkeypatch,
):
    transport, paho = make_transport(monkeypatch)
    monkeypatch.setattr(
        transport_module,
        "encode_event_post",
        lambda event_type, params: {
            "id": "1",
            "params": {event_type: params},
        },
    )
    callback_entered = threading.Event()
    callback_finished = threading.Event()
    callback_thread = None

    def publish_with_early_ack(topic, payload, qos):
        nonlocal callback_thread

        def acknowledge():
            callback_entered.set()
            transport._on_publish(paho, None, 73, 0, None)
            callback_finished.set()

        callback_thread = threading.Thread(target=acknowledge)
        callback_thread.start()
        assert callback_entered.wait(1.0)
        return PublishInfo(mid=73, published=False)

    paho.publish = publish_with_early_ack
    acknowledged = []
    transport.on_event_transport_ack = acknowledged.append

    assert transport.send_event(
        CloudEvent(
            event_uid="early-ack-event-uid",
            event_type="DELIVERY_COMPLETE",
            params={"payload": {"workUid": "work-1"}},
        )
    ) is True
    assert callback_finished.wait(1.0)
    assert callback_thread is not None
    callback_thread.join(timeout=1.0)

    assert acknowledged == ["early-ack-event-uid"]
    assert 73 not in transport._mid_to_event_uid
