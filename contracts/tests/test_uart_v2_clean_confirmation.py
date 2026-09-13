"""Human confirmation is an original-work-scoped fact, never inferred from power."""
import hashlib

import pytest
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard, process_values
from contracts.tests.test_uart_v2_clean_intent import intent_values, intent_scope
from contractlib import load_uart_registry, encode_uart_payload

NAME = "CLEAN_COMPLETION_CONFIRMED"


def confirmation_values():
    return intent_values() | {"mcuEventSequence": 5, "uptimeMs": 13000,
        "finalMeasurementUid": process_values()["measurementUid"], "lockPowerState": "DEENERGIZED",
        "solenoidHealth": "UNKNOWN", "cleanDoorStateBasis": "CLEANER_CONFIRMATION",
        "cleanerPhysicalCloseConfirmed": True}


def final_values():
    values = process_values("CLEAN_FINAL_WEIGHT_READY")
    values["operationUid"] = values.pop("sessionUid")
    values["cleanActionSequence"] = values.pop("roundIndex")
    del values["mcuCommandUid"]
    return values | {"mcuEventSequence": 4, "uptimeMs": 12020, "measurementKind": "STABLE_MEAN",
        "reportedWeightGrams": 123, "measurementElapsedMs": 1020, "sampleCount": 5, "sampleSpanGrams": 0}


def test_confirmation_retains_original_command_configuration_and_candidate_and_has_exact_custody(codec, c_guard):
    values = confirmation_values()
    raw = encode_uart_payload(load_uart_registry(), NAME, values)
    assert len(raw) == 83 and codec.decode_payload(NAME, raw) == values
    query = codec.encode_payload("QUERY_PROCESS_EVENT", intent_scope(NAME) | {"queryId": 1})
    assert len(query) == 97
    frame = codec.encode_frame(NAME, 1, raw)
    assert c_guard(frame, len(frame)) == 0
    expected = hashlib.sha256(b"ECOBIN:UART:PROCESS-EVENT:v2\0" + bytes([62]) + len(raw).to_bytes(2, "big") + raw).hexdigest()
    assert codec.compute_process_event_digest(NAME, raw) == expected
    receipt = codec.encode_payload("PROCESS_EVENT_SAVED", dict(mcuBootId=42, mcuEventSequence=5,
        eventMessageType=NAME, eventDigestSha256=expected))
    assert len(receipt) == 45


@pytest.mark.parametrize("changes", [{"mcuBootId": 0}, {"mcuEventSequence": 0}, {"portNo": 0}, {"portNo": 7},
    {"cleanActionSequence": 0}, {"configVersion": 0}, {"configVersion": 2**53},
    {"cleanerPhysicalCloseConfirmed": False}, {"cleanerPhysicalCloseConfirmed": 2},
    {"lockPowerState": "ENERGIZED"}, {"cleanDoorStateBasis": "NOT_OBSERVABLE"},
    *[{field: "00000000-0000-0000-0000-000000000000"} for field in
      ("mcuCommandUid", "operationUid", "finalMeasurementUid")]])
def test_invalid_confirmation_cannot_pass_reference_python_or_actual_c(codec, c_guard, changes):
    raw = bytearray(codec.encode_payload(NAME, confirmation_values()))
    for field in codec.MESSAGE_SPECS[NAME]["fields"]:
        if field["name"] not in changes:
            continue
        value = changes[field["name"]]
        if "enum" in field:
            value = codec.REGISTRY["enums"][field["enum"]]["values"].get(value, value)
        data = bytes.fromhex(value.replace("-", "")) if field["type"] == "uuid" else int(value).to_bytes(field["minimumSize"], "big")
        raw[field["offset"]:field["offset"] + len(data)] = data
    with pytest.raises(ValueError):
        encode_uart_payload(load_uart_registry(), NAME, confirmation_values() | changes)
    with pytest.raises(ValueError):
        codec.decode_payload(NAME, bytes(raw))
    frame = codec.encode_frame(NAME, 1, bytes(raw))
    assert c_guard(frame, len(frame)) != 0
