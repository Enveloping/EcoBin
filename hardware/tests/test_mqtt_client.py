import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import mqtt_client as mqtt_module
from edge_store import EdgeStore
from mqtt_client import MqttClient
from onenet_wire import canonical_payload_sha256, decode_service_command
from factory_seal.admission import FactorySealProductionGate
from factory_seal.validation import FactorySealPaths


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
        self.on_subscribe = None
        self.auto_connect = auto_connect
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


def test_connect_subscribes_to_onenet_thing_topic_tree(monkeypatch):
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

    assert client.connect()

    assert (
        "$sys/product/device/thing/#",
        1,
    ) in paho.subscriptions
    thing_topics = {
        topic
        for topic, _qos in paho.subscriptions
        if "/thing/" in topic
    }
    assert thing_topics == {"$sys/product/device/thing/#"}


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
    client.device_name = "SN-CONTRACT-0001"
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


def test_unsealed_physical_command_is_rejected_before_inbox_dispatch(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "SN-CONTRACT-0001"
    client.edge_boot_id = 9001
    client._trusted_cos_environment = None
    client._unsupported_command_types = frozenset()
    client._factory_seal_gate = FactorySealProductionGate(
        FactorySealPaths(
            edge_store=tmp_path / "edge.db",
            sealed=tmp_path / "sealed.json",
        )
    )
    dispatched = []
    client.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )

    example_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "start-delivery-session.service-wire.json",
    )
    with open(example_path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    params = dict(body["params"])
    params["scalarFields1"] = dict(params["scalarFields1"])
    params["scalarFields2"] = dict(params["scalarFields2"])
    now = datetime.now(timezone.utc)
    params["scalarFields1"]["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    params["scalarFields1"]["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    decoded = decode_service_command(body["identifier"], params)
    params["scalarFields1"]["payloadSha256"] = canonical_payload_sha256(
        decoded["payload"]
    )
    request = {"id": "request-unsealed-delivery", "params": params}
    topic = (
        "$sys/product/device/thing/service/"
        f"{body['identifier']}/invoke"
    )

    client._handle_service_call(topic, request)
    client._handle_service_call(topic, request)

    row = store.get_command(decoded["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == "FACTORY_NOT_SEALED"
    assert dispatched == []
    observations = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_COMMAND_OBSERVED'"""
    ).fetchall()
    assert len(observations) == 1
    observed = json.loads(observations[0]["payload_json"])["payload"]
    assert observed["stage"] == "REJECTED"
    assert observed["errorCode"] == "FACTORY_NOT_SEALED"
    replies = [
        payload
        for reply_topic, payload, _qos in paho.publishes
        if reply_topic.endswith("/invoke_reply")
    ]
    assert len(replies) == 2
    assert all(reply["data"]["receiptState"] == 3 for reply in replies)
    assert all(
        reply["data"]["errorCode"] == "FACTORY_NOT_SEALED"
        for reply in replies
    )
    store.close()


def test_duplicate_apply_configuration_is_acknowledged_without_redispatch(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "SN-CONTRACT-0001"
    client.edge_boot_id = 9001
    client._trusted_cos_environment = None
    client._unsupported_command_types = frozenset()
    dispatched = []
    client.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )

    example_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "apply-configuration.service-wire.json",
    )
    with open(example_path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    params = dict(body["params"])
    now = datetime.now(timezone.utc)
    params["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    params["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    decoded = decode_service_command(body["identifier"], params)
    params["payloadSha256"] = canonical_payload_sha256(
        decoded["payload"]
    )
    topic = (
        "$sys/product/device/thing/service/"
        f"{body['identifier']}/invoke"
    )
    request = {"id": "request-configuration", "params": params}

    client._handle_service_call(topic, request)
    client._handle_service_call(topic, request)

    assert len(dispatched) == 1
    assert dispatched[0][0] == decoded["commandUid"]
    assert dispatched[0][1] == "APPLY_CONFIGURATION"
    replies = [
        payload
        for reply_topic, payload, _qos in paho.publishes
        if reply_topic.endswith("/invoke_reply")
    ]
    assert [reply["data"]["receiptState"] for reply in replies] == [1, 2]
    store.close()


def test_duplicate_firmware_command_redispatches_fresh_cos_grant(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "SN-CONTRACT-0001"
    client.edge_boot_id = 9001
    client._unsupported_command_types = frozenset()
    client._trusted_cos_environment = {
        "bucket": "ecobin-contract-1250000000",
        "region": "ap-guangzhou",
        "baseUrl": (
            "https://ecobin-contract-1250000000"
            ".cos.ap-guangzhou.myqcloud.com"
        ),
    }
    dispatched = []
    client.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )

    example_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "start-mcu-firmware-update.service-wire.json",
    )
    with open(example_path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    params = dict(body["params"])
    params["scalarFields1"] = dict(params["scalarFields1"])
    params["scalarFields2"] = dict(params["scalarFields2"])
    now = datetime.now(timezone.utc)
    params["scalarFields1"]["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    params["scalarFields1"]["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    params["scalarFields2"]["cosGrantExpiresAt"] = (
        now + timedelta(minutes=10)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    decoded = decode_service_command(body["identifier"], params)
    params["scalarFields1"]["payloadSha256"] = (
        canonical_payload_sha256(decoded["payload"])
    )
    topic = (
        "$sys/product/device/thing/service/"
        f"{body['identifier']}/invoke"
    )
    request = {"id": "request-firmware", "params": params}

    client._handle_service_call(topic, request)
    params["scalarFields1"]["cosGrantGrantUid"] = (
        "71000000-0000-4000-8000-000000000008"
    )
    params["scalarFields1"]["cosGrantTmpSecretId"] = "FRESH_SECRET_ID"
    params["scalarFields2"]["cosGrantTmpSecretKey"] = "FRESH_SECRET_KEY"
    client._handle_service_call(topic, request)

    assert len(dispatched) == 2
    assert dispatched[0][0] == decoded["commandUid"]
    assert dispatched[1][0] == decoded["commandUid"]
    assert dispatched[0][2]["cosGrant"]["tmpSecretId"] == "TMP_SECRET_ID_7"
    assert dispatched[1][2]["cosGrant"]["tmpSecretId"] == "FRESH_SECRET_ID"
    replies = [
        payload
        for reply_topic, payload, _qos in paho.publishes
        if reply_topic.endswith("/invoke_reply")
    ]
    assert [reply["data"]["receiptState"] for reply in replies] == [1, 2]
    store.close()


def test_duplicate_factory_seal_requeues_only_repairable_failed_command(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "SN-CONTRACT-0001"
    client.edge_boot_id = 9001
    client._unsupported_command_types = frozenset()
    client._trusted_cos_environment = None
    dispatched = []
    client.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )

    example_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "authorize-factory-seal.service-wire.json",
    )
    with open(example_path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    params = dict(body["params"])
    now = datetime.now(timezone.utc)
    params["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    params["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    decoded = decode_service_command(body["identifier"], params)
    params["payloadSha256"] = canonical_payload_sha256(
        decoded["payload"]
    )
    topic = (
        "$sys/product/device/thing/service/"
        f"{body['identifier']}/invoke"
    )
    request = {"id": "request-factory-seal", "params": params}

    client._handle_service_call(topic, request)
    assert len(dispatched) == 1
    assert store.claim_next_command()["command_uid"] == decoded["commandUid"]
    assert store.fail_factory_seal_command_for_retry(
        decoded["commandUid"],
        "FACTORY_REPORT_INVALID",
    )

    client._handle_service_call(topic, request)
    assert len(dispatched) == 2
    assert store.get_command(decoded["commandUid"])["state"] == "PENDING"

    assert store.claim_next_command()["command_uid"] == decoded["commandUid"]
    assert store.complete_command(decoded["commandUid"])
    client._handle_service_call(topic, request)
    assert len(dispatched) == 2
    assert store.get_command(decoded["commandUid"])["state"] == "COMPLETED"

    terminal_params = dict(params)
    terminal_params["commandUid"] = str(uuid.uuid4())
    terminal_command = decode_service_command(
        body["identifier"],
        terminal_params,
    )
    terminal_request = {
        "id": "request-factory-seal-terminal",
        "params": terminal_params,
    }
    client._handle_service_call(topic, terminal_request)
    assert len(dispatched) == 3
    assert store.claim_next_command()["command_uid"] == terminal_command[
        "commandUid"
    ]
    assert store.reject_factory_seal_command(
        terminal_command,
        "ACCEPTANCE_EVIDENCE_MISMATCH",
    )

    client._handle_service_call(topic, terminal_request)
    assert len(dispatched) == 3
    assert store.get_command(terminal_command["commandUid"])[
        "state"
    ] == "REJECTED"
    store.close()


def test_expired_first_factory_seal_delivery_is_rejected_without_persisting(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "SN-CONTRACT-0001"
    client.edge_boot_id = 9001
    client._unsupported_command_types = frozenset()
    client._trusted_cos_environment = None
    dispatched = []
    client.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )

    example_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "authorize-factory-seal.service-wire.json",
    )
    with open(example_path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    params = dict(body["params"])
    now = datetime.now(timezone.utc)
    params["issuedAt"] = (
        now - timedelta(minutes=2)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    params["expiresAt"] = (
        now - timedelta(minutes=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    decoded = decode_service_command(body["identifier"], params)
    params["payloadSha256"] = canonical_payload_sha256(
        decoded["payload"]
    )
    topic = (
        "$sys/product/device/thing/service/"
        f"{body['identifier']}/invoke"
    )

    client._handle_service_call(
        topic,
        {"id": "expired-first-seal", "params": params},
    )

    assert dispatched == []
    assert store.get_command(decoded["commandUid"]) is None
    reply = paho.publishes[-1][1]
    assert reply["data"]["receiptState"] == 3
    assert reply["data"]["errorCode"] == "BAD_COMMAND"
    store.close()


def test_expired_factory_seal_duplicate_uses_original_persisted_receipt(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "SN-CONTRACT-0001"
    client.edge_boot_id = 9001
    client._unsupported_command_types = frozenset()
    client._trusted_cos_environment = None
    dispatched = []
    client.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )

    example_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "authorize-factory-seal.service-wire.json",
    )
    with open(example_path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    params = dict(body["params"])
    now = datetime.now(timezone.utc)
    params["issuedAt"] = (
        now - timedelta(minutes=2)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    params["expiresAt"] = (
        now - timedelta(minutes=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command = decode_service_command(body["identifier"], params)
    params["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    command = decode_service_command(body["identifier"], params)
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    with store.transaction():
        store._conn.execute(
            "UPDATE command_inbox SET received_at=? WHERE command_uid=?",
            (
                (
                    now - timedelta(seconds=90)
                ).isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                command["commandUid"],
            ),
        )
    assert store.claim_next_command()["command_uid"] == command[
        "commandUid"
    ]
    assert store.fail_factory_seal_command_for_retry(
        command["commandUid"],
        "FACTORY_REPORT_INVALID",
    )
    topic = (
        "$sys/product/device/thing/service/"
        f"{body['identifier']}/invoke"
    )

    client._handle_service_call(
        topic,
        {"id": "expired-duplicate-seal", "params": params},
    )

    assert len(dispatched) == 1
    assert dispatched[0][0] == command["commandUid"]
    assert store.get_command(command["commandUid"])["state"] == "PENDING"
    reply = paho.publishes[-1][1]
    assert reply["data"]["receiptState"] == 2
    assert reply["data"]["errorCode"] == ""
    store.close()


def test_accepted_business_confirmation_notifies_reliable_count_change_once(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    paho = LifecyclePahoClient()
    client = MqttClient.__new__(MqttClient)
    client._store = store
    client.client = paho
    client.product_id = "product"
    client.device_name = "SN-CONTRACT-0001"
    client.edge_boot_id = 9001
    client._trusted_cos_environment = None
    client._unsupported_command_types = frozenset()
    client.on_command_received = None
    reliable_count_changes = []
    client.on_reliable_event_count_changed = (
        lambda: reliable_count_changes.append("changed")
    )
    client._relay_pending_events = lambda: None

    example_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "confirm-edge-event.service-wire.json",
    )
    with open(example_path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    params = dict(body["params"])
    params["scalarFields"] = dict(params["scalarFields"])
    now = datetime.now(timezone.utc)
    params["scalarFields"]["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    params["scalarFields"]["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    original_event_uid = params["scalarFields"]["originalEventUid"]
    store.create_edge_event(
        original_event_uid,
        "DELIVERY_COMPLETE",
        {"workUid": "delivery-work-1"},
        work_uid="delivery-work-1",
        device_name="SN-CONTRACT-0001",
        target_type="DELIVERY_SESSION",
        target_uid="delivery-work-1",
    )
    stored_event = json.loads(
        store.get_event(original_event_uid)["payload_json"]
    )
    params["scalarFields"]["originalPayloadSha256"] = (
        stored_event["payloadSha256"]
    )
    decoded = decode_service_command(body["identifier"], params)
    params["scalarFields"]["payloadSha256"] = (
        canonical_payload_sha256(decoded["payload"])
    )
    decoded = decode_service_command(body["identifier"], params)
    assert store.count_pending_reliable_events() == 1

    topic = (
        "$sys/product/device/thing/service/"
        f"{body['identifier']}/invoke"
    )
    request = {"id": "request-confirmation", "params": params}
    client._handle_service_call(topic, request)
    client._handle_service_call(topic, request)

    assert store.count_pending_reliable_events() == 0
    assert reliable_count_changes == ["changed"]
    replies = [
        payload
        for reply_topic, payload, _qos in paho.publishes
        if reply_topic.endswith("/invoke_reply")
    ]
    assert [reply["data"]["receiptState"] for reply in replies] == [1, 2]
    store.close()
