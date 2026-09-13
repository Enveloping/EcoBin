"""A local continuation reports its own cause, never a new Pi command."""
import pytest
from contracts.tests.test_uart_v2_actuator_events import actuator_values, check_frame
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard
from contractlib import load_uart_registry, encode_uart_payload, uart_message_specs


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_local_door_result_preserves_authorizing_command_and_own_selection_cause(codec, c_guard, language):
    name = "DELIVERY_LOCAL_DOOR_RESULT"
    values = actuator_values("DELIVERY_DOOR_COMMAND_RESULT", roundIndex=2, selectionEventSequence=6)
    raw = encode_uart_payload(load_uart_registry(), name, values)
    check_frame(codec, c_guard, language, name, raw, True)
    assert codec.decode_payload(name, raw) == values


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("cause", [0, 7, 8, 0xFFFFFFFF])
def test_local_motion_cause_must_precede_its_output_event(codec, c_guard, language, cause):
    name = "DELIVERY_LOCAL_DOOR_RESULT"
    registry = load_uart_registry()
    values = actuator_values("DELIVERY_DOOR_COMMAND_RESULT", roundIndex=2, selectionEventSequence=6)
    raw = bytearray(encode_uart_payload(registry, name, values))
    offset = next(f["offset"] for f in uart_message_specs(registry)[name]["fields"] if f["name"] == "selectionEventSequence")
    raw[offset:offset + 4] = cause.to_bytes(4, "big")
    check_frame(codec, c_guard, language, name, bytes(raw), False)
