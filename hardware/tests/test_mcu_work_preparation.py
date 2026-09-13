"""Native byte stream -> real config/session/measurement/work preparation, no GPIO actions."""
import ctypes as c
import subprocess
from pathlib import Path

import pytest
import uart2_protocol as uart
from mcu_configuration import NativeMcuConfiguration
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_device_facts import scale_frame
from contracts.tests.test_uart_v2_command_guards import minimal_command
from edge_store import EdgeStore
from hardware.tests.test_native_result_handoff import result_payload
from hardware.tests.test_mcu_actuator_event_journal import Reservation

ROOT = Path(__file__).resolve().parents[2]
SINK = c.CFUNCTYPE(None, c.c_void_p, c.c_size_t, c.c_void_p)
GUARD = c.CFUNCTYPE(c.c_uint16, c.c_uint8, c.c_void_p, c.c_size_t, c.c_uint64, c.c_void_p)


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user, output = ROOT / "hardware_mcu/USER", tmp_path_factory.mktemp("preparation") / "preparation.dll"
    signatures = {
        "McuControlEndpoint_Init": (None, [c.c_void_p, c.c_uint8, SINK, c.c_void_p]),
        "McuControlEndpoint_Feed": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64]),
        "McuControlEndpoint_ReserveEventSequence": (c.c_uint32, [c.c_void_p]),
        "McuControlEndpoint_ReserveActuatorEvents": (c.c_uint8, [c.c_void_p, c.c_uint8, c.POINTER(Reservation)]),
        "McuControlEndpoint_CancelActuatorEvents": (c.c_uint8, [c.c_void_p, c.POINTER(Reservation)]),
        "McuControlEndpoint_PublishActuatorEvent": (c.c_uint32, [c.c_void_p, c.POINTER(Reservation),
            c.c_uint8, c.c_uint8, c.c_void_p, c.c_size_t]),
        "TestPreparation_SetEventSequence": (None, [c.c_void_p, c.c_uint32]),
        "McuWorkPreparation_Attach": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint8, GUARD, c.c_void_p]),
        "McuDeliveryExecution_Attach": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p]),
        "McuCleanExecution_Attach": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p]),
        "McuSafeCloseExecution_Attach": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p]),
        "McuCleanExecution_Request": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint8, c.c_uint16, c.c_uint64]),
        "McuCleanExecution_Confirm": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint16, c.c_void_p, c.c_uint64]),
        "McuDeliveryExecution_Select": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint8, c.c_uint64]),
        "McuWorkPreparation_Poll": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint64]),
        "McuWorkPreparation_AttachFullness": (c.c_uint8, [c.c_void_p, c.c_void_p]),
        "TestUltrasonic_Init": (None, []), "TestUltrasonic_Advance": (None, [c.c_uint32]),
        "TestUltrasonic_Edge": (None, [c.c_uint8]), "TestUltrasonic_Trigger": (c.c_uint8, []),
        "McuWorkPreparation_CopyStart": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t,
            c.POINTER(c.c_uint8), c.POINTER(c.c_uint64)]),
        "McuOpeningGate_Evaluate": (c.c_uint16, [c.c_void_p, c.c_void_p, c.c_uint8,
            c.c_void_p, c.c_size_t, c.c_uint64, c.c_uint64, c.c_void_p]),
        "TestPreparation_Weight": (c.c_void_p, [c.c_void_p]),
        "TestPreparation_Facts": (c.c_void_p, [c.c_void_p]),
        "TestPreparation_Work": (c.c_void_p, [c.c_void_p]),
        "TestPreparation_Process": (c.c_void_p, [c.c_void_p]),
        "McuProcessEventSlot_Init": (None, [c.c_void_p, c.c_uint64]),
        "McuProcessEventSlot_Freeze": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint8, c.c_void_p, c.c_size_t]),
        "McuWorkState_BeginAccepted": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint8]),
        "McuWorkState_Complete": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuDeviceFacts_PublishConfiguration": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_void_p, c.c_void_p, c.c_uint8]),
        "McuWeightRun_StartOwnedAttempt": (c.c_uint32, [c.c_void_p, c.c_uint64]),
        "McuWeightRun_Interrupt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint64]),
        "McuWeightRun_FinishOwnedAttempt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint32,
            c.c_uint64, c.c_uint64, c.c_void_p, c.c_size_t]),
        "TestFacts_InitHardware": (None, []), "TestFacts_Writes": (c.c_uint32, []),
        "TestFacts_Pinch": (None, [c.c_uint8]), "ActuatorRuntime_Tick": (None, []),
        "TestFacts_ReinitializeActuator": (None, []),
        "TestFacts_AdvanceOnEntry": (None, [c.c_uint32, c.c_uint32]),
        "TestFacts_StopOnEntry": (None, [c.c_uint32]),
        "ActuatorRuntime_StopForUpdate": (None, []),
        "ActuatorRuntime_SetDoorTarget": (c.c_uint8, [c.c_uint8]),
        "ActuatorRuntime_Unlock": (c.c_uint8, [c.c_uint32]),
        "RuntimeClock_Advance": (None, [c.c_uint32]),
    }
    sources = ("mcu_control_endpoint", "mcu_actuator_event_journal", "mcu_work_preparation", "mcu_opening_gate", "mcu_delivery_execution", "mcu_clean_execution", "mcu_safe_close_execution", "mcu_configuration", "mcu_config_collection",
        "mcu_session", "mcu_work_state", "mcu_result_slot", "mcu_result_builder", "mcu_process_measurement",
        "mcu_process_event_slot", "mcu_device_facts", "mcu_weight_run", "weight_measurement", "scale_reader",
        "actuator_runtime", "door_control", "clean_lock", "runtime_clock", "mcu_fullness_run", "ultrasonic_reader", "mcu_environment_ultrasonic")
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-I", str(user),
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1", *[str(user / (name + ".c")) for name in sources],
        str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        str(ROOT / "hardware_mcu/tests/device_facts_host.c"), str(ROOT / "hardware_mcu/tests/work_preparation_host.c"),
        str(ROOT / "hardware_mcu/tests/ultrasonic_host.c"),
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(output)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = c.CDLL(str(output))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    return lib


@pytest.fixture
def runtime(library):
    lib = library
    lib.TestFacts_InitHardware()
    endpoint, preparation, replies = (c.c_uint64 * 384)(), (c.c_uint64 * 384)(), []
    prerequisites = {"error": 0}
    sink = SINK(lambda data, size, _: replies.append(c.string_at(data, size)))
    # Explicit external application/safety/capability boundary; not a fake C state machine.
    def check_guard(message, payload, length, now, _):
        if "inspect" in prerequisites:
            prerequisites["inspect"](message, c.string_at(payload, length), now)
        return prerequisites["error"]
    guard = GUARD(check_guard)
    lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
    assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
    yield lib, endpoint, preparation, replies, prerequisites, sink, guard


def exchange(runtime, name, values=None, *, payload=None, now=0):
    lib, endpoint, _, replies, *_ = runtime
    replies.clear()
    raw = payload if payload is not None else uart.encode_payload(name, values)
    frame = uart.encode_frame(name, 1, raw)
    assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
    return [(decoded["messageName"], uart.decode_payload(decoded["messageName"], decoded["payload"]))
        for decoded in (uart.decode_frame(frame, sender_role="MCU") for frame in replies)]


def configured(runtime, *, applied=False, candidate=None):
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 1})[0][1]["mcuBootId"] == 0
    assert exchange(runtime, "BIND_BOOT", {"probeId": 1, "proposedMcuBootId": 42})[0][1]["status"] == "BOUND"
    if candidate is None:
        candidate = NativeMcuConfiguration(**inputs())
    for index in range(1, candidate.part_count + 1):
        name, payload = candidate.encode_part(index, application_uid="11111111-1111-4111-8111-111111111111",
            mcu_command_uid=f"00000000-0000-4000-8000-{index:012d}", target_mcu_boot_id=42, command_sequence=index)
        replies = exchange(runtime, name, payload=payload)
        assert len(replies) == 1 and replies[0][0] == "COMMAND_DECISION"
        assert replies[0][1]["outcome"] == "ACCEPTED"
    if applied:
        # Model the required OTHER consumers' completed-application boundary.
        # The preparation owner itself must NEVER publish whole-device APPLIED.
        lib, endpoint, *_ = runtime
        committed = uart.decode_payload(name, payload)
        assert lib.McuDeviceFacts_PublishConfiguration(lib.TestPreparation_Facts(endpoint),
            committed["configVersion"], bytes.fromhex(committed["contentSha256"]),
            bytes.fromhex(committed["mcuPayloadSha256"]), 0)
    return candidate


def start_values(name="START_DELIVERY_SESSION", **changes):
    config = inputs()
    spec = uart.MESSAGE_SPECS[name]
    values = minimal_command(uart.REGISTRY, spec | {"name": name})
    values.update(targetMcuBootId=42, commandSequence=6, configVersion=config["config_version"],
        configContentSha256=config["content_sha256"], portNo=1, startExecutionWindowMs=30000)
    if name == "START_DELIVERY_SESSION":
        values.update({key: config["device"][key] for key in
            ("continueDeliveryWaitMs", "negativeWeightThresholdGrams", "deliveryAutoCloseMs")})
        values["unitPriceTenThousandths"] = config["ports"][0]["unitPriceTenThousandths"]
    else:
        values["operationWindowMs"] = 300000
    values.update(changes)
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    return values


def original_scope(values, *, clean=False):
    return {"queryId": 1, **{key: values[key] for key in
        ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence", "portNo")},
        "workUid": values["operationUid" if clean else "sessionUid"],
        "workType": "CLEAN_OPERATION" if clean else "DELIVERY_SESSION"}


def test_wire_config_and_start_retain_original_work_and_only_begin_preopen_measurement(runtime):
    lib, _, preparation, *_ = runtime
    writes = lib.TestFacts_Writes()
    configured(runtime, applied=True)
    values = start_values()
    reply = exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]
    assert reply["outcome"] == "ACCEPTED"
    observed = exchange(runtime, "QUERY_WORK", original_scope(values))[0][1]
    assert observed["status"] == "RUNNING" and observed["phase"] == "DELIVERY_FIRST_PREOPEN_MEASURING"
    assert lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(preparation), 0) == 1
    assert lib.TestFacts_Writes() == writes  # neither receiving config nor START opens a door


@pytest.mark.parametrize("change", [
    {"configVersion": 9}, {"configContentSha256": "ff" * 32},
    {"continueDeliveryWaitMs": 1234}, {"negativeWeightThresholdGrams": 1},
    {"deliveryAutoCloseMs": 1234}, {"unitPriceTenThousandths": 1}, {"portNo": 2},
])
def test_start_must_match_actual_configuration_and_this_port_without_faking_applied(runtime, change):
    lib, _, preparation, *_ = runtime
    configured(runtime, applied=True)
    values = start_values(**change)
    reply = exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]
    assert reply["outcome"] == "REJECTED" and reply["errorCode"] == "STATE_CONFLICT"
    assert exchange(runtime, "QUERY_WORK", original_scope(values))[0][1]["status"] == "NOT_FOUND"
    assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(preparation), 0)
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", {"queryId": 2, "targetMcuBootId": 42, "portNo": 1})[0][1]
    assert facts["appliedConfigVersion"] == 8  # rejected start does not alter the externally published facts


def test_duplicate_start_and_new_busy_start_cannot_restart_or_damage_original_work(runtime):
    lib, _, preparation, _, prerequisites, *_ = runtime
    candidate = configured(runtime, applied=True)
    original = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", original)[0][1]["outcome"] == "ACCEPTED"
    reader = lib.TestPreparation_Weight(preparation)
    assert lib.McuWeightRun_StartOwnedAttempt(reader, 0) == 1
    prerequisites["error"] = 11
    assert exchange(runtime, "START_DELIVERY_SESSION", original)[0][1]["outcome"] == "ACCEPTED"  # old decision
    assert not lib.McuWeightRun_StartOwnedAttempt(reader, 0)  # still the original in-flight read
    prerequisites["error"] = 0
    other = start_values(commandSequence=7, mcuCommandUid="77777777-7777-4777-8777-777777777777",
                         sessionUid="88888888-8888-4888-8888-888888888888")
    reply = exchange(runtime, "START_DELIVERY_SESSION", other)[0][1]
    assert reply["outcome"] == "REJECTED" and reply["errorCode"] == "BUSY"
    retained = exchange(runtime, "QUERY_WORK", original_scope(original))[0][1]
    assert retained["phase"] == "DELIVERY_FIRST_PREOPEN_MEASURING"
    name, raw = candidate.encode_part(1, application_uid="99999999-9999-4999-8999-999999999999",
        mcu_command_uid="99999999-9999-4999-8999-999999999998", target_mcu_boot_id=42, command_sequence=8)
    assert exchange(runtime, name, payload=raw)[0][1]["errorCode"] == "BUSY"


def take_samples(runtime, grams, *, start=0, measurement=1, publish=True):
    lib, endpoint, owner, *_ = runtime
    reader = lib.TestPreparation_Weight(owner)
    previous = start
    for index, value in enumerate(grams):
        when = start + index * 250
        attempt = lib.McuWeightRun_StartOwnedAttempt(reader, when)
        assert attempt
        raw = scale_frame(value)
        lib.RuntimeClock_Advance(when + 20 - previous)
        previous = when + 20
        assert lib.McuWeightRun_FinishOwnedAttempt(reader, measurement, attempt, previous, previous, raw, len(raw))
        if publish:
            assert lib.McuWorkPreparation_Poll(owner, endpoint, previous)
    return previous


def test_actual_measurement_samples_are_also_current_scale_facts(runtime):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    assert exchange(runtime, "START_DELIVERY_SESSION", start_values())[0][1]["outcome"] == "ACCEPTED"
    writes = lib.TestFacts_Writes()
    now = take_samples(runtime, [450, 500, 550, 510, 490])
    query = {"queryId": 2, "targetMcuBootId": 42, "portNo": 1}
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", query, now=now)[0][1]
    assert facts["scaleReadStatus"] == "VALID"
    assert facts["scaleAttemptSequence"] == 5
    assert facts["scaleCapturedUptimeMs"] == 1020
    assert facts["scaleWeightGrams"] == 490  # latest raw sample, not the 500 g mean
    assert facts["scaleCalibrationVersion"] == inputs()["ports"][0]["calibrationVersion"]
    assert facts["measurementWeightGrams"] == 500
    lib.RuntimeClock_Advance(2000)
    assert lib.McuWorkPreparation_Poll(owner, endpoint, now + 2000)
    later = exchange(runtime, "QUERY_DEVICE_FACTS", query | {"queryId": 3}, now=now + 2000)[0][1]
    for key in facts:
        if key.startswith("scale") or key.startswith("measurement"):
            assert later[key] == facts[key]
    assert lib.TestFacts_Writes() == writes


def test_owned_timeout_replaces_raw_health_at_original_deadline_then_real_read_recovers(runtime):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    assert exchange(runtime, "START_DELIVERY_SESSION", start_values())[0][1]["outcome"] == "ACCEPTED"
    assert take_samples(runtime, [500]) == 20
    reader = lib.TestPreparation_Weight(owner)
    attempt = lib.McuWeightRun_StartOwnedAttempt(reader, 250)
    assert attempt == 2
    lib.RuntimeClock_Advance(780)  # foreground is late: the 200 ms response deadline was 450
    assert lib.McuWorkPreparation_Poll(owner, endpoint, 800)
    query = {"queryId": 2, "targetMcuBootId": 42, "portNo": 1}
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", query, now=800)[0][1]
    assert facts["scaleReadStatus"] == "TIMEOUT"
    assert facts["scaleAttemptSequence"] == 2 and facts["scaleCapturedUptimeMs"] == 450
    assert facts["scaleWeightGrams"] == 0  # unavailable placeholder, never the previous 500 g
    assert facts["measurementState"] == "RUNNING" and facts["measurementSampleCount"] == 1
    raw = scale_frame(600)
    assert not lib.McuWeightRun_FinishOwnedAttempt(reader, 1, attempt, 450, 800, raw, len(raw))
    # Test boundary supplies an exclusively owned NEW request; this is not
    # proof that flushing a real unnumbered RS485 channel establishes ownership.
    assert lib.McuWeightRun_StartOwnedAttempt(reader, 800) == 3
    lib.RuntimeClock_Advance(20)
    assert lib.McuWeightRun_FinishOwnedAttempt(reader, 1, 3, 820, 820, raw, len(raw))
    assert lib.McuWorkPreparation_Poll(owner, endpoint, 820)
    recovered = exchange(runtime, "QUERY_DEVICE_FACTS", query | {"queryId": 3}, now=820)[0][1]
    assert recovered["scaleReadStatus"] == "VALID" and recovered["scaleWeightGrams"] == 600
    assert recovered["scaleAttemptSequence"] == 3 and recovered["scaleCapturedUptimeMs"] == 820
    assert recovered["measurementSampleCount"] == 2


@pytest.mark.parametrize("kind,expected", [("crc", "CRC_ERROR"), ("shape", "PROTOCOL_ERROR"),
    ("range", "RANGE_ERROR"), ("null", "PROTOCOL_ERROR"), ("oversize", "PROTOCOL_ERROR")])
def test_bad_owned_read_replaces_current_value_without_adding_a_measurement_sample(runtime, kind, expected):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    assert exchange(runtime, "START_DELIVERY_SESSION", start_values())[0][1]["outcome"] == "ACCEPTED"
    assert take_samples(runtime, [500]) == 20
    reader = lib.TestPreparation_Weight(owner)
    assert lib.McuWeightRun_StartOwnedAttempt(reader, 250) == 2
    raw = scale_frame(2147483647 if kind == "range" else 500)
    if kind == "crc":
        raw = raw[:-1] + bytes([raw[-1] ^ 1])
    elif kind == "shape":
        raw = raw[:-1]
    elif kind == "oversize":
        raw = raw + bytes(256)
    elif kind == "null":
        raw = None
    lib.RuntimeClock_Advance(580)
    # Frame really completed at 270; processing at 600 must not refresh it.
    assert lib.McuWeightRun_FinishOwnedAttempt(reader, 1, 2, 270, 600, raw, len(raw) if raw else 9)
    assert lib.McuWorkPreparation_Poll(owner, endpoint, 600)
    query = {"queryId": 2, "targetMcuBootId": 42, "portNo": 1}
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", query, now=600)[0][1]
    assert facts["scaleReadStatus"] == expected and facts["scaleWeightGrams"] == 0
    assert facts["scaleCapturedUptimeMs"] == 270 and facts["scaleAttemptSequence"] == 2
    assert facts["measurementState"] == "RUNNING" and facts["measurementSampleCount"] == 1
    assert lib.McuWorkPreparation_Poll(owner, endpoint, 600)  # exact publication retry is harmless
    repeated = exchange(runtime, "QUERY_DEVICE_FACTS", query, now=600)[0][1]
    assert repeated == facts


def test_first_measurement_is_frozen_under_original_scope_and_saved_without_opening(runtime, tmp_path):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    start = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", start)[0][1]["outcome"] == "ACCEPTED"
    writes = lib.TestFacts_Writes()
    now = take_samples(runtime, [450, 500, 550, 510, 490])
    original = original_scope(start)
    scope = original | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY", "stepSequence": 1,
                        "configVersion": start["configVersion"]}
    replies = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert [name for name, _ in replies] == ["PROCESS_EVENT_QUERY_REPLY", "WORK_PREOPEN_WEIGHT_READY"]
    assert replies[0][1]["status"] == "HELD"
    event = replies[1][1]
    assert event["reportedWeightGrams"] == 500 and event["measurementKind"] == "STABLE_MEAN"
    assert event["sampleSpanGrams"] == 100 and event["measurementElapsedMs"] == 1020
    assert event["mcuCommandUid"] == start["mcuCommandUid"] and event["sessionUid"] == start["sessionUid"]
    assert exchange(runtime, "QUERY_WORK", original, now=now)[0][1]["phase"] == "DELIVERY_WAIT_FIRST_OPEN_AUTH"
    saved_scope, raw = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:], uart.encode_payload(replies[1][0], event)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(saved_scope, replies[1][0], raw)
        assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        assert lib.McuWorkPreparation_Poll(owner, endpoint, now)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[0][1]["status"] == "RELEASED"
        assert store.get_native_process_receipt(saved_scope)["payload"] == raw
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
    assert exchange(runtime, "QUERY_WORK", original, now=now)[0][1]["status"] == "RUNNING"
    assert lib.TestFacts_Writes() == writes  # data custody never substitutes for opening authorization


def test_clean_start_uses_the_same_real_measurement_chain_but_keeps_clean_identity(runtime):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    start = start_values("START_CLEAN_OPERATION")
    assert exchange(runtime, "START_CLEAN_OPERATION", start)[0][1]["outcome"] == "ACCEPTED"
    writes = lib.TestFacts_Writes()
    original = original_scope(start, clean=True)
    assert exchange(runtime, "QUERY_WORK", original)[0][1]["phase"] == "CLEAN_PREUNLOCK_MEASURING"
    now = take_samples(runtime, [1000] * 5)
    scope = original | {"eventMessageType": "WORK_PREUNLOCK_WEIGHT_READY", "stepSequence": 0,
                        "configVersion": start["configVersion"]}
    reply, event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert reply[1]["status"] == "HELD" and event[0] == "WORK_PREUNLOCK_WEIGHT_READY"
    assert event[1]["operationUid"] == start["operationUid"] and event[1]["reportedWeightGrams"] == 1000
    assert exchange(runtime, "QUERY_WORK", original, now=now)[0][1]["phase"] == "CLEAN_WAIT_FIRST_UNLOCK"
    assert lib.McuWorkPreparation_Poll(owner, endpoint, now)
    assert lib.TestFacts_Writes() == writes


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("mode", ["median", "unavailable"])
def test_five_second_terminal_evidence_keeps_median_separate_from_real_missing_weight(runtime, clean, mode):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
    start = start_values(name)
    assert exchange(runtime, name, start)[0][1]["outcome"] == "ACCEPTED"
    previous = take_samples(runtime, [-1000, 1000] * 10) if mode == "median" else 0
    lib.RuntimeClock_Advance(5000 - previous)
    assert lib.McuWorkPreparation_Poll(owner, endpoint, 5000)
    original = original_scope(start, clean=clean)
    event_name = "WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY"
    scope = original | {"eventMessageType": event_name, "stepSequence": 0 if clean else 1,
                        "configVersion": start["configVersion"]}
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=5000)[1][1]
    assert event["measurementKind"] == ("TIMEOUT_MEDIAN" if mode == "median" else "UNAVAILABLE")
    assert event["measurementElapsedMs"] == 5000 and event["reportedWeightGrams"] == 0
    assert event["sampleCount"] == (20 if mode == "median" else 0)
    assert event["faultCode"] == ("NONE" if mode == "median" else "WEIGHT_TIMEOUT")
    current = exchange(runtime, "QUERY_DEVICE_FACTS", {"queryId": 2, "targetMcuBootId": 42, "portNo": 1}, now=5000)[0][1]
    assert current["scaleReadStatus"] == ("VALID" if mode == "median" else "NOT_OBSERVED")
    assert current["scaleCapturedUptimeMs"] == (4770 if mode == "median" else 0)
    assert current["scaleWeightGrams"] == (1000 if mode == "median" else 0)
    if mode == "unavailable":
        assert exchange(runtime, "QUERY_WORK", original, now=5000)[0][1]["phase"] == (
            "CLEAN_FINALIZING" if clean else "DELIVERY_FINALIZING")
    # Repeated Poll cannot allocate a different identity or pretend to remeasure.
    assert lib.McuWorkPreparation_Poll(owner, endpoint, 5000)
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=5000)[1][1] == event


def test_complete_config_without_actual_applied_facts_cannot_start_work(runtime):
    configured(runtime)
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", {"queryId": 1, "targetMcuBootId": 42, "portNo": 1})[0][1]
    assert facts["appliedConfigVersion"] == 0
    values = start_values()
    decision = exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]
    assert decision["outcome"] == "REJECTED" and decision["errorCode"] == "STATE_CONFLICT"
    assert exchange(runtime, "QUERY_WORK", original_scope(values))[0][1]["status"] == "NOT_FOUND"


@pytest.mark.parametrize("clean", [False, True])
def test_original_start_bytes_and_window_origin_survive_duplicate_and_pi_reconnect(runtime, clean):
    lib, _, owner, *_ = runtime
    out, message, accepted_at = c.create_string_buffer(242), c.c_uint8(), c.c_uint64()
    assert not lib.McuWorkPreparation_CopyStart(owner, out, len(out), c.byref(message), c.byref(accepted_at))
    configured(runtime, applied=True)
    name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
    start = start_values(name)
    assert exchange(runtime, name, start, now=100)[0][1]["outcome"] == "ACCEPTED"
    lib.RuntimeClock_Advance(100)
    now = take_samples(runtime, [500] * 5, start=100)
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 2}, now=now)[0][1]["mcuBootId"] == 42
    assert exchange(runtime, "BIND_BOOT", {"probeId": 2, "proposedMcuBootId": 42}, now=now)[0][1]["status"] == "ALREADY_BOUND"
    assert exchange(runtime, name, start, now=now)[0][1]["outcome"] == "ACCEPTED"
    length = lib.McuWorkPreparation_CopyStart(owner, out, len(out), c.byref(message), c.byref(accepted_at))
    assert out.raw[:length] == uart.encode_payload(name, start)
    assert message.value == uart.MESSAGE_SPECS[name]["id"] and accepted_at.value == 100
    assert not lib.McuWorkPreparation_CopyStart(owner, out, length - 1, c.byref(message), c.byref(accepted_at))
    scope = original_scope(start, clean=clean) | {"eventMessageType": "WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY",
        "stepSequence": 0 if clean else 1, "configVersion": start["configVersion"]}
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    assert event["mcuEventSequence"] == 1 and event["sampleCount"] == 5


def test_reused_released_work_identity_is_rejected_before_acceptance_or_measurement(runtime):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    values = start_values()
    previous = original_scope(values) | {"commandSequence": 4,
        "mcuCommandUid": "12121212-1212-4212-8212-121212121212"}
    work, identity = lib.TestPreparation_Work(endpoint), uart.encode_payload("QUERY_WORK", previous)[8:]
    # Set up a genuinely completed/released predecessor through the public C
    # work/result APIs; the preparation slice does not yet finalize a business.
    assert lib.McuWorkState_BeginAccepted(work, identity, len(identity), 17)
    result = result_payload(originCommandUid=previous["mcuCommandUid"], originCommandSequence=4,
                            workUid=values["sessionUid"], portNo=1)
    assert lib.McuWorkState_Complete(work, result, len(result))
    assert exchange(runtime, "RESULT_SAVED", payload=result[:60])[0][1]["status"] == "RELEASED"
    assert exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]["outcome"] == "REJECTED"
    assert exchange(runtime, "QUERY_WORK", previous)[0][1]["status"] == "RESULT_RELEASED"
    assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(owner), 0)


@pytest.mark.parametrize("change", ["version", "content", "subset", "staging"])
def test_published_application_must_match_complete_active_candidate(runtime, change):
    lib, endpoint, _, *_ = runtime
    candidate = configured(runtime)
    data = inputs()
    assert lib.McuDeviceFacts_PublishConfiguration(lib.TestPreparation_Facts(endpoint),
        9 if change == "version" else data["config_version"],
        bytes.fromhex("cc" * 32 if change == "content" else data["content_sha256"]),
        bytes.fromhex("dd" * 32 if change == "subset" else candidate.mcu_payload_sha256),
        int(change == "staging"))
    assert exchange(runtime, "START_DELIVERY_SESSION", start_values())[0][1]["errorCode"] == "STATE_CONFLICT"


def test_new_partial_configuration_blocks_start_even_while_old_applied_facts_remain(runtime):
    from hardware.tests.test_mcu_configuration_runtime import versioned_candidate
    configured(runtime, applied=True)
    new = versioned_candidate(9)
    name, payload = new.encode_part(1, application_uid="99999999-9999-4999-8999-999999999999",
        mcu_command_uid="99999999-9999-4999-8999-999999999998", target_mcu_boot_id=42, command_sequence=6)
    assert exchange(runtime, name, payload=payload)[0][1]["outcome"] == "ACCEPTED"
    assert exchange(runtime, "START_DELIVERY_SESSION", start_values(commandSequence=7))[0][1]["errorCode"] == "BUSY"


@pytest.mark.parametrize("error,code", [(6, "EXPIRED"), (11, "SAFETY_BLOCKED"), (65535, "INTERNAL_FAULT")])
def test_external_prerequisite_refusal_is_cached_and_not_retried_as_a_new_action(runtime, error, code):
    lib, _, owner, _, prerequisites, *_ = runtime
    configured(runtime, applied=True)
    prerequisites["error"] = error
    values = start_values()
    first = exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]
    assert first["outcome"] == "REJECTED" and first["errorCode"] == code
    prerequisites["error"] = 0
    assert exchange(runtime, "START_DELIVERY_SESSION", values)[0][1] == first
    assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(owner), 0)


@pytest.mark.parametrize("name", ["AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR"])
def test_unimplemented_mechanical_command_is_not_acknowledged_or_executed(runtime, name):
    lib, _, _, *_ = runtime
    configured(runtime, applied=True)
    start = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", start)[0][1]["outcome"] == "ACCEPTED"
    values = minimal_command(uart.REGISTRY, uart.MESSAGE_SPECS[name] | {"name": name})
    values.update(targetMcuBootId=42, commandSequence=7, mcuCommandUid="77777777-7777-4777-8777-777777777777")
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    writes = lib.TestFacts_Writes()
    assert exchange(runtime, name, values) == []
    query = {"queryId": 1, **{key: values[key] for key in
        ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}}
    reply = exchange(runtime, "QUERY_COMMAND", query)[0][1]
    assert reply["outcome"] == "NOT_SEEN" and reply["highestCommandSequence"] == 6
    assert lib.TestFacts_Writes() == writes


def test_event_sequence_is_boot_global_not_local_measurement_or_transport_sequence(runtime):
    lib, endpoint, _, *_ = runtime
    assert not lib.McuControlEndpoint_ReserveEventSequence(endpoint)  # boot not assigned
    configured(runtime, applied=True)
    assert lib.McuControlEndpoint_ReserveEventSequence(endpoint) == 1  # another future event producer
    values = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]["outcome"] == "ACCEPTED"
    now = take_samples(runtime, [500] * 5)
    scope = original_scope(values) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY", "stepSequence": 1, "configVersion": 8}
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    assert event["mcuEventSequence"] == 2
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 2}, now=now)[0][1]["mcuBootId"] == 42
    assert exchange(runtime, "BIND_BOOT", {"probeId": 2, "proposedMcuBootId": 42}, now=now)[0][1]["status"] == "ALREADY_BOUND"
    assert lib.McuControlEndpoint_ReserveEventSequence(endpoint) == 3


def test_actual_preopen_measurement_cannot_spend_reserved_actuator_numbers(runtime):
    from hardware.tests.test_mcu_actuator_event_credits import publish
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    lib.TestPreparation_SetEventSequence(endpoint, 2**32 - 4)
    token = Reservation()
    assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 3, c.byref(token))
    values = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]["outcome"] == "ACCEPTED"
    now = take_samples(runtime, [500] * 5, publish=False)
    scope = original_scope(values) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY", "stepSequence": 1, "configVersion": 8}
    assert not lib.McuWorkPreparation_Poll(owner, endpoint, now)
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[0][1]["status"] == "NOT_FOUND"
    # Release only an entirely unstarted reservation; same measured data can publish.
    assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(token))
    assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 2, c.byref(token))
    assert publish(runtime, token, 0) == 2**32 - 3
    assert lib.McuWorkPreparation_Poll(owner, endpoint, now)
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    assert event["mcuEventSequence"] == 2**32 - 2
    assert not lib.McuControlEndpoint_ReserveEventSequence(endpoint)
    assert publish(runtime, token, 1, lockPowerState="DEENERGIZED") == 2**32 - 1


def test_real_mcu_reset_forgets_work_and_applied_config_and_old_start_is_boot_mismatch(runtime):
    lib, endpoint, owner, _, _, sink, guard = runtime
    configured(runtime, applied=True)
    original = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", original)[0][1]["outcome"] == "ACCEPTED"
    take_samples(runtime, [500] * 5)
    # Explicit actual-MCU-reset boundary, never used for a Pi reconnect.
    lib.TestFacts_InitHardware()
    lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
    assert lib.McuWorkPreparation_Attach(owner, endpoint, 2, guard, None)
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 2})[0][1]["mcuBootId"] == 0
    assert exchange(runtime, "BIND_BOOT", {"probeId": 2, "proposedMcuBootId": 43})[0][1]["status"] == "BOUND"
    assert exchange(runtime, "START_DELIVERY_SESSION", original)[0][1]["outcome"] == "BOOT_MISMATCH"
    assert exchange(runtime, "QUERY_WORK", original_scope(original))[0][1]["status"] == "BOOT_MISMATCH"
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", {"queryId": 1, "targetMcuBootId": 43, "portNo": 1})[0][1]
    assert facts["appliedConfigVersion"] == 0
    assert not lib.McuWorkPreparation_Poll(owner, endpoint, 0)


def test_replacing_or_late_attachment_does_not_clear_original(runtime):
    lib, endpoint, owner, _, _, _, guard = runtime
    configured(runtime, applied=True)
    original = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", original)[0][1]["outcome"] == "ACCEPTED"
    assert not lib.McuWorkPreparation_Attach(owner, endpoint, 2, guard, None)
    assert exchange(runtime, "QUERY_WORK", original_scope(original))[0][1]["phase"] == "DELIVERY_FIRST_PREOPEN_MEASURING"


@pytest.mark.parametrize("damage", ["crc", "content_digest", "short_payload"])
def test_invalid_start_never_reaches_owner_or_advances_command_high_water(runtime, damage):
    lib, endpoint, owner, replies, *_ = runtime
    configured(runtime, applied=True)
    values = start_values()
    payload = uart.encode_payload("START_DELIVERY_SESSION", values)
    if damage == "content_digest":
        payload = payload[:16] + bytes([payload[16] ^ 1]) + payload[17:]
    elif damage == "short_payload":
        payload = payload[:-1]
    frame = uart.encode_frame("START_DELIVERY_SESSION", 1, payload)
    if damage == "crc":
        frame = frame[:-1] + bytes([frame[-1] ^ 1])
    replies.clear()
    lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), 0)
    assert replies == []
    query = {"queryId": 1, **{key: values[key] for key in
        ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}}
    reply = exchange(runtime, "QUERY_COMMAND", query)[0][1]
    assert reply["outcome"] == "NOT_SEEN" and reply["highestCommandSequence"] == 5
    assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(owner), 0)
    assert exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]["outcome"] == "ACCEPTED"


@pytest.mark.parametrize("mode,observed", [("stable", 1020), ("unavailable", 5000)])
def test_delayed_publication_keeps_original_measurement_time_instead_of_refreshing_health(runtime, mode, observed):
    lib, endpoint, owner, *_ = runtime
    configured(runtime, applied=True)
    values = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", values)[0][1]["outcome"] == "ACCEPTED"
    previous = take_samples(runtime, [500] * 5, publish=False) if mode == "stable" else 0
    lib.RuntimeClock_Advance(60000 - previous)
    assert lib.McuWorkPreparation_Poll(owner, endpoint, 60000)
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", {"queryId": 1, "targetMcuBootId": 42, "portNo": 1}, now=60000)[0][1]
    assert facts["measurementObservedUptimeMs"] == observed
    scope = original_scope(values) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY", "stepSequence": 1, "configVersion": 8}
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=60000)[1][1]
    assert event["uptimeMs"] == observed
    assert event["measurementKind"] == ("STABLE_MEAN" if mode == "stable" else "UNAVAILABLE")
