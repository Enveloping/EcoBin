"""Adapter for the negotiated fixed-length MCU protocol.

The deployed MCU does not implement EcoBin UART 1.0.  It accepts three
fixed-length commands and returns one aggregate result for delivery or clean:

    Edge -> MCU: AA 01 AA, BB PRICE BB, EE 01 EE
    MCU -> Edge: DD PRE:u24 POST:u24 FULL DD
                 EF PRE:u24 POST:u24 FULL EF

This module deliberately contains no ACK wait, retry, protocol auto-detection,
or MCU restart recovery.  It exposes the small subset of the ``UartLink``
interface that the Edge runtime needs while keeping the wire compromise below
the existing OneNet/SQLite boundary.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Callable, Optional

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger("fixed-frame-mcu")

DELIVERY_HEADER = 0xDD
CLEAN_HEADER = 0xEF
RESULT_FRAME_LENGTH = 9
MAXIMUM_WEIGHT_GRAMS = 350_000
MAXIMUM_RECEIVE_BUFFER = 4096

COMPAT_DELIVERY_RESULT_TYPE = 240
COMPAT_CLEAN_RESULT_TYPE = 241


def price_digit_from_ten_thousandths(
    unit_price_ten_thousandths: int,
) -> int:
    """Floor the cloud price to one decimal and saturate at MCU digit 9."""
    if (
        not isinstance(unit_price_ten_thousandths, int)
        or isinstance(unit_price_ten_thousandths, bool)
        or unit_price_ten_thousandths < 0
    ):
        raise ValueError("unit price must be a non-negative integer")
    return min(unit_price_ten_thousandths // 1000, 9)


class FixedFrameParser:
    """Bounded fixed-length parser tolerant of partial, joined and noisy reads."""

    def __init__(self, maximum_buffer: int = MAXIMUM_RECEIVE_BUFFER):
        if maximum_buffer < RESULT_FRAME_LENGTH:
            raise ValueError("maximum buffer is smaller than one result frame")
        self.maximum_buffer = maximum_buffer
        self._buffer = bytearray()

    @property
    def buffered_length(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[dict]:
        if not data:
            return []
        self._buffer.extend(data)
        if len(self._buffer) > self.maximum_buffer:
            overflow = len(self._buffer) - self.maximum_buffer
            del self._buffer[:overflow]
            logger.warning(
                "fixed-frame MCU receive buffer overflow; dropped %d bytes",
                overflow,
            )

        results: list[dict] = []
        while self._buffer:
            start = self._next_header_index()
            if start is None:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < RESULT_FRAME_LENGTH:
                break

            candidate = bytes(self._buffer[:RESULT_FRAME_LENGTH])
            decoded = self._decode(candidate)
            if decoded is None:
                # The apparent header was noise or a payload byte from a
                # damaged frame.  Advance one byte and search again.
                del self._buffer[0]
                continue
            results.append(decoded)
            del self._buffer[:RESULT_FRAME_LENGTH]
        return results

    def _next_header_index(self) -> Optional[int]:
        delivery = self._buffer.find(bytes((DELIVERY_HEADER,)))
        clean = self._buffer.find(bytes((CLEAN_HEADER,)))
        candidates = [index for index in (delivery, clean) if index >= 0]
        return min(candidates) if candidates else None

    @staticmethod
    def _decode(frame: bytes) -> Optional[dict]:
        header = frame[0]
        if (
            header not in (DELIVERY_HEADER, CLEAN_HEADER)
            or frame[-1] != header
            or frame[7] not in (0, 1)
        ):
            return None
        pre_weight = int.from_bytes(frame[1:4], "big", signed=False)
        post_weight = int.from_bytes(frame[4:7], "big", signed=False)
        if (
            pre_weight > MAXIMUM_WEIGHT_GRAMS
            or post_weight > MAXIMUM_WEIGHT_GRAMS
        ):
            return None
        return {
            "result_type": (
                "DELIVERY" if header == DELIVERY_HEADER else "CLEAN"
            ),
            "pre_weight_grams": pre_weight,
            "post_weight_grams": post_weight,
            "infrared_blocked": frame[7] == 1,
            "raw_frame_hex": frame.hex(),
        }


class FixedFrameMcuAdapter:
    """Serial adapter implementing the negotiated fixed-frame wire protocol."""

    compatibility_mode = True

    def __init__(
        self,
        port: str,
        edge_boot_id: int,
        port_count: int = 1,
        baudrate: int = 115200,
        timeout_s: float = 0.5,
        serial_factory: Optional[Callable[..., object]] = None,
    ):
        if port_count != 1:
            raise ValueError("fixed-frame MCU protocol supports exactly one port")
        self.port = port
        self.edge_boot_id = edge_boot_id
        self.port_count = port_count
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self._serial_factory = serial_factory
        self._ser = None
        self._parser = FixedFrameParser()
        self._io_lock = threading.RLock()
        self._pending_events: deque[dict] = deque()
        self._mcu_boot_id = edge_boot_id
        self._mcu_capability = 0
        self._mcu_firmware_version = "fixed-frame-compat"
        self._mcu_event_sequence = 0

    @property
    def is_open(self) -> bool:
        return self._ser is not None and bool(
            getattr(self._ser, "is_open", True)
        )

    @property
    def mcu_session_ready(self) -> bool:
        return self.is_open

    def open(self) -> bool:
        factory = self._serial_factory
        if factory is None:
            if serial is None:
                logger.error("pyserial not installed, cannot open UART")
                return False
            factory = serial.Serial
        try:
            self._ser = factory(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self.timeout_s,
            )
            logger.info(
                "fixed-frame MCU UART opened: port=%s baudrate=%d",
                self.port,
                self.baudrate,
            )
            return True
        except Exception as error:
            self._ser = None
            logger.error("fixed-frame MCU UART open failed: %s", error)
            return False

    def close(self) -> None:
        with self._io_lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                finally:
                    self._ser = None

    def handshake(self) -> dict:
        """Return a local adapter identity without transmitting a wire frame."""
        if not self.is_open:
            raise RuntimeError("UART is not open")
        return {
            "mcu_boot_id": self._mcu_boot_id,
            "mcu_capability": 0,
            "mcu_port_count": 1,
            "mcu_firmware_identity": "negotiated-fixed-frame-mcu",
            "mcu_firmware_version": self._mcu_firmware_version,
            "mcu_pending_critical_events": 0,
            "uart_protocol_major": None,
            "uart_protocol_minor": None,
            "uart_state": "READY",
            "compatibility_mode": True,
            "fullness_sensor_kind": "DIGITAL_INFRARED",
        }

    def query_state(self, on_segment=None) -> list[dict]:
        """The fixed-frame MCU has no state query command."""
        return []

    def apply_configuration(
        self,
        command: dict,
        part_command_uids: list[str],
    ) -> dict:
        return {
            "acked": False,
            "error": "MCU_FEATURE_NOT_SUPPORTED",
            "parts": [],
        }

    def send_command(
        self,
        message_name: str,
        values: dict,
        *,
        mcu_command_uid: Optional[str] = None,
    ) -> dict:
        command_uid = str(
            uuid.UUID(mcu_command_uid or str(uuid.uuid4()))
        )
        if not self.is_open:
            return self._command_result(
                message_name,
                command_uid,
                False,
                "UART_CLOSED",
            )
        try:
            if message_name == "START_DELIVERY_SESSION":
                price = price_digit_from_ten_thousandths(
                    values["unitPriceTenThousandths"]
                )
                wire = bytes(
                    (
                        0xBB,
                        price,
                        0xBB,
                        0xAA,
                        0x01,
                        0xAA,
                    )
                )
                self._dispatch_start(wire)
            elif message_name == "START_CLEAN_OPERATION":
                self._dispatch_start(bytes((0xEE, 0x01, 0xEE)))
            else:
                return self._command_result(
                    message_name,
                    command_uid,
                    False,
                    "MCU_FEATURE_NOT_SUPPORTED",
                )
        except Exception as error:
            logger.error(
                "fixed-frame MCU command write failed: command=%s error=%s",
                message_name,
                error,
            )
            return self._command_result(
                message_name,
                command_uid,
                False,
                "UART_WRITE_FAILED",
            )
        return {
            "acked": True,
            "message_name": message_name,
            "mcu_command_uid": command_uid,
            "disposition": "LOCALLY_DISPATCHED",
            "compatibility_mode": True,
        }

    def _dispatch_start(self, wire: bytes) -> None:
        with self._io_lock:
            self._discard_pending_input()
            written = self._ser.write(wire)
            if written != len(wire):
                raise IOError(
                    "short UART write: "
                    f"expected={len(wire)} actual={written}"
                )
            self._ser.flush()

    def _discard_pending_input(self) -> None:
        self._parser.clear()
        self._pending_events.clear()
        reset = getattr(self._ser, "reset_input_buffer", None)
        if callable(reset):
            reset()

    @staticmethod
    def _command_result(
        message_name: str,
        command_uid: str,
        acked: bool,
        error: str,
    ) -> dict:
        return {
            "acked": acked,
            "message_name": message_name,
            "mcu_command_uid": command_uid,
            "error": error,
            "fatal": False,
            "compatibility_mode": True,
        }

    def read_mcu_event(self, timeout_ms: int = 500) -> Optional[dict]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        with self._io_lock:
            if self._pending_events:
                return self._pending_events.popleft()
            if not self.is_open:
                return None
            while time.monotonic() < deadline:
                remaining = max(0.01, deadline - time.monotonic())
                if hasattr(self._ser, "timeout"):
                    self._ser.timeout = remaining
                waiting = int(getattr(self._ser, "in_waiting", 0) or 0)
                chunk = self._ser.read(min(256, waiting or 1))
                if not chunk:
                    continue
                decoded = self._parser.feed(bytes(chunk))
                if not decoded:
                    continue
                events = [self._to_event(item) for item in decoded]
                self._pending_events.extend(events[1:])
                return events[0]
        return None

    def _to_event(self, decoded: dict) -> dict:
        self._mcu_event_sequence += 1
        delivery = decoded["result_type"] == "DELIVERY"
        return {
            "message_name": (
                "COMPAT_DELIVERY_RESULT"
                if delivery
                else "COMPAT_CLEAN_RESULT"
            ),
            "message_type": (
                COMPAT_DELIVERY_RESULT_TYPE
                if delivery
                else COMPAT_CLEAN_RESULT_TYPE
            ),
            "flags": 0,
            "tx_sequence": self._mcu_event_sequence,
            "payload": {
                "mcuBootId": self._mcu_boot_id,
                "mcuEventSequence": self._mcu_event_sequence,
                "uptimeMs": int(time.monotonic() * 1000),
                "preWeightGrams": decoded["pre_weight_grams"],
                "postWeightGrams": decoded["post_weight_grams"],
                "infraredBlocked": decoded["infrared_blocked"],
                "rawFrameHex": decoded["raw_frame_hex"],
            },
        }

    def send_ack(self, *args, **kwargs) -> None:
        """The negotiated fixed-frame protocol has no ACK frame."""

    def send_nack(self, *args, **kwargs) -> None:
        """The negotiated fixed-frame protocol has no NACK frame."""

    def send_safe_close_all(self) -> dict:
        return {
            "acked": False,
            "error": "MCU_FEATURE_NOT_SUPPORTED",
            "compatibility_mode": True,
        }
