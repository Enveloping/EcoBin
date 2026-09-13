"""A CRC-valid actuator report is not necessarily a valid physical-output fact."""
import uuid

import pytest
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard
from contractlib import (
    ContractError, decode_uart_frame, decode_uart_payload, encode_uart_frame,
    encode_uart_payload, load_uart_registry, uart_message_specs,
)
from generate_contracts import build_uart_stream_traces, build_uart_vectors


def actuator_values(name, **changes):
    values = {"mcuBootId": 42, "mcuEventSequence": 7, "uptimeMs": 1000,
              "mcuCommandUid": "11111111-1111-4111-8111-111111111111", "portNo": 1}
    if name == "CLEAN_OPERATION_INTERRUPTED":
        values.update(operationUid="22222222-2222-4222-8222-222222222222", cleanActionSequence=0,
            interruptedPhase="CLEAN_ACTIVE", interruptionReason="UPDATE_STOPPED", finalMeasurementEventSequence=0)
        return values | changes
    if name == "DELIVERY_POSTCLOSE_INTERRUPTED":
        values.update(sessionUid="22222222-2222-4222-8222-222222222222", roundIndex=1,
            interruptedPhase="DELIVERY_CLOSE_TRAVEL_WAIT", interruptionReason="UPDATE_STOPPED",
            postCloseMeasurementEventSequence=0)
        return values | changes
    if name == "DELIVERY_CYCLE_ABORTED":
        values.update(sessionUid="22222222-2222-4222-8222-222222222222", roundIndex=1,
            abortReason="UPDATE_STOPPED", openDispatched=True, selectionEventSequence=0)
        return values | changes
    if name in ("DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT", "DELIVERY_LOCAL_DOOR_RESULT"):
        values.update(sessionUid="22222222-2222-4222-8222-222222222222", roundIndex=1,
                      command="OPEN", outputStatus="COMMAND_DISPATCHED",
                      physicalDoorStateBasis="NOT_OBSERVABLE", faultCode="NONE")
        if name == "DELIVERY_LOCAL_DOOR_RESULT":
            values.update(roundIndex=2, selectionEventSequence=6)
        if name == "SAFE_CLOSE_RESULT":
            del values["sessionUid"], values["roundIndex"]
            values.update(command="CLOSE", scope="SINGLE_DELIVERY_DOOR")
    else:
        values.update(operationUid="33333333-3333-4333-8333-333333333333",
                      lockPowerState="ENERGIZED", solenoidHealth="OK")
    return values | changes


def changed_payload(registry, name, changes):
    raw = bytearray(encode_uart_payload(registry, name, actuator_values(name)))
    fields = {field["name"]: field for field in uart_message_specs(registry)[name]["fields"]}
    for key, value in changes.items():
        field = fields[key]
        if "enum" in field:
            value = registry["enums"][field["enum"]]["values"].get(value, value)
        encoded = (uuid.UUID(str(value)).bytes if field["type"] == "uuid"
                   else int(value).to_bytes(field["minimumSize"], "big"))
        raw[field["offset"]:field["offset"] + field["minimumSize"]] = encoded
    return bytes(raw)


def check_frame(codec, c_guard, language, name, payload, valid, flags=1):
    registry = load_uart_registry()
    frame = encode_uart_frame(registry, uart_message_specs(registry)[name]["id"], flags, 1, payload)
    if language == "c":
        assert (c_guard(frame, len(frame)) == 0) == valid
    else:
        decode = ((lambda: codec.decode_frame(frame, sender_role="MCU")) if language == "python"
                  else (lambda: decode_uart_frame(registry, frame, sender_role="MCU")))
        if valid:
            assert decode()["messageName"] == name
        else:
            with pytest.raises(codec.ProtocolError if language == "python" else ContractError):
                decode()


@pytest.mark.parametrize("name", ["DELIVERY_DOOR_COMMAND_RESULT", "CLEAN_LOCK_POWER_CHANGED", "SAFE_CLOSE_RESULT", "DELIVERY_LOCAL_DOOR_RESULT", "DELIVERY_CYCLE_ABORTED"])
@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_actuator_report_without_command_identity_never_reaches_consumer(codec, c_guard, name, language):
    registry = load_uart_registry()
    check_frame(codec, c_guard, language, name, encode_uart_payload(registry, name, actuator_values(name)), True)
    check_frame(codec, c_guard, language, name, changed_payload(registry, name,
                {"mcuCommandUid": str(uuid.UUID(int=0))}), False)


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("changes", [
    {"command": "NONE"}, {"outputStatus": "NOT_DISPATCHED"},
    {"faultCode": "DELIVERY_DOOR_OUTPUT_REJECTED"},
    {"outputStatus": "OUTPUT_REJECTED"},
    {"outputStatus": "OUTPUT_REJECTED", "faultCode": "WEIGHT_TIMEOUT"},
    {"outputStatus": "COMMAND_SUPERSEDED_BEFORE_DISPATCH", "faultCode": "DELIVERY_DOOR_OUTPUT_REJECTED"},
    {"command": "OPEN", "outputStatus": "COALESCED_WITH_EXISTING_CLOSE"},
])
def test_door_output_rejects_contradictory_result_even_with_valid_crc(codec, c_guard, language, changes):
    name = "DELIVERY_DOOR_COMMAND_RESULT"
    check_frame(codec, c_guard, language, name, changed_payload(load_uart_registry(), name, changes), False)


@pytest.mark.parametrize("name", ["DELIVERY_DOOR_COMMAND_RESULT", "CLEAN_LOCK_POWER_CHANGED", "SAFE_CLOSE_RESULT", "DELIVERY_LOCAL_DOOR_RESULT", "DELIVERY_CYCLE_ABORTED"])
def test_symbolic_and_numeric_output_facts_encode_identically(codec, name):
    registry = load_uart_registry()
    symbolic = actuator_values(name)
    numeric = dict(symbolic)
    for field in uart_message_specs(registry)[name]["fields"]:
        if "enum" in field:
            numeric[field["name"]] = registry["enums"][field["enum"]]["values"][symbolic[field["name"]]]
    expected = encode_uart_payload(registry, name, symbolic)
    assert encode_uart_payload(registry, name, numeric) == expected
    assert codec.encode_payload(name, numeric) == expected
    assert decode_uart_payload(registry, name, expected) == symbolic


def test_cross_language_stream_suite_covers_each_actuator_message_and_keeps_power_edges(codec):
    registry = load_uart_registry()
    traces = build_uart_stream_traces(registry, build_uart_vectors(registry))
    actuator_traces = [case for case in traces if case["name"].startswith("actuator_")]
    for name in registry["sessionPolicy"]["actuatorEventMessages"]:
        assert any(name in case["expectedMessageNames"] and case["expectedDiagnostics"] for case in actuator_traces)
    power = next(case for case in actuator_traces if case["name"] == "actuator_clean_power_edges")
    parser = codec.StreamParser(sender_role="MCU")
    frames = []
    for chunk in power["chunks"]:
        frames.extend(parser.feed(bytes.fromhex(chunk["hex"]), now_ms=chunk["atMs"]))
    events = [codec.decode_payload(frame["messageName"], frame["payload"]) for frame in frames]
    assert [event["lockPowerState"] for event in events] == ["ENERGIZED", "DEENERGIZED"]
    assert [event["mcuEventSequence"] for event in events] == [7, 8]


@pytest.mark.parametrize("language", ["reference", "python", "c"])
@pytest.mark.parametrize("name", ["DELIVERY_DOOR_COMMAND_RESULT", "CLEAN_LOCK_POWER_CHANGED", "SAFE_CLOSE_RESULT", "DELIVERY_LOCAL_DOOR_RESULT", "DELIVERY_CYCLE_ABORTED"])
def test_every_actuator_field_is_checked_before_dispatch(codec, c_guard, language, name):
    registry = load_uart_registry()
    for field in uart_message_specs(registry)[name]["fields"]:
        changes = []
        if field["type"] == "uuid":
            changes.append(str(uuid.UUID(int=0)))
        if field.get("minimum", 0) > 0:
            changes.append(field["minimum"] - 1)
        if "maximum" in field and field["maximum"] < registry["wireTypes"][field["type"]]["maximum"]:
            changes.append(field["maximum"] + 1)
        if "enum" in field:
            changes.append((1 << (8 * field["minimumSize"])) - 1)
        for value in changes:
            check_frame(codec, c_guard, language, name, changed_payload(registry, name, {field["name"]: value}), False)
    payload = encode_uart_payload(registry, name, actuator_values(name))
    for malformed in (b"", payload[:-1], payload + b"\0"):
        check_frame(codec, c_guard, language, name, malformed, False)


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_safe_close_never_reports_open_even_if_the_output_was_rejected(codec, c_guard, language):
    registry, name = load_uart_registry(), "SAFE_CLOSE_RESULT"
    for changes in ({"command": "OPEN"}, {"command": "OPEN", "outputStatus": "OUTPUT_REJECTED",
                                            "faultCode": "DELIVERY_DOOR_OUTPUT_REJECTED"}):
        check_frame(codec, c_guard, language, name, changed_payload(registry, name, changes), False)


@pytest.mark.parametrize("name", ["DELIVERY_DOOR_COMMAND_RESULT", "CLEAN_LOCK_POWER_CHANGED", "SAFE_CLOSE_RESULT", "DELIVERY_LOCAL_DOOR_RESULT", "DELIVERY_CYCLE_ABORTED"])
def test_valid_actuator_report_cannot_use_unimplemented_measurement_custody(codec, name):
    payload = codec.encode_payload(name, actuator_values(name))
    with pytest.raises(codec.ProtocolError, match="registered process"):
        codec.compute_process_event_digest(name, payload)
    with pytest.raises(codec.ProtocolError):
        codec.encode_payload("PROCESS_EVENT_SAVED", {"mcuBootId": 42, "mcuEventSequence": 7,
            "eventMessageType": name, "eventDigestSha256": "ab" * 32})
