"""Bounded hardware ports for the isolated factory acceptance executor.

The fixed-frame transport deliberately reuses ``FixedFrameMcuAdapter`` and its
``FixedFrameParser``.  It never imports the production EdgeStore and never
creates MQTT, COS, HTTP, or production-photo objects.
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import stat
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

from camera_capture import (
    CAMERA_OPEN_FAILED,
    OPENCV_NOT_INSTALLED,
    CameraCaptureError,
    capture_v4l2_jpeg,
    run_camera_captures,
)
from fixed_frame_mcu_adapter import (
    CLEAN_HEADER,
    DELIVERY_HEADER,
    FixedFrameMcuAdapter,
)
from simulated_camera import capture_simulated_camera, is_simulated_camera_source
from system.mcu_safe_gpio import GpioCommandError, WiringOpGpio
import uart2_protocol as uart


MAXIMUM_WEIGHT_GRAMS = 350_000
FACTORY_PRICE_DIGIT = 9
DELIVERY_WIRE = bytes((0xBB, FACTORY_PRICE_DIGIT, 0xBB, 0xAA, 0x01, 0xAA))
CLEAN_WIRE = bytes((0xEE, 0x01, 0xEE))
RESET_ASSERT_SECONDS = 0.05
BOOTLOADER_SETTLE_SECONDS = 0.25
APPLICATION_SETTLE_SECONDS = 0.35
STM32F103C8_DEVICE_ID = "0x0410"
MAXIMUM_ROM_PROBE_OUTPUT_CHARACTERS = 64 * 1024
CAMERA_REVIEW_NONCE = re.compile(r"^[0-9a-f]{32}$")


class AcceptanceHardwareError(RuntimeError):
    """A stable, non-secret hardware failure code."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            raise ValueError("hardware failure code is invalid")
        self.code = code
        super().__init__(code)


class NetworkAccessForbidden(RuntimeError):
    """The isolated executor attempted to open or resolve a network socket."""


_NETWORK_GUARD_LOCK = threading.RLock()


@contextmanager
def deny_network_access():
    """Fail closed if acceptance code or an injected probe touches networking.

    The factory-test service is a single isolated executor, so a process-wide
    guard is appropriate.  The nftables OUTPUT lock remains the independent
    operating-system boundary; this guard makes the Python boundary explicit
    and unit-testable.
    """

    with _NETWORK_GUARD_LOCK:
        original_socket = socket.socket
        original_create_connection = socket.create_connection
        original_getaddrinfo = socket.getaddrinfo

        def denied(*args, **kwargs):
            del args, kwargs
            raise NetworkAccessForbidden(
                "offline factory acceptance forbids network access"
            )

        socket.socket = denied  # type: ignore[assignment]
        socket.create_connection = denied  # type: ignore[assignment]
        socket.getaddrinfo = denied  # type: ignore[assignment]
        try:
            yield
        finally:
            socket.socket = original_socket  # type: ignore[assignment]
            socket.create_connection = original_create_connection
            socket.getaddrinfo = original_getaddrinfo


def sanitize_firmware_identity(result: dict) -> dict:
    """Require a trustworthy revision-2 identity and retain safe diagnostics."""

    if not isinstance(result, dict):
        raise AcceptanceHardwareError("MCU_F3_INVALID")
    version = result.get("firmwareVersion")
    identity = result.get("firmwareIdentityHex")
    version_code = result.get("firmwareVersionCode")
    capability = result.get("mcuCapabilityBitmap")
    diagnostic_fields = (
        "mcuBootId",
        "mcuHighestCommandSequence",
        "mcuCapabilityBitmap",
    )
    diagnostic_presence = tuple(name in result for name in diagnostic_fields)
    known_capability = int(
        uart.REGISTRY["capabilityPolicy"]["knownMaskHex"], 16
    )
    required_capability = int(
        uart.REGISTRY["capabilityPolicy"]["requiredMcuMaskHex"],
        16,
    )
    if (
        result.get("queryStatus") != "OK"
        or result.get("mode") != 1
        or result.get("statusCode") != 0
        or result.get("protocolRevision") != 2
        or not isinstance(version_code, int)
        or isinstance(version_code, bool)
        or version_code <= 0
        or not isinstance(version, str)
        or not 5 <= len(version) <= 32
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in version)
        or not isinstance(identity, str)
        or re.fullmatch(r"[0-9a-fA-F]{16}", identity) is None
        or int(identity, 16) == 0
        or (
            any(diagnostic_presence)
            and (
                not all(diagnostic_presence)
                or type(result.get("mcuBootId")) is not int
                or not 1 <= result["mcuBootId"] <= 9_007_199_254_740_991
                or type(result.get("mcuHighestCommandSequence")) is not int
                or not 0 <= result["mcuHighestCommandSequence"] <= 0xFFFFFFFF
                or type(capability) is not int
                or capability & ~known_capability
                or capability & required_capability != required_capability
            )
        )
    ):
        raise AcceptanceHardwareError("MCU_F3_NOT_TRUSTED_REVISION_2")
    sanitized = {
        "fixedFrameRevision": 2,
        "firmwareVersion": version,
        "firmwareVersionCode": version_code,
        "firmwareIdentityHex": identity.lower(),
    }
    if all(diagnostic_presence):
        sanitized.update(
            {
                "mcuBootId": result["mcuBootId"],
                "mcuHighestCommandSequence": result[
                    "mcuHighestCommandSequence"
                ],
                "mcuCapabilityBitmapHex": f"{capability:016x}",
            }
        )
    return sanitized


def sanitize_self_test(result: dict) -> dict:
    """Require a healthy F1 snapshot for factory acceptance."""

    if not isinstance(result, dict):
        raise AcceptanceHardwareError("MCU_F1_INVALID")
    weight = result.get("weightGrams")
    infrared = result.get("infraredBlocked")
    fullness_kind = result.get("fullnessSensorKind")
    fullness = None
    if fullness_kind is None:
        if result.get("infraredValid") is True and isinstance(infrared, bool):
            fullness = {
                "infraredBlocked": infrared,
            }
    elif fullness_kind == "DIGITAL_INFRARED":
        if (
            result.get("fullnessReadStatus") == "VALID"
            and result.get("infraredValid") is True
            and isinstance(infrared, bool)
            and result.get("fullnessBlocked") is infrared
        ):
            fullness = {
                "infraredBlocked": infrared,
                "fullnessSensorKind": fullness_kind,
                "fullnessReadStatus": "VALID",
                "fullnessDistanceMm": None,
                "fullnessDistanceThresholdMm": None,
                "fullnessBlocked": infrared,
            }
    elif fullness_kind == "ULTRASONIC":
        distance = result.get("fullnessDistanceMm")
        threshold = result.get("fullnessDistanceThresholdMm")
        blocked = result.get("fullnessBlocked")
        if (
            result.get("fullnessReadStatus") == "VALID"
            and result.get("infraredValid") is False
            and type(distance) is int
            and 0 <= distance <= 4_000
            and type(threshold) is int
            and 1 <= threshold <= 4_000
            and isinstance(blocked, bool)
            and blocked is (distance < threshold)
            and infrared is blocked
        ):
            fullness = {
                # Kept for report-v2/P7 readers; the raw fields below make its
                # ultrasonic threshold meaning explicit.
                "infraredBlocked": blocked,
                "fullnessSensorKind": fullness_kind,
                "fullnessReadStatus": "VALID",
                "fullnessDistanceMm": distance,
                "fullnessDistanceThresholdMm": threshold,
                "fullnessBlocked": blocked,
            }
    if (
        result.get("queryStatus") != "OK"
        or result.get("communicationHealthy") is not True
        or result.get("validFlags") != 3
        or result.get("weightValid") is not True
        or not isinstance(weight, int)
        or isinstance(weight, bool)
        or not 0 <= weight <= MAXIMUM_WEIGHT_GRAMS
        or fullness is None
        or result.get("smokeCode") != 0
        or result.get("smokeState") != "NORMAL"
        or result.get("smokeSensorHealth") != "OK"
    ):
        raise AcceptanceHardwareError("MCU_F1_UNHEALTHY")
    sanitized = {
        "weightGrams": weight,
        "smokeCode": 0,
    } | fullness
    sample_identity_fields = (
        "mcuBootId",
        "scaleAttemptSequence",
        "scaleCapturedUptimeMs",
    )
    present = tuple(name in result for name in sample_identity_fields)
    if any(present):
        boot_id = result.get("mcuBootId")
        attempt = result.get("scaleAttemptSequence")
        captured = result.get("scaleCapturedUptimeMs")
        if (
            not all(present)
            or type(boot_id) is not int
            or not 1 <= boot_id <= 0xFFFFFFFF
            or type(attempt) is not int
            or not 1 <= attempt <= 0xFFFFFFFF
            or type(captured) is not int
            or not 0 <= captured <= 0xFFFFFFFF
        ):
            raise AcceptanceHardwareError("MCU_SCALE_SAMPLE_IDENTITY_INVALID")
        sanitized.update(
            {
                "mcuBootId": boot_id,
                "scaleAttemptSequence": attempt,
                "scaleCapturedUptimeMs": captured,
            }
        )
    return sanitized


def identities_equal(left: dict, right: dict) -> bool:
    fields = (
        "fixedFrameRevision",
        "firmwareVersion",
        "firmwareVersionCode",
        "firmwareIdentityHex",
    )
    return all(left.get(field) == right.get(field) for field in fields)


class FixedFrameAcceptanceMcu:
    """Factory-only facade over the existing fixed-frame adapter.

    Physical AA/EE writes do not call ``send_command`` because production's
    compatibility adapter intentionally discards stale business input before a
    normal command.  Factory acceptance instead treats any stale, wrong,
    incomplete, or corrupt business result as a recovery condition.  The exact
    serial write and parser are still the existing adapter implementations.
    """

    def __init__(
        self,
        adapter: FixedFrameMcuAdapter,
        *,
        prewrite_quiet_ms: int = 100,
        minimum_action_result_delay_ms: int = 250,
    ) -> None:
        if not isinstance(adapter, FixedFrameMcuAdapter):
            raise TypeError("adapter must be FixedFrameMcuAdapter")
        for name, value in (
            ("prewrite_quiet_ms", prewrite_quiet_ms),
            ("minimum_action_result_delay_ms", minimum_action_result_delay_ms),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        self.adapter = adapter
        self.prewrite_quiet_ms = prewrite_quiet_ms
        self.minimum_action_result_delay_ms = minimum_action_result_delay_ms
        self._last_action_write_monotonic: Optional[float] = None

    @classmethod
    def for_port(
        cls,
        port: str = "/dev/ttyS5",
        *,
        timeout_s: float = 0.1,
        serial_factory: Optional[Callable[..., object]] = None,
        is_simulated: bool = False,
        prewrite_quiet_ms: Optional[int] = None,
        minimum_action_result_delay_ms: Optional[int] = None,
    ) -> "FixedFrameAcceptanceMcu":
        return cls(
            FixedFrameMcuAdapter(
                port,
                edge_boot_id=0,
                port_count=1,
                baudrate=115200,
                timeout_s=timeout_s,
                serial_factory=serial_factory,
                is_simulated=is_simulated,
            ),
            prewrite_quiet_ms=(
                (0 if is_simulated else 100)
                if prewrite_quiet_ms is None
                else prewrite_quiet_ms
            ),
            minimum_action_result_delay_ms=(
                (0 if is_simulated else 250)
                if minimum_action_result_delay_ms is None
                else minimum_action_result_delay_ms
            ),
        )

    @property
    def is_open(self) -> bool:
        return self.adapter.is_open

    def open(self) -> None:
        if not self.adapter.is_open and not self.adapter.open():
            raise AcceptanceHardwareError("UART5_OPEN_FAILED")

    def close(self) -> None:
        self.adapter.close()

    def query_identity(self, timeout_ms: int = 3_000) -> dict:
        self.open()
        return self.adapter.query_firmware_identity(timeout_ms=timeout_ms)

    def query_self_test(self, timeout_ms: int = 3_000) -> dict:
        self.open()
        return self.adapter.query_self_test(
            timeout_ms=timeout_ms,
            queue_unchanged_safety_event=False,
        )

    def execute_update_prepare(self, timeout_ms: int = 3_000) -> dict:
        self.open()
        return self.adapter.execute_firmware_update_prepare(
            timeout_ms=timeout_ms
        )

    def _reject_pending_before_physical_write_locked(self) -> None:
        adapter = self.adapter
        parser = adapter._parser  # noqa: SLF001
        invalid_before = {
            DELIVERY_HEADER: parser.invalid_count(DELIVERY_HEADER),
            CLEAN_HEADER: parser.invalid_count(CLEAN_HEADER),
        }
        decoded, invalid_smoke = adapter._read_available_decoded()  # noqa: SLF001
        if invalid_smoke or any(
            parser.invalid_count(header) > count
            for header, count in invalid_before.items()
        ):
            raise AcceptanceHardwareError("STALE_CORRUPT_FRAME")
        pending = list(adapter._pending_events)  # noqa: SLF001
        adapter._pending_events.clear()  # noqa: SLF001
        for item in decoded:
            if item.get("frame_type") in {"DELIVERY", "CLEAN"}:
                raise AcceptanceHardwareError("STALE_BUSINESS_RESULT")
            if item.get("frame_type") in {"SELF_TEST", "FIRMWARE_STATUS"}:
                raise AcceptanceHardwareError("STALE_QUERY_RESULT")
        for event in pending:
            if event.get("message_name") in {
                "COMPAT_DELIVERY_RESULT",
                "COMPAT_CLEAN_RESULT",
            }:
                raise AcceptanceHardwareError("STALE_BUSINESS_RESULT")
            payload = event.get("payload") or {}
            if (
                event.get("message_name") == "SAFETY_SENSOR_EVENT"
                and (
                    payload.get("smokeState") != "NORMAL"
                    or payload.get("smokeSensorHealth") != "OK"
                )
            ):
                raise AcceptanceHardwareError("SAFETY_EVENT_UNHEALTHY")
        if parser.buffered_length:
            raise AcceptanceHardwareError("STALE_INCOMPLETE_FRAME")

    def write_action_once(self, action: str) -> None:
        self.open()
        if action == "DELIVERY":
            wire = DELIVERY_WIRE
        elif action == "CLEAN":
            wire = CLEAN_WIRE
        else:
            raise ValueError("action must be DELIVERY or CLEAN")
        with self.adapter._foreground_io(  # noqa: SLF001
            f"FACTORY_{action}_WRITE"
        ):
            # The stale-input check and the one physical write share the same
            # serial lock.  A late DD/EF cannot slip between them and be bound
            # to this new local action.
            quiet_deadline = (
                time.monotonic() + self.prewrite_quiet_ms / 1000.0
            )
            while True:
                self._reject_pending_before_physical_write_locked()
                remaining = quiet_deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.01, remaining))
            self.adapter._write_exact(wire)  # noqa: SLF001
            self._last_action_write_monotonic = time.monotonic()

    def await_final_result(self, action: str, timeout_ms: int) -> dict:
        if action not in {"DELIVERY", "CLEAN"}:
            raise ValueError("action must be DELIVERY or CLEAN")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        expected = (
            "COMPAT_DELIVERY_RESULT"
            if action == "DELIVERY"
            else "COMPAT_CLEAN_RESULT"
        )
        parser = self.adapter._parser  # noqa: SLF001
        invalid_before = {
            DELIVERY_HEADER: parser.invalid_count(DELIVERY_HEADER),
            CLEAN_HEADER: parser.invalid_count(CLEAN_HEADER),
        }
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            event = self.adapter.read_mcu_event(timeout_ms=remaining_ms)
            if event is None:
                break
            name = event.get("message_name")
            if name == "SAFETY_SENSOR_EVENT":
                payload = event.get("payload") or {}
                if (
                    payload.get("smokeState") != "NORMAL"
                    or payload.get("smokeSensorHealth") != "OK"
                ):
                    raise AcceptanceHardwareError("SAFETY_EVENT_UNHEALTHY")
                continue
            if name != expected:
                raise AcceptanceHardwareError("WRONG_OR_LATE_FINAL_RESULT")
            written_at = self._last_action_write_monotonic
            if written_at is None or (
                (time.monotonic() - written_at) * 1000.0
                < self.minimum_action_result_delay_ms
            ):
                raise AcceptanceHardwareError(
                    "LATE_FINAL_RESULT_BEFORE_ACTION_WINDOW"
                )
            payload = event.get("payload") or {}
            pre_weight = payload.get("preWeightGrams")
            post_weight = payload.get("postWeightGrams")
            infrared = payload.get("infraredBlocked")
            if (
                not isinstance(pre_weight, int)
                or isinstance(pre_weight, bool)
                or not 0 <= pre_weight <= MAXIMUM_WEIGHT_GRAMS
                or not isinstance(post_weight, int)
                or isinstance(post_weight, bool)
                or not 0 <= post_weight <= MAXIMUM_WEIGHT_GRAMS
                or not isinstance(infrared, bool)
            ):
                raise AcceptanceHardwareError("FINAL_RESULT_INVALID")
            return {
                "preWeightGrams": pre_weight,
                "postWeightGrams": post_weight,
                "weightDeltaGrams": (
                    post_weight - pre_weight
                    if action == "DELIVERY"
                    else pre_weight - post_weight
                ),
                "infraredBlocked": infrared,
            }
        if any(
            parser.invalid_count(header) > count
            for header, count in invalid_before.items()
        ) or parser.buffered_length:
            raise AcceptanceHardwareError("CORRUPT_FINAL_RESULT")
        raise AcceptanceHardwareError("FINAL_RESULT_TIMEOUT")

    def confirm_final_result(self, result: dict) -> None:
        """Legacy DD/EF frames have no separate durable-result release."""

        if not isinstance(result, dict):
            raise ValueError("factory result must be an object")

    def business_input_marker(self) -> dict[int, int]:
        parser = self.adapter._parser  # noqa: SLF001
        return {
            DELIVERY_HEADER: parser.invalid_count(DELIVERY_HEADER),
            CLEAN_HEADER: parser.invalid_count(CLEAN_HEADER),
        }

    def require_business_quiet(
        self,
        quiet_ms: int = 150,
        *,
        invalid_marker: Optional[dict[int, int]] = None,
    ) -> None:
        if quiet_ms < 0:
            raise ValueError("quiet_ms must be non-negative")
        parser = self.adapter._parser  # noqa: SLF001
        invalid_before = invalid_marker or self.business_input_marker()
        if set(invalid_before) != {DELIVERY_HEADER, CLEAN_HEADER}:
            raise ValueError("business input marker is invalid")
        deadline = time.monotonic() + quiet_ms / 1000.0
        while time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            event = self.adapter.read_mcu_event(timeout_ms=remaining_ms)
            if event is None:
                continue
            if event.get("message_name") in {
                "COMPAT_DELIVERY_RESULT",
                "COMPAT_CLEAN_RESULT",
            }:
                raise AcceptanceHardwareError("DUPLICATE_OR_LATE_FINAL_RESULT")
            payload = event.get("payload") or {}
            if (
                event.get("message_name") == "SAFETY_SENSOR_EVENT"
                and (
                    payload.get("smokeState") != "NORMAL"
                    or payload.get("smokeSensorHealth") != "OK"
                )
            ):
                raise AcceptanceHardwareError("SAFETY_EVENT_UNHEALTHY")
        if any(
            parser.invalid_count(header) > count
            for header, count in invalid_before.items()
        ) or parser.buffered_length:
            raise AcceptanceHardwareError("CORRUPT_OR_INCOMPLETE_LATE_RESULT")

    def clear_input_for_recovery(self) -> None:
        self.open()
        with self.adapter._foreground_io("FACTORY_RECOVERY_CLEAR"):  # noqa: SLF001
            serial_port = self.adapter._ser  # noqa: SLF001
            if hasattr(serial_port, "reset_input_buffer"):
                serial_port.reset_input_buffer()
            else:
                while int(getattr(serial_port, "in_waiting", 0) or 0) > 0:
                    serial_port.read(
                        min(256, int(getattr(serial_port, "in_waiting", 0)))
                    )
            self.adapter._pending_events.clear()  # noqa: SLF001
            self.adapter._parser.clear()  # noqa: SLF001


class _FactoryWiringBootControl:
    """P7-only BOOT0/NRST driver built on the immutable safe GPIO primitive."""

    def __init__(
        self,
        *,
        gpio_path: str,
        boot0_wpi: int,
        reset_wpi: int,
        command_runner: Callable[..., object] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (boot0_wpi, reset_wpi) != (2, 5):
            raise ValueError("factory BOOT0/NRST pins must be WiringOP 2/5")
        self.boot0_wpi = boot0_wpi
        self.reset_wpi = reset_wpi
        self._gpio = WiringOpGpio(
            gpio_path,
            command_runner=command_runner,
        )
        self._sleep = sleeper

    def enter_system_bootloader(self) -> None:
        self._configure_outputs()
        self._write_and_verify(self.boot0_wpi, 1)
        self._pulse_reset()
        self._sleep(BOOTLOADER_SETTLE_SECONDS)

    def boot_application(self) -> None:
        self._configure_outputs()
        self._write_and_verify(self.boot0_wpi, 0)
        self._pulse_reset()
        self._sleep(APPLICATION_SETTLE_SECONDS)

    def force_application_selection(self) -> None:
        self._configure_outputs()
        self._write_and_verify(self.boot0_wpi, 0)
        self._write_and_verify(self.reset_wpi, 0)

    def _configure_outputs(self) -> None:
        self._run("mode", str(self.boot0_wpi), "out")
        self._run("mode", str(self.reset_wpi), "out")

    def _pulse_reset(self) -> None:
        # The Orange Pi drives the 2N7002 gate: high asserts STM32 NRST low;
        # low releases the gate and lets the board pull-up run the MCU.
        self._write_and_verify(self.reset_wpi, 1)
        self._sleep(RESET_ASSERT_SECONDS)
        self._write_and_verify(self.reset_wpi, 0)

    def _write_and_verify(self, pin: int, level: int) -> None:
        self._run("write", str(pin), str(level))
        observed = self._run("read", str(pin)).strip()
        if observed != str(level):
            raise AcceptanceHardwareError("GPIO_READBACK_MISMATCH")

    def _run(self, *arguments: str) -> str:
        try:
            return self._gpio.run(*arguments)
        except GpioCommandError as error:
            raise AcceptanceHardwareError("GPIO_CONTROL_FAILED") from error


class ReadOnlyStm32RomProbe:
    """Drive BOOT0/NRST and identify the STM32 ROM without any flash write."""

    def __init__(
        self,
        *,
        gpio_path: str,
        boot0_wpi: int,
        reset_wpi: int,
        serial_port: str = "/dev/ttyS5",
        stm32flash_path: str = "/usr/bin/stm32flash",
        timeout_seconds: float = 15.0,
        runner: Callable[..., object] = subprocess.run,
        gpio_runner: Callable[..., object] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not os.path.isabs(stm32flash_path):
            raise ValueError("stm32flash path must be absolute")
        if not os.path.isabs(serial_port):
            raise ValueError("serial port path must be absolute")
        if timeout_seconds <= 0:
            raise ValueError("ROM probe timeout must be positive")
        self.control = _FactoryWiringBootControl(
            gpio_path=gpio_path,
            boot0_wpi=boot0_wpi,
            reset_wpi=reset_wpi,
            command_runner=gpio_runner,
            sleeper=sleeper,
        )
        self.serial_port = serial_port
        self.stm32flash_path = stm32flash_path
        self.timeout_seconds = timeout_seconds
        self._runner = runner

    def enter_system_bootloader(self) -> None:
        self.control.enter_system_bootloader()

    def boot_application(self) -> None:
        self.control.boot_application()

    def force_application_selection(self) -> None:
        self.control.force_application_selection()

    def probe_read_only(self) -> dict:
        arguments = [
            self.stm32flash_path,
            "-b",
            "115200",
            "-m",
            "8e1",
            self.serial_port,
        ]
        # Guard against a future accidental mutation of this command.  A ROM
        # identity probe must never carry erase/write/go/read flags or a file.
        forbidden = {"-e", "-w", "-v", "-g", "-r", "-u", "-j", "-k"}
        if any(argument in forbidden for argument in arguments):
            raise AssertionError("ROM probe command contains a mutating flag")
        try:
            completed = self._runner(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                env={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                },
            )
        except subprocess.TimeoutExpired as error:
            raise AcceptanceHardwareError("STM32_ROM_PROBE_TIMEOUT") from error
        except OSError as error:
            raise AcceptanceHardwareError("STM32_ROM_PROBE_EXEC_FAILED") from error
        if int(getattr(completed, "returncode", -1)) != 0:
            raise AcceptanceHardwareError("STM32_ROM_NOT_DETECTED")
        output = getattr(completed, "stdout", "")
        if (
            not isinstance(output, str)
            or len(output) > MAXIMUM_ROM_PROBE_OUTPUT_CHARACTERS
        ):
            raise AcceptanceHardwareError("STM32_ROM_IDENTITY_INVALID")
        # stm32flash versions differ on whether the leading zero is printed.
        # A successful process exit is not sufficient: the immutable factory
        # wiring is specifically for STM32F103C8 (device ID 0x0410).
        match = re.search(
            r"(?i)\bdevice\s+id\s*:\s*0x0*410\b",
            output,
        )
        if match is None:
            raise AcceptanceHardwareError("STM32_ROM_IDENTITY_INVALID")
        return {
            "detected": True,
            "resultCode": "STM32F103C8_ROM_DETECTED_READ_ONLY",
            "deviceId": STM32F103C8_DEVICE_ID,
        }


class VirtualRomProbe:
    """Adapter for the existing in-memory VirtualFixedFrameMcu test model."""

    def __init__(self, model: object) -> None:
        required = ("enter_system_bootloader", "boot_application")
        if not all(callable(getattr(model, name, None)) for name in required):
            raise TypeError("virtual model does not implement ROM transitions")
        self.model = model
        self.read_only_probe_count = 0

    def enter_system_bootloader(self) -> None:
        self.model.enter_system_bootloader()

    def boot_application(self) -> None:
        self.model.boot_application()

    def force_application_selection(self) -> None:
        # The virtual model has no separate GPIO level; boot_application below
        # performs BOOT0-low + NRST and clears the F2 latch.
        return None

    def probe_read_only(self) -> dict:
        if getattr(self.model, "runtime_mode", None) != "SYSTEM_BOOTLOADER":
            raise AcceptanceHardwareError("STM32_ROM_NOT_DETECTED")
        self.read_only_probe_count += 1
        return {
            "detected": True,
            "resultCode": "STM32F103C8_ROM_DETECTED_READ_ONLY",
            "deviceId": STM32F103C8_DEVICE_ID,
        }


class OpenCvCapture:
    """Capture one local V4L2 frame without importing production PhotoManager."""

    def __call__(self, source: str, destination: Path) -> None:
        if is_simulated_camera_source(source):
            capture_simulated_camera(str(destination), source)
            return
        try:
            capture_v4l2_jpeg(source, destination)
        except CameraCaptureError as error:
            if error.code == OPENCV_NOT_INSTALLED:
                code = "OPENCV_NOT_INSTALLED"
            elif error.code == CAMERA_OPEN_FAILED:
                code = "CAMERA_OPEN_FAILED"
            else:
                code = "CAMERA_CAPTURE_FAILED"
            raise AcceptanceHardwareError(code) from error
        except Exception as error:
            raise AcceptanceHardwareError("CAMERA_CAPTURE_FAILED") from error


class FixedRoleCameraProbe:
    """Capture fixed outside/inside roles only into an isolated temp directory."""

    def __init__(
        self,
        *,
        outside_source: str,
        inside_source: str,
        temporary_directory: Path | str,
        capture: Optional[Callable[[str, Path], None]] = None,
        allow_simulated: bool = False,
        shared_group_readable: bool = False,
        forbidden_roots: tuple[Path | str, ...] = (
            "/var/lib/ecobin/photos",
            "/var/lib/ecobin/hardware/photos",
        ),
    ) -> None:
        if not isinstance(outside_source, str) or not outside_source:
            raise ValueError("outside camera source is required")
        if not isinstance(inside_source, str) or not inside_source:
            raise ValueError("inside camera source is required")
        if outside_source == inside_source:
            raise ValueError("outside and inside cameras must be distinct")
        if not allow_simulated:
            if any(
                is_simulated_camera_source(source)
                for source in (outside_source, inside_source)
            ):
                raise ValueError("production acceptance forbids simulated cameras")
            for source in (outside_source, inside_source):
                if not source.startswith("/dev/v4l/by-id/"):
                    raise ValueError(
                        "factory cameras must use stable /dev/v4l/by-id paths"
                    )
        self.outside_source = outside_source
        self.inside_source = inside_source
        self.temporary_directory = Path(temporary_directory)
        if not self.temporary_directory.is_absolute():
            raise ValueError("camera temporary directory must be absolute")
        temporary_resolved = self.temporary_directory.resolve(strict=False)
        for root in forbidden_roots:
            root_resolved = Path(root).resolve(strict=False)
            if temporary_resolved == root_resolved or root_resolved in temporary_resolved.parents:
                raise ValueError("factory camera temp path overlaps production photos")
        self.capture = capture or OpenCvCapture()
        self.shared_group_readable = shared_group_readable

    @staticmethod
    def _summary(role: str, source: str, byte_count: int) -> dict:
        source_kind = "SIMULATED" if is_simulated_camera_source(source) else "V4L2_BY_ID"
        # Only a short non-reversible configuration fingerprint enters the
        # report; no image bytes or production path is retained.
        fingerprint = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
        return {
            "role": role,
            "sourceKind": source_kind,
            "sourceFingerprint": fingerprint,
            "captureNonEmpty": byte_count > 0,
            "operatorRoleConfirmed": True,
        }

    def _review_path(self, role: str, nonce: str) -> Path:
        if CAMERA_REVIEW_NONCE.fullmatch(nonce) is None:
            raise AcceptanceHardwareError("CAMERA_REVIEW_NONCE_INVALID")
        if role not in {"OUTSIDE", "INSIDE"}:
            raise ValueError("camera role is invalid")
        return self.temporary_directory / f"{role.lower()}-{nonce}.jpg"

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                block = handle.read(64 * 1024)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()

    def capture_pending(self) -> dict:
        """Capture a fresh pair and retain it only for operator review."""

        directory = self.temporary_directory
        if directory.is_symlink():
            raise AcceptanceHardwareError("CAMERA_TEMP_PATH_UNSAFE")
        directory_mode = 0o750 if self.shared_group_readable else 0o700
        file_mode = 0o640 if self.shared_group_readable else 0o600
        directory.mkdir(mode=directory_mode, parents=True, exist_ok=True)
        os.chmod(directory, directory_mode)
        self.discard_all_pending()
        nonce = uuid.uuid4().hex
        files = {
            "OUTSIDE": self._review_path("OUTSIDE", nonce),
            "INSIDE": self._review_path("INSIDE", nonce),
        }
        summaries: dict[str, dict] = {}
        try:
            tasks = {}
            for role, source in (
                ("OUTSIDE", self.outside_source),
                ("INSIDE", self.inside_source),
            ):
                destination = files[role]
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
                tasks[role] = (
                    lambda camera_source=source, path=destination:
                    self.capture(camera_source, path)
                )

            outcomes = run_camera_captures(tasks)
            for role, source in (
                ("OUTSIDE", self.outside_source),
                ("INSIDE", self.inside_source),
            ):
                capture_error = outcomes[role].error
                if capture_error is not None:
                    if isinstance(capture_error, CameraCaptureError):
                        raise AcceptanceHardwareError(
                            "CAMERA_CAPTURE_FAILED"
                        ) from capture_error
                    raise capture_error
                destination = files[role]
                metadata = destination.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
                    raise AcceptanceHardwareError("CAMERA_CAPTURE_EMPTY")
                os.chmod(destination, file_mode)
                summary = self._summary(role, source, metadata.st_size)
                summary["captureSha256"] = self._file_digest(destination)
                summaries[role] = summary
        except BaseException:
            for path in files.values():
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            raise
        finally:
            try:
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                if os.name != "nt":
                    raise
        return {
            "reviewNonce": nonce,
            "outside": summaries["OUTSIDE"],
            "inside": summaries["INSIDE"],
            "resultCode": "WAITING_FOR_CAMERA_ROLE_CONFIRMATION",
        }

    def confirm_pending(self, nonce: str, expected: dict) -> dict:
        """Bind confirmation to the exact current capture, then delete it."""

        if not isinstance(expected, dict):
            raise AcceptanceHardwareError("CAMERA_REVIEW_STATE_INVALID")
        summaries: dict[str, dict] = {}
        try:
            for role, source, key in (
                ("OUTSIDE", self.outside_source, "outside"),
                ("INSIDE", self.inside_source, "inside"),
            ):
                path = self._review_path(role, nonce)
                metadata = path.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size <= 0
                    or self._file_digest(path)
                    != (expected.get(key) or {}).get("captureSha256")
                ):
                    raise AcceptanceHardwareError("CAMERA_REVIEW_CHANGED")
                summaries[key] = self._summary(role, source, metadata.st_size)
        except (FileNotFoundError, OSError) as error:
            raise AcceptanceHardwareError("CAMERA_REVIEW_UNAVAILABLE") from error
        finally:
            self.discard_all_pending()
        return {
            "outside": summaries["outside"],
            "inside": summaries["inside"],
            "resultCode": "DUAL_CAMERA_FIXED_ROLES_PASSED",
        }

    def discard_all_pending(self) -> None:
        directory = self.temporary_directory
        try:
            candidates = list(directory.glob("outside-*.jpg")) + list(
                directory.glob("inside-*.jpg")
            )
        except OSError:
            return
        for path in candidates:
            try:
                if path.parent == directory and not path.is_symlink():
                    path.unlink()
            except FileNotFoundError:
                pass
        if directory.exists():
            try:
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                if os.name != "nt":
                    raise

    def probe(
        self,
        *,
        outside_role_confirmed: bool,
        inside_role_confirmed: bool,
    ) -> dict:
        """Compatibility helper for direct tests; the executor uses two steps."""

        if outside_role_confirmed is not True or inside_role_confirmed is not True:
            raise AcceptanceHardwareError("CAMERA_ROLE_NOT_CONFIRMED")
        pending = self.capture_pending()
        return self.confirm_pending(pending["reviewNonce"], pending)
