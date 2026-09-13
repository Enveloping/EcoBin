"""Clean button intents name the original work/configuration, not a new command."""
import hashlib

import pytest
from contracts.tests.test_uart_v2_process_measurement import codec, c_guard, process_values
from contracts.tests.test_uart_v2_process_handoff import process_scope
from contractlib import load_uart_registry, encode_uart_payload


INTENTS = ("CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED")


def intent_values():
    base = process_values()
    return {key: base[key] for key in ("mcuBootId", "mcuCommandUid", "portNo", "configVersion")} | {
        "mcuEventSequence": 4, "uptimeMs": 11000, "operationUid": base["sessionUid"], "cleanActionSequence": 1}


def intent_scope(name):
    return process_scope() | {"workType": "CLEAN_OPERATION", "eventMessageType": name}


@pytest.mark.parametrize("name", INTENTS)
def test_intent_is_original_work_scoped_queryable_and_exactly_saved(codec, c_guard, name):
    values = intent_values()
    raw = encode_uart_payload(load_uart_registry(), name, values)
    assert len(raw) == 63 and codec.decode_payload(name, raw) == values
    query = codec.encode_payload("QUERY_PROCESS_EVENT", {"queryId": 1, **intent_scope(name)})
    assert len(query) == 97
    expected = hashlib.sha256(b"ECOBIN:UART:PROCESS-EVENT:v2\0" + bytes([codec.MESSAGE_SPECS[name]["id"]])
        + len(raw).to_bytes(2, "big") + raw).hexdigest()
    assert codec.compute_process_event_digest(name, raw) == expected
    receipt = codec.encode_payload("PROCESS_EVENT_SAVED", dict(mcuBootId=42, mcuEventSequence=4,
        eventMessageType=name, eventDigestSha256=expected))
    assert len(receipt) == 45
    frame = codec.encode_frame(name, 1, raw)
    assert c_guard(frame, len(frame)) == 0


@pytest.mark.parametrize("name", INTENTS)
@pytest.mark.parametrize("changes", [{"mcuBootId": 0}, {"mcuEventSequence": 0}, {"portNo": 0}, {"portNo": 7},
    {"cleanActionSequence": 0}, {"configVersion": 0}, {"configVersion": 2**53},
    {"mcuCommandUid": "00000000-0000-0000-0000-000000000000"},
    {"operationUid": "00000000-0000-0000-0000-000000000000"}])
def test_bad_intent_identity_is_rejected_by_reference_python_and_c(codec, c_guard, name, changes):
    raw = bytearray(codec.encode_payload(name, intent_values()))
    for field in codec.MESSAGE_SPECS[name]["fields"]:
        if field["name"] in changes:
            value = changes[field["name"]]
            data = bytes.fromhex(value.replace("-", "")) if field["type"] == "uuid" else value.to_bytes(field["minimumSize"], "big")
            raw[field["offset"]:field["offset"] + len(data)] = data
    with pytest.raises(ValueError):
        encode_uart_payload(load_uart_registry(), name, intent_values() | changes)
    with pytest.raises(ValueError):
        codec.decode_payload(name, bytes(raw))
    frame = codec.encode_frame(name, 1, bytes(raw))
    assert c_guard(frame, len(frame)) != 0


@pytest.mark.parametrize("name", INTENTS)
@pytest.mark.parametrize("changes", [{"workType": "DELIVERY_SESSION"}, {"stepSequence": 0}])
def test_clean_intent_query_cannot_use_delivery_or_initial_step(codec, c_guard, name, changes):
    original = {"queryId": 1, **intent_scope(name)}
    with pytest.raises(ValueError):
        codec.encode_payload("QUERY_PROCESS_EVENT", original | changes)
    raw = bytearray(codec.encode_payload("QUERY_PROCESS_EVENT", original))
    for field in codec.MESSAGE_SPECS["QUERY_PROCESS_EVENT"]["fields"]:
        if field["name"] in changes:
            value = changes[field["name"]]
            if "enum" in field:
                value = codec.REGISTRY["enums"][field["enum"]]["values"][value]
            raw[field["offset"]:field["offset"] + field["minimumSize"]] = value.to_bytes(field["minimumSize"], "big")
    frame = codec.encode_frame("QUERY_PROCESS_EVENT", 1, bytes(raw))
    assert c_guard(frame, len(frame)) != 0
