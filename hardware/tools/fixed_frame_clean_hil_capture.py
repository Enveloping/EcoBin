"""Capture one controlled fixed-frame MCU cleaning HIL session.

The caller owns service isolation and restoration.  This script validates the
expected firmware and idle sensor state before it writes the single cleaning
start command.  Every received UART chunk is copied to a binary file, while
stdout contains timestamped TX/RX and decoded-frame evidence.
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path

import serial


FIRMWARE_VERSION = "1.0.1-hil.4"
FIRMWARE_VERSION_CODE = 10004
FIRMWARE_IDENTITY = "391ce0b83076c981"

F3_REQUEST = bytes((0xF2, 0x01, 0xF2))
F1_REQUEST = bytes((0xF0, 0x01, 0xF0))
CLEAN_START = bytes((0xEE, 0x01, 0xEE))

FRAME_LENGTHS = {
    0xF3: 51,
    0xF1: 8,
    0xEF: 9,
    0xCC: 3,
    0xDD: 9,
    0xAA: 3,
    0xBB: 3,
}


class Capture:
    def __init__(self, raw_output: Path) -> None:
        self.started = time.monotonic()
        self.raw_file = raw_output.open("xb")

    def close(self) -> None:
        self.raw_file.close()

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def log(self, message: str) -> None:
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="milliseconds"
        )
        print(f"{stamp} t={self.elapsed():.3f} {message}", flush=True)

    def transmit(self, uart: serial.Serial, payload: bytes, label: str) -> None:
        self.log(f"TX label={label} raw={payload.hex(' ').upper()}")
        uart.write(payload)
        uart.flush()

    def receive_into(
        self,
        uart: serial.Serial,
        buffer: bytearray,
        duration_seconds: float,
    ) -> None:
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            chunk = uart.read(256)
            if chunk:
                self.raw_file.write(chunk)
                self.raw_file.flush()
                buffer.extend(chunk)
                self.log(f"RX_CHUNK raw={chunk.hex(' ').upper()}")
            else:
                time.sleep(0.005)

    def collect_query(
        self,
        uart: serial.Serial,
        request: bytes,
        marker: int,
        length: int,
        label: str,
        duration_seconds: float,
    ) -> bytes:
        stale = uart.read(uart.in_waiting or 0)
        if stale:
            self.raw_file.write(stale)
            self.raw_file.flush()
            self.log(
                f"RX_STALE_BEFORE_{label} raw={stale.hex(' ').upper()}"
            )

        self.transmit(uart, request, label)
        query_buffer = bytearray()
        self.receive_into(uart, query_buffer, duration_seconds)
        for index in range(0, len(query_buffer) - length + 1):
            candidate = bytes(query_buffer[index : index + length])
            if candidate[0] == marker and candidate[-1] == marker:
                frame_label = label.removesuffix("_QUERY")
                self.log(
                    f"RX_FRAME type={frame_label} "
                    f"raw={candidate.hex(' ').upper()}"
                )
                return candidate
        raise RuntimeError(f"{label}_FRAME_NOT_FOUND")

    def extract_frames(self, buffer: bytearray) -> list[bytes]:
        frames: list[bytes] = []
        while buffer:
            positions = [
                buffer.find(bytes((marker,))) for marker in FRAME_LENGTHS
            ]
            positions = [position for position in positions if position >= 0]
            if not positions:
                self.log(f"RX_UNFRAMED raw={bytes(buffer).hex(' ').upper()}")
                buffer.clear()
                break

            first = min(positions)
            if first:
                self.log(
                    f"RX_UNFRAMED raw={bytes(buffer[:first]).hex(' ').upper()}"
                )
                del buffer[:first]

            marker = buffer[0]
            length = FRAME_LENGTHS[marker]
            if len(buffer) < length:
                break

            candidate = bytes(buffer[:length])
            if candidate[-1] != marker:
                self.log(f"RX_REJECTED_START byte={marker:02X}")
                del buffer[0]
                continue

            del buffer[:length]
            frames.append(candidate)
        return frames


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyS5")
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    return parser.parse_args()


def _validate_precheck(capture: Capture, f3: bytes, f1: bytes) -> int:
    version = f3[9 : 9 + f3[8]].decode("ascii", errors="strict")
    version_code = int.from_bytes(f3[4:8], "big")
    identity = f3[41:49].hex()
    flags = f3[49]
    capture.log(
        "PRECHECK_F3 "
        f"mode={f3[1]:02X} status={f3[2]:02X} revision={f3[3]} "
        f"version_code={version_code} version={version} "
        f"identity={identity} flags={flags:02X}"
    )
    if not (
        f3[1] == 0x01
        and f3[2] == 0x00
        and f3[3] == 0x02
        and version_code == FIRMWARE_VERSION_CODE
        and version == FIRMWARE_VERSION
        and identity == FIRMWARE_IDENTITY
        and flags == 0x0F
    ):
        raise RuntimeError("PRECHECK_F3_MISMATCH")

    weight = int.from_bytes(f1[2:5], "big")
    capture.log(
        "PRECHECK_F1 "
        f"valid={f1[1]:02X} weight_g={weight} "
        f"full={f1[5]:02X} smoke={f1[6]:02X}"
    )
    if not ((f1[1] & 0x01) and weight <= 350000):
        raise RuntimeError("PRECHECK_F1_INVALID")
    return weight


def _run_capture(arguments: argparse.Namespace) -> None:
    capture = Capture(arguments.raw_output)
    try:
        capture.log(f"SESSION_START raw_path={arguments.raw_output}")
        with serial.Serial(
            arguments.port,
            115200,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.03,
            write_timeout=1.0,
        ) as uart:
            uart.reset_input_buffer()
            uart.reset_output_buffer()

            f3 = capture.collect_query(
                uart, F3_REQUEST, 0xF3, 51, "F3_QUERY", 1.0
            )
            f1 = capture.collect_query(
                uart, F1_REQUEST, 0xF1, 8, "F1_QUERY", 1.0
            )
            pre_weight = _validate_precheck(capture, f3, f1)

            uart.reset_input_buffer()
            capture.transmit(uart, CLEAN_START, "CLEAN_START_ONCE")
            capture.log(f"CLEAN_START_SENT pre_weight_g={pre_weight}")

            buffer = bytearray()
            next_f3 = time.monotonic() + 0.10
            next_f1: float | None = None
            deadline = time.monotonic() + arguments.timeout_seconds
            last_flags: int | None = None
            initial_lock_seen = False
            initial_off_seen = False
            repeat_count = 0
            pending_repeat_off = False
            ef_count = 0
            ef_first_time: float | None = None
            final_idle_seen = False
            final_f1_valid = False

            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_f3:
                    capture.transmit(uart, F3_REQUEST, "F3_POLL")
                    next_f3 = now + (0.75 if ef_count == 0 else 0.50)

                if initial_off_seen and ef_count == 0 and next_f1 is None:
                    next_f1 = now
                if next_f1 is not None and now >= next_f1:
                    capture.transmit(uart, F1_REQUEST, "F1_POLL")
                    next_f1 = now + (2.0 if ef_count == 0 else 0.75)

                capture.receive_into(uart, buffer, 0.08)
                for frame in capture.extract_frames(buffer):
                    capture.log(
                        f"RX_FRAME type={frame[0]:02X} "
                        f"raw={frame.hex(' ').upper()}"
                    )
                    if frame[0] == 0xF3:
                        flags = frame[49]
                        if flags != last_flags:
                            capture.log(
                                "F3_STATE "
                                f"mode={frame[1]:02X} status={frame[2]:02X} "
                                f"flags={flags:02X}"
                            )
                            last_flags = flags

                        if flags == 0x05:
                            if not initial_off_seen:
                                initial_lock_seen = True
                            elif not pending_repeat_off:
                                repeat_count += 1
                                pending_repeat_off = True
                                capture.log(
                                    f"REPEAT_UNLOCK_ACCEPTED count={repeat_count}"
                                )
                        elif flags == 0x0D:
                            if initial_lock_seen and not initial_off_seen:
                                initial_off_seen = True
                                capture.log("INITIAL_UNLOCK_AUTO_OFF_CONFIRMED")
                            elif pending_repeat_off:
                                pending_repeat_off = False
                                capture.log(
                                    "REPEAT_UNLOCK_AUTO_OFF_CONFIRMED "
                                    f"count={repeat_count}"
                                )
                        elif flags == 0x0F and ef_count:
                            final_idle_seen = True
                            capture.log("FINAL_CLEAN_IDLE_CONFIRMED")

                    elif frame[0] == 0xF1:
                        weight = int.from_bytes(frame[2:5], "big")
                        capture.log(
                            "F1_SAMPLE "
                            f"valid={frame[1]:02X} weight_g={weight} "
                            f"full={frame[5]:02X} smoke={frame[6]:02X}"
                        )
                        if (
                            ef_count
                            and (frame[1] & 0x01)
                            and weight <= 350000
                        ):
                            final_f1_valid = True

                    elif frame[0] == 0xEF:
                        ef_count += 1
                        before = int.from_bytes(frame[1:4], "big")
                        after = int.from_bytes(frame[4:7], "big")
                        capture.log(
                            "EF_ARCHIVED "
                            f"count={ef_count} pre_g={before} post_g={after} "
                            f"net_removed_g={before - after} "
                            f"full={frame[7]:02X} "
                            f"raw={frame.hex(' ').upper()}"
                        )
                        if ef_first_time is None:
                            ef_first_time = time.monotonic()
                            next_f3 = ef_first_time
                            next_f1 = ef_first_time

                    elif frame[0] == 0xCC:
                        capture.log(f"SMOKE_EVENT status={frame[1]:02X}")
                    else:
                        capture.log(
                            f"UNEXPECTED_FRAME marker={frame[0]:02X} "
                            f"raw={frame.hex(' ').upper()}"
                        )

                if (
                    ef_first_time is not None
                    and time.monotonic() - ef_first_time >= 5.0
                    and final_idle_seen
                    and final_f1_valid
                ):
                    capture.log(f"POST_EF_WINDOW_COMPLETE ef_count={ef_count}")
                    if ef_count != 1:
                        raise RuntimeError(f"EF_COUNT_MISMATCH={ef_count}")
                    capture.log("CLEAN_HIL_RESULT=PASS")
                    return

            raise RuntimeError("EF_WAIT_TIMEOUT")
    finally:
        capture.close()


def main() -> int:
    arguments = _arguments()
    try:
        _run_capture(arguments)
    except Exception as exc:
        print(f"CLEAN_HIL_RESULT=FAIL error={exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
