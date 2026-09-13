"""Explicit native candidate transport. Opens no device; no protocol fallback.

The caller owns the already-open serial endpoint exclusively. No background
thread, output queue, flush, ACK retry or reconnect replay. This class cannot
stop an external bridge from replaying bytes; direct-UART no-copy is required.
"""
from collections import deque
import math
import threading

import uart2_protocol as uart


class NativeUartTransport:
    def __init__(self, serial_port):
        self._port = serial_port
        self._owner = threading.get_ident()
        self._parser = uart.StreamParser(sender_role="MCU")
        self._last_now = -1
        self.diagnostics: deque[str] = deque(maxlen=32)
        self._check_endpoint()

    def _check_endpoint(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("native UART requires one foreground owner")
        timeout = self._port.write_timeout
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 1 or self._port.timeout != 0):
            raise ValueError("native UART requires bounded write timeout <=1s and nonblocking reads")
        if not self._port.is_open:
            raise OSError("native UART is closed")

    def write(self, frame: bytes) -> int:
        """One endpoint write only; short counts/errors are not repaired here."""
        self._check_endpoint()
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        uart.decode_payload(decoded["messageName"], decoded["payload"])
        return self._port.write(frame)

    def poll(self, now_ms: int) -> list[bytes]:
        """At most 512 input bytes per call, each complete frame delivered once."""
        self._check_endpoint()
        if type(now_ms) is not int or now_ms < 0 or now_ms < self._last_now:
            raise ValueError("native UART clock must be non-negative monotonic milliseconds")
        self._last_now = now_ms
        count = min(512, self._port.in_waiting)
        data = self._port.read(count) if count else b""
        if len(data) > count:
            raise ValueError("serial endpoint exceeded its bounded read")
        decoded = self._parser.feed(data, now_ms=now_ms)
        self.diagnostics.extend(self._parser.diagnostics)
        self._parser.diagnostics.clear()
        frames = []
        for frame in decoded:
            try:
                uart.decode_payload(frame["messageName"], frame["payload"])
            except ValueError:
                self.diagnostics.append("PAYLOAD_REJECTED")
                continue
            frames.append(uart.encode_frame(frame["messageName"], frame["txSequence"], frame["payload"]))
        return frames
