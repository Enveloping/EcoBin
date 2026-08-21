from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from edge_store import EdgeStore
from fixed_frame_mcu_adapter import FixedFrameMcuAdapter
from mcu_firmware_package import (
    VerifiedFirmwarePackage,
    create_package,
    generate_identity,
)
from mcu_firmware_updater import FirmwarePackageCache, McuFirmwareUpdater
from tools.fixed_frame_pty_simulator import SimulatorConfig, VirtualFixedFrameMcu
from tools.mcu_firmware_upgrade_simulator import McuFirmwareUpgradeSimulation


HARDWARE_COMPATIBILITY = "ECOBIN_MAINBOARD_V1.1"
SIGNING_KEY_ID = "SIMULATOR_TEST_KEY"


def _signing_key(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "simulator-private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return private_key, private_path


def _firmware_package(
    tmp_path: Path,
    private_path: Path,
    *,
    version: str,
    version_code: int,
    image_marker: int,
) -> tuple[Path, VerifiedFirmwarePackage]:
    directory = tmp_path / f"firmware-{version_code}"
    directory.mkdir()
    identity_path = directory / "identity.json"
    generate_identity(
        version=version,
        version_code=version_code,
        header_path=directory / "firmware_identity.h",
        metadata_path=identity_path,
    )
    image_path = directory / "firmware.bin"
    image_path.write_bytes(
        b"\x00\x20\x00\x08" + bytes((image_marker,)) * 4092
    )
    package_path = directory / "firmware.efw"
    verified = create_package(
        image_path=image_path,
        identity_metadata_path=identity_path,
        private_key_path=private_path,
        key_id=SIGNING_KEY_ID,
        hardware_compatibility=HARDWARE_COMPATIBILITY,
        build_commit=f"{image_marker:02x}" * 20,
        built_at="2026-08-21T00:00:00Z",
        output_path=package_path,
    )
    return package_path, verified


@dataclass
class _UpgradeHarness:
    store: EdgeStore
    adapter: FixedFrameMcuAdapter
    updater: McuFirmwareUpdater
    model: VirtualFixedFrameMcu
    simulation: McuFirmwareUpgradeSimulation
    target_path: Path
    target: VerifiedFirmwarePackage

    def close(self) -> None:
        self.adapter.close()
        self.store.close()


def _ready_upgrade_harness(
    tmp_path: Path,
    *,
    simulator_config: SimulatorConfig | None = None,
    simulation_options: dict | None = None,
) -> _UpgradeHarness:
    private_key, private_path = _signing_key(tmp_path)
    stable_path, _ = _firmware_package(
        tmp_path,
        private_path,
        version="1.0.0",
        version_code=10_000,
        image_marker=1,
    )
    target_path, target = _firmware_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20_000,
        image_marker=2,
    )
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    cache = FirmwarePackageCache(
        tmp_path / "cache",
        {SIGNING_KEY_ID: private_key.public_key()},
        HARDWARE_COMPATIBILITY,
    )
    model = VirtualFixedFrameMcu(
        simulator_config
        or SimulatorConfig(
            firmware_version="0.9.0",
            firmware_version_code=9_000,
            firmware_identity_hex="0909090909090909",
            response_delay_ms=0,
        )
    )
    effective_simulation_options = {
        # The harness first installs revision 2 through the explicitly local
        # legacy migration path. Every later application-to-ROM transition
        # must have a newly confirmed F2 preparation.
        "legacy_unprepared_bootloader_entries": 1,
        **(simulation_options or {}),
    }
    simulation = McuFirmwareUpgradeSimulation(
        model,
        **effective_simulation_options,
    )
    adapter = FixedFrameMcuAdapter(
        "simulated://mcu-firmware-upgrade",
        edge_boot_id=77,
        timeout_s=0.01,
        serial_factory=simulation.serial_factory,
        is_simulated=True,
    )
    updater = McuFirmwareUpdater(
        store=store,
        uart_link=adapter,
        package_cache=cache,
        boot_control=simulation,
        flash_runner=simulation,
    )
    assert adapter.open()
    first = updater.queue_local(
        stable_path,
        legacy_preflight=True,
    )
    assert updater.process_active()
    assert store.get_mcu_firmware_update(first["updateUid"])[
        "state"
    ] == "SUCCEEDED"
    return _UpgradeHarness(
        store=store,
        adapter=adapter,
        updater=updater,
        model=model,
        simulation=simulation,
        target_path=target_path,
        target=target,
    )


def test_real_updater_completes_an_upgrade_through_the_virtual_mcu(tmp_path):
    harness = _ready_upgrade_harness(tmp_path)
    try:
        second = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(second["updateUid"])
        assert update["state"] == "SUCCEEDED"
        assert harness.model.firmware_version == "2.0.0"
        assert harness.model.firmware_version_code == 20_000
        assert harness.model.firmware_identity_hex == harness.target.manifest[
            "firmwareIdentityHex"
        ]
        assert harness.model.firmware_prepare_count == 1
        assert harness.model.update_prepared is False
        assert harness.simulation.flash_version_codes == [10_000, 20_000]
    finally:
        harness.close()


def test_virtual_mcu_identity_error_rejects_before_prepare_or_flash(tmp_path):
    harness = _ready_upgrade_harness(tmp_path)
    harness.model.configure_firmware_identity_fault(status=3)
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "REJECTED"
        assert update["last_error_code"] == "FIRMWARE_IDENTITY_UNAVAILABLE"
        assert harness.model.firmware_prepare_count == 0
        assert harness.simulation.flash_version_codes == [10_000]
        assert harness.store.get_maintenance_lock() is None
    finally:
        harness.close()


def test_virtual_mcu_prepare_error_is_reset_and_rejected_safely(tmp_path):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulator_config=SimulatorConfig(
            firmware_prepare_status=3,
            firmware_prepare_safe_flags=0x0F,
            response_delay_ms=0,
        ),
    )
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "REJECTED"
        assert update["last_error_code"] == "MCU_PREPARE_EXECUTION_FAILED"
        assert harness.model.firmware_prepare_count == 1
        assert harness.model.runtime_mode == "APPLICATION"
        assert harness.model.update_prepared is False
        assert harness.simulation.flash_version_codes == [10_000]
        assert harness.store.get_maintenance_lock() is None
    finally:
        harness.close()


def test_virtual_mcu_dropped_prepare_response_is_reset_and_rejected(tmp_path):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulator_config=SimulatorConfig(
            firmware_prepare_dropped_responses=1,
            response_delay_ms=0,
        ),
    )
    execute_prepare = harness.adapter.execute_firmware_update_prepare
    harness.adapter.execute_firmware_update_prepare = lambda: execute_prepare(
        timeout_ms=30
    )
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "REJECTED"
        assert (
            update["last_error_code"]
            == "MCU_PREPARE_EXECUTION_UNCONFIRMED"
        )
        assert harness.model.firmware_prepare_count == 1
        assert harness.model.runtime_mode == "APPLICATION"
        assert harness.model.update_prepared is False
        assert harness.simulation.flash_version_codes == [10_000]
        assert harness.store.get_maintenance_lock() is None
    finally:
        harness.close()


def test_virtual_mcu_prepare_recovery_failure_keeps_maintenance_lock(tmp_path):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulator_config=SimulatorConfig(
            firmware_prepare_status=3,
            firmware_prepare_safe_flags=0x0F,
            response_delay_ms=0,
        ),
    )
    harness.simulation.configure_application_boot_failures(1)
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "FAILED_LOCKED"
        assert update["last_error_code"] == "MCU_PREPARE_RECOVERY_FAILED"
        assert harness.model.runtime_mode == "SYSTEM_BOOTLOADER"
        assert harness.model.update_prepared is True
        assert harness.store.get_maintenance_lock()["owner_uid"] == queued[
            "updateUid"
        ]
    finally:
        harness.close()


def test_virtual_mcu_self_test_failure_rolls_back_to_stable_firmware(tmp_path):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulation_options={
            "self_test_failure_version_codes": {20_000},
        },
    )
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "ROLLED_BACK"
        assert harness.model.firmware_version == "1.0.0"
        assert harness.model.firmware_version_code == 10_000
        assert harness.simulation.flash_version_codes == [
            10_000,
            20_000,
            20_000,
            20_000,
            10_000,
        ]
        assert harness.model.firmware_prepare_count == 4
        assert harness.store.get_maintenance_lock() is None
    finally:
        harness.close()


def test_virtual_mcu_identity_mismatch_rolls_back_to_stable_firmware(tmp_path):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulation_options={
            "identity_mismatch_version_codes": {20_000},
        },
    )
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "ROLLED_BACK"
        assert harness.model.firmware_version_code == 10_000
        assert harness.model.firmware_identity_hex != harness.target.manifest[
            "firmwareIdentityHex"
        ]
        assert harness.model.firmware_prepare_count == 4
        assert harness.store.get_maintenance_lock() is None
    finally:
        harness.close()


def test_repeated_prepare_failure_recovers_application_and_stays_locked(
    tmp_path,
    monkeypatch,
):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulation_options={
            "identity_mismatch_version_codes": {20_000},
        },
    )
    original_prepare = harness.adapter.execute_firmware_update_prepare
    prepare_calls = 0

    def fail_second_prepare(*args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_calls == 1:
            return original_prepare(*args, **kwargs)
        return {
            "queryStatus": "OK",
            "statusCode": 3,
            "status": "INTERNAL_ERROR",
            "safeFlags": 0x0F,
            "executed": False,
        }

    monkeypatch.setattr(
        harness.adapter,
        "execute_firmware_update_prepare",
        fail_second_prepare,
    )
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "FAILED_LOCKED"
        assert update["last_error_code"] == "MCU_PREPARE_EXECUTION_FAILED"
        assert update["target_attempt_count"] == 1
        assert update["rollback_attempt_count"] == 0
        assert harness.simulation.flash_version_codes == [10_000, 20_000]
        assert harness.model.runtime_mode == "APPLICATION"
        assert harness.model.update_prepared is False
        assert harness.store.get_maintenance_lock()["owner_uid"] == queued[
            "updateUid"
        ]
    finally:
        harness.close()


def test_unknown_mode_after_application_boot_failure_stays_locked(tmp_path):
    harness = _ready_upgrade_harness(tmp_path)
    harness.simulation.configure_application_boot_failures(1)
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "FAILED_LOCKED"
        assert update["last_error_code"] == "MCU_RUNTIME_MODE_UNCERTAIN"
        assert update["target_attempt_count"] == 1
        assert update["rollback_attempt_count"] == 0
        assert harness.simulation.flash_version_codes == [10_000, 20_000]
        assert harness.model.runtime_mode == "SYSTEM_BOOTLOADER"
        assert harness.store.get_maintenance_lock()["owner_uid"] == queued[
            "updateUid"
        ]
    finally:
        harness.close()


def test_restart_after_repeated_prepare_reproves_before_next_flash(
    tmp_path,
    monkeypatch,
):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulation_options={
            "identity_mismatch_version_codes": {20_000},
        },
    )
    original_record_attempt = harness.store.record_mcu_firmware_attempt

    def stop_after_second_prepare(update_uid, *, rollback, device_name=None):
        update = harness.store.get_mcu_firmware_update(update_uid)
        if (
            not rollback
            and update["target_attempt_count"] == 1
            and harness.model.firmware_prepare_count == 2
        ):
            raise SystemExit("simulated process exit after repeated F2")
        return original_record_attempt(
            update_uid,
            rollback=rollback,
            device_name=device_name,
        )

    monkeypatch.setattr(
        harness.store,
        "record_mcu_firmware_attempt",
        stop_after_second_prepare,
    )
    try:
        queued = harness.updater.queue_local(harness.target_path)
        with pytest.raises(SystemExit, match="after repeated F2"):
            harness.updater.process_active()

        interrupted = harness.store.get_mcu_firmware_update(
            queued["updateUid"]
        )
        assert interrupted["state"] == "VERIFYING_TARGET"
        assert interrupted["prepare_recovery_required"] is True
        assert harness.model.update_prepared is True

        monkeypatch.setattr(
            harness.store,
            "record_mcu_firmware_attempt",
            original_record_attempt,
        )
        resumed = McuFirmwareUpdater(
            store=harness.store,
            uart_link=harness.adapter,
            package_cache=harness.updater.cache,
            boot_control=harness.simulation,
            flash_runner=harness.simulation,
        )
        assert resumed.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "ROLLED_BACK"
        assert update["target_attempt_count"] == 3
        assert update["rollback_attempt_count"] == 1
        assert harness.model.firmware_prepare_count == 5
        assert harness.model.firmware_version_code == 10_000
        assert harness.store.get_maintenance_lock() is None
    finally:
        harness.close()


def test_virtual_mcu_flash_failure_exhausts_target_then_rolls_back(tmp_path):
    harness = _ready_upgrade_harness(
        tmp_path,
        simulation_options={
            "flash_failure_version_codes": {20_000},
        },
    )
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "ROLLED_BACK"
        assert harness.simulation.flash_attempt_version_codes == [
            10_000,
            20_000,
            20_000,
            20_000,
            10_000,
        ]
        assert harness.simulation.flash_version_codes == [10_000, 10_000]
        assert harness.model.firmware_version_code == 10_000
        assert harness.model.firmware_prepare_count == 1
    finally:
        harness.close()


def test_virtual_mcu_target_and_rollback_flash_failures_stay_locked(tmp_path):
    harness = _ready_upgrade_harness(tmp_path)
    harness.simulation.configure_flash_failures({10_000, 20_000})
    try:
        queued = harness.updater.queue_local(harness.target_path)
        assert harness.updater.process_active()

        update = harness.store.get_mcu_firmware_update(queued["updateUid"])
        assert update["state"] == "FAILED_LOCKED"
        assert update["last_error_code"] == "SIMULATED_FLASH_FAILED"
        assert update["target_attempt_count"] == 3
        assert update["rollback_attempt_count"] == 3
        assert harness.simulation.flash_attempt_version_codes == [
            10_000,
            20_000,
            20_000,
            20_000,
            10_000,
            10_000,
            10_000,
        ]
        assert harness.store.get_maintenance_lock()["owner_uid"] == queued[
            "updateUid"
        ]
        assert harness.model.runtime_mode == "SYSTEM_BOOTLOADER"
        assert harness.model.firmware_prepare_count == 1
    finally:
        harness.close()
