"""Test-only BOOT0, reset and flash boundary for a virtual fixed-frame MCU."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from mcu_firmware_updater import McuUpdateError
from tools.fixed_frame_pty_simulator import (
    VirtualFixedFrameMcu,
    VirtualFixedFrameSerialFactory,
)


def _positive_version_codes(
    values: Iterable[int],
    description: str,
) -> frozenset[int]:
    result = frozenset(int(value) for value in values)
    if any(value <= 0 for value in result):
        raise ValueError(f"{description} version codes must be positive")
    return result


class McuFirmwareUpgradeSimulation:
    """Expose one virtual MCU through the updater's hardware boundaries."""

    def __init__(
        self,
        model: VirtualFixedFrameMcu,
        *,
        self_test_failure_version_codes: Iterable[int] = (),
        identity_mismatch_version_codes: Iterable[int] = (),
        flash_failure_version_codes: Iterable[int] = (),
        application_boot_failures: int = 0,
        legacy_unprepared_bootloader_entries: int = 0,
    ) -> None:
        if not isinstance(model, VirtualFixedFrameMcu):
            raise TypeError("model must be a VirtualFixedFrameMcu")
        self.model = model
        self.serial_factory = VirtualFixedFrameSerialFactory(model)
        self.self_test_failure_version_codes = _positive_version_codes(
            self_test_failure_version_codes,
            "self-test failure",
        )
        self.identity_mismatch_version_codes = _positive_version_codes(
            identity_mismatch_version_codes,
            "identity mismatch",
        )
        self.flash_failure_version_codes = _positive_version_codes(
            flash_failure_version_codes,
            "flash failure",
        )
        self.configure_application_boot_failures(application_boot_failures)
        if (
            not isinstance(legacy_unprepared_bootloader_entries, int)
            or isinstance(legacy_unprepared_bootloader_entries, bool)
            or legacy_unprepared_bootloader_entries < 0
        ):
            raise ValueError(
                "legacy unprepared bootloader entry count must be non-negative"
            )
        self._legacy_unprepared_bootloader_entries_remaining = (
            legacy_unprepared_bootloader_entries
        )
        self.flash_version_codes: list[int] = []
        self.flash_attempt_version_codes: list[int] = []
        self.bootloader_entry_count = 0
        self.application_boot_count = 0
        self.application_selection_count = 0

    def configure_flash_failures(
        self,
        version_codes: Iterable[int],
    ) -> None:
        """Replace the firmware versions whose flash attempts must fail."""

        self.flash_failure_version_codes = _positive_version_codes(
            version_codes,
            "flash failure",
        )

    def configure_application_boot_failures(self, count: int) -> None:
        """Fail the next ``count`` BOOT0-low application reset attempts."""

        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise ValueError(
                "application boot failure count must be non-negative"
            )
        self._application_boot_failures_remaining = count

    def enter_system_bootloader(self) -> None:
        if (
            self.model.runtime_mode == "APPLICATION"
            and not self.model.update_prepared
        ):
            if self._legacy_unprepared_bootloader_entries_remaining <= 0:
                raise McuUpdateError(
                    "SIMULATED_PREPARE_REQUIRED",
                    "virtual MCU rejected application-to-ROM reset without F2 prepare",
                )
            self._legacy_unprepared_bootloader_entries_remaining -= 1
        self.bootloader_entry_count += 1
        self.model.enter_system_bootloader()

    def boot_application(self) -> None:
        self.application_boot_count += 1
        if self._application_boot_failures_remaining > 0:
            self._application_boot_failures_remaining -= 1
            self.model.enter_system_bootloader()
            raise McuUpdateError(
                "SIMULATED_APPLICATION_BOOT_FAILED",
                "virtual MCU injected an application boot failure",
            )
        self.model.boot_application()

    def force_application_selection(self) -> None:
        self.application_selection_count += 1
        if self.model.runtime_mode != "APPLICATION":
            raise McuUpdateError(
                "SIMULATED_APPLICATION_NOT_RUNNING",
                "virtual MCU application is not running",
            )

    def flash(
        self,
        image_path: Path,
        image_size: int,
        *,
        manifest: dict | None = None,
    ) -> dict:
        image_path = image_path.resolve(strict=True)
        if image_size <= 0 or image_path.stat().st_size != image_size:
            raise McuUpdateError(
                "IMAGE_SIZE_MISMATCH",
                "simulated image size differs from manifest",
            )
        if manifest is None:
            raise McuUpdateError(
                "SIMULATED_MANIFEST_MISSING",
                "simulated flash requires the verified manifest",
            )
        version_code = int(manifest["firmwareVersionCode"])
        self.flash_attempt_version_codes.append(version_code)
        if version_code in self.flash_failure_version_codes:
            raise McuUpdateError(
                "SIMULATED_FLASH_FAILED",
                "virtual MCU injected a flash failure",
            )
        installed_manifest = dict(manifest)
        if version_code in self.identity_mismatch_version_codes:
            identity = str(installed_manifest["firmwareIdentityHex"])
            installed_manifest["firmwareIdentityHex"] = (
                ("a" if identity[0] != "a" else "b") + identity[1:]
            )
        try:
            self.model.install_firmware(installed_manifest)
        except Exception as error:
            raise McuUpdateError(
                "SIMULATED_FLASH_FAILED",
                f"virtual MCU flash failed: {type(error).__name__}",
            ) from error
        self.model.set_self_test_failure(
            version_code in self.self_test_failure_version_codes
        )
        self.flash_version_codes.append(version_code)
        return {
            "returnCode": 0,
            "simulated": True,
            "firmwareVersionCode": version_code,
        }
