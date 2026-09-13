"""Real native work -> owned weight/ultrasonic observations -> exact Pi custody."""
import pytest
import sqlite3
import uart2_protocol as uart
from edge_store import EdgeStore
from mcu_configuration import NativeMcuConfiguration
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope
from hardware.tests.test_mcu_delivery_postclose import closed_cycle, advance
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_device_facts import scale_frame
from hardware.tests.test_mcu_clean_intent import active_clean, request, save_intent
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_delivery_continue import choose
from hardware.tests.test_mcu_delivery_finalization import save_process


def begin_terminal_measurement(runtime, tmp_path, clean=False):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    if clean:
        execution, start, initial, _, now = active_clean(runtime, tmp_path)
        assert request(runtime, execution, start, "CLEAN_FINISH_REQUESTED", now)
        store = EdgeStore(str(tmp_path / "finish-intent.db"))
        store.initialize()
        try:
            save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
        finally:
            store.close()
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        scope = original_scope(start, clean=True) | {"eventMessageType": "CLEAN_FINAL_WEIGHT_READY",
            "stepSequence": 1, "configVersion": start["configVersion"]}
    else:
        execution, start, initial, scope, now = closed_cycle(runtime, tmp_path)
        now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    assert not lib.TestUltrasonic_Trigger()  # original settle delay, not pre-open sampling
    return execution, start, initial, scope, now


def weigh(runtime, began, samples=(1000,) * 5, measurement=2):
    lib, endpoint, preparation, *_ = runtime
    now = began
    weight = lib.TestPreparation_Weight(preparation)
    for index, value in enumerate(samples):
        when = began + index * 250
        lib.TestUltrasonic_Advance((when - now) * 1000)
        now = when
        attempt = lib.McuWeightRun_StartOwnedAttempt(weight, now)
        assert attempt
        lib.TestUltrasonic_Advance(20000)
        now += 20
        raw = scale_frame(value)
        assert lib.McuWeightRun_FinishOwnedAttempt(weight, measurement, attempt, now, now, raw, len(raw))
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def collect_fullness(runtime, began, now, distances=(1000,) * 5, *, boot_id=42):
    lib, endpoint, preparation, *_ = runtime
    due = began + inputs()["ports"][0]["fullnessSettleWaitMs"]
    lib.TestUltrasonic_Advance((due - now) * 1000)
    now = due
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    for index, distance in enumerate(distances):
        assert lib.TestUltrasonic_Trigger()
        lib.TestUltrasonic_Advance(15)
        if distance is None:
            lib.TestUltrasonic_Advance(inputs()["ports"][0]["fullnessEchoTimeoutUs"])
        else:
            lib.TestUltrasonic_Edge(1)
            lib.TestUltrasonic_Advance((distance * 58 + 9) // 10)
            lib.TestUltrasonic_Edge(0)
        # Read the actual monotonic time rather than truncating each pulse delta.
        now = exchange(runtime, "QUERY_DEVICE_FACTS", {"queryId": 99, "targetMcuBootId": boot_id,
            "portNo": 1}, now=now)[0][1]["capturedUptimeMs"]
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        if index < len(distances) - 1:
            lib.TestUltrasonic_Advance(70000)
            now += 70
            lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def test_real_delivery_retains_one_combined_record_without_refresh_or_resampling(runtime, tmp_path):
    lib, endpoint, preparation, *_ = runtime
    execution, start, initial, scope, began = begin_terminal_measurement(runtime, tmp_path)
    weight = lib.TestPreparation_Weight(preparation)
    measured_at = weigh(runtime, began)
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=measured_at)[0][1]["status"] == "NOT_FOUND"
    now = collect_fullness(runtime, began, measured_at)
    response = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert [name for name, _ in response] == ["PROCESS_EVENT_QUERY_REPLY", "WORK_POSTCLOSE_WEIGHT_READY"]
    event = response[1][1]
    assert event["reportedWeightGrams"] == 1000 and event["uptimeMs"] == measured_at
    assert event["fullnessStartedUptimeMs"] == began
    assert event["fullnessCompletedUptimeMs"] > measured_at
    assert event["workFullnessStatus"] == "COMPLETE" and event["fullnessDistanceMm"] == 1000
    assert event["fullnessConfigContentSha256"] == start["configContentSha256"]
    raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", event)
    original = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    store = EdgeStore(str(tmp_path / "fullness-custody.db"))
    store.initialize()
    receipt = store.save_native_process_receipt(original, "WORK_POSTCLOSE_WEIGHT_READY", raw)
    store.close()
    lib.TestUltrasonic_Advance(10000000)
    now += 10000
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1] == event
    assert not lib.TestUltrasonic_Trigger()
    assert not lib.McuWeightRun_StartOwnedAttempt(weight, now)
    store = EdgeStore(str(tmp_path / "fullness-custody.db"))
    store.initialize()
    try:
        assert store.get_native_process_receipt(original)["payload"] == raw
        assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
    assert execution and initial  # Keep the attached native execution storage alive.


@pytest.mark.parametrize("clean,cause", [(False, "update"), (True, "update"),
    (False, "context"), (True, "context"), (True, "expiry")])
def test_work_interruption_after_weight_keeps_partial_group_without_clear_or_new_sampling(
        runtime, tmp_path, clean, cause):
    lib, endpoint, preparation, *_ = runtime
    execution, start, initial, scope, began = begin_terminal_measurement(runtime, tmp_path, clean)
    measured_at = weigh(runtime, began)
    now = collect_fullness(runtime, began, measured_at, [1000, None])
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[0][1]["status"] == "NOT_FOUND"
    if cause == "update":
        lib.ActuatorRuntime_StopForUpdate()
    elif cause == "context":
        lib.TestFacts_ReinitializeActuator()
    else:
        lib.TestUltrasonic_Advance(200000000)
        now += 200000
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    response = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert response[0][1]["status"] == "HELD"
    event = response[1][1]
    assert event["measurementKind"] == "STABLE_MEAN" and event["uptimeMs"] == measured_at
    assert event["reportedWeightGrams"] == 1000
    assert event["workFullnessStatus"] == "INTERRUPTED"
    assert event["fullnessStopReason"] == "CALLER_CANCELLED"
    assert event["fullnessCompletedSampleCount"] == 2 and event["fullnessValidSampleCount"] == 1
    assert event["workFullnessSensorValue"] == "NOT_OBSERVED" and event["workFullnessBasis"] == "NONE"
    assert not event["fullnessDistancePresent"] and event["fullnessDistanceMm"] == 0
    assert event["fullnessCompletedUptimeMs"] == now
    lib.TestUltrasonic_Advance(1000000)
    now += 1000
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1] == event
    assert not lib.TestUltrasonic_Trigger()
    assert execution and initial


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("distances,basis,valid,distance", [
    ([1000, 1100, 1200, 900, 800], "MEASURED_MEDIAN", 5, 1000),
    ([None] * 5, "NO_ECHO_CLEAR_FALLBACK", 0, 0),
    ([1000, None, None, None, None], "INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK", 1, 0),
])
def test_business_custody_preserves_actual_group_basis_without_business_completion(
        runtime, tmp_path, clean, distances, basis, valid, distance):
    lib, endpoint, preparation, *_ = runtime
    execution, start, initial, scope, began = begin_terminal_measurement(runtime, tmp_path, clean)
    measured_at = weigh(runtime, began)
    now = collect_fullness(runtime, began, measured_at, distances)
    name = scope["eventMessageType"]
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    assert event["measurementUid"] != initial["measurementUid"]
    assert event["reportedWeightGrams"] == 1000 and event["uptimeMs"] == measured_at
    assert event["workFullnessBasis"] == basis and event["workFullnessStatus"] == "COMPLETE"
    assert event["fullnessValidSampleCount"] == valid and event["fullnessDistanceMm"] == distance
    assert event["fullnessCompletedSampleCount"] == event["fullnessRequestedSampleCount"] == 5
    assert event["fullnessDistancePresent"] == (basis == "MEASURED_MEDIAN")
    assert event["fullnessConfigContentSha256"] == start["configContentSha256"]
    assert event["fullnessMcuPayloadSha256"] == NativeMcuConfiguration(**inputs()).mcu_payload_sha256
    assert event["fullnessMinimumValidSampleCount"] == inputs()["ports"][0]["fullnessMinimumValidSampleCount"]
    assert event["fullnessDistanceThresholdMm"] == inputs()["ports"][0]["fullnessDistanceThresholdMm"]
    store = EdgeStore(str(tmp_path / "combined.db"))
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:],
            name, uart.encode_payload(name, event))
        assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        work = exchange(runtime, "QUERY_WORK", original_scope(start, clean=clean), now=now)[0][1]
        assert work["status"] == "RUNNING"
        assert work["phase"] == ("CLEAN_RESULT_CONFIRMATION" if clean else "DELIVERY_WAIT_SELECTION")
        assert store.list_native_result_report_tasks() == []
        assert not facts(runtime, now)["cleanLockPowered"]
        assert execution
    finally:
        store.close()


def test_fullness_source_attachment_is_boot_only_and_never_changes_outputs(runtime):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    writes = lib.TestFacts_Writes()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    assert not lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    assert not lib.TestUltrasonic_Trigger()
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 1})[0][1]["mcuBootId"] == 0
    assert exchange(runtime, "BIND_BOOT", {"probeId": 1, "proposedMcuBootId": 42})[0][1]["status"] == "BOUND"
    assert not lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    assert lib.TestFacts_Writes() == writes and not lib.TestUltrasonic_Trigger()


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("failure", ["wrong_group", "disk_failure"])
def test_only_exact_durable_combined_bytes_allow_mcu_to_release_the_record(runtime, tmp_path, clean, failure):
    execution, start, _, scope, began = begin_terminal_measurement(runtime, tmp_path, clean)
    now = collect_fullness(runtime, began, weigh(runtime, began))
    name = scope["eventMessageType"]
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    raw = uart.encode_payload(name, event)
    store = EdgeStore(str(tmp_path / "failed-custody.db"))
    store.initialize()
    try:
        if failure == "wrong_group":
            # Same weight and business identity, valid but different sensor data.
            altered = uart.encode_payload(name, event | {"fullnessDistanceMm": 1001})
            receipt = store.save_native_process_receipt(raw_scope, name, altered)
            assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] != "RELEASED"
        else:
            # Simulated SQLite write boundary; no fake application/save method.
            store._conn.execute("""CREATE TEMP TRIGGER fail_fullness_receipt BEFORE INSERT ON native_process_receipt
                BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
            with pytest.raises(sqlite3.DatabaseError, match="simulated disk failure"):
                store.save_native_process_receipt(raw_scope, name, raw)
            assert store.get_native_process_receipt(raw_scope) is None
            assert store.get_native_measurement_event(42, event["mcuEventSequence"]) is None
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1] == event
        assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=clean), now=now)[0][1]["status"] == "RUNNING"
        assert store.list_native_result_report_tasks() == [] and execution
    finally:
        store.close()


def test_continued_delivery_uses_new_group_without_old_distance_or_new_cloud_work(runtime, tmp_path):
    execution, start, _, scope, began = begin_terminal_measurement(runtime, tmp_path)
    now = collect_fullness(runtime, began, weigh(runtime, began))
    first = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    store = EdgeStore(str(tmp_path / "two-rounds.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, first, now)
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        now = advance(runtime, now, 100)
        now = advance(runtime, now, start["deliveryAutoCloseMs"], poll=False)
        now = advance(runtime, now, 100)
        now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
        began = now
        measured = weigh(runtime, began, (1400,) * 5, measurement=3)
        now = collect_fullness(runtime, began, measured, [None] * 5)
        second_scope = scope | {"stepSequence": 2}
        second = exchange(runtime, "QUERY_PROCESS_EVENT", second_scope, now=now)[1][1]
        assert second["sessionUid"] == first["sessionUid"] == start["sessionUid"]
        assert second["roundIndex"] == 2 and second["measurementUid"] != first["measurementUid"]
        assert second["fullnessGroupSequence"] == first["fullnessGroupSequence"] + 1
        assert second["fullnessStartedUptimeMs"] > first["fullnessCompletedUptimeMs"]
        assert second["reportedWeightGrams"] == 1400 and second["uptimeMs"] == measured
        assert second["fullnessValidSampleCount"] == 0
        assert not second["fullnessDistancePresent"] and second["fullnessDistanceMm"] == 0
        assert second["workFullnessBasis"] == "NO_ECHO_CLEAR_FALLBACK"
        save_process(runtime, store, second_scope, second, now)
        assert store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:])["payload"] == uart.encode_payload(scope["eventMessageType"], first)
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
