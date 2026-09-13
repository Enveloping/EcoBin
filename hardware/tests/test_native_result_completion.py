"""rc.23 terminal decisions come from the trusted complete WORK_RESULT."""
import pytest
import uart2_protocol as uart

from hardware.tests.native_autonomous_recovery_fixture import autonomous_active_case
from hardware.tests.native_confirmation_fixture import autonomous_result_case
from hardware.tests.test_mcu_simplified_execution import library, request, runtime, select, tick
from hardware.tests.test_simplified_mcu_pi_business import finish_delivery_round
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_native_result_evidence import evaluate, rewrite_result, stored_result


@pytest.fixture(params=[False, True], ids=["delivery", "clean"])
def complete_case(runtime, tmp_path, request):
    samples = (100,) * 5 if request.param else (700,) * 5
    with autonomous_result_case(runtime, tmp_path, clean=request.param, samples=samples) as case:
        yield case


def result_value(case):
    return uart.decode_payload("WORK_RESULT", stored_result(case)["payload"])


def test_complete_result_exposes_terminal_control_without_button_or_action_prerequisites(complete_case):
    case = complete_case
    decision = evaluate(case)
    evidence = decision["evidence"]
    assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
    assert "completion" not in evidence and "execution" not in evidence
    if case.clean:
        assert evidence["finalControl"] == {
            "lockPowerState": "DEENERGIZED", "solenoidHealth": "UNKNOWN"}
    else:
        assert evidence["finalControl"] == {
            "command": "CLOSE", "outputStatus": "COMMAND_DISPATCHED",
            "physicalDoorStateBasis": "NOT_OBSERVABLE"}
    names = {row["message_name"] for row in case.store.list_native_commands()}
    assert names.isdisjoint({"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR"})
    assert case.store.get_work_slot() == case.occupancy


def test_removing_optional_terminal_process_rows_does_not_change_completion(complete_case):
    case = complete_case
    before = evaluate(case)
    case.store._conn.execute("PRAGMA foreign_keys=OFF")
    with case.store.transaction() as conn:
        conn.execute("DELETE FROM native_delivery_selection")
        conn.execute("DELETE FROM native_clean_confirmation")
        conn.execute("DELETE FROM native_clean_intent")
        conn.execute("DELETE FROM native_process_receipt")
    case.store._conn.execute("PRAGMA foreign_keys=ON")
    assert evaluate(case) == before


def test_actual_delivery_window_expiry_keeps_distinct_terminal_reason(runtime, tmp_path):
    with autonomous_active_case(runtime, tmp_path, clean=False) as case:
        wire = case.wire
        wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
        wire.now = take_samples(runtime, [700] * 5, start=wire.now, measurement=2)
        wire.custody("WORK_POSTCLOSE_WEIGHT_READY", 1)
        wire.advance(0)
        wire.advance(case.start["continueDeliveryWaitMs"])
        wire.advance(0)
        saved = wire.handoff_result()
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finishReason"] == "DELIVERY_WINDOW_EXPIRED"
        assert evaluate(case)["evidence"]["finalControl"]["command"] == "CLOSE"
        assert case.store.get_work_slot() == case.occupancy


def test_actual_delivery_terminal_weight_timeout_is_complete_without_net_weight(runtime, tmp_path):
    with autonomous_active_case(runtime, tmp_path, clean=False) as case:
        saved = case.wire.finish_delivery(unavailable=True)
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finishReason"] == "FAILED"
        assert result["finalKind"] == "UNAVAILABLE"
        assert result["finalFaultCode"] == "WEIGHT_TIMEOUT"
        assert evaluate(case)["evidence"]["finalControl"] == {
            "command": "CLOSE", "outputStatus": "COMMAND_DISPATCHED",
            "physicalDoorStateBasis": "NOT_OBSERVABLE"}
        assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


def test_actual_clean_final_weight_timeout_keeps_failure_without_fabricating_weight_or_control(runtime, tmp_path):
    with autonomous_result_case(runtime, tmp_path, clean=True, samples=()) as case:
        result = result_value(case)
        assert result["physicalCloseConfirmed"] is True
        assert result["finalKind"] == "UNAVAILABLE"
        assert result["finalFaultCode"] == "WEIGHT_TIMEOUT"
        assert result["finalWeightGrams"] == 0
        evidence = evaluate(case)["evidence"]
        assert evidence["final"]["measurement"]["measurementKind"] == "UNAVAILABLE"
        assert evidence["finalControl"] is None


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("reason", ["FAILED", "CANCELLED"])
def test_failed_or_cancelled_result_does_not_invent_safe_terminal_control(runtime, tmp_path, clean, reason):
    samples = (100,) * 5 if clean else (700,) * 5
    with autonomous_result_case(runtime, tmp_path, clean=clean, samples=samples) as case:
        saved = stored_result(case)
        changes = {"finishReason": reason}
        if clean:
            changes["physicalCloseConfirmed"] = False
        rewrite_result(case.store, saved, **changes)
        decision = evaluate(case)
        assert decision["evidence"]["finalControl"] is None
        assert decision["result"]["payload"] == case.store.get_native_mcu_result(
            saved["mcu_boot_id"], saved["result_sequence"])["payload"]


@pytest.mark.parametrize("clean", [False, True])
def test_protocol_rejects_terminal_success_without_required_internal_facts(runtime, tmp_path, clean):
    samples = (100,) * 5 if clean else (700,) * 5
    with autonomous_result_case(runtime, tmp_path, clean=clean, samples=samples) as case:
        result = result_value(case)
        if clean:
            result.update(physicalCloseConfirmed=False)
        else:
            result.update(deliveryRoundCount=0)
        result["resultDigestSha256"] = uart.compute_result_digest(result)
        with pytest.raises(uart.ProtocolError):
            uart.encode_payload("WORK_RESULT", result)


def test_continuous_delivery_uses_only_latest_round_as_final_result(runtime, tmp_path):
    with autonomous_active_case(runtime, tmp_path, clean=False) as case:
        now = tick(runtime, case.wire.now, inputs()["device"]["deliveryDoorTravelWaitMs"])
        now = take_samples(runtime, [700] * 5, start=now, measurement=2)
        assert select(runtime, case.delivery, now, "CONTINUE")
        now = finish_delivery_round(runtime, case, now, 3, 900)
        assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        saved = case.wire.handoff_result()
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["deliveryRoundCount"] == 2
        assert result["initialWeightGrams"] == 500
        assert result["finalWeightGrams"] == 900
        assert evaluate(case)["evidence"]["final"]["measurement"]["reportedWeightGrams"] == 900


def test_clean_reopen_and_finish_use_local_requests_without_old_unlock_command(runtime, tmp_path):
    with autonomous_active_case(runtime, tmp_path, clean=True) as case:
        now = case.wire.now
        assert request(runtime, case.cleanup, case.start, now, "CLEAN_UNLOCK_REQUESTED")
        now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
        assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
        now = take_samples(runtime, [100] * 5, start=now, measurement=2)
        case.wire.now = now
        saved = case.wire.handoff_result()
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finishReason"] == "CLEAN_CONFIRMED"
        assert result["cleanActionSequence"] == 2
        assert result["finalWeightGrams"] == 100
        names = {row["message_name"] for row in case.store.list_native_commands()}
        assert "UNLOCK_CLEAN_DOOR" not in names


def test_terminal_result_summary_survives_pi_restart_without_replaying_start(complete_case, tmp_path):
    from edge_store import EdgeStore

    case = complete_case
    before = evaluate(case)
    commands = case.store.list_native_commands()
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    case.store.initialize()
    assert evaluate(case) == before
    assert case.store.list_native_commands() == commands
    names = [frame["messageName"] for frame in case.wire.sent]
    assert names.count("START_CLEAN_OPERATION" if case.clean else "START_DELIVERY_SESSION") == 1
