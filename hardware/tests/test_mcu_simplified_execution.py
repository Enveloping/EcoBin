"""Real C START -> autonomous local work -> durable final result, no process ACKs."""
import ctypes as c
import subprocess
import uuid
from pathlib import Path

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from mcu_result_handoff import McuResultHandoff
from hardware.tests.test_mcu_work_preparation import (
    ROOT, SINK, GUARD, configured, exchange, original_scope, start_values,
    take_samples, runtime,
)
from hardware.tests.test_mcu_actuator_event_journal import Reservation
from hardware.tests.test_mcu_opening_gate import grant
from hardware.tests.test_native_configuration import inputs


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    output = tmp_path_factory.mktemp("simple-execution") / "execution.dll"
    signatures = {
        "McuControlEndpoint_Init": (None, [c.c_void_p, c.c_uint8, SINK, c.c_void_p]),
        "McuControlEndpoint_Feed": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64]),
        "McuWorkPreparation_Attach": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint8, GUARD, c.c_void_p]),
        "McuWorkPreparation_Poll": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint64]),
        "McuWorkPreparation_AttachFullness": (c.c_uint8, [c.c_void_p, c.c_void_p]),
        "TestUltrasonic_Init": (None, []),
        "McuDeliveryExecution_Attach": (c.c_uint8, [c.c_void_p] * 3),
        "McuCleanExecution_Attach": (c.c_uint8, [c.c_void_p] * 3),
        "McuDeliveryExecution_Select": (c.c_uint8, [c.c_void_p] * 3 + [c.c_uint8, c.c_uint64]),
        "McuDeliveryExecution_RequestSelection": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint8, c.c_uint64]),
        "McuDeliveryExecution_CloseCurrent": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint64]),
        "McuCleanExecution_Request": (c.c_uint8, [c.c_void_p] * 3 + [c.c_uint8, c.c_uint16, c.c_uint64]),
        "McuControlEndpoint_ReserveActuatorEvents": (c.c_uint8, [c.c_void_p, c.c_uint8, c.POINTER(Reservation)]),
        "TestFacts_InitHardware": (None, []), "TestFacts_Pinch": (None, [c.c_uint8]),
        "TestFacts_Writes": (c.c_uint32, []), "ActuatorRuntime_Tick": (None, []),
        "ActuatorRuntime_SetDoorTarget": (c.c_uint8, [c.c_uint8]),
        "ActuatorRuntime_StopForUpdate": (None, []), "RuntimeClock_Advance": (None, [c.c_uint32]),
        "TestPreparation_Weight": (c.c_void_p, [c.c_void_p]),
        "TestPreparation_Facts": (c.c_void_p, [c.c_void_p]),
        "McuDeviceFacts_PublishFullness": (c.c_uint8, [c.c_void_p, c.c_uint8,
            c.c_uint8, c.c_uint64, c.c_uint8, c.c_uint16]),
        "McuDeviceFacts_PublishConfiguration": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_void_p, c.c_void_p, c.c_uint8]),
        "McuWeightRun_StartOwnedAttempt": (c.c_uint32, [c.c_void_p, c.c_uint64]),
        "McuWeightRun_FinishOwnedAttempt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint32,
            c.c_uint64, c.c_uint64, c.c_void_p, c.c_size_t]),
        "McuWeightRun_Interrupt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint64]),
        "TestSimple_DeliveryMeasurement": (c.c_void_p, [c.c_void_p]),
        "TestSimple_CleanSequence": (c.c_uint16, [c.c_void_p]),
        "TestSimple_EnableApply": (c.c_uint8, [c.c_void_p, c.c_void_p]),
        "TestSimple_InstallPreemptingHardware": (None, []),
        "TestSimple_Preempt": (None, [c.c_uint32, c.c_uint32]),
        "TestSimple_Now": (c.c_uint64, []),
    }
    sources = ("mcu_control_endpoint", "mcu_actuator_event_journal", "mcu_work_preparation", "mcu_device_entry_url", "mcu_opening_gate",
        "mcu_delivery_execution", "mcu_clean_execution", "mcu_configuration", "mcu_config_collection",
        "mcu_session", "mcu_work_state", "mcu_result_slot", "mcu_result_builder", "mcu_process_measurement",
        "mcu_process_event_slot", "mcu_device_facts", "mcu_weight_run", "weight_measurement", "scale_reader",
        "actuator_runtime", "door_control", "clean_lock", "runtime_clock", "mcu_fullness_run",
        "ultrasonic_reader", "mcu_environment_ultrasonic")
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-I", str(user),
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1", *[str(user / f"{name}.c") for name in sources],
        str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        *[str(ROOT / f"hardware_mcu/tests/{name}.c") for name in
          ("device_facts_host", "work_preparation_host", "ultrasonic_host", "simplified_execution_host")],
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(output)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = c.CDLL(str(output))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    return lib


def setup(runtime, *, clean=False, initial=True):
    lib, endpoint, preparation, *_ = runtime
    delivery, cleanup = (c.c_uint64 * 64)(), (c.c_uint64 * 64)()
    runtime[4]["owners"] = (delivery, cleanup)  # Keep C callback contexts alive.
    assert lib.McuDeliveryExecution_Attach(delivery, preparation, endpoint)
    assert lib.McuCleanExecution_Attach(cleanup, preparation, endpoint)
    assert lib.TestSimple_EnableApply(preparation, endpoint)
    assert lib.ActuatorRuntime_SetDoorTarget(1)  # Actual boot CLOSE control.
    configured(runtime)  # No test-side PublishConfiguration: COMMIT applies it.
    name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
    start = start_values(name)
    assert exchange(runtime, name, start)[0][1]["outcome"] == "ACCEPTED"
    now = take_samples(runtime, [500] * 5) if initial else 0
    if initial:
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return delivery, cleanup, start, now


def tick(runtime, now, duration):
    lib, endpoint, preparation, *_ = runtime
    lib.RuntimeClock_Advance(duration)
    lib.ActuatorRuntime_Tick()
    now += duration
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def state(runtime, start, now, *, clean=False):
    return exchange(runtime, "QUERY_WORK", original_scope(start, clean=clean), now=now)[0][1]


def final(runtime, start, now, *, clean=False):
    reply = state(runtime, start, now, clean=clean)
    assert reply["status"] == "RESULT_HELD", reply
    identity = dict(mcuBootId=42, resultSequence=reply["resultSequence"],
        resultDigestSha256=reply["resultDigestSha256"], workUid=start["operationUid" if clean else "sessionUid"])
    responses = exchange(runtime, "QUERY_RESULT", {"queryId": 9, **identity}, now=now)
    assert responses[0][1]["status"] == "HELD"
    assert responses[1][0] == "WORK_RESULT"
    return responses[1][1]


def closed_measurement(runtime, start, now):
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    assert state(runtime, start, now)["phase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    assert state(runtime, start, now)["phase"] == "DELIVERY_POSTCLOSE_MEASURING"
    return now


def select(runtime, delivery, now, selection="END", uid=None):
    lib, endpoint, *_ = runtime
    uid = uid or c.string_at(lib.TestSimple_DeliveryMeasurement(delivery), 16)
    return lib.McuDeliveryExecution_Select(delivery, endpoint, uid,
        uart.REGISTRY["enums"]["DeliverySelection"]["values"][selection], now)


def request(runtime, cleanup, start, now, message, *, sequence=None):
    lib, endpoint, *_ = runtime
    sequence = lib.TestSimple_CleanSequence(cleanup) if sequence is None else sequence
    return lib.McuCleanExecution_Request(cleanup, endpoint, uuid.UUID(start["operationUid"]).bytes,
        uart.MESSAGE_SPECS[message]["id"], sequence, now)


def test_delivery_runs_and_commits_full_result_with_no_process_or_action_ack(runtime, tmp_path):
    delivery, cleanup, start, now = setup(runtime)
    # Saturate the old diagnostic action journal before motion: no reservation dependency.
    reservation = Reservation()
    assert runtime[0].McuControlEndpoint_ReserveActuatorEvents(runtime[1], 8, c.byref(reservation))
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1200] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    assert result["initialWeightGrams"] == 500 and result["finalWeightGrams"] == 1200
    assert result["finishReason"] == "DELIVERY_END" and result["deliveryRoundCount"] == 1
    raw = uart.encode_payload("WORK_RESULT", result)
    path = tmp_path / "edge.db"
    store = EdgeStore(str(path))
    store.initialize()
    sent = []
    try:
        identity = uart.decode_payload("RESULT_SAVED", raw[:60])
        client = McuResultHandoff(store, lambda frame: sent.append(frame) or len(frame), identity)
        assert client.accept_frame(uart.encode_frame("WORK_RESULT", 1, raw), now)
        assert store.get_native_mcu_result(42, result["resultSequence"])["payload"] == raw
        assert len(store.list_native_result_report_tasks()) == 1
        assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "RELEASED"
        assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "ALREADY_RELEASED"
    finally:
        store.close()
    reopened = EdgeStore(str(path))
    reopened.initialize()
    try:
        assert reopened.get_native_mcu_result(42, result["resultSequence"])["payload"] == raw
        restarted = McuResultHandoff(reopened, lambda frame: len(frame), identity)
        assert restarted.accept_frame(uart.encode_frame("WORK_RESULT", 2, raw), now)
        assert len(reopened.list_native_result_report_tasks()) == 1
    finally:
        reopened.close()
    assert cleanup


def test_continuous_delivery_rejects_stale_button_and_keeps_original_first_weight(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1000] * 5, start=now, measurement=2)
    old_uid = c.string_at(runtime[0].TestSimple_DeliveryMeasurement(delivery), 16)
    assert select(runtime, delivery, now, "CONTINUE")
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1500] * 5, start=now, measurement=3)
    assert not select(runtime, delivery, now, uid=old_uid)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    assert (result["deliveryRoundCount"], result["initialWeightGrams"], result["finalWeightGrams"]) == (2, 500, 1500)


def test_delivery_window_expiry_finishes_locally_without_button_or_pi_ack(runtime):
    _, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [700] * 5, start=now, measurement=2)
    now = tick(runtime, now, start["continueDeliveryWaitMs"])
    assert final(runtime, start, now)["finishReason"] == "DELIVERY_WINDOW_EXPIRED"


@pytest.mark.parametrize("clean", [False, True])
def test_initial_weight_timeout_fails_without_opening_or_diagnostic_ack(runtime, clean):
    _, _, start, now = setup(runtime, clean=clean, initial=False)
    now = tick(runtime, now, 5000)
    now = tick(runtime, now, 0)
    result = final(runtime, start, now, clean=clean)
    assert result["finishReason"] == "FAILED"
    assert result["initialKind"] == "UNAVAILABLE" and result["finalKind"] == "NOT_TAKEN"
    assert result["deliveryRoundCount"] == result["cleanActionSequence"] == 0


def test_final_weight_timeout_fails_at_five_seconds_with_actual_close_control(runtime):
    _, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = tick(runtime, now, 5000)
    result = final(runtime, start, now)
    assert result["finishReason"] == "FAILED" and result["finalKind"] == "UNAVAILABLE"
    assert result["finalElapsedMs"] == 5000 and result["finalFaultCode"] == "WEIGHT_TIMEOUT"
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", dict(queryId=10, targetMcuBootId=42, portNo=1), now=now)[0][1]
    assert facts["lastDeliveryDoorCommand"] == "CLOSE"


def test_control_interruption_at_weight_deadline_does_not_look_like_pure_weight_failure(runtime):
    _, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    runtime[0].RuntimeClock_Advance(5000)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now += 5000
    runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
    assert final(runtime, start, now)["finishReason"] == "CANCELLED"


def test_pinch_pauses_close_but_does_not_block_valid_delivery(runtime):
    delivery, _, start, now = setup(runtime)
    runtime[0].TestFacts_Pinch(1)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [700] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now)
    assert final(runtime, start, now)["finishReason"] == "DELIVERY_END"
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", dict(queryId=10, targetMcuBootId=42, portNo=1), now=now)[0][1]
    assert facts["pinchPaused"] and facts["lastDeliveryDoorCommand"] == "CLOSE"
    runtime[0].TestFacts_Pinch(0)
    runtime[0].ActuatorRuntime_Tick()
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", dict(queryId=11, targetMcuBootId=42, portNo=1), now=now)[0][1]
    assert facts["pb7Output"] and not facts["pinchPaused"]


@pytest.mark.parametrize("timeout", [False, True])
def test_clean_reopen_and_single_real_finish_button_complete_without_pi_permission(runtime, timeout):
    _, cleanup, start, now = setup(runtime, clean=True)
    now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    assert request(runtime, cleanup, start, now, "CLEAN_UNLOCK_REQUESTED")
    assert not request(runtime, cleanup, start, now, "CLEAN_UNLOCK_REQUESTED", sequence=0)
    now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    assert request(runtime, cleanup, start, now, "CLEAN_FINISH_REQUESTED")
    assert not request(runtime, cleanup, start, now, "CLEAN_FINISH_REQUESTED")
    now = tick(runtime, now, 5000) if timeout else take_samples(runtime, [100] * 5, start=now, measurement=2)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == ("FAILED" if timeout else "CLEAN_CONFIRMED")
    assert result["physicalCloseConfirmed"] and result["cleanActionSequence"] == 2
    assert result["initialWeightGrams"] == 500
    assert result["finalKind"] == ("UNAVAILABLE" if timeout else "STABLE_MEAN")


def test_clean_expiry_cancels_without_fabricating_human_confirmation(runtime):
    _, _, start, now = setup(runtime, clean=True)
    now = tick(runtime, now, start["operationWindowMs"])
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CANCELLED" and not result["physicalCloseConfirmed"]
    assert result["finalKind"] == "NOT_TAKEN"


def test_early_local_close_keeps_dead_time_and_duplicate_does_not_extend_it(runtime):
    delivery, _, start, now = setup(runtime)
    lib, endpoint, *_ = runtime
    assert not lib.McuDeliveryExecution_CloseCurrent(delivery, endpoint, now)  # not opened yet
    now = tick(runtime, now, 100)
    assert lib.McuDeliveryExecution_CloseCurrent(delivery, endpoint, now)
    now = tick(runtime, now, 50)
    assert lib.McuDeliveryExecution_CloseCurrent(delivery, endpoint, now)
    now = tick(runtime, now, 49)
    query = dict(queryId=90, targetMcuBootId=42, portNo=1)
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", query, now=now)[0][1]
    assert not facts["pb6Output"] and not facts["pb7Output"]
    now = tick(runtime, now, 1)
    facts = exchange(runtime, "QUERY_DEVICE_FACTS", query, now=now)[0][1]
    assert facts["pb7Output"] and facts["lastDeliveryDoorCommand"] == "CLOSE"
    assert state(runtime, start, now)["phase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    assert not lib.McuDeliveryExecution_CloseCurrent(delivery, endpoint, now)  # released cycle


def test_end_clicked_immediately_after_local_close_is_retained_until_postclose_weight(runtime):
    delivery, _, start, now = setup(runtime)
    lib, endpoint, preparation, *_ = runtime
    end = uart.REGISTRY["enums"]["DeliverySelection"]["values"]["END"]

    assert not lib.McuDeliveryExecution_RequestSelection(delivery, endpoint, end, now)
    now = tick(runtime, now, 100)
    assert lib.McuDeliveryExecution_CloseCurrent(delivery, endpoint, now)
    assert lib.McuDeliveryExecution_RequestSelection(delivery, endpoint, end, now)
    assert not lib.McuDeliveryExecution_RequestSelection(delivery, endpoint, end, now)

    now = tick(runtime, now, 100)
    assert state(runtime, start, now)["phase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [800] * 5, start=now, measurement=2)
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)

    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END"
    assert result["finalWeightGrams"] == 800
    assert result["deliveryRoundCount"] == 1


def test_duplicate_start_does_not_reopen_and_old_second_authorization_is_explicitly_rejected(runtime):
    delivery, _, start, now = setup(runtime)
    now = tick(runtime, now, 100)
    assert exchange(runtime, "START_DELIVERY_SESSION", start, now=now)[0][1]["outcome"] == "ACCEPTED"
    scope = original_scope(start) | dict(eventMessageType="WORK_PREOPEN_WEIGHT_READY", stepSequence=1,
        configVersion=start["configVersion"])
    first = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    message, command = grant(start, first)
    rejection = exchange(runtime, message, command, now=now)[0][1]
    assert rejection["outcome"] == "REJECTED" and rejection["errorCode"] == "UNSUPPORTED_MESSAGE"
    assert runtime[0].McuDeliveryExecution_CloseCurrent(delivery, runtime[1], now)
    now = tick(runtime, now, 100)
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [800] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    # The explicitly rejected later command consumed the one-command cache;
    # old START is still never re-executed, its original work remains queryable.
    assert exchange(runtime, "START_DELIVERY_SESSION", start, now=now)[0][1]["outcome"] == "OLD_DETAILS_UNAVAILABLE"
    assert final(runtime, start, now) == result


def test_next_business_after_final_ack_ignores_old_diagnostic_mailbox(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [900] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    next_start = start_values(commandSequence=7, mcuCommandUid=str(uuid.uuid4()), sessionUid=str(uuid.uuid4()))
    assert exchange(runtime, "START_DELIVERY_SESSION", next_start, now=now)[0][1]["errorCode"] == "BUSY"
    raw = uart.encode_payload("WORK_RESULT", result)
    assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "RELEASED"
    # BUSY was a cached command decision, so a genuinely new command is needed.
    next_start = start_values(commandSequence=8, mcuCommandUid=str(uuid.uuid4()), sessionUid=str(uuid.uuid4()))
    assert exchange(runtime, "START_DELIVERY_SESSION", next_start, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = tick(runtime, now, 250)  # Global scale cadence spans business boundaries.
    now = take_samples(runtime, [900] * 5, start=now, measurement=3)
    runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
    now = closed_measurement(runtime, next_start, now)
    now = take_samples(runtime, [1200] * 5, start=now, measurement=4)
    assert select(runtime, delivery, now)
    second = final(runtime, next_start, now)
    assert second["initialWeightGrams"] == 900 and second["deliveryRoundCount"] == 1
    assert second["resultSequence"] == result["resultSequence"] + 1


def test_final_weight_failure_then_new_business_does_not_rewrite_failed_result(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = tick(runtime, now, 5000)
    failed = final(runtime, start, now)
    assert failed["finishReason"] == "FAILED"
    raw = uart.encode_payload("WORK_RESULT", failed)
    assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "RELEASED"
    next_start = start_values(commandSequence=7, mcuCommandUid=str(uuid.uuid4()), sessionUid=str(uuid.uuid4()))
    assert exchange(runtime, "START_DELIVERY_SESSION", next_start, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = take_samples(runtime, [800] * 5, start=now, measurement=3)
    runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
    now = closed_measurement(runtime, next_start, now)
    now = take_samples(runtime, [900] * 5, start=now, measurement=4)
    assert select(runtime, delivery, now)
    new = final(runtime, next_start, now)
    assert new["workUid"] != failed["workUid"] and new["finishReason"] == "DELIVERY_END"
    assert uart.decode_payload("WORK_RESULT", raw)["finishReason"] == "FAILED"


def test_unstable_available_weight_uses_five_second_median_and_finishes_normally(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    began = now
    now = take_samples(runtime, [700, 1700] * 10, start=now, measurement=2)
    now = tick(runtime, now, began + 5000 - now)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END" and result["finalKind"] == "TIMEOUT_MEDIAN"
    assert result["finalElapsedMs"] == 5000 and result["finalSampleCount"] == 20


def test_unresponsive_optional_ultrasonic_source_cannot_delay_final_weight(runtime):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [700] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END"
    assert result["finalElapsedMs"] == 1020


@pytest.mark.parametrize("boundary", [2, 3, 4])
def test_timer_close_between_snapshot_and_cycle_read_never_cancels_delivery(runtime, boundary):
    lib, endpoint, preparation, *_ = runtime
    lib.TestSimple_InstallPreemptingHardware()
    delivery, _, start, now = setup(runtime)
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 90)  # Still in the 100 ms all-off reversing interval.
    lib.TestSimple_Preempt(boundary, 10)
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    # Exercise any still-pending critical-section hook, retaining its actual
    # time. The old pre-cycle snapshot failed boundary 3 with CANCELLED.
    for _ in range(3):
        lib.McuWorkPreparation_Poll(preparation, endpoint, lib.TestSimple_Now())
    now = lib.TestSimple_Now()
    assert state(runtime, start, now)["phase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    assert state(runtime, start, now)["phase"] == "DELIVERY_POSTCLOSE_MEASURING", (boundary, now, state(runtime, start, now))
    now = take_samples(runtime, [700] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now)
    assert final(runtime, start, now)["finishReason"] == "DELIVERY_END"
