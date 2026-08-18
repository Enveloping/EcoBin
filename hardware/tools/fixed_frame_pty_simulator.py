#!/usr/bin/env python3
"""Linux PTY simulator for the negotiated fixed-length EcoBin MCU protocol.

The simulator exposes a stable symbolic link to a Linux pseudo-terminal.
Run the real ``hardware/main.py`` with ``ECOBIN_SERIAL_PORT`` set to that
link.  Valid delivery and clean start commands are answered with configurable
DD/EF result frames, an F1 sensor snapshot, and an optional CC smoke change.
The fire-and-forget A0 device-entry URL frame is retained without a response.
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
SMOKE_HEADER = 0xCC
DEVICE_ENTRY_URL_HEADER = 0xA0

DOWNSTREAM_HEADERS = (
    DELIVERY_START_HEADER,
    PRICE_HEADER,
    CLEAN_START_HEADER,
    SELF_TEST_QUERY_HEADER,
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

    def handle_frame(self, frame: bytes) -> tuple[str, Optional[bytes]]:
        if not DownstreamFrameParser._is_valid(frame):
            raise ValueError("invalid downstream fixed frame")

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
                self.config.self_test_weight_valid
                | (self.config.self_test_infrared_valid << 1)
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
        "--response-delay-ms",
        type=_non_negative_integer,
        default=500,
        help=(
            "delay before an F1 self-test response and its optional "
            "following CC smoke change"
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
        help="exit after this many F1/DD/EF/CC responses; default keeps running",
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
