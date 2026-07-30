import base64
import json
import os
from datetime import datetime, timedelta, timezone

import mqtt_client as mqtt_module
from edge_store import EdgeStore
from mqtt_client import MqttClient
from onenet_wire import canonical_payload_sha256, decode_service_command


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


class FakeStore:
    def __init__(self):
        self.connection_states = []

    def save_mqtt_persistent_state(self, session_present, reason_code):
        self.connection_states.append((session_present, reason_code))


class LifecyclePahoClient:
    def __init__(self, *, auto_connect=True):
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_publish = None
        self.auto_connect = auto_connect
        self.connect_async_calls = 0
        self.reconnect_calls = 0
        self.loop_start_calls = 0
        self.loop_stop_calls = 0
        self.disconnect_calls = 0
        self.delay = None
        self.publishes = []

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
        return (0, 1)

    def publish(self, topic, payload, qos):
        self.publishes.append((topic, json.loads(payload), qos))
        return type("PublishInfo", (), {"mid": 1, "rc": 0})()


class ImmediateTimeoutEvent:
    def clear(self):
        pass

    def wait(self, seconds):
        assert seconds == 8
        return False

    def set(self):
        pass


def test_loop_forever_retries_initial_mqtt_connection():
    mqtt = MqttClient.__new__(MqttClient)
    mqtt._connected = False
    mqtt._exit_flag = FakeExitEvent()
    mqtt.client = FakePahoClient()
    attempts = []

    def connect():
        attempts.append(len(attempts) + 1)
        if len(attempts) == 3:
            mqtt._connected = True
            mqtt._exit_flag.set()
            return True
        return False

    mqtt.connect = connect

    mqtt.loop_forever()

    assert attempts == [1, 2, 3]
    assert mqtt._exit_flag.waits == [1, 2]
    assert mqtt.client.loop_stop_calls == 1
    assert mqtt.client.disconnect_calls == 1


def test_reconnect_reuses_one_paho_network_loop(monkeypatch):
    paho = LifecyclePahoClient()
    monkeypatch.setattr(
        mqtt_module.mqtt,
        "Client",
        lambda *args, **kwargs: paho,
    )
    client = MqttClient(
        product_id="product",
        device_name="device",
        device_key=base64.b64encode(b"device-key").decode(),
        edge_store=FakeStore(),
    )
    client._start_relay_loop = lambda: None
    client._publish_online = lambda: None
    snapshots = []
    client.on_connected = lambda: snapshots.append("snapshot")

    assert client.connect()
    client._on_disconnect(
        paho,
        None,
        None,
        7,
        None,
    )
    assert client.connect()

    assert paho.delay == (1, 30)
    assert paho.connect_async_calls == 1
    assert paho.reconnect_calls == 1
    assert paho.loop_start_calls == 1
    assert paho.loop_stop_calls == 0
    assert snapshots == ["snapshot", "snapshot"]


def test_connect_timeout_keeps_paho_network_loop_running(monkeypatch):
    paho = LifecyclePahoClient(auto_connect=False)
    monkeypatch.setattr(
        mqtt_module.mqtt,
        "Client",
        lambda *args, **kwargs: paho,
    )
    client = MqttClient(
        product_id="product",
        device_name="device",
        device_key=base64.b64encode(b"device-key").decode(),
        edge_store=FakeStore(),
    )
    client._connect_event = ImmediateTimeoutEvent()

    assert client.connect() is False

    assert client._network_loop_started is True
    assert paho.connect_async_calls == 1
    assert paho.loop_start_calls == 1
    assert paho.loop_stop_calls == 0


def test_fixed_frame_unsupported_service_is_rejected_synchronously(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "device"
    client.deployment_code = "Dp_demo_01"
    client.edge_boot_id = 9001
    client._trusted_cos_environment = None
    client._unsupported_command_types = frozenset({
        "END_CLEAN_BEFORE_UNLOCK",
        "RESUME_CLEAN_OPERATION",
    })
    dispatched = []
    client.on_command_received = (
        lambda *args: dispatched.append(args)
    )
    decoded_commands = []
    for request_index, example_name in enumerate((
        "end-clean-before-unlock.service-wire.json",
        "resume-clean-operation.service-wire.json",
    )):
        example_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "contracts",
            "examples",
            "onenet-wire",
            example_name,
        )
        with open(example_path, encoding="utf-8") as source:
            wire = json.load(source)
        body = wire["callServiceApiBodyTemplate"]
        params = dict(body["params"])
        now = datetime.now(timezone.utc)
        issued_at = now.isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        expires_at = (
            now + timedelta(minutes=5)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if "scalarFields1" in params:
            params["scalarFields1"] = dict(params["scalarFields1"])
            params["scalarFields2"] = dict(params["scalarFields2"])
            params["scalarFields1"]["issuedAt"] = issued_at
            params["scalarFields1"]["expiresAt"] = expires_at
            params["scalarFields2"]["cosGrantExpiresAt"] = expires_at
        else:
            params["issuedAt"] = issued_at
            params["expiresAt"] = expires_at
        decoded = decode_service_command(body["identifier"], params)
        if "scalarFields1" in params:
            params["scalarFields1"]["payloadSha256"] = (
                canonical_payload_sha256(decoded["payload"])
            )
        else:
            params["payloadSha256"] = canonical_payload_sha256(
                decoded["payload"]
            )
        decoded_commands.append(decoded)
        topic = (
            "$sys/product/device/thing/service/"
            f"{body['identifier']}/invoke"
        )
        request = {
            "id": f"request-{request_index}",
            "params": params,
        }
        client._handle_service_call(topic, request)
        client._handle_service_call(topic, request)

    for decoded in decoded_commands:
        command = store.get_command(decoded["commandUid"])
        assert command["state"] == "REJECTED"
        assert command["last_error"] == "MCU_FEATURE_NOT_SUPPORTED"
    assert dispatched == []
    observations = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_COMMAND_OBSERVED'"""
    ).fetchall()
    assert len(observations) == 2
    assert all(
        json.loads(row["payload_json"])["payload"]["stage"]
        == "REJECTED"
        for row in observations
    )
    assert all(
        json.loads(row["payload_json"])["payload"]["errorCode"]
        == "MCU_FEATURE_NOT_SUPPORTED"
        for row in observations
    )
    replies = [
        payload
        for topic, payload, qos in paho.publishes
        if topic.endswith("/invoke_reply")
    ]
    assert len(replies) == 4
    assert all(reply["data"]["receiptState"] == 3 for reply in replies)
    assert all(
        reply["data"]["errorCode"] == "MCU_FEATURE_NOT_SUPPORTED"
        for reply in replies
    )
    store.close()
