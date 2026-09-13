"""Actual C repeated clean pulses -> original button custody -> permanent ledger."""
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from job_safety import PhysicalAction, action_digest
from mcu_action_evidence import NativeActionReconciler, clean_unlock_action_key
from mcu_actuator_handoff import McuActuatorEventHandoff
from mcu_process_handoff import McuProcessEventHandoff
from mcu_session import McuBootSession, McuCommandDispatcher, NativePhysicalActionGate
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_mcu_work_preparation import library, runtime, original_scope, take_samples
from hardware.tests.test_native_action_reconciliation import restart_stores
from hardware.tests.test_command_processor import StoreBackedUpdaterClient
from job_safety import JobSafetyError, PermanentJobSafety
from local_control import LocalControlUnavailable

ROOT = Path(__file__).resolve().parents[2]


class CleanWire:
    """Injected serial and clock boundaries; the controller is the actual C DLL."""
    def __init__(self, case, runtime):
        self.case, self.runtime, self.now = case, runtime, case.now
        self.sent = []

    def write(self, frame):
        lib, endpoint, _, replies, *_ = self.runtime
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        values = uart.decode_payload(decoded["messageName"], decoded["payload"])
        self.sent.append((decoded["messageName"], values))
        if decoded["messageName"] == "UNLOCK_CLEAN_DOOR":
            uid = values["mcuCommandUid"]
            assert self.case.store.get_native_action_binding(uid)
            assert self.case.store.get_native_command(uid)["write_claimed"] == 1
            assert self.case.safety.get_physical_action(uid)["state"] == "ARMED"
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), self.now)
        return len(frame)

    def pump(self, client):
        while self.runtime[3]:
            client.accept_frame(self.runtime[3].pop(0), self.now)

    def advance(self, elapsed):
        lib, endpoint, preparation, *_ = self.runtime
        lib.RuntimeClock_Advance(elapsed)
        lib.ActuatorRuntime_Tick()
        self.now += elapsed
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, self.now)

    def boot(self):
        boot = McuBootSession(self.case.store, self.write)
        boot.poll(self.now)
        self.pump(boot)
        assert boot.current_boot(self.now) == 1
        return boot

    def intent(self, after, message="CLEAN_UNLOCK_REQUESTED"):
        case = self.case
        lib, endpoint, *_ = self.runtime
        assert lib.McuCleanExecution_Request(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
            uart.MESSAGE_SPECS[message]["id"], after, self.now)
        return self.custody(message, after + 1)

    def custody(self, message, step):
        case = self.case
        scope = original_scope(case.start, clean=True) | dict(eventMessageType=message,
            stepSequence=step, configVersion=case.start["configVersion"])
        del scope["queryId"]
        client = McuProcessEventHandoff(case.store, self.write, scope)
        client.poll(self.now)
        self.pump(client)
        return case.store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope | {"queryId": 1})[8:])

    def prepare_reopen(self, intent, *, uid=None, key=None, manual=False):
        case = self.case
        cause = uart.decode_payload(intent["message_name"], intent["payload"])
        remaining = case.initial["uptimeMs"] + case.opening["remainingOperationWindowMs"] - self.now
        if not manual and uid is None and key is None:
            binding = case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=remaining)
            return SimpleNamespace(action=binding["action"],
                command=case.store.get_native_command(binding["action"].action_uid), intent=intent)
        values = dict(case.opening, mcuCommandUid=uid or str(uuid.uuid4()), cleanActionSequence=cause["cleanActionSequence"],
            remainingOperationWindowMs=remaining)
        identity = {"mcuCommandUid", "targetMcuBootId", "commandSequence", "commandDigestSha256"}
        record = case.store.prepare_native_command("UNLOCK_CLEAN_DOOR", values["mcuCommandUid"], 1,
            {name: value for name, value in values.items() if name not in identity})
        key = key or clean_unlock_action_key(values)
        digest = action_digest(work_uid=case.permit.work_uid, command_uid=case.permit.command_uid,
            action_key=key, action_kind=record["message_name"], payload={"nativeUartPayloadHex": record["payload"].hex()})
        action = PhysicalAction(record["command_uid"], str(uuid.uuid4()), key, record["message_name"], digest)
        return SimpleNamespace(action=action, command=record, intent=intent)

    def dispatch(self, reopen, *, restart=False):
        case = self.case
        def original(candidate):
            assert candidate["payload"] == reopen.command["payload"]
            assert case.store.get_work_slot() == case.occupancy
            assert case.store.get_native_process_receipt(reopen.intent["scope"]) == reopen.intent
        gate = NativePhysicalActionGate(case.safety, case.permit, reopen.action, store=case.store,
            revalidate=original, deadline_ms=self.now + 10000, clock=lambda: self.now)
        dispatcher = McuCommandDispatcher(case.store, self.boot(), self.write, arm=gate, clock=lambda: self.now)
        assert dispatcher.send_once(reopen.action.action_uid)
        if restart:
            self.runtime[3].clear()
            path = case.store.db_path
            case.store.close()
            case.store = EdgeStore(path)
            case.store.initialize()
            dispatcher = McuCommandDispatcher(case.store, self.boot(), self.write,
                arm=lambda _: pytest.fail("old unlock must not be rearmed"), clock=lambda: self.now)
            assert not dispatcher.send_once(reopen.action.action_uid)
            dispatcher.poll(reopen.action.action_uid, self.now)
        self.pump(dispatcher)
        assert case.store.get_native_command(reopen.action.action_uid)["decision_outcome"] == "ACCEPTED"

    def save_outputs(self):
        self.advance(self.case.opening["unlockPulseMs"])
        client = McuActuatorEventHandoff(self.case.store, self.write, 1)
        for _ in range(3):
            client.poll(self.now)
            self.pump(client)
            self.advance(1000)


@pytest.fixture
def active(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=True) as case:
        case.first_proof = NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
        case.wire = CleanWire(case, runtime)
        case.wire.advance(40000 - case.wire.now)  # Original START deadline passed, clean window still open.
        yield case


@pytest.mark.parametrize("restart", [False, True])
def test_saved_reopen_intent_has_its_own_single_output_proof_without_ending_clean(active, restart):
    case, wire = active, active.wire
    intent = wire.intent(0)
    reopen = wire.prepare_reopen(intent)
    wire.dispatch(reopen, restart=restart)
    wire.save_outputs()
    proof = NativeActionReconciler(case.store, case.safety).reconcile(reopen.action.action_uid)
    assert proof["state"] == "CONFIRMED" and proof["evidence_sha256"] != case.first_proof["evidence_sha256"]
    bundle = json.loads(proof["bundle_json"])
    assert bundle["reopenIntent"]["payloadHex"] == intent["payload"].hex()
    assert bundle["reopenIntent"]["scopeHex"] == intent["scope"].hex()
    assert case.store.get_native_action_confirmation(case.action.action_uid) == case.first_proof
    assert case.safety.get_physical_action(reopen.action.action_uid)["receiptUid"] == reopen.action.receipt_uid
    assert sum(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent) == 1
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert case.store.list_native_result_report_tasks() == []


def test_same_button_cannot_be_assigned_an_arbitrary_logical_key_before_arm(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    reopen = wire.prepare_reopen(intent, key="unrelated-second-action")
    with pytest.raises(ValueError, match="logical.*conflict"):
        wire.dispatch(reopen)
    assert case.store.get_native_action_binding(reopen.action.action_uid) is None
    assert not any(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent)


def test_reopen_waits_for_first_unlock_ledger_reconciliation_before_new_arm(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    reopen = wire.prepare_reopen(intent, manual=True)
    # Simulate the ordinary lost-local-confirmation boundary of the first pulse.
    with case.store.transaction() as conn:
        conn.execute("UPDATE native_action_confirmation SET state='PENDING' WHERE action_uid=?", (case.action.action_uid,))
    with pytest.raises(ValueError, match="first.*confirmed"):
        wire.dispatch(reopen)
    assert case.store.get_native_action_binding(reopen.action.action_uid) is None
    assert not any(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent)
    assert NativeActionReconciler(case.store, case.safety).reconcile_pending() == [case.first_proof]
    wire.dispatch(reopen)
    wire.save_outputs()
    assert NativeActionReconciler(case.store, case.safety).reconcile(reopen.action.action_uid)["state"] == "CONFIRMED"


def test_unscoped_or_missing_reopen_intent_cannot_create_an_action_binding(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    reopen = wire.prepare_reopen(intent, manual=True)
    # Missing custody cannot be replaced by the old Python object in memory.
    with case.store.transaction() as conn:
        conn.execute("DELETE FROM native_clean_intent WHERE scope=?", (intent["scope"],))
    with pytest.raises(ValueError, match="saved.*intent"):
        case.store.bind_native_action(case.permit, reopen.action)
    assert case.store.get_native_action_binding(reopen.action.action_uid) is None


def test_two_actual_buttons_each_reopen_once_and_keep_all_three_original_receipts(active):
    case, wire = active, active.wire
    proofs = [case.first_proof]
    for after in (0, 1):
        intent = wire.intent(after)
        reopen = wire.prepare_reopen(intent)
        wire.dispatch(reopen, restart=True)
        wire.save_outputs()
        proof = NativeActionReconciler(case.store, case.safety).reconcile(reopen.action.action_uid)
        assert json.loads(proof["bundle_json"])["reopenIntent"]["payloadHex"] == intent["payload"].hex()
        proofs.append(proof)
    assert len({item["action_uid"] for item in proofs}) == 3
    assert len({item["evidence_sha256"] for item in proofs}) == 3
    assert all(case.store.get_native_action_confirmation(item["action_uid"]) == item for item in proofs)
    assert sum(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent) == 2
    assert case.store.get_work_slot() == case.occupancy


def test_one_saved_intent_cannot_back_a_second_command_identity_even_after_success(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    first = wire.prepare_reopen(intent)
    wire.dispatch(first)
    wire.save_outputs()
    NativeActionReconciler(case.store, case.safety).reconcile(first.action.action_uid)
    duplicate = wire.prepare_reopen(intent, manual=True)
    with pytest.raises(sqlite3.IntegrityError):
        wire.dispatch(duplicate)
    assert case.store.get_native_action_binding(duplicate.action.action_uid) is None
    assert sum(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent) == 1


def test_reopen_after_saved_final_weight_then_new_human_confirmation_preserves_correct_final_result(active):
    from mcu_work_query import McuWorkQuery
    from mcu_result_handoff import McuResultHandoff
    case, wire = active, active.wire
    wire.intent(0, "CLEAN_FINISH_REQUESTED")
    wire.advance(0)
    wire.now = take_samples(wire.runtime, [123] * 5, start=wire.now, measurement=2)
    old = wire.custody("CLEAN_FINAL_WEIGHT_READY", 1)
    reopen = wire.prepare_reopen(wire.intent(1))
    wire.dispatch(reopen, restart=True)
    wire.save_outputs()
    proof = NativeActionReconciler(case.store, case.safety).reconcile(reopen.action.action_uid)
    assert proof["state"] == "CONFIRMED"
    wire.intent(2, "CLEAN_FINISH_REQUESTED")
    wire.advance(0)
    wire.now = take_samples(wire.runtime, [456] * 5, start=wire.now, measurement=3)
    final = wire.custody("CLEAN_FINAL_WEIGHT_READY", 3)
    last = uart.decode_payload("CLEAN_FINAL_WEIGHT_READY", final["payload"])
    lib, endpoint, *_ = wire.runtime
    assert lib.McuCleanExecution_Confirm(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
        3, uuid.UUID(last["measurementUid"]).bytes, wire.now)
    wire.custody("CLEAN_COMPLETION_CONFIRMED", 3)
    wire.advance(0)
    original = original_scope(case.start, clean=True)
    del original["queryId"]
    query = McuWorkQuery(case.store, wire.write, original)
    query.poll(wire.now)
    wire.pump(query)
    status = query.observation(wire.now)
    assert status["status"] == "RESULT_HELD"
    identity = dict(mcuBootId=1, workUid=case.permit.work_uid, resultSequence=status["resultSequence"],
        resultDigestSha256=status["resultDigestSha256"])
    handoff = McuResultHandoff(case.store, wire.write, identity)
    handoff.poll(wire.now)
    wire.pump(handoff)
    result = uart.decode_payload("WORK_RESULT", case.store.get_native_mcu_result(1, identity["resultSequence"])["payload"])
    assert result["initialWeightGrams"] == 500 and result["finalWeightGrams"] == 456
    assert result["finalMeasurementUid"] == last["measurementUid"]
    assert result["cleanActionSequence"] == 3 and result["physicalCloseConfirmed"]
    assert result["finishReason"] == "CLEAN_CONFIRMED"
    assert case.store.get_native_process_receipt(old["scope"])["payload"] == old["payload"]
    assert case.store.get_native_action_confirmation(reopen.action.action_uid) == proof
    assert case.store.get_native_action_confirmation(case.action.action_uid) == case.first_proof
    assert case.store.get_work_slot() == case.occupancy
    assert [item["state"] for item in case.store.list_native_result_report_tasks()] == ["PENDING_CLASSIFICATION"]
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_preparing_same_saved_button_after_restart_returns_original_command_and_receipt(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    before = len(case.store.list_native_commands())
    binding = case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=100000)
    path = case.store.db_path
    case.store.close()
    case.store = EdgeStore(path)
    case.store.initialize()
    restored = case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=90000)
    assert restored == binding
    assert len(case.store.list_native_commands()) == before + 1
    assert uart.decode_payload("UNLOCK_CLEAN_DOOR", restored["command_payload"])["remainingOperationWindowMs"] == 100000
    assert not any(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent)
    action = restored["action"]
    reopen = SimpleNamespace(action=action, command=case.store.get_native_command(action.action_uid), intent=intent)
    wire.dispatch(reopen, restart=True)
    wire.save_outputs()
    assert NativeActionReconciler(case.store, case.safety).reconcile_pending()[0]["action_uid"] == action.action_uid
    assert case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=1) == binding
    assert sum(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent) == 1


@pytest.mark.parametrize("boundary", ["before-command", "after-command", "before-binding", "after-binding", "after-commit"])
def test_process_exit_cannot_leave_a_half_prepared_reopen_or_consume_a_second_command(active, boundary):
    case, wire = active, active.wire
    intent = wire.intent(0)
    before = case.store.list_native_commands()
    path = case.store.db_path
    case.store.close()
    child = r'''
import json, os, sys
from edge_store import EdgeStore
from job_safety import JobPermit
path, scope, permit, boundary = sys.argv[1:]
store = EdgeStore(path); store.initialize()
store._conn.create_function('crash_now', 0, lambda: os._exit(77))
if boundary != 'after-commit':
    timing = 'BEFORE' if boundary.startswith('before') else 'AFTER'
    table = 'native_mcu_command' if boundary.endswith('command') else 'native_action_binding'
    store._conn.execute('CREATE TEMP TRIGGER crash_prepare ' + timing + ' INSERT ON ' + table + ' BEGIN SELECT crash_now(); END')
binding = store.prepare_native_clean_reopen(JobPermit(**json.loads(permit)), bytes.fromhex(scope), remaining_operation_window_ms=100000)
assert binding['action'].action_uid and boundary == 'after-commit'
os._exit(77)
'''
    result = subprocess.run([sys.executable, "-c", child, str(path), intent["scope"].hex(), json.dumps(asdict(case.permit)), boundary],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=20)
    assert result.returncode == 77, result.stdout + result.stderr
    restart_stores(case)
    assert len(case.store.list_native_commands()) == len(before) + int(boundary == "after-commit")
    binding = case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=90000)
    record = case.store.get_native_command(binding["action"].action_uid)
    assert record["command_sequence"] == before[-1]["command_sequence"] + 1
    assert record["write_claimed"] == 0
    assert len(case.store.list_native_commands()) == len(before) + 1
    assert case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=80000) == binding
    assert case.store.get_work_slot() == case.occupancy and case.store.integrity_check()
    assert not any(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent)


def test_two_database_connections_prepare_exactly_one_action_for_one_button(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    before = len(case.store.list_native_commands())
    readers = [EdgeStore(case.store.db_path), EdgeStore(case.store.db_path)]
    for reader in readers:
        reader.initialize()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda reader: reader.prepare_native_clean_reopen(case.permit, intent["scope"],
                remaining_operation_window_ms=100000), readers))
        assert results[0] == results[1]
        assert len(case.store.list_native_commands()) == before + 1
    finally:
        for reader in readers:
            reader.close()


@pytest.mark.parametrize("remaining", [0, -1, True, 1.5, 0x100000000, 200001])
def test_invalid_or_extended_window_cannot_leave_a_prepared_action(active, remaining):
    case, wire = active, active.wire
    intent = wire.intent(0)
    before = case.store.list_native_commands()
    with pytest.raises(ValueError):
        case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=remaining)
    assert case.store.list_native_commands() == before


def test_finish_button_is_not_reinterpreted_as_an_unlock_request(active):
    case, wire = active, active.wire
    finish = wire.intent(0, "CLEAN_FINISH_REQUESTED")
    before = case.store.list_native_commands()
    with pytest.raises(ValueError, match="original unlock intent"):
        case.store.prepare_native_clean_reopen(case.permit, finish["scope"], remaining_operation_window_ms=100000)
    assert case.store.list_native_commands() == before


def test_missing_first_confirmation_rolls_back_both_new_command_and_binding(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    before = case.store.list_native_commands()
    with case.store.transaction() as conn:
        conn.execute("UPDATE native_action_confirmation SET state='PENDING' WHERE action_uid=?", (case.action.action_uid,))
    with pytest.raises(ValueError, match="first.*confirmed"):
        case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=100000)
    assert case.store.list_native_commands() == before
    assert NativeActionReconciler(case.store, case.safety).reconcile_pending() == [case.first_proof]
    assert case.store.prepare_native_clean_reopen(case.permit, intent["scope"], remaining_operation_window_ms=90000)


@pytest.mark.parametrize("commit", [False, True], ids=["request-lost", "reply-lost"])
def test_reopen_confirmation_recovers_original_button_and_receipt_after_both_stores_restart(active, commit):
    case, wire = active, active.wire
    reopen = wire.prepare_reopen(wire.intent(0))
    wire.dispatch(reopen)
    wire.save_outputs()
    class InterruptedRpc:
        def request(self, operation, payload):
            if operation == "CONFIRM_PHYSICAL_ACTION":
                if commit:
                    StoreBackedUpdaterClient(case.updater).request(operation, payload)
                raise LocalControlUnavailable("synthetic reopen confirmation interruption")
            return StoreBackedUpdaterClient(case.updater).request(operation, payload)
    with pytest.raises(JobSafetyError):
        NativeActionReconciler(case.store, PermanentJobSafety(InterruptedRpc())).reconcile_pending()
    pending = case.store.get_native_action_confirmation(reopen.action.action_uid)
    assert pending["state"] == "PENDING"
    restart_stores(case)
    assert NativeActionReconciler(case.store, case.safety).reconcile_pending() == [pending | {"state": "CONFIRMED"}]
    assert case.safety.get_physical_action(reopen.action.action_uid)["receiptUid"] == reopen.action.receipt_uid
    assert case.store.get_native_action_confirmation(case.action.action_uid) == case.first_proof
    assert case.store.get_work_slot() == case.occupancy
    assert sum(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_conflicting_button_custody_blocks_reopen_ledger_confirmation_after_restart(active, pending):
    case, wire = active, active.wire
    intent = wire.intent(0)
    reopen = wire.prepare_reopen(intent)
    wire.dispatch(reopen)
    wire.save_outputs()
    if pending:
        case.store.prepare_native_action_confirmation(reopen.action.action_uid)
    value = uart.decode_payload(intent["message_name"], intent["payload"])
    raw = uart.encode_payload(intent["message_name"], value | {"uptimeMs": value["uptimeMs"] + 1})
    with pytest.raises(ValueError, match="conflict"):
        case.store.save_native_process_receipt(intent["scope"], intent["message_name"], raw)
    restart_stores(case)
    with pytest.raises(ValueError, match="conflict"):
        NativeActionReconciler(case.store, case.safety).reconcile_pending()
    assert case.safety.get_physical_action(reopen.action.action_uid)["state"] == "ARMED"
    assert any(row["payload"] == raw for row in case.store.list_native_actuator_event_conflicts())
    assert case.store.get_work_slot() == case.occupancy


def test_actual_interrupted_reopen_pulse_is_not_declared_normal_execution(active):
    case, wire = active, active.wire
    reopen = wire.prepare_reopen(wire.intent(0))
    wire.dispatch(reopen)
    wire.advance(100)
    wire.runtime[0].ActuatorRuntime_StopForUpdate()
    wire.advance(0)
    wire.save_outputs()
    outputs = case.store.list_native_action_actuator_events(reopen.action.action_uid, "CLEAN_LOCK_POWER_CHANGED")
    values = [uart.decode_payload(row["message_name"], row["payload"]) for row in outputs]
    assert [row["lockPowerState"] for row in values] == ["ENERGIZED", "DEENERGIZED"]
    assert values[1]["uptimeMs"] - values[0]["uptimeMs"] == 100
    assert NativeActionReconciler(case.store, case.safety).reconcile_pending() == []
    assert case.store.get_native_action_confirmation(reopen.action.action_uid) is None
    assert case.safety.get_physical_action(reopen.action.action_uid)["state"] == "ARMED"
    assert case.store.get_work_slot() == case.occupancy and case.store.list_native_result_report_tasks() == []


def test_conflict_in_first_pulse_prevents_preparing_a_later_unlock(active):
    case, wire = active, active.wire
    intent = wire.intent(0)
    original = case.store.get_native_actuator_event(1, 3)
    value = uart.decode_payload(original["message_name"], original["payload"])
    changed = uart.encode_payload(original["message_name"], value | {"uptimeMs": value["uptimeMs"] + 1})
    with pytest.raises(ValueError, match="conflict"):
        case.store.save_native_actuator_event(original["message_name"], changed)
    before = case.store.list_native_commands()
    with pytest.raises(ValueError, match="conflict"):
        wire.prepare_reopen(intent)
    assert case.store.list_native_commands() == before
    assert case.store.get_native_action_confirmation(case.action.action_uid) == case.first_proof
    assert not any(name == "UNLOCK_CLEAN_DOOR" for name, _ in wire.sent)
