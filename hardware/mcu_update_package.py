"""Verify signed MCU packages and materialize only fixed updater-owned slots."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mcu_firmware_package import (
    KEY_ID_PATTERN,
    MAX_PACKAGE_SIZE,
    FirmwarePackageError,
    VerifiedFirmwarePackage,
    load_public_key,
    verify_package,
)


class McuPackageStageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StagedMcuUpdate:
    target_manifest: dict[str, Any]
    rollback_manifest: dict[str, Any]
    target_package_sha256: str
    rollback_package_sha256: str


class McuFirmwarePackageStager:
    """Consume ``incoming/<update UID>/{target,rollback}.efw`` only."""

    def __init__(
        self,
        firmware_root: str | os.PathLike[str],
        public_key_root: str | os.PathLike[str],
        hardware_compatibility: str,
    ) -> None:
        if not isinstance(hardware_compatibility, str) or not hardware_compatibility:
            raise ValueError("MCU hardware compatibility must be configured")
        self.firmware_root = Path(firmware_root)
        self.public_key_root = Path(public_key_root)
        self.hardware_compatibility = hardware_compatibility

    def stage_pair(
        self,
        *,
        update_uid: str,
        expected_target_package_sha256: str,
        expected_rollback_package_sha256: str,
    ) -> StagedMcuUpdate:
        try:
            parsed_update_uid = uuid.UUID(update_uid)
        except (ValueError, TypeError, AttributeError) as error:
            raise McuPackageStageError(
                "MCU_UPDATE_IDENTITY_INVALID",
                "MCU update identity must be a canonical UUIDv4",
            ) from error
        if parsed_update_uid.version != 4 or str(parsed_update_uid) != update_uid:
            raise McuPackageStageError(
                "MCU_UPDATE_IDENTITY_INVALID",
                "MCU update identity must be a canonical UUIDv4",
            )
        _require_hex_sha256(expected_target_package_sha256)
        _require_hex_sha256(expected_rollback_package_sha256)
        self._require_private_directory(self.firmware_root, "MCU firmware root")
        incoming_root = self.firmware_root / "incoming"
        self._require_private_directory(incoming_root, "MCU firmware incoming root")
        source_root = incoming_root / update_uid
        self._require_private_directory(source_root, "MCU firmware incoming update")
        keys = self._load_public_keys()
        target = self._verify_fixed_package(
            source_root / "target.efw",
            expected_target_package_sha256,
            keys,
        )
        rollback = self._verify_fixed_package(
            source_root / "rollback.efw",
            expected_rollback_package_sha256,
            keys,
        )
        if target.manifest["firmwareIdentityHex"] == rollback.manifest[
            "firmwareIdentityHex"
        ]:
            raise McuPackageStageError(
                "MCU_TARGET_EQUALS_ROLLBACK",
                "target MCU firmware must differ from the rollback firmware",
            )
        if int(target.manifest["firmwareVersionCode"]) < int(
            rollback.manifest["firmwareVersionCode"]
        ):
            raise McuPackageStageError(
                "MCU_DOWNGRADE_BLOCKED",
                "local candidate does not permit an MCU firmware downgrade",
            )

        update_root = self.firmware_root / update_uid
        self._create_or_require_private_directory(update_root)
        slots = {
            "target.efw": (source_root / "target.efw").read_bytes(),
            "target.bin": target.image,
            "rollback.efw": (source_root / "rollback.efw").read_bytes(),
            "rollback.bin": rollback.image,
        }
        self._preflight_existing_slots(update_root, slots)
        for name, content in slots.items():
            self._write_verified_slot(update_root / name, content)
        self._require_file_digest(update_root / "target.bin", target.manifest["imageSha256"])
        self._require_file_digest(update_root / "rollback.bin", rollback.manifest["imageSha256"])
        self._require_file_digest(update_root / "target.efw", target.package_sha256)
        self._require_file_digest(update_root / "rollback.efw", rollback.package_sha256)
        return StagedMcuUpdate(
            target_manifest=dict(target.manifest),
            rollback_manifest=dict(rollback.manifest),
            target_package_sha256=target.package_sha256,
            rollback_package_sha256=rollback.package_sha256,
        )

    def _verify_fixed_package(
        self,
        path: Path,
        expected_sha256: str,
        keys: dict[str, Ed25519PublicKey],
    ) -> VerifiedFirmwarePackage:
        self._require_private_regular_file(path, "MCU firmware incoming package")
        self._require_file_digest(path, expected_sha256)
        try:
            verified = verify_package(
                path,
                keys,
                expected_hardware_compatibility=self.hardware_compatibility,
            )
        except (OSError, FirmwarePackageError) as error:
            raise McuPackageStageError(
                "MCU_PACKAGE_INVALID",
                "signed MCU firmware package verification failed",
            ) from error
        if verified.package_sha256 != expected_sha256:
            raise McuPackageStageError(
                "MCU_PACKAGE_DIGEST_MISMATCH",
                "MCU firmware package digest changed during verification",
            )
        return verified

    def _load_public_keys(self) -> dict[str, Ed25519PublicKey]:
        try:
            metadata = self.public_key_root.lstat()
        except FileNotFoundError as error:
            raise McuPackageStageError(
                "MCU_SIGNING_KEYS_UNAVAILABLE",
                "MCU signing public-key directory is unavailable",
            ) from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or self.public_key_root.is_symlink()
            or (os.name == "posix" and metadata.st_mode & 0o022)
        ):
            raise McuPackageStageError(
                "MCU_SIGNING_KEYS_UNSAFE",
                "MCU signing public-key directory is unsafe",
            )
        keys: dict[str, Ed25519PublicKey] = {}
        for path in sorted(self.public_key_root.iterdir()):
            if path.suffix != ".pem":
                raise McuPackageStageError(
                    "MCU_SIGNING_KEYS_UNSAFE",
                    "MCU signing directory contains an unsupported entry",
                )
            key_id = path.stem
            if KEY_ID_PATTERN.fullmatch(key_id) is None or key_id in keys:
                raise McuPackageStageError(
                    "MCU_SIGNING_KEYS_UNSAFE",
                    "MCU signing key identity is invalid",
                )
            self._require_public_regular_file(path)
            try:
                keys[key_id] = load_public_key(path)
            except (OSError, ValueError, TypeError) as error:
                raise McuPackageStageError(
                    "MCU_SIGNING_KEY_INVALID",
                    "MCU signing public key is invalid",
                ) from error
        if not keys:
            raise McuPackageStageError(
                "MCU_SIGNING_KEYS_UNAVAILABLE",
                "no MCU signing public key is installed",
            )
        return keys

    @staticmethod
    def _require_public_regular_file(path: Path) -> None:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (os.name == "posix" and metadata.st_mode & 0o022)
            or metadata.st_size <= 0
            or metadata.st_size > 64 * 1024
        ):
            raise McuPackageStageError(
                "MCU_SIGNING_KEYS_UNSAFE",
                "MCU signing public-key file is unsafe",
            )

    @staticmethod
    def _require_private_regular_file(path: Path, label: str) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            raise McuPackageStageError(
                "MCU_PACKAGE_NOT_READY",
                f"{label} does not exist",
            ) from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > MAX_PACKAGE_SIZE
            or (os.name == "posix" and metadata.st_uid != os.getuid())
            or (os.name == "posix" and metadata.st_mode & 0o077)
        ):
            raise McuPackageStageError(
                "MCU_PACKAGE_UNSAFE",
                f"{label} permissions, type, owner, or size are unsafe",
            )

    @staticmethod
    def _require_private_directory(path: Path, label: str) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            raise McuPackageStageError(
                "MCU_PACKAGE_NOT_READY",
                f"{label} does not exist",
            ) from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or path.is_symlink()
            or (os.name == "posix" and metadata.st_uid != os.getuid())
            or (
                os.name == "posix"
                and stat.S_IMODE(metadata.st_mode) != 0o700
            )
        ):
            raise McuPackageStageError(
                "MCU_PACKAGE_UNSAFE",
                f"{label} is not an updater-owned 0700 directory",
            )

    @staticmethod
    def _create_or_require_private_directory(path: Path) -> None:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        McuFirmwarePackageStager._require_private_directory(
            path,
            "MCU firmware staged update",
        )

    @staticmethod
    def _preflight_existing_slots(
        update_root: Path,
        slots: dict[str, bytes],
    ) -> None:
        """Detect every existing-slot conflict before materializing bytes."""

        for name, content in slots.items():
            path = update_root / name
            if not path.exists() and not path.is_symlink():
                continue
            McuFirmwarePackageStager._require_private_regular_file(
                path,
                "MCU firmware staged slot",
            )
            if path.read_bytes() != content:
                raise McuPackageStageError(
                    "MCU_STAGED_SLOT_CONFLICT",
                    "existing MCU firmware staged slot has different bytes",
                )

    @staticmethod
    def _write_verified_slot(path: Path, content: bytes) -> None:
        if path.exists() or path.is_symlink():
            McuFirmwarePackageStager._require_private_regular_file(
                path,
                "MCU firmware staged slot",
            )
            if path.read_bytes() != content:
                raise McuPackageStageError(
                    "MCU_STAGED_SLOT_CONFLICT",
                    "existing MCU firmware staged slot has different bytes",
                )
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary_path = Path(temporary)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written <= 0:
                    raise OSError("MCU staged slot write made no progress")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary_path, path)
            if os.name == "posix":
                directory_descriptor = os.open(
                    path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _require_file_digest(path: Path, expected_sha256: str) -> None:
        digest = hashlib.sha256()
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise McuPackageStageError(
                    "MCU_PACKAGE_UNSAFE",
                    "MCU firmware file changed before digest verification",
                )
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            os.close(descriptor)
        if digest.hexdigest() != expected_sha256:
            raise McuPackageStageError(
                "MCU_PACKAGE_DIGEST_MISMATCH",
                "MCU firmware bytes differ from the expected SHA-256",
            )


def _require_hex_sha256(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise McuPackageStageError(
            "MCU_PACKAGE_DIGEST_INVALID",
            "MCU firmware package SHA-256 is invalid",
        )
    return value
