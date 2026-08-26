from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import camera_capture
import factory.acceptance_hardware as acceptance_hardware
from factory.acceptance_core import (
    STATE_SCHEMA_VERSION,
    AcceptanceError,
    FactoryAcceptanceExecutor,
)
from factory.acceptance_hardware import (
    AcceptanceHardwareError,
    CLEAN_WIRE,
    DELIVERY_WIRE,
    FixedFrameAcceptanceMcu,
    FixedRoleCameraProbe,
    OpenCvCapture,
    ReadOnlyStm32RomProbe,
    VirtualRomProbe,
)
from factory.acceptance_storage import AcceptanceLockBusy, AtomicJsonFile
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


def test_factory_camera_roles_are_captured_in_parallel(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    capture_threads: set[int] = set()

    def parallel_capture(source: str, destination: Path) -> None:
        capture_threads.add(threading.get_ident())
        barrier.wait(timeout=1)
        destination.write_bytes(b"parallel-camera-probe:" + source.encode("ascii"))

    cameras = FixedRoleCameraProbe(
        outside_source="simulated://outside",
        inside_source="simulated://inside",
        temporary_directory=tmp_path / "camera-temp",
        capture=parallel_capture,
        allow_simulated=True,
    )

    pending = cameras.capture_pending()

    assert len(capture_threads) == 2
    assert pending["outside"]["captureNonEmpty"] is True
    assert pending["inside"]["captureNonEmpty"] is True
    cameras.discard_all_pending()


def test_factory_camera_failure_still_attempts_peer_and_cleans_pair(
    tmp_path: Path,
) -> None:
    barrier = threading.Barrier(2)
    attempted: set[str] = set()

    def one_failed_capture(source: str, destination: Path) -> None:
        attempted.add(source)
        barrier.wait(timeout=1)
        if source == "simulated://outside":
            raise RuntimeError("outside camera failed")
        destination.write_bytes(b"inside-camera-succeeded")

    cameras = FixedRoleCameraProbe(
        outside_source="simulated://outside",
        inside_source="simulated://inside",
        temporary_directory=tmp_path / "camera-temp",
        capture=one_failed_capture,
        allow_simulated=True,
    )

    with pytest.raises(RuntimeError, match="outside camera failed"):
        cameras.capture_pending()

    assert attempted == {"simulated://outside", "simulated://inside"}
    assert list((tmp_path / "camera-temp").glob("*.jpg")) == []


def test_camera_analysis_failure_is_persisted_as_factory_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def failed_capture(_source, _destination) -> None:
        raise camera_capture.CameraCaptureError("CAMERA_CAPTURE_FAILED")

    monkeypatch.setattr(
        acceptance_hardware,
        "capture_v4l2_jpeg",
        failed_capture,
        raising=False,
    )
    executor, _model, _factory = _build_executor(
        tmp_path,
        capture=OpenCvCapture(),
    )
    executor.cameras.outside_source = "/dev/v4l/by-id/outside-camera"
    executor.cameras.inside_source = "/dev/v4l/by-id/inside-camera"

    with executor:
        _begin(executor)
        executor.check_mcu()
        with pytest.raises(AcceptanceError, match="CAMERA_CAPTURE_FAILED"):
            executor.capture_cameras()

        state = executor.snapshot()
        assert state["phase"] == "CAMERA_CHECK_FAILED"
        assert state["checks"]["cameras"] == {
            "status": "FAILED",
            "resultCode": "CAMERA_CAPTURE_FAILED",
        }


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
        mcu_update_line_installed=True,
    )


def _set_weight(model: VirtualFixedFrameMcu, value: int) -> None:
    object.__setattr__(model.config, "self_test_weight_grams", value)


def _pass_prerequisites(
    executor: FactoryAcceptanceExecutor,
    model: VirtualFixedFrameMcu,
) -> None:
    _begin(executor)
    _pass_prerequisites_after_begin(executor, model)


def _pass_prerequisites_after_begin(
    executor: FactoryAcceptanceExecutor,
    model: VirtualFixedFrameMcu,
) -> None:
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


def _pass_sensor_and_camera_checks(
    executor: FactoryAcceptanceExecutor,
    model: VirtualFixedFrameMcu,
) -> None:
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


def _pass_delivery(executor: FactoryAcceptanceExecutor) -> None:
    executor.run_action(
        "DELIVERY",
        operator_area_safe_confirmed=True,
        timeout_ms=100,
        quiet_ms=0,
    )
    executor.confirm_delivery_area_safe(
        operator_confirmed=True,
        quiet_ms=0,
    )


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


def test_boolean_acceptance_schema_versions_are_rejected() -> None:
    with pytest.raises(
        AcceptanceError,
        match="ACCEPTANCE_REPORT_SCHEMA_INVALID",
    ):
        FactoryAcceptanceExecutor._validate_report({"schemaVersion": True})


def test_missing_update_line_skips_f2_and_still_allows_full_acceptance(
    tmp_path: Path,
) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    reset_calls: list[str] = []
    original_force_application = executor.bootloader.force_application_selection
    original_boot_application = executor.bootloader.boot_application

    def track_force_application() -> None:
        reset_calls.append("force_application_selection")
        original_force_application()

    def track_boot_application() -> None:
        reset_calls.append("boot_application")
        original_boot_application()

    executor.bootloader.force_application_selection = track_force_application
    executor.bootloader.boot_application = track_boot_application
    with executor:
        executor.begin_run(
            image_release_id="ecobin-zero3-1.0.0",
            boot_id="11111111-2222-3333-4444-555555555555",
            wall_time_trusted=False,
            hardware_config_digest="a" * 64,
            mcu_update_line_installed=False,
        )
        _pass_sensor_and_camera_checks(executor, model)
        state = executor.snapshot()
        assert state["checks"]["upgradeLine"] == {
            "status": "NOT_APPLICABLE",
            "resultCode": "MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED",
            "prepareSendAttempts": 0,
            "romWritePerformed": False,
            "romDeviceId": None,
        }
        with pytest.raises(
            AcceptanceError,
            match="MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED",
        ):
            executor.check_upgrade_line()
        assert model.firmware_prepare_count == 0
        assert executor.bootloader.read_only_probe_count == 0

        _pass_delivery(executor)
        executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        executor.confirm_clean_door_closed(
            operator_confirmed=True,
            quiet_ms=0,
        )
        report = executor.finalize()

    assert report["status"] == "PASSED"
    assert report["schemaVersion"] == 2
    assert report["mcuRemoteUpdateCapable"] is False
    assert report["mcuPeripheralEvidenceMode"] == "PHYSICAL"
    assert report["checks"]["upgradeLine"]["prepareSendAttempts"] == 0
    assert model.firmware_prepare_count == 0
    assert reset_calls == []


def test_exact_simulation_identity_requires_explicit_final_confirmation(
    tmp_path: Path,
) -> None:
    executor, model, _factory = _build_executor(
        tmp_path,
        config=SimulatorConfig(
            firmware_version="factory-sim-1.0.0",
            firmware_version_code=1,
            firmware_identity_hex="45434f53494d3031",
        ),
    )
    with executor:
        executor.begin_run(
            image_release_id="ecobin-zero3-1.0.0",
            boot_id="11111111-2222-3333-4444-555555555555",
            wall_time_trusted=False,
            hardware_config_digest="a" * 64,
            mcu_update_line_installed=False,
        )
        _pass_sensor_and_camera_checks(executor, model)
        assert (
            executor.snapshot()["mcuPeripheralEvidenceMode"]
            == "SIMULATED_PERIPHERALS"
        )
        _pass_delivery(executor)
        executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        executor.confirm_clean_door_closed(
            operator_confirmed=True,
            quiet_ms=0,
        )
        with pytest.raises(
            AcceptanceError,
            match="SIMULATED_PERIPHERAL_EVIDENCE_CONFIRMATION_REQUIRED",
        ):
            executor.finalize()
        report = executor.finalize(
            confirm_simulated_peripheral_evidence=True
        )

    assert report["status"] == "PASSED"
    assert report["mcuPeripheralEvidenceMode"] == "SIMULATED_PERIPHERALS"
    assert report["mcuRemoteUpdateCapable"] is False


@pytest.mark.parametrize(
    ("version", "version_code", "identity_hex"),
    (
        ("factory-sim-1.0.0", 1, "0102030405060708"),
        ("1.0.0", 1, "45434f53494d3031"),
        ("factory-sim-1.0.1", 1, "45434f53494d3031"),
        ("factory-sim-1.0.0", 2, "45434f53494d3031"),
    ),
)
def test_partial_simulation_identity_fails_closed(
    tmp_path: Path,
    version: str,
    version_code: int,
    identity_hex: str,
) -> None:
    executor, _model, _factory = _build_executor(
        tmp_path,
        config=SimulatorConfig(
            firmware_version=version,
            firmware_version_code=version_code,
            firmware_identity_hex=identity_hex,
        ),
    )
    with executor:
        _begin(executor)
        with pytest.raises(
            AcceptanceError,
            match="MCU_SIMULATION_IDENTITY_INVALID",
        ):
            executor.check_mcu()
        assert executor.snapshot()["checks"]["mcu"] == {
            "status": "FAILED",
            "resultCode": "MCU_SIMULATION_IDENTITY_INVALID",
        }


@pytest.mark.parametrize(
    ("check_name", "check_status", "result_code"),
    (
        ("delivery", "PASSED", "DELIVERY_SAFE_VERIFIED"),
        (
            "delivery",
            "FAILED_SAFE",
            "DELIVERY_FAILED_BUT_APPLICATION_RECOVERED",
        ),
        ("clean", "PASSED", "CLEAN_SAFE_VERIFIED"),
        (
            "clean",
            "FAILED_SAFE",
            "CLEAN_FAILED_BUT_APPLICATION_RECOVERED",
        ),
    ),
)
def test_schema_1_action_result_without_post_action_confirmation_requires_new_run(
    tmp_path: Path,
    check_name: str,
    check_status: str,
    result_code: str,
) -> None:
    state_path = _paths(tmp_path)["state_path"]
    AtomicJsonFile(state_path).write(
        {
            "schemaVersion": 1,
            "revision": 7,
            "status": "RUNNING",
            "phase": f"{check_name.upper()}_SAFE_VERIFIED",
            "imageReleaseId": "ecobin-zero3-1.0.0",
            "bootId": "11111111-2222-3333-4444-555555555555",
            "hardwareConfigDigest": "a" * 64,
            "timing": {
                "startedMonotonicMs": 1_000,
                "wallTimeTrusted": False,
                "trustedStartedAtUtc": None,
            },
            "mcuIdentity": {
                "fixedFrameRevision": 2,
                "firmwareVersion": "1.0.0",
                "firmwareVersionCode": 10_000,
                "firmwareIdentityHex": "0102030405060708",
            },
            "checks": {
                check_name: {
                    "status": check_status,
                    "resultCode": result_code,
                    "sendAttempts": 1,
                }
            },
            "recovery": None,
            "activeAction": None,
        }
    )
    executor, _model, _factory = _build_executor(tmp_path)

    with executor:
        migrated = executor.snapshot()

    assert STATE_SCHEMA_VERSION == 3
    assert migrated["schemaVersion"] == STATE_SCHEMA_VERSION
    assert migrated["revision"] == 9
    assert migrated["status"] == "FAILED"
    assert migrated["phase"] == "LEGACY_ACTION_SAFETY_CONFIRMATION_REQUIRED"
    assert migrated["checks"][check_name]["status"] == "FAILED"
    assert migrated["checks"][check_name]["resultCode"] == (
        "LEGACY_ACTION_REQUIRES_NEW_ACCEPTANCE_RUN"
    )


def test_schema_1_unsafe_delivery_waits_for_clean_recovery_then_fails_run(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        _pass_delivery(executor)
        executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )

    state_file = AtomicJsonFile(_paths(tmp_path)["state_path"])
    legacy = state_file.read()
    assert legacy is not None
    legacy["schemaVersion"] = 1
    del legacy["checks"]["delivery"]["operatorAreaSafeConfirmed"]
    state_file.write(legacy)

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        migrated = resumed.snapshot()
        assert migrated["schemaVersion"] == STATE_SCHEMA_VERSION
        assert migrated["status"] == "RECOVERY_REQUIRED"
        assert migrated["phase"] == "CLEAN_AWAITING_DOOR_CONFIRMATION"
        assert migrated["legacySafetyFailuresPending"] == ["delivery"]

        terminal = resumed.confirm_clean_door_closed(
            operator_confirmed=True,
            quiet_ms=0,
        )

        assert terminal["status"] == "FAILED"
        assert terminal["phase"] == "COMPLETE"
        assert resumed.read_report()["status"] == "FAILED"
        assert model.delivery_start_count == 1
        assert model.clean_start_count == 1


def test_schema_2_active_run_never_guesses_update_line_choice(
    tmp_path: Path,
) -> None:
    state_path = _paths(tmp_path)["state_path"]
    AtomicJsonFile(state_path).write(
        {
            "schemaVersion": 2,
            "revision": 4,
            "status": "RUNNING",
            "phase": "WEIGHT_CHECK_PASSED",
            "imageReleaseId": "ecobin-zero3-1.0.0",
            "bootId": "11111111-2222-3333-4444-555555555555",
            "hardwareConfigDigest": "a" * 64,
            "checks": {},
            "recovery": None,
            "activeAction": None,
        }
    )
    executor, _model, _factory = _build_executor(tmp_path)

    with executor:
        migrated = executor.snapshot()
        assert migrated["schemaVersion"] == 3
        assert migrated["status"] == "FAILED"
        assert migrated["phase"] == "LEGACY_UPDATE_LINE_SELECTION_REQUIRED"
        assert migrated["mcuUpdateLineInstalled"] is None

        restarted = executor.begin_run(
            image_release_id="ecobin-zero3-1.0.0",
            boot_id="11111111-2222-3333-4444-555555555555",
            wall_time_trusted=False,
            hardware_config_digest="a" * 64,
            mcu_update_line_installed=False,
            restart_terminal=True,
        )

    assert restarted["status"] == "RUNNING"
    assert restarted["mcuUpdateLineInstalled"] is False
    assert restarted["checks"]["upgradeLine"]["status"] == "NOT_APPLICABLE"


def test_schema_2_running_run_preserves_proven_update_line(tmp_path: Path) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)

    state_file = AtomicJsonFile(_paths(tmp_path)["state_path"])
    previous = state_file.read()
    assert previous is not None
    previous["schemaVersion"] = 2
    previous.pop("mcuUpdateLineInstalled")
    state_file.write(previous)

    resumed, _model, _factory = _build_executor(tmp_path)
    with resumed:
        migrated = resumed.snapshot()

    assert migrated["schemaVersion"] == 3
    assert migrated["status"] == "RUNNING"
    assert migrated["phase"] == previous["phase"]
    assert migrated["mcuUpdateLineInstalled"] is True
    assert "legacyUpdateLineSelectionRequired" not in migrated


def test_schema_2_recovery_preserves_proven_update_line(tmp_path: Path) -> None:
    def interrupt_after_journal(point: str) -> None:
        if point == "delivery.after_command_journal":
            raise PowerLoss()

    executor, model, factory = _build_executor(
        tmp_path,
        fault_hook=interrupt_after_journal,
    )
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )

    state_file = AtomicJsonFile(_paths(tmp_path)["state_path"])
    previous = state_file.read()
    assert previous is not None
    previous["schemaVersion"] = 2
    previous.pop("mcuUpdateLineInstalled")
    state_file.write(previous)

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        migrated = resumed.snapshot()

    assert migrated["schemaVersion"] == 3
    assert migrated["status"] == "RECOVERY_REQUIRED"
    assert migrated["phase"] == "DELIVERY_COMMAND_MAY_HAVE_BEEN_SENT"
    assert migrated["mcuUpdateLineInstalled"] is True
    assert migrated["checks"]["upgradeLine"]["status"] == "PASSED"
    assert "legacyUpdateLineSelectionRequired" not in migrated


def test_schema_1_unsafe_delivery_and_armed_clean_reconcile_to_failed_run(
    tmp_path: Path,
) -> None:
    def fault(point: str) -> None:
        if point == "clean.after_armed":
            raise PowerLoss()

    executor, model, factory = _build_executor(tmp_path, fault_hook=fault)
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            _pass_delivery(executor)
            executor.run_action(
                "CLEAN",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
    assert model.clean_start_count == 0

    state_file = AtomicJsonFile(_paths(tmp_path)["state_path"])
    legacy = state_file.read()
    assert legacy is not None
    legacy["schemaVersion"] = 1
    del legacy["checks"]["delivery"]["operatorAreaSafeConfirmed"]
    state_file.write(legacy)

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        assert resumed.snapshot()["status"] == "FAILED"
        assert resumed.snapshot()["phase"] == "COMPLETE"
        assert resumed.read_report()["status"] == "FAILED"
    assert model.clean_start_count == 0


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
        with pytest.raises(
            AcceptanceError,
            match="UPGRADE_LINE_ALREADY_ATTEMPTED_IN_CURRENT_RUN",
        ):
            executor.check_upgrade_line()
        assert model.firmware_prepare_count == 1


def test_replaced_mcu_is_rejected_before_f2_can_be_sent(tmp_path: Path) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        executor.check_mcu()
        model.firmware_identity_hex = "1112131415161718"

        with pytest.raises(
            AcceptanceError,
            match="MCU_IDENTITY_CHANGED_SINCE_INITIAL_CHECK",
        ):
            executor.check_upgrade_line()

        assert model.firmware_prepare_count == 0
        state = executor.snapshot()
        assert state["status"] == "RUNNING"
        assert "upgradeLine" not in state["checks"]


@pytest.mark.parametrize("action", ("DELIVERY", "CLEAN"))
def test_replaced_mcu_is_rejected_before_physical_action_can_be_sent(
    tmp_path: Path,
    action: str,
) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        if action == "CLEAN":
            _pass_delivery(executor)
        model.firmware_identity_hex = "2122232425262728"

        with pytest.raises(
            AcceptanceError,
            match="MCU_IDENTITY_CHANGED_SINCE_INITIAL_CHECK",
        ):
            executor.run_action(
                action,
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )

        assert model.delivery_start_count == (1 if action == "CLEAN" else 0)
        assert model.clean_start_count == 0
        assert action.lower() not in executor.snapshot()["checks"]


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
        assert recovered.snapshot()["status"] == "FAILED"
        assert recovered.read_report()["status"] == "FAILED"
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

        assert recovered["status"] == "FAILED"
        assert recovered["checks"]["upgradeLine"]["status"] == "FAILED_SAFE"
        assert recovered["checks"]["upgradeLine"]["resultCode"] == (
            "UPGRADE_LINE_FAILED_BUT_APPLICATION_RECOVERED"
        )
        assert resumed.read_report()["status"] == "FAILED"
        with pytest.raises(
            AcceptanceError,
            match="ACCEPTANCE_RUN_NOT_RUNNING",
        ):
            resumed.check_upgrade_line()
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
        executor.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )
        assert model.delivery_start_count == 1
        writes = [wire for session in factory.sessions for wire in session.writes]
        assert writes.count(DELIVERY_WIRE) == 1
        check = executor.snapshot()["checks"]["delivery"]
        assert check["status"] == "PASSED"
        assert check["sendAttempts"] == 1
        with pytest.raises(
            AcceptanceError,
            match="DELIVERY_ALREADY_ATTEMPTED_IN_CURRENT_RUN",
        ):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
        assert model.delivery_start_count == 1


def test_delivery_dd_requires_fresh_post_action_area_confirmation(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)

        executor.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )

        pending = executor.snapshot()
        assert model.delivery_start_count == 1
        assert pending["status"] == "RECOVERY_REQUIRED"
        assert pending["phase"] == "DELIVERY_AWAITING_AREA_CONFIRMATION"
        assert pending["recovery"]["awaitingAreaSafetyConfirmation"] is True
        assert pending["checks"]["delivery"]["status"] == "RECOVERY_REQUIRED"
        with pytest.raises(
            AcceptanceError,
            match="DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED",
        ):
            executor.confirm_delivery_area_safe(
                operator_confirmed=False,
                quiet_ms=0,
            )
        assert executor.snapshot()["status"] == "RECOVERY_REQUIRED"

        confirmed = executor.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )

        assert confirmed["status"] == "RUNNING"
        assert confirmed["phase"] == "DELIVERY_SAFE_VERIFIED"
        assert confirmed["recovery"] is None
        assert confirmed["activeAction"] is None
        assert confirmed["checks"]["delivery"]["status"] == "PASSED"
        assert (
            confirmed["checks"]["delivery"]["operatorAreaSafeConfirmed"]
            is True
        )
        writes = [wire for session in factory.sessions for wire in session.writes]
        assert writes.count(DELIVERY_WIRE) == 1

        revision = confirmed["revision"]
        executor.bootloader.boot_application = lambda: (_ for _ in ()).throw(
            AssertionError("duplicate confirmation performed hardware I/O")
        )
        duplicate = executor.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )
        assert duplicate["revision"] == revision


def test_delivery_confirmation_failure_keeps_the_persisted_lock(
    tmp_path: Path,
) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        executor.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        original_query_identity = executor._query_identity

        def fail_identity() -> dict:
            raise AcceptanceHardwareError("MCU_F3_QUERY_TIMEOUT")

        executor._query_identity = fail_identity
        with pytest.raises(
            AcceptanceError,
            match="MCU_F3_QUERY_TIMEOUT",
        ):
            executor.confirm_delivery_area_safe(
                operator_confirmed=True,
                quiet_ms=0,
            )

        locked = executor.snapshot()
        assert locked["status"] == "RECOVERY_REQUIRED"
        assert locked["activeAction"]["type"] == "DELIVERY"
        assert locked["recovery"]["awaitingAreaSafetyConfirmation"] is True
        assert (
            locked["checks"]["delivery"]["operatorAreaSafeConfirmed"]
            is False
        )
        assert model.delivery_start_count == 1

        executor._query_identity = original_query_identity
        confirmed = executor.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )
        assert confirmed["checks"]["delivery"]["status"] == "PASSED"
        assert model.delivery_start_count == 1


def test_delivery_pending_confirmation_survives_executor_restart(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        executor.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        pending = resumed.snapshot()
        assert pending["phase"] == "DELIVERY_AWAITING_AREA_CONFIRMATION"
        assert pending["status"] == "RECOVERY_REQUIRED"
        confirmed = resumed.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )
        assert confirmed["checks"]["delivery"]["status"] == "PASSED"
        assert model.delivery_start_count == 1


def test_clean_ee_is_one_write_and_requires_manual_door_confirmation(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        _pass_delivery(executor)
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
        _pass_delivery(executor)
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
            _pass_delivery(executor)
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


def test_delivery_recovery_keeps_lock_until_fresh_area_confirmation(
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
                quiet_ms=0,
            )

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with resumed:
        recovered = resumed.recover(quiet_ms=0)

        assert recovered["status"] == "RECOVERY_REQUIRED"
        assert recovered["phase"] == "DELIVERY_AWAITING_AREA_CONFIRMATION"
        assert recovered["recovery"]["hardwareVerified"] is True
        assert (
            recovered["recovery"]["awaitingAreaSafetyConfirmation"] is True
        )
        assert recovered["checks"]["delivery"]["status"] == (
            "RECOVERY_REQUIRED"
        )

        confirmed = resumed.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )

        assert confirmed["status"] == "FAILED"
        assert confirmed["recovery"] is None
        assert confirmed["checks"]["delivery"]["status"] == "FAILED_SAFE"
        assert confirmed["checks"]["delivery"]["resultCode"] == (
            "DELIVERY_FAILED_BUT_APPLICATION_RECOVERED"
        )
        assert (
            confirmed["checks"]["delivery"]["operatorAreaSafeConfirmed"]
            is True
        )
        failed_report = resumed.read_report()
        assert failed_report is not None
        assert failed_report["status"] == "FAILED"
        assert (
            failed_report["checks"]["delivery"][
                "operatorAreaSafeConfirmed"
            ]
            is True
        )
        with pytest.raises(AcceptanceError, match="ACCEPTANCE_RUN_NOT_RUNNING"):
            resumed.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
        assert model.delivery_start_count == 1


def test_power_loss_before_recovered_delivery_report_only_allows_finalize(
    tmp_path: Path,
) -> None:
    command_interrupted = False

    def interrupt_command(point: str) -> None:
        nonlocal command_interrupted
        if point == "delivery.after_serial_write" and not command_interrupted:
            command_interrupted = True
            raise PowerLoss()

    executor, model, factory = _build_executor(
        tmp_path,
        fault_hook=interrupt_command,
    )
    with pytest.raises(PowerLoss):
        with executor:
            _pass_prerequisites(executor, model)
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )

    finalize_interrupted = False

    def interrupt_finalize(point: str) -> None:
        nonlocal finalize_interrupted
        if (
            point == "delivery.after_recovered_safe_before_finalize"
            and not finalize_interrupted
        ):
            finalize_interrupted = True
            raise PowerLoss()

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
        fault_hook=interrupt_finalize,
    )
    with pytest.raises(PowerLoss):
        with resumed:
            resumed.recover(quiet_ms=0)
            resumed.confirm_delivery_area_safe(
                operator_confirmed=True,
                quiet_ms=0,
            )

    finalizer, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with finalizer:
        intermediate = finalizer.snapshot()
        assert intermediate["status"] == "RUNNING"
        assert intermediate["phase"] == "DELIVERY_RECOVERED_SAFE"
        assert intermediate["checks"]["delivery"]["status"] == "FAILED_SAFE"
        assert intermediate["recovery"] is None
        assert finalizer.read_report() is None
        with pytest.raises(
            AcceptanceError,
            match="DELIVERY_ALREADY_ATTEMPTED_IN_CURRENT_RUN",
        ):
            finalizer.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )

        report = finalizer.finalize()

        assert report["status"] == "FAILED"
        assert finalizer.snapshot()["status"] == "FAILED"
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


def test_unknown_delivery_result_without_update_line_stays_locked_without_reset(
    tmp_path: Path,
) -> None:
    executor, model, factory = _build_executor(tmp_path)
    reset_calls: list[str] = []
    original_force_application = executor.bootloader.force_application_selection
    original_boot_application = executor.bootloader.boot_application

    def track_force_application() -> None:
        reset_calls.append("force_application_selection")
        original_force_application()

    def track_boot_application() -> None:
        reset_calls.append("boot_application")
        original_boot_application()

    executor.bootloader.force_application_selection = track_force_application
    executor.bootloader.boot_application = track_boot_application
    with executor:
        executor.begin_run(
            image_release_id="ecobin-zero3-1.0.0",
            boot_id="11111111-2222-3333-4444-555555555555",
            wall_time_trusted=False,
            hardware_config_digest="a" * 64,
            mcu_update_line_installed=False,
        )
        _pass_sensor_and_camera_checks(executor, model)
        original = executor.mcu.await_final_result

        def discard_then_wait(action: str, timeout_ms: int):
            serial = factory.sessions[-1]
            with serial._condition:
                serial._received.clear()
                serial._condition.notify_all()
            return original(action, timeout_ms)

        executor.mcu.await_final_result = discard_then_wait
        with pytest.raises(AcceptanceError, match="FINAL_RESULT_TIMEOUT"):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=20,
                quiet_ms=0,
            )

        with pytest.raises(
            AcceptanceError,
            match="MCU_RESET_LINE_REQUIRED_FOR_RECOVERY",
        ) as recovery_error:
            executor.recover(quiet_ms=0)

        state = executor.snapshot()
        assert recovery_error.value.recovery_required is True
        assert state["status"] == "RECOVERY_REQUIRED"
        assert state["recovery"]["resultCode"] == (
            "MCU_RESET_LINE_REQUIRED_FOR_RECOVERY"
        )
        assert state["checks"]["delivery"]["sendAttempts"] == 1
        assert model.delivery_start_count == 1
        assert reset_calls == []


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


def _assert_finalize_rejects_pending_physical_recovery(
    executor: FactoryAcceptanceExecutor,
) -> None:
    before = executor.snapshot()
    with pytest.raises(
        AcceptanceError,
        match="ACCEPTANCE_RECOVERY_REQUIRED",
    ) as blocked:
        executor.finalize()
    assert blocked.value.recovery_required is True
    assert executor.snapshot() == before


def test_core_finalize_rejects_pending_delivery_confirmation(tmp_path: Path) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        executor.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        _assert_finalize_rejects_pending_physical_recovery(executor)


def test_core_finalize_rejects_pending_clean_confirmation(tmp_path: Path) -> None:
    executor, model, _factory = _build_executor(tmp_path)
    with executor:
        _pass_prerequisites(executor, model)
        _pass_delivery(executor)
        executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        _assert_finalize_rejects_pending_physical_recovery(executor)


def test_core_finalize_rejects_pending_f2_recovery(tmp_path: Path) -> None:
    tripped = False

    def fault(point: str) -> None:
        nonlocal tripped
        if point == "upgrade_line.after_prepare_journal" and not tripped:
            tripped = True
            raise PowerLoss()

    executor, _model, _factory = _build_executor(tmp_path, fault_hook=fault)
    with executor:
        _begin(executor)
        executor.check_mcu()
        with pytest.raises(PowerLoss):
            executor.check_upgrade_line()
        _assert_finalize_rejects_pending_physical_recovery(executor)


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
        executor.confirm_delivery_area_safe(
            operator_confirmed=True,
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
        assert (
            report["checks"]["delivery"]["operatorAreaSafeConfirmed"]
            is True
        )
        assert report["checks"]["clean"]["weightDeltaGrams"] == 10_400
        assert report["checks"]["clean"]["cleanDoorConfirmed"] is True
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
            executor.confirm_delivery_area_safe(
                operator_confirmed=True,
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


def test_current_pending_report_is_still_validated_before_reconciliation(
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
            _pass_delivery(executor)
            executor.run_action(
                "CLEAN",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
            executor.confirm_clean_door_closed(
                operator_confirmed=True,
                quiet_ms=0,
            )
            executor.finalize()

    report_file = AtomicJsonFile(_paths(tmp_path)["report_path"])
    report = report_file.read()
    assert report is not None
    del report["checks"]["delivery"]["operatorAreaSafeConfirmed"]
    report_file.write(report)

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
    )
    with pytest.raises(
        AcceptanceError,
        match="ACCEPTANCE_REPORT_DELIVERY_SAFETY_CONFIRMATION_INVALID",
    ):
        with resumed:
            pass

    pending_state = AtomicJsonFile(_paths(tmp_path)["state_path"]).read()
    assert pending_state is not None
    assert pending_state["phase"] == "FINALIZING_REPORT"
    assert pending_state["status"] == "RUNNING"


def test_old_report_does_not_block_retrying_new_run_finalization_after_power_loss(
    tmp_path: Path,
) -> None:
    now = [1.0]
    interrupt_finalize = [False]

    def fault(point: str) -> None:
        if interrupt_finalize[0] and point == "finalize.after_state_journal":
            interrupt_finalize[0] = False
            raise PowerLoss()

    executor, model, factory = _build_executor(
        tmp_path,
        fault_hook=fault,
        executor_options={"monotonic": lambda: now[0]},
    )
    old_report: dict = {}
    new_boot_id = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    with pytest.raises(PowerLoss):
        with executor:
            _begin(executor)
            old_report = executor.finalize()
            assert old_report["status"] == "FAILED"

            now[0] = 2.0
            executor.begin_run(
                image_release_id="ecobin-zero3-1.0.0",
                boot_id=new_boot_id,
                wall_time_trusted=False,
                hardware_config_digest="a" * 64,
                mcu_update_line_installed=True,
                restart_terminal=True,
            )
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
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
            executor.confirm_delivery_area_safe(
                operator_confirmed=True,
                quiet_ms=0,
            )
            executor.run_action(
                "CLEAN",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
            executor.confirm_clean_door_closed(
                operator_confirmed=True,
                quiet_ms=0,
            )
            assert executor.read_report() == old_report

            now[0] = 3.0
            interrupt_finalize[0] = True
            executor.finalize()

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
        executor_options={"monotonic": lambda: now[0]},
    )
    with resumed:
        pending = resumed.snapshot()
        assert pending["status"] == "RUNNING"
        assert pending["phase"] == "FINALIZING_REPORT"
        assert pending["pendingFinalStatus"] == "PASSED"
        assert resumed.read_report() == old_report
        pending_revision = pending["revision"]
        with pytest.raises(
            AcceptanceError,
            match="ACCEPTANCE_REPORT_FINALIZATION_PENDING",
        ):
            resumed.check_mcu()
        assert resumed.snapshot()["revision"] == pending_revision
        assert resumed.snapshot()["phase"] == "FINALIZING_REPORT"

        now[0] = 4.0
        new_report = resumed.finalize()

        assert new_report["status"] == "PASSED"
        assert new_report["bootId"] == new_boot_id
        assert new_report["timing"]["finishedMonotonicMs"] == 4_000
        assert new_report != old_report
        assert resumed.read_report() == new_report
        assert resumed.snapshot()["status"] == "PASSED"
        assert resumed.snapshot()["phase"] == "COMPLETE"


def test_legacy_passed_report_is_ignored_while_retrying_new_finalization(
    tmp_path: Path,
) -> None:
    now = [1.0]
    interrupt_finalize = [False]

    def fault(point: str) -> None:
        if interrupt_finalize[0] and point == "finalize.after_state_journal":
            interrupt_finalize[0] = False
            raise PowerLoss()

    executor, model, factory = _build_executor(
        tmp_path,
        fault_hook=fault,
        executor_options={"monotonic": lambda: now[0]},
    )
    with executor:
        _pass_prerequisites(executor, model)
        _pass_delivery(executor)
        executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=100,
            quiet_ms=0,
        )
        executor.confirm_clean_door_closed(
            operator_confirmed=True,
            quiet_ms=0,
        )
        executor.finalize()

    state_file = AtomicJsonFile(_paths(tmp_path)["state_path"])
    report_file = AtomicJsonFile(_paths(tmp_path)["report_path"])
    legacy_state = state_file.read()
    legacy_report = report_file.read()
    assert legacy_state is not None
    assert legacy_report is not None
    legacy_state["schemaVersion"] = 1
    del legacy_state["checks"]["delivery"]["operatorAreaSafeConfirmed"]
    del legacy_state["checks"]["clean"]["cleanDoorConfirmed"]
    del legacy_report["checks"]["delivery"]["operatorAreaSafeConfirmed"]
    del legacy_report["checks"]["clean"]["cleanDoorConfirmed"]
    state_file.write(legacy_state)
    report_file.write(legacy_report)

    # A service restart during the same OS boot retains bootId.  The stale
    # report must still be distinguished from the new run by its timing bind.
    current_boot_id = "11111111-2222-3333-4444-555555555555"
    interrupted, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
        fault_hook=fault,
        executor_options={"monotonic": lambda: now[0]},
    )
    with pytest.raises(PowerLoss):
        with interrupted:
            assert interrupted.snapshot()["status"] == "FAILED"
            now[0] = 2.0
            interrupted.begin_run(
                image_release_id="ecobin-zero3-1.0.0",
                boot_id=current_boot_id,
                wall_time_trusted=False,
                hardware_config_digest="a" * 64,
                mcu_update_line_installed=True,
                restart_terminal=True,
            )
            _pass_prerequisites_after_begin(interrupted, model)
            _pass_delivery(interrupted)
            interrupted.run_action(
                "CLEAN",
                operator_area_safe_confirmed=True,
                timeout_ms=100,
                quiet_ms=0,
            )
            interrupted.confirm_clean_door_closed(
                operator_confirmed=True,
                quiet_ms=0,
            )
            now[0] = 3.0
            interrupt_finalize[0] = True
            interrupted.finalize()

    resumed, _model, _factory = _build_executor(
        tmp_path,
        model=model,
        serial_factory=factory,
        executor_options={"monotonic": lambda: now[0]},
    )
    with resumed:
        pending = resumed.snapshot()
        assert pending["status"] == "RUNNING"
        assert pending["phase"] == "FINALIZING_REPORT"
        assert pending["pendingFinalStatus"] == "PASSED"
        assert report_file.read() == legacy_report

        now[0] = 4.0
        new_report = resumed.finalize()

        assert new_report["status"] == "PASSED"
        assert new_report["bootId"] == legacy_report["bootId"] == current_boot_id
        assert new_report["timing"]["startedMonotonicMs"] == 2_000
        assert legacy_report["timing"]["startedMonotonicMs"] == 1_000
        assert resumed.read_report() == new_report
        assert resumed.snapshot()["status"] == "PASSED"
        assert resumed.snapshot()["phase"] == "COMPLETE"


def test_duplicate_begin_returns_same_running_state(tmp_path: Path) -> None:
    executor, _model, _factory = _build_executor(tmp_path)
    with executor:
        _begin(executor)
        first_revision = executor.snapshot()["revision"]
        _begin(executor)
        assert executor.snapshot()["revision"] == first_revision
