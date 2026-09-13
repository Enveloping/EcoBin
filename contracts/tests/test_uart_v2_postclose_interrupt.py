"""A post-CLOSE interruption is a separate fact, never replacement of CLOSE."""
import pytest
from contracts.tests.test_uart_v2_actuator_events import check_frame
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard
from contractlib import load_uart_registry, encode_uart_payload


def interrupt_values(**changes):
    return dict(mcuBootId=42, mcuEventSequence=9, uptimeMs=1200,
        mcuCommandUid="11111111-1111-4111-8111-111111111111",
        sessionUid="22222222-2222-4222-8222-222222222222", portNo=1, roundIndex=1,
        interruptedPhase="DELIVERY_CLOSE_TRAVEL_WAIT", interruptionReason="UPDATE_STOPPED",
        postCloseMeasurementEventSequence=0) | changes


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_postclose_interruption_retains_original_scope_and_explicit_phase(codec, c_guard, language):
    name = "DELIVERY_POSTCLOSE_INTERRUPTED"
    raw = encode_uart_payload(load_uart_registry(), name, interrupt_values())
    assert len(raw) == 61
    check_frame(codec, c_guard, language, name, raw, True)
    assert codec.decode_payload(name, raw) == interrupt_values()


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("changes", [
    {"interruptedPhase": "IDLE"}, {"interruptedPhase": "DELIVERY_OPEN_COMMAND"},
    {"interruptedPhase": "CLEAN_FINALIZING"}, {"postCloseMeasurementEventSequence": 1},
    {"interruptedPhase": "DELIVERY_POSTCLOSE_MEASURING"},
    {"interruptedPhase": "DELIVERY_WAIT_SELECTION", "postCloseMeasurementEventSequence": 9},
    {"interruptedPhase": "DELIVERY_FINALIZING", "postCloseMeasurementEventSequence": 10},
])
def test_postclose_interruption_rejects_wrong_stage_or_measurement_reference(codec, c_guard, language, changes):
    name = "DELIVERY_POSTCLOSE_INTERRUPTED"
    raw = encode_uart_payload(load_uart_registry(), name, interrupt_values(**changes), validate_semantics=False)
    check_frame(codec, c_guard, language, name, raw, False)
