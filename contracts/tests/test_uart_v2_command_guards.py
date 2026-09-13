"""Command CRC alone cannot grant access to a business-state consumer."""
import ctypes
import hashlib
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import (CONTRACTS_ROOT, ContractError, compute_uart_command_digest,
    decode_uart_frame, encode_uart_frame, encode_uart_payload, load_uart_registry, uart_message_specs)
from generate_contracts import build_outputs, build_uart_stream_traces, build_uart_vectors


def minimal_command(registry, spec):
    values = {}
    for field in spec["fields"]:
        if "enum" in field:
            value = next(iter(registry["enums"][field["enum"]]["values"]))
        elif field["type"] == "uuid":
            value = str(uuid.UUID(int=field["offset"] + 1))
        elif field["type"] == "sha256":
            value = "ab" * 32
        elif field["type"] == "bool":
            value = False
        else:
            value = field.get("const", field.get("minimum", 0))
        values[field["name"]] = value
    values["commandDigestSha256"] = compute_uart_command_digest(registry, spec["name"], values)
    return values


@pytest.fixture(scope="module")
def codec():
    generated = types.ModuleType("command_guard_candidate")
    source = build_outputs()[CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"]
    exec(compile(source, "command_guard_candidate", "exec"), generated.__dict__)
    return generated


@pytest.fixture(scope="module")
def c_guard(tmp_path_factory):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    destination = tmp_path_factory.mktemp("command-guard") / "guard.dll"
    source = CONTRACTS_ROOT.parent / "hardware_mcu/tests/command_guard_host.c"
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        str(source), "-Wl,/EXPORT:TestCommandGuard", "-o", str(destination)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    library = ctypes.CDLL(str(destination))
    library.TestCommandGuard.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    library.TestCommandGuard.restype = ctypes.c_int
    return library.TestCommandGuard


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_every_business_command_rejects_crc_valid_wrong_content_digest(codec, c_guard, language):
    registry = load_uart_registry()
    commands = [spec | {"name": name} for name, spec in uart_message_specs(registry).items() if spec["category"] == "COMMAND"]
    assert len(commands) == 9
    for spec in commands:
        payload = encode_uart_payload(registry, spec["name"], minimal_command(registry, spec))
        valid = encode_uart_frame(registry, spec["id"], 1, 1, payload)
        broken = bytearray(payload)
        broken[16] ^= 1  # wrong SHA, with a freshly correct transport CRC
        invalid = encode_uart_frame(registry, spec["id"], 1, 2, bytes(broken))
        if language == "c":
            assert c_guard(valid, len(valid)) == 0
            assert c_guard(invalid, len(invalid)) != 0, spec["name"]
        elif language == "python":
            assert codec.decode_frame(valid, sender_role="EDGE")["messageName"] == spec["name"]
            with pytest.raises(codec.ProtocolError, match="invalid command payload"):
                codec.decode_frame(invalid, sender_role="EDGE")
        else:
            assert decode_uart_frame(registry, valid, sender_role="EDGE")["messageName"] == spec["name"]
            with pytest.raises(ContractError, match="invalid command payload"):
                decode_uart_frame(registry, invalid, sender_role="EDGE")


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_invalid_fields_are_rejected_even_with_a_recomputed_valid_digest(codec, c_guard, language):
    registry = load_uart_registry()
    for name, spec in uart_message_specs(registry).items():
        if spec["category"] != "COMMAND":
            continue
        payload = encode_uart_payload(registry, name, minimal_command(registry, spec | {"name": name}))
        for field in spec["fields"]:
            invalid_values = []
            kind, width, offset = field["type"], field["minimumSize"], field["offset"]
            if kind == "uuid":
                invalid_values.append(0)
            if field.get("minimum", 0) > 0:
                invalid_values.append(field["minimum"] - 1)
            if "maximum" in field and field["maximum"] < registry["wireTypes"][kind]["maximum"]:
                invalid_values.append(field["maximum"] + 1)
            if "enum" in field:
                invalid_values.append((1 << (width * 8)) - 1)
            for value in invalid_values:
                raw = bytearray(payload)
                raw[offset:offset + width] = value.to_bytes(width, "big")
                preimage = bytes.fromhex(registry["digestProfiles"]["commandDigestSha256"]["domainHex"])
                preimage += bytes([spec["id"]]) + (len(raw) - 48).to_bytes(2, "big") + raw[48:]
                raw[16:48] = hashlib.sha256(preimage).digest()
                frame = encode_uart_frame(registry, spec["id"], 1, 2, bytes(raw))
                if language == "c":
                    assert c_guard(frame, len(frame)) != 0, (name, field["name"], value)
                elif language == "python":
                    with pytest.raises(codec.ProtocolError):
                        codec.decode_frame(frame, sender_role="EDGE")
                else:
                    with pytest.raises(ContractError):
                        decode_uart_frame(registry, frame, sender_role="EDGE")


def test_all_command_guards_have_shared_c_java_python_stream_cases():
    registry = load_uart_registry()
    traces = build_uart_stream_traces(registry, build_uart_vectors(registry))
    names = {trace["name"] for trace in traces}
    for name, spec in uart_message_specs(registry).items():
        if spec["category"] == "COMMAND":
            for label in ["valid", "wrong_digest", "mcuCommandUid_zero", "short", "long"]:
                assert "command_guard_" + name.lower() + "_" + label in names
