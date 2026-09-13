"""Configuration/control commands must not bypass the fenced payload guard."""
import hashlib

import pytest
from contracts.tests.test_uart_v2_command_guards import codec, c_guard, minimal_command
from contractlib import (ContractError, decode_uart_frame, encode_uart_frame, encode_uart_payload,
    load_uart_registry, uart_message_specs, compute_uart_command_digest)
from generate_contracts import build_uart_stream_traces, build_uart_vectors


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_configuration_and_control_commands_reject_wrong_digest(codec, c_guard, language):
    registry = load_uart_registry()
    specs = uart_message_specs(registry)
    for name in ("QUERY_STATE", "SAFE_CLOSE", "CONFIG_BEGIN", "CONFIG_DEVICE_BLOCK", "CONFIG_PORT_BLOCK", "CONFIG_COMMIT"):
        spec = specs[name]
        payload = encode_uart_payload(registry, name, minimal_command(registry, spec | {"name": name}))
        valid = encode_uart_frame(registry, spec["id"], 1, 1, payload)
        broken = bytearray(payload)
        broken[16] ^= 1
        invalid = encode_uart_frame(registry, spec["id"], 1, 2, bytes(broken))
        if language == "c":
            assert c_guard(valid, len(valid)) == 0
            assert c_guard(invalid, len(invalid)) != 0, name
        elif language == "python":
            assert codec.decode_frame(valid, sender_role="EDGE")["messageName"] == name
            with pytest.raises(codec.ProtocolError, match="invalid command payload"):
                codec.decode_frame(invalid, sender_role="EDGE")
        else:
            assert decode_uart_frame(registry, valid, sender_role="EDGE")["messageName"] == name
            with pytest.raises(ContractError, match="invalid command payload"):
                decode_uart_frame(registry, invalid, sender_role="EDGE")


@pytest.mark.parametrize("language", ["reference", "python"])
def test_numeric_scope_cannot_bypass_close_port_relationship(codec, language):
    registry = load_uart_registry()
    values = minimal_command(registry, uart_message_specs(registry)["SAFE_CLOSE"] | {"name": "SAFE_CLOSE"})
    values |= {"scope": registry["enums"]["SafeCloseScope"]["values"]["SINGLE_DELIVERY_DOOR"], "portNo": 0}
    values["commandDigestSha256"] = compute_uart_command_digest(registry, "SAFE_CLOSE", values)
    if language == "python":
        with pytest.raises(codec.ProtocolError):
            codec.encode_payload("SAFE_CLOSE", values)
    else:
        with pytest.raises(ContractError):
            encode_uart_payload(registry, "SAFE_CLOSE", values)


def test_configuration_and_control_guards_have_shared_three_language_cases():
    registry = load_uart_registry()
    names = {trace["name"] for trace in build_uart_stream_traces(registry, build_uart_vectors(registry))}
    for name in ("query_state", "safe_close", "config_begin", "config_device_block", "config_port_block", "config_commit"):
        assert "command_guard_" + name + "_valid" in names
        assert "command_guard_" + name + "_wrong_digest" in names
    assert "command_guard_config_port_block_port_occupies_commit" in names


def changed_frame(registry, name, changes):
    spec = uart_message_specs(registry)[name]
    raw = bytearray(encode_uart_payload(registry, name, minimal_command(registry, spec | {"name": name})))
    for field in spec["fields"]:
        if field["name"] in changes:
            value = changes[field["name"]]
            if "enum" in field:
                value = registry["enums"][field["enum"]]["values"].get(value, value)
            start, size = field["offset"], field["minimumSize"]
            raw[start:start + size] = value.to_bytes(size, "big", signed=field["type"] == "i32")
    prefix = bytes.fromhex(registry["digestProfiles"]["commandDigestSha256"]["domainHex"])
    prefix += bytes([spec["id"]]) + (len(raw) - 48).to_bytes(2, "big")
    raw[16:48] = hashlib.sha256(prefix + raw[48:]).digest()
    return encode_uart_frame(registry, spec["id"], 1, 2, bytes(raw))


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_correct_digest_does_not_hide_inconsistent_configuration_or_close_scope(codec, c_guard, language):
    registry = load_uart_registry()
    for name, changes in (
        ("SAFE_CLOSE", {"scope": "ALL_DELIVERY_DOORS", "portNo": 1}),
        ("SAFE_CLOSE", {"scope": "SINGLE_DELIVERY_DOOR", "portNo": 0}),
        ("CONFIG_BEGIN", {"partCount": 5}),
        ("CONFIG_PORT_BLOCK", {"partIndex": 4}),
        ("CONFIG_PORT_BLOCK", {"partIndex": 4, "portNo": 2, "partCount": 4}),
        ("CONFIG_PORT_BLOCK", {"weightMinimumGrams": 10, "weightMaximumGrams": 1}),
        ("CONFIG_PORT_BLOCK", {"weightMaximumGrams": -1}),
        ("CONFIG_PORT_BLOCK", {"fullnessMinimumValidSampleCount": 4, "fullnessSampleCount": 3}),
        ("CONFIG_COMMIT", {"partCount": 5}),
        ("CONFIG_DEVICE_BLOCK", {"smokeMonitoringEnabled": 2}),
    ):
        invalid = changed_frame(registry, name, changes)
        if language == "c":
            assert c_guard(invalid, len(invalid)) != 0, (name, changes)
        elif language == "python":
            with pytest.raises(codec.ProtocolError):
                codec.decode_frame(invalid, sender_role="EDGE")
        else:
            with pytest.raises(ContractError):
                decode_uart_frame(registry, invalid, sender_role="EDGE")
