"""Real C final results reconcile only their original, durably saved evidence."""
import uuid
import hashlib

import pytest
import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_work_recovery import active, RecoveryWire
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_mcu_work_fullness import weigh, collect_fullness
from mcu_process_handoff import process_event_receipt


def completed(case):
    saved = case.wire.finish_clean() if case.clean else case.wire.finish_delivery()
    return saved, uart.decode_payload("WORK_RESULT", saved["payload"])


def evaluate(case):
    return case.store.evaluate_native_work_recovery(case.permit, case.start["mcuCommandUid"], current_boot=lambda: None)


def test_saved_final_result_resolves_exact_original_measurements_and_configuration(active):
    case = active
    saved, result = completed(case)
    before = len(case.wire.sent)
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    evidence = decision["evidence"]
    assert evidence["state"] == "MATCHED"
    assert evidence["configuration"]["configVersion"] == case.start["configVersion"]
    assert evidence["configuration"]["contentSha256"] == case.start["configContentSha256"]
    assert evidence["configuration"]["mcuPayloadSha256"] == inputs()["expected_sha256"]
    for role in ("initial", "final"):
        source = evidence[role]
        values = uart.decode_payload(source["messageName"], source["payload"])
        assert values["measurementUid"] == result[role + "MeasurementUid"]
        assert values["mcuEventSequence"] == result[role + "McuEventSequence"]
        assert values["reportedWeightGrams"] == result[role + "WeightGrams"]
        scope = uart.decode_payload("QUERY_PROCESS_EVENT", (1).to_bytes(8, "big") + source["scope"])
        assert scope["workUid"] == case.permit.work_uid
        assert scope["mcuCommandUid"] == case.start["mcuCommandUid"]
    assert evidence["finalFullness"]["workFullnessStatus"] == "NOT_SAMPLED"
    assert decision["result"]["payload"] == saved["payload"]
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.list_pending_events() == []
    assert len(case.wire.sent) == before
    assert len(case.store.list_native_result_report_tasks()) == 1
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


def completed_with_group(case, runtime):
    wire = RecoveryWire(case, runtime)
    case.wire = wire
    lib, endpoint, *_ = runtime
    if case.clean:
        wire.intent(0, "CLEAN_FINISH_REQUESTED")
        wire.advance(0)
        name = "CLEAN_FINAL_WEIGHT_READY"
    else:
        wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
        name = "WORK_POSTCLOSE_WEIGHT_READY"
    began = wire.now
    wire.now = collect_fullness(runtime, began, weigh(runtime, began), boot_id=case.start["targetMcuBootId"])
    record = wire.custody(name, 1)
    value = uart.decode_payload(name, record["payload"])
    wire.advance(0)
    if case.clean:
        assert lib.McuCleanExecution_Confirm(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
            1, uuid.UUID(value["measurementUid"]).bytes, wire.now)
        wire.custody("CLEAN_COMPLETION_CONFIRMED", 1)
    else:
        assert lib.McuDeliveryExecution_Select(case.execution, endpoint, uuid.UUID(value["measurementUid"]).bytes, 2, wire.now)
        wire.custody("DELIVERY_SELECTION", 1)
    wire.advance(0)
    return wire.handoff_result(), record


@pytest.mark.parametrize("clean", [False, True])
def test_actual_combined_group_is_reconciled_without_refreshing_independent_timestamps(runtime, tmp_path, clean):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    with executed_action_case(runtime, tmp_path, clean_work=clean) as case:
        saved, final = completed_with_group(case, runtime)
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        original = uart.decode_payload(final["message_name"], final["payload"])
        decision = evaluate(case)
        assert decision["evidence"]["state"] == "MATCHED"
        group = decision["evidence"]["finalFullness"]
        assert original["uptimeMs"] < group["fullnessCompletedUptimeMs"] <= result["completedUptimeMs"]
        assert group["fullnessConfigContentSha256"] == decision["evidence"]["configuration"]["contentSha256"]
        assert group["fullnessMcuPayloadSha256"] == decision["evidence"]["configuration"]["mcuPayloadSha256"]
        assert case.store.list_pending_events() == [] and case.store.get_work_slot() == case.occupancy


def rewrite_process(store, record, **changes):
    """Inject a semantically wrong source with valid payload/digest/custody bytes.

    Bypass only the SQLite storage boundary, not the actual reconciler. The
    immutable public archive correctly refuses replacing a stored original.
    """
    values = uart.decode_payload(record["message_name"], record["payload"]) | changes
    raw = uart.encode_payload(record["message_name"], values)
    receipt = process_event_receipt(record["scope"], record["message_name"], raw)
    with store.transaction() as conn:
        conn.execute("UPDATE native_measurement_event SET payload=?,payload_sha256=? WHERE mcu_boot_id=? AND event_sequence=?",
            (raw, hashlib.sha256(raw).hexdigest(), values["mcuBootId"], values["mcuEventSequence"]))
        conn.execute("UPDATE native_process_receipt SET saved_payload=? WHERE scope=?", (receipt, record["scope"]))


def rewrite_result(store, record, **changes):
    """Fault injection at the persisted-byte boundary, with a valid wire digest."""
    value = uart.decode_payload("WORK_RESULT", record["payload"]) | changes
    value["resultDigestSha256"] = uart.compute_result_digest(value)
    raw = uart.encode_payload("WORK_RESULT", value)
    with store.transaction() as conn:
        conn.execute("UPDATE native_mcu_result SET payload=?,result_digest=? WHERE mcu_boot_id=? AND result_sequence=?",
            (raw, value["resultDigestSha256"], record["mcu_boot_id"], record["result_sequence"]))
    return raw


@pytest.mark.parametrize("role", ["initial", "final"])
def test_agreeing_result_and_measurement_must_still_use_the_original_calibration_version(active, role):
    case = active
    saved, result = completed(case)
    source = evaluate(case)["evidence"][role]
    record = case.store.get_native_process_receipt(source["scope"])
    version = result[role + "CalibrationVersion"] + 1
    rewrite_process(case.store, record, calibrationVersion=version)
    raw = rewrite_result(case.store, saved, **{role + "CalibrationVersion": version})
    with pytest.raises(ValueError, match="calibration.*original configuration"):
        evaluate(case)
    assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"])["payload"] == raw
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


@pytest.mark.parametrize("change", ["threshold", "minimum", "count", "content", "subset", "before_phase", "after_result"])
def test_fullness_cannot_disagree_with_original_configuration_or_phase_even_when_its_digest_is_valid(runtime, tmp_path, change):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        saved, final = completed_with_group(case, runtime)
        original = uart.decode_payload(final["message_name"], final["payload"])
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        changes = {
            "threshold": {"fullnessDistanceThresholdMm": inputs()["ports"][0]["fullnessDistanceThresholdMm"] + 1},
            "minimum": {"fullnessMinimumValidSampleCount": 4},
            "count": {"fullnessRequestedSampleCount": 6, "fullnessCompletedSampleCount": 6},
            "content": {"fullnessConfigContentSha256": "ab" * 32},
            "subset": {"fullnessMcuPayloadSha256": "cd" * 32},
            "before_phase": {"fullnessStartedUptimeMs": original["uptimeMs"] - original["measurementElapsedMs"] - 1},
            "after_result": {"fullnessCompletedUptimeMs": result["completedUptimeMs"] + 1},
        }[change]
        rewrite_process(case.store, final, **changes)
        with pytest.raises(ValueError, match="fullness.*original (configuration|phase)"):
            evaluate(case)
        assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"])["payload"] == saved["payload"]
        assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


@pytest.mark.parametrize("role", ["initial", "final"])
def test_missing_scoped_receipt_keeps_complete_result_and_requests_only_original_evidence(active, role):
    case = active
    saved, result = completed(case)
    source = evaluate(case)["evidence"][role]
    with case.store.transaction() as conn:
        # A selection owns a foreign key to its original post-close receipt.
        # Simulate a coherent missing custody set, without disabling SQLite's
        # production integrity constraints or removing the bare measurement.
        selections = conn.execute("SELECT scope,payload FROM native_delivery_selection WHERE mcu_boot_id=? AND postclose_event_sequence=?",
            (result[role + "SourceMcuBootId"], result[role + "McuEventSequence"])).fetchall()
        conn.execute("DELETE FROM native_delivery_selection WHERE mcu_boot_id=? AND postclose_event_sequence=?",
            (result[role + "SourceMcuBootId"], result[role + "McuEventSequence"]))
        conn.execute("DELETE FROM native_process_receipt WHERE scope=?", (source["scope"],))
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["evidence"]["state"] == "WAITING_FOR_PROCESS_CUSTODY"
    assert decision["evidence"]["missing"] == [dict(role=role, messageName=source["messageName"],
        scope=source["scope"], mcuBootId=result[role + "SourceMcuBootId"],
        mcuEventSequence=result[role + "McuEventSequence"], measurementUid=result[role + "MeasurementUid"])]
    assert decision["result"]["payload"] == saved["payload"]
    assert case.store.get_native_measurement_event(result[role + "SourceMcuBootId"], result[role + "McuEventSequence"])
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    case.store.save_native_process_receipt(source["scope"], source["messageName"], source["payload"])
    for selection in selections:
        case.store.save_native_process_receipt(selection["scope"], "DELIVERY_SELECTION", selection["payload"])
    assert evaluate(case)["evidence"]["state"] == "MATCHED"
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


def test_cached_configuration_acceptance_without_original_reply_is_not_evidence(active):
    case = active
    saved, _ = completed(case)
    command = next(row for row in case.store.list_native_commands() if row["message_name"] == "CONFIG_PORT_BLOCK")
    replies = case.store.list_native_command_observations(command["command_uid"])
    with case.store.transaction() as conn:
        conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (command["command_uid"],))
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["result"]["payload"] == saved["payload"]
    assert decision["evidence"]["state"] == "WAITING_FOR_CONFIGURATION_CUSTODY"
    assert decision["evidence"]["configuration"] is None
    assert decision["evidence"]["initial"] and decision["evidence"]["final"]
    for reply in replies:
        case.store.save_native_command_observation(reply["message_name"], reply["payload"])
    assert evaluate(case)["evidence"]["state"] == "MATCHED"
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


@pytest.mark.parametrize("change", ["WeightGrams", "ElapsedMs", "SampleCount", "SpanGrams", "CalibrationVersion", "MeasurementUid", "McuEventSequence"])
def test_valid_result_digest_cannot_replace_the_original_measurement_metadata(active, change):
    case = active
    saved, value = completed(case)
    key = "final" + change
    raw = rewrite_result(case.store, saved, **{key: str(uuid.uuid4()) if change == "MeasurementUid" else value[key] + 1})
    with pytest.raises(ValueError, match="measurement contradicts original process custody"):
        evaluate(case)
    assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"])["payload"] == raw
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


def test_an_earlier_round_cannot_substitute_for_the_results_original_final_scope(active):
    case = active
    saved, value = completed(case)
    key = "cleanActionSequence" if case.clean else "deliveryRoundCount"
    rewrite_result(case.store, saved, **{key: value[key] + 1})
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["evidence"]["state"] == "WAITING_FOR_PROCESS_CUSTODY"
    assert decision["evidence"]["final"] is None
    missing = decision["evidence"]["missing"]
    final_missing = [item for item in missing if item["role"] == "final"]
    assert len(final_missing) == 1
    if not case.clean:
        outputs = [item for item in missing if item["role"] == "actuatorOutput"]
        assert len(outputs) == 1 and outputs[0]["stepSequence"] == value[key] + 1
    original = uart.decode_payload("QUERY_PROCESS_EVENT", (1).to_bytes(8, "big") + final_missing[0]["scope"])
    assert original["stepSequence"] == value[key] + 1
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


@pytest.mark.parametrize("restart_mcu", [False, True])
def test_later_accepted_configuration_cannot_reinterpret_the_original_result(active, tmp_path, restart_mcu):
    from mcu_configuration import NativeMcuConfiguration
    from mcu_session import McuCommandDispatcher

    case = active
    saved, _ = completed(case)
    original = evaluate(case)["evidence"]
    wire = case.wire
    boot = wire.reset_mcu() if restart_mcu else wire.handshake()
    boot_id = boot.current_boot(wire.now)
    values = inputs()
    base = NativeMcuConfiguration(**values)
    # A real different configuration version/content, unchanged MCU policies.
    # The full-cloud validation/application owner remains an explicit boundary.
    values["config_version"] += 1
    values["content_sha256"] = "ab" * 32
    domain = bytes.fromhex(uart.REGISTRY["digestProfiles"]["mcuPayloadSha256"]["domainHex"])
    preimage = domain + values["config_version"].to_bytes(8, "big") + bytes.fromhex(values["content_sha256"]) + base.digest_preimage[len(domain) + 40:]
    values["expected_sha256"] = hashlib.sha256(preimage).hexdigest()
    candidate = NativeMcuConfiguration(**values)
    def config_guard(record):
        assert record["message_name"].startswith("CONFIG_")
        return lambda: None  # Non-actuating configuration authorization boundary.
    dispatcher = McuCommandDispatcher(case.store, boot, wire.write, arm=config_guard, clock=lambda: wire.now)
    identity = {"mcuCommandUid", "targetMcuBootId", "commandSequence", "commandDigestSha256"}
    application_uid = str(uuid.uuid4())
    for index in range(1, candidate.part_count + 1):
        name, raw = candidate.encode_part(index, application_uid=application_uid,
            mcu_command_uid=str(uuid.uuid4()), target_mcu_boot_id=boot_id, command_sequence=index)
        value = uart.decode_payload(name, raw)
        command = case.store.prepare_native_command(name, value["mcuCommandUid"], boot_id,
            {key: item for key, item in value.items() if key not in identity})
        assert dispatcher.send_once(command["command_uid"])
        wire.pump(dispatcher)
        assert case.store.get_native_command(command["command_uid"])["decision_outcome"] == "ACCEPTED"
    # Reopen the real Pi database too: no live C cache or in-memory copy may
    # replace the persisted original evidence.
    from edge_store import EdgeStore
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    case.store.initialize()
    decision = evaluate(case)
    assert decision["result"]["payload"] == saved["payload"]
    assert decision["evidence"] == original
    assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []
