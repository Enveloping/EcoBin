"""Actual C controller/facts modules, decoded by the generated Python codec."""
import ctypes as c
import subprocess
from pathlib import Path

import pytest
import uart2_protocol as uart

ROOT = Path(__file__).resolve().parents[2]


class WeightConfig(c.Structure):
    _fields_ = [(name, c.c_uint32) for name in ("timeout", "window", "age", "span")] + [
        ("stable_samples", c.c_uint8), ("median_samples", c.c_uint8), ("minimum", c.c_int32), ("maximum", c.c_int32)]


class WeightResult(c.Structure):
    _fields_ = [(name, c.c_uint32) for name in ("sequence", "elapsed", "span")] + [
        ("grams", c.c_int32), ("status", c.c_uint8), ("available", c.c_uint8), ("count", c.c_uint8)]


class ScaleObservation(c.Structure):
    _fields_ = [("captured", c.c_uint64), ("attempt", c.c_uint32), ("calibration", c.c_uint32),
        ("grams", c.c_int32), ("port", c.c_uint8), ("status", c.c_uint8)]


@pytest.fixture
def mcu(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    signatures = {
        "McuDeviceFacts_Init": (None, [c.c_void_p, c.c_uint8]),
        "McuDeviceFacts_PublishSmoke": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_uint64]),
        "McuDeviceFacts_PublishFullness": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_uint8, c.c_uint64, c.c_uint8, c.c_uint16]),
        "McuDeviceFacts_Capture": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p, c.c_size_t]),
        "McuWorkState_Init": (None, [c.c_void_p, c.c_uint64]),
        "McuWorkState_BeginAccepted": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint8]),
        "McuWorkState_Complete": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_Saved": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_CopyHeld": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "ActuatorRuntime_SetDoorTarget": (c.c_uint8, [c.c_uint8]),
        "ActuatorRuntime_Unlock": (c.c_uint8, [c.c_uint32]),
        "ActuatorRuntime_StopForUpdate": (None, []),
        "ActuatorRuntime_Tick": (None, []),
        "RuntimeClock_Advance": (None, [c.c_uint32]),
        "TestFacts_InitHardware": (None, []), "TestFacts_Pinch": (None, [c.c_uint8]),
        "TestFacts_Writes": (c.c_uint32, []),
        "McuDeviceFacts_ObserveScale": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint64, c.c_uint32, c.c_void_p, c.c_size_t, c.c_int32, c.c_int32]),
        "McuDeviceFacts_ScaleTimeout": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint64, c.c_uint32]),
        "McuDeviceFacts_PublishScaleObservation": (c.c_uint8, [c.c_void_p, c.POINTER(ScaleObservation)]),
        "McuDeviceFacts_PublishConfiguration": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_void_p, c.c_void_p, c.c_uint8]),
        "McuDeviceFacts_PublishMeasurement": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint64, c.c_uint64]),
        "WeightMeasurement_Begin": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint32, c.c_uint32, c.c_uint32]),
        "WeightMeasurement_Observe": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint32, c.c_uint32, c.c_int32, c.c_uint32]),
        "WeightMeasurement_Poll": (WeightResult, [c.c_void_p, c.c_uint32]),
    }
    path = tmp_path / "facts.dll"
    compiled = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-I", str(user),
        *[str(user / (name + ".c")) for name in ("mcu_device_facts", "mcu_work_state", "mcu_result_slot", "actuator_runtime", "door_control", "clean_lock", "runtime_clock", "scale_reader", "weight_measurement")],
        str(ROOT / "hardware_mcu/tests/device_facts_host.c"),
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(path)],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    library = c.CDLL(str(path))
    for name, (restype, argtypes) in signatures.items():
        fn = getattr(library, name)
        fn.restype, fn.argtypes = restype, argtypes
    library.TestFacts_InitHardware()
    return library


def sources(mcu):
    facts, work = (c.c_uint64 * 28)(), (c.c_uint64 * 38)()
    mcu.McuDeviceFacts_Init(facts, 1)
    mcu.McuWorkState_Init(work, 42)
    return facts, work


def capture(mcu, facts, work, **changes):
    request = uart.encode_payload("QUERY_DEVICE_FACTS", {"queryId": 1, "targetMcuBootId": 42, "portNo": 1} | changes)
    size = uart.MESSAGE_SPECS["DEVICE_FACTS_REPLY"]["maximumPayloadLength"]
    out = c.create_string_buffer(size)
    assert mcu.McuDeviceFacts_Capture(facts, work, request, len(request), out, size) == size
    return uart.decode_payload("DEVICE_FACTS_REPLY", out.raw)


@pytest.mark.parametrize("changes", [
    {"attempt": 0}, {"attempt": 1}, {"captured": 99}, {"captured": 201}, {"port": 0}, {"port": 2},
    {"status": 1}, {"status": 255}, {"status": 2, "grams": 1},
    {"attempt": 2, "calibration": 8}, {"attempt": 2, "grams": 501}, {"attempt": 2, "captured": 101},
])
def test_reader_observation_rejects_foreign_stale_future_and_conflicting_facts(mcu, changes):
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(200)
    original = ScaleObservation(100, 2, 7, 500, 1, 0)
    assert mcu.McuDeviceFacts_PublishScaleObservation(facts, c.byref(original))
    before = capture(mcu, facts, work)
    values = {"captured": 100, "attempt": 3, "calibration": 7, "grams": 500, "port": 1, "status": 0} | changes
    conflict = ScaleObservation(**values)
    assert not mcu.McuDeviceFacts_PublishScaleObservation(facts, c.byref(conflict))
    assert capture(mcu, facts, work) == before
    assert mcu.McuDeviceFacts_PublishScaleObservation(facts, c.byref(original))
    assert capture(mcu, facts, work) == before


def test_latest_scale_failure_does_not_rewrite_historical_terminal_measurement(mcu):
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(1200)
    terminal = WeightResult(1, 1020, 100, 500, 1, 1, 5)
    assert mcu.McuDeviceFacts_PublishMeasurement(facts, c.byref(terminal), 3, 1020)
    assert mcu.McuDeviceFacts_PublishScaleObservation(facts, c.byref(ScaleObservation(1020, 5, 7, 490, 1, 0)))
    before = capture(mcu, facts, work)
    assert mcu.McuDeviceFacts_PublishScaleObservation(facts, c.byref(ScaleObservation(1200, 6, 8, 0, 1, 2)))
    after = capture(mcu, facts, work)
    assert after["scaleReadStatus"] == "TIMEOUT" and after["scaleCalibrationVersion"] == 8
    for key in before:
        if key.startswith("measurement"):
            assert after[key] == before[key]


def test_unobserved_environment_is_not_reported_as_safe_or_clear(mcu):
    facts, work = sources(mcu)
    values = capture(mcu, facts, work)
    assert values["smokeObservationState"] == "NOT_OBSERVED"
    assert values["smokeObservedUptimeMs"] == 0
    assert values["fullnessReadStatus"] == "NOT_OBSERVED"
    assert values["fullnessObservationKind"] == "NONE"
    assert values["fullnessCapturedUptimeMs"] == 0
    assert values["fullnessInfraredBlocked"] is False
    assert values["fullnessDistanceMm"] == 0


def test_environment_publication_retains_actual_capture_time_and_failure(mcu):
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(100)
    assert mcu.McuDeviceFacts_PublishSmoke(facts, 1, 100)
    assert mcu.McuDeviceFacts_PublishFullness(facts, 2, 1, 100, 1, 0)
    mcu.RuntimeClock_Advance(500)
    values = capture(mcu, facts, work)
    assert values["smokeObservationState"] == "NORMAL"
    assert values["smokeObservedUptimeMs"] == 100
    assert values["fullnessObservationKind"] == "DIGITAL_INFRARED"
    assert values["fullnessReadStatus"] == "VALID"
    assert values["fullnessInfraredBlocked"] is True
    assert values["fullnessCapturedUptimeMs"] == 100
    assert mcu.McuDeviceFacts_PublishSmoke(facts, 3, 600)
    assert mcu.McuDeviceFacts_PublishFullness(facts, 2, 2, 600, 0, 0)
    values = capture(mcu, facts, work)
    assert values["smokeObservationState"] == "UNAVAILABLE"
    assert values["fullnessReadStatus"] == "UNAVAILABLE"
    assert values["fullnessInfraredBlocked"] is False
    assert values["fullnessDistanceMm"] == 0
    assert values["fullnessCapturedUptimeMs"] == 600


@pytest.mark.parametrize("state", [1, 2, 3])
def test_actual_smoke_state_is_retained_without_consuming_or_refreshing_it(mcu, state):
    facts, work = sources(mcu)
    assert mcu.McuDeviceFacts_PublishSmoke(facts, state, 0)
    before = bytes(facts)
    mcu.RuntimeClock_Advance(0xFFFFFFFF)
    mcu.RuntimeClock_Advance(100)
    values = capture(mcu, facts, work)
    assert values["smokeObservedUptimeMs"] == 0
    assert values["smokeObservationState"] == {1: "NORMAL", 2: "ALARM", 3: "UNAVAILABLE"}[state]
    assert values["capturedUptimeMs"] == 0xFFFFFFFF + 100
    assert bytes(facts) == before


@pytest.mark.parametrize("kind,status,blocked,distance", [
    (2, 1, 0, 0), (2, 1, 1, 0), (2, 2, 0, 0),
    (1, 1, 0, 0), (1, 1, 0, 65535), (1, 2, 0, 0),
])
def test_fullness_preserves_raw_kind_and_does_not_derive_a_business_percentage(mcu, kind, status, blocked, distance):
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(100)
    assert mcu.McuDeviceFacts_PublishFullness(facts, kind, status, 100, blocked, distance)
    values = capture(mcu, facts, work)
    assert values["fullnessDistanceMm"] == distance
    assert values["fullnessInfraredBlocked"] == bool(blocked)
    assert values["fullnessReadStatus"] == {1: "VALID", 2: "UNAVAILABLE"}[status]
    assert values["fullnessObservationKind"] == {1: "ULTRASONIC", 2: "DIGITAL_INFRARED"}[kind]


@pytest.mark.parametrize("kind,status,blocked,distance", [
    (0, 1, 0, 0), (3, 1, 0, 0), (1, 0, 0, 0), (1, 3, 0, 0),
    (1, 1, 1, 20), (2, 1, 1, 20), (2, 1, 2, 0),
    (1, 2, 0, 20), (2, 2, 1, 0),
])
def test_contradictory_environment_fields_do_not_replace_previous_facts(mcu, kind, status, blocked, distance):
    facts, work = sources(mcu)
    before = bytes(facts)
    assert not mcu.McuDeviceFacts_PublishFullness(facts, kind, status, 0, blocked, distance)
    assert bytes(facts) == before
    assert capture(mcu, facts, work)["fullnessReadStatus"] == "NOT_OBSERVED"


@pytest.mark.parametrize("sensor", ["smoke", "fullness"])
def test_environment_rejects_future_older_and_same_time_conflicting_observations(mcu, sensor):
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(100)
    def publish(at, value):
        if sensor == "smoke":
            return mcu.McuDeviceFacts_PublishSmoke(facts, value, at)
        return mcu.McuDeviceFacts_PublishFullness(facts, 2, 1, at, value - 1, 0)
    assert publish(100, 1)
    before = bytes(facts)
    assert not publish(101, 1)
    assert not publish(99, 1)
    assert not publish(100, 2)
    assert publish(100, 1)
    assert bytes(facts) == before
    mcu.RuntimeClock_Advance(50)
    assert publish(150, 2)
    assert bytes(facts) != before


@pytest.mark.parametrize("scope", [{"targetMcuBootId": 43}, {"portNo": 2}])
def test_unavailable_scope_zeroes_environment_instead_of_leaking_other_boot_or_port(mcu, scope):
    facts, work = sources(mcu)
    assert mcu.McuDeviceFacts_PublishSmoke(facts, 2, 0)
    assert mcu.McuDeviceFacts_PublishFullness(facts, 2, 1, 0, 1, 0)
    values = capture(mcu, facts, work, **scope)
    assert values["smokeObservationState"] == "NOT_OBSERVED"
    assert values["fullnessReadStatus"] == "NOT_OBSERVED"
    assert values["fullnessObservationKind"] == "NONE"
    assert not values["fullnessInfraredBlocked"]


def test_snapshot_uses_one_control_update_and_never_writes_outputs(mcu):
    facts, work = sources(mcu)
    assert mcu.ActuatorRuntime_SetDoorTarget(1)  # Internal CLOSE=1; wire CLOSE=2
    mcu.RuntimeClock_Advance(5)
    mcu.TestFacts_Pinch(1)
    writes = mcu.TestFacts_Writes()
    retained = bytes(facts), bytes(work)
    before = capture(mcu, facts, work)
    assert before["lastDeliveryDoorCommand"] == "CLOSE"
    assert before["pb7Output"] and not before["pb5Active"]
    assert before["controlUptimeMs"] == 0 and before["capturedUptimeMs"] == 5
    assert mcu.TestFacts_Writes() == writes
    assert (bytes(facts), bytes(work)) == retained
    mcu.ActuatorRuntime_Tick()
    after = capture(mcu, facts, work, queryId=2)
    assert after["pb5Active"] and after["pinchPaused"] and not after["pb7Output"]
    assert after["lastDeliveryDoorCommand"] == "CLOSE" and after["doorActionActive"]
    assert after["controlUptimeMs"] == 5
    assert before["pb7Output"]  # Already captured body is immutable
    assert after["scaleReadStatus"] == "NOT_OBSERVED"


def scale_frame(grams):
    raw = (grams & 0xFFFFFFFF)
    frame = bytes([1, 3, 4]) + (raw & 0xFFFF).to_bytes(2, "big") + (raw >> 16).to_bytes(2, "big")
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return frame + crc.to_bytes(2, "little")


def test_raw_reading_keeps_capture_time_and_failure_does_not_reuse_old_weight(mcu):
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(250)
    raw = scale_frame(500)
    assert mcu.McuDeviceFacts_ObserveScale(facts, 1, 250, 4, raw, len(raw), -350000, 350000)
    mcu.RuntimeClock_Advance(1000)
    values = capture(mcu, facts, work)
    assert values["scaleReadStatus"] == "VALID" and values["scaleWeightGrams"] == 500
    assert values["scaleCapturedUptimeMs"] == 250 and values["capturedUptimeMs"] == 1250
    assert not mcu.McuDeviceFacts_ObserveScale(facts, 1, 1250, 4, raw, len(raw), -350000, 350000)
    assert mcu.McuDeviceFacts_ScaleTimeout(facts, 2, 1250, 4)
    values = capture(mcu, facts, work)
    assert values["scaleReadStatus"] == "TIMEOUT" and values["scaleWeightGrams"] == 0
    assert values["scaleAttemptSequence"] == 2
    corrupt = raw[:-1] + bytes([raw[-1] ^ 1])
    assert mcu.McuDeviceFacts_ObserveScale(facts, 3, 1250, 4, corrupt, len(corrupt), -350000, 350000)
    assert capture(mcu, facts, work)["scaleReadStatus"] == "CRC_ERROR"
    zero = scale_frame(0)
    assert mcu.McuDeviceFacts_ObserveScale(facts, 4, 1250, 4, zero, len(zero), -350000, 350000)
    assert capture(mcu, facts, work)["scaleReadStatus"] == "VALID"
    assert not mcu.McuDeviceFacts_ScaleTimeout(facts, 5, 1251, 4)  # impossible future ingress
    assert mcu.McuDeviceFacts_ObserveScale(facts, 5, 1250, 4, raw, len(raw), -100, 100)
    assert capture(mcu, facts, work)["scaleReadStatus"] == "RANGE_ERROR"
    assert mcu.McuDeviceFacts_ObserveScale(facts, 6, 1250, 4, raw, len(raw) - 1, -350000, 350000)
    assert capture(mcu, facts, work)["scaleReadStatus"] == "PROTOCOL_ERROR"
    mcu.RuntimeClock_Advance(0xFFFFFFFF)
    assert capture(mcu, facts, work)["capturedUptimeMs"] == 1250 + 0xFFFFFFFF
    assert capture(mcu, facts, work)["scaleCapturedUptimeMs"] == 1250  # does not wrap fresh


def test_configuration_and_real_measurement_core_are_distinct_snapshot_facts(mcu):
    facts, work = sources(mcu)
    content, config = bytes.fromhex("ab" * 32), bytes.fromhex("cd" * 32)
    assert not mcu.McuDeviceFacts_PublishConfiguration(facts, 3, content, bytes(32), 0)
    assert mcu.McuDeviceFacts_PublishConfiguration(facts, 3, content, config, 0)
    assert not mcu.McuDeviceFacts_PublishConfiguration(facts, 2, content, config, 0)
    assert not mcu.McuDeviceFacts_PublishConfiguration(facts, 3, config, content, 0)
    engine = (c.c_uint64 * 48)()
    policy = WeightConfig(5000, 1500, 750, 100, 5, 5, -350000, 350000)
    assert mcu.WeightMeasurement_Begin(engine, c.byref(policy), 1, 0, 0)
    for sequence in range(1, 21):
        now = sequence * 250
        mcu.RuntimeClock_Advance(250)
        assert mcu.WeightMeasurement_Observe(engine, 1, sequence, now, 0 if sequence % 2 else 500, now)
        observed = mcu.WeightMeasurement_Poll(engine, now)
        assert mcu.McuDeviceFacts_PublishMeasurement(facts, c.byref(observed), 3, now)
    values = capture(mcu, facts, work)
    assert values["measurementState"] == "TIMEOUT_MEDIAN"
    assert values["measurementWeightGrams"] == 250 and values["measurementElapsedMs"] == 5000
    assert values["measurementSampleCount"] == 20 and values["measurementSpanGrams"] == 500
    assert values["measurementConfigVersion"] == values["appliedConfigVersion"] == 3
    assert values["scaleReadStatus"] == "NOT_OBSERVED"  # result does not claim a fresh raw read
    assert mcu.McuDeviceFacts_PublishConfiguration(facts, 4, config, content, 1)
    values = capture(mcu, facts, work)
    assert values["measurementConfigVersion"] == 3 and values["appliedConfigVersion"] == 4
    assert values["configStaging"]
    mcu.RuntimeClock_Advance(500)
    assert mcu.McuDeviceFacts_PublishMeasurement(facts, c.byref(observed), 3, 5500)
    assert capture(mcu, facts, work)["measurementObservedUptimeMs"] == 5000
    observed.grams += 1
    assert not mcu.McuDeviceFacts_PublishMeasurement(facts, c.byref(observed), 3, 5000)
    assert capture(mcu, facts, work)["measurementWeightGrams"] == 250


def test_python_queries_real_c_facts_and_counts_transport_delay_in_sample_age(mcu, tmp_path):
    from edge_store import EdgeStore
    from mcu_work_query import McuDeviceFactsQuery
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(250)
    raw = scale_frame(0)
    assert mcu.McuDeviceFacts_ObserveScale(facts, 1, 250, 4, raw, len(raw), -350000, 350000)
    incoming, sent = [], []
    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        assert decoded["messageName"] == "QUERY_DEVICE_FACTS"
        sent.append(frame)
        out = c.create_string_buffer(uart.MESSAGE_SPECS["DEVICE_FACTS_REPLY"]["maximumPayloadLength"])
        assert mcu.McuDeviceFacts_Capture(facts, work, decoded["payload"], len(decoded["payload"]), out, len(out)) == len(out)
        incoming.append(uart.encode_frame("DEVICE_FACTS_REPLY", 1, out.raw))
        return len(frame)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        client = McuDeviceFactsQuery(store, write, target_mcu_boot_id=42, port_no=1)
        assert client.poll(0) == 1
        assert client.accept_frame(incoming[-1], 100)
        assert client.latest_weight(100, maximum_age_ms=750) == 0  # real zero, not missing
        assert client.latest_weight(751, maximum_age_ms=750) is None
        assert client.observation(751)["scaleReadStatus"] == "VALID"  # historical diagnostic still visible
        mcu.RuntimeClock_Advance(1000)
        assert client.poll(1000) == 2
        assert not client.accept_frame(incoming[0], 1001)
        assert client.accept_frame(incoming[-1], 1001)
        assert client.latest_weight(1001, maximum_age_ms=750) is None
        mcu.McuWorkState_Init(work, 0)
        assert client.poll(2000) == 3
        assert client.accept_frame(incoming[-1], 2001)
        assert client.observation(2001)["status"] == "BOOT_MISMATCH"
        assert client.latest_weight(2001) is None
        assert len(sent) == 3 and store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_snapshot_of_retained_work_never_releases_its_result(mcu):
    from hardware.tests.test_mcu_work_state_c import query_payload
    from hardware.tests.test_native_result_handoff import result_payload
    facts, work = sources(mcu)
    original = query_payload()
    assert mcu.McuWorkState_BeginAccepted(work, original[8:], 78, 16)
    values = capture(mcu, facts, work)
    assert values["retainedWorkState"] == "RUNNING" and values["retainedResultSequence"] == 0
    assert values["retainedWorkUid"] == uart.decode_payload("QUERY_WORK", original)["workUid"]
    result = result_payload()
    assert mcu.McuWorkState_Complete(work, result, len(result))
    assert capture(mcu, facts, work)["retainedWorkState"] == "RESULT_HELD"
    out = c.create_string_buffer(199)
    assert mcu.McuWorkState_CopyHeld(work, out, len(out)) == 199 and out.raw == result
    assert mcu.McuWorkState_Saved(work, result[:60], 60) == 1
    values = capture(mcu, facts, work)
    assert values["retainedWorkState"] == "RESULT_RELEASED" and values["retainedResultSequence"] == 3


def test_open_ignores_pinch_and_update_stop_does_not_erase_last_target(mcu):
    facts, work = sources(mcu)
    mcu.TestFacts_Pinch(1)
    assert mcu.ActuatorRuntime_SetDoorTarget(2)
    assert mcu.ActuatorRuntime_Unlock(4800)
    values = capture(mcu, facts, work)
    assert values["pb5Active"] and values["pb6Output"] and not values["pinchPaused"]
    assert values["cleanLockPowered"]
    mcu.ActuatorRuntime_StopForUpdate()
    values = capture(mcu, facts, work)
    assert values["lastDeliveryDoorCommand"] == "OPEN" and values["updateLatched"]
    assert not values["doorActionActive"] and not values["pb6Output"] and not values["cleanLockPowered"]
    assert capture(mcu, facts, work, portNo=2)["status"] == "PORT_UNSUPPORTED"
    mcu.McuWorkState_Init(work, 0)
    values = capture(mcu, facts, work)
    assert values["status"] == "BOOT_MISMATCH" and values["lastDeliveryDoorCommand"] == "NONE"


@pytest.mark.parametrize("module", ["mcu_device_facts", "actuator_runtime", "runtime_clock"])
def test_facts_modules_target_compile(tmp_path, module):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required")
    result = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
        "-I", str(ROOT / "hardware_mcu/USER"), "-c", str(ROOT / "hardware_mcu/USER" / (module + ".c")),
        "-o", str(tmp_path / (module + ".o"))], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stdout.strip() and not result.stderr.strip()
