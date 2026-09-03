from __future__ import annotations

import pytest

from business_control import McuMaintenancePortError
from fixed_frame_mcu_maintenance import FixedFrameMcuMaintenancePort


def _identity(*, mode: int = 1, safe_flags: int = 0x0F) -> dict:
    return {
        "queryStatus": "OK",
        "mode": mode,
        "statusCode": 0,
        "protocolRevision": 2,
        "firmwareVersionCode": 17,
        "firmwareVersion": "1.2.3",
        "firmwareIdentityHex": "0123456789abcdef",
        "safeFlags": safe_flags,
    }


def _self_test(**overrides) -> dict:
    result = {
        "queryStatus": "OK",
        "communicationHealthy": True,
        "validFlags": 3,
        "weightGrams": 12345,
        "infraredBlocked": False,
        "smokeState": "NORMAL",
        "smokeSensorHealth": "OK",
    }
    result.update(overrides)
    return result


class FakeFixedFrameUart:
    def __init__(self) -> None:
        self.is_open = True
        self.identity = _identity()
        self.self_test = _self_test()
        self.prepare = {**_identity(mode=2, safe_flags=0x1F), "executed": True}
        self.calls: list[str] = []
        self.prepare_error: Exception | None = None
        self.open_result = True

    def open(self) -> bool:
        self.calls.append("OPEN")
        self.is_open = self.open_result
        return self.open_result

    def close(self) -> None:
        self.calls.append("CLOSE")
        self.is_open = False

    def query_firmware_identity(self) -> dict:
        self.calls.append("F3")
        return dict(self.identity)

    def query_self_test(self) -> dict:
        self.calls.append("F1")
        return dict(self.self_test)

    def execute_firmware_update_prepare(self) -> dict:
        self.calls.append("F2")
        if self.prepare_error is not None:
            raise self.prepare_error
        return dict(self.prepare)


def _port(
    uart: FakeFixedFrameUart,
    *,
    active_work=None,
    stopping: bool = False,
    authorize=lambda _update_uid, _stage, **_bindings: None,
) -> FixedFrameMcuMaintenancePort:
    return FixedFrameMcuMaintenancePort(
        uart,
        active_work_reader=lambda: active_work,
        shutdown_requested=lambda: stopping,
        maintenance_authorizer=authorize,
    )


def test_observation_reads_fresh_f3_and_f1_without_handing_off_uart() -> None:
    uart = FakeFixedFrameUart()

    evidence = _port(uart).observe_mcu_maintenance_state()

    assert uart.calls == ["F3", "F1"]
    assert evidence.f3.firmware_identity is not None
    assert evidence.f3.firmware_identity.firmware_version_code == 17
    assert evidence.f1.weight_grams == 12345
    assert evidence.uart_handed_off is False


def test_malformed_success_response_becomes_protocol_error_evidence() -> None:
    uart = FakeFixedFrameUart()
    uart.identity["safeFlags"] = 0x80
    uart.self_test["validFlags"] = 7

    evidence = _port(uart).observe_mcu_maintenance_state()

    assert evidence.f3.query_status == "PROTOCOL_ERROR"
    assert evidence.f3.firmware_identity is None
    assert evidence.f1.query_status == "PROTOCOL_ERROR"


def test_quiesce_reproves_sensors_executes_f2_then_closes_uart() -> None:
    uart = FakeFixedFrameUart()

    evidence = _port(uart).quiesce_mcu_for_update(
        update_uid="22222222-2222-4222-8222-222222222222",
        handoff_uid="33333333-3333-4333-8333-333333333333",
        expected_observation_sha256="a" * 64,
    )

    assert uart.calls == ["F3", "F1", "F2", "CLOSE"]
    assert evidence.f3.mode == 2
    assert evidence.f3.safe_flags == 0x1F
    assert evidence.f1.query_status == "OK"
    assert evidence.uart_handed_off is True
    assert uart.is_open is False


def test_active_work_or_bad_self_test_prevents_f2() -> None:
    active_uart = FakeFixedFrameUart()
    with pytest.raises(McuMaintenancePortError) as active:
        _port(active_uart, active_work={"workUid": "busy"}).quiesce_mcu_for_update(
            update_uid="ignored",
            handoff_uid="ignored",
            expected_observation_sha256="ignored",
        )
    assert active.value.code == "MCU_MAINTENANCE_BUSY"
    assert active_uart.calls == []

    unhealthy_uart = FakeFixedFrameUart()
    unhealthy_uart.self_test = _self_test(
        queryStatus="TIMEOUT",
        communicationHealthy=False,
        validFlags=0,
        weightGrams=None,
        infraredBlocked=None,
        smokeState="UNKNOWN",
        smokeSensorHealth="TIMEOUT",
    )
    with pytest.raises(McuMaintenancePortError) as unhealthy:
        _port(unhealthy_uart).quiesce_mcu_for_update(
            update_uid="ignored",
            handoff_uid="ignored",
            expected_observation_sha256="ignored",
        )
    assert unhealthy.value.code == "MCU_QUIESCE_FAILED"
    assert unhealthy_uart.calls == ["F3", "F1"]
    assert unhealthy_uart.is_open is True


def test_uncertain_f2_always_relinquishes_uart_and_returns_failed_fact() -> None:
    uart = FakeFixedFrameUart()
    uart.prepare = {
        "queryStatus": "TIMEOUT",
        "mode": 2,
        "statusCode": None,
        "protocolRevision": None,
        "firmwareVersionCode": None,
        "firmwareVersion": None,
        "firmwareIdentityHex": None,
        "safeFlags": None,
    }

    evidence = _port(uart).quiesce_mcu_for_update(
        update_uid="ignored",
        handoff_uid="ignored",
        expected_observation_sha256="ignored",
    )

    assert uart.calls[-2:] == ["F2", "CLOSE"]
    assert evidence.f3.query_status == "TIMEOUT"
    assert evidence.uart_handed_off is True


def test_f2_exception_also_relinquishes_uart_and_fails_closed() -> None:
    uart = FakeFixedFrameUart()
    uart.prepare_error = RuntimeError("serial write result unknown")

    with pytest.raises(McuMaintenancePortError) as raised:
        _port(uart).quiesce_mcu_for_update(
            update_uid="ignored",
            handoff_uid="ignored",
            expected_observation_sha256="ignored",
        )

    assert raised.value.code == "MCU_QUIESCE_FAILED"
    assert uart.calls[-2:] == ["F2", "CLOSE"]
    assert uart.is_open is False


def test_verification_reopens_uart_and_reads_fresh_identity_and_sensors() -> None:
    uart = FakeFixedFrameUart()
    uart.is_open = False

    evidence = _port(uart).verify_mcu_after_update(
        update_uid="ignored",
        handoff_uid="ignored",
        expected_firmware_identity_sha256="ignored",
        observed_flash_evidence_sha256="ignored",
        quiesce_evidence_sha256="ignored",
    )

    assert uart.calls == ["OPEN", "F3", "F1"]
    assert evidence.f3.query_status == "OK"
    assert evidence.f1.query_status == "OK"
    assert evidence.uart_handed_off is False


def test_shutdown_or_unreadable_work_state_blocks_hardware_access() -> None:
    uart = FakeFixedFrameUart()
    with pytest.raises(McuMaintenancePortError) as stopping:
        _port(uart, stopping=True).observe_mcu_maintenance_state()
    assert stopping.value.code == "MCU_MAINTENANCE_BUSY"
    assert uart.calls == []

    port = FixedFrameMcuMaintenancePort(
        uart,
        active_work_reader=lambda: (_ for _ in ()).throw(OSError("db")),
        shutdown_requested=lambda: False,
        maintenance_authorizer=lambda _update_uid, _stage, **_bindings: None,
    )
    with pytest.raises(McuMaintenancePortError) as unreadable:
        port.verify_mcu_after_update(
            update_uid="ignored",
            handoff_uid="ignored",
            expected_firmware_identity_sha256="ignored",
            observed_flash_evidence_sha256="ignored",
            quiesce_evidence_sha256="ignored",
        )
    assert unreadable.value.code == "MCU_OBSERVATION_UNAVAILABLE"
    assert uart.calls == []


def test_unconfirmed_permanent_maintenance_fence_prevents_uart_access() -> None:
    uart = FakeFixedFrameUart()

    def reject(_update_uid: str, _stage: str, **_bindings: str) -> None:
        raise RuntimeError("gate is not in maintenance")

    with pytest.raises(McuMaintenancePortError) as raised:
        _port(uart, authorize=reject).quiesce_mcu_for_update(
            update_uid="22222222-2222-4222-8222-222222222222",
            handoff_uid="ignored",
            expected_observation_sha256="ignored",
        )

    assert raised.value.code == "MCU_MAINTENANCE_NOT_AUTHORIZED"
    assert uart.calls == []
