from __future__ import annotations

import io
import json

import pytest
import uuid
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from edge_store import EdgeStore
from mcu_firmware_package import create_package, generate_identity
from mcu_firmware_updater import (
    CosFirmwareDownloader,
    FirmwarePackageCache,
    McuUpdateError,
    McuFirmwareUpdater,
    Stm32FlashRunner,
    WiringOpBootControl,
)

HARDWARE = "ECOBIN_MAINBOARD_V1.1"
KEY_ID = "RELEASE_2026_01"


def signing_key(tmp_path: Path):
    private = Ed25519PrivateKey.generate()
    path = tmp_path / "release-private.pem"
    path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return private, path


def build_package(
    tmp_path: Path,
    private_key_path: Path,
    *,
    version: str,
    version_code: int,
    marker: int,
):
    directory = tmp_path / f"release-{version_code}"
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
        b"\x00\x20\x00\x08" + bytes((marker,)) * 4092
    )
    package_path = directory / "firmware.efw"
    verified = create_package(
        image_path=image_path,
        identity_metadata_path=identity_path,
        private_key_path=private_key_path,
        key_id=KEY_ID,
        hardware_compatibility=HARDWARE,
        build_commit=f"{marker:02x}" * 8,
        built_at="2026-08-19T10:00:00Z",
        output_path=package_path,
    )
    return package_path, verified


class FakeUart:
    compatibility_mode = True

    def __init__(self):
        self.is_open = False
        self.current_manifest = None
        self.failed_self_test_identities = set()
        self.internal_error_identities = set()
        self.open_count = 0
        self.close_count = 0
        self.prepare_count = 0

    def open(self):
        self.is_open = True
        self.open_count += 1
        return True

    def close(self):
        self.is_open = False
        self.close_count += 1

    def query_firmware_identity(self):
        if not self.is_open or self.current_manifest is None:
            return {"queryStatus": "TIMEOUT"}
        manifest = self.current_manifest
        return {
            "queryStatus": "OK",
            "statusCode": (
                3
                if manifest["firmwareIdentityHex"]
                in self.internal_error_identities
                else 0
            ),
            "protocolRevision": manifest["fixedFrameRevision"],
            "firmwareVersionCode": manifest["firmwareVersionCode"],
            "firmwareVersion": manifest["firmwareVersion"],
            "firmwareIdentityHex": manifest["firmwareIdentityHex"],
        }

    def execute_firmware_update_prepare(self):
        self.prepare_count += 1
        return {
            "queryStatus": "OK",
            "statusCode": 0,
            "status": "OK",
            "safeFlags": 0x1F,
            "executed": True,
        }

    def query_self_test(self):
        failed = (
            self.current_manifest is None
            or self.current_manifest["firmwareIdentityHex"]
            in self.failed_self_test_identities
        )
        return {
            "queryStatus": "OK" if not failed else "PROTOCOL_ERROR",
            "communicationHealthy": not failed,
            "validFlags": 3 if not failed else 0,
            "weightValid": not failed,
            "infraredValid": not failed,
            "smokeSensorHealth": "OK" if not failed else "PROTOCOL_ERROR",
        }


class FakeBootControl:
    def __init__(self):
        self.operations = []

    def enter_system_bootloader(self):
        self.operations.append("BOOTLOADER")

    def boot_application(self):
        self.operations.append("APPLICATION")

    def force_application_selection(self):
        self.operations.append("APPLICATION_SELECTED")


class InstallingFlashRunner:
    def __init__(self, uart: FakeUart, manifests_by_image_sha: dict):
        self.uart = uart
        self.manifests_by_image_sha = manifests_by_image_sha
        self.calls = []
        self.failures_remaining = 0
        self.unexpected_failures_remaining = 0

    def flash(self, image_path: Path, image_size: int, *, manifest=None):
        del manifest
        self.calls.append((image_path, image_size))
        if self.unexpected_failures_remaining:
            self.unexpected_failures_remaining -= 1
            raise RuntimeError("injected unexpected flash failure")
        if self.failures_remaining:
            self.failures_remaining -= 1
            from mcu_firmware_updater import McuUpdateError

            raise McuUpdateError("STM32FLASH_FAILED", "injected flash failure")
        self.uart.current_manifest = self.manifests_by_image_sha[
            image_path.stem
        ]
        return {"returnCode": 0}


class FailingOnceDownloader:
    def __init__(self, source_path: Path, failures: int = 1):
        self.source_path = source_path
        self.calls = 0
        self.failures = failures

    def download(self, *, destination_directory: Path, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise McuUpdateError(
                "COS_DOWNLOAD_FAILED",
                "injected temporary COS failure",
            )
        destination = destination_directory / "retried-firmware.efw"
        destination.write_bytes(self.source_path.read_bytes())
        return destination


class UnexpectedFailingDownloader:
    def download(self, **_kwargs):
        raise OSError("injected cache I/O failure")


class DenyFactorySealGate:
    def require_command_allowed(self, _command_type):
        raise RuntimeError("factory seal is not complete")


def updater_fixture(tmp_path: Path):
    private, private_path = signing_key(tmp_path)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    cache = FirmwarePackageCache(
        tmp_path / "cache",
        {KEY_ID: private.public_key()},
        HARDWARE,
    )
    uart = FakeUart()
    boot = FakeBootControl()
    manifests = {}
    flasher = InstallingFlashRunner(uart, manifests)
    updater = McuFirmwareUpdater(
        store=store,
        uart_link=uart,
        package_cache=cache,
        boot_control=boot,
        flash_runner=flasher,
    )
    return private_path, store, cache, uart, boot, flasher, manifests, updater


def register_manifest(manifests: dict, verified):
    manifests[verified.manifest["imageSha256"]] = verified.manifest


def install_first_stable(
    tmp_path,
    private_path,
    store,
    uart,
    manifests,
    updater,
    *,
    version="1.0.0",
    version_code=10000,
    marker=1,
):
    package, verified = build_package(
        tmp_path,
        private_path,
        version=version,
        version_code=version_code,
        marker=marker,
    )
    register_manifest(manifests, verified)
    queued = updater.queue_local(package, legacy_preflight=True)
    assert updater.process_active()
    assert store.get_mcu_firmware_update(queued["updateUid"])["state"] == "SUCCEEDED"
    assert uart.current_manifest == verified.manifest
    return package, verified


def test_local_legacy_first_install_is_verified_flashed_and_promoted(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        boot,
        flasher,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    package, verified = build_package(
        tmp_path,
        private_path,
        version="1.0.0",
        version_code=10000,
        marker=1,
    )
    register_manifest(manifests, verified)

    queued = updater.queue_local(
        package,
        legacy_preflight=True,
        requested_reason="first revision-2 install",
    )
    assert updater.process_active()

    update = store.get_mcu_firmware_update(queued["updateUid"])
    stable = store.get_mcu_firmware_state()
    assert update["state"] == "SUCCEEDED"
    assert update["target_attempt_count"] == 1
    assert stable["current_manifest"] == verified.manifest
    assert store.get_maintenance_lock() is None
    assert len(flasher.calls) == 1
    assert boot.operations == [
        "BOOTLOADER",
        "APPLICATION",
        "APPLICATION_SELECTED",
    ]
    assert uart.is_open is True


def test_revision_two_preflight_uses_f2_prepare_before_update(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        _,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target, verified = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, verified)

    queued = updater.queue_local(target)
    assert updater.process_active()

    assert store.get_mcu_firmware_update(queued["updateUid"])["state"] == "SUCCEEDED"
    assert uart.prepare_count == 1


def test_three_target_failures_automatically_restore_previous_stable(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        _,
        flasher,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    _, stable = install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    uart.failed_self_test_identities.add(
        target.manifest["firmwareIdentityHex"]
    )
    calls_before = len(flasher.calls)

    queued = updater.queue_local(target_path)
    assert updater.process_active()

    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "ROLLED_BACK"
    assert update["target_attempt_count"] == 3
    assert update["rollback_attempt_count"] == 1
    assert uart.current_manifest == stable.manifest
    assert store.get_mcu_firmware_state()["current_manifest"] == stable.manifest
    assert len(flasher.calls) - calls_before == 4
    assert uart.prepare_count == 4
    assert store.get_maintenance_lock() is None


def test_f3_internal_error_can_never_promote_target_firmware(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        _,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    _, stable = install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    uart.internal_error_identities.add(
        target.manifest["firmwareIdentityHex"]
    )

    queued = updater.queue_local(target_path, legacy_preflight=True)
    assert updater.process_active()

    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "ROLLED_BACK"
    assert update["target_attempt_count"] == 3
    assert store.get_mcu_firmware_state()["current_manifest"] == (
        stable.manifest
    )
    assert uart.current_manifest == stable.manifest
    assert uart.prepare_count == 3


def test_cloud_download_failure_is_journaled_reported_and_retryable(
    tmp_path,
):
    (
        private_path,
        store,
        _,
        _,
        _,
        _,
        _,
        updater,
    ) = updater_fixture(tmp_path)
    package_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    updater.downloader = FailingOnceDownloader(package_path)
    updater.device_name = "SN-TEST-1"
    deployment_uid = str(uuid.uuid4())
    command_uid = str(uuid.uuid4())
    arguments = {
        "deployment_uid": deployment_uid,
        "command_uid": command_uid,
        "object_key": (
            f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
            f"{target.package_sha256}.efw"
        ),
        "package_sha256": target.package_sha256,
        "package_size": target.package_size,
        "cos_grant": {
            "keyPrefix": (
                f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
            )
        },
        "release_uid": target.manifest["releaseUid"],
        "firmware_version": target.manifest["firmwareVersion"],
        "firmware_version_code": target.manifest[
            "firmwareVersionCode"
        ],
        "firmware_identity_hex": target.manifest[
            "firmwareIdentityHex"
        ],
    }

    with pytest.raises(McuUpdateError) as failure:
        updater.queue_cloud(**arguments)
    assert failure.value.code == "COS_DOWNLOAD_FAILED"

    interrupted = store.get_mcu_firmware_update_by_deployment(
        deployment_uid
    )
    assert interrupted is not None
    assert interrupted["state"] == "PACKAGE_FETCH_FAILED"
    assert interrupted["last_error_code"] == "COS_DOWNLOAD_FAILED"
    stages = [
        json.loads(row["payload_json"])["payload"]["stage"]
        for row in store.list_pending_events()
    ]
    assert stages == ["QUEUED", "PACKAGE_FETCH_FAILED"]

    queued = updater.queue_cloud(**arguments)
    assert queued["updateUid"] == interrupted["update_uid"]
    assert queued["state"] == "QUEUED"
    assert store.get_mcu_firmware_update(
        queued["updateUid"]
    )["package_ready"] is True


def test_cloud_update_rechecks_seal_before_f2_after_queue(
    tmp_path,
):
    (
        private_path,
        store,
        _,
        uart,
        _,
        flasher,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    package_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    updater.downloader = FailingOnceDownloader(
        package_path,
        failures=0,
    )
    updater.device_name = "SN-TEST-1"
    updater.factory_seal_gate = DenyFactorySealGate()
    prepare_before = uart.prepare_count
    flash_before = len(flasher.calls)
    queued = updater.queue_cloud(
        deployment_uid=str(uuid.uuid4()),
        command_uid=str(uuid.uuid4()),
        object_key=(
            f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
            f"{target.package_sha256}.efw"
        ),
        package_sha256=target.package_sha256,
        package_size=target.package_size,
        cos_grant={
            "keyPrefix": (
                f"ecobin/mcu-firmware/"
                f"{target.manifest['releaseUid']}/"
            )
        },
        release_uid=target.manifest["releaseUid"],
        firmware_version=target.manifest["firmwareVersion"],
        firmware_version_code=target.manifest["firmwareVersionCode"],
        firmware_identity_hex=target.manifest["firmwareIdentityHex"],
    )

    assert updater.process_active()

    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "FACTORY_NOT_SEALED"
    assert update["target_attempt_count"] == 0
    assert uart.prepare_count == prepare_before
    assert len(flasher.calls) == flash_before
    assert store.get_maintenance_lock() is None


def test_each_package_acquisition_failure_emits_a_fresh_retry_fact(
    tmp_path,
):
    (
        private_path,
        store,
        _,
        _,
        _,
        _,
        _,
        updater,
    ) = updater_fixture(tmp_path)
    package_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    updater.downloader = FailingOnceDownloader(package_path, failures=3)
    updater.device_name = "SN-TEST-1"
    arguments = {
        "deployment_uid": str(uuid.uuid4()),
        "command_uid": str(uuid.uuid4()),
        "object_key": (
            f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
            f"{target.package_sha256}.efw"
        ),
        "package_sha256": target.package_sha256,
        "package_size": target.package_size,
        "cos_grant": {
            "keyPrefix": (
                f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
            )
        },
        "release_uid": target.manifest["releaseUid"],
        "firmware_version": target.manifest["firmwareVersion"],
        "firmware_version_code": target.manifest[
            "firmwareVersionCode"
        ],
        "firmware_identity_hex": target.manifest[
            "firmwareIdentityHex"
        ],
    }

    for _ in range(3):
        with pytest.raises(McuUpdateError):
            updater.queue_cloud(**arguments)

    stages = [
        json.loads(row["payload_json"])["payload"]["stage"]
        for row in store.list_pending_events()
    ]
    assert stages == [
        "QUEUED",
        "PACKAGE_FETCH_FAILED",
        "PACKAGE_FETCH_FAILED",
        "REJECTED",
    ]
    update = store.get_mcu_firmware_update_by_deployment(
        arguments["deployment_uid"]
    )
    assert update["state"] == "REJECTED"
    assert update["package_acquisition_attempt_count"] == 3
    assert store.get_maintenance_lock() is None


def test_unexpected_package_io_failure_is_terminal_and_unlocks(tmp_path):
    (
        private_path,
        store,
        _,
        _,
        _,
        _,
        _,
        updater,
    ) = updater_fixture(tmp_path)
    _, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    updater.downloader = UnexpectedFailingDownloader()
    updater.device_name = "SN-TEST-1"

    with pytest.raises(McuUpdateError) as failure:
        updater.queue_cloud(
            deployment_uid=str(uuid.uuid4()),
            command_uid=str(uuid.uuid4()),
            object_key=(
                f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
                f"{target.package_sha256}.efw"
            ),
            package_sha256=target.package_sha256,
            package_size=target.package_size,
            cos_grant={
                "keyPrefix": (
                    f"ecobin/mcu-firmware/"
                    f"{target.manifest['releaseUid']}/"
                )
            },
            release_uid=target.manifest["releaseUid"],
            firmware_version=target.manifest["firmwareVersion"],
            firmware_version_code=target.manifest[
                "firmwareVersionCode"
            ],
            firmware_identity_hex=target.manifest[
                "firmwareIdentityHex"
            ],
        )

    assert failure.value.code == "PACKAGE_ACQUISITION_FAILED"
    update = store.get_mcu_firmware_update_by_deployment(
        next(
            json.loads(row["payload_json"])["payload"]["deploymentUid"]
            for row in store.list_pending_events()
        )
    )
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "PACKAGE_ACQUISITION_FAILED"
    stages = [
        json.loads(row["payload_json"])["payload"]["stage"]
        for row in store.list_pending_events()
    ]
    assert stages == ["QUEUED", "REJECTED"]
    assert store.get_maintenance_lock() is None


def test_invalid_signature_is_rejected_without_credential_retry(tmp_path):
    (
        _,
        store,
        _,
        _,
        _,
        _,
        _,
        updater,
    ) = updater_fixture(tmp_path)
    rogue_dir = tmp_path / "rogue-key"
    rogue_dir.mkdir()
    _, rogue_private_path = signing_key(rogue_dir)
    rogue_package, rogue = build_package(
        tmp_path,
        rogue_private_path,
        version="9.9.9",
        version_code=90_909,
        marker=9,
    )
    updater.downloader = FailingOnceDownloader(
        rogue_package,
        failures=0,
    )
    updater.device_name = "SN-TEST-1"
    deployment_uid = str(uuid.uuid4())

    with pytest.raises(McuUpdateError) as failure:
        updater.queue_cloud(
            deployment_uid=deployment_uid,
            command_uid=str(uuid.uuid4()),
            object_key=(
                f"ecobin/mcu-firmware/{rogue.manifest['releaseUid']}/"
                f"{rogue.package_sha256}.efw"
            ),
            package_sha256=rogue.package_sha256,
            package_size=rogue.package_size,
            cos_grant={
                "keyPrefix": (
                    f"ecobin/mcu-firmware/"
                    f"{rogue.manifest['releaseUid']}/"
                )
            },
            release_uid=rogue.manifest["releaseUid"],
            firmware_version=rogue.manifest["firmwareVersion"],
            firmware_version_code=rogue.manifest[
                "firmwareVersionCode"
            ],
            firmware_identity_hex=rogue.manifest[
                "firmwareIdentityHex"
            ],
        )

    assert failure.value.code == "PACKAGE_INVALID"
    update = store.get_mcu_firmware_update_by_deployment(deployment_uid)
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "PACKAGE_INVALID"
    assert store.get_maintenance_lock() is None
    assert [
        json.loads(row["payload_json"])["payload"]["stage"]
        for row in store.list_pending_events()
    ] == ["QUEUED", "REJECTED"]


def test_busy_physical_work_is_reported_as_terminal_rejection(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        _,
        _,
        _,
        updater,
    ) = updater_fixture(tmp_path)
    package_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20_000,
        marker=2,
    )
    updater.downloader = FailingOnceDownloader(package_path, failures=0)
    updater.device_name = "SN-TEST-1"
    assert store.acquire_work_slot(
        "DELIVERY",
        str(uuid.uuid4()),
        1,
        {},
    )
    deployment_uid = str(uuid.uuid4())

    with pytest.raises(McuUpdateError) as failure:
        updater.queue_cloud(
            deployment_uid=deployment_uid,
            command_uid=str(uuid.uuid4()),
            object_key=(
                f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
                f"{target.package_sha256}.efw"
            ),
            package_sha256=target.package_sha256,
            package_size=target.package_size,
            cos_grant={
                "keyPrefix": (
                    f"ecobin/mcu-firmware/"
                    f"{target.manifest['releaseUid']}/"
                )
            },
            release_uid=target.manifest["releaseUid"],
            firmware_version=target.manifest["firmwareVersion"],
            firmware_version_code=target.manifest[
                "firmwareVersionCode"
            ],
            firmware_identity_hex=target.manifest[
                "firmwareIdentityHex"
            ],
        )

    assert failure.value.code == "PHYSICAL_WORK_BUSY"
    update = store.get_mcu_firmware_update_by_deployment(deployment_uid)
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "PHYSICAL_WORK_BUSY"
    assert store.get_maintenance_lock() is None
    assert store.get_work_slot()["work_type"] == "DELIVERY"
    assert uart.prepare_count == 0
    assert [
        json.loads(row["payload_json"])["payload"]["stage"]
        for row in store.list_pending_events()
    ] == ["REJECTED"]


def test_other_maintenance_owner_is_preserved_when_cloud_update_rejected(
    tmp_path,
):
    (
        private_path,
        store,
        _,
        _,
        _,
        _,
        _,
        updater,
    ) = updater_fixture(tmp_path)
    package_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20_000,
        marker=2,
    )
    updater.downloader = FailingOnceDownloader(package_path, failures=0)
    updater.device_name = "SN-TEST-1"
    owner_uid = str(uuid.uuid4())
    assert store.begin_mcu_firmware_update(
        update_uid=owner_uid,
        deployment_uid=str(uuid.uuid4()),
        source="LOCAL",
        package_path=str(package_path.resolve()),
        package_sha256=target.package_sha256,
        manifest=target.manifest,
    ) == "ACCEPTED"
    deployment_uid = str(uuid.uuid4())

    with pytest.raises(McuUpdateError) as failure:
        updater.queue_cloud(
            deployment_uid=deployment_uid,
            command_uid=str(uuid.uuid4()),
            object_key=(
                f"ecobin/mcu-firmware/{target.manifest['releaseUid']}/"
                f"{target.package_sha256}.efw"
            ),
            package_sha256=target.package_sha256,
            package_size=target.package_size,
            cos_grant={
                "keyPrefix": (
                    f"ecobin/mcu-firmware/"
                    f"{target.manifest['releaseUid']}/"
                )
            },
            release_uid=target.manifest["releaseUid"],
            firmware_version=target.manifest["firmwareVersion"],
            firmware_version_code=target.manifest[
                "firmwareVersionCode"
            ],
            firmware_identity_hex=target.manifest[
                "firmwareIdentityHex"
            ],
        )

    assert failure.value.code == "MAINTENANCE_BUSY"
    update = store.get_mcu_firmware_update_by_deployment(deployment_uid)
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "MAINTENANCE_BUSY"
    assert store.get_maintenance_lock()["owner_uid"] == owner_uid
    assert [
        json.loads(row["payload_json"])["payload"]["stage"]
        for row in store.list_pending_events()
    ] == ["REJECTED"]


def test_target_and_rollback_failure_leave_persistent_business_lock(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        _,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    _, stable = install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    uart.failed_self_test_identities.update(
        {
            target.manifest["firmwareIdentityHex"],
            stable.manifest["firmwareIdentityHex"],
        }
    )

    queued = updater.queue_local(target_path)
    assert updater.process_active()

    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "FAILED_LOCKED"
    assert update["target_attempt_count"] == 3
    assert update["rollback_attempt_count"] == 3
    assert store.get_maintenance_lock()["owner_uid"] == queued["updateUid"]
    assert uart.is_open is False
    assert uart.prepare_count == 6


def test_unconfirmed_prepare_execution_recovers_application_then_rejects(
    tmp_path,
):
    (
        private_path,
        store,
        _,
        uart,
        boot,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)

    def fail_prepare():
        raise RuntimeError("injected unexpected preflight failure")

    uart.execute_firmware_update_prepare = fail_prepare
    queued = updater.queue_local(target_path)

    assert updater.process_active()
    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "MCU_PREPARE_EXECUTION_UNCONFIRMED"
    assert update["target_attempt_count"] == 0
    assert store.get_maintenance_lock() is None
    assert boot.operations[-2:] == ["APPLICATION", "APPLICATION_SELECTED"]


def test_prepare_execution_recovery_failure_keeps_business_locked(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        boot,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)

    uart.execute_firmware_update_prepare = lambda: {
        "queryStatus": "OK",
        "statusCode": 3,
        "status": "INTERNAL_ERROR",
        "safeFlags": 0x0F,
        "executed": False,
    }
    uart.open = lambda: False
    queued = updater.queue_local(target_path)

    assert updater.process_active()
    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "FAILED_LOCKED"
    assert update["last_error_code"] == "MCU_PREPARE_RECOVERY_FAILED"
    assert store.get_maintenance_lock()["owner_uid"] == queued["updateUid"]
    assert "APPLICATION" in boot.operations


def test_confirmed_prepare_execution_failure_recovers_then_rejects(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        boot,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)

    uart.execute_firmware_update_prepare = lambda: {
        "queryStatus": "OK",
        "statusCode": 3,
        "status": "INTERNAL_ERROR",
        "safeFlags": 0x0F,
        "executed": False,
    }
    queued = updater.queue_local(target_path)

    assert updater.process_active()
    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "MCU_PREPARE_EXECUTION_FAILED"
    assert store.get_maintenance_lock() is None
    assert boot.operations[-2:] == ["APPLICATION", "APPLICATION_SELECTED"]


@pytest.mark.parametrize("commit_before_error", [False, True])
def test_prepared_journal_failure_recovers_application_before_unlocking(
    tmp_path,
    monkeypatch,
    commit_before_error,
):
    (
        private_path,
        store,
        _,
        uart,
        boot,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    operations_before = len(boot.operations)
    original_transition = store.transition_mcu_firmware_update

    def fail_prepared_transition(update_uid, state, **kwargs):
        if state == "PREPARED":
            if commit_before_error:
                assert original_transition(update_uid, state, **kwargs)
            raise OSError("injected PREPARED journal failure")
        return original_transition(update_uid, state, **kwargs)

    monkeypatch.setattr(
        store,
        "transition_mcu_firmware_update",
        fail_prepared_transition,
    )
    queued = updater.queue_local(target_path)

    assert updater.process_active()
    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert uart.prepare_count == 1
    assert boot.operations[operations_before:] == [
        "APPLICATION",
        "APPLICATION_SELECTED",
    ]
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "MCU_PREPARED_JOURNAL_FAILED"
    assert store.get_maintenance_lock() is None


def test_prepared_journal_failure_locks_when_application_recovery_fails(
    tmp_path,
    monkeypatch,
):
    (
        private_path,
        store,
        _,
        uart,
        boot,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    operations_before = len(boot.operations)
    original_transition = store.transition_mcu_firmware_update

    def fail_prepared_transition(update_uid, state, **kwargs):
        if state == "PREPARED":
            raise OSError("injected PREPARED journal failure")
        return original_transition(update_uid, state, **kwargs)

    monkeypatch.setattr(
        store,
        "transition_mcu_firmware_update",
        fail_prepared_transition,
    )
    uart.open = lambda: False
    queued = updater.queue_local(target_path)

    assert updater.process_active()
    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert uart.prepare_count == 1
    assert boot.operations[operations_before:] == [
        "APPLICATION",
        "APPLICATION_SELECTED",
    ]
    assert update["state"] == "FAILED_LOCKED"
    assert update["last_error_code"] == "MCU_PREPARE_RECOVERY_FAILED"
    assert store.get_maintenance_lock()["owner_uid"] == queued["updateUid"]


def test_restart_with_prepare_marker_recovers_before_rejecting(
    tmp_path,
    monkeypatch,
):
    (
        private_path,
        store,
        _,
        uart,
        boot,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    queued = updater.queue_local(target_path)
    update_uid = queued["updateUid"]
    assert store.transition_mcu_firmware_update(update_uid, "PREFLIGHT")
    expected_identity = uart.query_firmware_identity()
    assert store.arm_mcu_firmware_prepare_recovery(
        update_uid,
        expected_identity,
    )
    operations_before = len(boot.operations)
    original_query = uart.query_firmware_identity
    query_count = 0

    def fail_first_query_after_restart():
        nonlocal query_count
        query_count += 1
        if query_count == 1:
            return {"queryStatus": "TIMEOUT"}
        return original_query()

    monkeypatch.setattr(
        uart,
        "query_firmware_identity",
        fail_first_query_after_restart,
    )

    assert updater.process_active()
    update = store.get_mcu_firmware_update(update_uid)
    assert boot.operations[operations_before:] == [
        "APPLICATION",
        "APPLICATION_SELECTED",
    ]
    assert update["state"] == "REJECTED"
    assert update["last_error_code"] == "FIRMWARE_IDENTITY_UNAVAILABLE"
    assert store.get_maintenance_lock() is None


def test_unexpected_postflash_failure_automatically_rolls_back(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        _,
        flasher,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    _, stable = install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
    )
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    register_manifest(manifests, target)
    flasher.unexpected_failures_remaining = 1
    queued = updater.queue_local(target_path)

    assert updater.process_active()
    update = store.get_mcu_firmware_update(queued["updateUid"])
    assert update["state"] == "ROLLED_BACK"
    assert update["target_attempt_count"] == 1
    assert update["rollback_attempt_count"] == 1
    assert uart.current_manifest == stable.manifest
    assert store.get_maintenance_lock() is None


def test_downgrade_is_rejected_unless_local_break_glass_is_explicit(tmp_path):
    (
        private_path,
        store,
        _,
        uart,
        _,
        _,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    install_first_stable(
        tmp_path,
        private_path,
        store,
        uart,
        manifests,
        updater,
        version="2.0.0",
        version_code=20000,
        marker=2,
    )
    older_path, older = build_package(
        tmp_path,
        private_path,
        version="1.0.0",
        version_code=10000,
        marker=1,
    )
    register_manifest(manifests, older)

    rejected = updater.queue_local(older_path)
    assert updater.process_active()
    assert store.get_mcu_firmware_update(rejected["updateUid"])["state"] == "REJECTED"
    assert uart.current_manifest["firmwareVersionCode"] == 20000

    allowed = updater.queue_local(older_path, allow_downgrade=True)
    assert updater.process_active()
    assert store.get_mcu_firmware_update(allowed["updateUid"])["state"] == "SUCCEEDED"
    assert uart.current_manifest["firmwareVersionCode"] == 10000


def test_restart_verifies_completed_target_before_reflashing(tmp_path):
    (
        private_path,
        store,
        cache,
        uart,
        _,
        flasher,
        manifests,
        updater,
    ) = updater_fixture(tmp_path)
    target_path, target = build_package(
        tmp_path,
        private_path,
        version="1.0.0",
        version_code=10000,
        marker=1,
    )
    register_manifest(manifests, target)
    queued = updater.queue_local(target_path, legacy_preflight=True)
    store.transition_mcu_firmware_update(queued["updateUid"], "PREPARED")
    store.record_mcu_firmware_attempt(queued["updateUid"], rollback=False)
    store.transition_mcu_firmware_update(queued["updateUid"], "VERIFYING_TARGET")
    uart.current_manifest = target.manifest
    calls_before = len(flasher.calls)

    resumed = McuFirmwareUpdater(
        store=store,
        uart_link=uart,
        package_cache=cache,
        boot_control=FakeBootControl(),
        flash_runner=flasher,
    )
    assert resumed.process_active()

    assert store.get_mcu_firmware_update(queued["updateUid"])["state"] == "SUCCEEDED"
    assert len(flasher.calls) == calls_before


def test_wiringop_boot_control_defaults_to_high_active_open_drain_reset():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="")

    control = WiringOpBootControl(
        gpio_path="/usr/local/bin/gpio",
        boot0_wpi=2,
        reset_wpi=5,
        command_runner=run,
        sleeper=lambda _: None,
    )

    control.enter_system_bootloader()
    control.boot_application()
    control.force_application_selection()

    assert [call[0] for call in calls] == [
        ["/usr/local/bin/gpio", "mode", "2", "out"],
        ["/usr/local/bin/gpio", "mode", "5", "out"],
        ["/usr/local/bin/gpio", "write", "2", "1"],
        ["/usr/local/bin/gpio", "write", "5", "1"],
        ["/usr/local/bin/gpio", "write", "5", "0"],
        ["/usr/local/bin/gpio", "mode", "2", "out"],
        ["/usr/local/bin/gpio", "mode", "5", "out"],
        ["/usr/local/bin/gpio", "write", "2", "0"],
        ["/usr/local/bin/gpio", "write", "5", "1"],
        ["/usr/local/bin/gpio", "write", "5", "0"],
        ["/usr/local/bin/gpio", "mode", "2", "out"],
        ["/usr/local/bin/gpio", "mode", "5", "out"],
        ["/usr/local/bin/gpio", "write", "2", "0"],
        ["/usr/local/bin/gpio", "write", "5", "0"],
    ]
    assert all("shell" not in kwargs for _, kwargs in calls)


def test_stm32flash_runner_uses_8e1_bounded_flash_range_and_verify(tmp_path):
    image = tmp_path / "image.bin"
    image.write_bytes(b"x" * 1024)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="verified")

    runner = Stm32FlashRunner(
        executable_path="/usr/bin/stm32flash",
        serial_port="/dev/ttyS5",
        command_runner=run,
    )
    result = runner.flash(image, 1024)

    assert calls[0][0] == [
        "/usr/bin/stm32flash",
        "-b",
        "115200",
        "-m",
        "8e1",
        "-f",
        "-S",
        "0x08000000:1024",
        "-w",
        str(image.resolve()),
        "-v",
        "-n",
        "3",
        "/dev/ttyS5",
    ]
    assert "shell" not in calls[0][1]
    assert result["output"] == "verified"


def test_cos_downloader_bounds_and_hashes_private_object(tmp_path):
    payload = b"signed-package-bytes"
    import hashlib

    class Client:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": "private-bucket", "Key": "mcu/a.efw"}
            return {"Body": io.BytesIO(payload)}

    downloader = CosFirmwareDownloader(
        client_factory=lambda grant: Client(),
    )
    grant = {
        "bucket": "private-bucket",
        "keyPrefix": "mcu/",
        "tmpSecretId": "never-persist-me",
        "tmpSecretKey": "never-persist-me-either",
        "sessionTokenParts": ["secret-token"],
    }
    path = downloader.download(
        grant=grant,
        object_key="mcu/a.efw",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_size=len(payload),
        destination_directory=tmp_path,
    )

    assert path.read_bytes() == payload
