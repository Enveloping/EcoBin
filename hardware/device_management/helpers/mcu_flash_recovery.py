"""Fixed MCU application-boot recovery shared by the helper and systemd."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from local_control import LocalControlActionError

GPIO_BINARY = "/usr/bin/gpio"
BOOT0_WPI = 2
RESET_GATE_WPI = 5
BOOT0_ACTIVE_LEVEL = 1
RESET_ACTIVE_LEVEL = 1
SAFE_LEVEL = 0
COMMAND_TIMEOUT_SECONDS = 5
RESET_ASSERT_SECONDS = 0.05
BOOT_SETTLE_SECONDS = 0.2
MCU_RECOVERY_MARKER_PATH = Path(
    "/run/ecobin/privileged/mcu-application-recovery-required"
)
_MARKER_CONTENT = b"RECOVERY_REQUIRED\n"


class McuApplicationRecovery:
    """Select the application boot path, pulse reset, and prove safe pins."""

    def __init__(
        self,
        *,
        marker_path: Path = MCU_RECOVERY_MARKER_PATH,
        command_runner: Any = subprocess.run,
        sleeper: Any = time.sleep,
    ) -> None:
        self.marker_path = marker_path
        self._run_command = command_runner
        self._sleep = sleeper

    def arm(self, *, allow_existing: bool = False) -> None:
        """Persist recovery intent before any MCU pin can be changed."""

        self._require_safe_marker_parent()
        try:
            descriptor = os.open(
                self.marker_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except FileExistsError as error:
            self._require_safe_marker()
            if allow_existing:
                return
            raise LocalControlActionError(
                "MCU_RECOVERY_REQUIRED",
                "an earlier MCU operation still requires fixed application recovery",
            ) from error
        except Exception as error:
            raise LocalControlActionError(
                "MCU_RECOVERY_GUARD_FAILED",
                "the fixed MCU recovery guard could not be created",
            ) from error

        try:
            metadata = os.fstat(descriptor)
            if not self._marker_metadata_is_safe(metadata):
                raise OSError("new MCU recovery guard metadata is unsafe")
            written = os.write(descriptor, _MARKER_CONTENT)
            if written != len(_MARKER_CONTENT):
                raise OSError("MCU recovery guard write was incomplete")
            os.fsync(descriptor)
        except Exception as error:
            try:
                self.marker_path.unlink()
            except OSError:
                pass
            raise LocalControlActionError(
                "MCU_RECOVERY_GUARD_FAILED",
                "the fixed MCU recovery guard could not be persisted",
            ) from error
        finally:
            os.close(descriptor)

    def is_armed(self) -> bool:
        try:
            self.marker_path.lstat()
        except FileNotFoundError:
            return False
        self._require_safe_marker_parent()
        self._require_safe_marker()
        return True

    def restore_and_disarm(self) -> dict[str, Any]:
        """Recover the MCU and retain the marker unless every readback succeeds."""

        try:
            # BOOT0 must be proven low before reset is ever asserted.  Otherwise
            # the pulse could deliberately boot the STM32 ROM loader again.
            self._gpio("mode", str(BOOT0_WPI), "out")
            self._gpio("write", str(BOOT0_WPI), str(SAFE_LEVEL))
            self._require_gpio_level(BOOT0_WPI, SAFE_LEVEL)
            self._gpio("mode", str(RESET_GATE_WPI), "out")
            self._gpio("write", str(RESET_GATE_WPI), str(RESET_ACTIVE_LEVEL))
            self._sleep(RESET_ASSERT_SECONDS)
            self._gpio("write", str(RESET_GATE_WPI), str(SAFE_LEVEL))
            self._sleep(BOOT_SETTLE_SECONDS)
            self._require_gpio_level(BOOT0_WPI, SAFE_LEVEL)
            self._require_gpio_level(RESET_GATE_WPI, SAFE_LEVEL)
        except Exception as error:  # noqa: BLE001 - best effort must cover all failures
            self._best_effort_safe_levels()
            raise LocalControlActionError(
                "MCU_SAFE_RECOVERY_FAILED",
                "MCU pins could not be restored to the safe application state",
            ) from error

        try:
            self._require_safe_marker()
            self.marker_path.unlink()
        except Exception as error:
            raise LocalControlActionError(
                "MCU_RECOVERY_GUARD_FAILED",
                "the completed MCU recovery guard could not be cleared",
            ) from error
        return {
            "disposition": "APPLICATION_BOOT_PATH_SELECTED",
            "safeApplicationSelected": True,
            "gpioReadback": {
                "boot0Level": SAFE_LEVEL,
                "resetGateLevel": SAFE_LEVEL,
            },
        }

    def _best_effort_safe_levels(self) -> None:
        for arguments in (
            ("mode", str(BOOT0_WPI), "out"),
            ("write", str(BOOT0_WPI), str(SAFE_LEVEL)),
            ("mode", str(RESET_GATE_WPI), "out"),
            ("write", str(RESET_GATE_WPI), str(SAFE_LEVEL)),
        ):
            try:
                self._gpio(*arguments)
            except Exception:  # noqa: BLE001 - preserve the original recovery error
                pass

    def _require_gpio_level(self, pin: int, expected: int) -> None:
        result = self._gpio("read", str(pin))
        if str(getattr(result, "stdout", "")).strip() != str(expected):
            raise LocalControlActionError(
                "GPIO_READBACK_FAILED",
                "fixed MCU GPIO readback did not match",
            )

    def _gpio(self, *arguments: str) -> Any:
        try:
            result = self._run_command(
                [GPIO_BINARY, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise LocalControlActionError(
                "GPIO_CONTROL_FAILED_TIMEOUT",
                "fixed MCU GPIO command failed before its fixed deadline",
            ) from error
        except Exception as error:
            raise LocalControlActionError(
                "GPIO_CONTROL_FAILED",
                "fixed MCU GPIO command failed",
            ) from error
        if int(getattr(result, "returncode", 1)) != 0:
            raise LocalControlActionError(
                "GPIO_CONTROL_FAILED",
                "fixed MCU GPIO command failed",
            )
        return result

    def _require_safe_marker_parent(self) -> None:
        try:
            metadata = self.marker_path.parent.lstat()
        except FileNotFoundError as error:
            raise LocalControlActionError(
                "MCU_RECOVERY_GUARD_FAILED",
                "the fixed MCU recovery directory does not exist",
            ) from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or self.marker_path.parent.is_symlink()
            or metadata.st_uid != _effective_uid()
            or metadata.st_mode & 0o022
        ):
            raise LocalControlActionError(
                "MCU_RECOVERY_GUARD_FAILED",
                "the fixed MCU recovery directory is unsafe",
            )

    def _require_safe_marker(self) -> None:
        try:
            metadata = self.marker_path.lstat()
        except FileNotFoundError as error:
            raise LocalControlActionError(
                "MCU_RECOVERY_GUARD_FAILED",
                "the fixed MCU recovery guard does not exist",
            ) from error
        if not self._marker_metadata_is_safe(metadata):
            raise LocalControlActionError(
                "MCU_RECOVERY_GUARD_FAILED",
                "the fixed MCU recovery guard is unsafe",
            )

    @staticmethod
    def _marker_metadata_is_safe(metadata: os.stat_result) -> bool:
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and metadata.st_uid == _effective_uid()
            and stat.S_IMODE(metadata.st_mode) == 0o600
        )


def _effective_uid() -> int:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        return -1
    return int(getter())


def main() -> int:
    """Recover only an interrupted mutation; discovery requests remain inert."""

    recovery = McuApplicationRecovery()
    try:
        if not recovery.is_armed():
            return 0
        recovery.restore_and_disarm()
    except Exception:  # noqa: BLE001 - systemd needs only a stable failure status
        print("fixed MCU application recovery failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
