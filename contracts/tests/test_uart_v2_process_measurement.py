"""Process weights use the same native value kinds as the retained final result."""
import sys
import types
import ctypes
import subprocess
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import (CONTRACTS_ROOT, ContractError, decode_uart_frame,
                         decode_uart_payload, encode_uart_frame, encode_uart_payload,
                         load_uart_registry, uart_message_specs)
from generate_contracts import build_outputs


def unsampled_fullness_values():
    """Test input explicitly says no sensor observation; never a codec default."""
    registry = load_uart_registry()
    return {field["name"]: (next(key for key, number in registry["enums"][field["enum"]]["values"].items() if number == 0)
        if "enum" in field else "00" * 32 if field["type"] == "sha256" else False if field["type"] == "bool" else 0)
        for field in registry["fieldGroups"]["workFullnessEvidence"]}


def process_values(name="WORK_PREOPEN_WEIGHT_READY"):
    values = {
        "mcuBootId": 42, "mcuEventSequence": 3, "uptimeMs": 10000,
        "mcuCommandUid": "11111111-1111-4111-8111-111111111111",
        "sessionUid": "22222222-2222-4222-8222-222222222222",
        "portNo": 1, "roundIndex": 1,
        "measurementUid": "33333333-3333-4333-8333-333333333333",
        "measurementKind": "TIMEOUT_MEDIAN", "reportedWeightGrams": 0,
        "measurementElapsedMs": 5000, "sampleCount": 20,
        "sampleSpanGrams": 2000, "calibrationVersion": 1,
        "faultCode": "NONE", "configVersion": 7,
    }
    if name in ("WORK_POSTCLOSE_WEIGHT_READY", "CLEAN_FINAL_WEIGHT_READY"):
        values.update(unsampled_fullness_values())
    return values


@pytest.fixture(scope="module")
def codec():
    generated = types.ModuleType("process_measurement_candidate")
    source = build_outputs()[CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"]
    exec(compile(source, "process_measurement_candidate", "exec"), generated.__dict__)
    return generated


def test_preopen_can_report_a_real_zero_timeout_median_without_claiming_stability(codec):
    registry = load_uart_registry()
    values = process_values()
    payload = encode_uart_payload(registry, "WORK_PREOPEN_WEIGHT_READY", values)
    assert codec.decode_payload("WORK_PREOPEN_WEIGHT_READY", payload) == values
    assert decode_uart_payload(registry, "WORK_PREOPEN_WEIGHT_READY",
        codec.encode_payload("WORK_PREOPEN_WEIGHT_READY", values)) == values


def process_message_values(registry, name):
    common = process_values()
    values = {}
    for field in uart_message_specs(registry)[name]["fields"]:
        key = field["name"]
        if key in common:
            values[key] = common[key]
        elif field["type"] == "uuid":
            values[key] = "44444444-4444-4444-8444-444444444444"
        elif field["type"] == "sha256":
            values[key] = "00" * 32
        elif field["type"] == "bool":
            values[key] = False
        elif "enum" in field:
            values[key] = next(iter(registry["enums"][field["enum"]]["values"]))
        else:
            values[key] = field.get("minimum", 0)
    if name == "FULLNESS_SAMPLE_RESULT":
        values.update(representativeDistancePresent=True, representativeDistanceMm=200,
                      requestedSampleCount=5, validSampleCount=5,
                      fullnessSampleBasis="MEASURED_MEDIAN", fullnessSensorValue="CLEAR")
    if "workFullnessStatus" in values:
        values.update(unsampled_fullness_values())
    return values


@pytest.fixture(scope="module")
def c_guard(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    target = tmp_path_factory.mktemp("process-guard") / "guard.dll"
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1",
        str(CONTRACTS_ROOT.parent / "hardware_mcu/tests/command_guard_host.c"),
        str(CONTRACTS_ROOT / "uart/generated/c/ecobin_uart_protocol.c"),
        "-Wl,/EXPORT:TestMcuFrameGuard", "-o", str(target)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    library = ctypes.CDLL(str(target))
    library.TestMcuFrameGuard.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    library.TestMcuFrameGuard.restype = ctypes.c_int
    return library.TestMcuFrameGuard


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_all_six_process_messages_reject_semantic_errors_before_dispatch(codec, c_guard, language):
    registry = load_uart_registry()
    for name in registry["sessionPolicy"]["processMeasurementMessages"]:
        values = process_message_values(registry, name)
        spec = uart_message_specs(registry)[name]
        def check(changes, valid):
            payload = bytearray(encode_uart_payload(registry, name, values))
            for key, changed in changes.items():
                field = next(field for field in spec["fields"] if field["name"] == key)
                if "enum" in field:
                    changed = registry["enums"][field["enum"]]["values"].get(changed, changed)
                size, offset = field["minimumSize"], field["offset"]
                raw = (uuid.UUID(str(changed)).bytes if field["type"] == "uuid" else
                       int(changed).to_bytes(size, "big", signed=field["type"] == "i32"))
                payload[offset:offset + size] = raw
            frame = encode_uart_frame(registry, spec["id"], 1, 1, bytes(payload))
            if language == "c":
                assert (c_guard(frame, len(frame)) == 0) == valid, (name, changes)
            elif valid:
                decoded = (codec.decode_frame(frame, sender_role="MCU") if language == "python"
                           else decode_uart_frame(registry, frame, sender_role="MCU"))
                assert decoded["messageName"] == name
            else:
                with pytest.raises(codec.ProtocolError if language == "python" else ContractError):
                    (codec.decode_frame(frame, sender_role="MCU") if language == "python"
                     else decode_uart_frame(registry, frame, sender_role="MCU"))
        for change in ({}, {"reportedWeightGrams": -2147483648},
                       {"measurementKind": "STABLE_MEAN", "sampleSpanGrams": 100},
                       {"measurementKind": "UNAVAILABLE", "reportedWeightGrams": 0, "sampleCount": 0}):
            check(change, True)
        for change in ({"sampleCount": 4}, {"measurementElapsedMs": 4999},
                       {"measurementKind": "STABLE_MEAN", "sampleSpanGrams": 101},
                       {"measurementKind": "UNAVAILABLE", "reportedWeightGrams": 1},
                       {"measurementKind": "NOT_TAKEN"}, {"measurementKind": "MCU_RESET_LOST"},
                       {"mcuBootId": 0}, {"mcuEventSequence": 0}, {"configVersion": 0},
                       {"sampleCount": 33}, {"measurementElapsedMs": 5001},
                       {"measurementUid": str(uuid.UUID(int=0))},
                       {"uptimeMs": 4999}, {"faultCode": "WEIGHT_UNSTABLE"}):
            check(change, False)


def test_symbolic_and_numeric_enum_input_mean_the_same_thing_for_all_process_messages(codec):
    registry = load_uart_registry()
    for name in registry["sessionPolicy"]["processMeasurementMessages"]:
        symbolic = process_message_values(registry, name)
        numeric = dict(symbolic)
        for field in uart_message_specs(registry)[name]["fields"]:
            if "enum" in field:
                numeric[field["name"]] = registry["enums"][field["enum"]]["values"][symbolic[field["name"]]]
        expected = encode_uart_payload(registry, name, symbolic)
        assert encode_uart_payload(registry, name, numeric) == expected
        assert codec.encode_payload(name, numeric) == expected
