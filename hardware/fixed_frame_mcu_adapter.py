"""Adapter for the negotiated fixed-length MCU protocol.

The deployed MCU uses a deliberately small fixed-frame protocol:

    Edge -> MCU: AA 01 AA, BB PRICE BB, EE 01 EE, F0 01 F0,
                 F2 MODE F2, A0 LEN URL_DATA[192] A0
    MCU -> Edge: DD PRE:u24 POST:u24 FULL DD
                 EF PRE:u24 POST:u24 FULL EF
                 F1 VALID WEIGHT:u24 FULL SMOKE F1
                 F3 MODE STATUS REV VERSION_CODE:u32 VERSION_LEN
                    VERSION[32] IDENTITY[8] SAFE_FLAGS F3
                 CC SMOKE CC

The adapter keeps the wire simple: no CRC, generic ACK, retry, flow identity,
protocol auto-detection, or MCU work recovery.  OneNet-only fields are added at
this boundary and are never required from the MCU firmware.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from typing import Callable, Optional

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger("fixed-frame-mcu")

DELIVERY_HEADER = 0xDD
CLEAN_HEADER = 0xEF
SMOKE_HEADER = 0xCC
SELF_TEST_QUERY_HEADER = 0xF0
SELF_TEST_RESPONSE_HEADER = 0xF1
FIRMWARE_QUERY_HEADER = 0xF2
FIRMWARE_STATUS_HEADER = 0xF3
DEVICE_ENTRY_URL_HEADER = 0xA0

RESULT_FRAME_LENGTH = 9
SMOKE_FRAME_LENGTH = 3
SELF_TEST_FRAME_LENGTH = 8
FIRMWARE_STATUS_FRAME_LENGTH = 51
SELF_TEST_QUERY_FRAME = bytes((SELF_TEST_QUERY_HEADER, 0x01, SELF_TEST_QUERY_HEADER))
SELF_TEST_TIMEOUT_MS = 3_000
FIRMWARE_QUERY_TIMEOUT_MS = 3_000
FIRMWARE_PROTOCOL_REVISION = 2
FIRMWARE_QUERY_IDENTITY_MODE = 1
FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE = 2
FIRMWARE_REQUIRED_SAFE_FLAGS = 0x1F
FACTORY_SIM_FIRMWARE_VERSION = "factory-sim-1.0.0"
FACTORY_SIM_FIRMWARE_VERSION_CODE = 1
FACTORY_SIM_FIRMWARE_IDENTITY_HEX = "45434f53494d3031"
DEVICE_ENTRY_URL_FIELD_LENGTH = 192
DEVICE_ENTRY_URL_FRAME_LENGTH = 195
MAXIMUM_WEIGHT_GRAMS = 350_000
MAXIMUM_RECEIVE_BUFFER = 4096
FOREGROUND_IO_WAIT_WARNING_MS = 750.0
SERIAL_READ_TIMEOUT_CAP_S = 0.05

FRAME_LENGTHS = {
    DELIVERY_HEADER: RESULT_FRAME_LENGTH,
    CLEAN_HEADER: RESULT_FRAME_LENGTH,
    SMOKE_HEADER: SMOKE_FRAME_LENGTH,
    SELF_TEST_RESPONSE_HEADER: SELF_TEST_FRAME_LENGTH,
    FIRMWARE_STATUS_HEADER: FIRMWARE_STATUS_FRAME_LENGTH,
}

FIRMWARE_STATUS_NAMES = {
    0: "OK",
    # Revision 2 was corrected before production rollout: the Edge owns the
    # business admission decision.  Values 1 and 2 are retained only so an
    # accidentally flashed pre-correction image fails closed as a legacy
    # execution response; they are never treated as an MCU admission policy.
    1: "LEGACY_BUSY",
    2: "LEGACY_UNSAFE",
    3: "INTERNAL_ERROR",
}

COMPAT_DELIVERY_RESULT_TYPE = 240
COMPAT_CLEAN_RESULT_TYPE = 241
COMPAT_SAFETY_EVENT_TYPE = 54


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
        if maximum_buffer < max(FRAME_LENGTHS.values()):
            raise ValueError("maximum buffer is smaller than one result frame")
        self.maximum_buffer = maximum_buffer
        self._buffer = bytearray()
        self._invalid_counts = {
            header: 0 for header in FRAME_LENGTHS
        }

    @property
    def buffered_length(self) -> int:
        return len(self._buffer)

    def invalid_count(self, header: int) -> int:
        return self._invalid_counts.get(header, 0)

    def clear(self) -> None:
        self._buffer.clear()

    def discard_incomplete_except(self, headers: set[int]) -> None:
        """Keep an incomplete safety frame while discarding stale work bytes."""
        if self._buffer and self._buffer[0] not in headers:
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
            header = self._buffer[0]
            frame_length = FRAME_LENGTHS[header]
            if len(self._buffer) < frame_length:
                break

            candidate = bytes(self._buffer[:frame_length])
            decoded = self._decode(candidate)
            if decoded is None:
                self._invalid_counts[header] += 1
                # The apparent header was noise or a payload byte from a
                # damaged frame. Advance one byte and search again.
                del self._buffer[0]
                continue
            results.append(decoded)
            del self._buffer[:frame_length]
        return results

    def _next_header_index(self) -> Optional[int]:
        candidates = [
            self._buffer.find(bytes((header,)))
            for header in FRAME_LENGTHS
        ]
        candidates = [index for index in candidates if index >= 0]
        return min(candidates) if candidates else None

    @staticmethod
    def _decode(frame: bytes) -> Optional[dict]:
        header = frame[0]
        if frame[-1] != header:
            return None

        if header in (DELIVERY_HEADER, CLEAN_HEADER):
            if len(frame) != RESULT_FRAME_LENGTH or frame[7] not in (0, 1):
                return None
            pre_weight = int.from_bytes(frame[1:4], "big", signed=False)
            post_weight = int.from_bytes(frame[4:7], "big", signed=False)
            if (
                pre_weight > MAXIMUM_WEIGHT_GRAMS
                or post_weight > MAXIMUM_WEIGHT_GRAMS
            ):
                return None
            return {
                "frame_type": (
                    "DELIVERY" if header == DELIVERY_HEADER else "CLEAN"
                ),
                "pre_weight_grams": pre_weight,
                "post_weight_grams": post_weight,
                "infrared_blocked": frame[7] == 1,
                "raw_frame_hex": frame.hex(),
            }

        if header == SMOKE_HEADER:
            if len(frame) != SMOKE_FRAME_LENGTH or frame[1] not in (0, 1, 2):
                return None
            return {
                "frame_type": "SMOKE",
                "smoke_code": frame[1],
                "raw_frame_hex": frame.hex(),
            }

        if header == SELF_TEST_RESPONSE_HEADER:
            if len(frame) != SELF_TEST_FRAME_LENGTH:
                return None
            valid_flags = frame[1]
            weight = int.from_bytes(frame[2:5], "big", signed=False)
            infrared = frame[5]
            smoke = frame[6]
            if (
                valid_flags & 0xFC
                or weight > MAXIMUM_WEIGHT_GRAMS
                or infrared not in (0, 1)
                or smoke not in (0, 1, 2)
                or (not (valid_flags & 0x01) and weight != 0)
                or (not (valid_flags & 0x02) and infrared != 0)
            ):
                return None
            return {
                "frame_type": "SELF_TEST",
                "valid_flags": valid_flags,
                "weight_valid": bool(valid_flags & 0x01),
                "weight_grams": weight,
                "infrared_valid": bool(valid_flags & 0x02),
                "infrared_blocked": infrared == 1,
                "smoke_code": smoke,
                "raw_frame_hex": frame.hex(),
            }

        if header == FIRMWARE_STATUS_HEADER:
            if len(frame) != FIRMWARE_STATUS_FRAME_LENGTH:
                return None
            mode = frame[1]
            status_code = frame[2]
            protocol_revision = frame[3]
            version_code = int.from_bytes(frame[4:8], "big", signed=False)
            version_length = frame[8]
            version_field = frame[9:41]
            identity = frame[41:49]
            safe_flags = frame[49]
            if (
                mode not in {
                    FIRMWARE_QUERY_IDENTITY_MODE,
                    FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE,
                }
                or status_code not in FIRMWARE_STATUS_NAMES
                or protocol_revision != FIRMWARE_PROTOCOL_REVISION
                or version_code == 0
                or not 1 <= version_length <= len(version_field)
                or safe_flags & ~FIRMWARE_REQUIRED_SAFE_FLAGS
                or not any(identity)
            ):
                return None
            encoded_version = version_field[:version_length]
            if (
                any(byte < 0x21 or byte > 0x7E for byte in encoded_version)
                or any(version_field[version_length:])
            ):
                return None
            try:
                version = encoded_version.decode("ascii")
            except UnicodeDecodeError:
                return None
            return {
                "frame_type": "FIRMWARE_STATUS",
                "mode": mode,
                "status_code": status_code,
                "status": FIRMWARE_STATUS_NAMES[status_code],
                "protocol_revision": protocol_revision,
                "firmware_version_code": version_code,
                "firmware_version": version,
                "firmware_identity_hex": identity.hex(),
                "safe_flags": safe_flags,
                "raw_frame_hex": frame.hex(),
            }

        return None


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
        is_simulated: bool = False,
        device_entry_url_provider: Optional[
            Callable[[], Optional[dict]]
        ] = None,
    ):
        if port_count != 1:
            raise ValueError("fixed-frame MCU protocol supports exactly one port")
        self.port = port
        self.edge_boot_id = edge_boot_id
        self.port_count = port_count
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self._serial_factory = serial_factory
        self._device_entry_url_provider = device_entry_url_provider
        self.is_simulated = bool(is_simulated)
        self._ser = None
        self._parser = FixedFrameParser()
        self._io_lock = threading.RLock()
        # Passive reads may block for one serial timeout.  Foreground work
        # announces intent through a separate condition before waiting on the
        # RLock, preventing the reader from repeatedly reacquiring it first.
        self._foreground_condition = threading.Condition()
        self._foreground_operations = 0
        self._pending_events: deque[dict] = deque()
        self._mcu_boot_id = edge_boot_id
        self._mcu_capability = 0
        self._mcu_firmware_version = "fixed-frame-compat"
        self._mcu_firmware_version_code = None
        self._mcu_firmware_identity = None
        self._fixed_frame_revision = 1
        self._verified_firmware_identity = None
        self._mcu_event_sequence = 0
        self._has_opened_once = False

    @property
    def is_open(self) -> bool:
        return self._ser is not None and bool(
            getattr(self._ser, "is_open", True)
        )

    @property
    def mcu_session_ready(self) -> bool:
        return self.is_open

    @property
    def verified_firmware_identity(self) -> Optional[dict]:
        """Last F3 identity proven by a successful MCU status response."""
        return (
            dict(self._verified_firmware_identity)
            if self._verified_firmware_identity is not None
            else None
        )

    @property
    def mcu_peripherals_simulated(self) -> bool:
        """Whether a successful F3 proves the dedicated factory image.

        Matching only a friendly version string would let an incomplete or
        corrupt response mislabel production evidence.  All immutable fields
        of the published factory-simulation identity must match.
        """

        identity = self._verified_firmware_identity
        return bool(
            identity
            and identity.get("firmwareVersion")
            == FACTORY_SIM_FIRMWARE_VERSION
            and identity.get("firmwareVersionCode")
            == FACTORY_SIM_FIRMWARE_VERSION_CODE
            and identity.get("firmwareIdentityHex")
            == FACTORY_SIM_FIRMWARE_IDENTITY_HEX
        )

    def open(self) -> bool:
        with self._foreground_io("OPEN"):
            factory = self._serial_factory
            if factory is None:
                if serial is None:
                    logger.error("pyserial not installed, cannot open UART")
                    return False
                factory = serial.Serial
            try:
                reopening = self._has_opened_once
                serial_arguments = dict(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                    timeout=min(self.timeout_s, SERIAL_READ_TIMEOUT_CAP_S),
                )
                if self._serial_factory is None:
                    # Production owns ttyS5 exclusively.  A second process
                    # must fail at open instead of interleaving protocol or
                    # STM32 ROM-loader bytes with this adapter.
                    serial_arguments["exclusive"] = True
                self._ser = factory(**serial_arguments)
                self._verified_firmware_identity = None
                logger.info(
                    "fixed-frame MCU UART opened: port=%s baudrate=%d",
                    self.port,
                    self.baudrate,
                )
                self._has_opened_once = True
                if reopening:
                    self._resend_device_entry_url_after_reopen()
                return True
            except Exception as error:
                self._ser = None
                logger.error("fixed-frame MCU UART open failed: %s", error)
                return False

    def _resend_device_entry_url_after_reopen(self) -> None:
        provider = self._device_entry_url_provider
        if not callable(provider):
            return
        try:
            record = provider()
            if record is not None:
                self.send_device_entry_url(record["deviceEntryUrl"])
                logger.info(
                    "stored device entry URL resent after UART reopen"
                )
        except Exception as error:
            logger.warning(
                "stored device entry URL resend after UART reopen failed: %s",
                error,
            )

    def close(self) -> None:
        with self._foreground_io("CLOSE"):
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
            "mcu_firmware_identity": (
                self._mcu_firmware_identity
                or "negotiated-fixed-frame-mcu"
            ),
            "mcu_firmware_version": self._mcu_firmware_version,
            "mcu_firmware_version_code": self._mcu_firmware_version_code,
            "fixed_frame_revision": self._fixed_frame_revision,
            "mcu_pending_critical_events": 0,
            "uart_protocol_major": None,
            "uart_protocol_minor": None,
            "uart_state": "READY",
            "compatibility_mode": True,
            "fullness_sensor_kind": "DIGITAL_INFRARED",
        }

    def query_state(self, on_segment=None) -> list[dict]:
        """The fixed-frame MCU still has no general work/state query."""
        del on_segment
        return []

    def query_firmware_identity(
        self,
        timeout_ms: int = FIRMWARE_QUERY_TIMEOUT_MS,
    ) -> dict:
        """Query the revision-2 firmware identity without changing MCU state."""
        return self.query_firmware_status(
            FIRMWARE_QUERY_IDENTITY_MODE,
            timeout_ms=timeout_ms,
        )

    def execute_firmware_update_prepare(
        self,
        timeout_ms: int = FIRMWARE_QUERY_TIMEOUT_MS,
    ) -> dict:
        """Command the MCU to stop outputs and latch update execution mode.

        The Edge has already made the business admission decision and owns the
        maintenance state machine.  This response proves only whether the MCU
        executed the requested stop-and-latch action.
        """
        result = self.query_firmware_status(
            FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE,
            timeout_ms=timeout_ms,
        )
        result["executed"] = bool(
            result.get("queryStatus") == "OK"
            and result.get("statusCode") == 0
            and (result.get("safeFlags") or 0) & FIRMWARE_REQUIRED_SAFE_FLAGS
            == FIRMWARE_REQUIRED_SAFE_FLAGS
        )
        return result

    def query_firmware_status(
        self,
        mode: int,
        timeout_ms: int = FIRMWARE_QUERY_TIMEOUT_MS,
    ) -> dict:
        """Send one F2 challenge and return the matching fresh F3 snapshot."""
        if mode not in {
            FIRMWARE_QUERY_IDENTITY_MODE,
            FIRMWARE_EXECUTE_UPDATE_PREPARE_MODE,
        }:
            raise ValueError("firmware query mode must be 1 or 2")
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or timeout_ms <= 0
        ):
            raise ValueError("firmware query timeout must be a positive integer")

        with self._foreground_io("FIRMWARE_STATUS"):
            if not self.is_open:
                return self._failed_firmware_query("UART_CLOSED", mode)

            self._drain_before_firmware_query()
            invalid_before = self._parser.invalid_count(
                FIRMWARE_STATUS_HEADER
            )
            try:
                self._write_exact(
                    bytes((FIRMWARE_QUERY_HEADER, mode, FIRMWARE_QUERY_HEADER))
                )
            except Exception as error:
                logger.error("fixed-frame firmware query write failed: %s", error)
                return self._failed_firmware_query("UART_WRITE_FAILED", mode)

            deadline = time.monotonic() + timeout_ms / 1000.0
            response = None
            while time.monotonic() < deadline:
                chunk = self._read_chunk(deadline)
                if not chunk:
                    continue
                invalid_smoke_before = self._parser.invalid_count(SMOKE_HEADER)
                for item in self._parser.feed(chunk):
                    frame_type = item["frame_type"]
                    if frame_type == "FIRMWARE_STATUS":
                        if item["mode"] == mode and response is None:
                            response = item
                        else:
                            logger.warning(
                                "discarding stale or duplicate firmware status: "
                                "expected_mode=%d actual_mode=%d",
                                mode,
                                item["mode"],
                            )
                    elif frame_type == "SELF_TEST":
                        self._pending_events.append(
                            self._to_safety_event(
                                item["smoke_code"],
                                raw_frame_hex=item["raw_frame_hex"],
                            )
                        )
                    else:
                        self._pending_events.append(self._to_event(item))
                self._queue_invalid_smoke_events(invalid_smoke_before)
                if response is not None:
                    self._remember_firmware_status(response)
                    return self._successful_firmware_query(response)

            query_status = (
                "PROTOCOL_ERROR"
                if self._parser.invalid_count(FIRMWARE_STATUS_HEADER)
                > invalid_before
                else "TIMEOUT"
            )
            return self._failed_firmware_query(query_status, mode)

    def query_self_test(
        self,
        timeout_ms: int = SELF_TEST_TIMEOUT_MS,
        on_result: Optional[Callable[[dict], object]] = None,
        *,
        queue_unchanged_safety_event: bool = True,
        dispatch_gate: Optional[Callable[[], None]] = None,
    ) -> dict:
        """Request, optionally persist, then queue one fresh sensor snapshot.

        ``on_result`` runs while the serial lock is still held. This keeps a
        following CC frame from being persisted before an older F1 snapshot.
        A recovery caller may suppress the safety event only when that callback
        explicitly reports that the projected smoke state is unchanged.
        """
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or timeout_ms <= 0
        ):
            raise ValueError("self-test timeout must be a positive integer")
        if on_result is not None and not callable(on_result):
            raise ValueError("self-test result callback must be callable")
        if not isinstance(queue_unchanged_safety_event, bool):
            raise ValueError("safety event queue policy must be boolean")
        with self._foreground_io("SELF_TEST"):
            if not self.is_open:
                return self._finish_self_test(
                    self._failed_self_test("UART_CLOSED"),
                    on_result,
                )

            self._drain_before_self_test()
            invalid_before = self._parser.invalid_count(
                SELF_TEST_RESPONSE_HEADER
            )
            # Arm only after the foreground UART lock, open check and stale
            # input drain.  A rejected arm must escape unchanged and must not
            # be mistaken for an ordinary UART write failure.
            if dispatch_gate is not None:
                dispatch_gate()
            try:
                self._write_exact(SELF_TEST_QUERY_FRAME)
            except Exception as error:
                logger.error("fixed-frame self-test query write failed: %s", error)
                if dispatch_gate is not None:
                    raise
                result = self._failed_self_test("UART_WRITE_FAILED")
                changed = self._notify_self_test(result, on_result)
                if queue_unchanged_safety_event or changed is not False:
                    self._pending_events.append(
                        self._to_safety_event(
                            None,
                            health="PROTOCOL_ERROR",
                        )
                    )
                return result

            deadline = time.monotonic() + timeout_ms / 1000.0
            response: Optional[dict] = None
            completed_result: Optional[dict] = None
            while time.monotonic() < deadline:
                chunk = self._read_chunk(deadline)
                if not chunk:
                    continue
                invalid_smoke_before = self._parser.invalid_count(SMOKE_HEADER)
                decoded = self._parser.feed(chunk)
                for item in decoded:
                    if item["frame_type"] == "SELF_TEST":
                        if response is None:
                            response = item
                            completed_result = self._successful_self_test(
                                response
                            )
                            changed = self._notify_self_test(
                                completed_result,
                                on_result,
                            )
                            if (
                                queue_unchanged_safety_event
                                or changed is not False
                            ):
                                self._pending_events.append(
                                    self._to_safety_event(
                                        item["smoke_code"],
                                        raw_frame_hex=item["raw_frame_hex"],
                                    )
                                )
                        else:
                            logger.warning(
                                "discarding duplicate fixed-frame self-test response"
                            )
                    else:
                        self._pending_events.append(self._to_event(item))
                self._queue_invalid_smoke_events(invalid_smoke_before)
                if response is not None:
                    return completed_result

            query_status = (
                "PROTOCOL_ERROR"
                if self._parser.invalid_count(SELF_TEST_RESPONSE_HEADER)
                > invalid_before
                else "TIMEOUT"
            )
            health = (
                "PROTOCOL_ERROR"
                if query_status == "PROTOCOL_ERROR"
                else "TIMEOUT"
            )
            result = self._failed_self_test(query_status)
            changed = self._notify_self_test(result, on_result)
            if queue_unchanged_safety_event or changed is not False:
                self._pending_events.append(
                    self._to_safety_event(None, health=health)
                )
            return result

    @staticmethod
    def _notify_self_test(
        result: dict,
        on_result: Optional[Callable[[dict], object]],
    ) -> object:
        if on_result is None:
            return None
        return on_result(result)

    @staticmethod
    def _finish_self_test(
        result: dict,
        on_result: Optional[Callable[[dict], object]],
    ) -> dict:
        FixedFrameMcuAdapter._notify_self_test(
            result,
            on_result,
        )
        return result

    def apply_configuration(
        self,
        command: dict,
        part_command_uids: list[str],
    ) -> dict:
        del command, part_command_uids
        return {
            "acked": False,
            "error": "MCU_FEATURE_NOT_SUPPORTED",
            "parts": [],
        }

    def send_device_entry_url(self, url: str) -> dict:
        """Send one fire-and-forget URL frame; the MCU sends no response."""
        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= DEVICE_ENTRY_URL_FIELD_LENGTH
            or not url.startswith("https://")
            or any(
                ord(character) < 0x21 or ord(character) > 0x7E
                for character in url
            )
        ):
            raise ValueError(
                "device entry URL must be printable ASCII HTTPS within 192 bytes"
            )
        if not self.is_open:
            raise RuntimeError("UART is closed")
        encoded = url.encode("ascii")
        wire = b"".join(
            (
                bytes((DEVICE_ENTRY_URL_HEADER, len(encoded))),
                encoded.ljust(DEVICE_ENTRY_URL_FIELD_LENGTH, b"\x00"),
                bytes((DEVICE_ENTRY_URL_HEADER,)),
            )
        )
        if len(wire) != DEVICE_ENTRY_URL_FRAME_LENGTH:
            raise AssertionError("device entry URL frame length is invalid")
        with self._foreground_io("DEVICE_ENTRY_URL"):
            self._write_exact(wire)
        return {
            "disposition": "LOCALLY_DISPATCHED",
            "frameLength": len(wire),
            "responseExpected": False,
        }

    def send_command(
        self,
        message_name: str,
        values: dict,
        *,
        mcu_command_uid: Optional[str] = None,
        dispatch_deadline_monotonic: Optional[float] = None,
        dispatch_gate: Optional[Callable[[], None]] = None,
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
        relative_deadline = self._physical_dispatch_deadline(values)
        deadlines = [
            deadline
            for deadline in (
                relative_deadline,
                dispatch_deadline_monotonic,
            )
            if deadline is not None
        ]
        dispatch_deadline = min(deadlines) if deadlines else None
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
                dispatch_error = self._dispatch_start(
                    wire,
                    message_name=message_name,
                    dispatch_deadline=dispatch_deadline,
                    dispatch_gate=dispatch_gate,
                )
                if dispatch_error is not None:
                    return self._command_result(
                        message_name,
                        command_uid,
                        False,
                        dispatch_error,
                    )
            elif message_name == "START_CLEAN_OPERATION":
                dispatch_error = self._dispatch_start(
                    bytes((0xEE, 0x01, 0xEE)),
                    message_name=message_name,
                    dispatch_deadline=dispatch_deadline,
                    dispatch_gate=dispatch_gate,
                )
                if dispatch_error is not None:
                    return self._command_result(
                        message_name,
                        command_uid,
                        False,
                        dispatch_error,
                    )
            else:
                return self._command_result(
                    message_name,
                    command_uid,
                    False,
                    "MCU_FEATURE_NOT_SUPPORTED",
                )
        except Exception as error:
            logger.error(
                "fixed-frame MCU command dispatch failed: command=%s error=%s",
                message_name,
                error,
            )
            if dispatch_gate is not None:
                raise
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

    def send_command_before_deadline(
        self,
        message_name: str,
        values: dict,
        *,
        mcu_command_uid: Optional[str] = None,
        dispatch_deadline_monotonic: float,
        dispatch_gate: Optional[Callable[[], None]] = None,
    ) -> dict:
        """Carry the deadline and arm gate through the foreground I/O lock."""

        return self.send_command(
            message_name,
            values,
            mcu_command_uid=mcu_command_uid,
            dispatch_deadline_monotonic=dispatch_deadline_monotonic,
            dispatch_gate=dispatch_gate,
        )

    def _dispatch_start(
        self,
        wire: bytes,
        *,
        message_name: str,
        dispatch_deadline: Optional[float],
        dispatch_gate: Optional[Callable[[], None]],
    ) -> Optional[str]:
        with self._foreground_io(message_name) as wait_ms:
            if not self.is_open:
                return "UART_CLOSED"
            if self._dispatch_expired(dispatch_deadline):
                logger.warning(
                    "fixed-frame physical command expired before UART write: "
                    "command=%s uart_wait_ms=%.1f",
                    message_name,
                    wait_ms,
                )
                return "COMMAND_EXPIRED"
            self._discard_stale_business_input()
            if self._dispatch_expired(dispatch_deadline):
                logger.warning(
                    "fixed-frame physical command expired while preparing UART "
                    "write: command=%s uart_wait_ms=%.1f",
                    message_name,
                    wait_ms,
                )
                return "COMMAND_EXPIRED"
            # This is the irreversible boundary: all local UART prechecks are
            # complete and the first write follows without releasing the lock.
            if dispatch_gate is not None:
                dispatch_gate()
            self._write_exact(wire)
        logger.info(
            "fixed-frame physical command locally dispatched: "
            "command=%s uart_wait_ms=%.1f",
            message_name,
            wait_ms,
        )
        return None

    @staticmethod
    def _physical_dispatch_deadline(values: dict) -> Optional[float]:
        window_ms = values.get("startExecutionWindowMs")
        if window_ms is None:
            return None
        if (
            not isinstance(window_ms, int)
            or isinstance(window_ms, bool)
            or window_ms <= 0
        ):
            return time.monotonic()
        return time.monotonic() + window_ms / 1000.0

    @staticmethod
    def _dispatch_expired(deadline: Optional[float]) -> bool:
        return deadline is not None and time.monotonic() >= deadline

    @contextmanager
    def _foreground_io(self, operation_name: str):
        """Give one finite foreground transaction priority over new reads."""
        requested_at = time.monotonic()
        with self._foreground_condition:
            self._foreground_operations += 1
        try:
            with self._io_lock:
                wait_ms = (time.monotonic() - requested_at) * 1000.0
                if wait_ms >= FOREGROUND_IO_WAIT_WARNING_MS:
                    logger.warning(
                        "fixed-frame foreground UART operation waited too long: "
                        "operation=%s uart_wait_ms=%.1f",
                        operation_name,
                        wait_ms,
                    )
                yield wait_ms
        finally:
            with self._foreground_condition:
                self._foreground_operations -= 1
                if self._foreground_operations == 0:
                    self._foreground_condition.notify_all()

    def _wait_for_background_turn(self, deadline: float) -> bool:
        """Wait without consuming more than the caller's read-time budget."""
        with self._foreground_condition:
            while self._foreground_operations > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._foreground_condition.wait(timeout=remaining)
            return True

    def _write_exact(self, wire: bytes) -> None:
        written = self._ser.write(wire)
        if written != len(wire):
            raise IOError(
                "short UART write: "
                f"expected={len(wire)} actual={written}"
            )
        self._ser.flush()

    def _read_chunk(self, deadline: float) -> bytes:
        if time.monotonic() >= deadline:
            return b""
        waiting = int(getattr(self._ser, "in_waiting", 0) or 0)
        # Changing pySerial.timeout on an open Windows COM port triggers a
        # live driver reconfiguration.  PTD01 may then lose the rest of a
        # fragmented frame.  Keep the timeout fixed after open; when the
        # driver reports no buffered data, a one-byte read also gives USB VCP
        # a pending read request so that the first fragment can be delivered.
        return bytes(self._ser.read(min(256, waiting or 1)))

    def _read_available_decoded(self) -> tuple[list[dict], int]:
        decoded: list[dict] = []
        invalid_smoke_before = self._parser.invalid_count(SMOKE_HEADER)
        while self.is_open:
            waiting = int(getattr(self._ser, "in_waiting", 0) or 0)
            if waiting <= 0:
                break
            chunk = self._ser.read(min(256, waiting))
            if not chunk:
                break
            decoded.extend(self._parser.feed(bytes(chunk)))
        invalid_smoke = (
            self._parser.invalid_count(SMOKE_HEADER) - invalid_smoke_before
        )
        return decoded, invalid_smoke

    def _drain_before_self_test(self) -> None:
        decoded, invalid_smoke = self._read_available_decoded()
        for item in decoded:
            if item["frame_type"] == "SELF_TEST":
                logger.warning("discarding stale fixed-frame self-test response")
                continue
            if item["frame_type"] == "FIRMWARE_STATUS":
                logger.warning("discarding unsolicited firmware status")
                continue
            self._pending_events.append(self._to_event(item))
        for _ in range(invalid_smoke):
            self._pending_events.append(
                self._to_safety_event(None, health="PROTOCOL_ERROR")
            )
        # A response that began before this F0 challenge must not become its
        # evidence merely because the remaining bytes arrive afterwards.
        # Other partial frames may still complete while this query is waiting.
        self._parser.discard_incomplete_except({
            DELIVERY_HEADER,
            CLEAN_HEADER,
            SMOKE_HEADER,
        })

    def _drain_before_firmware_query(self) -> None:
        decoded, invalid_smoke = self._read_available_decoded()
        for item in decoded:
            frame_type = item["frame_type"]
            if frame_type == "FIRMWARE_STATUS":
                logger.warning("discarding stale fixed-frame firmware status")
            elif frame_type == "SELF_TEST":
                self._pending_events.append(
                    self._to_safety_event(
                        item["smoke_code"],
                        raw_frame_hex=item["raw_frame_hex"],
                    )
                )
            else:
                self._pending_events.append(self._to_event(item))
        for _ in range(invalid_smoke):
            self._pending_events.append(
                self._to_safety_event(None, health="PROTOCOL_ERROR")
            )
        # An F3 prefix received before this challenge is stale evidence.
        self._parser.discard_incomplete_except({
            DELIVERY_HEADER,
            CLEAN_HEADER,
            SMOKE_HEADER,
            SELF_TEST_RESPONSE_HEADER,
        })

    def _discard_stale_business_input(self) -> None:
        retained = deque(
            event
            for event in self._pending_events
            if event.get("message_name") == "SAFETY_SENSOR_EVENT"
        )
        self._pending_events = retained
        decoded, invalid_smoke = self._read_available_decoded()
        for item in decoded:
            if item["frame_type"] == "SMOKE":
                self._pending_events.append(self._to_event(item))
        for _ in range(invalid_smoke):
            self._pending_events.append(
                self._to_safety_event(None, health="PROTOCOL_ERROR")
            )
        self._parser.discard_incomplete_except({SMOKE_HEADER})

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
        if not self._wait_for_background_turn(deadline):
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._io_lock.acquire(timeout=remaining):
            return None
        try:
            if self._pending_events:
                return self._pending_events.popleft()
            if not self.is_open:
                return None
            while time.monotonic() < deadline:
                chunk = self._read_chunk(deadline)
                if not chunk:
                    continue
                invalid_smoke_before = self._parser.invalid_count(SMOKE_HEADER)
                decoded = self._parser.feed(chunk)
                events = [
                    self._to_event(item)
                    for item in decoded
                    if item["frame_type"] not in {
                        "SELF_TEST",
                        "FIRMWARE_STATUS",
                    }
                ]
                self._pending_events.extend(events)
                self._queue_invalid_smoke_events(invalid_smoke_before)
                if self._pending_events:
                    return self._pending_events.popleft()
            return None
        finally:
            self._io_lock.release()

    def has_pending_business_result(self) -> bool:
        """Drain immediately available bytes and report queued DD/EF facts.

        Recovery uses this after its read-only F3/F1 probes.  A terminal fact
        already present in the UART buffer must be processed by the normal
        state machine and may not be discarded by an operator quarantine.
        """

        with self._foreground_io("PENDING_BUSINESS_RESULT_CHECK"):
            if self.is_open:
                decoded, invalid_smoke = self._read_available_decoded()
                for item in decoded:
                    frame_type = item["frame_type"]
                    if frame_type == "SELF_TEST":
                        self._pending_events.append(
                            self._to_safety_event(
                                item["smoke_code"],
                                raw_frame_hex=item["raw_frame_hex"],
                            )
                        )
                    elif frame_type == "FIRMWARE_STATUS":
                        logger.warning(
                            "discarding unsolicited firmware status"
                        )
                    else:
                        self._pending_events.append(self._to_event(item))
                for _ in range(invalid_smoke):
                    self._pending_events.append(
                        self._to_safety_event(
                            None, health="PROTOCOL_ERROR"
                        )
                    )
            return any(
                event.get("message_name")
                in {"COMPAT_DELIVERY_RESULT", "COMPAT_CLEAN_RESULT"}
                for event in self._pending_events
            )

    def _queue_invalid_smoke_events(self, invalid_before: int) -> None:
        invalid_after = self._parser.invalid_count(SMOKE_HEADER)
        for _ in range(max(0, invalid_after - invalid_before)):
            self._pending_events.append(
                self._to_safety_event(None, health="PROTOCOL_ERROR")
            )

    def _to_event(self, decoded: dict) -> dict:
        frame_type = decoded["frame_type"]
        if frame_type == "SMOKE":
            return self._to_safety_event(
                decoded["smoke_code"],
                raw_frame_hex=decoded["raw_frame_hex"],
            )
        if frame_type not in {"DELIVERY", "CLEAN"}:
            raise ValueError(f"unsupported decoded frame: {frame_type}")
        self._mcu_event_sequence += 1
        delivery = frame_type == "DELIVERY"
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

    def _to_safety_event(
        self,
        smoke_code: Optional[int],
        *,
        health: Optional[str] = None,
        raw_frame_hex: Optional[str] = None,
    ) -> dict:
        self._mcu_event_sequence += 1
        if smoke_code == 0:
            smoke_state = "NORMAL"
            smoke_health = "OK"
            fault_code = "NONE"
        elif smoke_code == 1:
            smoke_state = "ALARM"
            smoke_health = "OK"
            fault_code = "NONE"
        else:
            smoke_state = "UNKNOWN"
            smoke_health = health or "SENSOR_FAULT"
            fault_code = "SMOKE_SENSOR"
        return {
            "message_name": "SAFETY_SENSOR_EVENT",
            "message_type": COMPAT_SAFETY_EVENT_TYPE,
            "flags": 0,
            "tx_sequence": self._mcu_event_sequence,
            "payload": {
                "mcuBootId": self._mcu_boot_id,
                "mcuEventSequence": self._mcu_event_sequence,
                "uptimeMs": int(time.monotonic() * 1000),
                "portNo": 1,
                "smokeState": smoke_state,
                "smokeSensorHealth": smoke_health,
                "faultCode": fault_code,
                "workType": "NONE",
                "workUid": None,
                "compatibilityMode": True,
                "rawFrameHex": raw_frame_hex,
            },
        }

    @staticmethod
    def _successful_self_test(decoded: dict) -> dict:
        smoke_code = decoded["smoke_code"]
        if smoke_code == 0:
            smoke_state = "NORMAL"
            smoke_health = "OK"
            fault_code = None
        elif smoke_code == 1:
            smoke_state = "ALARM"
            smoke_health = "OK"
            fault_code = None
        else:
            smoke_state = "UNKNOWN"
            smoke_health = "SENSOR_FAULT"
            fault_code = "SMOKE_SENSOR"
        weight_valid = decoded["weight_valid"]
        infrared_valid = decoded["infrared_valid"]
        return {
            "queryStatus": "OK",
            "communicationHealthy": True,
            "portNo": 1,
            "validFlags": decoded["valid_flags"],
            "weightValid": weight_valid,
            "weightGrams": (
                decoded["weight_grams"] if weight_valid else None
            ),
            "weightMeasurementUid": (
                str(uuid.uuid4()) if weight_valid else None
            ),
            "infraredValid": infrared_valid,
            "infraredBlocked": (
                decoded["infrared_blocked"]
                if infrared_valid
                else None
            ),
            "smokeCode": smoke_code,
            "smokeState": smoke_state,
            "smokeSensorHealth": smoke_health,
            "faultCode": fault_code,
            "rawFrameHex": decoded["raw_frame_hex"],
        }

    def _remember_firmware_status(self, decoded: dict) -> None:
        self._mcu_firmware_version = decoded["firmware_version"]
        self._mcu_firmware_version_code = decoded["firmware_version_code"]
        self._mcu_firmware_identity = decoded["firmware_identity_hex"]
        self._fixed_frame_revision = decoded["protocol_revision"]
        if (
            decoded["status_code"] == 0
            and decoded["protocol_revision"] == 2
            and 5 <= len(decoded["firmware_version"]) <= 32
        ):
            self._verified_firmware_identity = {
                "queryStatus": "OK",
                "statusCode": 0,
                "fixedFrameRevision": 2,
                "firmwareVersionCode": decoded[
                    "firmware_version_code"
                ],
                "firmwareVersion": decoded["firmware_version"],
                "firmwareIdentityHex": decoded[
                    "firmware_identity_hex"
                ],
            }
        else:
            self._verified_firmware_identity = None

    @staticmethod
    def _successful_firmware_query(decoded: dict) -> dict:
        return {
            "queryStatus": "OK",
            "mode": decoded["mode"],
            "statusCode": decoded["status_code"],
            "status": decoded["status"],
            "protocolRevision": decoded["protocol_revision"],
            "firmwareVersionCode": decoded["firmware_version_code"],
            "firmwareVersion": decoded["firmware_version"],
            "firmwareIdentityHex": decoded["firmware_identity_hex"],
            "safeFlags": decoded["safe_flags"],
            "rawFrameHex": decoded["raw_frame_hex"],
        }

    @staticmethod
    def _failed_firmware_query(query_status: str, mode: int) -> dict:
        return {
            "queryStatus": query_status,
            "mode": mode,
            "statusCode": None,
            "status": None,
            "protocolRevision": None,
            "firmwareVersionCode": None,
            "firmwareVersion": None,
            "firmwareIdentityHex": None,
            "safeFlags": None,
            "rawFrameHex": None,
        }

    @staticmethod
    def _failed_self_test(query_status: str) -> dict:
        health = (
            "TIMEOUT" if query_status == "TIMEOUT" else "PROTOCOL_ERROR"
        )
        return {
            "queryStatus": query_status,
            "communicationHealthy": False,
            "portNo": 1,
            "validFlags": 0,
            "weightValid": False,
            "weightGrams": None,
            "weightMeasurementUid": None,
            "infraredValid": False,
            "infraredBlocked": None,
            "smokeCode": None,
            "smokeState": "UNKNOWN",
            "smokeSensorHealth": health,
            "faultCode": "SMOKE_SENSOR",
            "rawFrameHex": None,
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
