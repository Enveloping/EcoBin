"""Correlate fully-written native UART requests with their exact MCU replies.

This module is intentionally memory-only.  A Pi restart starts new observation
windows; it never turns an old wall-clock timestamp into current communication
evidence.  The UART owner is the only caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import uart2_protocol as uart


QUERY_REPLIES = {
    "QUERY_ACTUATOR_EVENT": "ACTUATOR_EVENT_QUERY_REPLY",
    "QUERY_COMMAND": "COMMAND_QUERY_RESULT",
    "QUERY_DEVICE_FACTS": "DEVICE_FACTS_REPLY",
    "QUERY_DEVICE_IDENTITY": "DEVICE_IDENTITY_REPLY",
    "QUERY_PROCESS_EVENT": "PROCESS_EVENT_QUERY_REPLY",
    "QUERY_RESULT": "RESULT_QUERY_REPLY",
    "QUERY_WORK": "WORK_QUERY_REPLY",
}

DIRECT_REPLIES = {
    "BOOT_PROBE": "BOOT_PROBE_REPLY",
    "BIND_BOOT": "BIND_BOOT_REPLY",
    "ACTUATOR_EVENT_SAVED": "ACTUATOR_EVENT_SAVED_REPLY",
    "PROCESS_EVENT_SAVED": "PROCESS_EVENT_SAVED_REPLY",
    "RESULT_SAVED": "RESULT_SAVED_REPLY",
}

COMMAND_IDENTITY = (
    "mcuCommandUid",
    "commandDigestSha256",
    "targetMcuBootId",
    "commandSequence",
)


@dataclass(frozen=True)
class RequestReplyEvent:
    request_name: str
    reply_name: str | None
    channel: str
    identity: dict[str, Any]
    written_at_ms: int
    observed_at_ms: int
    request_sequence: int
    critical: bool
    late: bool = False
    error: str | None = None
    reply: dict[str, Any] | None = None


@dataclass
class _Expectation:
    request_name: str
    reply_names: tuple[str, ...]
    channel: str
    identity: dict[str, Any]
    written_at_ms: int
    request_sequence: int
    critical: bool


def _identity_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


class UartRequestReplyTracker:
    """Track request deadlines without interpreting UART silence as a fault."""

    def __init__(self, *, timeout_ms: int = 5000):
        if type(timeout_ms) is not int or timeout_ms < 1:
            raise ValueError("UART reply timeout must be a positive integer")
        self.timeout_ms = timeout_ms
        self._channels: dict[str, list[_Expectation]] = {}
        self._timed_out_channels: set[str] = set()
        self._matches: list[RequestReplyEvent] = []
        self._timeouts: list[RequestReplyEvent] = []
        self._write_failures: list[RequestReplyEvent] = []
        self._last_matched_ms: int | None = None
        self._last_registered_sequence = 0

    @property
    def last_matched_ms(self) -> int | None:
        return self._last_matched_ms

    @property
    def last_registered_sequence(self) -> int:
        return self._last_registered_sequence

    @staticmethod
    def _request(decoded: dict) -> tuple[str, tuple[str, ...], str, dict, bool] | None:
        name = decoded["messageName"]
        values = uart.decode_payload(name, decoded["payload"])
        if name in QUERY_REPLIES:
            reply = QUERY_REPLIES[name]
            # The channel retains the immutable target.  A valid reply to any
            # later query ID on that same target proves the channel is alive.
            # One exact later reply for the same query class proves this
            # transport path is responding.  Its own expectation still
            # verifies query ID, boot and every target field before clearing
            # earlier unanswered attempts in the class.
            channel = "query:" + name
            return name, (reply,), channel, values, False
        if name in DIRECT_REPLIES:
            reply = DIRECT_REPLIES[name]
            channel = "request:" + name
            return name, (reply,), channel, values, False
        spec = uart.MESSAGE_SPECS.get(name)
        if spec and spec.get("direction") == "EDGE_TO_MCU" and spec.get("ackRequired"):
            identity = {key: values[key] for key in COMMAND_IDENTITY}
            channel = "command:" + ":".join(
                _identity_text(identity[key]) for key in COMMAND_IDENTITY
            )
            return name, ("COMMAND_DECISION", "COMMAND_QUERY_RESULT"), channel, identity, True
        return None

    @staticmethod
    def _matches_reply(expectation: _Expectation, decoded: dict) -> bool:
        name = decoded["messageName"]
        if name not in expectation.reply_names:
            return False
        values = uart.decode_payload(name, decoded["payload"])
        request = expectation.identity
        if expectation.request_name == "BOOT_PROBE":
            return values["probeId"] == request["probeId"]
        if expectation.request_name == "BIND_BOOT":
            return (
                values["probeId"] == request["probeId"]
                and values["proposedMcuBootId"] == request["proposedMcuBootId"]
            )
        if expectation.request_name in DIRECT_REPLIES:
            return all(values.get(key) == value for key, value in request.items())
        return all(values.get(key) == value for key, value in request.items())

    def register_complete_write(self, frame: bytes, now_ms: int) -> bool:
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        request = self._request(decoded)
        if request is None:
            return False
        name, replies, channel, identity, critical = request
        self._last_registered_sequence += 1
        expectation = _Expectation(
            name,
            replies,
            channel,
            identity,
            now_ms,
            self._last_registered_sequence,
            critical,
        )
        bucket = self._channels.setdefault(channel, [])
        bucket.append(expectation)
        # Periodic queries have a naturally small five-second working set.
        # Retain a generous bounded tail for unusual polling intervals.
        if len(bucket) > 32:
            del bucket[:-32]
        return True

    def record_write_failure(self, frame: bytes, now_ms: int, error: str) -> None:
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        request = self._request(decoded)
        if request is None:
            return
        name, _replies, channel, identity, critical = request
        self._write_failures.append(RequestReplyEvent(
            request_name=name,
            reply_name=None,
            channel=channel,
            identity=dict(identity),
            written_at_ms=now_ms,
            observed_at_ms=now_ms,
            request_sequence=0,
            critical=critical,
            error=error,
        ))

    def accept_reply(self, frame: bytes, now_ms: int) -> list[RequestReplyEvent]:
        decoded = uart.decode_frame(frame, sender_role="MCU")
        matched: list[tuple[str, _Expectation]] = []
        for channel, expectations in tuple(self._channels.items()):
            for expectation in expectations:
                if self._matches_reply(expectation, decoded):
                    matched.append((channel, expectation))
                    break
        events = []
        for channel, expectation in matched:
            late = channel in self._timed_out_channels
            event = RequestReplyEvent(
                request_name=expectation.request_name,
                reply_name=decoded["messageName"],
                channel=channel,
                identity=dict(expectation.identity),
                written_at_ms=expectation.written_at_ms,
                observed_at_ms=now_ms,
                request_sequence=expectation.request_sequence,
                critical=expectation.critical,
                late=late,
                reply=dict(uart.decode_payload(
                    decoded["messageName"],
                    decoded["payload"],
                )),
            )
            events.append(event)
            self._matches.append(event)
            self._channels.pop(channel, None)
            self._timed_out_channels.discard(channel)
        if events:
            self._last_matched_ms = now_ms
        return events

    def expire(self, now_ms: int) -> list[RequestReplyEvent]:
        created = []
        for channel, expectations in tuple(self._channels.items()):
            if channel in self._timed_out_channels or not expectations:
                continue
            first = expectations[0]
            if now_ms - first.written_at_ms < self.timeout_ms:
                continue
            event = RequestReplyEvent(
                request_name=first.request_name,
                reply_name=None,
                channel=channel,
                identity=dict(first.identity),
                written_at_ms=first.written_at_ms,
                observed_at_ms=now_ms,
                request_sequence=first.request_sequence,
                critical=first.critical,
            )
            self._timed_out_channels.add(channel)
            self._timeouts.append(event)
            created.append(event)
        return created

    def take_matches(self) -> list[RequestReplyEvent]:
        values, self._matches = self._matches, []
        return values

    def take_timeouts(self) -> list[RequestReplyEvent]:
        values, self._timeouts = self._timeouts, []
        return values

    def take_write_failures(self) -> list[RequestReplyEvent]:
        values, self._write_failures = self._write_failures, []
        return values
