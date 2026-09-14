import threading
import time

import main as main_module
from device_identity import DeviceIdentity
from main import EcoBinEdge


class ConfigurationStore:
    def __init__(self, interval_ms=None):
        self.interval_ms = interval_ms

    def get_latest_applied_configuration(self):
        if self.interval_ms is None:
            return None
        return {
            "payload": {
                "deviceConfig": {
                    "edgeHeartbeatIntervalMs": self.interval_ms,
                },
            },
        }


def edge_without_initialization(interval_ms=None):
    edge = EcoBinEdge.__new__(EcoBinEdge)
    edge.store = ConfigurationStore(interval_ms)
    edge._runtime_snapshot_lock = threading.Lock()
    edge._runtime_snapshot_requested = threading.Event()
    edge._last_runtime_snapshot_monotonic = 0.0
    return edge


def test_runtime_snapshot_uses_one_hour_default(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "EDGE_RUNTIME_SNAPSHOT_INTERVAL_S",
        3600.0,
    )

    assert edge_without_initialization()._runtime_snapshot_interval_seconds() == 3600.0


def test_runtime_snapshot_clamps_legacy_configuration_to_ten_minutes():
    edge = edge_without_initialization(300_000)

    assert edge._runtime_snapshot_interval_seconds() == 600.0


def test_state_change_publish_is_deferred_inside_five_second_window():
    edge = edge_without_initialization(3_600_000)
    edge._last_runtime_snapshot_monotonic = time.monotonic()

    result = edge._publish_runtime_snapshot_now(force=False)

    assert result == {
        "published": False,
        "skipped_unchanged": False,
        "deferred": True,
    }
    assert edge._runtime_snapshot_requested.is_set()


def test_native_snapshot_entry_uses_coherent_runtime_observation(monkeypatch):
    facts = {"status": "AVAILABLE", "currentMcuBootId": 42, "portNo": 1}
    identity = {
        "queryStatus": "OK",
        "statusCode": 0,
        "fixedFrameRevision": 2,
        "firmwareVersionCode": 10_004,
        "firmwareVersion": "1.0.1-hil.4",
        "firmwareIdentityHex": "391ce0b83076c981",
    }

    class NativeUart:
        compatibility_mode = False
        port_count = 1
        uart_state = "READY"

        @staticmethod
        def current_runtime_observation():
            return {
                "mcuBootId": 42,
                "mcuCapability": 0,
                "mcuFirmwareVersion": "1.0.1-hil.4",
                "mcuFirmwareIdentity": identity,
                "deviceFacts": facts,
            }

    edge = edge_without_initialization()
    edge._native_mode = True
    edge.uart = NativeUart()
    edge.cloud_transport = object()
    edge.device_identity = DeviceIdentity("SN-DEMO-0001")
    edge._last_runtime_snapshot_fingerprint = None
    calls = []

    def publish(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "published": False,
            "skipped_unchanged": True,
            "payload_sha256": "a" * 64,
        }

    monkeypatch.setattr("edge_boot._publish_runtime_snapshot", publish)

    edge._publish_runtime_snapshot_now(force=False)

    args, kwargs = calls[0]
    assert args[4] is None
    assert kwargs["device_facts"] == facts
    assert args[3] == {
        "mcu_boot_id": 42,
        "mcu_capability": 0,
        "mcu_firmware_version": "1.0.1-hil.4",
        "mcu_firmware_identity": identity,
        "mcu_port_count": 1,
        "uart_protocol_major": 2,
        "uart_protocol_minor": 0,
        "fullness_sensor_kind": "ULTRASONIC",
        "uart_state": "READY",
        "compatibility_mode": False,
    }
    assert edge._software_runtime_facts()["mcuFirmware"] == {
        "versionName": "1.0.1-hil.4",
        "versionCode": 10_004,
        "identityHex": "391ce0b83076c981",
        "fixedFrameRevision": 2,
    }
