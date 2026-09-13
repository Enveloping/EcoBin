"""Clean interruption is independent evidence, never a physical-close fact."""
import uuid
import pytest
from contracts.tests.test_uart_v2_actuator_events import check_frame
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard
from contractlib import load_uart_registry, encode_uart_payload, uart_message_specs

NAME = "CLEAN_OPERATION_INTERRUPTED"


def interruption_values(**changes):
    return dict(mcuBootId=42, mcuEventSequence=10, uptimeMs=12000,
        mcuCommandUid="11111111-1111-4111-8111-111111111111",
        operationUid="22222222-2222-4222-8222-222222222222", portNo=1,
        cleanActionSequence=0, interruptedPhase="CLEAN_ACTIVE", interruptionReason="UPDATE_STOPPED",
        finalMeasurementEventSequence=0) | changes


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_clean_interruption_names_original_unlock_and_operation_without_claiming_close(codec, c_guard, language):
    raw = encode_uart_payload(load_uart_registry(), NAME, interruption_values())
    assert len(raw) == 61
    check_frame(codec, c_guard, language, NAME, raw, True)
    assert codec.decode_payload(NAME, raw) == interruption_values()


def changed(changes):
    registry = load_uart_registry()
    raw = bytearray(encode_uart_payload(registry, NAME, interruption_values()))
    fields = {field["name"]: field for field in uart_message_specs(registry)[NAME]["fields"]}
    for key, value in changes.items():
        field = fields[key]
        if "enum" in field:
            value = registry["enums"][field["enum"]]["values"].get(value, value)
        encoded = uuid.UUID(str(value)).bytes if field["type"] == "uuid" else int(value).to_bytes(field["minimumSize"], "big")
        raw[field["offset"]:field["offset"] + field["minimumSize"]] = encoded
    return bytes(raw)


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_interruption_cannot_rewrite_frozen_human_confirmation(codec, c_guard, language):
    check_frame(codec, c_guard, language, NAME, changed({"interruptedPhase": "CLEAN_FINALIZING"}), False)


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("changes", [
    {"finalMeasurementEventSequence": 9},
    {"interruptedPhase": "CLEAN_FINAL_MEASURING", "cleanActionSequence": 1},
    {"interruptedPhase": "CLEAN_RESULT_CONFIRMATION", "finalMeasurementEventSequence": 9},
    {"interruptedPhase": "CLEAN_RESULT_CONFIRMATION", "cleanActionSequence": 1, "finalMeasurementEventSequence": 10},
])
def test_interruption_never_uses_missing_stale_or_future_final_measurement(codec, c_guard, language, changes):
    check_frame(codec, c_guard, language, NAME, changed(changes), False)


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_clean_interruption_rejects_every_invalid_identity_enum_and_frame_boundary(codec, c_guard, language):
    registry = load_uart_registry()
    for field in uart_message_specs(registry)[NAME]["fields"]:
        invalid = []
        if field["type"] == "uuid":
            invalid.append(str(uuid.UUID(int=0)))
        if field.get("minimum", 0):
            invalid.append(field["minimum"] - 1)
        if "maximum" in field and field["maximum"] < registry["wireTypes"][field["type"]]["maximum"]:
            invalid.append(field["maximum"] + 1)
        if "enum" in field:
            invalid.append((1 << (8 * field["minimumSize"])) - 1)
        for value in invalid:
            check_frame(codec, c_guard, language, NAME, changed({field["name"]: value}), False)
    raw = encode_uart_payload(registry, NAME, interruption_values())
    for bad in (b"", raw[:-1], raw + b"\0"):
        check_frame(codec, c_guard, language, NAME, bad, False)
