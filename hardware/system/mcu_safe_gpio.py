"""Establish the fail-safe application selection for the production MCU.

The production mainboard connects BOOT0 to WiringOP wPi 2 and drives an
external 2N7002 gate from wPi 5.  Both Orange Pi outputs must be low in the
normal application state.  In particular, wPi 5 high *asserts* STM32 NRST by
turning the MOSFET on, so this helper must never write a high level.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence


MCU_BOOT0_WPI = 2
MCU_RESET_GATE_WPI = 5
SAFE_LEVEL = 0
GPIO_COMMAND_TIMEOUT_SECONDS = 5
DEFAULT_SAFE_FACT_PATH = Path("/run/ecobin/mcu-safe-gpio/boot-safe.json")
DEFAULT_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class GpioCommandError(RuntimeError):
    """A bounded, non-secret WiringOP command failure."""


class WiringOpGpio:
    """Run WiringOP without a shell and return its bounded text output."""

    def __init__(
        self,
        gpio_path: str,
        *,
        command_runner: Callable[..., object] = subprocess.run,
        timeout_seconds: int = GPIO_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if not gpio_path or not os.path.isabs(gpio_path):
            raise ValueError("WiringOP gpio path must be absolute")
        if timeout_seconds <= 0:
            raise ValueError("WiringOP timeout must be positive")
        self.gpio_path = gpio_path
        self._run_command = command_runner
        self.timeout_seconds = timeout_seconds

    def run(self, *arguments: str) -> str:
        argv = [self.gpio_path, *arguments]
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
        except Exception as error:
            raise GpioCommandError("WiringOP GPIO execution failed") from error
        if int(getattr(result, "returncode", 1)) != 0:
            raise GpioCommandError("WiringOP GPIO command returned a failure")
        return str(getattr(result, "stdout", ""))


class SafeMcuGpioInitializer:
    """Drive only the two fixed production outputs to their safe low state."""

    def __init__(self, gpio: WiringOpGpio) -> None:
        self.gpio = gpio

    def establish(self) -> None:
        """Select application boot and release the open-drain reset gate.

        BOOT0 is made safe before the reset gate is released.  This order also
        recovers a process that died while NRST was asserted: the eventual
        release can only boot the application, never the system ROM.
        """

        try:
            self._drive_and_verify_low(MCU_BOOT0_WPI, "BOOT0")
            self._drive_and_verify_low(MCU_RESET_GATE_WPI, "reset gate")
        except Exception:
            self._best_effort_safe_levels()
            raise

    def _drive_and_verify_low(self, pin: int, name: str) -> None:
        self.gpio.run("mode", str(pin), "out")
        self.gpio.run("write", str(pin), str(SAFE_LEVEL))
        self._verify_low(pin, name)

    def _best_effort_safe_levels(self) -> None:
        # Never release a reset gate possibly left asserted until BOOT0 has
        # been driven and read back low.  A failed recovery therefore leaves
        # the MCU unavailable instead of risking a boot into the system ROM.
        try:
            self._drive_and_verify_low(MCU_BOOT0_WPI, "BOOT0")
        except Exception:
            return
        try:
            self._drive_and_verify_low(MCU_RESET_GATE_WPI, "reset gate")
        except Exception:
            return

    def _verify_low(self, pin: int, name: str) -> None:
        value = self.gpio.run("read", str(pin)).strip()
        if value != str(SAFE_LEVEL):
            raise GpioCommandError(f"{name} safe GPIO readback did not match")


def establish_safe_gpio_fact(
    initializer: SafeMcuGpioInitializer,
    fact_path: Path,
    boot_id_path: Path,
) -> None:
    """Publish a crash-safe fact only after both GPIOs read back low."""

    _require_absolute(fact_path, "safe GPIO fact")
    _require_absolute(boot_id_path, "boot ID")
    _remove_previous_fact(fact_path)
    initializer.establish()
    boot_id = _read_boot_id(boot_id_path)
    document = {
        "schemaVersion": 1,
        "status": "SAFE_APPLICATION",
        "bootId": boot_id,
        "boot0": {"wpi": MCU_BOOT0_WPI, "level": SAFE_LEVEL},
        "resetGate": {
            "wpi": MCU_RESET_GATE_WPI,
            "level": SAFE_LEVEL,
        },
    }
    _atomic_write_root_fact(
        fact_path,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
        .encode("utf-8"),
    )


def _remove_previous_fact(path: Path) -> None:
    parent_fd = _open_trusted_parent(path)
    try:
        try:
            os.unlink(path.name, dir_fd=parent_fd)
        except FileNotFoundError:
            return
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _atomic_write_root_fact(path: Path, payload: bytes) -> None:
    if not payload or len(payload) > 4096:
        raise OSError("safe GPIO fact payload has an invalid size")
    parent_fd = _open_trusted_parent(path)
    temporary_name = f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("safe GPIO fact write made no progress")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def _open_trusted_parent(path: Path) -> int:
    parent = path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or parent.is_symlink():
        raise OSError("safe GPIO fact parent is not a real directory")
    if getattr(info, "st_uid", 0) != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise OSError("safe GPIO fact parent is not root-controlled")
    return os.open(
        parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )


def _read_boot_id(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip().lower()
    if not _BOOT_ID.fullmatch(value):
        raise OSError("kernel boot ID is invalid")
    return value


def _require_absolute(path: Path, name: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"{name} path must be absolute")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set EcoBin MCU BOOT0 and reset gate to their safe state",
    )
    parser.add_argument(
        "--gpio-path",
        default=os.getenv("ECOBIN_GPIO_PATH", "/usr/bin/gpio"),
        help="absolute WiringOP gpio executable path",
    )
    parser.add_argument(
        "--fact-path",
        type=Path,
        default=DEFAULT_SAFE_FACT_PATH,
        help="root-controlled current-boot safety fact",
    )
    parser.add_argument(
        "--boot-id-path",
        type=Path,
        default=DEFAULT_BOOT_ID_PATH,
        help="kernel boot ID source",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        establish_safe_gpio_fact(
            SafeMcuGpioInitializer(WiringOpGpio(args.gpio_path)),
            args.fact_path,
            args.boot_id_path,
        )
    except (GpioCommandError, OSError, ValueError) as error:
        print(f"MCU safe GPIO initialization failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
