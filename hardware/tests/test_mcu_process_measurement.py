"""A real C work measurement keeps its original work identity and native value kind."""
import ctypes as c
import subprocess
from pathlib import Path

import pytest
import uart2_protocol as uart
from hardware.tests.test_mcu_result_builder import Measurement, available, builder, work
from hardware.tests.test_mcu_device_facts import WeightConfig
from hardware.tests.test_mcu_work_state_c import query_payload

ROOT = Path(__file__).resolve().parents[2]


class Meta(c.Structure):
    _fields_ = [("config_version", c.c_uint64), ("observed_uptime_ms", c.c_uint64),
                ("step_sequence", c.c_uint32)]


@pytest.fixture(scope="module")
def producer(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    target = tmp_path_factory.mktemp("process-producer") / "producer.dll"
    source = ROOT / "hardware_mcu/USER/mcu_process_measurement.c"
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1", str(source),
        str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        "-Wl,/EXPORT:McuProcessMeasurement_BuildWorkEvent", "-o", str(target)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    library = c.CDLL(str(target))
    library.McuProcessMeasurement_BuildWorkEvent.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p,
        c.c_uint8, c.c_void_p, c.c_size_t]
    library.McuProcessMeasurement_BuildWorkEvent.restype = c.c_size_t
    return library.McuProcessMeasurement_BuildWorkEvent


def test_actual_c_preopen_measurement_names_retained_work_without_changing_it(producer, builder):
    state = work(builder)
    assert builder.McuWorkState_SetPhase(state, 17)
    measurement = available(builder, 1, 0)
    meta, scratch, before = Meta(7, 2000, 1), c.create_string_buffer(242), bytes(state)
    length = producer(state, c.byref(measurement), c.byref(meta), 48, scratch, 242)
    assert length == 97
    decoded = uart.decode_payload("WORK_PREOPEN_WEIGHT_READY", scratch.raw[:length])
    assert decoded["measurementKind"] == "STABLE_MEAN" and decoded["reportedWeightGrams"] == 0
    assert decoded["measurementUid"] == "00000000-0000-0000-0000-000000000001"
    assert decoded["mcuBootId"] == 42 and decoded["mcuEventSequence"] == 1
    assert decoded["configVersion"] == 7 and decoded["sampleCount"] == 5
    assert bytes(state) == before


def test_no_fullness_observation_cannot_leak_old_scratch_as_new_sensor_evidence(producer, builder):
    state = work(builder)
    assert builder.McuWorkState_SetPhase(state, 24)
    measured = available(builder, 1, 200)
    meta = Meta(7, 2000, 1)
    scratch = c.create_string_buffer(b"\xa5" * 242, 242)
    size = producer(state, c.byref(measured), c.byref(meta), 50, scratch, 242)
    assert size == 207
    values = uart.decode_payload("WORK_POSTCLOSE_WEIGHT_READY", scratch.raw[:size])
    assert values["workFullnessStatus"] == "NOT_SAMPLED"
    assert values["fullnessGroupSequence"] == 0 and values["workFullnessSensorValue"] == "NOT_OBSERVED"


@pytest.mark.parametrize("name,phase,clean,step", [
    ("WORK_PREOPEN_WEIGHT_READY", 17, False, 1),
    ("WORK_POSTCLOSE_WEIGHT_READY", 24, False, 3),
    ("WORK_PREUNLOCK_WEIGHT_READY", 33, True, 0),
    ("CLEAN_FINAL_WEIGHT_READY", 37, True, 7),
])
def test_all_work_producers_keep_the_same_real_timeout_median_evidence(producer, builder, name, phase, clean, step):
    changes = {"workType": "CLEAN_OPERATION"} if clean else {}
    state = work(builder, **changes)
    assert builder.McuWorkState_SetPhase(state, phase)
    original = uart.decode_payload("QUERY_WORK", query_payload(**changes))
    core, measured = (c.c_uint64 * 48)(), Measurement()
    config = WeightConfig(5000, 1500, 750, 100, 5, 5, -350000, 350000)
    assert builder.WeightMeasurement_Begin(core, c.byref(config), 9, 0, 0)
    for sample in range(1, 21):
        timestamp = (sample - 1) * 250
        assert builder.WeightMeasurement_Observe(core, 9, sample, timestamp,
            -1000 if sample % 2 else 1000, timestamp)
    result = builder.WeightMeasurement_Poll(core, 5000)
    assert result.available and result.status == 2 and result.grams == 0
    assert builder.McuResultMeasurement_FromAvailable(c.byref(measured), c.byref(result),
        (9).to_bytes(16, "big"), 42, 19, 4)
    meta, scratch = Meta(7, 4294967296, step), c.create_string_buffer(242)
    before = bytes(state)
    length = producer(state, c.byref(measured), c.byref(meta), uart.MESSAGE_SPECS[name]["id"], scratch, 242)
    assert length == uart.MESSAGE_SPECS[name]["maximumPayloadLength"]
    frame = uart.encode_frame(name, 99, scratch.raw[:length])
    assert uart.decode_frame(frame, sender_role="MCU")["messageName"] == name
    decoded = uart.decode_payload(name, scratch.raw[:length])
    assert decoded["operationUid" if clean else "sessionUid"] == original["workUid"]
    if "mcuCommandUid" in decoded:
        assert decoded["mcuCommandUid"] == original["mcuCommandUid"]
    assert decoded["portNo"] == original["portNo"]
    assert decoded["measurementKind"] == "TIMEOUT_MEDIAN" and decoded["reportedWeightGrams"] == 0
    assert decoded["sampleCount"] == 20 and decoded["sampleSpanGrams"] == 2000
    assert decoded["measurementElapsedMs"] == 5000 and decoded["uptimeMs"] == 4294967296
    assert decoded["mcuEventSequence"] == 19 and decoded["calibrationVersion"] == 4
    assert bytes(state) == before and measured.event_sequence == 19


@pytest.mark.parametrize("invalid", ["boot", "kind", "fault", "span", "samples", "elapsed",
    "config", "early_time", "step_wrap", "no_step", "phase", "unsupported", "short", "null"])
def test_invalid_process_assembly_does_not_advance_work_or_result(producer, builder, invalid):
    state = work(builder)
    assert builder.McuWorkState_SetPhase(state, 17)
    measured = available(builder, 1, 100)
    meta, scratch = Meta(7, 2000, 1), c.create_string_buffer(242)
    message, capacity = 48, 242
    if invalid == "boot": measured.source_boot_id = 43
    elif invalid == "kind": measured.kind = 1
    elif invalid == "fault": measured.fault_code = 200
    elif invalid == "span": measured.span_grams = 101
    elif invalid == "samples": measured.sample_count = 4
    elif invalid == "elapsed": measured.elapsed_ms = 5001
    elif invalid == "config": meta.config_version = 0
    elif invalid == "early_time": meta.observed_uptime_ms = 999
    elif invalid == "step_wrap": meta.step_sequence = 65536
    elif invalid == "no_step": meta.step_sequence = 0
    elif invalid == "phase": assert builder.McuWorkState_SetPhase(state, 21)
    elif invalid == "unsupported": message = 57
    elif invalid == "short": capacity = 96
    before = bytes(state), bytes(measured), bytes(meta)
    assert not producer(None if invalid == "null" else state, c.byref(measured), c.byref(meta),
                        message, scratch, capacity)
    assert (bytes(state), bytes(measured), bytes(meta)) == before


def test_explicit_terminal_no_data_is_not_a_successful_zero(producer, builder):
    state = work(builder)
    assert builder.McuWorkState_SetPhase(state, 17)
    # Supplied by the measurement owner, not invented by this serializer.
    measured = Measurement()
    measured.kind, measured.source_boot_id, measured.event_sequence = 4, 42, 9
    measured.uid[:] = (7).to_bytes(16, "big")
    measured.elapsed_ms = 5000
    measured.fault_code = uart.REGISTRY["enums"]["FaultCode"]["values"]["WEIGHT_TIMEOUT"]
    meta, scratch = Meta(7, 5000, 1), c.create_string_buffer(242)
    length = producer(state, c.byref(measured), c.byref(meta), 48, scratch, 242)
    decoded = uart.decode_payload("WORK_PREOPEN_WEIGHT_READY", scratch.raw[:length])
    assert decoded["measurementKind"] == "UNAVAILABLE" and decoded["sampleCount"] == 0
    assert decoded["reportedWeightGrams"] == 0 and decoded["faultCode"] == "WEIGHT_TIMEOUT"
    measured.grams = 1
    assert not producer(state, c.byref(measured), c.byref(meta), 48, scratch, 242)
