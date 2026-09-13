"""Actual sensor group + actual C weight -> original business-scoped bytes."""
import ctypes as c
import pytest
import uart2_protocol as uart
from hardware.tests.test_mcu_ultrasonic import ultrasonic_library, ultrasonic
from hardware.tests.test_mcu_fullness_run import ready, with_policy, complete
from hardware.tests.test_mcu_result_builder import builder, work, available
from hardware.tests.test_mcu_process_measurement import Meta


class FullnessPolicy(c.Structure):
    _fields_ = [("version", c.c_uint64), ("echo_timeout", c.c_uint32),
        ("settle_wait", c.c_uint32), ("threshold", c.c_uint32),
        ("port", c.c_uint8), ("enabled", c.c_uint8), ("kind", c.c_uint8),
        ("count", c.c_uint8), ("minimum", c.c_uint8)]


@pytest.mark.parametrize("samples, basis", [([100, 200, 150, 900, 300], "MEASURED_MEDIAN"),
    ([None] * 5, "NO_ECHO_CLEAR_FALLBACK"), ([100, None, None, None, None], "INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK")])
def test_actual_group_is_embedded_with_its_original_configuration_and_times(ultrasonic, builder, samples, basis):
    lib = ultrasonic
    run, facts, (_, config, _, _), candidate = ready(lib, with_policy(fullnessSettleWaitMs=0))
    assert lib.McuFullnessRun_Begin(run, facts, config)
    group = complete(lib, run, samples)
    policy = FullnessPolicy()
    assert lib.McuConfiguration_ReadFullnessPolicy(config, 1, c.byref(policy))
    state = work(builder)
    assert builder.McuWorkState_SetPhase(state, 24)
    measured = available(builder, 7, 200)
    meta = Meta(8, 2000, 1)
    output = c.create_string_buffer(b"\xa5" * 242, 242)
    size = lib.McuProcessMeasurement_BuildWorkEventWithFullness(state, c.byref(measured), c.byref(meta),
        run, 50, output, 242)
    assert size == 207
    value = uart.decode_payload("WORK_POSTCLOSE_WEIGHT_READY", output.raw[:size])
    assert value["workFullnessStatus"] == "COMPLETE" and value["workFullnessBasis"] == basis
    assert value["fullnessMcuPayloadSha256"] == candidate.mcu_payload_sha256
    assert value["fullnessCompletedUptimeMs"] == group.completed_ms
    assert value["fullnessLastCapturedUptimeMs"] == group.last_captured_ms
    assert value["fullnessValidSampleCount"] == group.valid
    assert value["uptimeMs"] == 2000 and value["reportedWeightGrams"] == 200
    assert value["fullnessGroupSequence"] == 1 and value["mcuEventSequence"] == 7
