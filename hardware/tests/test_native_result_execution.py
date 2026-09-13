"""rc.23 execution custody comes from START plus the MCU's complete result.

The current MCU owns door/lock progression and local button handling.  These
tests therefore exercise the autonomous C implementation without manufacturing
deprecated Pi-side action authorizations or treating optional process journals
as prerequisites for accepting an immutable ``WORK_RESULT``.
"""
import uuid

import pytest
import uart2_protocol as uart

from hardware.tests.native_autonomous_recovery_fixture import autonomous_active_case
from hardware.tests.native_confirmation_fixture import autonomous_result_case
from hardware.tests.test_mcu_simplified_execution import library, request, runtime, select
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_native_result_evidence import evaluate, rewrite_result, stored_result
from hardware.tests.test_simplified_mcu_pi_business import finish_delivery_round


OLD_ACTION_COMMANDS = {"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR"}
OPTIONAL_EXECUTION_TABLES = (
    "native_process_receipt",
    "native_delivery_selection",
    "native_clean_confirmation",
    "native_clean_intent",
    "native_actuator_event",
    "native_measurement_event",
)


def next_cycle(case, runtime):
    """Complete one current multi-step work without a second Pi command.

    Kept as a public test helper because neighbouring tests historically import
    this name.  The helper now invokes only local MCU button selections/requests;
    the sole native business command remains the original START.
    """
    wire = case.wire
    if case.clean:
        assert request(runtime, case.cleanup, case.start, wire.now, "CLEAN_UNLOCK_REQUESTED")
        wire.advance(inputs()["device"]["cleanSolenoidPulseMs"])
        assert request(runtime, case.cleanup, case.start, wire.now, "CLEAN_FINISH_REQUESTED")
        wire.now = take_samples(runtime, [900] * 5, start=wire.now, measurement=2)
    else:
        wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
        wire.now = take_samples(runtime, [700] * 5, start=wire.now, measurement=2)
        assert select(runtime, case.delivery, wire.now, "CONTINUE")
        wire.now = finish_delivery_round(runtime, case, wire.now, 3, 900)
        assert select(runtime, case.delivery, wire.now, "END")
    return wire.handoff_result()


@pytest.fixture(params=[False, True], ids=["delivery", "clean"])
def complete_case(runtime, tmp_path, request):
    samples = (100,) * 5 if request.param else (700,) * 5
    with autonomous_result_case(runtime, tmp_path, clean=request.param, samples=samples) as case:
        yield case


def command_names(case):
    return [row["message_name"] for row in case.store.list_native_commands()]


@pytest.mark.parametrize("clean", [False, True], ids=["delivery", "clean"])
def test_next_cycle_is_mcu_local_and_keeps_exactly_one_start(runtime, tmp_path, clean):
    with autonomous_active_case(runtime, tmp_path, clean=clean) as case:
        commands_before = case.store.list_native_commands()
        saved = next_cycle(case, runtime)
        result = uart.decode_payload("WORK_RESULT", saved["payload"])

        assert case.store.list_native_commands() == commands_before
        start = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
        assert command_names(case).count(start) == 1
        assert set(command_names(case)).isdisjoint(OLD_ACTION_COMMANDS)
        assert case.store.list_native_work_actuator_events(case.permit.work_uid) == []
        assert case.store.get_native_action_by_key(
            case.permit.work_uid,
            "clean:first-unlock" if clean else "delivery:first-open",
        ) is None
        assert result["initialWeightGrams"] == 500
        assert result["finalWeightGrams"] == 900
        assert result["initialMcuEventSequence"] < result["finalMcuEventSequence"]
        if clean:
            assert result["finishReason"] == "CLEAN_CONFIRMED"
            assert result["cleanActionSequence"] == 2
            assert result["deliveryRoundCount"] == 0
        else:
            assert result["finishReason"] == "DELIVERY_END"
            assert result["deliveryRoundCount"] == 2
            assert result["cleanActionSequence"] == 0


def test_complete_result_itself_projects_the_final_control(complete_case):
    case = complete_case
    decision = evaluate(case)
    evidence = decision["evidence"]
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert evidence["state"] == "MATCHED"
    assert "execution" not in evidence and "completion" not in evidence
    if case.clean:
        assert evidence["finalControl"] == {
            "lockPowerState": "DEENERGIZED",
            "solenoidHealth": "UNKNOWN",
        }
    else:
        assert evidence["finalControl"] == {
            "command": "CLOSE",
            "outputStatus": "COMMAND_DISPATCHED",
            "physicalDoorStateBasis": "NOT_OBSERVABLE",
        }


def test_optional_process_and_action_rows_do_not_gate_the_complete_result(complete_case):
    case = complete_case
    before = evaluate(case)
    case.store._conn.execute("PRAGMA foreign_keys=OFF")
    with case.store.transaction() as conn:
        for table in OPTIONAL_EXECUTION_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM native_action_binding")
    case.store._conn.execute("PRAGMA foreign_keys=ON")

    assert evaluate(case) == before
    assert set(command_names(case)).isdisjoint(OLD_ACTION_COMMANDS)
    assert case.store.list_native_work_actuator_events(case.permit.work_uid) == []


@pytest.mark.parametrize(
    "field",
    ["mcuBootId", "workUid", "portNo", "configVersion", "originCommandUid", "originCommandSequence"],
)
def test_self_consistent_result_cannot_replace_original_start_identity(complete_case, field):
    case = complete_case
    saved = stored_result(case)
    result = uart.decode_payload("WORK_RESULT", saved["payload"])
    if field == "mcuBootId":
        value = result[field] + 1
        changes = {
            field: value,
            "initialSourceMcuBootId": value,
            "finalSourceMcuBootId": value,
        }
    elif field in {"workUid", "originCommandUid"}:
        changes = {field: str(uuid.uuid4())}
    elif field == "portNo":
        changes = {field: 2}
    else:
        changes = {field: result[field] + 1}
    rewrite_result(case.store, saved, **changes)

    with pytest.raises(ValueError, match="original START"):
        evaluate(case)
    assert case.store.get_work_slot() == case.occupancy


def test_protocol_rejects_success_with_an_impossible_work_round(complete_case):
    case = complete_case
    result = uart.decode_payload("WORK_RESULT", stored_result(case)["payload"])
    if case.clean:
        result["cleanActionSequence"] = 0
    else:
        result["deliveryRoundCount"] = 0
    result["resultDigestSha256"] = uart.compute_result_digest(result)

    with pytest.raises(uart.ProtocolError, match="round|action sequence"):
        uart.encode_payload("WORK_RESULT", result)


@pytest.mark.parametrize("bad", ["same-sequence", "same-measurement"])
def test_protocol_rejects_unordered_result_measurements(complete_case, bad):
    case = complete_case
    result = uart.decode_payload("WORK_RESULT", stored_result(case)["payload"])
    if bad == "same-sequence":
        result["finalMcuEventSequence"] = result["initialMcuEventSequence"]
    else:
        result["finalMeasurementUid"] = result["initialMeasurementUid"]
    result["resultDigestSha256"] = uart.compute_result_digest(result)

    with pytest.raises(uart.ProtocolError, match="distinct and ordered"):
        uart.encode_payload("WORK_RESULT", result)


def test_result_digest_corruption_is_rejected_from_sqlite(complete_case):
    case = complete_case
    saved = stored_result(case)
    corrupted = bytearray(saved["payload"])
    corrupted[28] ^= 1
    with case.store.transaction() as conn:
        conn.execute(
            "UPDATE native_mcu_result SET payload=? WHERE mcu_boot_id=? AND result_sequence=?",
            (bytes(corrupted), saved["mcu_boot_id"], saved["result_sequence"]),
        )

    with pytest.raises(uart.ProtocolError, match="digest mismatch"):
        evaluate(case)


def test_same_result_identity_conflict_keeps_original_and_blocks_evaluation(complete_case):
    case = complete_case
    saved = stored_result(case)
    original = saved["payload"]
    changed = uart.decode_payload("WORK_RESULT", original)
    changed["finalWeightGrams"] += 1
    changed["resultDigestSha256"] = uart.compute_result_digest(changed)
    conflicting = uart.encode_payload("WORK_RESULT", changed)

    with pytest.raises(ValueError, match="identity conflict"):
        case.store.save_native_mcu_result(conflicting)
    assert case.store.get_native_mcu_result(
        saved["mcu_boot_id"], saved["result_sequence"]
    )["payload"] == original
    assert case.store.list_native_mcu_result_conflicts()[0]["payload"] == conflicting
    assert len(case.store.list_native_result_report_tasks()) == 1
    with pytest.raises(ValueError, match="unresolved conflict"):
        evaluate(case)


def test_repeated_identical_result_is_idempotent(complete_case):
    case = complete_case
    saved = stored_result(case)
    task = case.store.list_native_result_report_tasks()[0]

    receipt = case.store.save_native_mcu_result(saved["payload"])

    assert receipt["taskUid"] == task["task_uid"]
    assert case.store.list_native_mcu_result_conflicts() == []
    assert len(case.store.list_native_result_report_tasks()) == 1
    assert evaluate(case)["result"]["payload"] == saved["payload"]


@pytest.mark.parametrize("damage", ["result-index", "result-digest-index", "missing-task"])
def test_sqlite_damage_remains_visible_after_pi_reopen(complete_case, tmp_path, damage):
    from edge_store import EdgeStore

    case = complete_case
    saved = stored_result(case)
    with case.store.transaction() as conn:
        if damage == "result-index":
            conn.execute(
                "UPDATE native_mcu_result SET work_uid=?",
                (str(uuid.uuid4()),),
            )
        elif damage == "result-digest-index":
            conn.execute(
                "UPDATE native_mcu_result SET result_digest=?",
                ("00" * 32,),
            )
        else:
            conn.execute("DELETE FROM native_result_report_outbox")
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    case.store.initialize()

    expected = "classification task" if damage == "missing-task" else "original START"
    with pytest.raises(ValueError, match=expected):
        evaluate(case)
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.get_native_mcu_result(
        saved["mcu_boot_id"], saved["result_sequence"]
    ) is not None
