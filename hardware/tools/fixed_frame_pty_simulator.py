#!/usr/bin/env python3
"""Linux PTY simulator for the negotiated fixed-length EcoBin MCU protocol.

The simulator exposes a stable symbolic link to a Linux pseudo-terminal.
Run the real ``hardware/main.py`` with ``ECOBIN_SERIAL_PORT`` set to that
link.  Valid delivery and clean start commands are answered with configurable
DD/EF result frames, an F1 sensor snapshot, and an optional CC smoke change.
F2 firmware queries receive a configurable revision-2 F3 identity or update-
prepare result.  The fire-and-forget A0 device-entry URL frame is retained
without a response.
"""

from __future__ import annotations

import argparse
import heapq
import os
import selectors
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

COMMAND_FRAME_LENGTH = 3
DEVICE_ENTRY_URL_FIELD_LENGTH = 192
DEVICE_ENTRY_URL_FRAME_LENGTH = 195
MAXIMUM_WEIGHT_GRAMS = 350_000
MAXIMUM_RECEIVE_BUFFER = 4096

DELIVERY_START_HEADER = 0xAA
PRICE_HEADER = 0xBB
CLEAN_START_HEADER = 0xEE
DELIVERY_RESULT_HEADER = 0xDD
CLEAN_RESULT_HEADER = 0xEF
SELF_TEST_QUERY_HEADER = 0xF0
SELF_TEST_RESPONSE_HEADER = 0xF1
FIRMWARE_QUERY_HEADER = 0xF2
FIRMWARE_STATUS_HEADER = 0xF3
SMOKE_HEADER = 0xCC
DEVICE_ENTRY_URL_HEADER = 0xA0
FIRMWARE_PROTOCOL_REVISION = 2
FIRMWARE_QUERY_IDENTITY_MODE = 1
FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE = 2
FIRMWARE_APPLICATION_SAFE_FLAGS = 0x0F
FIRMWARE_PREPARED_SAFE_FLAGS = 0x1F

DOWNSTREAM_HEADERS = (
    DELIVERY_START_HEADER,
    PRICE_HEADER,
    CLEAN_START_HEADER,
    SELF_TEST_QUERY_HEADER,
    FIRMWARE_QUERY_HEADER,
    DEVICE_ENTRY_URL_HEADER,
)


def _validate_weight(name: str, value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAXIMUM_WEIGHT_GRAMS
    ):
        raise ValueError(
            f"{name} must be an integer in 0..{MAXIMUM_WEIGHT_GRAMS}"
        )


def _validate_binary_flag(name: str, value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value not in (0, 1)
    ):
        raise ValueError(f"{name} must be 0 or 1")


def _validate_smoke_code(name: str, value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value not in (0, 1, 2)
    ):
        raise ValueError(f"{name} must be 0, 1 or 2")


def encode_result_frame(
    header: int,
    pre_weight_grams: int,
    post_weight_grams: int,
    infrared_blocked: int,
) -> bytes:
    """Encode one valid nine-byte DD or EF result frame."""
    if header not in (DELIVERY_RESULT_HEADER, CLEAN_RESULT_HEADER):
        raise ValueError("result header must be DD or EF")
    _validate_weight("pre_weight_grams", pre_weight_grams)
    _validate_weight("post_weight_grams", post_weight_grams)
    _validate_binary_flag("infrared_blocked", infrared_blocked)
    return b"".join(
        (
            bytes((header,)),
            pre_weight_grams.to_bytes(3, "big", signed=False),
            post_weight_grams.to_bytes(3, "big", signed=False),
            bytes((infrared_blocked, header)),
        )
    )


def encode_self_test_frame(
    valid_flags: int,
    weight_grams: int,
    infrared_blocked: int,
    smoke_code: int,
) -> bytes:
    """Encode one valid eight-byte F1 sensor self-test response."""
    if (
        not isinstance(valid_flags, int)
        or isinstance(valid_flags, bool)
        or valid_flags & 0xFC
    ):
        raise ValueError("valid_flags must use only bits 0 and 1")
    _validate_weight("weight_grams", weight_grams)
    _validate_binary_flag("infrared_blocked", infrared_blocked)
    _validate_smoke_code("smoke_code", smoke_code)
    if not (valid_flags & 0x01):
        weight_grams = 0
    if not (valid_flags & 0x02):
        infrared_blocked = 0
    return b"".join(
        (
            bytes((SELF_TEST_RESPONSE_HEADER, valid_flags)),
            weight_grams.to_bytes(3, "big", signed=False),
            bytes(
                (
                    infrared_blocked,
                    smoke_code,
                    SELF_TEST_RESPONSE_HEADER,
                )
            ),
        )
    )


def encode_smoke_frame(smoke_code: int) -> bytes:
    """Encode one three-byte CC smoke state report."""
    _validate_smoke_code("smoke_code", smoke_code)
    return bytes((SMOKE_HEADER, smoke_code, SMOKE_HEADER))


def encode_firmware_status_frame(
    *,
    mode: int,
    status: int,
    firmware_version: str,
    firmware_version_code: int,
    firmware_identity_hex: str,
    safe_flags: int,
) -> bytes:
    """Encode one revision-2 F3 firmware identity/status response."""

    if (
        not isinstance(mode, int)
        or isinstance(mode, bool)
        or mode not in {
            FIRMWARE_QUERY_IDENTITY_MODE,
            FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE,
        }
    ):
        raise ValueError("firmware mode must be 1 or 2")
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or status not in {0, 1, 2, 3}
    ):
        raise ValueError("firmware status must be in 0..3")
    if (
        not isinstance(firmware_version_code, int)
        or isinstance(firmware_version_code, bool)
        or not 1 <= firmware_version_code <= 0xFFFFFFFF
    ):
        raise ValueError("firmware version code must be uint32 and non-zero")
    if not isinstance(firmware_version, str):
        raise ValueError("firmware version must be text")
    try:
        encoded_version = firmware_version.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("firmware version must be printable ASCII") from error
    if (
        not 1 <= len(encoded_version) <= 32
        or any(byte < 0x20 or byte > 0x7E for byte in encoded_version)
    ):
        raise ValueError("firmware version must be 1..32 printable ASCII bytes")
    if (
        not isinstance(firmware_identity_hex, str)
        or len(firmware_identity_hex) != 16
        or any(
            character not in "0123456789abcdef"
            for character in firmware_identity_hex
        )
        or firmware_identity_hex == "0" * 16
    ):
        raise ValueError(
            "firmware identity must be 8 lowercase hex bytes and not all zero"
        )
    if (
        not isinstance(safe_flags, int)
        or isinstance(safe_flags, bool)
        or safe_flags & ~FIRMWARE_PREPARED_SAFE_FLAGS
    ):
        raise ValueError("firmware safe flags must use only bits 0..4")
    identity = bytes.fromhex(firmware_identity_hex)
    return b"".join(
        (
            bytes((FIRMWARE_STATUS_HEADER, mode, status)),
            bytes((FIRMWARE_PROTOCOL_REVISION,)),
            firmware_version_code.to_bytes(4, "big", signed=False),
            bytes((len(encoded_version),)),
            encoded_version.ljust(32, b"\x00"),
            identity,
            bytes((safe_flags, FIRMWARE_STATUS_HEADER)),
        )
    )


class DownstreamFrameParser:
    """Parse noisy, fragmented, or joined Edge-to-MCU frames."""

    def __init__(self, maximum_buffer: int = MAXIMUM_RECEIVE_BUFFER):
        if maximum_buffer < COMMAND_FRAME_LENGTH:
            raise ValueError("maximum buffer is smaller than one command frame")
        self.maximum_buffer = maximum_buffer
        self._buffer = bytearray()

    @property
    def buffered_length(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buffer.extend(data)
        if len(self._buffer) > self.maximum_buffer:
            overflow = len(self._buffer) - self.maximum_buffer
            del self._buffer[:overflow]

        frames: list[bytes] = []
        while self._buffer:
            start = self._next_header_index()
            if start is None:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            frame_length = self._candidate_frame_length()
            if frame_length is None or len(self._buffer) < frame_length:
                break

            candidate = bytes(self._buffer[:frame_length])
            if self._is_valid(candidate):
                frames.append(candidate)
                del self._buffer[:frame_length]
            else:
                del self._buffer[0]
        return frames

    def _candidate_frame_length(self) -> Optional[int]:
        if not self._buffer:
            return None
        if self._buffer[0] == DEVICE_ENTRY_URL_HEADER:
            return DEVICE_ENTRY_URL_FRAME_LENGTH
        return COMMAND_FRAME_LENGTH

    def _next_header_index(self) -> Optional[int]:
        candidates = [
            self._buffer.find(bytes((header,)))
            for header in DOWNSTREAM_HEADERS
        ]
        candidates = [index for index in candidates if index >= 0]
        return min(candidates) if candidates else None

    @staticmethod
    def _is_valid(frame: bytes) -> bool:
        if frame and frame[0] == DEVICE_ENTRY_URL_HEADER:
            if (
                len(frame) != DEVICE_ENTRY_URL_FRAME_LENGTH
                or frame[-1] != DEVICE_ENTRY_URL_HEADER
                or not 1 <= frame[1] <= DEVICE_ENTRY_URL_FIELD_LENGTH
            ):
                return False
            length = frame[1]
            value = frame[2 : 2 + length]
            padding = frame[2 + length : -1]
            return (
                all(0x20 <= byte <= 0x7E for byte in value)
                and value.startswith(b"https://")
                and not any(padding)
            )
        if (
            len(frame) != COMMAND_FRAME_LENGTH
            or frame[0] not in DOWNSTREAM_HEADERS
            or frame[-1] != frame[0]
        ):
            return False
        if frame[0] == PRICE_HEADER:
            return 0 <= frame[1] <= 9
        if frame[0] == FIRMWARE_QUERY_HEADER:
            return frame[1] in {
                FIRMWARE_QUERY_IDENTITY_MODE,
                FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE,
            }
        return frame[1] == 1


@dataclass(frozen=True)
class SimulatorConfig:
    delivery_pre_weight_grams: int = 10_000
    delivery_post_weight_grams: int = 11_200
    delivery_infrared_blocked: int = 0
    clean_pre_weight_grams: int = 11_200
    clean_post_weight_grams: int = 800
    clean_infrared_blocked: int = 0
    self_test_weight_grams: int = 10_000
    self_test_weight_valid: int = 1
    self_test_infrared_blocked: int = 0
    self_test_infrared_valid: int = 1
    smoke_state: int = 0
    smoke_change_to: Optional[int] = None
    firmware_version: str = "1.0.0"
    firmware_version_code: int = 10_000
    firmware_identity_hex: str = "0102030405060708"
    firmware_identity_status: int = 0
    firmware_identity_dropped_responses: int = 0
    firmware_prepare_status: int = 0
    firmware_prepare_safe_flags: int = FIRMWARE_PREPARED_SAFE_FLAGS
    firmware_prepare_dropped_responses: int = 0
    response_delay_ms: int = 500
    delivery_result_delay_ms: int = 40_000
    clean_result_delay_ms: int = 40_000

    def __post_init__(self) -> None:
        _validate_weight(
            "delivery_pre_weight_grams",
            self.delivery_pre_weight_grams,
        )
        _validate_weight(
            "delivery_post_weight_grams",
            self.delivery_post_weight_grams,
        )
        _validate_binary_flag(
            "delivery_infrared_blocked",
            self.delivery_infrared_blocked,
        )
        _validate_weight(
            "clean_pre_weight_grams",
            self.clean_pre_weight_grams,
        )
        _validate_weight(
            "clean_post_weight_grams",
            self.clean_post_weight_grams,
        )
        _validate_binary_flag(
            "clean_infrared_blocked",
            self.clean_infrared_blocked,
        )
        _validate_weight(
            "self_test_weight_grams",
            self.self_test_weight_grams,
        )
        _validate_binary_flag(
            "self_test_weight_valid",
            self.self_test_weight_valid,
        )
        _validate_binary_flag(
            "self_test_infrared_blocked",
            self.self_test_infrared_blocked,
        )
        _validate_binary_flag(
            "self_test_infrared_valid",
            self.self_test_infrared_valid,
        )
        _validate_smoke_code("smoke_state", self.smoke_state)
        if self.smoke_change_to is not None:
            _validate_smoke_code("smoke_change_to", self.smoke_change_to)
        encode_firmware_status_frame(
            mode=FIRMWARE_QUERY_IDENTITY_MODE,
            status=self.firmware_identity_status,
            firmware_version=self.firmware_version,
            firmware_version_code=self.firmware_version_code,
            firmware_identity_hex=self.firmware_identity_hex,
            safe_flags=FIRMWARE_APPLICATION_SAFE_FLAGS,
        )
        encode_firmware_status_frame(
            mode=FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE,
            status=self.firmware_prepare_status,
            firmware_version=self.firmware_version,
            firmware_version_code=self.firmware_version_code,
            firmware_identity_hex=self.firmware_identity_hex,
            safe_flags=self.firmware_prepare_safe_flags,
        )
        for name, value in (
            (
                "firmware_identity_dropped_responses",
                self.firmware_identity_dropped_responses,
            ),
            (
                "firmware_prepare_dropped_responses",
                self.firmware_prepare_dropped_responses,
            ),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{name} must be non-negative")
        if (
            not isinstance(self.response_delay_ms, int)
            or isinstance(self.response_delay_ms, bool)
            or self.response_delay_ms < 0
        ):
            raise ValueError("response_delay_ms must be a non-negative integer")
        for name, value in (
            ("delivery_result_delay_ms", self.delivery_result_delay_ms),
            ("clean_result_delay_ms", self.clean_result_delay_ms),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")

    def response_delay_for(self, request_name: str) -> int:
        """Return the configured delay for one simulated MCU response."""
        if request_name == "DELIVERY_START":
            return self.delivery_result_delay_ms
        if request_name == "CLEAN_START":
            return self.clean_result_delay_ms
        return self.response_delay_ms


class VirtualFixedFrameMcu:
    """State and response logic independent from the Linux PTY transport."""

    def __init__(self, config: SimulatorConfig):
        self.config = config
        self.last_price_digit: Optional[int] = None
        self.delivery_start_count = 0
        self.clean_start_count = 0
        self.self_test_query_count = 0
        self.device_entry_url_count = 0
        self.device_entry_url: Optional[str] = None
        self.smoke_state = config.smoke_state
        self.firmware_identity_query_count = 0
        self.firmware_prepare_count = 0
        self.update_prepared = False
        self.runtime_mode = "APPLICATION"
        self.firmware_version = config.firmware_version
        self.firmware_version_code = config.firmware_version_code
        self.firmware_identity_hex = config.firmware_identity_hex
        self._self_test_forced_failure = False
        self._firmware_identity_status = config.firmware_identity_status
        self._firmware_identity_drops_remaining = (
            config.firmware_identity_dropped_responses
        )
        self._firmware_prepare_drops_remaining = (
            config.firmware_prepare_dropped_responses
        )

    def handle_frame(self, frame: bytes) -> tuple[str, Optional[bytes]]:
        if not DownstreamFrameParser._is_valid(frame):
            raise ValueError("invalid downstream fixed frame")
        if self.runtime_mode != "APPLICATION":
            return "FRAME_IGNORED_SYSTEM_BOOTLOADER", None

        if frame[0] == FIRMWARE_QUERY_HEADER:
            mode = frame[1]
            if mode == FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE:
                self.firmware_prepare_count += 1
                self.update_prepared = True
                if self._firmware_prepare_drops_remaining > 0:
                    self._firmware_prepare_drops_remaining -= 1
                    return "FIRMWARE_PREPARE_RESPONSE_DROPPED", None
                return (
                    "FIRMWARE_PREPARE",
                    encode_firmware_status_frame(
                        mode=mode,
                        status=self.config.firmware_prepare_status,
                        firmware_version=self.firmware_version,
                        firmware_version_code=self.firmware_version_code,
                        firmware_identity_hex=self.firmware_identity_hex,
                        safe_flags=self.config.firmware_prepare_safe_flags,
                    ),
                )
            self.firmware_identity_query_count += 1
            if self._firmware_identity_drops_remaining > 0:
                self._firmware_identity_drops_remaining -= 1
                return "FIRMWARE_IDENTITY_RESPONSE_DROPPED", None
            return (
                "FIRMWARE_IDENTITY",
                encode_firmware_status_frame(
                    mode=mode,
                    status=self._firmware_identity_status,
                    firmware_version=self.firmware_version,
                    firmware_version_code=self.firmware_version_code,
                    firmware_identity_hex=self.firmware_identity_hex,
                    safe_flags=(
                        FIRMWARE_PREPARED_SAFE_FLAGS
                        if self.update_prepared
                        else FIRMWARE_APPLICATION_SAFE_FLAGS
                    ),
                ),
            )

        if self.update_prepared and frame[0] in {
            DELIVERY_START_HEADER,
            PRICE_HEADER,
            CLEAN_START_HEADER,
            DEVICE_ENTRY_URL_HEADER,
        }:
            ignored_names = {
                DELIVERY_START_HEADER: "DELIVERY_IGNORED_UPDATE_PREPARED",
                PRICE_HEADER: "PRICE_IGNORED_UPDATE_PREPARED",
                CLEAN_START_HEADER: "CLEAN_IGNORED_UPDATE_PREPARED",
                DEVICE_ENTRY_URL_HEADER: "URL_IGNORED_UPDATE_PREPARED",
            }
            return ignored_names[frame[0]], None

        if frame[0] == PRICE_HEADER:
            self.last_price_digit = frame[1]
            return "PRICE", None
        if frame[0] == DEVICE_ENTRY_URL_HEADER:
            length = frame[1]
            self.device_entry_url = frame[2 : 2 + length].decode("ascii")
            self.device_entry_url_count += 1
            return "DEVICE_ENTRY_URL", None
        if frame[0] == DELIVERY_START_HEADER:
            self.delivery_start_count += 1
            return (
                "DELIVERY_START",
                encode_result_frame(
                    DELIVERY_RESULT_HEADER,
                    self.config.delivery_pre_weight_grams,
                    self.config.delivery_post_weight_grams,
                    self.config.delivery_infrared_blocked,
                ),
            )

        if frame[0] == SELF_TEST_QUERY_HEADER:
            self.self_test_query_count += 1
            valid_flags = (
                0
                if self._self_test_forced_failure
                else (
                    self.config.self_test_weight_valid
                    | (self.config.self_test_infrared_valid << 1)
                )
            )
            return (
                "SELF_TEST",
                encode_self_test_frame(
                    valid_flags,
                    self.config.self_test_weight_grams,
                    self.config.self_test_infrared_blocked,
                    self.smoke_state,
                ),
            )

        self.clean_start_count += 1
        return (
            "CLEAN_START",
            encode_result_frame(
                CLEAN_RESULT_HEADER,
                self.config.clean_pre_weight_grams,
                self.config.clean_post_weight_grams,
                self.config.clean_infrared_blocked,
            ),
        )

    def enter_system_bootloader(self) -> None:
        """Simulate BOOT0-high reset into the STM32 ROM bootloader."""

        self.runtime_mode = "SYSTEM_BOOTLOADER"

    def install_firmware(self, manifest: dict) -> None:
        """Install one verified manifest while the ROM bootloader is active."""

        if self.runtime_mode != "SYSTEM_BOOTLOADER":
            raise RuntimeError("simulated MCU is not in the system bootloader")
        if not isinstance(manifest, dict) or manifest.get(
            "fixedFrameRevision"
        ) != FIRMWARE_PROTOCOL_REVISION:
            raise ValueError("simulated firmware must declare revision 2")
        version = manifest.get("firmwareVersion")
        version_code = manifest.get("firmwareVersionCode")
        identity_hex = manifest.get("firmwareIdentityHex")
        encode_firmware_status_frame(
            mode=FIRMWARE_QUERY_IDENTITY_MODE,
            status=0,
            firmware_version=version,
            firmware_version_code=version_code,
            firmware_identity_hex=identity_hex,
            safe_flags=FIRMWARE_APPLICATION_SAFE_FLAGS,
        )
        self.firmware_version = version
        self.firmware_version_code = version_code
        self.firmware_identity_hex = identity_hex

    def boot_application(self) -> None:
        """Simulate BOOT0-low reset into the installed application."""

        self.runtime_mode = "APPLICATION"
        self.update_prepared = False

    def set_self_test_failure(self, enabled: bool) -> None:
        """Force F1 to report invalid weight/infrared for fault injection."""

        if not isinstance(enabled, bool):
            raise ValueError("self-test failure flag must be boolean")
        self._self_test_forced_failure = enabled

    def configure_firmware_identity_fault(
        self,
        *,
        status: int = 0,
        dropped_responses: int = 0,
    ) -> None:
        """Replace F2 mode-1 status/drop behavior for the next queries."""

        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or status not in {0, 1, 2, 3}
        ):
            raise ValueError("firmware identity status must be in 0..3")
        if (
            not isinstance(dropped_responses, int)
            or isinstance(dropped_responses, bool)
            or dropped_responses < 0
        ):
            raise ValueError(
                "firmware identity dropped responses must be non-negative"
            )
        self._firmware_identity_status = status
        self._firmware_identity_drops_remaining = dropped_responses

    def change_smoke_state(self, smoke_code: int) -> Optional[bytes]:
        """Return a CC frame only when the configured smoke state changes."""
        _validate_smoke_code("smoke_code", smoke_code)
        if smoke_code == self.smoke_state:
            return None
        self.smoke_state = smoke_code
        return encode_smoke_frame(smoke_code)


def _hex_bytes(data: bytes) -> str:
    return " ".join(f"{value:02X}" for value in data)


def _default_log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp} {message}", flush=True)


class VirtualFixedFrameSerial:
    """PySerial-compatible in-memory transport backed by one virtual MCU."""

    def __init__(
        self,
        model: VirtualFixedFrameMcu,
        *,
        timeout: float = 0.5,
    ):
        self.model = model
        self.timeout = timeout
        self.is_open = True
        self.writes: list[bytes] = []
        self._parser = DownstreamFrameParser()
        self._received = bytearray()
        self._condition = threading.Condition()

    @property
    def in_waiting(self) -> int:
        with self._condition:
            return len(self._received)

    def write(self, data: bytes) -> int:
        wire = bytes(data)
        with self._condition:
            if not self.is_open:
                raise IOError("virtual MCU serial port is closed")
            self.writes.append(wire)
            for frame in self._parser.feed(wire):
                _, response = self.model.handle_frame(frame)
                if response is not None:
                    self._received.extend(response)
            self._condition.notify_all()
        return len(wire)

    def read(self, size: int) -> bytes:
        if size <= 0:
            return b""
        with self._condition:
            if not self._received and self.is_open:
                self._condition.wait(timeout=max(0.0, float(self.timeout)))
            chunk = bytes(self._received[:size])
            del self._received[:size]
            return chunk

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        with self._condition:
            self._received.clear()

    def close(self) -> None:
        with self._condition:
            self.is_open = False
            self._condition.notify_all()


class VirtualFixedFrameSerialFactory:
    """Open fresh serial sessions while retaining the same simulated MCU."""

    def __init__(self, model: VirtualFixedFrameMcu):
        self.model = model
        self.sessions: list[VirtualFixedFrameSerial] = []

    def __call__(self, **kwargs) -> VirtualFixedFrameSerial:
        serial_session = VirtualFixedFrameSerial(
            self.model,
            timeout=float(kwargs.get("timeout", 0.5)),
        )
        self.sessions.append(serial_session)
        return serial_session


class LinuxPtyFixedFrameSimulator:
    """Own a Linux PTY and exchange frames with the real serial adapter."""

    def __init__(
        self,
        config: SimulatorConfig,
        link_path: Path,
        *,
        exit_after_responses: Optional[int] = None,
        log: Callable[[str], None] = _default_log,
    ):
        if exit_after_responses is not None and exit_after_responses <= 0:
            raise ValueError("exit_after_responses must be positive")
        self.config = config
        self.link_path = Path(link_path)
        self.exit_after_responses = exit_after_responses
        self.log = log
        self.model = VirtualFixedFrameMcu(config)
        self._parser = DownstreamFrameParser()
        self._stop_event = threading.Event()
        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self._slave_path: Optional[str] = None
        self._smoke_change_scheduled = False

    @property
    def slave_path(self) -> Optional[str]:
        return self._slave_path

    @property
    def is_open(self) -> bool:
        return self._master_fd is not None

    def open(self) -> str:
        if not sys.platform.startswith("linux"):
            raise RuntimeError(
                "Linux PTY support is required; run this simulator on Linux"
            )
        if self.is_open:
            return str(self.link_path)

        import pty
        import tty

        master_fd, slave_fd = pty.openpty()
        try:
            tty.setraw(slave_fd)
            slave_path = os.ttyname(slave_fd)
            self._install_link(slave_path)
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise

        self._master_fd = master_fd
        self._slave_fd = slave_fd
        self._slave_path = slave_path
        self._stop_event.clear()
        self.log(
            "READY "
            f"slave={slave_path} link={self.link_path} "
            "protocol=fixed-frame"
        )
        return str(self.link_path)

    def _install_link(self, slave_path: str) -> None:
        parent = self.link_path.parent
        if not parent.is_dir():
            raise FileNotFoundError(
                f"PTY link parent directory does not exist: {parent}"
            )
        if os.path.lexists(self.link_path) and not self.link_path.is_symlink():
            raise FileExistsError(
                f"refusing to replace non-symlink path: {self.link_path}"
            )

        temporary_link = parent / (
            f".{self.link_path.name}.{os.getpid()}."
            f"{time.time_ns()}.tmp"
        )
        try:
            os.symlink(slave_path, temporary_link)
            os.replace(temporary_link, self.link_path)
        finally:
            if temporary_link.is_symlink():
                temporary_link.unlink()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if self._master_fd is None:
            raise RuntimeError("simulator is not open")

        pending: list[tuple[float, int, str, bytes]] = []
        order = 0
        responses_sent = 0
        selector = selectors.DefaultSelector()
        selector.register(self._master_fd, selectors.EVENT_READ)
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                while pending and pending[0][0] <= now:
                    _, _, result_name, response = heapq.heappop(pending)
                    self._write_response(result_name, response)
                    responses_sent += 1
                    if (
                        self.exit_after_responses is not None
                        and responses_sent >= self.exit_after_responses
                    ):
                        self._stop_event.set()
                        break
                if self._stop_event.is_set():
                    break

                timeout = 0.25
                if pending:
                    timeout = min(
                        timeout,
                        max(0.0, pending[0][0] - time.monotonic()),
                    )
                for _, _ in selector.select(timeout):
                    chunk = os.read(self._master_fd, 4096)
                    for frame in self._parser.feed(chunk):
                        name, response = self.model.handle_frame(frame)
                        self._log_received(name, frame)
                        if response is not None:
                            order += 1
                            due = (
                                time.monotonic()
                                + self.config.response_delay_for(name) / 1000.0
                            )
                            heapq.heappush(
                                pending,
                                (due, order, f"{name}_RESULT", response),
                            )
                            if (
                                name == "SELF_TEST"
                                and not self._smoke_change_scheduled
                                and self.config.smoke_change_to is not None
                            ):
                                self._smoke_change_scheduled = True
                                smoke_response = self.model.change_smoke_state(
                                    self.config.smoke_change_to
                                )
                                if smoke_response is not None:
                                    order += 1
                                    heapq.heappush(
                                        pending,
                                        (
                                            due + 0.001,
                                            order,
                                            "SMOKE_CHANGED",
                                            smoke_response,
                                        ),
                                    )
        finally:
            selector.close()

    def _log_received(self, name: str, frame: bytes) -> None:
        details = ""
        if name == "PRICE":
            details = (
                f" digit={self.model.last_price_digit} "
                f"displayYuanPerKg=0.{self.model.last_price_digit}"
            )
        elif name == "DEVICE_ENTRY_URL":
            details = f" url={self.model.device_entry_url}"
        elif frame[0] == FIRMWARE_QUERY_HEADER:
            details = (
                f" mode={frame[1]} prepared={self.model.update_prepared}"
            )
        self.log(f"RX {name} frame={_hex_bytes(frame)}{details}")

    def _write_response(self, name: str, response: bytes) -> None:
        if self._master_fd is None:
            raise RuntimeError("simulator is closed")
        written = os.write(self._master_fd, response)
        if written != len(response):
            raise IOError(
                "short PTY write: "
                f"expected={len(response)} actual={written}"
            )
        if response[0] in (DELIVERY_RESULT_HEADER, CLEAN_RESULT_HEADER):
            pre_weight = int.from_bytes(response[1:4], "big", signed=False)
            post_weight = int.from_bytes(response[4:7], "big", signed=False)
            details = (
                f" preGrams={pre_weight} postGrams={post_weight} "
                f"infraredBlocked={response[7]}"
            )
        elif response[0] == SELF_TEST_RESPONSE_HEADER:
            weight = int.from_bytes(response[2:5], "big", signed=False)
            details = (
                f" validFlags={response[1]} weightGrams={weight} "
                f"infraredBlocked={response[5]} smoke={response[6]}"
            )
        elif response[0] == FIRMWARE_STATUS_HEADER:
            version_length = response[8]
            version = response[9 : 9 + version_length].decode("ascii")
            version_code = int.from_bytes(response[4:8], "big", signed=False)
            details = (
                f" mode={response[1]} status={response[2]} "
                f"revision={response[3]} version={version} "
                f"versionCode={version_code} "
                f"identity={response[41:49].hex()} "
                f"safeFlags=0x{response[49]:02X}"
            )
        else:
            details = f" smoke={response[1]}"
        self.log(f"TX {name} frame={_hex_bytes(response)}{details}")

    def close(self) -> None:
        self.stop()
        slave_path = self._slave_path
        if (
            slave_path is not None
            and self.link_path.is_symlink()
            and self._link_target() == os.path.abspath(slave_path)
        ):
            self.link_path.unlink()
        for attribute in ("_master_fd", "_slave_fd"):
            descriptor = getattr(self, attribute)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attribute, None)
        self._slave_path = None

    def _link_target(self) -> str:
        target = os.readlink(self.link_path)
        if not os.path.isabs(target):
            target = os.path.join(self.link_path.parent, target)
        return os.path.abspath(target)

    def __enter__(self) -> "LinuxPtyFixedFrameSimulator":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _weight_argument(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= MAXIMUM_WEIGHT_GRAMS:
        raise argparse.ArgumentTypeError(
            f"weight must be in 0..{MAXIMUM_WEIGHT_GRAMS} grams"
        )
    return parsed


def _non_negative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _positive_uint32(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("value must be in 1..4294967295")
    return parsed


def _firmware_version_argument(value: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise argparse.ArgumentTypeError(
            "firmware version must be printable ASCII"
        ) from error
    if (
        not 1 <= len(encoded) <= 32
        or any(byte < 0x20 or byte > 0x7E for byte in encoded)
    ):
        raise argparse.ArgumentTypeError(
            "firmware version must be 1..32 printable ASCII bytes"
        )
    return value


def _firmware_identity_argument(value: str) -> str:
    if (
        len(value) != 16
        or value == "0" * 16
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise argparse.ArgumentTypeError(
            "firmware identity must be 16 lowercase hex characters and not all zero"
        )
    return value


def _firmware_safe_flags_argument(value: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "firmware safe flags must be an integer"
        ) from error
    if not 0 <= parsed <= FIRMWARE_PREPARED_SAFE_FLAGS:
        raise argparse.ArgumentTypeError(
            "firmware safe flags must use only bits 0..4"
        )
    return parsed


def parse_args(
    arguments: Optional[Sequence[str]] = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Full-chain instructions: "
            "tools/FIXED-FRAME-PTY-SIMULATOR.md"
        ),
    )
    parser.add_argument(
        "--link",
        type=Path,
        default=Path("/tmp/ecobin-fixed-frame-mcu"),
        help="stable symlink exposed as the serial port",
    )
    parser.add_argument(
        "--delivery-pre-grams",
        type=_weight_argument,
        default=10_000,
    )
    parser.add_argument(
        "--delivery-post-grams",
        type=_weight_argument,
        default=11_200,
    )
    parser.add_argument(
        "--delivery-full",
        type=int,
        choices=(0, 1),
        default=0,
        help="DD raw infrared flag: 0=clear, 1=blocked",
    )
    parser.add_argument(
        "--clean-pre-grams",
        type=_weight_argument,
        default=11_200,
    )
    parser.add_argument(
        "--clean-post-grams",
        type=_weight_argument,
        default=800,
    )
    parser.add_argument(
        "--clean-full",
        type=int,
        choices=(0, 1),
        default=0,
        help="EF raw infrared flag: 0=clear, 1=blocked",
    )
    parser.add_argument(
        "--self-test-weight-grams",
        type=_weight_argument,
        default=10_000,
    )
    parser.add_argument(
        "--self-test-weight-valid",
        type=int,
        choices=(0, 1),
        default=1,
    )
    parser.add_argument(
        "--self-test-full",
        type=int,
        choices=(0, 1),
        default=0,
        help="F1 raw infrared flag: 0=clear, 1=blocked",
    )
    parser.add_argument(
        "--self-test-full-valid",
        type=int,
        choices=(0, 1),
        default=1,
    )
    parser.add_argument(
        "--smoke-state",
        type=int,
        choices=(0, 1, 2),
        default=0,
        help="F1 smoke state: 0=normal, 1=alarm, 2=unavailable",
    )
    parser.add_argument(
        "--smoke-change-to",
        type=int,
        choices=(0, 1, 2),
        help="send one CC change immediately after the first F1 response",
    )
    parser.add_argument(
        "--firmware-version",
        type=_firmware_version_argument,
        default="1.0.0",
        help="version text returned by F3",
    )
    parser.add_argument(
        "--firmware-version-code",
        type=_positive_uint32,
        default=10_000,
        help="monotonic firmware version code returned by F3",
    )
    parser.add_argument(
        "--firmware-identity-hex",
        type=_firmware_identity_argument,
        default="0102030405060708",
        help="eight-byte firmware build identity returned by F3",
    )
    parser.add_argument(
        "--firmware-identity-status",
        type=int,
        choices=(0, 1, 2, 3),
        default=0,
        help="F3 status for F2 mode 1 identity queries",
    )
    parser.add_argument(
        "--firmware-identity-drop-responses",
        type=_non_negative_integer,
        default=0,
        help="drop this many initial F2 mode 1 responses",
    )
    parser.add_argument(
        "--firmware-prepare-status",
        type=int,
        choices=(0, 1, 2, 3),
        default=0,
        help="F3 status after F2 mode 2 stop-and-latch execution",
    )
    parser.add_argument(
        "--firmware-prepare-safe-flags",
        type=_firmware_safe_flags_argument,
        default=FIRMWARE_PREPARED_SAFE_FLAGS,
        help="F3 safe flags after F2 mode 2; accepts decimal or 0x hex",
    )
    parser.add_argument(
        "--firmware-prepare-drop-responses",
        type=_non_negative_integer,
        default=0,
        help="execute F2 mode 2 but drop this many initial F3 responses",
    )
    parser.add_argument(
        "--response-delay-ms",
        type=_non_negative_integer,
        default=500,
        help=(
            "delay before F1/F3 responses and an optional following "
            "CC smoke change"
        ),
    )
    parser.add_argument(
        "--delivery-result-delay-ms",
        type=_non_negative_integer,
        default=40_000,
        help="delay before the DD delivery result",
    )
    parser.add_argument(
        "--clean-result-delay-ms",
        type=_non_negative_integer,
        default=40_000,
        help="delay before the EF clean result",
    )
    parser.add_argument(
        "--exit-after-responses",
        type=int,
        help=(
            "exit after this many F1/F3/DD/EF/CC responses; "
            "default keeps running"
        ),
    )
    parsed = parser.parse_args(arguments)
    if (
        parsed.exit_after_responses is not None
        and parsed.exit_after_responses <= 0
    ):
        parser.error("--exit-after-responses must be positive")
    return parsed


def main(arguments: Optional[Sequence[str]] = None) -> int:
    args = parse_args(arguments)
    if not sys.platform.startswith("linux"):
        print(
            "error: fixed-frame PTY simulator must run on Linux",
            file=sys.stderr,
        )
        return 2

    config = SimulatorConfig(
        delivery_pre_weight_grams=args.delivery_pre_grams,
        delivery_post_weight_grams=args.delivery_post_grams,
        delivery_infrared_blocked=args.delivery_full,
        clean_pre_weight_grams=args.clean_pre_grams,
        clean_post_weight_grams=args.clean_post_grams,
        clean_infrared_blocked=args.clean_full,
        self_test_weight_grams=args.self_test_weight_grams,
        self_test_weight_valid=args.self_test_weight_valid,
        self_test_infrared_blocked=args.self_test_full,
        self_test_infrared_valid=args.self_test_full_valid,
        smoke_state=args.smoke_state,
        smoke_change_to=args.smoke_change_to,
        firmware_version=args.firmware_version,
        firmware_version_code=args.firmware_version_code,
        firmware_identity_hex=args.firmware_identity_hex,
        firmware_identity_status=args.firmware_identity_status,
        firmware_identity_dropped_responses=(
            args.firmware_identity_drop_responses
        ),
        firmware_prepare_status=args.firmware_prepare_status,
        firmware_prepare_safe_flags=args.firmware_prepare_safe_flags,
        firmware_prepare_dropped_responses=(
            args.firmware_prepare_drop_responses
        ),
        response_delay_ms=args.response_delay_ms,
        delivery_result_delay_ms=args.delivery_result_delay_ms,
        clean_result_delay_ms=args.clean_result_delay_ms,
    )
    simulator = LinuxPtyFixedFrameSimulator(
        config,
        args.link,
        exit_after_responses=args.exit_after_responses,
    )

    def stop_simulator(signum, frame) -> None:
        del signum, frame
        simulator.stop()

    signal.signal(signal.SIGINT, stop_simulator)
    signal.signal(signal.SIGTERM, stop_simulator)
    try:
        simulator.open()
        simulator.run()
        return 0
    except Exception as error:
        print(
            f"error: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        simulator.close()


if __name__ == "__main__":
    raise SystemExit(main())
