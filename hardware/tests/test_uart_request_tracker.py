import pytest

import uart2_protocol as uart
from uart_request_tracker import UartRequestReplyTracker


def frame(name, sequence, values, *, sender="EDGE"):
    return uart.encode_frame(name, sequence, uart.encode_payload(name, values))


def test_idle_time_never_creates_a_communication_timeout():
    tracker = UartRequestReplyTracker(timeout_ms=5000)

    assert tracker.expire(500_000) == []
    assert tracker.take_timeouts() == []


def test_timeout_starts_only_after_complete_write_and_at_exact_deadline():
    tracker = UartRequestReplyTracker(timeout_ms=5000)
    request = frame("BOOT_PROBE", 1, {"probeId": 41})

    tracker.record_write_failure(request, 0, "UART_SHORT_WRITE")
    assert tracker.expire(100_000) == []
    failure = tracker.take_write_failures()
    assert [event.error for event in failure] == ["UART_SHORT_WRITE"]

    assert tracker.register_complete_write(request, 100)
    assert tracker.expire(5099) == []
    expired = tracker.expire(5100)
    assert len(expired) == 1
    assert expired[0].request_name == "BOOT_PROBE"


def test_wrong_reply_cannot_satisfy_request_but_exact_late_reply_can():
    tracker = UartRequestReplyTracker(timeout_ms=5000)
    request = frame("BOOT_PROBE", 1, {"probeId": 41})
    tracker.register_complete_write(request, 0)

    wrong = frame(
        "BOOT_PROBE_REPLY",
        1,
        {"probeId": 42, "mcuBootId": 7},
        sender="MCU",
    )
    assert tracker.accept_reply(wrong, 1000) == []
    tracker.expire(5000)
    exact = frame(
        "BOOT_PROBE_REPLY",
        2,
        {"probeId": 41, "mcuBootId": 7},
        sender="MCU",
    )
    matched = tracker.accept_reply(exact, 5100)
    assert len(matched) == 1
    assert matched[0].late is True
    assert tracker.last_matched_ms == 5100


def test_later_exact_reply_clears_earlier_unanswered_same_class_queries():
    tracker = UartRequestReplyTracker(timeout_ms=5000)
    first = frame("BOOT_PROBE", 1, {"probeId": 1})
    second = frame("BOOT_PROBE", 2, {"probeId": 2})
    tracker.register_complete_write(first, 0)
    tracker.register_complete_write(second, 1000)

    reply = frame(
        "BOOT_PROBE_REPLY",
        2,
        {"probeId": 2, "mcuBootId": 7},
        sender="MCU",
    )
    assert len(tracker.accept_reply(reply, 4999)) == 1
    assert tracker.expire(9000) == []


def test_control_command_wait_is_independent_from_unrelated_replies():
    tracker = UartRequestReplyTracker(timeout_ms=5000)
    identity = {
        "mcuCommandUid": "11111111-1111-4111-8111-111111111111",
        "commandDigestSha256": "0" * 64,
        "targetMcuBootId": 42,
        "commandSequence": 7,
    }
    values = identity | {
        "snapshotUid": "22222222-2222-4222-8222-222222222222"
    }
    identity["commandDigestSha256"] = values[
        "commandDigestSha256"
    ] = uart.compute_command_digest("QUERY_STATE", values)
    command = frame(
        "QUERY_STATE",
        7,
        values,
    )
    tracker.register_complete_write(command, 0)
    boot = frame("BOOT_PROBE", 8, {"probeId": 8})
    tracker.register_complete_write(boot, 1000)
    boot_reply = frame(
        "BOOT_PROBE_REPLY",
        8,
        {"probeId": 8, "mcuBootId": 42},
        sender="MCU",
    )
    tracker.accept_reply(boot_reply, 2000)

    expired = tracker.expire(5000)
    assert len(expired) == 1
    assert expired[0].critical is True
    assert expired[0].identity == identity


def test_transport_never_registers_short_or_exceptional_write():
    from uart2_transport import NativeUartTransport

    class Port:
        timeout, write_timeout, is_open, in_waiting = 0, 0.1, True, 0

        def __init__(self, result):
            self.result = result

        def write(self, value):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        def read(self, _count):
            return b""

    request = frame("BOOT_PROBE", 1, {"probeId": 1})
    short = NativeUartTransport(Port(1), clock=lambda: 0)
    assert short.write(request) == 1
    assert short.requests.expire(100_000) == []
    assert short.requests.take_write_failures()[0].error == "UART_SHORT_WRITE"

    failed = NativeUartTransport(Port(OSError("broken")), clock=lambda: 0)
    with pytest.raises(OSError, match="broken"):
        failed.write(request)
    assert failed.requests.expire(100_000) == []
    assert failed.requests.take_write_failures()[0].error == "UART_WRITE_FAILED"


def test_transport_deadline_uses_time_after_complete_endpoint_write():
    from uart2_transport import NativeUartTransport

    current = [100]

    class Port:
        timeout, write_timeout, is_open, in_waiting = 0, 0.1, True, 0

        def write(self, value):
            current[0] = 200
            return len(value)

        def read(self, _count):
            return b""

    request = frame("BOOT_PROBE", 1, {"probeId": 1})
    transport = NativeUartTransport(Port(), clock=lambda: current[0])
    assert transport.poll(100) == []
    assert transport.write(request) == len(request)
    assert transport.requests.expire(5_199) == []
    expired = transport.requests.expire(5_200)
    assert len(expired) == 1
    assert expired[0].written_at_ms == 200
