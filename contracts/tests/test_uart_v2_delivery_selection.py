"""A delivery choice names its original work and the exact saved round weight."""
import hashlib

import pytest
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard, process_values
from contracts.tests.test_uart_v2_process_handoff import process_scope
from contractlib import load_uart_registry, encode_uart_payload, compute_uart_process_event_digest


def selection_values():
    weight = process_values()
    return {key: weight[key] for key in ("mcuBootId", "mcuCommandUid", "sessionUid", "portNo", "roundIndex", "configVersion")} | {
        "mcuEventSequence": 4, "uptimeMs": 11000, "postCloseMeasurementUid": weight["measurementUid"], "selection": "END"}


def test_selection_is_queryable_and_exactly_saved_under_original_work_scope(codec):
    values = selection_values()
    raw = codec.encode_payload("DELIVERY_SELECTION", values)
    assert len(raw) == 80 and codec.decode_payload("DELIVERY_SELECTION", raw) == values
    scope = process_scope() | {"eventMessageType": "DELIVERY_SELECTION"}
    query = codec.encode_payload("QUERY_PROCESS_EVENT", scope | {"queryId": 1})
    assert len(query) == 97
    expected = hashlib.sha256(b"ECOBIN:UART:PROCESS-EVENT:v2\0" + bytes([51]) + len(raw).to_bytes(2, "big") + raw).hexdigest()
    assert codec.compute_process_event_digest("DELIVERY_SELECTION", raw) == expected
    assert compute_uart_process_event_digest(load_uart_registry(), "DELIVERY_SELECTION", raw) == expected
    receipt = codec.encode_payload("PROCESS_EVENT_SAVED", dict(mcuBootId=42, mcuEventSequence=4,
        eventMessageType="DELIVERY_SELECTION", eventDigestSha256=expected))
    assert len(receipt) == 45


@pytest.mark.parametrize("changes", [{"mcuBootId": 0}, {"mcuEventSequence": 0}, {"portNo": 0},
    {"portNo": 7}, {"roundIndex": 0}, {"selection": 255}, {"configVersion": 0}, {"configVersion": 2**53},
    *[{field: "00000000-0000-0000-0000-000000000000"} for field in
      ("mcuCommandUid", "sessionUid", "postCloseMeasurementUid")]])
def test_invalid_selection_is_rejected_by_reference_python_and_actual_c(codec, c_guard, changes):
    values = selection_values() | changes
    name = "DELIVERY_SELECTION"
    registry = load_uart_registry()
    raw = bytearray(codec.encode_payload(name, selection_values()))
    for field in codec.MESSAGE_SPECS[name]["fields"]:
        if field["name"] in changes:
            value = changes[field["name"]]
            data = bytes.fromhex(value.replace("-", "")) if field["type"] == "uuid" else value.to_bytes(field["minimumSize"], "big")
            raw[field["offset"]:field["offset"] + len(data)] = data
    raw = bytes(raw)
    with pytest.raises(ValueError):
        encode_uart_payload(registry, name, values)
    with pytest.raises(ValueError):
        codec.decode_payload(name, raw)
    frame = codec.encode_frame(name, 1, raw)
    assert c_guard(frame, len(frame)) != 0


@pytest.mark.parametrize("selection", ["CONTINUE", "END", "WINDOW_EXPIRED"])
def test_all_choices_are_distinct_valid_facts_not_implied_motion_permission(codec, c_guard, selection):
    values = selection_values() | {"selection": selection}
    raw = codec.encode_payload("DELIVERY_SELECTION", values)
    frame = codec.encode_frame("DELIVERY_SELECTION", 1, raw)
    assert c_guard(frame, len(frame)) == 0
    assert codec.decode_payload("DELIVERY_SELECTION", raw)["selection"] == selection


def test_generated_selection_mutations_reach_payload_guard_and_recover(codec):
    from contractlib import UartStreamParser
    from generate_contracts import build_session_control_traces, build_uart_vectors

    registry = load_uart_registry()
    traces = [trace for trace in build_session_control_traces(registry, build_uart_vectors(registry))
              if trace["name"].startswith("delivery_selection_saved_round_reject_")]
    assert traces
    for trace in traces:
        for parser in (UartStreamParser(registry, sender_role="MCU"), codec.StreamParser(sender_role="MCU")):
            frames = []
            for chunk in trace["chunks"]:
                frames.extend(parser.feed(bytes.fromhex(chunk["hex"]), now_ms=chunk["atMs"]))
            assert [frame["messageName"] for frame in frames] == ["DELIVERY_SELECTION"], trace["name"]
            assert parser.diagnostics == ["SEMANTIC_REJECTED:invalid session payload"], trace["name"]
