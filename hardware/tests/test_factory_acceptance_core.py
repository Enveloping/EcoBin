from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.acceptance_core import AcceptanceError, FactoryAcceptanceExecutor
from factory.acceptance_hardware import (
    AcceptanceHardwareError,
    CLEAN_WIRE,
    DELIVERY_WIRE,
    FixedFrameAcceptanceMcu,
    FixedRoleCameraProbe,
    ReadOnlyStm32RomProbe,
    VirtualRomProbe,
)
from factory.acceptance_storage import AcceptanceLockBusy
from tools.fixed_frame_pty_simulator import (
    CLEAN_RESULT_HEADER,
    DELIVERY_RESULT_HEADER,
    SimulatorConfig,
    VirtualFixedFrameMcu,
    VirtualFixedFrameSerialFactory,
    encode_result_frame,
)


class PowerLoss(BaseException):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture(source: str, destination: Path) -> None:
    destination.write_bytes(b"local-camera-probe:" + source.encode("ascii"))


def _paths(tmp_path: Path) -> dict:
    return {
        "state_path": tmp_path / "acceptance" / "state.json",
        "report_path": tmp_path / "acceptance" / "report.json",
        "instance_lock_path": tmp_path / "locks" / "acceptance.lock",
        "uart_lock_path": tmp_path / "locks" / "uart5.lock",
    }


def _build_executor(
    tmp_path: Path,
    *,
    config: SimulatorConfig | None = None,
    model: VirtualFixedFrameMcu | None = None,
    serial_factory: VirtualFixedFrameSerialFactory | None = None,
    fault_hook=None,
    capture=_capture,
    executor_options: dict | None = None,
):
    model = model or VirtualFixedFrameMcu(config or SimulatorConfig())
    serial_factory = serial_factory or VirtualFixedFrameSerialFactory(model)
    mcu = FixedFrameAcceptanceMcu.for_port(
        "simulated://factory-mcu",
        timeout_s=0.005,
        serial_factory=serial_factory,
        is_simulated=True,
    )
    cameras = FixedRoleCameraProbe(
        outside_source="simulated://outside",
        inside_source="simulated://inside",
        temporary_directory=tmp_path / "camera-temp",
        capture=capture,
        allow_simulated=True,
    )
    executor = FactoryAcceptanceExecutor(
        mcu=mcu,
        bootloader=VirtualRomProbe(model),
        cameras=cameras,
        fault_hook=fault_hook,
        **(executor_options or {}),
        **_paths(tmp_path),
    )
    return executor, model, serial_factory


def _begin(executor: FactoryAcceptanceExecutor) -> None:
    executor.begin_run(
        image_release_id="ecobin-zero3-1.0.0",
        boot_id="11111111-2222-3333-4444-555555555555",
        wall_time_trusted=False,
        hardware_config_digest="a" * 64,
    )


def _set_weight(model: VirtualFixedFrameMcu, value: int) -> None:
    object.__setattr__(model.config, "self_test_weight_grams", value)


def _pass_prerequisites(
    executor: FactoryAcceptanceExecutor,
    model: VirtualFixedFrameMcu,
) -> None:
    _begin(executor)
    executor.check_mcu()
    _set_weight(model, 1_000)
    executor.capture_empty_weight()
    _set_weight(model, 1_500)
    executor.capture_loaded_weight()
    _set_weight(model, 1_000)
    executor.confirm_weight_removed()
    camera_state = executor.capture_cameras()
    executor.confirm_cameras(
        review_nonce=camera_state["checks"]["cameras"]["reviewNonce"],
        outside_role_confirmed=True,
        inside_role_confirmed=True,
    )
    executor.check_upgrade_line()


def test_revision_2_f3_and_healthy_f1_are_required(tmp_path: Path) -> None:
    executor, _model, _factory = _build_executor(
        tmp_path,
        config=SimulatorConfig(firmware_identity_status=3),
    )
    with executor:
        _begin(executor)
        with pytest.raises(
            AcceptanceError,
            match="MCU_F3_NOT_TRUSTED_REVISION_2",
        ):
            executor.check_mcu()
        state = executor.snapshot()
        assert state["checks"]["mcu"] == {
            "status": "FAILED",
            "resultCode": "MCU_F3_NOT_TRUSTED_REVISION_2",
        }


def test_invalid_f1_blocks_mcu_check(tmp_path: Path) -> None:
    executor, _model, _factory = _build_executor(
        tmp_path,
        config=SimulatorConfig(self_test_weight_valid=0),
    )
    with executor:
        _begin(executor)
        with pytest.raises(AcceptanceError, match="MCU_F1_UNHEALTHY"):
            executor.check_mcu()


@pytest.mark.parametrize(
    ("delta", "passed"),
    ((489, False), (490, True), (510, True), (511, False)),
)
def test_500g_weight_boundaries(
    tmp_path: Path,
    delta: int,
    passed: bool,
) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        executor.check_mcu()
        _set_weight(model, 2_000)
        executor.capture_empty_weight()
        _set_weight(model, 2_000 + delta)
        if passed:
            executor.capture_loaded_weight()
            _set_weight(model, 2_000)
            executor.confirm_weight_removed()
            assert executor.snapshot()["checks"]["weight"]["status"] == "PASSED"
        else:
            with pytest.raises(AcceptanceError, match="WEIGHT_DELTA_OUT_OF_RANGE"):
                executor.capture_loaded_weight()
            assert executor.snapshot()["checks"]["weight"]["status"] == "FAILED"


def test_test_weight_must_be_removed_before_actions(tmp_path: Path) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        executor.check_mcu()
        _set_weight(model, 1_000)
        executor.capture_empty_weight()
        _set_weight(model, 1_500)
        executor.capture_loaded_weight()
        with pytest.raises(AcceptanceError, match="TEST_WEIGHT_NOT_REMOVED"):
            executor.confirm_weight_removed()


def test_weight_uses_three_sample_stable_median_for_all_three_stages(
    tmp_path: Path,
) -> None:
    executor, _model, _factory = _build_executor(
        tmp_path,
        executor_options={"weight_sample_interval_ms": 0},
    )
    with executor:
        _begin(executor)
        executor.check_mcu()

        samples = iter((1000, 1002, 1001))
        executor._query_self_test = lambda: {
            "weightGrams": next(samples),
            "infraredBlocked": False,
            "smokeCode": 0,
        }
        executor.capture_empty_weight()

        samples = iter((1499, 1501, 1500))
        executor.capture_loaded_weight()

        samples = iter((1002, 1000, 1001))
        state = executor.confirm_weight_removed()

        weight = state["checks"]["weight"]
        assert weight["emptyWeightGrams"] == 1001
        assert weight["loadedWeightGrams"] == 1500
        assert weight["removedWeightGrams"] == 1001
        assert weight["deltaGrams"] == 499


def test_persistent_weight_jitter_times_out_as_unstable(tmp_path: Path) -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    executor, _model, _factory = _build_executor(
        tmp_path,
        executor_options={
            "monotonic": lambda: now[0],
            "sleeper": sleep,
            "weight_sample_interval_ms": 1,
            "weight_sample_timeout_ms": 5,
        },
    )
    with executor:
        _begin(executor)
        executor.check_mcu()
        values = iter((1000, 1010) * 20)
        executor._query_self_test = lambda: {
            "weightGrams": next(values),
            "infraredBlocked": False,
            "smokeCode": 0,
        }

        with pytest.raises(AcceptanceError, match="WEIGHT_READING_NOT_STABLE"):
            executor.capture_empty_weight()

        assert executor.snapshot()["checks"]["weight"]["status"] == "FAILED"


def test_f1_timeout_during_stable_sampling_is_a_failed_weight_fact(
    tmp_path: Path,
) -> None:
    executor, _model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        executor.check_mcu()

        def timeout() -> dict:
            raise AcceptanceHardwareError("MCU_F1_QUERY_TIMEOUT")

        executor._query_self_test = timeout
        with pytest.raises(AcceptanceError, match="MCU_F1_QUERY_TIMEOUT"):
            executor.capture_empty_weight()
        assert executor.snapshot()["checks"]["weight"] == {
            "status": "FAILED",
            "resultCode": "MCU_F1_QUERY_TIMEOUT",
        }


def test_upgrade_line_is_read_only_and_recovers_original_application(
    tmp_path: Path,
) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        executor.check_mcu()
        result = executor.check_upgrade_line()
        check = result["checks"]["upgradeLine"]
        assert check["status"] == "PASSED"
        assert check["romWritePerformed"] is False
        assert model.firmware_prepare_count == 1
        assert model.runtime_mode == "APPLICATION"
        assert model.update_prepared is False
        assert executor.bootloader.read_only_probe_count == 1


def test_real_rom_probe_command_has_no_flash_mutation_and_uses_wpi_2_5() -> None:
    levels = {2: 0, 5: 0}
    gpio_calls: list[list[str]] = []
    rom_calls: list[list[str]] = []

    def gpio_runner(argv, **kwargs):
        del kwargs
        gpio_calls.append(list(argv))
        command = argv[1]
        pin = int(argv[2])
        if command == "write":
            levels[pin] = int(argv[3])
            output = ""
        elif command == "read":
            output = str(levels[pin])
        else:
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)

    def rom_runner(argv, **kwargs):
        del kwargs
        rom_calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="Device ID: 0x0410")

    probe = ReadOnlyStm32RomProbe(
        gpio_path="/usr/bin/gpio",
        boot0_wpi=2,
        reset_wpi=5,
        stm32flash_path="/usr/bin/stm32flash",
        serial_port="/dev/ttyS5",
        runner=rom_runner,
        gpio_runner=gpio_runner,
        sleeper=lambda _seconds: None,
    )
    probe.enter_system_bootloader()
    assert levels == {2: 1, 5: 0}
    assert probe.probe_read_only()["detected"] is True
    probe.boot_application()
    assert levels == {2: 0, 5: 0}
    assert rom_calls == [[
        "/usr/bin/stm32flash",
        "-b",
        "115200",
        "-m",
        "8e1",
        "/dev/ttyS5",
    ]]
    flattened = " ".join(rom_calls[0])
    for forbidden in (" -e ", " -w ", " -r ", " -g ", " -v "):
        assert forbidden not in f" {flattened} "
    assert ["/usr/bin/gpio", "write", "2", "1"] in gpio_calls
    assert ["/usr/bin/gpio", "write", "5", "1"] in gpio_calls
    assert ["/usr/bin/gpio", "write", "5", "0"] in gpio_calls


@pytest.mark.parametrize(
    "output",
    (
        "stm32flash succeeded without an identity",
        "Device ID: 0x0412",
        "Device ID: 0x00000411",
    ),
)
def test_rom_probe_rejects_success_exit_for_missing_or_wrong_device_id(
    output: str,
) -> None:
    probe = ReadOnlyStm32RomProbe(
        gpio_path="/usr/bin/gpio",
        boot0_wpi=2,
        reset_wpi=5,
        runner=lambda _argv, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=output,
        ),
    )

    with pytest.raises(AcceptanceHardwareError, match="STM32_ROM_IDENTITY_INVALID"):
        probe.probe_read_only()


@pytest.mark.parametrize("output", ("Device ID: 0x410", "DEVICE ID: 0X00000410"))
def test_rom_probe_normalizes_valid_stm32f103c8_device_id(output: str) -> None:
    probe = ReadOnlyStm32RomProbe(
        gpio_path="/usr/bin/gpio",
        boot0_wpi=2,
        reset_wpi=5,
        runner=lambda _argv, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=output,
        ),
    )

    assert probe.probe_read_only() == {
        "detected": True,
        "resultCode": "STM32F103C8_ROM_DETECTED_READ_ONLY",
        "deviceId": "0x0410",
    }


def test_prepare_journal_precedes_f2_and_power_loss_requires_recovery(
    tmp_path: Path,
) -> None:
    def fault(point: str) -> None:
        if point == "upgrade_line.after_prepare_journal":
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _begin(executor)
            executor.check_mcu()
            executor.check_upgrade_line()
    assert model.firmware_prepare_count == 0

    recovered, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with recovered:
        state = recovered.snapshot()
        assert state["status"] == "RECOVERY_REQUIRED"
        assert state["phase"] == "F2_COMMAND_MAY_HAVE_BEEN_SENT"
        recovered.recover()
        assert recovered.snapshot()["status"] == "RUNNING"
        assert model.firmware_prepare_count == 0


def test_power_loss_after_f2_never_resends_prepare_and_requires_app_reproof(
    tmp_path: Path,
) -> None:
    tripped = False

    def fault(point: str) -> None:
        nonlocal tripped
        if point == "upgrade_line.after_prepare_response" and not tripped:
            tripped = True
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _begin(executor)
            executor.check_mcu()
            executor.check_upgrade_line()
    assert model.firmware_prepare_count == 1

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        state = resumed.snapshot()
        assert state["status"] == "RECOVERY_REQUIRED"
        assert state["phase"] == "F2_PREPARED"
        assert state["checks"]["upgradeLine"]["prepareSendAttempts"] == 1

        recovered = resumed.recover(quiet_ms=0)

        assert recovered["status"] == "RUNNING"
        assert recovered["checks"]["upgradeLine"]["status"] == "FAILED_SAFE"
        assert recovered["checks"]["upgradeLine"]["resultCode"] == (
            "UPGRADE_LINE_FAILED_BUT_APPLICATION_RECOVERED"
        )
        assert model.firmware_prepare_count == 1


def test_delivery_bb_and_aa_are_one_write_and_never_retried(tmp_path: Path) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        executor.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        assert model.delivery_start_count == 1
        writes = [wire for session in factory.sessions for wire in session.writes]
        assert writes.count(DELIVERY_WIRE) == 1
        check = executor.snapshot()["checks"]["delivery"]
        assert check["status"] == "PASSED"
        assert check["sendAttempts"] == 1


def test_clean_ee_is_one_write_and_requires_manual_door_confirmation(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        assert model.clean_start_count == 1
        writes = [wire for session in factory.sessions for wire in session.writes]
        assert writes.count(CLEAN_WIRE) == 1
        assert executor.snapshot()["status"] == "RECOVERY_REQUIRED"
        with pytest.raises(AcceptanceError, match="USE_CLEAN_DOOR_CONFIRMATION"):
            executor.recover(clean_door_closed_confirmed=True, quiet_ms=0)
        executor.confirm_clean_door_closed(operator_confirmed=True, quiet_ms=0)
        assert executor.snapshot()["status"] == "RUNNING"
        assert executor.snapshot()["checks"]["clean"]["status"] == "PASSED"
        assert model.clean_start_count == 1

        revision = executor.snapshot()["revision"]
        executor.confirm_clean_door_closed(operator_confirmed=True, quiet_ms=0)
        assert executor.snapshot()["revision"] == revision


def test_clean_start_rejects_any_pre_action_door_confirmation_argument(
    tmp_path: Path,
) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        with pytest.raises(TypeError, match="clean_door_closed_confirmed"):
            executor.run_action(
                "CLEAN",
                operator_area_safe_confirmed=True,
                clean_door_closed_confirmed=True,
            )
        assert model.clean_start_count == 0


def test_power_loss_after_ef_before_door_confirmation_stays_locked(
    tmp_path: Path,
) -> None:
    tripped = False

    def fault(point: str) -> None:
        nonlocal tripped
        if point == "clean.after_awaiting_door_confirmation" and not tripped:
            tripped = True
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            executor.run_action(
                "CLEAN",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
    assert model.clean_start_count == 1

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        assert resumed.snapshot()["phase"] == "CLEAN_AWAITING_DOOR_CONFIRMATION"
        with pytest.raises(AcceptanceError, match="CLEAN_DOOR_CONFIRMATION_REQUIRED"):
            resumed.confirm_clean_door_closed(
                operator_confirmed=False,
                quiet_ms=0,
            )
        resumed.confirm_clean_door_closed(operator_confirmed=True, quiet_ms=0)
        assert resumed.snapshot()["checks"]["clean"]["status"] == "PASSED"
        assert model.clean_start_count == 1


def test_power_loss_after_action_journal_does_not_send_or_auto_retry(
    tmp_path: Path,
) -> None:
    armed = False

    def fault(point: str) -> None:
        nonlocal armed
        if point == "delivery.after_command_journal" and not armed:
            armed = True
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
            )
    assert model.delivery_start_count == 0
    persisted = json.loads(_paths(tmp_path)["state_path"].read_text("utf-8"))
    assert persisted["status"] == "RECOVERY_REQUIRED"
    assert persisted["activeAction"]["sendAttempts"] == 1

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        resumed.recover(quiet_ms=0)
        assert model.delivery_start_count == 0


def test_power_loss_while_armed_is_closed_as_proven_not_sent(
    tmp_path: Path,
) -> None:
    tripped = False

    def fault(point: str) -> None:
        nonlocal tripped
        if point == "delivery.after_armed" and not tripped:
            tripped = True
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
            )
    assert model.delivery_start_count == 0

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        state = resumed.snapshot()
        assert state["status"] == "RUNNING"
        assert state["activeAction"] is None
        assert state["checks"]["delivery"] == {
            "status": "FAILED_SAFE",
            "resultCode": "DELIVERY_INTERRUPTED_BEFORE_COMMAND",
            "sendAttempts": 0,
            "result": None,
        }
        assert model.delivery_start_count == 0


def test_power_loss_after_uart_write_keeps_one_send_and_requires_recovery(
    tmp_path: Path,
) -> None:
    tripped = False

    def fault(point: str) -> None:
        nonlocal tripped
        if point == "delivery.after_serial_write" and not tripped:
            tripped = True
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
            )
    assert model.delivery_start_count == 1

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        assert resumed.snapshot()["status"] == "RECOVERY_REQUIRED"
        resumed.recover(quiet_ms=0)
        assert model.delivery_start_count == 1


@pytest.mark.parametrize(
    ("failure_code", "replacement"),
    (
        ("FINAL_RESULT_TIMEOUT", b""),
        (
            "WRONG_OR_LATE_FINAL_RESULT",
            encode_result_frame(CLEAN_RESULT_HEADER, 1_000, 500, 0),
        ),
        (
            "CORRUPT_FINAL_RESULT",
            bytes.fromhex("DD 00 00 01 00 00 02 02 DD"),
        ),
    ),
)
def test_timeout_wrong_or_corrupt_final_result_requires_recovery(
    tmp_path: Path,
    failure_code: str,
    replacement: bytes,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        original = executor.mcu.await_final_result

        def replace_then_wait(action: str, timeout_ms: int):
            serial = factory.sessions[-1]
            with serial._condition:
                serial._received[:] = replacement
                serial._condition.notify_all()
            return original(action, timeout_ms)

        executor.mcu.await_final_result = replace_then_wait
        with pytest.raises(AcceptanceError, match=failure_code):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=20,
                quiet_ms=0,
            )
        state = executor.snapshot()
        assert state["status"] == "RECOVERY_REQUIRED"
        assert state["checks"]["delivery"]["sendAttempts"] == 1
        assert model.delivery_start_count == 1


def test_duplicate_final_result_after_recording_requires_recovery(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        original = executor.mcu.await_final_result

        def duplicate_after_result(action: str, timeout_ms: int):
            result = original(action, timeout_ms)
            duplicate = encode_result_frame(
                DELIVERY_RESULT_HEADER,
                10_000,
                11_200,
                0,
            )
            serial = factory.sessions[-1]
            with serial._condition:
                serial._received.extend(duplicate)
                serial._condition.notify_all()
            return result

        executor.mcu.await_final_result = duplicate_after_result
        with pytest.raises(
            AcceptanceError,
            match="DUPLICATE_OR_LATE_FINAL_RESULT",
        ):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=5,
            )
        assert executor.snapshot()["phase"] == "DELIVERY_RESULT_RECORDED"


def test_corrupt_late_result_consumed_during_f3_still_requires_recovery(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        original = executor.mcu.await_final_result

        def corrupt_after_result(action: str, timeout_ms: int):
            result = original(action, timeout_ms)
            corrupt = bytes.fromhex("DD 00 00 01 00 00 02 02 DD")
            serial = factory.sessions[-1]
            with serial._condition:
                serial._received.extend(corrupt)
                serial._condition.notify_all()
            return result

        executor.mcu.await_final_result = corrupt_after_result
        with pytest.raises(
            AcceptanceError,
            match="CORRUPT_OR_INCOMPLETE_LATE_RESULT",
        ):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
        assert executor.snapshot()["status"] == "RECOVERY_REQUIRED"


def test_stale_result_is_rejected_before_new_action_write(tmp_path: Path) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        stale = encode_result_frame(
            DELIVERY_RESULT_HEADER,
            100,
            200,
            0,
        )
        serial = factory.sessions[-1]
        with serial._condition:
            serial._received.extend(stale)
            serial._condition.notify_all()
        with pytest.raises(AcceptanceError, match="STALE_BUSINESS_RESULT"):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
        assert model.delivery_start_count == 0
        assert executor.snapshot()["status"] == "RECOVERY_REQUIRED"


def test_same_type_result_arriving_before_physical_action_window_is_late(
    tmp_path: Path,
) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    executor.mcu.minimum_action_result_delay_ms = 1_000
    with executor:
        _pass_prerequisites(executor, model)
        with pytest.raises(
            AcceptanceError,
            match="LATE_FINAL_RESULT_BEFORE_ACTION_WINDOW",
        ):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
        assert executor.snapshot()["status"] == "RECOVERY_REQUIRED"
        assert model.delivery_start_count == 1


def test_short_serial_write_is_never_retried(tmp_path: Path) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)

        def short_write(_action: str) -> None:
            raise OSError("short write")

        executor.mcu.write_action_once = short_write
        with pytest.raises(AcceptanceError, match="DELIVERY_UNEXPECTED_FAILURE"):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=20,
            )
        state = executor.snapshot()
        assert state["status"] == "RECOVERY_REQUIRED"
        assert state["activeAction"]["sendAttempts"] == 1
        assert model.delivery_start_count == 0


def test_two_executors_cannot_own_factory_uart_concurrently(tmp_path: Path) -> None:
    first, _model, _factory = _build_executor(tmp_path)
    second, _model2, _factory2 = _build_executor(tmp_path / "other-state")
    # Share only the lock paths while keeping independent test state paths.
    second._lease = type(first._lease)(
        _paths(tmp_path)["instance_lock_path"],
        _paths(tmp_path)["uart_lock_path"],
    )
    first.open()
    try:
        with pytest.raises(AcceptanceLockBusy):
            second.open()
    finally:
        first.close()


def test_network_attempt_from_camera_probe_is_blocked(tmp_path: Path) -> None:
    def network_capture(_source: str, _destination: Path) -> None:
        socket.socket()

    executor, model, _factory = _build_executor(
        tmp_path,
        capture=network_capture,
    )
    with executor:
        _begin(executor)
        executor.check_mcu()
        with pytest.raises(AcceptanceError, match="NETWORK_ACCESS_FORBIDDEN"):
            executor.capture_cameras()
        assert executor.snapshot()["checks"]["cameras"] == {
            "status": "FAILED",
            "resultCode": "NETWORK_ACCESS_FORBIDDEN",
        }


def test_camera_roles_can_only_be_confirmed_for_current_captured_nonce(
    tmp_path: Path,
) -> None:
    executor, _model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        executor.check_mcu()
        captured = executor.capture_cameras()
        nonce = captured["checks"]["cameras"]["reviewNonce"]
        assert len(list((tmp_path / "camera-temp").glob("*.jpg"))) == 2

        with pytest.raises(AcceptanceError, match="CAMERA_REVIEW_NONCE_MISMATCH"):
            executor.confirm_cameras(
                review_nonce="0" * 32,
                outside_role_confirmed=True,
                inside_role_confirmed=True,
            )

        state = executor.confirm_cameras(
            review_nonce=nonce,
            outside_role_confirmed=True,
            inside_role_confirmed=True,
        )
        assert state["checks"]["cameras"]["status"] == "PASSED"
        assert list((tmp_path / "camera-temp").glob("*.jpg")) == []


def test_expired_camera_capture_is_deleted_and_cannot_be_confirmed(
    tmp_path: Path,
) -> None:
    now = [1.0]
    executor, _model, _factory = _build_executor(
        tmp_path,
        executor_options={
            "monotonic": lambda: now[0],
            "camera_review_ttl_ms": 10,
        },
    )
    with executor:
        _begin(executor)
        executor.check_mcu()
        captured = executor.capture_cameras()
        nonce = captured["checks"]["cameras"]["reviewNonce"]
        now[0] = 2.0

        with pytest.raises(AcceptanceError, match="CAMERA_REVIEW_EXPIRED"):
            executor.confirm_cameras(
                review_nonce=nonce,
                outside_role_confirmed=True,
                inside_role_confirmed=True,
            )

        assert list((tmp_path / "camera-temp").glob("*.jpg")) == []


def test_executor_restart_invalidates_pending_camera_review(tmp_path: Path) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        executor.check_mcu()
        captured = executor.capture_cameras()
        old_nonce = captured["checks"]["cameras"]["reviewNonce"]

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        assert resumed.snapshot()["checks"]["cameras"] == {
            "status": "FAILED",
            "resultCode": "CAMERA_REVIEW_INVALIDATED_BY_EXECUTOR_RESTART",
        }
        with pytest.raises(AcceptanceError, match="CAMERA_CAPTURE_REQUIRED"):
            resumed.confirm_cameras(
                review_nonce=old_nonce,
                outside_role_confirmed=True,
                inside_role_confirmed=True,
            )


def test_full_pass_report_is_private_sanitized_and_does_not_touch_production_data(
    tmp_path: Path,
) -> None:
    production_db = tmp_path / "production" / "edge.sqlite"
    production_photo = tmp_path / "production" / "photos" / "existing.jpg"
    production_photo.parent.mkdir(parents=True)
    production_db.write_bytes(b"edge-store-production-bytes")
    production_photo.write_bytes(b"production-photo-bytes")
    before_db = _sha256(production_db)
    before_photo = _sha256(production_photo)

    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        executor.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        executor.confirm_clean_door_closed(operator_confirmed=True, quiet_ms=0)
        report = executor.finalize()
        assert report["status"] == "PASSED"
        assert report["recoveryRequired"] is False
        assert report["hardwareConfigDigest"] == "a" * 64
        assert report["checks"]["weight"]["deltaGrams"] == 500
        assert report["checks"]["delivery"]["weightDeltaGrams"] == 1_200
        assert report["checks"]["clean"]["weightDeltaGrams"] == 10_400
        assert report["checks"]["upgradeLine"]["romWritePerformed"] is False
        assert report["checks"]["upgradeLine"]["romDeviceId"] == "0x0410"
        assert executor.read_report() == report

    report_path = _paths(tmp_path)["report_path"]
    if os.name != "nt":
        assert stat.S_IMODE(report_path.stat().st_mode) & 0o077 == 0
    report_text = report_path.read_text("utf-8").lower()
    for forbidden in (
        "rawframe",
        "commanduid",
        "password",
        "onenet",
        "mqtt",
        "orderuid",
        "bagcode",
        "photo.jpg",
    ):
        assert forbidden not in report_text
    assert list((tmp_path / "camera-temp").glob("*.jpg")) == []
    assert _sha256(production_db) == before_db
    assert _sha256(production_photo) == before_photo


def test_report_commit_reconciles_after_power_loss_before_state_terminal(
    tmp_path: Path,
) -> None:
    tripped = False

    def fault(point: str) -> None:
        nonlocal tripped
        if point == "finalize.after_report_commit" and not tripped:
            tripped = True
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
            executor.run_action(
                "CLEAN",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
            executor.confirm_clean_door_closed(operator_confirmed=True, quiet_ms=0)
            executor.finalize()

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        assert resumed.snapshot()["status"] == "PASSED"
        assert resumed.snapshot()["phase"] == "COMPLETE"
        assert resumed.read_report()["status"] == "PASSED"


def test_duplicate_begin_returns_same_running_state(tmp_path: Path) -> None:
    executor, _model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        first_revision = executor.snapshot()["revision"]
        _begin(executor)
        assert executor.snapshot()["revision"] == first_revision
