"""rc.23 final-result custody is reconciled to the exact original START."""
import uuid
from dataclasses import replace

import pytest
import uart2_protocol as uart

from hardware.tests.native_confirmation_fixture import autonomous_result_case
from hardware.tests.test_mcu_simplified_execution import library, runtime
from hardware.tests.test_native_configuration import inputs


def completed(case):
    """Finish an active current work, retained for neighbouring native tests."""
    saved = case.wire.finish_clean() if case.clean else case.wire.finish_delivery()
    return saved, uart.decode_payload("WORK_RESULT", saved["payload"])


def evaluate(case):
    return case.store.evaluate_native_work_recovery(
        case.permit, case.start["mcuCommandUid"], current_boot=lambda: None)


def stored_result(case):
    task = case.store.list_native_result_report_tasks()[0]
    return case.store.get_native_mcu_result(task["mcu_boot_id"], task["result_sequence"])


def rewrite_result(store, record, **changes):
    """Inject a self-consistent result at the SQLite boundary."""
    value = uart.decode_payload("WORK_RESULT", record["payload"]) | changes
    value["resultDigestSha256"] = uart.compute_result_digest(value)
    raw = uart.encode_payload("WORK_RESULT", value)
    with store.transaction() as conn:
        conn.execute(
            "UPDATE native_mcu_result SET payload=?,result_digest=? "
            "WHERE mcu_boot_id=? AND result_sequence=?",
            (raw, value["resultDigestSha256"], record["mcu_boot_id"], record["result_sequence"]),
        )
    return raw


@pytest.fixture(params=[False, True], ids=["delivery", "clean"])
def complete_case(runtime, tmp_path, request):
    samples = (100,) * 5 if request.param else (700,) * 5
    with autonomous_result_case(runtime, tmp_path, clean=request.param, samples=samples) as case:
        yield case


def test_saved_result_projects_exact_original_measurements_configuration_and_control(complete_case):
    case = complete_case
    saved = stored_result(case)
    result = uart.decode_payload("WORK_RESULT", saved["payload"])
    decision = evaluate(case)
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert decision["result"]["payload"] == saved["payload"]
    evidence = decision["evidence"]
    assert evidence["state"] == "MATCHED"
    assert evidence["configuration"]["configVersion"] == case.start["configVersion"]
    assert evidence["configuration"]["contentSha256"] == case.start["configContentSha256"]
    assert evidence["configuration"]["mcuPayloadSha256"] == inputs()["expected_sha256"]
    for role in ("initial", "final"):
        source = evidence[role]
        assert source["source"] == "WORK_RESULT" and source["role"] == role
        measurement = source["measurement"]
        assert measurement["measurementUid"] == result[role + "MeasurementUid"]
        assert measurement["mcuEventSequence"] == result[role + "McuEventSequence"]
        assert measurement["reportedWeightGrams"] == result[role + "WeightGrams"]
    expected = ({"lockPowerState": "DEENERGIZED", "solenoidHealth": "UNKNOWN"}
        if case.clean else {"command": "CLOSE", "outputStatus": "COMMAND_DISPATCHED",
            "physicalDoorStateBasis": "NOT_OBSERVABLE"})
    assert evidence["finalControl"] == expected
    assert evidence["missing"] == []
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.list_pending_events() == []


def test_optional_process_and_button_rows_are_not_normal_result_prerequisites(complete_case):
    case = complete_case
    before = evaluate(case)
    case.store._conn.execute("PRAGMA foreign_keys=OFF")
    with case.store.transaction() as conn:
        for table in ("native_process_receipt", "native_delivery_selection", "native_clean_confirmation",
                      "native_clean_intent", "native_actuator_event", "native_measurement_event"):
            conn.execute(f"DELETE FROM {table}")
    case.store._conn.execute("PRAGMA foreign_keys=ON")
    assert evaluate(case) == before
    names = {row["message_name"] for row in case.store.list_native_commands()}
    assert names.isdisjoint({"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR"})


def test_lost_configuration_ack_does_not_erase_durable_original_configuration(complete_case):
    case = complete_case
    command_uids = [row["command_uid"] for row in case.store.list_native_commands()
        if row["message_name"].startswith("CONFIG_")]
    with case.store.transaction() as conn:
        for uid in command_uids:
            conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (uid,))
    evidence = evaluate(case)["evidence"]
    assert evidence["state"] == "MATCHED"
    assert evidence["configuration"]["configVersion"] == case.start["configVersion"]


@pytest.mark.parametrize("role", ["initial", "final"])
def test_self_consistent_result_cannot_change_original_calibration(complete_case, role):
    case = complete_case
    saved = stored_result(case)
    result = uart.decode_payload("WORK_RESULT", saved["payload"])
    rewrite_result(case.store, saved, **{role + "CalibrationVersion": result[role + "CalibrationVersion"] + 1})
    with pytest.raises(ValueError, match="calibration differs from original configuration"):
        evaluate(case)
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("field", [
    "mcuBootId", "workUid", "workType", "portNo", "configVersion",
    "originCommandUid", "originCommandSequence",
])
def test_valid_result_digest_cannot_replace_original_start_identity(complete_case, field):
    case = complete_case
    saved = stored_result(case)
    result = uart.decode_payload("WORK_RESULT", saved["payload"])
    if field == "mcuBootId":
        value = result[field] + 1
        changes = {field: value, "initialSourceMcuBootId": value, "finalSourceMcuBootId": value}
    elif field in {"workUid", "originCommandUid"}:
        changes = {field: str(uuid.uuid4())}
    elif field == "workType":
        changes = ({"workType": "DELIVERY_SESSION", "finishReason": "DELIVERY_END",
            "cleanActionSequence": 0, "physicalCloseConfirmed": False, "deliveryRoundCount": 1,
            "negativeWeightAnomaly": False} if case.clean else
            {"workType": "CLEAN_OPERATION", "finishReason": "CLEAN_CONFIRMED",
             "cleanActionSequence": 1, "physicalCloseConfirmed": True, "deliveryRoundCount": 0,
             "negativeWeightAnomaly": False})
    elif field == "portNo":
        changes = {field: 2}
    else:
        changes = {field: result[field] + 1}
    rewrite_result(case.store, saved, **changes)
    with pytest.raises(ValueError, match="original START"):
        evaluate(case)
    assert case.store.list_pending_events() == []


@pytest.mark.parametrize("field", [
    "finalWeightGrams", "finalElapsedMs", "finalSampleCount", "finalSpanGrams",
    "finalMeasurementUid", "finalMcuEventSequence",
])
def test_authoritative_result_metadata_is_not_replaced_by_optional_process_rows(complete_case, field):
    case = complete_case
    saved = stored_result(case)
    result = uart.decode_payload("WORK_RESULT", saved["payload"])
    replacement = str(uuid.uuid4()) if field == "finalMeasurementUid" else result[field] + 1
    raw = rewrite_result(case.store, saved, **{field: replacement})
    decision = evaluate(case)
    assert decision["result"]["payload"] == raw
    key = {
        "finalWeightGrams": "reportedWeightGrams", "finalElapsedMs": "measurementElapsedMs",
        "finalSampleCount": "sampleCount", "finalSpanGrams": "sampleSpanGrams",
        "finalMeasurementUid": "measurementUid", "finalMcuEventSequence": "mcuEventSequence",
    }[field]
    assert decision["evidence"]["final"]["measurement"][key] == replacement


def test_result_row_index_or_classification_task_corruption_is_never_hidden(complete_case):
    case = complete_case
    saved = stored_result(case)
    with case.store.transaction() as conn:
        conn.execute("UPDATE native_mcu_result SET work_uid=?", (str(uuid.uuid4()),))
    with pytest.raises(ValueError, match="original START"):
        evaluate(case)
    with case.store.transaction() as conn:
        conn.execute("UPDATE native_mcu_result SET work_uid=?", (case.permit.work_uid,))
        conn.execute("DELETE FROM native_result_report_outbox")
    with pytest.raises(ValueError, match="classification task"):
        evaluate(case)
    assert saved["payload"] == case.store._conn.execute("SELECT payload FROM native_mcu_result").fetchone()[0]


def test_result_and_original_permit_survive_pi_database_reopen(complete_case, tmp_path):
    from edge_store import EdgeStore

    case = complete_case
    before = evaluate(case)
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    case.store.initialize()
    assert evaluate(case) == before
    forged = replace(case.permit, permit_uid=str(uuid.uuid4()))
    with pytest.raises(ValueError, match="permit differs from the original work slot"):
        case.store.evaluate_native_work_recovery(
            forged, case.start["mcuCommandUid"], current_boot=lambda: None)
