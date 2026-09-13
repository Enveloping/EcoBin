"""Real C measurement -> C result assembly/SHA -> retained result -> SQLite."""
import ctypes as c
import subprocess
import uuid
from pathlib import Path

import pytest
from edge_store import EdgeStore
import uart2_protocol as uart
from hardware.tests.test_mcu_device_facts import WeightConfig, WeightResult
from hardware.tests.test_mcu_work_state_c import query_payload
from hardware.tests.test_mcu_control_endpoint import endpoint, bind
from hardware.tests.test_native_result_handoff import result_payload

ROOT = Path(__file__).resolve().parents[2]


class Measurement(c.Structure):
    _fields_ = [("kind", c.c_uint8), ("uid", c.c_uint8 * 16), ("source_boot_id", c.c_uint64),
        ("event_sequence", c.c_uint32), ("grams", c.c_int32), ("elapsed_ms", c.c_uint16),
        ("sample_count", c.c_uint8), ("span_grams", c.c_uint32), ("calibration_version", c.c_uint32),
        ("fault_code", c.c_uint16)]


class Summary(c.Structure):
    _fields_ = [("config_version", c.c_uint64), ("completed_uptime_ms", c.c_uint64),
        ("delivery_round_count", c.c_uint16), ("clean_action_sequence", c.c_uint32),
        ("finish_reason", c.c_uint8), ("physical_close_confirmed", c.c_uint8),
        ("negative_weight_anomaly", c.c_uint8)]


@pytest.fixture
def builder(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user, output = ROOT / "hardware_mcu/USER", tmp_path / "builder.dll"
    signatures = {
        "McuResultMeasurement_FromAvailable": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint64, c.c_uint32, c.c_uint32]),
        "McuResultBuilder_Complete": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_Init": (None, [c.c_void_p, c.c_uint64]),
        "McuWorkState_SetPhase": (c.c_uint8, [c.c_void_p, c.c_uint8]),
        "McuWorkState_BeginAccepted": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint8]),
        "McuWorkState_CopyHeld": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_Saved": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_Complete": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "WeightMeasurement_Begin": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint32, c.c_uint32, c.c_uint32]),
        "WeightMeasurement_Observe": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint32, c.c_uint32, c.c_int32, c.c_uint32]),
        "WeightMeasurement_Poll": (WeightResult, [c.c_void_p, c.c_uint32]),
    }
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-I", str(user),
        *[str(user / (name + ".c")) for name in ("mcu_result_builder", "mcu_work_state", "mcu_result_slot", "weight_measurement")],
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(output)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    library = c.CDLL(str(output))
    for name, (restype, argtypes) in signatures.items():
        getattr(library, name).restype, getattr(library, name).argtypes = restype, argtypes
    return library


def available(builder, number, grams):
    core, measured = (c.c_uint64 * 48)(), Measurement()
    config = WeightConfig(5000, 1500, 750, 100, 5, 5, -350000, 350000)
    assert builder.WeightMeasurement_Begin(core, c.byref(config), number, 0, 0)
    for sample in range(1, 6):
        assert builder.WeightMeasurement_Observe(core, number, sample, (sample - 1) * 250, grams, (sample - 1) * 250)
    result = builder.WeightMeasurement_Poll(core, 1000)
    assert result.status == 1
    assert builder.McuResultMeasurement_FromAvailable(c.byref(measured), c.byref(result),
        number.to_bytes(16, "big"), 42, number, 4)
    return measured


def work(builder, **changes):
    state = (c.c_uint64 * 38)()
    builder.McuWorkState_Init(state, 42)
    original = query_payload(**changes)[8:]
    phase = 32 if changes.get("workType") == "CLEAN_OPERATION" else 16
    assert builder.McuWorkState_BeginAccepted(state, original, len(original), phase)
    return state


def test_actual_c_weights_are_assembled_frozen_and_saved_without_python_encoding(builder, tmp_path):
    state = work(builder)
    initial, final = available(builder, 1, 0), available(builder, 2, 1000)
    summary = Summary(5, 2500, 1, 0, 1, 0, 0)
    scratch, held = c.create_string_buffer(199), c.create_string_buffer(199)
    assert builder.McuResultBuilder_Complete(state, c.byref(summary), c.byref(initial), c.byref(final), scratch, 199)
    assert builder.McuWorkState_CopyHeld(state, held, 199) == 199
    assert held.raw == scratch.raw
    decoded = uart.decode_payload("WORK_RESULT", held.raw)  # verifies C-generated SHA and all fields
    assert decoded["resultSequence"] == 1 and decoded["mcuBootId"] == 42
    assert decoded["initialKind"] == "STABLE_MEAN" and decoded["initialWeightGrams"] == 0
    assert decoded["finalWeightGrams"] == 1000 and decoded["finalSampleCount"] == 5
    assert decoded["initialCalibrationVersion"] == 4 and decoded["configVersion"] == 5
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        saved = store.save_native_mcu_result(held.raw)
        assert builder.McuWorkState_Saved(state, saved["savedPayload"], 60) == 1
        assert builder.McuWorkState_CopyHeld(state, held, 199) == 0
        assert len(store.list_native_result_report_tasks()) == 1
    finally:
        store.close()


def test_real_timeout_median_and_explicit_lost_initial_weight_remain_distinct(builder):
    state = work(builder, workType="CLEAN_OPERATION")
    initial, final, core = Measurement(), Measurement(), (c.c_uint64 * 48)()
    initial.kind = 1  # Explicit MCU_RESET_LOST; no invented pre-restart identity.
    config = WeightConfig(5000, 1500, 750, 100, 5, 5, -350000, 350000)
    assert builder.WeightMeasurement_Begin(core, c.byref(config), 9, 0, 0)
    for sample in range(1, 21):
        timestamp = (sample - 1) * 250
        assert builder.WeightMeasurement_Observe(core, 9, sample, timestamp, -1000 if sample % 2 else 1000, timestamp)
    result = builder.WeightMeasurement_Poll(core, 5000)
    assert result.status == 2 and result.available == 1 and result.grams == 0
    assert builder.McuResultMeasurement_FromAvailable(c.byref(final), c.byref(result), (9).to_bytes(16, "big"), 42, 19, 4)
    summary, scratch = Summary(5, 5000, 0, 2, 3, 1, 0), c.create_string_buffer(199)
    assert builder.McuResultBuilder_Complete(state, c.byref(summary), c.byref(initial), c.byref(final), scratch, 199)
    decoded = uart.decode_payload("WORK_RESULT", scratch.raw)
    assert decoded["initialKind"] == "MCU_RESET_LOST" and decoded["initialSourceMcuBootId"] == 0
    assert decoded["finalKind"] == "TIMEOUT_MEDIAN" and decoded["finalWeightGrams"] == 0
    assert decoded["finalSampleCount"] == 20 and decoded["finalSpanGrams"] == 2000
    assert decoded["finalMcuEventSequence"] == 19  # not reused local measurement ID 9
    assert decoded["physicalCloseConfirmed"] and decoded["cleanActionSequence"] == 2


def test_frozen_result_cannot_be_recomputed_or_replaced_before_exact_saved(builder):
    state = work(builder)
    initial, final = available(builder, 1, -20), available(builder, 2, 50)
    summary, scratch, held = Summary(5, 2500, 1, 0, 1, 0, 0), c.create_string_buffer(199), c.create_string_buffer(199)
    def complete():
        return builder.McuResultBuilder_Complete(state, c.byref(summary), c.byref(initial), c.byref(final), scratch, 199)
    assert complete()
    original = scratch.raw
    assert uart.decode_payload("WORK_RESULT", original)["initialWeightGrams"] == -20
    assert complete() and scratch.raw == original
    final.grams = 51
    assert not complete()
    assert builder.McuWorkState_CopyHeld(state, held, 199) == 199 and held.raw == original
    assert builder.McuWorkState_Saved(state, original[:60], 60) == 1
    assert not complete()  # Released historical work cannot be newly completed.
    next_identity = query_payload(commandSequence=8, mcuCommandUid="44444444-4444-4444-8444-444444444444",
        workUid="55555555-5555-4555-8555-555555555555")[8:]
    assert builder.McuWorkState_BeginAccepted(state, next_identity, len(next_identity), 16)
    assert complete()
    new_result = scratch.raw
    assert uart.decode_payload("WORK_RESULT", new_result)["resultSequence"] == 2
    assert builder.McuWorkState_Saved(state, original[:60], 60) == 3  # NOT_FOUND, no release
    assert builder.McuWorkState_CopyHeld(state, held, 199) == 199 and held.raw == new_result


@pytest.mark.parametrize("invalid", ["short_buffer", "wrong_boot", "same_measurement", "missing_with_value", "bad_summary"])
def test_invalid_assembly_does_not_consume_sequence_or_change_running_work(builder, invalid):
    state = work(builder)
    initial, final = available(builder, 1, 0), available(builder, 2, 1000)
    summary, scratch = Summary(5, 2500, 1, 0, 1, 0, 0), c.create_string_buffer(199)
    previous = bytes(state)
    if invalid == "wrong_boot":
        final.source_boot_id = 43
    elif invalid == "same_measurement":
        final.uid = initial.uid
    elif invalid == "missing_with_value":
        final.kind = 0
    elif invalid == "bad_summary":
        summary.physical_close_confirmed = 1  # clean-only fact in a delivery
    assert not builder.McuResultBuilder_Complete(state, c.byref(summary), c.byref(initial), c.byref(final),
        scratch, 198 if invalid == "short_buffer" else 199)
    assert bytes(state) == previous


@pytest.mark.parametrize("status", [0, 3, 4, 5, 6])
def test_unavailable_or_pending_core_is_not_promoted_to_available(builder, status):
    output = Measurement()
    output.grams = 123
    before = bytes(output)
    result = WeightResult(1, 5000, 0, 0, status, 0, 0)
    assert not builder.McuResultMeasurement_FromAvailable(c.byref(output), c.byref(result), (1).to_bytes(16, "big"), 42, 1, 4)
    assert bytes(output) == before


def test_result_sequence_exhaustion_does_not_wrap_or_damage_original_reference(builder):
    state = work(builder)
    previous = result_payload(resultSequence=0xFFFFFFFF)
    assert builder.McuWorkState_Complete(state, previous, len(previous))
    assert builder.McuWorkState_Saved(state, previous[:60], 60) == 1
    next_identity = query_payload(commandSequence=8, mcuCommandUid="44444444-4444-4444-8444-444444444444",
        workUid="55555555-5555-4555-8555-555555555555")[8:]
    assert builder.McuWorkState_BeginAccepted(state, next_identity, len(next_identity), 16)
    initial, final = available(builder, 1, 0), available(builder, 2, 1000)
    summary, scratch, before = Summary(5, 2500, 1, 0, 1, 0, 0), c.create_string_buffer(199), bytes(state)
    assert not builder.McuResultBuilder_Complete(state, c.byref(summary), c.byref(initial), c.byref(final), scratch, 199)
    assert bytes(state) == before


def test_c_assembled_result_crosses_c_wire_and_pi_custody_before_release(builder, endpoint, tmp_path):
    from mcu_result_handoff import McuResultHandoff
    from hardware.tests.test_native_command_session import CCommand, CDecision
    lib, memory, replies, sink = endpoint
    bind(endpoint)
    query = query_payload()
    original = uart.decode_payload("QUERY_WORK", query)
    command = CCommand(42, original["commandSequence"],
        (c.c_uint8 * 16).from_buffer_copy(uuid.UUID(original["mcuCommandUid"]).bytes),
        (c.c_uint8 * 32).from_buffer_copy(bytes.fromhex(original["commandDigestSha256"])))
    decision = CDecision()
    assert lib.McuSession_ReceiveCommand(lib.TestControl_Session(memory), c.byref(command), 0, c.byref(decision))
    assert decision.execute
    state = lib.TestControl_Work(memory)
    assert builder.McuWorkState_BeginAccepted(state, query[8:], 78, 16)
    initial, final = available(builder, 1, 0), available(builder, 2, 1000)
    summary, scratch = Summary(5, 2500, 1, 0, 1, 0, 0), c.create_string_buffer(199)
    assert builder.McuResultBuilder_Complete(state, c.byref(summary), c.byref(initial), c.byref(final), scratch, 199)
    result = scratch.raw
    replies.clear()
    sent = []
    def write(frame):
        sent.append(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        client = McuResultHandoff(store, write, uart.decode_payload("RESULT_SAVED", result[:60]))
        client.poll(0)
        assert client.accept_frame(replies.pop(0), 0)
        body = replies.pop(0)
        assert uart.decode_frame(body)["payload"] == result
        assert client.accept_frame(body, 0)
        assert store.get_native_mcu_result(42, 1)["payload"] == result
        assert lib.McuWorkState_CopyHeld(state, scratch, 199) == 0
        assert len(store.list_native_result_report_tasks()) == 1
        assert [uart.decode_frame(frame)["messageName"] for frame in sent] == ["QUERY_RESULT", "RESULT_SAVED"]
    finally:
        store.close()


def test_builder_target_compile(tmp_path):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required")
    output = tmp_path / "builder.o"
    run = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
        "-I", str(ROOT / "hardware_mcu/USER"), "-c", str(ROOT / "hardware_mcu/USER/mcu_result_builder.c"),
        "-o", str(output)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not run.stdout.strip() and not run.stderr.strip()
    sizes = subprocess.run([str(compiler.with_name("fromelf.exe")), "--text", "-z", str(output)],
        capture_output=True, text=True, timeout=30)
    assert sizes.returncode == 0, sizes.stdout + sizes.stderr
    print(sizes.stdout)
