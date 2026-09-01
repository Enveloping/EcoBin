from __future__ import annotations

import json
import os
import stat

import pytest

from factory_progress import (
    EnrollmentProgressWriter,
    RuntimeProgressWriter,
    validate_enrollment_progress,
    validate_runtime_progress,
)
from main import EcoBinEdge


def test_enrollment_progress_is_exact_atomic_and_root_only(tmp_path):
    path = tmp_path / "enrollment" / "progress.json"
    writer = EnrollmentProgressWriter(path)

    writer.report("IDENTITY_PREPARATION")
    document = writer.report(
        "RETRY_WAIT",
        last_error_code="ENROLLMENT_NETWORK_UNAVAILABLE",
        retryable=True,
    )

    assert document == {
        "schemaVersion": 1,
        "phase": "RETRY_WAIT",
        "lastErrorCode": "ENROLLMENT_NETWORK_UNAVAILABLE",
        "retryable": True,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == document
    assert path.stat().st_size <= 256
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not list(path.parent.glob(".progress.json.tmp-*"))


def test_enrollment_progress_rejects_extra_or_sensitive_fields():
    valid = {
        "schemaVersion": 1,
        "phase": "FAILED",
        "lastErrorCode": "ENROLLMENT_BACKEND_REJECTED",
        "retryable": False,
    }
    validate_enrollment_progress(valid)

    with pytest.raises(ValueError, match="fields"):
        validate_enrollment_progress({**valid, "deviceUid": "secret"})
    with pytest.raises(ValueError, match="allowlisted"):
        validate_enrollment_progress({
            **valid,
            "lastErrorCode": "https://backend.example/secret",
        })
    validate_enrollment_progress({
        **valid,
        "lastErrorCode": "K1_CLEANUP_FAILED",
    })


def test_runtime_progress_uses_exact_schema_and_monotonic_heartbeat(tmp_path):
    readings = iter((12.345, 17.999))
    path = tmp_path / "hardware" / "factory-progress.json"
    writer = RuntimeProgressWriter(path, monotonic=lambda: next(readings))

    first = writer.report(
        service_state="RUNNING",
        uart_state="READY",
        mqtt_state="CONNECTED",
    )
    second = writer.report(p8_phase="CAMERA_CAPTURE")

    assert first["observedMonotonicMs"] == 12_345
    assert second == {
        "schemaVersion": 1,
        "serviceState": "RUNNING",
        "uartState": "READY",
        "mqttState": "CONNECTED",
        "p8Phase": "CAMERA_CAPTURE",
        "lastErrorCode": None,
        "observedMonotonicMs": 17_999,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == second
    assert path.stat().st_size <= 512


def test_runtime_progress_preserves_p8_failure_across_mqtt_state_changes(
    tmp_path,
):
    path = tmp_path / "hardware" / "factory-progress.json"
    writer = RuntimeProgressWriter(path, monotonic=lambda: 20.0)
    writer.report(
        service_state="RUNNING",
        uart_state="READY",
        mqtt_state="CONNECTED",
    )
    writer.report(
        p8_phase="FAILED",
        last_error_code="P8_CAMERA_CAPTURE_FAILED",
    )

    disconnected = writer.report(
        mqtt_state="FAILED",
        last_error_code="MQTT_DISCONNECTED",
    )
    reconnected = writer.report(
        mqtt_state="CONNECTED",
        last_error_code=None,
    )

    assert disconnected["lastErrorCode"] == "P8_CAMERA_CAPTURE_FAILED"
    assert reconnected["lastErrorCode"] == "P8_CAMERA_CAPTURE_FAILED"

    next_attempt = writer.report(
        p8_phase="REQUEST_RECEIVED",
        last_error_code=None,
    )
    assert next_attempt["lastErrorCode"] is None


def test_runtime_progress_rejects_unknown_fields_and_error_content():
    document = {
        "schemaVersion": 1,
        "serviceState": "RUNNING",
        "uartState": "READY",
        "mqttState": "CONNECTED",
        "p8Phase": "IDLE",
        "lastErrorCode": None,
        "observedMonotonicMs": 1,
    }
    validate_runtime_progress(document)
    with pytest.raises(ValueError, match="fields"):
        validate_runtime_progress({**document, "cosUrl": "secret"})
    with pytest.raises(ValueError, match="allowlisted"):
        validate_runtime_progress({
            **document,
            "lastErrorCode": "TOKEN_ABC123",
        })
    with pytest.raises(ValueError, match="non-negative"):
        validate_runtime_progress({
            **document,
            "observedMonotonicMs": -1,
        })


def test_runtime_progress_refuses_symlink_destination(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("do not replace", encoding="utf-8")
    link = tmp_path / "factory-progress.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    writer = RuntimeProgressWriter(link)
    with pytest.raises(ValueError, match="regular file"):
        writer.report()
    assert target.read_text(encoding="utf-8") == "do not replace"


class _OneHeartbeat:
    def __init__(self):
        self.calls = 0

    def wait(self, timeout):
        assert timeout == 5.0
        self.calls += 1
        return self.calls > 1


class _ProgressCapture:
    def __init__(self):
        self.values = []

    def report(self, **values):
        self.values.append(values)


def test_runtime_factory_heartbeat_refreshes_uart_and_mqtt_every_five_seconds():
    edge = EcoBinEdge.__new__(EcoBinEdge)
    edge._exit_flag = _OneHeartbeat()
    edge._uart_recovering = type("Flag", (), {"is_set": lambda _self: False})()
    edge.uart = type(
        "Uart",
        (),
        {"is_open": True, "mcu_session_ready": True},
    )()
    edge.cloud_transport = type(
        "CloudTransport",
        (),
        {"connected": True},
    )()
    edge.factory_progress = _ProgressCapture()

    edge._factory_progress_loop()

    assert edge.factory_progress.values == [{
        "uart_state": "READY",
        "mqtt_state": "CONNECTED",
    }]
