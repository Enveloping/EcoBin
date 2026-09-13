"""Real C endpoint: journal reservations also protect global event-number credit."""
import ctypes as c
import pytest
import uart2_protocol as uart

from hardware.tests.test_mcu_control_endpoint import endpoint, bind, exchange, request
from hardware.tests.test_mcu_actuator_event_journal import Reservation, payload

MAX_SEQUENCE = 2**32 - 1


def reserve(endpoint, count=2):
    token = Reservation()
    assert endpoint[0].McuControlEndpoint_ReserveActuatorEvents(endpoint[1], count, c.byref(token)) == 1
    return token


def publish(endpoint, token, member, name="CLEAN_LOCK_POWER_CHANGED", **changes):
    # Only the local builder accepts a zero sequence placeholder. Never wire it.
    body = payload(name, **changes)
    template = body[:8] + bytes(4) + body[12:]
    return endpoint[0].McuControlEndpoint_PublishActuatorEvent(endpoint[1], c.byref(token),
        member, uart.MESSAGE_TYPE[name], template, len(template))


def next_held(endpoint, after=0):
    output, message = c.create_string_buffer(60), c.c_uint8()
    length = endpoint[0].McuControlEndpoint_CopyNextActuatorEvent(endpoint[1], after, c.byref(message), output, len(output))
    return (message.value, output.raw[:length]) if length else None


def cancel(endpoint, token):
    return endpoint[0].McuControlEndpoint_CancelActuatorEvents(endpoint[1], c.byref(token))


def confirm(endpoint, held):
    message, body = held
    return endpoint[0].McuControlEndpoint_ConfirmActuatorEventSaved(endpoint[1], message, body, len(body))


def test_reserved_lock_pair_cannot_be_exhausted_by_other_event_producers(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 3)
    token = Reservation()
    assert lib.McuControlEndpoint_ReserveActuatorEvents(memory, 2, c.byref(token)) == 1
    assert (token.boot_id, token.number) == (42, 1)
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == MAX_SEQUENCE - 2
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0


def test_reserved_numbers_are_assigned_at_publication_not_ahead_of_interleaved_events(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == 1
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 2
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED", uptimeMs=2000) == 3
    assert next_held(endpoint) == (uart.MESSAGE_TYPE["CLEAN_LOCK_POWER_CHANGED"], payload(mcuEventSequence=1))
    assert next_held(endpoint, 1)[1] == payload(mcuEventSequence=3, lockPowerState="DEENERGIZED", uptimeMs=2000)
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 4


def test_reserved_pair_uses_final_numbers_without_wrapping(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 2)
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == MAX_SEQUENCE - 1
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == MAX_SEQUENCE
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0
    assert next_held(endpoint, MAX_SEQUENCE - 1)[1] == payload(mcuEventSequence=MAX_SEQUENCE, lockPowerState="DEENERGIZED")
    # Retrying the same local publication must return the old number at exhaustion.
    assert publish(endpoint, token, 0) == MAX_SEQUENCE - 1
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == MAX_SEQUENCE


def test_cancel_unstarted_group_restores_both_ram_and_number_credit(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 8)
    token = reserve(endpoint, 8)
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0
    assert cancel(endpoint, token) == 1
    assert cancel(endpoint, token) == 0
    replacement = reserve(endpoint, 8)
    assert replacement.number != token.number
    assert publish(endpoint, token, 0) == 0
    assert cancel(endpoint, replacement) == 1
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == MAX_SEQUENCE - 7


def test_saved_on_does_not_cancel_or_consume_the_future_off_credit(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 2)
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == MAX_SEQUENCE - 1
    held = next_held(endpoint)
    assert confirm(endpoint, held) == 1
    assert confirm(endpoint, held) == 2
    assert cancel(endpoint, token) == 0
    assert publish(endpoint, token, 0) == MAX_SEQUENCE - 1
    assert next_held(endpoint) is None
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == MAX_SEQUENCE
    assert confirm(endpoint, next_held(endpoint)) == 1
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0
    assert publish(endpoint, token, 0) == MAX_SEQUENCE - 1
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == MAX_SEQUENCE
    assert next_held(endpoint) is None


@pytest.mark.parametrize("count", [0, 9, 255])
def test_invalid_reservation_is_atomic(endpoint, count):
    bind(endpoint)
    lib, memory, *_ = endpoint
    token = Reservation(88, 99)
    before = bytes(memory)
    assert not lib.McuControlEndpoint_ReserveActuatorEvents(memory, count, c.byref(token))
    assert bytes(memory) == before and (token.boot_id, token.number) == (88, 99)
    assert reserve(endpoint, 8).number == 1


def test_number_shortage_reserves_no_ram_and_changes_no_token(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 1)
    token = Reservation(88, 99)
    before = bytes(memory)
    assert not lib.McuControlEndpoint_ReserveActuatorEvents(memory, 2, c.byref(token))
    assert bytes(memory) == before and (token.boot_id, token.number) == (88, 99)
    token = reserve(endpoint, 1)
    assert token.number == 1 and publish(endpoint, token, 0) == MAX_SEQUENCE


def test_ram_shortage_reserves_no_number_credit(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 10)
    reserve(endpoint, 8)
    before, token = bytes(memory), Reservation(88, 99)
    assert not lib.McuControlEndpoint_ReserveActuatorEvents(memory, 1, c.byref(token))
    assert bytes(memory) == before and (token.boot_id, token.number) == (88, 99)
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == MAX_SEQUENCE - 9
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == MAX_SEQUENCE - 8
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0


@pytest.mark.parametrize("name", ["CLEAN_LOCK_POWER_CHANGED", "DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT"])
def test_all_actuator_types_share_the_counter_and_duplicate_publication_is_read_only(endpoint, name):
    bind(endpoint)
    lib, memory, *_ = endpoint
    token = reserve(endpoint, 3)
    assert publish(endpoint, token, 0, name) == 1
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 2
    assert publish(endpoint, token, 1, "SAFE_CLOSE_RESULT") == 3
    before = bytes(memory)
    assert publish(endpoint, token, 0, name) == 1
    assert bytes(memory) == before
    assert publish(endpoint, token, 0, name, uptimeMs=9999) == 0
    assert bytes(memory) == before
    assert next_held(endpoint) == (uart.MESSAGE_TYPE[name], payload(name, mcuEventSequence=1))
    assert publish(endpoint, token, 2, "DELIVERY_DOOR_COMMAND_RESULT") == 4


@pytest.mark.parametrize("damage", ["short", "long", "nonzero_sequence", "wrong_boot", "nil_command", "invalid_state", "wrong_message", "wrong_member", "wrong_token", "stale_boot", "null_body"])
def test_failed_publication_consumes_no_credit_or_sequence(endpoint, damage):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 2)
    token = reserve(endpoint)
    body = bytearray(payload())
    body[8:12] = bytes(4)
    member, message, use_token = 0, uart.MESSAGE_TYPE["CLEAN_LOCK_POWER_CHANGED"], token
    if damage == "short":
        body = body[:9]
    elif damage == "long":
        body += bytes(100)
    elif damage == "nonzero_sequence":
        body[8:12] = (123).to_bytes(4, "big")
    elif damage == "wrong_boot":
        body[:8] = (43).to_bytes(8, "big")
    elif damage in ("nil_command", "invalid_state"):
        field = next(field for field in uart.MESSAGE_SPECS["CLEAN_LOCK_POWER_CHANGED"]["fields"]
            if field["name"] == ("mcuCommandUid" if damage == "nil_command" else "lockPowerState"))
        body[field["offset"]:field["offset"] + field["minimumSize"]] = (
            bytes(field["minimumSize"]) if damage == "nil_command" else b"\xff")
    elif damage == "wrong_message":
        message = uart.MESSAGE_TYPE["WORK_PREOPEN_WEIGHT_READY"]
    elif damage == "wrong_member":
        member = 2
    elif damage == "wrong_token":
        use_token = Reservation(42, token.number + 1)
    elif damage == "stale_boot":
        use_token = Reservation(41, token.number)
    raw = None if damage == "null_body" else bytes(body)
    before = bytes(memory)
    assert not lib.McuControlEndpoint_PublishActuatorEvent(memory, c.byref(use_token), member, message, raw, len(body))
    assert bytes(memory) == before
    assert lib.McuControlEndpoint_ReserveEventSequence(memory) == 0
    assert publish(endpoint, token, 0) == MAX_SEQUENCE - 1
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == MAX_SEQUENCE


def test_probe_and_duplicate_binding_preserve_all_reserved_and_held_evidence(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 2)
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == MAX_SEQUENCE - 1
    held = next_held(endpoint)
    for proposed, expected in [(42, "ALREADY_BOUND"), (43, "ALREADY_BOUND")]:
        exchange(endpoint, request("BOOT_PROBE", {"probeId": proposed}))
        frames = exchange(endpoint, request("BIND_BOOT", {"probeId": proposed, "proposedMcuBootId": proposed}))
        reply = uart.decode_payload("BIND_BOOT_REPLY", uart.decode_frame(frames[0])["payload"])
        assert reply["status"] == expected and reply["mcuBootId"] == 42
        assert next_held(endpoint) == held
        assert not lib.McuControlEndpoint_ReserveEventSequence(memory)
        assert not cancel(endpoint, token)
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == MAX_SEQUENCE


def test_real_mcu_reset_discards_old_reservations_but_new_boot_rejects_old_tokens(endpoint):
    bind(endpoint)
    lib, memory, _, sink = endpoint
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == 1
    held = next_held(endpoint)
    lib.McuControlEndpoint_Init(memory, 1, sink, None)
    assert not lib.McuControlEndpoint_ReserveEventSequence(memory)
    untouched = Reservation(88, 99)
    assert not lib.McuControlEndpoint_ReserveActuatorEvents(memory, 2, c.byref(untouched))
    assert (untouched.boot_id, untouched.number) == (88, 99)
    assert next_held(endpoint) is None
    exchange(endpoint, request("BOOT_PROBE", {"probeId": 2}))
    exchange(endpoint, request("BIND_BOOT", {"probeId": 2, "proposedMcuBootId": 43}))
    new = reserve(endpoint)
    assert new.number == token.number and new.boot_id == 43
    assert not publish(endpoint, token, 1)
    assert not cancel(endpoint, token)
    assert confirm(endpoint, held) == 5
    assert publish(endpoint, new, 0, mcuBootId=43) == 1


def test_reclaiming_saved_on_never_unlocks_original_off_credit(endpoint):
    bind(endpoint)
    lib, memory, *_ = endpoint
    lib.TestControl_SetEventSequence(memory, MAX_SEQUENCE - 9)
    original = reserve(endpoint)
    assert publish(endpoint, original, 0) == MAX_SEQUENCE - 8
    on = next_held(endpoint)
    assert confirm(endpoint, on) == 1
    replacement = reserve(endpoint, 7)  # forces reuse of the saved ON slot
    assert publish(endpoint, original, 0) == 0
    assert confirm(endpoint, on) == 3
    assert cancel(endpoint, original) == 0
    assert not lib.McuControlEndpoint_ReserveEventSequence(memory)
    assert cancel(endpoint, replacement) == 1
    assert publish(endpoint, original, 1, lockPowerState="DEENERGIZED") == MAX_SEQUENCE - 7


def test_local_publication_and_transport_ack_neither_move_outputs_nor_release_evidence(endpoint):
    bind(endpoint)
    lib, memory, replies, _ = endpoint
    writes, reply_count = lib.TestFacts_Writes(), len(replies)
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == 1
    held = next_held(endpoint)
    assert lib.TestFacts_Writes() == writes and len(replies) == reply_count
    ack = request("ACK", {"senderBootId": 77, "referencedSenderBootId": 42,
        "referencedTxSequence": 1, "referencedMessageType": 53, "disposition": "ACCEPTED"})
    assert exchange(endpoint, ack) == []
    assert next_held(endpoint) == held
    assert cancel(endpoint, token) == 0
    assert lib.TestFacts_Writes() == writes
