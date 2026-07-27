import json
import os
from datetime import datetime, timedelta, timezone

from edge_boot import (
    _publish_runtime_snapshot,
    boot_sequence,
    recover_after_online_mcu_hello,
)
from edge_store import EdgeStore
from onenet_wire import (
    canonical_payload_sha256,
    decode_service_command,
    encode_event_post,
)
from uart_link import compute_mcu_payload_sha256


class FakeMqttClient:
    deployment_code = "Dp_demo_01"

    def __init__(self):
        self.published = []

    def publish_event(self, event_type, payload):
        self.published.append((event_type, payload))
        return 1

    def connect(self):
        return True


class BootRecoveryUart:
    def __init__(self):
        self.commands = []
        self.sent_values = []
        self.events = []
        self.acks = []

    def open(self):
        return True

    def close(self):
        pass

    def handshake(self):
        return {
            "mcu_boot_id": 42,
            "mcu_capability": 0x300,
            "mcu_firmware_version": "rc3-test",
        }

    def renegotiate_from_mcu_hello(self, frame):
        assert frame["message_name"] == "HELLO"
        return self.handshake()

    def query_state(self, on_segment=None):
        return [{
            "message_name": "STATE_SNAPSHOT_BEGIN",
            "message_type": 80,
            "tx_sequence": 1,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "activeWorkType": "NONE",
                "activeWorkUid": "00000000-0000-0000-0000-000000000000",
                "activePortNo": 0,
                "activeWorkPhase": "BOOT_RECOVERY",
                "appliedConfigVersion": 0,
                "appliedContentSha256": "0" * 64,
                "appliedMcuPayloadSha256": "0" * 64,
            },
        }]

    def apply_configuration(self, command, part_command_uids):
        self.commands.append("APPLY_CONFIGURATION")
        payload = command["payload"]
        config = payload["config"]
        self.events.append({
            "message_name": "CONFIG_APPLY_RESULT",
            "message_type": 20,
            "tx_sequence": 2,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 2,
                "uptimeMs": 1000,
                "mcuCommandUid": part_command_uids[-1],
                "applicationUid": payload["applicationUid"],
                "status": "APPLIED",
                "configVersion": config["version"],
                "contentSha256": config["contentSha256"],
                "mcuPayloadSha256": config["mcuPayloadSha256"],
                "faultCode": "NONE",
            },
        })
        return {
            "acked": True,
            "commit_mcu_command_uid": part_command_uids[-1],
            "parts": [],
        }

    def send_command(self, message_name, values, *, mcu_command_uid=None):
        self.commands.append(message_name)
        self.sent_values.append((message_name, dict(values), mcu_command_uid))
        if message_name == "CONFIRM_NO_ACTIVE_WORK":
            self.events.append({
                "message_name": "BOOT_RECONCILIATION_RESULT",
                "message_type": 63,
                "tx_sequence": 3,
                "payload": {
                    "mcuBootId": 42,
                    "mcuEventSequence": 3,
                    "uptimeMs": 2000,
                    "mcuCommandUid": mcu_command_uid,
                    "decision": "CONFIRM_NO_ACTIVE_WORK",
                    "status": "ACCEPTED",
                    "activeWorkType": "NONE",
                    "activeWorkUid": "00000000-0000-0000-0000-000000000000",
                    "activePortNo": 0,
                    "recoveryGeneration": 0,
                    "nextCleanActionSequence": 0,
                    "faultCode": "NONE",
                },
            })
        elif message_name == "RESUME_CLEAN_OPERATION":
            self.events.append({
                "message_name": "BOOT_RECONCILIATION_RESULT",
                "message_type": 63,
                "tx_sequence": 3,
                "payload": {
                    "mcuBootId": 42,
                    "mcuEventSequence": 3,
                    "uptimeMs": 2000,
                    "mcuCommandUid": mcu_command_uid,
                    "decision": "RESUME_CLEAN_OPERATION",
                    "status": "ACCEPTED",
                    "activeWorkType": "CLEAN_OPERATION",
                    "activeWorkUid": values["operationUid"],
                    "activePortNo": values["portNo"],
                    "recoveryGeneration": values["recoveryGeneration"],
                    "nextCleanActionSequence": values[
                        "nextCleanActionSequence"
                    ],
                    "faultCode": "NONE",
                },
            })
        return {
            "acked": True,
            "mcu_command_uid": mcu_command_uid,
        }

    def read_mcu_event(self, timeout_ms=500):
        return self.events.pop(0) if self.events else None

    def send_ack(self, *args):
        self.acks.append(args)

    def send_nack(self, *args):
        raise AssertionError("unexpected NACK")


class FixedFrameBootUart:
    compatibility_mode = True

    def __init__(self):
        self.opened = False

    def open(self):
        self.opened = True
        return True

    def close(self):
        self.opened = False

    def handshake(self):
        assert self.opened
        return {
            "mcu_boot_id": 123,
            "mcu_capability": 0,
            "mcu_port_count": 1,
            "mcu_firmware_version": "fixed-frame-compat",
            "uart_protocol_major": None,
            "uart_protocol_minor": None,
            "uart_state": "READY",
            "fullness_sensor_kind": "DIGITAL_INFRARED",
            "compatibility_mode": True,
        }

    def query_state(self, on_segment=None):
        raise AssertionError("fixed-frame boot must not query MCU state")


def mark_configuration_applied(store):
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "apply-configuration.service-wire.json",
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat().replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat().replace("+00:00", "Z")
    command["payload"]["config"]["mcuPayloadSha256"] = (
        compute_mcu_payload_sha256(command["payload"])
    )
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    part_uids = [
        f"60000000-0000-4000-8000-{index:012d}"
        for index in range(1, len(command["payload"]["ports"]) + 4)
    ]
    store.save_configuration_edge(command, part_uids)
    store.apply_configuration_result({
        "mcuCommandUid": part_uids[-1],
        "applicationUid": command["payload"]["applicationUid"],
        "status": "APPLIED",
        "configVersion": command["payload"]["config"]["version"],
        "contentSha256": command["payload"]["config"]["contentSha256"],
        "mcuPayloadSha256": command["payload"]["config"]["mcuPayloadSha256"],
        "faultCode": "NONE",
    })


def test_publish_runtime_snapshot_uses_valid_edge_boot_id_and_event_uid(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mqtt = FakeMqttClient()

    _publish_runtime_snapshot(
        store,
        mqtt,
        {
            "mcu_boot_id": 456,
            "mcu_firmware_version": "test-fw",
            "mcu_capability": 1,
        },
        [],
    )

    assert len(mqtt.published) == 1
    event_type, payload = mqtt.published[0]
    assert event_type == "DEVICE_RUNTIME_SNAPSHOT"
    assert payload["eventUid"]
    assert payload["payload"]["edgeBootId"] == 123
    assert payload["payload"]["mcuBootId"] == 456
    assert "lastDeliveryDoorActualOutputMs" not in payload["payload"]["ports"][0]


def test_non_uart_fault_does_not_misreport_uart_link_as_faulted(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    store.record_fault("SMOKE_SENSOR", 1536, "WARNING", {})
    mqtt = FakeMqttClient()

    _publish_runtime_snapshot(
        store,
        mqtt,
        {
            "mcu_boot_id": 456,
            "mcu_firmware_version": "test-fw",
            "mcu_capability": 1,
        },
        [],
    )

    assert mqtt.published[0][1]["payload"]["uartState"] == "READY"
    store.close()


def test_fixed_frame_boot_skips_query_and_releases_stale_local_work(
    tmp_path,
    monkeypatch,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    command_uid = "61000000-0000-4000-8000-000000000001"
    command = {
        "commandUid": command_uid,
        "commandType": "START_DELIVERY_SESSION",
    }
    store.receive_command(
        command_uid,
        command["commandType"],
        command,
    )
    assert store.claim_next_command()["command_uid"] == command_uid
    work_uid = "62000000-0000-4000-8000-000000000001"
    assert store.acquire_work_slot(
        "DELIVERY",
        work_uid,
        1,
        {
            "start_command_uid": command_uid,
            "phase": "WAITING_COMPAT_DELIVERY_RESULT",
        },
    )
    uart = FixedFrameBootUart()
    mqtt = FakeMqttClient()
    store.set_state(
        "fixed_frame_latest_observation_json",
        '{"postWeightGrams":123}',
    )
    store.set_state(
        "latest_runtime_ports_json",
        '[{"portNo":1,"fullnessSensorKind":"ULTRASONIC"}]',
    )
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, mqtt, None, None)

    assert result["status"] == "READY"
    assert result["snapshot_count"] == 0
    assert store.get_work_slot() is None
    inbox = store.get_command(command_uid)
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == (
        "PROCESS_RESTARTED_MCU_STATE_UNKNOWN"
    )
    assert store.get_state(
        "fixed_frame_last_abandoned_work_uid"
    ) == work_uid
    assert store.get_state(
        "fixed_frame_latest_observation_json"
    ) == ""
    snapshot = mqtt.published[-1][1]["payload"]
    assert snapshot["uartProtocolMajor"] is None
    assert snapshot["uartProtocolMinor"] is None
    assert snapshot["ports"][0]["fullnessSensorKind"] == (
        "DIGITAL_INFRARED"
    )
    encode_event_post(
        "DEVICE_RUNTIME_SNAPSHOT",
        mqtt.published[-1][1],
    )
    store.close()


def test_boot_recovery_reapplies_ram_config_before_confirming_no_work(
    tmp_path, monkeypatch
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    uart = BootRecoveryUart()
    mqtt = FakeMqttClient()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, mqtt, None, None)

    assert result["status"] == "READY"
    assert store.get_mcu_receive_generation() == 1
    assert uart.commands == [
        "APPLY_CONFIGURATION",
        "CONFIRM_NO_ACTIVE_WORK",
    ]
    assert len(uart.acks) == 2
    store.close()


def test_online_mcu_restart_runs_state_and_configuration_recovery(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    uart = BootRecoveryUart()
    hello = {
        "message_name": "HELLO",
        "message_type": 1,
        "tx_sequence": 9,
        "payload": {"senderBootId": 42},
    }

    result = recover_after_online_mcu_hello(store, uart, hello)

    assert result["status"] == "READY"
    assert result["mcu_info"]["mcu_boot_id"] == 42
    assert result["mcu_info"]["mcu_receive_generation"] == 1
    assert store.get_mcu_receive_generation() == 1
    assert uart.commands == [
        "APPLY_CONFIGURATION",
        "CONFIRM_NO_ACTIVE_WORK",
    ]
    assert len(uart.acks) == 2
    store.close()


def test_boot_recovery_resumes_original_clean_without_resetting_window(
    tmp_path, monkeypatch
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    deadline = (
        datetime.now(timezone.utc) + timedelta(minutes=20)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    operation_uid = "70000000-0000-4000-8000-000000000001"
    store.acquire_work_slot(
        "CLEAN",
        operation_uid,
        1,
        {
            "operation_uid": operation_uid,
            "port_no": 1,
            "config": {"version": 8, "contentSha256": "a" * 64},
            "start_mcu_command_uid": (
                "71000000-0000-4000-8000-000000000001"
            ),
            "operation_deadline": deadline,
            "recovery_generation": 0,
            "action_sequence": 4,
            "phase": "ACTIVE",
        },
    )
    uart = BootRecoveryUart()
    mqtt = FakeMqttClient()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, mqtt, None, None)

    assert result["status"] == "READY"
    assert uart.commands == [
        "APPLY_CONFIGURATION",
        "RESUME_CLEAN_OPERATION",
    ]
    _, values, _ = uart.sent_values[-1]
    assert values["operationUid"] == operation_uid
    assert values["recoveryGeneration"] == 1
    assert values["nextCleanActionSequence"] == 5
    slot = store.get_work_slot()
    assert slot["context"]["operation_deadline"] == deadline
    assert slot["context"]["recovery_generation"] == 1
    assert slot["context"]["action_sequence"] == 4
    assert slot["context"]["phase"] == "CLEAN_RECOVERY_REQUIRED"
    store.close()


def test_boot_recovery_marks_interrupted_delivery_for_manual_review(
    tmp_path, monkeypatch
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    session_uid = "72000000-0000-4000-8000-000000000001"
    store.acquire_work_slot(
        "DELIVERY",
        session_uid,
        1,
        {
            "session_uid": session_uid,
            "port_no": 1,
            "start_command_uid": (
                "73000000-0000-4000-8000-000000000001"
            ),
            "deployment_code": "Dp_demo_01",
            "unit_price_ten_thousandths": 4500,
            "phase": "WAITING_SELECTION",
            "first_measurement": {
                "measurementUid": (
                    "74000000-0000-4000-8000-000000000001"
                ),
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 1000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
                "mcuBootId": 41,
                "mcuEventSequence": 8,
            },
            "final_measurement": None,
            "negative_weight_anomaly": False,
        },
    )
    uart = BootRecoveryUart()
    mqtt = FakeMqttClient()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, mqtt, None, None)

    assert result["status"] == "READY"
    assert uart.commands[-1] == "CONFIRM_NO_ACTIVE_WORK"
    slot = store.get_work_slot()
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "DEVICE_INTERRUPTED"
    event_rows = [
        row for row in store.list_pending_events()
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert len(event_rows) == 1
    envelope = json.loads(event_rows[0]["payload_json"])
    assert envelope["eventType"] == "DELIVERY_COMPLETE"
    assert envelope["payload"]["completionReason"] == "DEVICE_INTERRUPTED"
    assert envelope["payload"]["manualReviewRequired"] is True
    store.close()
