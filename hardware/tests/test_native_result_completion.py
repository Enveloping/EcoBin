"""Actual C terminal choices/confirmation remain attached to the original result."""
import uuid

import pytest
import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime, take_samples, exchange, original_scope
from hardware.tests.test_native_work_recovery import active, RecoveryWire
from hardware.tests.test_native_result_evidence import completed, completed_with_group, evaluate, rewrite_result
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from mcu_process_handoff import process_event_receipt
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_work_fullness import collect_fullness


def test_complete_result_exposes_the_exact_original_terminal_process_receipt(active):
    case = active
    saved, result = completed(case)
    before = len(case.wire.sent)
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["evidence"]["state"] == "MATCHED"
    source = decision["evidence"]["completion"]
    name = "CLEAN_COMPLETION_CONFIRMED" if case.clean else "DELIVERY_SELECTION"
    assert source["messageName"] == name
    original = uart.decode_payload(name, source["payload"])
    assert original["finalMeasurementUid" if case.clean else "postCloseMeasurementUid"] == result["finalMeasurementUid"]
    assert original["uptimeMs"] <= result["completedUptimeMs"]
    assert case.store.get_native_process_receipt(source["scope"])["payload"] == source["payload"]
    assert decision["result"]["payload"] == saved["payload"]
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []
    assert len(case.wire.sent) == before
    assert len(case.store.list_native_result_report_tasks()) == 1


def test_missing_terminal_receipt_keeps_the_complete_result_pending_original_evidence(active):
    case = active
    saved, result = completed(case)
    source = evaluate(case)["evidence"]["completion"]
    table = "native_clean_confirmation" if case.clean else "native_delivery_selection"
    with case.store.transaction() as conn:
        conn.execute(f"DELETE FROM {table} WHERE scope=?", (source["scope"],))
    before = len(case.wire.sent)
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["evidence"]["state"] == "WAITING_FOR_PROCESS_CUSTODY"
    assert decision["evidence"]["completion"] is None
    assert decision["evidence"]["missing"] == [dict(role="completion", messageName=source["messageName"],
        scope=source["scope"], mcuBootId=result["mcuBootId"], measurementUid=result["finalMeasurementUid"])]
    assert decision["result"]["payload"] == saved["payload"]
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []
    assert len(case.wire.sent) == before
    case.store.save_native_process_receipt(source["scope"], source["messageName"], source["payload"])
    assert evaluate(case)["evidence"]["state"] == "MATCHED"


def test_result_cannot_contradict_its_original_choice_or_human_confirmation(active):
    case = active
    saved, _ = completed(case)
    changes = dict(physicalCloseConfirmed=False, finishReason="FAILED") if case.clean else dict(finishReason="DELIVERY_WINDOW_EXPIRED")
    raw = rewrite_result(case.store, saved, **changes)
    with pytest.raises(ValueError, match="completion contradicts original"):
        evaluate(case)
    assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"])["payload"] == raw
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


def rewrite_completion(store, source, **changes):
    """Storage-boundary fault injection; public custody is immutable."""
    name = source["messageName"]
    value = uart.decode_payload(name, source["payload"]) | changes
    raw = uart.encode_payload(name, value)
    receipt = process_event_receipt(source["scope"], name, raw)
    table = "native_clean_confirmation" if name == "CLEAN_COMPLETION_CONFIRMED" else "native_delivery_selection"
    with store.transaction() as conn:
        conn.execute(f"UPDATE {table} SET payload=?,saved_payload=? WHERE scope=?", (raw, receipt, source["scope"]))


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("point", ["before_fullness_completion", "after_result"])
def test_terminal_action_cannot_predate_its_full_sample_group_or_postdate_the_result(runtime, tmp_path, clean, point):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    with executed_action_case(runtime, tmp_path, clean_work=clean) as case:
        saved, final = completed_with_group(case, runtime)
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        evidence = evaluate(case)["evidence"]
        phase_end = evidence["finalFullness"]["fullnessCompletedUptimeMs"]
        assert uart.decode_payload(final["message_name"], final["payload"])["uptimeMs"] < phase_end
        timestamp = phase_end - 1 if point == "before_fullness_completion" else result["completedUptimeMs"] + 1
        rewrite_completion(case.store, evidence["completion"], uptimeMs=timestamp)
        with pytest.raises(ValueError, match="completion.*original phase"):
            evaluate(case)
        assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"])["payload"] == saved["payload"]
        assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


def test_actual_delivery_choice_timeout_keeps_its_distinct_terminal_reason(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        wire = case.wire = RecoveryWire(case, runtime)
        wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
        wire.now = take_samples(runtime, [700] * 5, start=wire.now, measurement=2)
        wire.custody("WORK_POSTCLOSE_WEIGHT_READY", 1)
        wire.advance(0)
        wire.advance(case.start["continueDeliveryWaitMs"])
        terminal = wire.custody("DELIVERY_SELECTION", 1)
        wire.advance(0)
        saved = wire.handoff_result()
        assert uart.decode_payload("WORK_RESULT", saved["payload"])["finishReason"] == "DELIVERY_WINDOW_EXPIRED"
        evidence = evaluate(case)["evidence"]
        assert evidence["state"] == "MATCHED"
        assert evidence["completion"]["payload"] == terminal["payload"]
        assert uart.decode_payload("DELIVERY_SELECTION", terminal["payload"])["selection"] == "WINDOW_EXPIRED"


def test_actual_failed_delivery_does_not_invent_or_require_a_successful_ending_choice(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        case.wire = RecoveryWire(case, runtime)
        saved = case.wire.finish_delivery(unavailable=True)
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finishReason"] == "FAILED" and result["finalKind"] == "UNAVAILABLE"
        evidence = evaluate(case)["evidence"]
        assert evidence["state"] == "MATCHED" and evidence["completion"] is None
        assert evidence["missing"] == []
        assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


def test_failed_delivery_measurement_cannot_be_relabelled_as_a_normal_ending(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        case.wire = RecoveryWire(case, runtime)
        saved = case.wire.finish_delivery(unavailable=True)
        rewrite_result(case.store, saved, finishReason="DELIVERY_END")
        with pytest.raises(ValueError, match="completion requires an available original final measurement"):
            evaluate(case)
        assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


@pytest.mark.parametrize("interrupted", [False, True])
def test_actual_clean_weight_failure_keeps_its_original_human_confirmation(runtime, tmp_path, interrupted):
    with executed_action_case(runtime, tmp_path, clean_work=True) as case:
        wire = case.wire = RecoveryWire(case, runtime)
        lib, endpoint, preparation, *_ = runtime
        wire.intent(0, "CLEAN_FINISH_REQUESTED")
        wire.advance(0)
        if interrupted:
            wire.now = take_samples(runtime, [123], start=wire.now, measurement=2)
            # Explicit acquisition interruption boundary, as in the existing
            # C confirmation test; not a simulated automatic safety verdict.
            assert lib.McuWeightRun_Interrupt(lib.TestPreparation_Weight(preparation), 2, wire.now)
            wire.advance(0)
        else:
            wire.advance(5000)
        final = wire.custody("CLEAN_FINAL_WEIGHT_READY", 1)
        last = uart.decode_payload("CLEAN_FINAL_WEIGHT_READY", final["payload"])
        assert lib.McuCleanExecution_Confirm(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
            1, uuid.UUID(last["measurementUid"]).bytes, wire.now)
        terminal = wire.custody("CLEAN_COMPLETION_CONFIRMED", 1)
        wire.advance(0)
        saved = wire.handoff_result()
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finishReason"] == ("FAILED" if interrupted else "CLEAN_CONFIRMED")
        assert result["physicalCloseConfirmed"] and result["finalKind"] == ("INTERRUPTED" if interrupted else "UNAVAILABLE")
        evidence = evaluate(case)["evidence"]
        assert evidence["state"] == "MATCHED" and evidence["completion"]["payload"] == terminal["payload"]
        assert evidence["missing"] == []
        assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


def test_claimed_terminal_action_requires_an_original_final_measurement_identity(active):
    case = active
    saved, _ = completed(case)
    missing = {"finalKind": "NOT_TAKEN", "finalMeasurementUid": str(uuid.UUID(int=0)), "finalFaultCode": "NONE"}
    missing.update({"final" + suffix: 0 for suffix in ("SourceMcuBootId", "McuEventSequence", "WeightGrams",
        "ElapsedMs", "SampleCount", "SpanGrams", "CalibrationVersion")})
    raw = rewrite_result(case.store, saved, **missing)
    with pytest.raises(ValueError, match="completion requires an original final measurement"):
        evaluate(case)
    assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"])["payload"] == raw
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


def test_missing_original_clean_finish_request_is_waiting_not_a_lost_result(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=True) as case:
        case.wire = RecoveryWire(case, runtime)
        saved, result = completed(case)
        terminal = evaluate(case)["evidence"]["completion"]
        scope = uart.decode_payload("QUERY_PROCESS_EVENT", (1).to_bytes(8, "big") + terminal["scope"])
        scope["eventMessageType"] = "CLEAN_FINISH_REQUESTED"
        raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
        original = case.store.get_native_process_receipt(raw_scope)
        with case.store.transaction() as conn:
            conn.execute("DELETE FROM native_clean_intent WHERE scope=?", (raw_scope,))
        decision = evaluate(case)
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert decision["evidence"]["state"] == "WAITING_FOR_PROCESS_CUSTODY"
        assert decision["evidence"]["missing"] == [dict(role="completionRequest", messageName="CLEAN_FINISH_REQUESTED",
            scope=raw_scope, mcuBootId=result["mcuBootId"])]
        assert decision["result"]["payload"] == saved["payload"]
        case.store.save_native_process_receipt(raw_scope, "CLEAN_FINISH_REQUESTED", original["payload"])
        assert evaluate(case)["evidence"]["completion"] == terminal
        assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


@pytest.mark.parametrize("clean", [False, True])
def test_continuation_or_reopen_uses_only_the_new_final_candidate_and_terminal_receipt(runtime, tmp_path, clean):
    from mcu_action_evidence import NativeActionReconciler
    from mcu_actuator_handoff import McuActuatorEventHandoff
    with executed_action_case(runtime, tmp_path, clean_work=clean) as case:
        wire = case.wire = RecoveryWire(case, runtime)
        lib, endpoint, *_ = runtime
        name = "CLEAN_FINAL_WEIGHT_READY" if clean else "WORK_POSTCLOSE_WEIGHT_READY"
        if clean:
            assert NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)["state"] == "CONFIRMED"
            wire.intent(0, "CLEAN_FINISH_REQUESTED")
            wire.advance(0)
        else:
            wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
        wire.now = take_samples(runtime, [700] * 5, start=wire.now, measurement=2)
        first = wire.custody(name, 1)
        old = uart.decode_payload(name, first["payload"])
        wire.advance(0)
        if clean:
            reopen = wire.prepare_reopen(wire.intent(1, "CLEAN_UNLOCK_REQUESTED"))
            wire.dispatch(reopen)
            wire.save_outputs()
            assert NativeActionReconciler(case.store, case.safety).reconcile(reopen.action.action_uid)["state"] == "CONFIRMED"
            wire.intent(2, "CLEAN_FINISH_REQUESTED")
            wire.advance(0)
            step = 3
        else:
            assert lib.McuDeliveryExecution_Select(case.execution, endpoint, uuid.UUID(old["measurementUid"]).bytes, 1, wire.now)
            wire.custody("DELIVERY_SELECTION", 1)
            wire.advance(0)
            for elapsed in (100, case.start["deliveryAutoCloseMs"], 100, inputs()["device"]["deliveryDoorTravelWaitMs"]):
                wire.advance(elapsed)
            step = 2
        wire.now = take_samples(runtime, [900] * 5, start=wire.now, measurement=3)
        final = wire.custody(name, step)
        current = uart.decode_payload(name, final["payload"])
        wire.advance(0)
        if clean:
            assert lib.McuCleanExecution_Confirm(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
                step, uuid.UUID(current["measurementUid"]).bytes, wire.now)
            terminal_name = "CLEAN_COMPLETION_CONFIRMED"
        else:
            assert lib.McuDeliveryExecution_Select(case.execution, endpoint, uuid.UUID(current["measurementUid"]).bytes, 2, wire.now)
            terminal_name = "DELIVERY_SELECTION"
        terminal = wire.custody(terminal_name, step)
        wire.advance(0)
        saved = wire.handoff_result()
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finalMeasurementUid"] == current["measurementUid"] != old["measurementUid"]
        assert result["cleanActionSequence" if clean else "deliveryRoundCount"] == step
        evidence = evaluate(case)["evidence"]
        if not clean:
            assert evidence["state"] == "WAITING_FOR_ACTUATOR_CUSTODY"
            client = McuActuatorEventHandoff(case.store, wire.write, 1)
            for _ in range(3):
                client.poll(wire.now)
                wire.pump(client)
                wire.advance(1000)
            evidence = evaluate(case)["evidence"]
        assert evidence["state"] == "MATCHED"
        assert evidence["final"]["payload"] == final["payload"]
        assert evidence["completion"]["payload"] == terminal["payload"]
        assert case.store.get_native_process_receipt(first["scope"])["payload"] == first["payload"]
        assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("handoff_delay", [0, 4000])
@pytest.mark.parametrize("distances", [(1000,) * 5, (None,) * 5], ids=["echo", "no-echo"])
def test_actual_weight_timeout_result_cannot_finish_before_its_later_fullness_group(runtime, tmp_path, clean, handoff_delay, distances):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    with executed_action_case(runtime, tmp_path, clean_work=clean) as case:
        wire = case.wire = RecoveryWire(case, runtime)
        if clean:
            wire.intent(0, "CLEAN_FINISH_REQUESTED")
            wire.advance(0)
        else:
            wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
        began = wire.now
        # No new scale reply. The real weight owner expires at five seconds;
        # the real ultrasonic group only starts after the configured settle.
        wire.now = collect_fullness(runtime, began, began, distances, boot_id=1)
        captured = exchange(runtime, "QUERY_DEVICE_FACTS", dict(queryId=999, targetMcuBootId=1, portNo=1), now=wire.now)[0][1]
        assert captured["capturedUptimeMs"] == wire.now
        wire.advance(handoff_delay)
        name = "CLEAN_FINAL_WEIGHT_READY" if clean else "WORK_POSTCLOSE_WEIGHT_READY"
        final = wire.custody(name, 1)
        measurement = uart.decode_payload(name, final["payload"])
        assert measurement["measurementKind"] == "UNAVAILABLE"
        assert measurement["uptimeMs"] == began + 5000 < measurement["fullnessCompletedUptimeMs"]
        if clean:
            assert lib.McuCleanExecution_Confirm(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
                1, uuid.UUID(measurement["measurementUid"]).bytes, wire.now)
            wire.custody("CLEAN_COMPLETION_CONFIRMED", 1)
        wire.advance(0)
        observed = exchange(runtime, "QUERY_WORK", original_scope(case.start, clean=clean), now=wire.now)[0][1]
        assert observed["status"] == "RESULT_HELD"
        identity = dict(queryId=123, mcuBootId=1, resultSequence=observed["resultSequence"],
            workUid=case.permit.work_uid, resultDigestSha256=observed["resultDigestSha256"])
        original_result = exchange(runtime, "QUERY_RESULT", identity, now=wire.now)[1][1]
        saved = wire.handoff_result()
        assert saved["payload"] == uart.encode_payload("WORK_RESULT", original_result)
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["completedUptimeMs"] >= measurement["fullnessCompletedUptimeMs"], {
            "weight": measurement["uptimeMs"], "fullness": measurement["fullnessCompletedUptimeMs"],
            "result": result["completedUptimeMs"]}
        assert result["finalElapsedMs"] == 5000 and result["finalKind"] == "UNAVAILABLE"
        assert result["completedUptimeMs"] == (wire.now if clean else measurement["fullnessCompletedUptimeMs"])
        assert evaluate(case)["evidence"]["state"] == "MATCHED"
