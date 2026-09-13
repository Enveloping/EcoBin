"""Actual delivery C owner -> fresh post-close measurement -> exact SQLite custody."""
import pytest

import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_delivery_execution import enable, facts
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope, take_samples
from hardware.tests.test_native_configuration import inputs


def closed_cycle(runtime, tmp_path, **start_options):
    execution = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path, **start_options)
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    for delta in (100, start["deliveryAutoCloseMs"], 100):
        lib.RuntimeClock_Advance(delta)
        lib.ActuatorRuntime_Tick()
        now += delta
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    scope = original_scope(start) | {"eventMessageType": "WORK_POSTCLOSE_WEIGHT_READY",
        "stepSequence": 1, "configVersion": start["configVersion"]}
    return execution, start, initial, scope, now


def advance(runtime, now, delta, *, poll=True):
    lib, endpoint, preparation, *_ = runtime
    lib.RuntimeClock_Advance(delta)
    lib.ActuatorRuntime_Tick()
    now += delta
    if poll:
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def test_close_wait_starts_new_measurement_and_preserves_exact_postclose_record(runtime, tmp_path):
    execution, start, initial, scope, now = closed_cycle(runtime, tmp_path)
    lib, endpoint, preparation, *_ = runtime
    reader = lib.TestPreparation_Weight(preparation)
    wait = inputs()["device"]["deliveryDoorTravelWaitMs"]
    now = advance(runtime, now, wait - 1)
    assert facts(runtime, now)["retainedWorkPhase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    assert not lib.McuWeightRun_StartOwnedAttempt(reader, now)
    now = advance(runtime, now, 1)
    assert facts(runtime, now)["retainedWorkPhase"] == "DELIVERY_POSTCLOSE_MEASURING"
    assert facts(runtime, now)["measurementState"] == "RUNNING"
    assert facts(runtime, now)["measurementSequence"] == 2
    assert facts(runtime, now)["scaleAttemptSequence"] == 5  # Begin did not create a read
    assert facts(runtime, now)["scaleCapturedUptimeMs"] == initial["uptimeMs"]
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[0][1]["status"] == "NOT_FOUND"
    now = take_samples(runtime, [1000, 1050, 950, 1010, 990], start=now, measurement=2)
    observed = facts(runtime, now)
    assert observed["scaleReadStatus"] == "VALID" and observed["scaleWeightGrams"] == 990
    assert observed["scaleAttemptSequence"] == 10 and observed["scaleCapturedUptimeMs"] == now
    response = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert [name for name, _ in response] == ["PROCESS_EVENT_QUERY_REPLY", "WORK_POSTCLOSE_WEIGHT_READY"]
    event = response[1][1]
    assert event["measurementKind"] == "STABLE_MEAN" and event["reportedWeightGrams"] == 1000
    assert event["sampleSpanGrams"] == 100 and event["sampleCount"] == 5
    assert event["measurementElapsedMs"] == 1020 and event["uptimeMs"] == now
    assert event["measurementUid"] != initial["measurementUid"]
    assert event["mcuEventSequence"] == initial["mcuEventSequence"] + 3
    assert event["mcuCommandUid"] == start["mcuCommandUid"] and event["sessionUid"] == start["sessionUid"]
    assert event["roundIndex"] == 1 and event["configVersion"] == start["configVersion"]
    assert facts(runtime, now)["retainedWorkPhase"] == "DELIVERY_WAIT_SELECTION"
    raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", event)
    saved_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    store = EdgeStore(str(tmp_path / "postclose.db"))
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(saved_scope, "WORK_POSTCLOSE_WEIGHT_READY", raw)
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1] == event
        assert not lib.McuWeightRun_StartOwnedAttempt(reader, now)
        assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        assert store.get_native_process_receipt(saved_scope)["payload"] == raw
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
    assert execution


@pytest.mark.parametrize("samples,kind,value,count,span,phase", [
    ([-500, 1500] * 10, "TIMEOUT_MEDIAN", 500, 20, 2000, "DELIVERY_WAIT_SELECTION"),
    ([], "UNAVAILABLE", 0, 0, 0, "DELIVERY_FINALIZING"),
    ([1000] * 4, "UNAVAILABLE", 0, 4, 0, "DELIVERY_FINALIZING"),
])
def test_five_second_limit_uses_fresh_median_or_explicit_missing_not_initial_weight(
        runtime, tmp_path, samples, kind, value, count, span, phase):
    execution, start, initial, scope, now = closed_cycle(runtime, tmp_path)
    lib, endpoint, preparation, *_ = runtime
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = now
    if samples:
        now = take_samples(runtime, samples, start=now, measurement=2)
    now = advance(runtime, now, began + 4999 - now)
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[0][1]["status"] == "NOT_FOUND"
    now = advance(runtime, now, 1)
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    assert event["measurementKind"] == kind and event["reportedWeightGrams"] == value
    assert event["sampleCount"] == count and event["sampleSpanGrams"] == span
    assert event["measurementElapsedMs"] == 5000 and event["uptimeMs"] == began + 5000
    assert event["measurementUid"] != initial["measurementUid"]
    assert event["faultCode"] == ("NONE" if kind == "TIMEOUT_MEDIAN" else "WEIGHT_TIMEOUT")
    assert facts(runtime, now)["retainedWorkPhase"] == phase
    assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
    assert execution and endpoint and preparation


@pytest.mark.parametrize("stopped", [False, True])
def test_pinch_alone_does_not_block_postclose_measurement_but_update_stop_does(runtime, tmp_path, stopped):
    execution, _, _, scope, now = closed_cycle(runtime, tmp_path)
    lib, _, preparation, *_ = runtime
    lib.TestFacts_Pinch(1)
    lib.ActuatorRuntime_Tick()
    assert facts(runtime, now)["pinchPaused"]
    if stopped:
        lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    observed = facts(runtime, now)
    assert observed["lastDeliveryDoorCommand"] == "CLOSE"
    assert observed["retainedWorkPhase"] == ("SAFETY_LOCKED" if stopped else "DELIVERY_POSTCLOSE_MEASURING")
    if stopped:
        lib.TestFacts_Pinch(0)
        now = advance(runtime, now, 10000)
        assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(preparation), now)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[0][1]["status"] == "NOT_FOUND"
        assert not facts(runtime, now)["pb7Output"]
    else:
        now = take_samples(runtime, [1000] * 5, start=now, measurement=2)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]["reportedWeightGrams"] == 1000
        assert facts(runtime, now)["pinchPaused"]  # Still NOT a physical closed-door claim.
    assert execution


def test_late_foreground_and_32bit_wrap_do_not_backdate_window_or_refresh_terminal_value(runtime, tmp_path):
    execution, _, _, scope, now = closed_cycle(runtime, tmp_path, start_at=2**32 - 1000)
    lib, endpoint, preparation, *_ = runtime
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"] + 20000)
    began = now
    assert facts(runtime, now)["measurementState"] == "RUNNING"
    now = take_samples(runtime, [1200] * 5, start=now, measurement=2, publish=False)
    measured = now
    now = advance(runtime, now, 9000)
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    assert event["uptimeMs"] == measured == began + 1020
    assert event["measurementElapsedMs"] == 1020 and event["reportedWeightGrams"] == 1200
    assert facts(runtime, now)["measurementObservedUptimeMs"] == measured
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1] == event
    assert execution


def test_postclose_uses_actual_45000ms_config_not_a_hardcoded_default(runtime, tmp_path):
    import hashlib
    from mcu_configuration import NativeMcuConfiguration

    values = inputs()
    preimage = bytearray(NativeMcuConfiguration(**values).digest_preimage)
    fields = {field["name"]: field for field in uart.MESSAGE_SPECS["CONFIG_DEVICE_BLOCK"]["fields"]}
    offset = (len(bytes.fromhex(uart.REGISTRY["digestProfiles"]["mcuPayloadSha256"]["domainHex"]))
        + 8 + 32 + 1 + fields["deliveryDoorTravelWaitMs"]["offset"] - fields["continueDeliveryWaitMs"]["offset"])
    preimage[offset:offset + 4] = (45000).to_bytes(4, "big")
    values["device"]["deliveryDoorTravelWaitMs"] = 45000
    values["expected_sha256"] = hashlib.sha256(preimage).hexdigest()
    candidate = NativeMcuConfiguration(**values)
    execution, _, _, scope, now = closed_cycle(runtime, tmp_path, candidate=candidate)
    now = advance(runtime, now, 44999)
    assert facts(runtime, now)["retainedWorkPhase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    now = advance(runtime, now, 1)
    assert facts(runtime, now)["retainedWorkPhase"] == "DELIVERY_POSTCLOSE_MEASURING"
    now = take_samples(runtime, [900] * 5, start=now, measurement=2)
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]["reportedWeightGrams"] == 900
    assert execution


@pytest.mark.parametrize("lost", ["before_send", "after_delivery"])
def test_actual_postclose_handoff_survives_pi_restart_and_lost_save_confirmation(runtime, tmp_path, lost):
    from mcu_process_handoff import McuProcessEventHandoff

    execution, start, _, scope, now = closed_cycle(runtime, tmp_path)
    lib, endpoint, preparation, replies, *_ = runtime
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [1400] * 5, start=now, measurement=2)
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", event)
    saved_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    path = str(tmp_path / "handoff.db")
    store = EdgeStore(path)
    store.initialize()
    replies.clear()
    broken, sent = True, []

    def write(frame):
        name = uart.decode_frame(frame, sender_role="EDGE")["messageName"]
        sent.append(name)
        if name == "PROCESS_EVENT_SAVED":
            other = EdgeStore(path)
            other.initialize()
            try:
                assert other.get_native_process_receipt(saved_scope)["payload"] == raw
            finally:
                other.close()
            if broken and lost == "before_send":
                raise OSError("synthetic UART loss before save frame")
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and name == "PROCESS_EVENT_SAVED":
            replies.clear()  # MCU received SAVED, but its reply was lost.
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    original = {key: value for key, value in scope.items() if key != "queryId"}
    try:
        client = McuProcessEventHandoff(store, write, original)
        first_query = client.poll(now)
        pump(client)
        assert sent == ["QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED"]
        assert store.get_native_process_receipt(saved_scope)["payload"] == raw
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        now = advance(runtime, now, 1000)
        resumed = McuProcessEventHandoff(store, write, original)
        assert resumed.poll(now) > first_query
        pump(resumed)
        now = advance(runtime, now, 1000)
        resumed.poll(now)
        pump(resumed)
        assert resumed.observation(now)["status"] == "RELEASED"
        assert set(sent) == {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED"}
        assert sent.count("PROCESS_EVENT_SAVED") == (2 if lost == "before_send" else 1)
        assert facts(runtime, now)["measurementSequence"] == 2
        assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(preparation), now)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
    assert execution
