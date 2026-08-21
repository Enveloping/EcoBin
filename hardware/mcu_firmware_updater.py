"""Reboot-safe STM32F103C8T6 firmware updates over the application UART.

The edge process owns the complete safety boundary:

* signed ``.efw`` packages are verified and copied into a private cache;
* SQLite maintenance lock excludes delivery/cleaning work before BOOT0 moves;
* the application UART is closed before ``stm32flash`` opens it as 8E1;
* target and automatic rollback each get at most three full attempts;
* F3 identity plus F1 sensor self-test are required before business unlocks.

Temporary COS credentials are accepted only by :class:`CosFirmwareDownloader`
and are never retained in SQLite, files, logs, or exception messages.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mcu_firmware_package import (
    FLASH_BASE,
    KEY_ID_PATTERN,
    MAX_PACKAGE_SIZE,
    FirmwarePackageError,
    VerifiedFirmwarePackage,
    load_public_key,
    verify_package,
)

logger = logging.getLogger("mcu-firmware-updater")

TARGET_ATTEMPT_LIMIT = 3
ROLLBACK_ATTEMPT_LIMIT = 3
PACKAGE_ACQUISITION_ATTEMPT_LIMIT = 3
STM32_BOOTLOADER_BAUDRATE = 115200
STM32_BOOTLOADER_SERIAL_MODE = "8e1"
STM32FLASH_INTERNAL_RETRIES = 3
STM32FLASH_TIMEOUT_SECONDS = 120
APPLICATION_SETTLE_SECONDS = 0.35
RESET_ASSERT_SECONDS = 0.05
BOOTLOADER_SETTLE_SECONDS = 0.25
OUTPUT_LIMIT = 4096
HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RETRYABLE_PACKAGE_ACQUISITION_ERRORS = frozenset({
    "COS_DOWNLOAD_FAILED",
    "COS_GRANT_MISSING",
    "COS_RESPONSE_INVALID",
})


class McuUpdateError(RuntimeError):
    """A stable, non-secret error suitable for the persistent journal."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = _symbol(code)


@dataclass(frozen=True)
class StagedFirmwarePackage:
    verified: VerifiedFirmwarePackage
    package_path: Path
    image_path: Path


def _symbol(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in str(value).upper()
    ).strip("_")
    return (normalized or "MCU_UPDATE_FAILED")[:64]


def _safe_message(value: object) -> str:
    text = " ".join(str(value).replace("\x00", "").split())
    return text[:512] or "MCU firmware update failed"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if not hasattr(os, "fchmod"):
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def load_release_public_keys(
    directory: Path,
) -> dict[str, Ed25519PublicKey]:
    """Load ``KEY_ID.pem`` files from a non-writable deployment directory."""
    directory = directory.resolve()
    if not directory.is_dir():
        raise McuUpdateError(
            "SIGNING_KEYS_UNAVAILABLE",
            "MCU release public-key directory is missing",
        )
    if os.name == "posix" and directory.stat().st_mode & (
        stat.S_IWGRP | stat.S_IWOTH
    ):
        raise McuUpdateError(
            "SIGNING_KEYS_INSECURE",
            "MCU release public-key directory is group/world writable",
        )
    keys: dict[str, Ed25519PublicKey] = {}
    for path in sorted(directory.glob("*.pem")):
        if path.is_symlink() or not path.is_file():
            raise McuUpdateError(
                "SIGNING_KEYS_INSECURE",
                "MCU release public-key entries must be regular files",
            )
        key_id = path.stem
        if not KEY_ID_PATTERN.fullmatch(key_id) or key_id in keys:
            raise McuUpdateError(
                "SIGNING_KEY_ID_INVALID",
                "MCU release public-key filename is invalid or duplicated",
            )
        if os.name == "posix" and path.stat().st_mode & (
            stat.S_IWGRP | stat.S_IWOTH
        ):
            raise McuUpdateError(
                "SIGNING_KEYS_INSECURE",
                "MCU release public-key file is group/world writable",
            )
        try:
            keys[key_id] = load_public_key(path)
        except (OSError, ValueError, TypeError) as error:
            raise McuUpdateError(
                "SIGNING_KEY_INVALID",
                "MCU release public key is not valid Ed25519 PEM",
            ) from error
    if not keys:
        raise McuUpdateError(
            "SIGNING_KEYS_UNAVAILABLE",
            "no MCU release public keys are installed",
        )
    return keys


class FirmwarePackageCache:
    """Verify packages and materialize immutable package/image cache entries."""

    def __init__(
        self,
        cache_directory: Path,
        public_keys: Mapping[str, Ed25519PublicKey],
        hardware_compatibility: str,
    ):
        if not hardware_compatibility:
            raise ValueError("hardware compatibility must not be empty")
        self.root = cache_directory.resolve()
        self.packages = self.root / "packages"
        self.images = self.root / "images"
        self.incoming = self.root / "incoming"
        self.public_keys = dict(public_keys)
        self.hardware_compatibility = hardware_compatibility
        for directory in (self.root, self.packages, self.images, self.incoming):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix":
                os.chmod(directory, 0o700)

    def stage(self, source_path: Path) -> StagedFirmwarePackage:
        source_path = source_path.resolve(strict=True)
        try:
            verified = verify_package(
                source_path,
                self.public_keys,
                expected_hardware_compatibility=self.hardware_compatibility,
            )
        except (OSError, FirmwarePackageError) as error:
            raise McuUpdateError("PACKAGE_INVALID", _safe_message(error)) from error
        package_path = self.packages / f"{verified.package_sha256}.efw"
        image_sha256 = verified.manifest["imageSha256"]
        image_path = self.images / f"{image_sha256}.bin"
        _atomic_write(package_path, source_path.read_bytes())
        _atomic_write(image_path, verified.image)
        self._assert_cached_hash(package_path, verified.package_sha256)
        self._assert_cached_hash(image_path, image_sha256)
        return StagedFirmwarePackage(verified, package_path, image_path)

    def load(
        self,
        package_path: Path,
        expected_package_sha256: str,
        expected_manifest: dict,
    ) -> StagedFirmwarePackage:
        package_path = package_path.resolve(strict=True)
        if not HEX_64_PATTERN.fullmatch(expected_package_sha256):
            raise McuUpdateError(
                "PACKAGE_DIGEST_INVALID",
                "persisted MCU package digest is invalid",
            )
        self._assert_cached_hash(package_path, expected_package_sha256)
        try:
            verified = verify_package(
                package_path,
                self.public_keys,
                expected_hardware_compatibility=self.hardware_compatibility,
            )
        except (OSError, FirmwarePackageError) as error:
            raise McuUpdateError(
                "PACKAGE_CACHE_INVALID",
                _safe_message(error),
            ) from error
        if verified.manifest != expected_manifest:
            raise McuUpdateError(
                "PACKAGE_MANIFEST_CHANGED",
                "cached MCU package manifest differs from the update journal",
            )
        image_path = self.images / f"{verified.manifest['imageSha256']}.bin"
        if not image_path.exists():
            _atomic_write(image_path, verified.image)
        self._assert_cached_hash(image_path, verified.manifest["imageSha256"])
        return StagedFirmwarePackage(verified, package_path, image_path)

    @staticmethod
    def _assert_cached_hash(path: Path, expected_sha256: str) -> None:
        if _file_sha256(path) != expected_sha256:
            raise McuUpdateError(
                "PACKAGE_CACHE_DIGEST_MISMATCH",
                "cached MCU firmware bytes failed SHA-256 verification",
            )


class CosFirmwareDownloader:
    """Download one private firmware object with an execution-only STS grant."""

    def __init__(
        self,
        timeout_seconds: int = 30,
        client_factory: Optional[Callable[[dict], object]] = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("COS firmware timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory

    def download(
        self,
        *,
        grant: dict[str, Any],
        object_key: str,
        expected_sha256: str,
        destination_directory: Path,
        expected_size: Optional[int] = None,
    ) -> Path:
        if not isinstance(grant, dict):
            raise McuUpdateError("COS_GRANT_MISSING", "firmware COS grant is missing")
        if (
            not isinstance(object_key, str)
            or not object_key
            or object_key.startswith("/")
            or ".." in object_key.split("/")
            or not object_key.startswith(str(grant.get("keyPrefix") or ""))
        ):
            raise McuUpdateError(
                "COS_OBJECT_KEY_INVALID",
                "firmware COS object key is outside the granted prefix",
            )
        if not HEX_64_PATTERN.fullmatch(str(expected_sha256)):
            raise McuUpdateError(
                "PACKAGE_DIGEST_INVALID",
                "expected MCU package SHA-256 is invalid",
            )
        if expected_size is not None and (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or not 1 <= expected_size <= MAX_PACKAGE_SIZE
        ):
            raise McuUpdateError(
                "PACKAGE_SIZE_INVALID",
                "expected MCU package size is invalid",
            )
        destination_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(
            prefix="mcu-firmware-",
            suffix=".efw",
            dir=destination_directory,
        )
        path = Path(temporary)
        digest = hashlib.sha256()
        size = 0
        try:
            client = (
                self._client_factory(grant)
                if self._client_factory is not None
                else self._client(grant)
            )
            response = client.get_object(
                Bucket=grant["bucket"],
                Key=object_key,
            )
            body = response.get("Body") if isinstance(response, dict) else None
            stream = (
                body.get_raw_stream()
                if hasattr(body, "get_raw_stream")
                else body
            )
            if stream is None or not hasattr(stream, "read"):
                raise McuUpdateError(
                    "COS_RESPONSE_INVALID",
                    "firmware COS response body is unreadable",
                )
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as target:
                fd = -1
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_PACKAGE_SIZE:
                        raise McuUpdateError(
                            "PACKAGE_TOO_LARGE",
                            "downloaded MCU package exceeds 128 KiB",
                        )
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if expected_size is not None and size != expected_size:
                raise McuUpdateError(
                    "PACKAGE_SIZE_MISMATCH",
                    "downloaded MCU package size differs from deployment",
                )
            if digest.hexdigest() != expected_sha256:
                raise McuUpdateError(
                    "PACKAGE_DIGEST_MISMATCH",
                    "downloaded MCU package SHA-256 differs from deployment",
                )
            return path
        except McuUpdateError:
            raise
        except Exception as error:
            raise McuUpdateError(
                "COS_DOWNLOAD_FAILED",
                f"firmware COS download failed: {type(error).__name__}",
            ) from error
        finally:
            if fd >= 0:
                os.close(fd)
            if path.exists() and (
                size == 0
                or digest.hexdigest() != str(expected_sha256)
                or (expected_size is not None and size != expected_size)
            ):
                path.unlink()

    def _client(self, grant: dict[str, Any]):
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError as error:
            raise McuUpdateError(
                "COS_SDK_NOT_INSTALLED",
                "COS SDK is unavailable for MCU firmware download",
            ) from error
        config = CosConfig(
            Region=grant["region"],
            SecretId=grant["tmpSecretId"],
            SecretKey=grant["tmpSecretKey"],
            Token="".join(grant["sessionTokenParts"]),
            Scheme="https",
            Timeout=self.timeout_seconds,
        )
        return CosS3Client(config)


class WiringOpBootControl:
    """Drive MCU BOOT0 and NRST through WiringOP's ``gpio`` command."""

    def __init__(
        self,
        *,
        gpio_path: str,
        boot0_wpi: int,
        reset_wpi: int,
        boot0_active_level: int = 1,
        reset_active_level: int = 0,
        command_runner: Callable[..., object] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not os.path.isabs(gpio_path):
            raise ValueError("WiringOP gpio path must be absolute")
        if boot0_wpi < 0 or reset_wpi < 0 or boot0_wpi == reset_wpi:
            raise ValueError("BOOT0 and NRST WiringOP pins are invalid")
        if boot0_active_level not in {0, 1} or reset_active_level not in {0, 1}:
            raise ValueError("GPIO active levels must be 0 or 1")
        self.gpio_path = gpio_path
        self.boot0_wpi = boot0_wpi
        self.reset_wpi = reset_wpi
        self.boot0_active_level = boot0_active_level
        self.reset_active_level = reset_active_level
        self._run_command = command_runner
        self._sleep = sleeper

    def enter_system_bootloader(self) -> None:
        self._configure_outputs()
        self._write(self.boot0_wpi, self.boot0_active_level)
        self._pulse_reset()
        self._sleep(BOOTLOADER_SETTLE_SECONDS)

    def boot_application(self) -> None:
        self._configure_outputs()
        self._write(self.boot0_wpi, 1 - self.boot0_active_level)
        self._pulse_reset()
        self._sleep(APPLICATION_SETTLE_SECONDS)

    def force_application_selection(self) -> None:
        """Leave BOOT0 inactive without resetting a verified application."""
        self._configure_outputs()
        self._write(self.boot0_wpi, 1 - self.boot0_active_level)
        self._write(self.reset_wpi, 1 - self.reset_active_level)

    def _configure_outputs(self) -> None:
        self._command("mode", str(self.boot0_wpi), "out")
        self._command("mode", str(self.reset_wpi), "out")

    def _pulse_reset(self) -> None:
        self._write(self.reset_wpi, self.reset_active_level)
        self._sleep(RESET_ASSERT_SECONDS)
        self._write(self.reset_wpi, 1 - self.reset_active_level)

    def _write(self, pin: int, level: int) -> None:
        self._command("write", str(pin), str(level))

    def _command(self, *arguments: str) -> None:
        argv = [self.gpio_path, *arguments]
        try:
            result = self._run_command(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception as error:
            raise McuUpdateError(
                "GPIO_CONTROL_FAILED",
                f"WiringOP GPIO execution failed: {type(error).__name__}",
            ) from error
        if int(getattr(result, "returncode", 1)) != 0:
            raise McuUpdateError(
                "GPIO_CONTROL_FAILED",
                "WiringOP GPIO command returned a failure",
            )


class Stm32FlashRunner:
    """Invoke stm32flash without a shell and with bounded, sanitized output."""

    def __init__(
        self,
        *,
        executable_path: str,
        serial_port: str,
        command_runner: Callable[..., object] = subprocess.run,
        timeout_seconds: int = STM32FLASH_TIMEOUT_SECONDS,
    ):
        if not os.path.isabs(executable_path):
            raise ValueError("stm32flash path must be absolute")
        if not os.path.isabs(serial_port):
            raise ValueError("STM32 serial port must be absolute")
        self.executable_path = executable_path
        self.serial_port = serial_port
        self._run_command = command_runner
        self.timeout_seconds = timeout_seconds

    def flash(self, image_path: Path, image_size: int) -> dict:
        image_path = image_path.resolve(strict=True)
        if image_path.stat().st_size != image_size or image_size <= 0:
            raise McuUpdateError(
                "IMAGE_SIZE_MISMATCH",
                "cached MCU image size differs from manifest",
            )
        argv = [
            self.executable_path,
            "-b",
            str(STM32_BOOTLOADER_BAUDRATE),
            "-m",
            STM32_BOOTLOADER_SERIAL_MODE,
            "-f",
            "-S",
            f"0x{FLASH_BASE:08x}:{image_size}",
            "-w",
            str(image_path),
            "-v",
            "-n",
            str(STM32FLASH_INTERNAL_RETRIES),
            self.serial_port,
        ]
        try:
            result = self._run_command(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise McuUpdateError(
                "STM32FLASH_TIMEOUT",
                "stm32flash exceeded its execution deadline",
            ) from error
        except Exception as error:
            raise McuUpdateError(
                "STM32FLASH_EXEC_FAILED",
                f"stm32flash could not execute: {type(error).__name__}",
            ) from error
        output = self._sanitize_output(getattr(result, "stdout", ""))
        if int(getattr(result, "returncode", 1)) != 0:
            message = "stm32flash write/verify failed"
            if output:
                message = f"{message}: {output[-1024:]}"
            raise McuUpdateError("STM32FLASH_FAILED", message)
        return {
            "returnCode": 0,
            "output": output,
            "imageSize": image_size,
        }

    @staticmethod
    def _sanitize_output(value: object) -> str:
        text = str(value or "")
        printable = "".join(
            character
            for character in text
            if character in "\r\n\t" or 0x20 <= ord(character) <= 0x7E
        )
        return printable[-OUTPUT_LIMIT:]


class McuFirmwareUpdater:
    """Orchestrate one exclusive target update and automatic rollback."""

    def __init__(
        self,
        *,
        store,
        uart_link,
        package_cache: FirmwarePackageCache,
        boot_control: WiringOpBootControl,
        flash_runner: Stm32FlashRunner,
        downloader: Optional[CosFirmwareDownloader] = None,
        enabled: bool = True,
        device_name: Optional[str] = None,
    ):
        self.store = store
        self.uart = uart_link
        self.cache = package_cache
        self.boot = boot_control
        self.flash_runner = flash_runner
        self.downloader = downloader or CosFirmwareDownloader()
        self.enabled = bool(enabled)
        self.device_name = device_name
        self._execution_lock = threading.Lock()

    def queue_local(
        self,
        package_path: Path,
        *,
        deployment_uid: Optional[str] = None,
        legacy_preflight: bool = False,
        allow_downgrade: bool = False,
        requested_reason: Optional[str] = None,
    ) -> dict:
        self._require_enabled()
        staged = self.cache.stage(package_path)
        return self._queue_staged(
            staged,
            deployment_uid=deployment_uid or str(uuid.uuid4()),
            command_uid=None,
            source="LOCAL",
            legacy_preflight=legacy_preflight,
            allow_downgrade=allow_downgrade,
            requested_reason=requested_reason,
        )

    def queue_cloud(
        self,
        *,
        deployment_uid: str,
        command_uid: str,
        object_key: str,
        package_sha256: str,
        package_size: int,
        cos_grant: dict,
        release_uid: str,
        firmware_version: str,
        firmware_version_code: int,
        firmware_identity_hex: str,
        requested_reason: Optional[str] = None,
    ) -> dict:
        if not self.device_name:
            raise McuUpdateError(
                "DEVICE_NAME_UNAVAILABLE",
                "cloud MCU updates require a device name for reliable progress",
            )
        provisional_manifest = {
            "releaseUid": release_uid,
            "hardwareCompatibility": self.cache.hardware_compatibility,
            "firmwareVersion": firmware_version,
            "firmwareVersionCode": firmware_version_code,
            "firmwareIdentityHex": firmware_identity_hex,
            "fixedFrameRevision": 2,
        }
        update_uid = str(uuid.uuid4())
        if not self.enabled:
            error = McuUpdateError(
                "MCU_UPDATE_DISABLED",
                "MCU firmware update is disabled on this edge device",
            )
            rejected = self.store.reject_mcu_firmware_update_before_start(
                update_uid=update_uid,
                deployment_uid=deployment_uid,
                command_uid=command_uid,
                package_sha256=package_sha256,
                manifest=provisional_manifest,
                error_code=error.code,
                error_message=_safe_message(error),
                device_name=self.device_name,
                requested_reason=requested_reason,
            )
            if rejected not in {"ACCEPTED", "DUPLICATE"}:
                raise McuUpdateError(
                    "UPDATE_REJECTION_CONFLICT",
                    "disabled MCU update rejection conflicts with journal",
                )
            raise error
        disposition = self.store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=deployment_uid,
            command_uid=command_uid,
            source="CLOUD",
            package_path=str(
                (self.cache.packages / f"{package_sha256}.efw").resolve()
            ),
            package_sha256=package_sha256,
            manifest=provisional_manifest,
            legacy_preflight=False,
            allow_downgrade=False,
            requested_reason=requested_reason,
            package_ready=False,
            device_name=self.device_name,
        )
        if disposition != "ACCEPTED":
            existing = self.store.get_mcu_firmware_update_by_deployment(
                deployment_uid
            )
            if existing is not None and self._same_cloud_request(
                existing,
                command_uid=command_uid,
                package_sha256=package_sha256,
                expected_identity=provisional_manifest,
            ):
                update_uid = existing["update_uid"]
                if existing["package_ready"]:
                    return self._duplicate_result(existing)
                if existing["state"] not in {
                    "QUEUED",
                    "PACKAGE_FETCH_FAILED",
                }:
                    return self._duplicate_result(existing)
            else:
                code = {
                    "CONFLICT": "DEPLOYMENT_CONFLICT",
                    "MAINTENANCE_BUSY": "MAINTENANCE_BUSY",
                    "WORK_BUSY": "PHYSICAL_WORK_BUSY",
                    "COMMAND_BUSY": "PHYSICAL_COMMAND_BUSY",
                }.get(disposition, "UPDATE_QUEUE_REJECTED")
                error = McuUpdateError(
                    code,
                    f"MCU update queue rejected: {disposition}",
                )
                if disposition in {
                    "MAINTENANCE_BUSY",
                    "WORK_BUSY",
                    "COMMAND_BUSY",
                }:
                    rejected = (
                        self.store.reject_mcu_firmware_update_before_start(
                            update_uid=update_uid,
                            deployment_uid=deployment_uid,
                            command_uid=command_uid,
                            package_sha256=package_sha256,
                            manifest=provisional_manifest,
                            error_code=error.code,
                            error_message=_safe_message(error),
                            device_name=self.device_name,
                            requested_reason=requested_reason,
                        )
                    )
                    if rejected not in {"ACCEPTED", "DUPLICATE"}:
                        raise McuUpdateError(
                            "UPDATE_REJECTION_CONFLICT",
                            "MCU queue rejection could not be journaled",
                        )
                raise error

        acquisition_attempt = (
            self.store.record_mcu_firmware_package_acquisition_attempt(
                update_uid,
                attempt_limit=PACKAGE_ACQUISITION_ATTEMPT_LIMIT,
            )
        )
        if acquisition_attempt is None:
            exhausted = McuUpdateError(
                "PACKAGE_FETCH_RETRY_EXHAUSTED",
                "MCU package acquisition retry limit is exhausted",
            )
            self.store.reject_mcu_firmware_update(
                update_uid,
                exhausted.code,
                _safe_message(exhausted),
                device_name=self.device_name,
            )
            raise exhausted

        try:
            temporary = self.downloader.download(
                grant=cos_grant,
                object_key=object_key,
                expected_sha256=package_sha256,
                expected_size=package_size,
                destination_directory=self.cache.incoming,
            )
            try:
                staged = self.cache.stage(temporary)
            finally:
                if temporary.exists():
                    temporary.unlink()
            if staged.verified.package_sha256 != package_sha256:
                raise McuUpdateError(
                    "PACKAGE_DIGEST_MISMATCH",
                    "verified MCU package differs from deployment digest",
                )
            actual_identity = {
                key: staged.verified.manifest.get(key)
                for key in provisional_manifest
            }
            if actual_identity != provisional_manifest:
                raise McuUpdateError(
                    "PACKAGE_IDENTITY_MISMATCH",
                    "signed MCU manifest identity differs from deployment command",
                )
            attached = self.store.attach_mcu_firmware_package(
                update_uid,
                package_path=str(staged.package_path),
                package_sha256=staged.verified.package_sha256,
                manifest=staged.verified.manifest,
                device_name=self.device_name,
            )
            if attached not in {"ACCEPTED", "DUPLICATE"}:
                raise McuUpdateError(
                    "PACKAGE_ATTACH_CONFLICT",
                    f"verified MCU package could not be attached: {attached}",
                )
        except McuUpdateError as error:
            if (
                error.code in RETRYABLE_PACKAGE_ACQUISITION_ERRORS
                and acquisition_attempt
                < PACKAGE_ACQUISITION_ATTEMPT_LIMIT
            ):
                self.store.fail_mcu_firmware_package_acquisition(
                    update_uid,
                    error.code,
                    _safe_message(error),
                    device_name=self.device_name,
                )
            else:
                self.store.reject_mcu_firmware_update(
                    update_uid,
                    error.code,
                    _safe_message(error),
                    device_name=self.device_name,
                )
            raise
        except Exception as error:
            acquisition_error = McuUpdateError(
                "PACKAGE_ACQUISITION_FAILED",
                "unexpected failure while acquiring the MCU firmware package: "
                f"{type(error).__name__}",
            )
            self.store.reject_mcu_firmware_update(
                update_uid,
                acquisition_error.code,
                _safe_message(acquisition_error),
                device_name=self.device_name,
            )
            raise acquisition_error from error

        update = self.store.get_mcu_firmware_update(update_uid)
        return {
            "disposition": "QUEUED",
            "updateUid": update_uid,
            "deploymentUid": deployment_uid,
            "state": "QUEUED",
            "manifest": update["manifest"],
        }

    @staticmethod
    def _same_cloud_request(
        update: dict,
        *,
        command_uid: str,
        package_sha256: str,
        expected_identity: dict,
    ) -> bool:
        return (
            update["source"] == "CLOUD"
            and update["command_uid"] == command_uid
            and update["package_sha256"] == package_sha256
            and all(
                update["manifest"].get(key) == value
                for key, value in expected_identity.items()
            )
        )

    @staticmethod
    def _duplicate_result(update: dict) -> dict:
        return {
            "disposition": "DUPLICATE",
            "updateUid": update["update_uid"],
            "deploymentUid": update["deployment_uid"],
            "state": update["state"],
            "manifest": update["manifest"],
        }

    def _queue_staged(
        self,
        staged: StagedFirmwarePackage,
        *,
        deployment_uid: str,
        command_uid: Optional[str],
        source: str,
        legacy_preflight: bool,
        allow_downgrade: bool,
        requested_reason: Optional[str],
    ) -> dict:
        update_uid = str(uuid.uuid4())
        disposition = self.store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=deployment_uid,
            command_uid=command_uid,
            source=source,
            package_path=str(staged.package_path),
            package_sha256=staged.verified.package_sha256,
            manifest=staged.verified.manifest,
            legacy_preflight=legacy_preflight,
            allow_downgrade=allow_downgrade,
            requested_reason=requested_reason,
            package_ready=True,
            device_name=self.device_name,
        )
        if disposition == "DUPLICATE":
            existing = self.store.get_mcu_firmware_update_by_deployment(
                deployment_uid
            )
            return {
                "disposition": "DUPLICATE",
                "updateUid": existing["update_uid"],
                "deploymentUid": deployment_uid,
                "state": existing["state"],
                "manifest": existing["manifest"],
            }
        if disposition != "ACCEPTED":
            code = {
                "CONFLICT": "DEPLOYMENT_CONFLICT",
                "MAINTENANCE_BUSY": "MAINTENANCE_BUSY",
                "WORK_BUSY": "PHYSICAL_WORK_BUSY",
                "COMMAND_BUSY": "PHYSICAL_COMMAND_BUSY",
            }.get(disposition, "UPDATE_QUEUE_REJECTED")
            raise McuUpdateError(code, f"MCU update queue rejected: {disposition}")
        update = self.store.get_mcu_firmware_update(update_uid)
        return {
            "disposition": "QUEUED",
            "updateUid": update_uid,
            "deploymentUid": deployment_uid,
            "state": "QUEUED",
            "manifest": staged.verified.manifest,
        }

    def process_active(self) -> bool:
        """Run or resume the journal owner. Returns whether work was attempted."""
        if not self._execution_lock.acquire(blocking=False):
            return False
        try:
            update = self.store.get_active_mcu_firmware_update()
            if update is None:
                return False
            if not self.enabled:
                logger.error(
                    "MCU update %s remains locked because updater is disabled",
                    update["update_uid"],
                )
                return False
            if update["state"] == "FAILED_LOCKED":
                return False
            if not update["package_ready"]:
                if update["state"] != "PACKAGE_FETCH_FAILED":
                    self.store.fail_mcu_firmware_package_acquisition(
                        update["update_uid"],
                        "PACKAGE_ACQUISITION_INTERRUPTED",
                        "edge restarted before the MCU package was cached",
                        device_name=self.device_name,
                    )
                    return True
                return False
            try:
                self._execute(update)
            except Exception as error:
                self._handle_unexpected_failure(update, error)
            return True
        finally:
            self._execution_lock.release()

    def _handle_unexpected_failure(
        self,
        update: dict,
        cause: Exception,
    ) -> None:
        """Convert an internal defect into a deterministic safe journal state."""
        logger.exception(
            "unexpected MCU updater failure: update=%s type=%s",
            update["update_uid"],
            type(cause).__name__,
        )
        error = McuUpdateError(
            "MCU_UPDATE_INTERNAL_ERROR",
            f"unexpected updater failure: {type(cause).__name__}",
        )
        try:
            current = self.store.get_mcu_firmware_update(
                update["update_uid"]
            )
            if current is None:
                logger.critical(
                    "MCU updater journal disappeared after an internal failure: %s",
                    update["update_uid"],
                )
                return
            state = current["state"]
            no_flash_attempt = (
                current["target_attempt_count"] == 0
                and current["rollback_attempt_count"] == 0
            )
            if state in {"QUEUED", "PREFLIGHT", "PREPARED"} and no_flash_attempt:
                self._reject_preflight_failure_safely(current, error)
                return
            if state in {"ROLLING_BACK", "VERIFYING_ROLLBACK"}:
                self._lock_failure(current, error)
                return
            self._rollback_or_lock(current, error)
        except Exception as recovery_error:
            logger.exception(
                "unexpected MCU updater recovery failure: update=%s type=%s",
                update["update_uid"],
                type(recovery_error).__name__,
            )
            try:
                current = self.store.get_mcu_firmware_update(
                    update["update_uid"]
                )
                if current is not None and current["state"] not in {
                    "SUCCEEDED",
                    "ROLLED_BACK",
                    "REJECTED",
                    "FAILED_LOCKED",
                }:
                    self._lock_failure(
                        current,
                        McuUpdateError(
                            "MCU_UPDATE_RECOVERY_INTERNAL_ERROR",
                            "unexpected updater recovery failure",
                        ),
                    )
            except Exception:
                # The existing SQLite maintenance row remains fail-closed even
                # if the journal or GPIO boundary itself is unavailable.
                logger.critical(
                    "MCU updater could not persist its fail-closed state: %s",
                    update["update_uid"],
                    exc_info=True,
                )

    def _execute(self, update: dict) -> None:
        state = update["state"]
        if state in {"ROLLING_BACK", "VERIFYING_ROLLBACK"}:
            self._resume_rollback(update)
            return

        try:
            target = self.cache.load(
                Path(update["package_path"]),
                update["package_sha256"],
                update["manifest"],
            )
        except McuUpdateError as error:
            if state in {"QUEUED", "PREFLIGHT", "PREPARED"}:
                self._reject_preflight_failure_safely(update, error)
            else:
                self._rollback_or_lock(update, error)
            return

        if state == "VERIFYING_TARGET":
            try:
                self._verify_application(target.verified.manifest)
                self._complete_target(update)
                return
            except McuUpdateError as error:
                self._record_nonterminal_error(update, error)
                update = self.store.get_mcu_firmware_update(
                    update["update_uid"]
                )
        elif state == "FLASHING_TARGET" and update["target_attempt_count"] > 0:
            # A process may have died after stm32flash finished but before the
            # VERIFYING_TARGET commit. Try the application once before erasing.
            try:
                self._verify_application(target.verified.manifest)
                self._complete_target(update)
                return
            except McuUpdateError:
                pass
        else:
            try:
                self._preflight(update, target.verified.manifest)
            except McuUpdateError as error:
                current = self.store.get_mcu_firmware_update(
                    update["update_uid"]
                )
                if current is not None and current["state"] == "FAILED_LOCKED":
                    return
                self._reject_preflight_failure_safely(
                    current or update,
                    error,
                )
                return
            update = self.store.get_mcu_firmware_update(update["update_uid"])

        self._attempt_target(update, target)

    def _preflight(self, update: dict, target_manifest: dict) -> None:
        self.store.transition_mcu_firmware_update(
            update["update_uid"],
            "PREFLIGHT",
            device_name=self.device_name,
        )
        update = self.store.get_mcu_firmware_update(update["update_uid"])
        if self.store.get_work_slot() is not None:
            raise McuUpdateError(
                "PHYSICAL_WORK_BUSY",
                "physical work became active before MCU update preflight",
            )

        stable = self.store.get_mcu_firmware_state()
        stable_manifest = stable.get("current_manifest")
        stable_version_code = (
            stable_manifest.get("firmwareVersionCode")
            if stable_manifest
            else None
        )
        if update["legacy_preflight"]:
            if update["source"] != "LOCAL":
                raise McuUpdateError(
                    "LEGACY_PREFLIGHT_FORBIDDEN",
                    "legacy preflight is allowed only from local SSH",
                )
            self._enforce_version_policy(
                update,
                target_manifest,
                stable_version_code,
            )
        else:
            if not self.uart.is_open and not self.uart.open():
                raise McuUpdateError(
                    "UART_OPEN_FAILED",
                    "application UART could not open for update preflight",
                )
            identity = self.uart.query_firmware_identity()
            if not self._identity_response_ok(identity):
                raise McuUpdateError(
                    "FIRMWARE_IDENTITY_UNAVAILABLE",
                    "MCU did not return a valid revision-2 identity snapshot",
                )
            self._enforce_version_policy(
                update,
                target_manifest,
                identity.get("firmwareVersionCode"),
            )
            try:
                recovery_armed = self.store.arm_mcu_firmware_prepare_recovery(
                    update["update_uid"],
                    identity,
                )
            except Exception as cause:
                raise McuUpdateError(
                    "MCU_PREPARE_JOURNAL_FAILED",
                    "could not persist the MCU prepare recovery identity",
                ) from cause
            if not recovery_armed:
                raise McuUpdateError(
                    "MCU_PREPARE_JOURNAL_FAILED",
                    "could not persist the MCU prepare recovery identity",
                )
            try:
                execution = self.uart.execute_firmware_update_prepare()
            except Exception as cause:
                raise McuUpdateError(
                    "MCU_PREPARE_EXECUTION_UNCONFIRMED",
                    "MCU update preparation execution raised an internal error",
                ) from cause
            if not execution.get("executed"):
                confirmed_response = (
                    execution.get("queryStatus") == "OK"
                    and execution.get("statusCode") is not None
                )
                error = McuUpdateError(
                    (
                        "MCU_PREPARE_EXECUTION_FAILED"
                        if confirmed_response
                        else "MCU_PREPARE_EXECUTION_UNCONFIRMED"
                    ),
                    "MCU did not confirm update preparation execution",
                )
                raise error
            try:
                prepared = self.store.transition_mcu_firmware_update(
                    update["update_uid"],
                    "PREPARED",
                    device_name=self.device_name,
                )
                if not prepared:
                    raise RuntimeError("PREPARED journal transition was lost")
            except Exception as cause:
                raise McuUpdateError(
                    "MCU_PREPARED_JOURNAL_FAILED",
                    "MCU stopped outputs but PREPARED could not be journaled",
                ) from cause
        if update["legacy_preflight"]:
            self.store.transition_mcu_firmware_update(
                update["update_uid"],
                "PREPARED",
                device_name=self.device_name,
            )

    def _recover_after_prepare_failure(
        self,
        update: dict,
        expected_identity: dict,
        execution_error: McuUpdateError,
    ) -> None:
        """Clear a possibly latched MCU before releasing Edge maintenance.

        An F2 execution response can be lost after the MCU has already stopped
        its outputs and latched maintenance mode.  The Edge must therefore
        reset and re-prove the unchanged application before a pre-flash
        rejection is allowed to release the local maintenance lock.
        """
        try:
            self.uart.close()
            self.boot.boot_application()
            if not self.uart.open():
                raise McuUpdateError(
                    "MCU_PREPARE_RECOVERY_FAILED",
                    "MCU application UART did not reopen after prepare failure",
                )
            recovered = self.uart.query_firmware_identity()
            expected = {
                key: expected_identity.get(key)
                for key in (
                    "protocolRevision",
                    "firmwareVersionCode",
                    "firmwareVersion",
                    "firmwareIdentityHex",
                )
            }
            actual = {key: recovered.get(key) for key in expected}
            if not self._identity_response_ok(recovered) or actual != expected:
                raise McuUpdateError(
                    "MCU_PREPARE_RECOVERY_FAILED",
                    "MCU application identity did not recover after prepare failure",
                )
            if not self._self_test_response_ok(self.uart.query_self_test()):
                raise McuUpdateError(
                    "MCU_PREPARE_RECOVERY_FAILED",
                    "MCU self-test did not recover after prepare failure",
                )
            self.boot.force_application_selection()
        except Exception as recovery_cause:
            recovery_error = (
                recovery_cause
                if isinstance(recovery_cause, McuUpdateError)
                else McuUpdateError(
                    "MCU_PREPARE_RECOVERY_FAILED",
                    "unexpected MCU application recovery failure",
                )
            )
            combined = McuUpdateError(
                "MCU_PREPARE_RECOVERY_FAILED",
                f"{execution_error.code}; {recovery_error.code}",
            )
            self._lock_failure(update, combined)
            raise combined from recovery_cause

    def _reject_preflight_failure_safely(
        self,
        update: dict,
        error: McuUpdateError,
    ) -> None:
        """Recover a possibly F2-latched application before unlocking."""

        if update.get("prepare_recovery_required"):
            expected_identity = update.get("prepare_identity")
            if not isinstance(expected_identity, dict):
                self._lock_failure(
                    update,
                    McuUpdateError(
                        "MCU_PREPARE_RECOVERY_FAILED",
                        "persisted MCU prepare identity is unavailable",
                    ),
                )
                return
            try:
                self._recover_after_prepare_failure(
                    update,
                    expected_identity,
                    error,
                )
            except McuUpdateError:
                # Recovery already persisted FAILED_LOCKED.
                return
        self._reject(update, error)

    @staticmethod
    def _enforce_version_policy(
        update: dict,
        target_manifest: dict,
        current_version_code: Optional[int],
    ) -> None:
        if current_version_code is None:
            return
        target = int(target_manifest["firmwareVersionCode"])
        current = int(current_version_code)
        if target >= current:
            return
        if update["source"] == "LOCAL" and update["allow_downgrade"]:
            return
        raise McuUpdateError(
            "DOWNGRADE_BLOCKED",
            f"MCU firmware downgrade is blocked: current={current} target={target}",
        )

    def _attempt_target(
        self,
        update: dict,
        target: StagedFirmwarePackage,
    ) -> None:
        last_error = McuUpdateError(
            "TARGET_ATTEMPTS_EXHAUSTED",
            "target firmware attempts were exhausted",
        )
        while update["target_attempt_count"] < TARGET_ATTEMPT_LIMIT:
            attempt = self.store.record_mcu_firmware_attempt(
                update["update_uid"],
                rollback=False,
                device_name=self.device_name,
            )
            update = self.store.get_mcu_firmware_update(update["update_uid"])
            try:
                self._flash(target)
                self.store.transition_mcu_firmware_update(
                    update["update_uid"],
                    "VERIFYING_TARGET",
                    device_name=self.device_name,
                )
                update = self.store.get_mcu_firmware_update(
                    update["update_uid"]
                )
                self._verify_application(target.verified.manifest)
                self._complete_target(update)
                return
            except McuUpdateError as error:
                last_error = error
                logger.warning(
                    "MCU target attempt %d/%d failed: %s",
                    attempt,
                    TARGET_ATTEMPT_LIMIT,
                    error.code,
                )
                self._record_nonterminal_error(update, error)
                update = self.store.get_mcu_firmware_update(
                    update["update_uid"]
                )
        self._rollback_or_lock(update, last_error)

    def _resume_rollback(self, update: dict) -> None:
        try:
            rollback = self._load_rollback(update)
        except McuUpdateError as error:
            self._lock_failure(update, error)
            return
        if update["rollback_attempt_count"] > 0:
            try:
                self._verify_application(rollback.verified.manifest)
                self._complete_rollback(update)
                return
            except McuUpdateError:
                pass
        self._attempt_rollback(update, rollback)

    def _rollback_or_lock(self, update: dict, target_error: McuUpdateError) -> None:
        try:
            rollback = self._load_rollback(update)
        except McuUpdateError as rollback_error:
            combined = McuUpdateError(
                "ROLLBACK_UNAVAILABLE",
                f"{target_error.code}; {rollback_error.code}",
            )
            self._lock_failure(update, combined)
            return
        self._record_nonterminal_error(update, target_error)
        self._attempt_rollback(update, rollback)

    def _load_rollback(self, update: dict) -> StagedFirmwarePackage:
        if (
            not update.get("previous_package_path")
            or not update.get("previous_package_sha256")
            or not update.get("previous_manifest")
        ):
            raise McuUpdateError(
                "NO_STABLE_ROLLBACK",
                "no previously verified stable MCU package is cached",
            )
        return self.cache.load(
            Path(update["previous_package_path"]),
            update["previous_package_sha256"],
            update["previous_manifest"],
        )

    def _attempt_rollback(
        self,
        update: dict,
        rollback: StagedFirmwarePackage,
    ) -> None:
        last_error = McuUpdateError(
            "ROLLBACK_ATTEMPTS_EXHAUSTED",
            "rollback attempts were exhausted",
        )
        while update["rollback_attempt_count"] < ROLLBACK_ATTEMPT_LIMIT:
            attempt = self.store.record_mcu_firmware_attempt(
                update["update_uid"],
                rollback=True,
                device_name=self.device_name,
            )
            update = self.store.get_mcu_firmware_update(update["update_uid"])
            try:
                self._flash(rollback)
                self.store.transition_mcu_firmware_update(
                    update["update_uid"],
                    "VERIFYING_ROLLBACK",
                    device_name=self.device_name,
                )
                update = self.store.get_mcu_firmware_update(
                    update["update_uid"]
                )
                self._verify_application(rollback.verified.manifest)
                self._complete_rollback(update)
                return
            except McuUpdateError as error:
                last_error = error
                logger.error(
                    "MCU rollback attempt %d/%d failed: %s",
                    attempt,
                    ROLLBACK_ATTEMPT_LIMIT,
                    error.code,
                )
                self._record_nonterminal_error(update, error)
                update = self.store.get_mcu_firmware_update(
                    update["update_uid"]
                )
        self._lock_failure(update, last_error)

    def _flash(self, package: StagedFirmwarePackage) -> None:
        manifest = package.verified.manifest
        if _file_sha256(package.image_path) != manifest["imageSha256"]:
            raise McuUpdateError(
                "IMAGE_CACHE_DIGEST_MISMATCH",
                "cached MCU image failed verification immediately before flash",
            )
        self.uart.close()
        self.boot.enter_system_bootloader()
        self.flash_runner.flash(
            package.image_path,
            int(manifest["imageSize"]),
        )

    def _verify_application(self, manifest: dict) -> None:
        self.uart.close()
        self.boot.boot_application()
        if not self.uart.open():
            raise McuUpdateError(
                "APPLICATION_UART_OPEN_FAILED",
                "MCU application UART did not reopen after reset",
            )
        identity = self.uart.query_firmware_identity()
        if not self._identity_response_ok(identity):
            raise McuUpdateError(
                "APPLICATION_IDENTITY_TIMEOUT",
                "flashed MCU application did not return a valid F3 identity",
            )
        expected = {
            "protocolRevision": int(manifest["fixedFrameRevision"]),
            "firmwareVersionCode": int(manifest["firmwareVersionCode"]),
            "firmwareVersion": manifest["firmwareVersion"],
            "firmwareIdentityHex": manifest["firmwareIdentityHex"],
        }
        actual = {key: identity.get(key) for key in expected}
        if actual != expected:
            raise McuUpdateError(
                "APPLICATION_IDENTITY_MISMATCH",
                "flashed MCU identity differs from the signed manifest",
            )
        self_test = self.uart.query_self_test()
        if not self._self_test_response_ok(self_test):
            raise McuUpdateError(
                "APPLICATION_SELF_TEST_FAILED",
                "flashed MCU application failed the F1 sensor self-test",
            )
        self.boot.force_application_selection()

    def _complete_target(self, update: dict) -> None:
        if not self.store.complete_mcu_firmware_update(
            update["update_uid"],
            device_name=self.device_name,
        ):
            raise McuUpdateError(
                "JOURNAL_COMMIT_FAILED",
                "target firmware verified but stable-state commit failed",
            )
        logger.info(
            "MCU firmware update succeeded: update=%s version=%s",
            update["update_uid"],
            update["manifest"]["firmwareVersion"],
        )

    def _complete_rollback(self, update: dict) -> None:
        if not self.store.complete_mcu_firmware_rollback(
            update["update_uid"],
            device_name=self.device_name,
        ):
            raise McuUpdateError(
                "JOURNAL_COMMIT_FAILED",
                "rollback verified but journal commit failed",
            )
        logger.error(
            "MCU target failed and previous stable firmware was restored: %s",
            update["update_uid"],
        )

    def _reject(self, update: dict, error: McuUpdateError) -> None:
        self.store.reject_mcu_firmware_update(
            update["update_uid"],
            error.code,
            _safe_message(error),
            device_name=self.device_name,
        )
        logger.warning(
            "MCU firmware update rejected before flash: update=%s code=%s",
            update["update_uid"],
            error.code,
        )

    def _lock_failure(self, update: dict, error: McuUpdateError) -> None:
        self.uart.close()
        try:
            self.boot.force_application_selection()
        except McuUpdateError:
            pass
        self.store.fail_mcu_firmware_update_locked(
            update["update_uid"],
            error.code,
            _safe_message(error),
            device_name=self.device_name,
        )
        logger.critical(
            "MCU update and rollback failed; business remains locked: "
            "update=%s code=%s",
            update["update_uid"],
            error.code,
        )

    def _record_nonterminal_error(
        self,
        update: dict,
        error: McuUpdateError,
    ) -> None:
        current = self.store.get_mcu_firmware_update(update["update_uid"])
        if current is None:
            return
        self.store.transition_mcu_firmware_update(
            update["update_uid"],
            current["state"],
            error_code=error.code,
            error_message=_safe_message(error),
        )

    @staticmethod
    def _identity_response_ok(identity: dict) -> bool:
        return (
            identity.get("queryStatus") == "OK"
            and identity.get("statusCode") == 0
        )

    @staticmethod
    def _self_test_response_ok(self_test: dict) -> bool:
        return (
            self_test.get("queryStatus") == "OK"
            and self_test.get("communicationHealthy") is True
            and self_test.get("validFlags") == 0x03
            and self_test.get("weightValid") is True
            and self_test.get("infraredValid") is True
            and self_test.get("smokeSensorHealth") == "OK"
        )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise McuUpdateError(
                "MCU_UPDATE_DISABLED",
                "MCU firmware update is disabled on this edge device",
            )
