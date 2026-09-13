"""A cancelled acquisition is neither a sensor timeout nor an unattempted slot."""
import pytest
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard, process_message_values
from contracts.tests.test_uart_v2_actuator_events import check_frame
from contractlib import load_uart_registry, encode_uart_payload


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_interrupted_measurement_preserves_attempt_identity_and_partial_count(codec, c_guard, language):
    registry = load_uart_registry()
    for name in registry["sessionPolicy"]["processMeasurementMessages"]:
        values = process_message_values(registry, name) | dict(measurementKind="INTERRUPTED",
            faultCode="MEASUREMENT_INTERRUPTED", measurementElapsedMs=300, sampleCount=1,
            sampleSpanGrams=0, reportedWeightGrams=0)
        raw = encode_uart_payload(registry, name, values)
        check_frame(codec, c_guard, language, name, raw, True)
        assert codec.decode_payload(name, raw) == values


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("changes", [{"faultCode": "NONE"}, {"faultCode": "WEIGHT_TIMEOUT"}, {"measurementKind": "UNAVAILABLE"}])
def test_interrupt_kind_and_cause_must_agree_in_process_data(codec, c_guard, language, changes):
    registry, name = load_uart_registry(), "WORK_POSTCLOSE_WEIGHT_READY"
    values = process_message_values(registry, name) | dict(measurementKind="INTERRUPTED",
        faultCode="MEASUREMENT_INTERRUPTED", measurementElapsedMs=300, sampleCount=1,
        sampleSpanGrams=0, reportedWeightGrams=0) | changes
    raw = encode_uart_payload(registry, name, values, validate_semantics=False)
    check_frame(codec, c_guard, language, name, raw, False)


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("changes,valid", [({}, True), ({"finishReason": "DELIVERY_END"}, False),
    ({"finalFaultCode": "WEIGHT_TIMEOUT"}, False), ({"finalKind": "UNAVAILABLE"}, False)])
def test_final_result_keeps_interruption_distinct_from_success_and_sensor_failure(codec, c_guard, language, changes, valid):
    from contracts.tests.test_uart_v2_result import result_values
    values = result_values() | dict(finishReason="FAILED", finalKind="INTERRUPTED",
        finalFaultCode="MEASUREMENT_INTERRUPTED", finalElapsedMs=300, finalSampleCount=1,
        finalSpanGrams=0, finalWeightGrams=0) | changes
    values["resultDigestSha256"] = codec.compute_result_digest(values)
    raw = encode_uart_payload(load_uart_registry(), "WORK_RESULT", values, validate_semantics=False)
    check_frame(codec, c_guard, language, "WORK_RESULT", raw, valid, flags=0)
