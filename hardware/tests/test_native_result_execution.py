"""Actual MCU results retain their original command-bound execution evidence."""
from dataclasses import asdict

import pytest
import uuid
import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime, take_samples, original_scope
from hardware.tests.test_native_work_recovery import active, RecoveryWire
from hardware.tests.test_native_result_evidence import completed, evaluate
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_native_configuration import inputs
from mcu_action_evidence import NativeActionReconciler
from mcu_actuator_handoff import McuActuatorEventHandoff


def scope_for(case, name, step):
    return uart.encode_payload("QUERY_PROCESS_EVENT", original_scope(case.start, clean=case.clean) | dict(
        eventMessageType=name, stepSequence=step, configVersion=case.start["configVersion"]))[8:]


def next_cycle(case, runtime):
    wire = case.wire = RecoveryWire(case, runtime)
    lib, endpoint, *_ = runtime
    name = "CLEAN_FINAL_WEIGHT_READY" if case.clean else "WORK_POSTCLOSE_WEIGHT_READY"
    if case.clean:
        NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
        wire.intent(0, "CLEAN_FINISH_REQUESTED")
        wire.advance(0)
    else:
        wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
    wire.now = take_samples(runtime, [700] * 5, start=wire.now, measurement=2)
    old = uart.decode_payload(name, wire.custody(name, 1)["payload"])
    wire.advance(0)
    if case.clean:
        reopen = wire.prepare_reopen(wire.intent(1))
        wire.dispatch(reopen)
        wire.save_outputs()
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
    last = uart.decode_payload(name, wire.custody(name, step)["payload"])
    wire.advance(0)
    if case.clean:
        assert lib.McuCleanExecution_Confirm(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
            step, uuid.UUID(last["measurementUid"]).bytes, wire.now)
    else:
        assert lib.McuDeliveryExecution_Select(case.execution, endpoint, uuid.UUID(last["measurementUid"]).bytes, 2, wire.now)
    wire.custody("CLEAN_COMPLETION_CONFIRMED" if case.clean else "DELIVERY_SELECTION", step)
    wire.advance(0)
    saved = wire.handoff_result()
    client = McuActuatorEventHandoff(case.store, wire.write, 1)
    for _ in range(3):
        client.poll(wire.now)
        wire.pump(client)
        wire.advance(1000)
    return saved


@pytest.mark.parametrize("clean", [False, True])
def test_missing_whole_later_cycle_cannot_borrow_the_first_outputs(runtime, tmp_path, clean):
    with executed_action_case(runtime, tmp_path, clean_work=clean) as case:
        saved = next_cycle(case, runtime)
        original = case.store.list_native_work_actuator_events(case.permit.work_uid)
        assert len(original) == 4
        assert evaluate(case)["evidence"]["state"] == "MATCHED"
        lost = original[-2:]
        with case.store.transaction() as conn:
            for row in lost:
                conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=? AND event_sequence=?",
                    (row["mcu_boot_id"], row["event_sequence"]))
        result = evaluate(case)
        assert result["evidence"]["state"] == "WAITING_FOR_ACTUATOR_CUSTODY"
        assert result["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert result["result"]["payload"] == saved["payload"]
        for row in lost:
            case.store.save_native_actuator_event(row["message_name"], row["payload"])
        assert evaluate(case)["evidence"]["state"] == "MATCHED"


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("bad", ["direction", "duration", "before-cause"])
def test_later_output_cycle_is_checked_against_its_own_original_cause(runtime, tmp_path, clean, bad):
    with executed_action_case(runtime, tmp_path, clean_work=clean) as case:
        next_cycle(case, runtime)
        rows = case.store.list_native_work_actuator_events(case.permit.work_uid)[-2:]
        first = uart.decode_payload(rows[0]["message_name"], rows[0]["payload"])
        if bad == "direction":
            changes = {"lockPowerState": "ENERGIZED"} if clean else {"command": "OPEN"}
            row = rows[-1]
        elif bad == "duration":
            changes, row = {"uptimeMs": first["uptimeMs"] + 1}, rows[-1]
        else:
            changes, row = {"uptimeMs": 1}, rows[0]
        rewrite_output(case.store, row, **changes)
        with pytest.raises(ValueError, match="output|chronology|predates"):
            evaluate(case)


def rewrite_output(store, row, **changes):
    """Inject validly encoded but contradictory bytes at the storage boundary."""
    value = uart.decode_payload(row["message_name"], row["payload"]) | changes
    raw = uart.encode_payload(row["message_name"], value)
    saved = uart.encode_payload("ACTUATOR_EVENT_SAVED", dict(mcuBootId=value["mcuBootId"],
        mcuEventSequence=value["mcuEventSequence"], eventMessageType=row["message_name"],
        eventDigestSha256=uart.compute_actuator_event_digest(row["message_name"], raw)))
    with store.transaction() as conn:
        conn.execute("UPDATE native_actuator_event SET payload=?,saved_payload=? WHERE mcu_boot_id=? AND event_sequence=?",
            (raw, saved, row["mcu_boot_id"], row["event_sequence"]))


@pytest.mark.parametrize("bad", ["direction", "duration", "before-weight"])
def test_normal_result_cannot_claim_an_invalid_first_output_cycle(active, bad):
    case = active
    completed(case)
    rows = case.store.list_native_work_actuator_events(case.permit.work_uid)
    first = uart.decode_payload(rows[0]["message_name"], rows[0]["payload"])
    if bad == "direction":
        changes = {"lockPowerState": "ENERGIZED"} if case.clean else {"command": "OPEN"}
        row = rows[-1]
    elif bad == "duration":
        changes, row = {"uptimeMs": first["uptimeMs"] + 1}, rows[-1]
    else:
        changes, row = {"uptimeMs": 1}, rows[0]
    rewrite_output(case.store, row, **changes)
    with pytest.raises(ValueError, match="output|chronology"):
        evaluate(case)


def test_result_exposes_original_accepted_start_and_bound_output_facts(active):
    case = active
    saved, result = completed(case)
    before = len(case.wire.sent)
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["evidence"]["state"] == "MATCHED"
    execution = decision["evidence"]["execution"]
    assert execution["startAcceptance"]["messageName"] == "COMMAND_DECISION"
    assert len(execution["commands"]) == 1
    command = execution["commands"][0]
    assert command["binding"]["permit"] == asdict(case.permit)
    assert command["binding"]["action"] == asdict(case.action)
    witness = command["acceptance"]
    accepted = uart.decode_payload(witness["messageName"], bytes.fromhex(witness["payloadHex"]))
    assert accepted["outcome"] == "ACCEPTED"
    assert accepted["mcuCommandUid"] == case.action.action_uid
    assert len(execution["events"]) == 2
    expected = "CLEAN_LOCK_POWER_CHANGED" if case.clean else "DELIVERY_DOOR_COMMAND_RESULT"
    for event in execution["events"]:
        assert event["message_name"] == expected
        raw = uart.decode_payload(expected, event["payload"])
        assert raw["mcuCommandUid"] == case.action.action_uid
        assert raw["uptimeMs"] <= result["completedUptimeMs"]
    assert decision["result"]["payload"] == saved["payload"]
    assert len(case.wire.sent) == before
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.list_pending_events() == []
    assert case.store.get_native_action_confirmation(case.action.action_uid) is None


@pytest.mark.parametrize("remove_all", [False, True])
def test_missing_outputs_wait_for_original_custody_without_losing_complete_result(active, remove_all):
    case = active
    saved, _ = completed(case)
    original = case.store.list_native_work_actuator_events(case.permit.work_uid)
    lost = original if remove_all else original[-1:]
    with case.store.transaction() as conn:
        for row in lost:
            conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=? AND event_sequence=?",
                (row["mcu_boot_id"], row["event_sequence"]))
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["evidence"]["state"] == "WAITING_FOR_ACTUATOR_CUSTODY"
    assert decision["evidence"]["missing"]
    assert decision["result"]["payload"] == saved["payload"]
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    for row in lost:
        case.store.save_native_actuator_event(row["message_name"], row["payload"])
    assert evaluate(case)["evidence"]["state"] == "MATCHED"


@pytest.mark.parametrize("target", ["start", "action"])
def test_missing_acceptance_is_not_replaced_by_the_decision_cache(active, target):
    case = active
    saved, _ = completed(case)
    uid = case.start["mcuCommandUid"] if target == "start" else case.action.action_uid
    with case.store.transaction() as conn:
        conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (uid,))
    result = evaluate(case)
    assert result["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert result["evidence"]["state"] == "WAITING_FOR_COMMAND_CUSTODY"
    assert result["evidence"]["missing"] == [dict(role=target + "Acceptance", commandUid=uid)]
    assert result["result"]["payload"] == saved["payload"]


def test_rejected_authorization_cannot_be_treated_as_merely_missing(active):
    case = active
    completed(case)
    with case.store.transaction() as conn:
        conn.execute("UPDATE native_mcu_command SET decision_outcome='REJECTED',decision_error='BUSY' WHERE command_uid=?",
            (case.action.action_uid,))
    with pytest.raises(ValueError, match="authorization|acceptance|dispatch"):
        evaluate(case)


@pytest.mark.parametrize("column", ["reported_work_uid", "reported_command_uid", "port_no"])
def test_corrupt_actuator_index_cannot_hide_original_bytes(active, column):
    case = active
    completed(case)
    row = case.store.list_native_work_actuator_events(case.permit.work_uid)[0]
    with case.store.transaction() as conn:
        conn.execute(f"UPDATE native_actuator_event SET {column}=? WHERE mcu_boot_id=? AND event_sequence=?",
            (6 if column == "port_no" else str(uuid.uuid4()), row["mcu_boot_id"], row["event_sequence"]))
    with pytest.raises(ValueError, match="actuator evidence is corrupt"):
        evaluate(case)


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("later", [False, True])
def test_final_measurement_cannot_precede_the_last_close_or_lock_off(runtime, tmp_path, clean, later):
    with executed_action_case(runtime, tmp_path, clean_work=clean) as case:
        case.wire = RecoveryWire(case, runtime)
        next_cycle(case, runtime) if later else completed(case)
        decision = evaluate(case)
        source = decision["evidence"]["final"]
        final = uart.decode_payload(source["messageName"], source["payload"])
        row = case.store.list_native_work_actuator_events(case.permit.work_uid)[-1]
        rewrite_output(case.store, row, uptimeMs=final["uptimeMs"] - 1)
        with pytest.raises(ValueError, match="measurement|phase"):
            evaluate(case)


def test_saved_reopen_button_requires_its_own_binding_even_when_command_and_outputs_are_lost(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=True) as case:
        saved = next_cycle(case, runtime)
        later = case.store.list_native_work_actuator_events(case.permit.work_uid)[-2:]
        uid = later[0]["reported_command_uid"]
        original = case.store.get_native_action_binding(uid)
        assert evaluate(case)["evidence"]["state"] == "MATCHED"
        with case.store.transaction() as conn:
            conn.execute("DELETE FROM native_actuator_event WHERE reported_command_uid=?", (uid,))
            conn.execute("DELETE FROM native_action_binding WHERE action_uid=?", (uid,))
            conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (uid,))
            conn.execute("DELETE FROM native_mcu_command WHERE command_uid=?", (uid,))
        decision = evaluate(case)
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert decision["evidence"]["state"] == "WAITING_FOR_COMMAND_CUSTODY"
        assert decision["evidence"]["missing"] == [dict(role="actionBinding", workUid=case.permit.work_uid,
            actionKey=original["action"].action_key)]
        assert decision["result"]["payload"] == saved["payload"]
        assert case.store.get_work_slot() == case.occupancy
        assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


@pytest.mark.parametrize("step,name", [(1, "CLEAN_FINISH_REQUESTED"), (2, "CLEAN_UNLOCK_REQUESTED")])
def test_missing_intermediate_clean_button_waits_without_guessing_its_kind(runtime, tmp_path, step, name):
    with executed_action_case(runtime, tmp_path, clean_work=True) as case:
        saved = next_cycle(case, runtime)
        scope = scope_for(case, name, step)
        row = case.store.get_native_process_receipt(scope)
        with case.store.transaction() as conn:
            conn.execute("DELETE FROM native_clean_intent WHERE scope=?", (scope,))
        decision = evaluate(case)
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert decision["evidence"]["state"] == "WAITING_FOR_PROCESS_CUSTODY"
        missing = decision["evidence"]["missing"]
        assert len(missing) == 1 and missing[0]["role"] == "actionCause"
        assert missing[0]["stepSequence"] == step
        assert missing[0]["alternatives"] == [dict(messageName=kind, scope=scope_for(case, kind, step))
            for kind in ("CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED")]
        assert decision["result"]["payload"] == saved["payload"]
        case.store.save_native_process_receipt(scope, name, row["payload"])
        assert evaluate(case)["evidence"]["state"] == "MATCHED"


def test_missing_prior_delivery_weight_defers_dependent_continue_validation(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        saved = next_cycle(case, runtime)
        name = "WORK_POSTCLOSE_WEIGHT_READY"
        scope = scope_for(case, name, 1)
        row = case.store.get_native_process_receipt(scope)
        selection_scope = scope_for(case, "DELIVERY_SELECTION", 1)
        selection = case.store.get_native_process_receipt(selection_scope)
        with case.store.transaction() as conn:
            conn.execute("DELETE FROM native_delivery_selection WHERE scope=?", (selection_scope,))
            conn.execute("DELETE FROM native_process_receipt WHERE scope=?", (scope,))
        decision = evaluate(case)
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert decision["evidence"]["state"] == "WAITING_FOR_PROCESS_CUSTODY"
        assert decision["evidence"]["missing"] == [dict(role="actionMeasurement", messageName=name,
            scope=scope, mcuBootId=case.start["targetMcuBootId"])]
        assert decision["result"]["payload"] == saved["payload"]
        case.store.save_native_process_receipt(scope, name, row["payload"])
        assert evaluate(case)["evidence"]["missing"] == [dict(role="actionCause", messageName="DELIVERY_SELECTION",
            scope=selection_scope, mcuBootId=case.start["targetMcuBootId"])]
        case.store.save_native_process_receipt(selection_scope, "DELIVERY_SELECTION", selection["payload"])
        assert evaluate(case)["evidence"]["state"] == "MATCHED"


def test_normal_delivery_result_cannot_hide_outputs_from_an_unreported_later_round(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        next_cycle(case, runtime)
        row = case.store.list_native_work_actuator_events(case.permit.work_uid)[-1]
        rewrite_output(case.store, row, roundIndex=3)
        with pytest.raises(ValueError, match="round|output"):
            evaluate(case)


def test_intermediate_clean_buttons_keep_their_original_chronology(runtime, tmp_path):
    from mcu_process_handoff import process_event_receipt
    with executed_action_case(runtime, tmp_path, clean_work=True) as case:
        next_cycle(case, runtime)
        name = "CLEAN_FINISH_REQUESTED"
        scope = scope_for(case, name, 1)
        earlier = uart.decode_payload(name, case.store.get_native_process_receipt(scope)["payload"])
        later = uart.decode_payload("CLEAN_UNLOCK_REQUESTED", case.store.get_native_process_receipt(
            scope_for(case, "CLEAN_UNLOCK_REQUESTED", 2))["payload"])
        raw = uart.encode_payload(name, earlier | dict(uptimeMs=later["uptimeMs"] + 1))
        with case.store.transaction() as conn:
            conn.execute("UPDATE native_clean_intent SET payload=?,saved_payload=? WHERE scope=?",
                (raw, process_event_receipt(scope, name, raw), scope))
        with pytest.raises(ValueError, match="chronology|button"):
            evaluate(case)
