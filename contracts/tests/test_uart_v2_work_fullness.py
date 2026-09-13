"""A business-scoped terminal weight retains independent fullness evidence."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import (ContractError, load_uart_registry, encode_uart_payload, decode_uart_payload,
                         encode_uart_frame, uart_message_specs)
from test_uart_v2_process_measurement import process_values, process_message_values, codec, c_guard


def fullness_values():
    return dict(workFullnessStatus="COMPLETE", fullnessGroupSequence=1,
        fullnessConfigContentSha256="ab" * 32, fullnessMcuPayloadSha256="cd" * 32,
        fullnessStartedUptimeMs=9500, fullnessCompletedUptimeMs=10500, fullnessLastCapturedUptimeMs=10400,
        workFullnessSensorKind="ULTRASONIC", workFullnessSensorValue="BLOCKED", workFullnessBasis="MEASURED_MEDIAN",
        fullnessDistancePresent=True, fullnessDistanceMm=100,
        fullnessRequestedSampleCount=5, fullnessCompletedSampleCount=5, fullnessValidSampleCount=4,
        fullnessMinimumValidSampleCount=3, fullnessDistanceThresholdMm=200, fullnessStopReason="NONE")


def test_postclose_record_keeps_weight_and_sensor_times_without_inventing_another_business():
    registry = load_uart_registry()
    values = process_values() | fullness_values()
    raw = encode_uart_payload(registry, "WORK_POSTCLOSE_WEIGHT_READY", values)
    assert len(raw) == 207
    observed = decode_uart_payload(registry, "WORK_POSTCLOSE_WEIGHT_READY", raw)
    assert observed == values
    assert observed["uptimeMs"] == 10000  # original weight resolution, not refreshed
    assert observed["fullnessCompletedUptimeMs"] == 10500  # independent actual sensor group


@pytest.mark.parametrize("changes", [
    {"workFullnessStatus": "NOT_SAMPLED"}, {"fullnessGroupSequence": 0},
    {"fullnessConfigContentSha256": "00" * 32}, {"fullnessMcuPayloadSha256": "00" * 32},
    {"fullnessCompletedSampleCount": 4}, {"fullnessValidSampleCount": 6},
    {"fullnessMinimumValidSampleCount": 0}, {"fullnessRequestedSampleCount": 2},
    {"fullnessMinimumValidSampleCount": 6}, {"fullnessDistanceThresholdMm": 0},
    {"workFullnessSensorKind": "DIGITAL_INFRARED"}, {"workFullnessSensorKind": "NONE"},
    {"fullnessCompletedUptimeMs": 9499}, {"fullnessLastCapturedUptimeMs": 10501},
    {"fullnessLastCapturedUptimeMs": 9499}, {"fullnessStopReason": "CALLER_CANCELLED"},
    {"fullnessValidSampleCount": 2}, {"workFullnessSensorValue": "CLEAR"},
    {"workFullnessBasis": "NONE"}, {"workFullnessStatus": "INTERRUPTED"},
])
@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("name", ["WORK_POSTCLOSE_WEIGHT_READY", "CLEAN_FINAL_WEIGHT_READY"])
def test_inconsistent_group_cannot_enter_a_business_record(changes, language, name, codec, c_guard):
    registry = load_uart_registry()
    spec = uart_message_specs(registry)[name]
    raw = bytearray(encode_uart_payload(registry, name, process_message_values(registry, name) | fullness_values()))
    for key, value in changes.items():
        field = next(field for field in spec["fields"] if field["name"] == key)
        if "enum" in field:
            value = registry["enums"][field["enum"]]["values"].get(value, value)
        size, offset = field["minimumSize"], field["offset"]
        raw[offset:offset + size] = bytes.fromhex(value) if field["type"] == "sha256" else int(value).to_bytes(size, "big")
    if language == "c":
        frame = encode_uart_frame(registry, spec["id"], 1, 1, bytes(raw))
        assert c_guard(frame, len(frame)) != 0
    else:
        with pytest.raises(codec.ProtocolError if language == "python" else ContractError):
            if language == "python":
                codec.decode_payload(name, bytes(raw))
            else:
                decode_uart_payload(registry, name, bytes(raw))


@pytest.mark.parametrize("name", ["WORK_POSTCLOSE_WEIGHT_READY", "CLEAN_FINAL_WEIGHT_READY"])
@pytest.mark.parametrize("change", [
    {},
    {"fullnessDistanceMm": 200, "workFullnessSensorValue": "CLEAR"},
    {"fullnessValidSampleCount": 0, "workFullnessBasis": "NO_ECHO_CLEAR_FALLBACK",
     "workFullnessSensorValue": "CLEAR", "fullnessDistancePresent": False, "fullnessDistanceMm": 0},
    {"fullnessValidSampleCount": 2, "workFullnessBasis": "INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK",
     "workFullnessSensorValue": "CLEAR", "fullnessDistancePresent": False, "fullnessDistanceMm": 0},
    {"workFullnessStatus": "INTERRUPTED", "fullnessStopReason": "CALLER_CANCELLED",
     "fullnessCompletedSampleCount": 2, "fullnessValidSampleCount": 1,
     "workFullnessSensorValue": "NOT_OBSERVED", "workFullnessBasis": "NONE",
     "fullnessDistancePresent": False, "fullnessDistanceMm": 0},
])
def test_actual_terminal_outcomes_round_trip_all_three_codecs(name, change, codec, c_guard):
    registry = load_uart_registry()
    values = process_message_values(registry, name) | fullness_values() | change
    raw = encode_uart_payload(registry, name, values)
    assert decode_uart_payload(registry, name, raw) == values
    assert codec.decode_payload(name, raw) == values
    assert codec.encode_payload(name, values) == raw
    frame = encode_uart_frame(registry, uart_message_specs(registry)[name]["id"], 1, 1, raw)
    assert c_guard(frame, len(frame)) == 0


@pytest.mark.parametrize("name", ["WORK_POSTCLOSE_WEIGHT_READY", "CLEAN_FINAL_WEIGHT_READY"])
def test_old_packet_is_not_silently_upgraded_to_unsampled_evidence(name, codec, c_guard):
    registry = load_uart_registry()
    spec = uart_message_specs(registry)[name]
    raw = encode_uart_payload(registry, name, process_message_values(registry, name))
    offset = next(field["offset"] for field in spec["fields"] if field["name"] == "workFullnessStatus")
    old = raw[:offset]
    for decode, error in ((lambda: decode_uart_payload(registry, name, old), ContractError),
                          (lambda: codec.decode_payload(name, old), codec.ProtocolError)):
        with pytest.raises(error):
            decode()
    frame = encode_uart_frame(registry, spec["id"], 1, 1, old)
    assert c_guard(frame, len(frame)) != 0
