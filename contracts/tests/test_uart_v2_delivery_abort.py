"""An interrupted cycle names its original cause, never a successful close."""
import pytest
from contracts.tests.test_uart_v2_actuator_events import check_frame
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard
from contractlib import load_uart_registry, encode_uart_payload, uart_message_specs


def abort_values(**changes):
    return dict(mcuBootId=42, mcuEventSequence=9, uptimeMs=1200,
        mcuCommandUid="11111111-1111-4111-8111-111111111111",
        sessionUid="22222222-2222-4222-8222-222222222222", portNo=1, roundIndex=1,
        abortReason="UPDATE_STOPPED", openDispatched=True, selectionEventSequence=0) | changes


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_cycle_abort_has_immutable_identity_and_explicit_cause(codec, c_guard, language):
    name = "DELIVERY_CYCLE_ABORTED"
    raw = encode_uart_payload(load_uart_registry(), name, abort_values())
    assert len(raw) == 61
    check_frame(codec, c_guard, language, name, raw, True)
    assert codec.decode_payload(name, raw) == abort_values()


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("changes", [
    {"abortReason": 1}, {"abortReason": 2}, {"selectionEventSequence": 1},
    {"roundIndex": 2}, {"roundIndex": 2, "selectionEventSequence": 9},
    {"roundIndex": 2, "selectionEventSequence": 10}, {"openDispatched": 2},
])
def test_contradictory_abort_facts_are_rejected_before_dispatch(codec, c_guard, language, changes):
    registry, name = load_uart_registry(), "DELIVERY_CYCLE_ABORTED"
    raw = bytearray(encode_uart_payload(registry, name, abort_values()))
    fields = {f["name"]: f for f in uart_message_specs(registry)[name]["fields"]}
    for key, value in changes.items():
        field = fields[key]
        raw[field["offset"]:field["offset"] + field["minimumSize"]] = value.to_bytes(field["minimumSize"], "big")
    check_frame(codec, c_guard, language, name, bytes(raw), False)
