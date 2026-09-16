import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import edge_boot as edge_boot_module
from cloud_transport import CloudEvent
from device_identity import DeviceIdentity
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
from trusted_clock import ClockSample


def test_clock_state_uses_shared_clock_sampler(monkeypatch):
    monkeypatch.setattr(
        edge_boot_module,
        "sample_clock",
        lambda: ClockSample(
            "SYNCED",
            "2026-08-24T12:00:00.000Z",
            1,
            "2026-08-24T12:00:00.000Z",
        ),
    )
    assert edge_boot_module._clock_state() == "SYNCED"

    monkeypatch.setattr(
        edge_boot_module,
        "sample_clock",
        lambda: ClockSample(
            "ESTIMATED",
            None,
            None,
            "2026-08-24T12:00:01.000Z",
        ),
    )
    assert edge_boot_module._clock_state() == "ESTIMATED"


DEVICE_IDENTITY = DeviceIdentity("SN-DEMO-0001")


class FakeCloudTransport:

    def __init__(self):
        self.published = []
        self.connected = True
        self.connect_called = False

    def send_event(self, event):
        assert isinstance(event, CloudEvent)
        self.published.append((event.event_type, dict(event.params)))
        return True

    def connect(self):
        self.connect_called = True
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
        self.self_test_queries = 0
        self.firmware_identity_queries = 0
        self.device_entry_urls = []
        self.firmware_identity_result = {
            "queryStatus": "OK",
            "statusCode": 0,
            "protocolRevision": 2,
            "firmwareVersionCode": 20_100,
            "firmwareVersion": "2.1.0",
            "firmwareIdentityHex": "0123456789abcdef",
        }

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
        raise AssertionError("fixed-frame boot must not query general MCU state")

    def query_self_test(self, timeout_ms=3000, on_result=None):
        assert timeout_ms == 3000
        self.self_test_queries += 1
        result = {
            "queryStatus": "OK",
            "communicationHealthy": True,
            "portNo": 1,
            "validFlags": 3,
            "weightValid": True,
            "weightGrams": 1234,
            "weightMeasurementUid": (
                "64000000-0000-4000-8000-000000000001"
            ),
            "infraredValid": True,
            "infraredBlocked": False,
            "smokeCode": 0,
            "smokeState": "NORMAL",
            "smokeSensorHealth": "OK",
            "faultCode": None,
            "rawFrameHex": "f1030004d20000f1",
        }
        if on_result is not None:
            on_result(result)
        return result

    def query_firmware_identity(self, timeout_ms=3000):
        assert timeout_ms == 3000
        self.firmware_identity_queries += 1
        return dict(self.firmware_identity_result)

    def send_device_entry_url(self, url):
        self.device_entry_urls.append(url)


class FixedFrameTimeoutBootUart(FixedFrameBootUart):

    def query_self_test(self, timeout_ms=3000, on_result=None):
        assert timeout_ms == 3000
        self.self_test_queries += 1
        result = {
            "queryStatus": "TIMEOUT",
            "communicationHealthy": False,
            "portNo": 1,
            "validFlags": 0,
            "weightValid": False,
            "weightGrams": None,
            "weightMeasurementUid": None,
            "infraredValid": False,
            "infraredBlocked": None,
            "smokeCode": None,
            "smokeState": "UNKNOWN",
            "smokeSensorHealth": "TIMEOUT",
            "faultCode": "SMOKE_SENSOR",
            "rawFrameHex": None,
        }
        if on_result is not None:
            on_result(result)
        return result


class FailedOpenUart:
    def open(self):
        return False


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


def test_fixed_frame_boot_reports_and_recovers_uart_fault(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()

    failed = boot_sequence(
        store,
        FailedOpenUart(),
        DEVICE_IDENTITY,
    )

    assert failed["status"] == "SAFETY_LOCKED"
    fault = store.get_active_edge_fault("UART", "UART_PROTOCOL")
    assert fault is not None
    observed = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_FAULT_OBSERVED'"""
    ).fetchone()
    observed_payload = json.loads(observed["payload_json"])["payload"]
    assert observed_payload["faultUid"] == fault["fault_uid"]
    assert observed_payload["mcuBootId"] is None
    assert observed_payload["mcuEventSequence"] is None

    recovered = boot_sequence(
        store,
        FixedFrameBootUart(),
        DEVICE_IDENTITY,
    )

    assert recovered["status"] == "READY"
    assert store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
    recovery = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_FAULT_RECOVERED'"""
    ).fetchone()
    assert json.loads(recovery["payload_json"])["payload"][
        "faultUid"
    ] == fault["fault_uid"]
    store.close()


def test_publish_runtime_snapshot_uses_valid_edge_boot_id_and_event_uid(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    cloud_transport = FakeCloudTransport()

    _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        {
            "mcu_boot_id": 456,
            "mcu_firmware_version": "test-fw",
            "mcu_capability": 1,
        },
        [],
    )

    assert len(cloud_transport.published) == 1
    event_type, payload = cloud_transport.published[0]
    assert event_type == "DEVICE_RUNTIME_SNAPSHOT"
    assert payload["eventUid"]
    assert payload["payload"]["edgeBootId"] == 123
    assert payload["payload"]["mcuBootId"] == 456
    assert "lastDeliveryDoorActualOutputMs" not in payload["payload"]["ports"][0]


def test_runtime_snapshot_semantic_dedupe_and_forced_fallback(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    cloud_transport = FakeCloudTransport()
    mcu_info = {
        "mcu_boot_id": 456,
        "mcu_firmware_version": "test-fw",
        "mcu_capability": 1,
    }

    first = _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        mcu_info,
        [],
        force=True,
    )
    unchanged = _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        mcu_info,
        [],
        force=False,
        previous_payload_sha256=first["payload_sha256"],
    )
    fallback = _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        mcu_info,
        [],
        force=True,
        previous_payload_sha256=first["payload_sha256"],
    )

    assert first["published"] is True
    assert unchanged == {
        "published": False,
        "skipped_unchanged": True,
        "payload_sha256": first["payload_sha256"],
    }
    assert fallback["published"] is True
    assert len(cloud_transport.published) == 2
    assert (
        cloud_transport.published[0][1]["eventUid"]
        != cloud_transport.published[1][1]["eventUid"]
    )
    store.close()


def test_runtime_snapshot_state_change_bypasses_semantic_dedupe(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    cloud_transport = FakeCloudTransport()
    mcu_info = {
        "mcu_boot_id": 456,
        "mcu_firmware_version": "test-fw",
        "mcu_capability": 1,
    }
    first = _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        mcu_info,
        [],
    )

    changed_mcu_info = dict(mcu_info, uart_state="FAULT")
    changed = _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        changed_mcu_info,
        [],
        force=False,
        previous_payload_sha256=first["payload_sha256"],
    )

    assert changed["published"] is True
    assert changed["payload_sha256"] != first["payload_sha256"]
    assert len(cloud_transport.published) == 2
    store.close()


def native_device_facts(**changes):
    values = {
        "status": "AVAILABLE",
        "portNo": 1,
        "currentMcuBootId": 456,
        "capturedUptimeMs": 10_000,
        "lastDeliveryDoorCommand": "CLOSE",
        "doorActionActive": True,
        "pb6Output": False,
        "pb7Output": True,
        "pinchPaused": False,
        "cleanLockPowered": True,
        "scaleReadStatus": "VALID",
        "scaleAttemptSequence": 7,
        "scaleCapturedUptimeMs": 10_000,
        "scaleWeightGrams": 523,
        "scaleCalibrationVersion": 4,
        "measurementSequence": 0,
        "measurementState": "NONE",
        "measurementObservedUptimeMs": 0,
        "measurementElapsedMs": 0,
        "measurementSampleCount": 0,
        "measurementWeightGrams": 0,
        "smokeObservationState": "ALARM",
        "fullnessObservationKind": "ULTRASONIC",
        "fullnessReadStatus": "VALID",
        "fullnessInfraredBlocked": False,
        "fullnessDistanceMm": 500,
    }
    values.update(changes)
    return values


def test_native_runtime_snapshot_projects_only_current_device_facts(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    cloud = FakeCloudTransport()
    info = {
        "mcu_boot_id": 456,
        "mcu_port_count": 1,
        "mcu_firmware_version": "1.0.1-hil.4",
        "mcu_capability": 0,
        "uart_protocol_major": 2,
        "uart_protocol_minor": 0,
        "uart_state": "READY",
    }

    _publish_runtime_snapshot(
        store,
        cloud,
        DEVICE_IDENTITY,
        info,
        None,
        device_facts=native_device_facts(),
    )

    payload = cloud.published[0][1]["payload"]
    port = payload["ports"][0]
    assert port["lastDeliveryDoorCommand"] == "CLOSE"
    assert port["lastDeliveryDoorOutputStatus"] == "COMMAND_DISPATCHED"
    assert port["deliveryDoorPhysicalStateBasis"] == "NOT_OBSERVABLE"
    assert port["cleanLockPowerState"] == "ENERGIZED"
    assert port["weightMeasurementStatus"] == "UNSTABLE"
    assert port["weightValueAvailable"] is True
    assert port["reportedWeightGrams"] == 523
    assert port["weightValueKind"] == "LAST_OBSERVED"
    assert port["smokeState"] == "ALARM"
    assert port["smokeSensorHealth"] == "OK"
    assert port["fullnessSensorKind"] == "ULTRASONIC"
    assert port["fullnessSensorValue"] == "BLOCKED"
    assert port["fullnessSampleBasis"] == "NOT_SAMPLED"
    assert port["representativeDistanceMm"] == 500
    assert port["fullnessValidSampleCount"] == 1
    assert uuid.UUID(port["weightMeasurementUid"]).version == 4
    encode_event_post("DEVICE_RUNTIME_SNAPSHOT", cloud.published[0][1])
    store.close()


def test_native_runtime_snapshot_projects_timeout_median_not_latest_raw_value(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    cloud = FakeCloudTransport()
    info = {
        "mcu_boot_id": 456,
        "mcu_port_count": 1,
        "uart_protocol_major": 2,
        "uart_protocol_minor": 0,
        "uart_state": "READY",
    }
    facts = native_device_facts(
        scaleWeightGrams=999,
        measurementSequence=8,
        measurementState="TIMEOUT_MEDIAN",
        measurementObservedUptimeMs=10_000,
        measurementElapsedMs=5_000,
        measurementSampleCount=20,
        measurementWeightGrams=850,
    )

    _publish_runtime_snapshot(
        store,
        cloud,
        DEVICE_IDENTITY,
        info,
        None,
        device_facts=facts,
    )

    port = cloud.published[0][1]["payload"]["ports"][0]
    assert port["weightMeasurementStatus"] == "UNSTABLE"
    assert port["weightValueAvailable"] is True
    assert port["reportedWeightGrams"] == 850
    assert port["reportedWeightGrams"] != facts["scaleWeightGrams"]
    assert port["weightValueKind"] == "TIMEOUT_MEDIAN"
    assert port["weightSensorHealth"] == "OK"
    assert port["weightFaultCode"] is None
    assert port["measurementElapsedMs"] == 5_000
    assert port["weightSampleCount"] == 20
    assert port["calibrationVersion"] == 4
    assert uuid.UUID(port["weightMeasurementUid"]).version == 4
    encode_event_post("DEVICE_RUNTIME_SNAPSHOT", cloud.published[0][1])
    store.close()


@pytest.mark.parametrize(
    "changes,expected",
    [
        (
            {
                "measurementSequence": 8,
                "measurementState": "STABLE_MEAN",
                "measurementObservedUptimeMs": 10_000,
                "measurementElapsedMs": 1_500,
                "measurementSampleCount": 5,
                "measurementWeightGrams": 700,
            },
            ("STABLE", True, 700, "STABLE_WINDOW_MEAN", "OK"),
        ),
        (
            {},
            ("UNSTABLE", True, 523, "LAST_OBSERVED", "OK"),
        ),
        (
            {
                "measurementSequence": 8,
                "measurementState": "UNAVAILABLE",
                "measurementObservedUptimeMs": 10_000,
                "measurementElapsedMs": 5_000,
                "measurementSampleCount": 0,
                "measurementWeightGrams": 0,
            },
            ("TIMEOUT", False, None, "NONE", "TIMEOUT"),
        ),
    ],
)
def test_native_weight_projection_regresses_stable_raw_and_fault_terminals(
    tmp_path,
    changes,
    expected,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    fields = edge_boot_module._native_weight_fields(
        store,
        1,
        native_device_facts(**changes),
    )
    assert (
        fields["weightMeasurementStatus"],
        fields["weightValueAvailable"],
        fields["reportedWeightGrams"],
        fields["weightValueKind"],
        fields["weightSensorHealth"],
    ) == expected
    store.close()


def test_native_runtime_snapshot_reuses_uuid4_only_for_same_weight_observation(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    info = {
        "mcu_boot_id": 456,
        "mcu_port_count": 1,
        "uart_protocol_major": 2,
        "uart_protocol_minor": 0,
        "uart_state": "READY",
    }

    first_cloud = FakeCloudTransport()
    _publish_runtime_snapshot(
        store, first_cloud, DEVICE_IDENTITY, info, None,
        device_facts=native_device_facts(),
    )
    first_uid = first_cloud.published[0][1]["payload"]["ports"][0][
        "weightMeasurementUid"
    ]
    same_cloud = FakeCloudTransport()
    _publish_runtime_snapshot(
        store, same_cloud, DEVICE_IDENTITY, info, None,
        device_facts=native_device_facts(),
    )
    changed_cloud = FakeCloudTransport()
    _publish_runtime_snapshot(
        store, changed_cloud, DEVICE_IDENTITY, info, None,
        device_facts=native_device_facts(
            scaleAttemptSequence=8,
            scaleCapturedUptimeMs=10_100,
            capturedUptimeMs=10_100,
        ),
    )
    same_uid = same_cloud.published[0][1]["payload"]["ports"][0][
        "weightMeasurementUid"
    ]
    changed_uid = changed_cloud.published[0][1]["payload"]["ports"][0][
        "weightMeasurementUid"
    ]

    assert first_uid == same_uid
    assert changed_uid != first_uid
    assert uuid.UUID(first_uid).version == 4
    assert uuid.UUID(changed_uid).version == 4
    store.close()


def test_native_snapshot_never_reuses_old_port_for_missing_or_wrong_boot_facts(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    store.set_state(
        "latest_runtime_ports_json",
        json.dumps([{"portNo": 1, "reportedWeightGrams": 999}]),
    )
    info = {
        "mcu_boot_id": 456,
        "mcu_port_count": 1,
        "uart_protocol_major": 2,
        "uart_protocol_minor": 0,
        "uart_state": "STARTING",
    }
    for facts in (
        None,
        native_device_facts(status="BOOT_MISMATCH"),
        native_device_facts(currentMcuBootId=455),
    ):
        cloud = FakeCloudTransport()
        _publish_runtime_snapshot(
            store,
            cloud,
            DEVICE_IDENTITY,
            info,
            None,
            device_facts=facts,
        )
        payload = cloud.published[0][1]["payload"]
        port = payload["ports"][0]
        assert payload["uartState"] == "NEGOTIATING"
        assert port["weightValueAvailable"] is False
        assert port["reportedWeightGrams"] is None
        assert port["smokeState"] == "UNKNOWN"
        assert port["fullnessSensorValue"] == "CLEAR"
        assert port["fullnessSampleBasis"] == "NOT_SAMPLED"
        assert port["fullnessValidSampleCount"] == 0
        encode_event_post("DEVICE_RUNTIME_SNAPSHOT", cloud.published[0][1])
    store.close()


def test_fixed_frame_runtime_marks_cleaner_confirmation_basis(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_state(
        "port_1_clean_runtime_context_json",
        json.dumps({"cleaner_physical_close_confirmed": True}),
    )

    port = edge_boot_module._fixed_frame_runtime_ports(
        store,
        None,
        [],
    )[0]

    assert port["cleanerPhysicalCloseConfirmed"] is True
    assert port["cleanDoorStateBasis"] == "CLEANER_CONFIRMATION"
    store.close()


def test_non_uart_fault_does_not_misreport_uart_link_as_faulted(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    store.record_fault("SMOKE_SENSOR", 1536, "WARNING", {})
    cloud_transport = FakeCloudTransport()

    _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        {
            "mcu_boot_id": 456,
            "mcu_firmware_version": "test-fw",
            "mcu_capability": 1,
        },
        [],
    )

    assert cloud_transport.published[0][1]["payload"]["uartState"] == "READY"
    store.close()


def test_fixed_frame_boot_queries_sensors_and_releases_stale_local_work(
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
    cloud_transport = FakeCloudTransport()
    store.set_state(
        "fixed_frame_latest_observation_json",
        '{"postWeightGrams":123}',
    )
    store.set_state(
        "latest_runtime_ports_json",
        '[{"portNo":1,"fullnessSensorKind":"ULTRASONIC"}]',
    )
    device_entry_url = (
        "https://www.jinshoubao.com/device-entry/restart-proof"
    )
    store.save_device_entry_url(
        device_entry_url,
        hashlib.sha256(device_entry_url.encode("ascii")).hexdigest(),
        "2026-08-08T00:00:00.000Z",
    )
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, DEVICE_IDENTITY)

    assert result["status"] == "READY"
    assert cloud_transport.connect_called is False
    assert cloud_transport.published == []
    assert result["snapshot_count"] == 1
    assert uart.self_test_queries == 1
    assert uart.firmware_identity_queries == 1
    assert uart.device_entry_urls == [device_entry_url]
    assert store.get_work_slot() is None
    inbox = store.get_command(command_uid)
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "EDGE_RESTARTED"
    assert store.get_state(
        "fixed_frame_latest_observation_json"
    ) == '{"postWeightGrams":123}'
    _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        result["mcu_info"],
        result["snapshots"],
    )
    snapshot = cloud_transport.published[-1][1]["payload"]
    assert snapshot["uartProtocolMajor"] is None
    assert snapshot["uartProtocolMinor"] is None
    assert snapshot["mcuFirmwareVersion"] == "2.1.0"
    assert snapshot["mcuFirmwareIdentity"] == {
        "queryStatus": "OK",
        "statusCode": 0,
        "fixedFrameRevision": 2,
        "firmwareVersionCode": 20_100,
        "firmwareVersion": "2.1.0",
        "firmwareIdentityHex": "0123456789abcdef",
    }
    assert snapshot["ports"][0]["fullnessSensorKind"] == (
        "DIGITAL_INFRARED"
    )
    assert snapshot["ports"][0]["reportedWeightGrams"] == 1234
    assert snapshot["ports"][0]["smokeState"] == "NORMAL"
    encode_event_post(
        "DEVICE_RUNTIME_SNAPSHOT",
        cloud_transport.published[-1][1],
    )
    store.close()


def test_fixed_frame_boot_does_not_publish_internal_error_as_identity(
    tmp_path,
    monkeypatch,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    uart = FixedFrameBootUart()
    uart.firmware_identity_result["statusCode"] = 3
    cloud_transport = FakeCloudTransport()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, DEVICE_IDENTITY)

    assert result["status"] == "READY"
    assert cloud_transport.connect_called is False
    assert cloud_transport.published == []
    _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        result["mcu_info"],
        result["snapshots"],
    )
    snapshot = cloud_transport.published[-1][1]["payload"]
    assert "mcuFirmwareIdentity" not in snapshot
    assert snapshot["mcuFirmwareVersion"] == "fixed-frame-compat"
    store.close()


def test_fixed_frame_self_test_timeout_completes_local_boot_but_degrades(
    tmp_path,
    monkeypatch,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    uart = FixedFrameTimeoutBootUart()
    cloud_transport = FakeCloudTransport()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, DEVICE_IDENTITY)

    assert result["status"] == "DEGRADED"
    assert result["reason"] == "fixed_frame_sensor_self_test_failed"
    assert uart.self_test_queries == 1
    assert cloud_transport.connect_called is False
    assert cloud_transport.published == []
    _publish_runtime_snapshot(
        store,
        cloud_transport,
        DEVICE_IDENTITY,
        result["mcu_info"],
        result["snapshots"],
    )
    assert len(cloud_transport.published) == 1
    snapshot = cloud_transport.published[0][1]["payload"]
    assert snapshot["uartState"] == "FAULT"
    assert snapshot["ports"][0]["weightValueAvailable"] is False
    assert snapshot["ports"][0]["weightSensorHealth"] == "TIMEOUT"
    assert snapshot["ports"][0]["smokeState"] == "UNKNOWN"
    assert snapshot["ports"][0]["smokeSensorHealth"] == "TIMEOUT"
    assert store.get_active_edge_fault("UART", "UART_PROTOCOL") is not None
    store.close()


def test_boot_recovery_reapplies_ram_config_before_confirming_no_work(
    tmp_path, monkeypatch
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    uart = BootRecoveryUart()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, DEVICE_IDENTITY)

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


def test_boot_restart_aborts_clean_and_requires_a_new_complete_clean(
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
    start_command_uid = "71000000-0000-4000-8000-000000000001"
    start_command = {
        "commandUid": start_command_uid,
        "commandType": "START_CLEAN_OPERATION",
        "targetDeviceName": "SN-DEMO-0001",
    }
    store.receive_command(
        start_command_uid,
        start_command["commandType"],
        start_command,
    )
    assert store.claim_next_command()["command_uid"] == start_command_uid
    store.acquire_work_slot(
        "CLEAN",
        operation_uid,
        1,
        {
            "operation_uid": operation_uid,
            "port_no": 1,
            "config": {"version": 8, "contentSha256": "a" * 64},
            "start_command_uid": start_command_uid,
            "start_mcu_command_uid": start_command_uid,
            "operation_deadline": deadline,
            "recovery_generation": 0,
            "action_sequence": 4,
            "phase": "ACTIVE",
        },
    )
    uart = BootRecoveryUart()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, DEVICE_IDENTITY)

    assert result["status"] == "READY"
    assert uart.commands == [
        "APPLY_CONFIGURATION",
        "CONFIRM_NO_ACTIVE_WORK",
    ]
    assert store.get_work_slot() is None
    assert store.clean_restart_interlock_active(1) is True
    assert store.get_command(start_command_uid)["last_error"] == (
        "EDGE_RESTARTED"
    )
    failed = [
        row for row in store.list_pending_events()
        if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
    ]
    assert len(failed) == 1
    assert json.loads(failed[0]["payload_json"])["payload"] == {
        "observedCommandType": "START_CLEAN_OPERATION",
        "stage": "FAILED",
        "mcuCommandUid": start_command_uid,
        "errorCode": "EDGE_RESTARTED",
    }
    store.close()


def test_boot_restart_appends_edge_restart_after_clean_window_expiry(
    tmp_path,
    monkeypatch,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    operation_uid = "70000000-0000-4000-8000-000000000011"
    start_command_uid = "71000000-0000-4000-8000-000000000011"
    start_command = {
        "commandUid": start_command_uid,
        "commandType": "START_CLEAN_OPERATION",
        "targetDeviceName": "SN-DEMO-0001",
    }
    store.receive_command(
        start_command_uid,
        start_command["commandType"],
        start_command,
    )
    assert store.claim_next_command()["command_uid"] == start_command_uid
    assert store.acquire_work_slot(
        "CLEAN",
        operation_uid,
        1,
        {
            "operation_uid": operation_uid,
            "port_no": 1,
            "config": {"version": 8, "contentSha256": "a" * 64},
            "start_command_uid": start_command_uid,
            "start_mcu_command_uid": start_command_uid,
            "operation_deadline": "2026-08-25T00:30:00.000Z",
            "recovery_generation": 0,
            "action_sequence": 4,
            "phase": "ACTIVE",
        },
    )
    assert store.mark_clean_window_expired_for_recovery(
        operation_uid
    ) == "RECOVERY_REQUIRED"
    assert store.get_work_slot()["context"]["phase"] == (
        "CLEAN_RECOVERY_REQUIRED"
    )
    uart = BootRecoveryUart()
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, DEVICE_IDENTITY)

    assert result["status"] == "READY"
    assert store.get_work_slot() is None
    assert store.clean_restart_interlock_active(1) is True
    assert store.get_command(start_command_uid)["last_error"] == (
        "EDGE_RESTARTED"
    )
    failed = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events()
        if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
    ]
    assert [event["payload"]["errorCode"] for event in failed] == [
        "COMMAND_EXPIRED",
        "EDGE_RESTARTED",
    ]
    assert [event["payload"]["stage"] for event in failed] == [
        "FAILED",
        "FAILED",
    ]
    store.close()


def test_boot_restart_aborts_delivery_without_fake_completion(
    tmp_path, monkeypatch
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mark_configuration_applied(store)
    session_uid = "72000000-0000-4000-8000-000000000001"
    start_command_uid = "73000000-0000-4000-8000-000000000001"
    start_command = {
        "commandUid": start_command_uid,
        "commandType": "START_DELIVERY_SESSION",
        "targetDeviceName": "SN-DEMO-0001",
    }
    store.receive_command(
        start_command_uid,
        start_command["commandType"],
        start_command,
    )
    assert store.claim_next_command()["command_uid"] == start_command_uid
    store.acquire_work_slot(
        "DELIVERY",
        session_uid,
        1,
        {
            "session_uid": session_uid,
            "port_no": 1,
            "start_command_uid": start_command_uid,
            "device_name": "SN-DEMO-0001",
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
    monkeypatch.setattr("edge_boot.time.sleep", lambda _: None)

    result = boot_sequence(store, uart, DEVICE_IDENTITY)

    assert result["status"] == "READY"
    assert uart.commands[-1] == "CONFIRM_NO_ACTIVE_WORK"
    assert store.get_work_slot() is None
    event_rows = [
        row for row in store.list_pending_events()
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert event_rows == []
    failed = [
        row for row in store.list_pending_events()
        if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
    ]
    assert len(failed) == 1
    assert json.loads(failed[0]["payload_json"])["payload"][
        "errorCode"
    ] == "EDGE_RESTARTED"
    store.close()
