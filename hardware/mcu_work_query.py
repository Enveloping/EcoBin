"""Candidate native UART work/device-facts observers. Not enabled by main/UartLink.

Single foreground owner; caller restores the ORIGINAL accepted START/RESUME
identity from its business ledger. The injected writer must be the exclusive
UART owner, perform one bounded write, and must not queue/retry/copy a request.
These components open no port, send only read-only queries, and never
clear occupancy, classify financial data, acknowledge a result or authorize mechanical work.

Every attempt uses a committed query ID. Partial writes and exceptions stay
unknown; the next attempt has a NEW identity. Late replies are diagnostic only
(not accepted here). A fresh NOT_FOUND is not proof of non-execution.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from edge_store import EdgeStore
import uart2_protocol as uart


class _McuReadOnlyQuery:
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int],
                 original_identity: Mapping, *, request_name: str, reply_name: str, interval_ms: int):
        if (request_name, reply_name) not in {
            ("QUERY_WORK", "WORK_QUERY_REPLY"), ("QUERY_DEVICE_FACTS", "DEVICE_FACTS_REPLY"),
            ("QUERY_DEVICE_IDENTITY", "DEVICE_IDENTITY_REPLY"),
            ("QUERY_COMMAND", "COMMAND_QUERY_RESULT"), ("QUERY_RESULT", "RESULT_QUERY_REPLY"),
            ("QUERY_PROCESS_EVENT", "PROCESS_EVENT_QUERY_REPLY"),
            ("QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_QUERY_REPLY")
        }:
            raise ValueError("only supported read-only queries are allowed")
        if type(interval_ms) is not int or interval_ms < 1:
            raise ValueError("query interval must be a positive integer")
        self._request_name, self._reply_name = request_name, reply_name
        fields = {field["name"] for field in uart.MESSAGE_SPECS[request_name]["fields"]} - {"queryId"}
        if set(original_identity) != fields:
            raise ValueError("complete query identity required")
        encoded = uart.encode_payload(request_name, dict(original_identity) | {"queryId": 1})
        self._identity = uart.decode_payload(request_name, encoded)
        del self._identity["queryId"]
        self._store, self._write = store, write
        self._interval = interval_ms
        self._last_now = -1
        self._deadline = 0
        self._request_started_ms = 0
        self._pending: bytes | None = None
        self._reply: bytes | None = None
        self._observation: dict | None = None
        self._conflict = False
        self.conflict_payload: bytes | None = None
        self.last_write_error: str | None = None

    def _check_time(self, now_ms: int) -> None:
        if type(now_ms) is not int or now_ms < 0 or now_ms < self._last_now:
            raise ValueError("query clock must be non-negative monotonic milliseconds")
        self._last_now = now_ms

    def poll(self, now_ms: int) -> int | None:
        """At most one read-only write; returns new ID or None when not due."""
        self._check_time(now_ms)
        if now_ms < self._deadline:
            return None
        self._pending = self._reply = self._observation = None
        self._conflict = False
        self.conflict_payload = None
        self.last_write_error = None
        query_id = self._store.reserve_native_query_id()  # errors propagate, no write
        payload = uart.encode_payload(self._request_name, self._identity | {"queryId": query_id})
        frame = uart.encode_frame(self._request_name, ((query_id - 1) % 0xFFFFFFFF) + 1, payload)
        self._pending = payload
        self._request_started_ms = now_ms
        self._deadline = now_ms + self._interval
        try:
            count = self._write(frame)
            if type(count) is not int or count != len(frame):
                self.last_write_error = "SHORT_WRITE"
        except OSError:
            self.last_write_error = "WRITE_FAILED"
        return query_id

    def accept_frame(self, frame: bytes, now_ms: int) -> bool:
        """Accept a valid, fresh exact echo. Never acts on an unrelated frame."""
        self._check_time(now_ms)
        if self._pending is None or now_ms >= self._deadline or self._conflict:
            return False
        try:
            decoded = uart.decode_frame(frame, sender_role="MCU")
            if decoded["messageName"] != self._reply_name:
                return False
            payload = decoded["payload"]
            values = uart.decode_payload(self._reply_name, payload)
        except (ValueError, TypeError):
            return False
        if payload[:len(self._pending)] != self._pending:
            return False
        if self._reply is not None and self._reply != payload:
            self._conflict = True
            self.conflict_payload = payload
            self._observation = None
            return False
        self._reply = payload
        self._observation = values
        return True

    def observation(self, now_ms: int) -> dict | None:
        """None means unknown/stale, not idle. Returned facts grant no actions."""
        self._check_time(now_ms)
        if now_ms >= self._deadline or self._observation is None:
            return None
        return dict(self._observation)

    @property
    def request_started_ms(self) -> int:
        """Origin of the current reply's conservative transport-age allowance."""
        return self._request_started_ms


class McuActuatorEventQuery(_McuReadOnlyQuery):
    """Always fetch oldest unconfirmed data; a missing event is not work idle."""
    def __init__(self, store, write, boot_id, *, interval_ms=1000):
        super().__init__(store, write, dict(targetMcuBootId=boot_id, afterMcuEventSequence=0),
            request_name="QUERY_ACTUATOR_EVENT", reply_name="ACTUATOR_EVENT_QUERY_REPLY", interval_ms=interval_ms)


class McuWorkQuery(_McuReadOnlyQuery):
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int],
                 original_identity: Mapping, *, interval_ms: int = 1000):
        super().__init__(store, write, original_identity, request_name="QUERY_WORK",
                         reply_name="WORK_QUERY_REPLY", interval_ms=interval_ms)


class McuCommandQuery(_McuReadOnlyQuery):
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int],
                 original_identity: Mapping, *, interval_ms: int = 1000):
        super().__init__(store, write, original_identity, request_name="QUERY_COMMAND",
                         reply_name="COMMAND_QUERY_RESULT", interval_ms=interval_ms)


class McuResultQuery(_McuReadOnlyQuery):
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int],
                 original_identity: Mapping, *, interval_ms: int = 1000):
        super().__init__(store, write, original_identity, request_name="QUERY_RESULT",
                         reply_name="RESULT_QUERY_REPLY", interval_ms=interval_ms)


class McuProcessEventQuery(_McuReadOnlyQuery):
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int],
                 original_identity: Mapping, *, interval_ms: int = 1000):
        super().__init__(store, write, original_identity, request_name="QUERY_PROCESS_EVENT",
                         reply_name="PROCESS_EVENT_QUERY_REPLY", interval_ms=interval_ms)


class McuDeviceFactsQuery(_McuReadOnlyQuery):
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int], *,
                 target_mcu_boot_id: int, port_no: int, interval_ms: int = 1000):
        super().__init__(store, write, {"targetMcuBootId": target_mcu_boot_id, "portNo": port_no},
                         request_name="QUERY_DEVICE_FACTS", reply_name="DEVICE_FACTS_REPLY", interval_ms=interval_ms)

    def latest_weight(self, now_ms: int, *, maximum_age_ms: int = 750) -> int | None:
        """Fresh raw grams only; this is neither a stable result nor admission.

        Add the entire local time since query initiation as a conservative
        transport/processing allowance. MCU and Pi uptime clocks are not compared
        directly. Stale/failed/missing readings remain inspectable as observations.
        """
        if type(maximum_age_ms) is not int or maximum_age_ms < 0:
            raise ValueError("maximum sample age must be a non-negative integer")
        facts = self.observation(now_ms)
        if facts is None or facts["status"] != "AVAILABLE" or facts["scaleReadStatus"] != "VALID":
            return None
        age = facts["capturedUptimeMs"] - facts["scaleCapturedUptimeMs"] + now_ms - self._request_started_ms
        return facts["scaleWeightGrams"] if age <= maximum_age_ms else None


class McuDeviceIdentityQuery(_McuReadOnlyQuery):
    """Read-only identity and command high-water observation for one MCU boot."""

    def __init__(self, store: EdgeStore, write: Callable[[bytes], int], *,
                 target_mcu_boot_id: int, interval_ms: int = 1000):
        super().__init__(store, write, {"targetMcuBootId": target_mcu_boot_id},
                         request_name="QUERY_DEVICE_IDENTITY",
                         reply_name="DEVICE_IDENTITY_REPLY", interval_ms=interval_ms)
