"""Fixed, local-only STM32 flashing primitive for the permanent updater."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - deployment is Linux-only
    fcntl = None  # type: ignore[assignment]

from local_control import LocalControlActionError

from .mcu_flash_recovery import (
    BOOT0_ACTIVE_LEVEL,
    BOOT0_WPI,
    BOOT_SETTLE_SECONDS,
    COMMAND_TIMEOUT_SECONDS,
    GPIO_BINARY,
    MCU_RECOVERY_MARKER_PATH,
    RESET_ACTIVE_LEVEL,
    RESET_ASSERT_SECONDS,
    RESET_GATE_WPI,
    SAFE_LEVEL,
    McuApplicationRecovery,
)

STM32FLASH_BINARY = "/usr/bin/stm32flash"
SERIAL_DEVICE = "/dev/ttyS5"
BUSINESS_SERVICE = "ecobin-hardware.service"
BUSINESS_SERVICES = (
    BUSINESS_SERVICE,
    "ecobin-business.service",
    "ecobin-business-updatable-candidate.service",
)
SYSTEMCTL = "/usr/bin/systemctl"
FIRMWARE_ROOT = Path("/var/lib/ecobin/updater/mcu-firmware")
FLASH_BASE = 0x08000000
FLASH_BAUDRATE = 115200
FLASH_SERIAL_MODE = "8e1"
FLASH_RETRIES = 3
MAX_FIRMWARE_BYTES = 4 * 1024 * 1024
FLASH_TIMEOUT_SECONDS = 120
OUTPUT_LIMIT = 1024
_PRINTABLE_OUTPUT = re.compile(r"[^\x09\x0a\x0d\x20-\x7e]")


class McuFlashPrimitives:
    """Flash only one of two fixed images associated with one update UID."""

    def __init__(
        self,
        *,
        firmware_root: Path = FIRMWARE_ROOT,
        updater_uid: int,
        recovery_marker_path: Path = MCU_RECOVERY_MARKER_PATH,
        command_runner: Any = subprocess.run,
        sleeper: Any = time.sleep,
    ) -> None:
        if (
            isinstance(updater_uid, bool)
            or not isinstance(updater_uid, int)
            or updater_uid < 0
        ):
            raise ValueError("updater UID must be a non-negative integer")
        self.firmware_root = firmware_root
        self.updater_uid = updater_uid
        self._run_command = command_runner
        self._sleep = sleeper
        self._application_recovery = McuApplicationRecovery(
            marker_path=recovery_marker_path,
            command_runner=command_runner,
            sleeper=sleeper,
        )

    def flash(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload.get("updateUid"), "updateUid")
        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        source = payload.get("source")
        if source not in {"TARGET", "ROLLBACK"}:
            raise LocalControlActionError(
                "REQUEST_INVALID",
                "MCU firmware source must be TARGET or ROLLBACK",
            )
        self._require_business_inactive()
        image_path = self.firmware_root / update_uid / (
            "target.bin" if source == "TARGET" else "rollback.bin"
        )
        descriptor, image_size = self._open_fixed_firmware(image_path)
        flash_result: dict[str, Any] | None = None
        operation_error: Exception | None = None
        recovery_error: Exception | None = None
        recovery_armed = False
        try:
            self._application_recovery.arm()
            recovery_armed = True
            try:
                self._enter_bootloader()
                flash_result = self._run_flash(descriptor, image_size)
            except Exception as error:  # noqa: BLE001 - safe GPIO recovery is mandatory
                operation_error = error
            finally:
                if recovery_armed:
                    try:
                        self._restore_safe_application()
                    except Exception as error:  # noqa: BLE001 - report unsafe pins
                        recovery_error = error
        finally:
            os.close(descriptor)
        if recovery_error is not None:
            raise LocalControlActionError(
                "MCU_SAFE_RECOVERY_FAILED",
                "MCU pins could not be restored to the safe application state",
            ) from recovery_error
        if operation_error is not None:
            if isinstance(operation_error, LocalControlActionError):
                raise operation_error
            raise LocalControlActionError(
                "MCU_FLASH_FAILED",
                "fixed MCU flash operation failed",
            ) from operation_error
        if flash_result is None:
            raise LocalControlActionError(
                "MCU_FLASH_FAILED",
                "fixed MCU flash operation produced no result",
            )
        return {
            "updateUid": update_uid,
            "actionUid": action_uid,
            "source": source,
            # stm32flash verifies the written bytes.  Application startup and
            # protocol health are separate, unprivileged updater checks.
            "disposition": "FIRMWARE_WRITE_VERIFIED",
            **flash_result,
            "safeApplicationSelected": True,
        }

    def recover_application(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the one fixed application-boot recovery action."""

        update_uid = _require_uuid4(payload.get("updateUid"), "updateUid")
        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        self._require_business_inactive()
        self._application_recovery.arm(allow_existing=True)
        result = self._application_recovery.restore_and_disarm()
        return {
            "updateUid": update_uid,
            "actionUid": action_uid,
            **result,
        }

    def _require_business_inactive(self) -> None:
        for business_service in BUSINESS_SERVICES:
            try:
                result = self._run_command(
                    [
                        SYSTEMCTL,
                        "show",
                        "--property=ActiveState",
                        "--value",
                        "--",
                        business_service,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                    check=False,
                )
            except Exception as error:
                raise LocalControlActionError(
                    "BUSINESS_RUNTIME_STATE_UNKNOWN",
                    "fixed business service state could not be read",
                ) from error
            state = str(getattr(result, "stdout", "")).strip().lower()
            if int(getattr(result, "returncode", 1)) != 0 or state not in {
                "inactive",
                "failed",
            }:
                raise LocalControlActionError(
                    "BUSINESS_RUNTIME_ACTIVE",
                    "all fixed business services must be stopped before MCU flashing",
                )

    def _open_fixed_firmware(self, image_path: Path) -> tuple[int, int]:
        _require_real_directory(self.firmware_root, "MCU firmware root")
        update_directory = image_path.parent
        _require_real_directory(update_directory, "MCU firmware update directory")
        try:
            metadata = image_path.lstat()
        except FileNotFoundError as error:
            raise LocalControlActionError(
                "FIRMWARE_IMAGE_NOT_READY",
                "fixed MCU firmware image does not exist",
            ) from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != self.updater_uid
            or metadata.st_mode & 0o022
            or metadata.st_size <= 0
            or metadata.st_size > MAX_FIRMWARE_BYTES
        ):
            raise LocalControlActionError(
                "FIRMWARE_IMAGE_INVALID",
                "fixed MCU firmware image permissions, type, or size are invalid",
            )
        source_descriptor = os.open(
            image_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or opened.st_size != metadata.st_size
        ):
            os.close(source_descriptor)
            raise LocalControlActionError(
                "FIRMWARE_IMAGE_CHANGED",
                "fixed MCU firmware image changed before flashing",
            )
        if not hasattr(os, "memfd_create"):
            os.close(source_descriptor)
            raise LocalControlActionError(
                "FIRMWARE_IMAGE_COPY_FAILED",
                "sealed in-memory firmware copies are unavailable",
            )
        memory_descriptor: int | None = None
        try:
            memory_descriptor = os.memfd_create(
                "ecobin-mcu-firmware",
                getattr(os, "MFD_CLOEXEC", 0)
                | getattr(os, "MFD_ALLOW_SEALING", 0),
            )
            copied = 0
            while copied < opened.st_size:
                chunk = os.read(
                    source_descriptor,
                    min(1024 * 1024, opened.st_size - copied),
                )
                if not chunk:
                    raise OSError("MCU firmware copy ended before its fixed size")
                offset = 0
                while offset < len(chunk):
                    written = os.write(memory_descriptor, chunk[offset:])
                    if written <= 0:
                        raise OSError("MCU firmware copy made no progress")
                    offset += written
                copied += len(chunk)
            finished = os.fstat(source_descriptor)
            if (
                finished.st_size != opened.st_size
                or finished.st_mtime_ns != opened.st_mtime_ns
            ):
                raise LocalControlActionError(
                    "FIRMWARE_IMAGE_CHANGED",
                    "fixed MCU firmware image changed while it was copied",
                )
            os.lseek(memory_descriptor, 0, os.SEEK_SET)
            seals = (
                getattr(fcntl, "F_SEAL_SEAL", 0)
                | getattr(fcntl, "F_SEAL_SHRINK", 0)
                | getattr(fcntl, "F_SEAL_GROW", 0)
                | getattr(fcntl, "F_SEAL_WRITE", 0)
            )
            if not seals or fcntl is None or not hasattr(fcntl, "F_ADD_SEALS"):
                raise OSError("kernel file sealing is unavailable")
            fcntl.fcntl(memory_descriptor, fcntl.F_ADD_SEALS, seals)
            return memory_descriptor, opened.st_size
        except LocalControlActionError:
            if memory_descriptor is not None:
                os.close(memory_descriptor)
            raise
        except Exception as error:
            if memory_descriptor is not None:
                os.close(memory_descriptor)
            raise LocalControlActionError(
                "FIRMWARE_IMAGE_COPY_FAILED",
                "fixed MCU firmware could not be copied into sealed memory",
            ) from error
        finally:
            os.close(source_descriptor)

    def _enter_bootloader(self) -> None:
        self._gpio("mode", str(BOOT0_WPI), "out")
        self._gpio("mode", str(RESET_GATE_WPI), "out")
        self._gpio("write", str(BOOT0_WPI), str(BOOT0_ACTIVE_LEVEL))
        self._gpio("write", str(RESET_GATE_WPI), str(RESET_ACTIVE_LEVEL))
        self._sleep(RESET_ASSERT_SECONDS)
        self._gpio("write", str(RESET_GATE_WPI), str(SAFE_LEVEL))
        self._sleep(BOOT_SETTLE_SECONDS)
        self._require_gpio_level(BOOT0_WPI, BOOT0_ACTIVE_LEVEL)
        self._require_gpio_level(RESET_GATE_WPI, SAFE_LEVEL)

    def _restore_safe_application(self) -> None:
        self._application_recovery.restore_and_disarm()

    def _require_gpio_level(self, pin: int, expected: int) -> None:
        result = self._gpio("read", str(pin))
        if str(getattr(result, "stdout", "")).strip() != str(expected):
            raise LocalControlActionError(
                "GPIO_READBACK_FAILED",
                "fixed MCU GPIO readback did not match",
            )

    def _gpio(self, *arguments: str) -> Any:
        return self._run_fixed_command(
            [GPIO_BINARY, *arguments],
            timeout=COMMAND_TIMEOUT_SECONDS,
            error_code="GPIO_CONTROL_FAILED",
            error_message="fixed MCU GPIO command failed",
        )

    def _run_flash(self, descriptor: int, image_size: int) -> dict[str, Any]:
        inherited_path = f"/proc/self/fd/{descriptor}"
        argv = [
            STM32FLASH_BINARY,
            "-b",
            str(FLASH_BAUDRATE),
            "-m",
            FLASH_SERIAL_MODE,
            "-f",
            "-S",
            f"0x{FLASH_BASE:08x}:{image_size}",
            "-w",
            inherited_path,
            "-v",
            "-n",
            str(FLASH_RETRIES),
            SERIAL_DEVICE,
        ]
        result = self._run_fixed_command(
            argv,
            timeout=FLASH_TIMEOUT_SECONDS,
            error_code="STM32FLASH_FAILED",
            error_message="fixed stm32flash write and verify failed",
            pass_fds=(descriptor,),
        )
        return {
            "imageSize": image_size,
            "toolOutput": _sanitize_output(getattr(result, "stdout", "")),
        }

    def _run_fixed_command(
        self,
        argv: list[str],
        *,
        timeout: int,
        error_code: str,
        error_message: str,
        pass_fds: tuple[int, ...] = (),
    ) -> Any:
        try:
            result = self._run_command(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
                pass_fds=pass_fds,
            )
        except subprocess.TimeoutExpired as error:
            raise LocalControlActionError(
                f"{error_code}_TIMEOUT",
                f"{error_message} before its fixed deadline",
            ) from error
        except Exception as error:
            raise LocalControlActionError(error_code, error_message) from error
        if int(getattr(result, "returncode", 1)) != 0:
            raise LocalControlActionError(error_code, error_message)
        return result


def _require_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise LocalControlActionError(
            "FIXED_PATH_NOT_READY",
            f"{label} does not exist",
        ) from error
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise LocalControlActionError(
            "FIXED_PATH_UNSAFE",
            f"{label} is not a real directory",
        )


def _sanitize_output(value: Any) -> str:
    return _PRINTABLE_OUTPUT.sub("", str(value or ""))[-OUTPUT_LIMIT:]


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LocalControlActionError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise LocalControlActionError(
            "REQUEST_INVALID",
            f"{field} must be a UUIDv4",
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise LocalControlActionError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    return value
