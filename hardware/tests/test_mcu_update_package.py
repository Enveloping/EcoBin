from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcu_firmware_package import create_package, generate_identity
from mcu_update_package import McuFirmwarePackageStager, McuPackageStageError


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
HARDWARE_COMPATIBILITY = "ecobin-stm32f103c8t6-rev1"


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def _fixture(tmp_path: Path):
    firmware_root = _private_dir(tmp_path / "firmware")
    incoming = _private_dir(firmware_root / "incoming")
    update_root = _private_dir(incoming / UPDATE_UID)
    key_root = _private_dir(tmp_path / "keys")
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path = key_root / "MCU_TEST.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    if os.name == "posix":
        private_path.chmod(0o600)
        public_path.chmod(0o644)
    return firmware_root, update_root, key_root, private_path


def _package(
    tmp_path: Path,
    *,
    output: Path,
    private_key: Path,
    version: str,
    version_code: int,
    image_byte: int,
):
    identity_dir = _private_dir(
        tmp_path / f"identity-{version_code}-{image_byte}"
    )
    image_path = identity_dir / "firmware.bin"
    image_path.write_bytes(bytes([image_byte]) * 1024)
    metadata_path = identity_dir / "identity.json"
    generate_identity(
        version=version,
        version_code=version_code,
        header_path=identity_dir / "identity.h",
        metadata_path=metadata_path,
        application_protocol_family="FIXED_FRAME",
        release_uid=uuid.UUID(
            f"{version_code:08x}-1111-4111-8111-{image_byte:012x}"
        ),
    )
    result = create_package(
        image_path=image_path,
        identity_metadata_path=metadata_path,
        private_key_path=private_key,
        key_id="MCU_TEST",
        hardware_compatibility=HARDWARE_COMPATIBILITY,
        build_commit="a" * 40,
        built_at="2026-09-03T00:00:00Z",
        output_path=output,
    )
    if os.name == "posix":
        output.chmod(0o600)
    return result


def _pair(tmp_path: Path, *, target_code: int = 2, rollback_code: int = 1):
    firmware_root, incoming, key_root, private_key = _fixture(tmp_path)
    target = _package(
        tmp_path,
        output=incoming / "target.efw",
        private_key=private_key,
        version=f"1.0.{target_code}",
        version_code=target_code,
        image_byte=0x22,
    )
    rollback = _package(
        tmp_path,
        output=incoming / "rollback.efw",
        private_key=private_key,
        version=f"1.0.{rollback_code}",
        version_code=rollback_code,
        image_byte=0x11,
    )
    stager = McuFirmwarePackageStager(
        firmware_root,
        key_root,
        HARDWARE_COMPATIBILITY,
    )
    return stager, firmware_root, target, rollback


def test_signed_target_and_rollback_are_materialized_in_fixed_private_slots(
    tmp_path,
) -> None:
    stager, root, target, rollback = _pair(tmp_path)

    staged = stager.stage_pair(
        update_uid=UPDATE_UID,
        expected_target_package_sha256=target.package_sha256,
        expected_rollback_package_sha256=rollback.package_sha256,
    )
    duplicate = stager.stage_pair(
        update_uid=UPDATE_UID,
        expected_target_package_sha256=target.package_sha256,
        expected_rollback_package_sha256=rollback.package_sha256,
    )

    update_root = root / UPDATE_UID
    assert staged == duplicate
    assert (update_root / "target.bin").read_bytes() == target.image
    assert (update_root / "rollback.bin").read_bytes() == rollback.image
    assert hashlib.sha256((update_root / "target.efw").read_bytes()).hexdigest() == (
        target.package_sha256
    )
    assert set(path.name for path in update_root.iterdir()) == {
        "target.efw",
        "target.bin",
        "rollback.efw",
        "rollback.bin",
    }


def test_expected_package_digest_is_required_before_staging(tmp_path) -> None:
    stager, root, target, rollback = _pair(tmp_path)

    with pytest.raises(McuPackageStageError) as raised:
        stager.stage_pair(
            update_uid=UPDATE_UID,
            expected_target_package_sha256="9" * 64,
            expected_rollback_package_sha256=rollback.package_sha256,
        )

    assert raised.value.code == "MCU_PACKAGE_DIGEST_MISMATCH"
    assert not (root / UPDATE_UID).exists()


def test_target_downgrade_is_rejected_before_fixed_slots_are_created(tmp_path) -> None:
    stager, root, target, rollback = _pair(
        tmp_path,
        target_code=1,
        rollback_code=2,
    )

    with pytest.raises(McuPackageStageError) as raised:
        stager.stage_pair(
            update_uid=UPDATE_UID,
            expected_target_package_sha256=target.package_sha256,
            expected_rollback_package_sha256=rollback.package_sha256,
        )

    assert raised.value.code == "MCU_DOWNGRADE_BLOCKED"
    assert not (root / UPDATE_UID).exists()


def test_existing_fixed_slot_with_other_bytes_is_never_overwritten(tmp_path) -> None:
    stager, root, target, rollback = _pair(tmp_path)
    update_root = _private_dir(root / UPDATE_UID)
    conflict = update_root / "target.bin"
    conflict.write_bytes(b"different")
    if os.name == "posix":
        conflict.chmod(0o600)

    with pytest.raises(McuPackageStageError) as raised:
        stager.stage_pair(
            update_uid=UPDATE_UID,
            expected_target_package_sha256=target.package_sha256,
            expected_rollback_package_sha256=rollback.package_sha256,
        )

    assert raised.value.code == "MCU_STAGED_SLOT_CONFLICT"
    assert conflict.read_bytes() == b"different"
