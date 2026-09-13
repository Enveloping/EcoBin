"""Native exact command/action binding and permanent-ledger reconciliation."""
import json
import os
import subprocess
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from mcu_session import McuCommandDispatcher, NativePhysicalActionGate
from job_safety import JobSafetyError, PermanentJobSafety, PhysicalAction, action_digest
from local_control import LocalControlUnavailable
from updater_store import UpdaterStore
from mcu_action_evidence import NativeActionReconciler
from hardware.tests.test_native_command_session import ready, mcu, COMMAND_UID, WORK_UID
from hardware.tests.test_command_processor import make_real_job_safety, StoreBackedUpdaterClient
from hardware.tests.test_job_safety import _command
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_mcu_work_preparation import library, runtime

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=[False, True], ids=["delivery", "clean"])
def case(runtime, tmp_path, request):
    with executed_action_case(runtime, tmp_path, clean_work=request.param) as value:
        yield value


def restart_stores(case):
    path = case.store.db_path
    case.store.close()
    case.updater.close()
    case.store = EdgeStore(path)
    case.store.initialize()
    case.updater = UpdaterStore(Path(path).parent / "updater.db", release_version="stage4-test", enable_stage4_candidate=True)
    case.updater.initialize()
    case.safety = PermanentJobSafety(StoreBackedUpdaterClient(case.updater))


@pytest.mark.parametrize("commit", [False, True], ids=["request-lost", "reply-lost"])
def test_committed_proof_and_original_receipt_survive_separate_store_restart(case, commit):
    seen = []
    class InterruptedRpc:
        def request(self, operation, payload):
            if operation == "CONFIRM_PHYSICAL_ACTION":
                reader = EdgeStore(case.store.db_path)
                reader.initialize()
                try:
                    proof = reader.get_native_action_confirmation(case.action.action_uid)
                    assert proof["state"] == "PENDING"
                    assert proof["evidence_sha256"] == payload["evidenceDigestSha256"]
                    seen.append(dict(payload))
                finally:
                    reader.close()
                if commit:
                    StoreBackedUpdaterClient(case.updater).request(operation, payload)
                raise LocalControlUnavailable("simulated local RPC disconnect")
            return StoreBackedUpdaterClient(case.updater).request(operation, payload)
    safety = PermanentJobSafety(InterruptedRpc())
    with pytest.raises(JobSafetyError):
        NativeActionReconciler(case.store, safety).reconcile(case.action.action_uid)
    pending = case.store.get_native_action_confirmation(case.action.action_uid)
    assert pending["state"] == "PENDING" and seen
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == ("CONFIRMED" if commit else "ARMED")
    restart_stores(case)
    confirmed = NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert confirmed == pending | {"state": "CONFIRMED"}
    assert NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid) == confirmed
    assert case.safety.get_physical_action(case.action.action_uid)["receiptUid"] == case.action.receipt_uid
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert case.sent.count(case.action_name) == 1


@pytest.mark.parametrize("missing", ["decision", "start-decision", "initial-receipt", "open-output", "close-output"])
def test_partial_evidence_never_confirms_armed_action_or_releases_work(case, missing):
    # Deliberately remove a durable input to model interrupted/missing custody.
    with case.store.transaction() as conn:
        if missing in {"decision", "start-decision"}:
            uid = case.action.action_uid if missing == "decision" else case.start["mcuCommandUid"]
            conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (uid,))
        elif missing == "initial-receipt":
            conn.execute("DELETE FROM native_process_receipt WHERE scope=?", (case.scope,))
        else:
            conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=1 AND event_sequence=?",
                (2 if missing == "open-output" else 3,))
    assert NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid) is None
    assert case.store.get_native_action_confirmation(case.action.action_uid) is None
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("pending", [False, True])
def test_conflicting_raw_output_is_retained_and_blocks_original_proof(case, pending):
    if pending:
        case.store.prepare_native_action_confirmation(case.action.action_uid)
    row = case.store.get_native_actuator_event(1, 3)
    value = uart.decode_payload(row["message_name"], row["payload"])
    changed = uart.encode_payload(row["message_name"], value | {"uptimeMs": value["uptimeMs"] + 1})
    with pytest.raises(ValueError, match="conflict"):
        case.store.save_native_actuator_event(row["message_name"], changed)
    restart_stores(case)
    with pytest.raises(ValueError, match="conflict"):
        NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert any(item["payload"] == changed for item in case.store.list_native_actuator_event_conflicts())
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"
    assert case.store.get_work_slot() == case.occupancy


def test_rejected_original_query_overrides_an_unconfirmed_success_candidate(case):
    case.store.prepare_native_action_confirmation(case.action.action_uid)
    identity = {key: case.opening[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId")}
    identity["commandSequence"] = case.store.get_native_command(case.action.action_uid)["command_sequence"]
    raw = uart.encode_payload("COMMAND_DECISION", identity | dict(currentMcuBootId=1, outcome="REJECTED", errorCode="BUSY"))
    assert case.store.save_native_command_observation("COMMAND_DECISION", raw)
    with pytest.raises(ValueError, match="conflict"):
        NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"


def test_new_identical_acceptance_query_does_not_change_pending_proof_digest(case):
    pending = case.store.prepare_native_action_confirmation(case.action.action_uid)
    record = case.store.get_native_command(case.action.action_uid)
    value = uart.decode_payload(record["message_name"], record["payload"])
    identity = {key: value[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}
    raw = uart.encode_payload("COMMAND_DECISION", identity | dict(currentMcuBootId=1, outcome="ACCEPTED", errorCode="NONE"))
    assert case.store.save_native_command_observation("COMMAND_DECISION", raw)
    assert case.store.prepare_native_action_confirmation(case.action.action_uid) == pending
    assert NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid) == pending | {"state": "CONFIRMED"}


def test_first_not_seen_then_actual_acceptance_is_not_a_later_contradiction(case):
    # Reconstruct the real receive order: a query can precede delayed reception.
    record = case.store.get_native_command(case.action.action_uid)
    observations = case.store.list_native_command_observations(case.action.action_uid)
    with case.store.transaction() as conn:
        conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (case.action.action_uid,))
        conn.execute("UPDATE native_mcu_command SET decision_outcome=NULL,decision_error=NULL WHERE command_uid=?", (case.action.action_uid,))
    value = uart.decode_payload(record["message_name"], record["payload"])
    identity = {key: value[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}
    raw = uart.encode_payload("COMMAND_QUERY_RESULT", identity | dict(queryId=999, currentMcuBootId=1,
        outcome="NOT_SEEN", errorCode="NONE", highestCommandSequence=record["command_sequence"] - 1))
    assert case.store.save_native_command_observation("COMMAND_QUERY_RESULT", raw)
    for item in observations:
        assert case.store.save_native_command_observation(item["message_name"], item["payload"])
    assert not case.store.get_native_command(case.action.action_uid)["conflict"]
    assert NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)["state"] == "CONFIRMED"


@pytest.mark.parametrize("boundary", ["before-proof", "during-proof", "after-proof", "after-ledger", "before-local", "during-local", "after-local"])
def test_actual_process_exit_between_two_database_commits_is_recoverable(case, boundary):
    path = case.store.db_path
    case.store.close()
    case.updater.close()
    child = r'''
import os, sys
from pathlib import Path
from edge_store import EdgeStore
from updater_store import UpdaterStore
from job_safety import PermanentJobSafety
from mcu_action_evidence import NativeActionReconciler
path, uid, boundary = sys.argv[1:]
store = EdgeStore(path); store.initialize()
updater = UpdaterStore(Path(path).parent / 'updater.db', release_version='stage4-test', enable_stage4_candidate=True)
updater.initialize()
store._conn.create_function('crash_now', 0, lambda: os._exit(77))
if boundary in {'before-proof', 'during-proof', 'before-local', 'during-local'}:
    timing = 'BEFORE' if boundary.startswith('before') else 'AFTER'
    operation = 'INSERT' if boundary.endswith('proof') else 'UPDATE'
    store._conn.execute('CREATE TEMP TRIGGER crash_effect ' + timing + ' ' + operation +
        ' ON native_action_confirmation BEGIN SELECT crash_now(); END')
class Rpc:
    def request(self, operation, payload):
        if boundary == 'after-proof' and operation == 'GET_JOB_PERMIT': os._exit(77)
        result = {'GET_JOB_PERMIT':updater.get_job_permit, 'GET_PHYSICAL_ACTION':updater.get_physical_action,
            'CONFIRM_PHYSICAL_ACTION':updater.confirm_physical_action}[operation](payload)
        if boundary == 'after-ledger' and operation == 'CONFIRM_PHYSICAL_ACTION': os._exit(77)
        return result
assert NativeActionReconciler(store, PermanentJobSafety(Rpc())).reconcile(uid)['state'] == 'CONFIRMED'
assert boundary == 'after-local'
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-c", child, str(path), case.action.action_uid, boundary],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=20)
    assert run.returncode == 77, run.stdout + run.stderr
    restart_stores(case)
    proof = case.store.get_native_action_confirmation(case.action.action_uid)
    if boundary in {"before-proof", "during-proof"}:
        assert proof is None
    else:
        assert proof["state"] == ("CONFIRMED" if boundary == "after-local" else "PENDING")
    before = case.safety.get_physical_action(case.action.action_uid)
    assert before["state"] == ("ARMED" if boundary in {"before-proof", "during-proof", "after-proof"} else "CONFIRMED")
    confirmed = NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert confirmed["state"] == "CONFIRMED"
    if proof:
        assert proof["evidence_sha256"] == confirmed["evidence_sha256"]
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.integrity_check()
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert case.sent.count(case.action_name) == 1


def test_conflict_arriving_during_external_commit_cannot_clear_work_or_erase_proof(case):
    class ConcurrentCustody:
        def request(self, operation, payload):
            result = StoreBackedUpdaterClient(case.updater).request(operation, payload)
            if operation == "CONFIRM_PHYSICAL_ACTION":
                row = case.store.get_native_actuator_event(1, 3)
                value = uart.decode_payload(row["message_name"], row["payload"])
                raw = uart.encode_payload(row["message_name"], value | {"uptimeMs": value["uptimeMs"] + 1})
                with pytest.raises(ValueError, match="conflict"):
                    case.store.save_native_actuator_event(row["message_name"], raw)
            return result
    with pytest.raises(ValueError, match="conflict"):
        NativeActionReconciler(case.store, PermanentJobSafety(ConcurrentCustody())).reconcile(case.action.action_uid)
    # Separate stores cannot atomically prevent a concurrent later contradiction.
    # Keep the first confirmed historical proof AND the new contradictory bytes.
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == "CONFIRMED"
    assert case.store.get_native_action_confirmation(case.action.action_uid)["state"] == "PENDING"
    restart_stores(case)
    with pytest.raises(ValueError, match="conflict"):
        NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert case.store.list_native_actuator_event_conflicts()
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("change", ["boot", "work", "port", "reverse-time", "short-duration", "third-output"])
def test_well_formed_but_unrelated_or_incomplete_outputs_do_not_confirm(case, change):
    row = case.store.get_native_actuator_event(1, 3)
    value = uart.decode_payload(row["message_name"], row["payload"])
    first = case.store.get_native_actuator_event(1, 2)
    at = uart.decode_payload(first["message_name"], first["payload"])["uptimeMs"]
    modifications = {"boot": {"mcuBootId": 2}, "work": {"operationUid" if case.clean else "sessionUid": "99999999-9999-4999-8999-999999999999"},
        "port": {"portNo": 2}, "reverse-time": {"uptimeMs": at - 1},
        "short-duration": {"uptimeMs": at + (case.opening["unlockPulseMs"] if case.clean else case.start["deliveryAutoCloseMs"]) - 1},
        "third-output": {"mcuEventSequence": 4, "uptimeMs": value["uptimeMs"] + 1}}
    if change != "third-output":
        with case.store.transaction() as conn:
            conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=1 AND event_sequence=3")
    case.store.save_native_actuator_event(row["message_name"], uart.encode_payload(row["message_name"], value | modifications[change]))
    reconciler = NativeActionReconciler(case.store, case.safety)
    if change == "short-duration":
        assert reconciler.reconcile(case.action.action_uid) is None
    else:
        with pytest.raises(ValueError):
            reconciler.reconcile(case.action.action_uid)
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("field", ["receipt_uid", "action_key"])
def test_restart_cannot_replace_original_binding_even_before_reconciliation(case, field):
    changed = replace(case.action, **{field: "99999999-9999-4999-8999-999999999999"})
    if field == "action_key":
        changed = replace(changed, action_digest_sha256=action_digest(work_uid=case.permit.work_uid,
            command_uid=case.permit.command_uid, action_key=changed.action_key, action_kind=changed.action_kind,
            payload={"nativeUartPayloadHex": case.store.get_native_command(changed.action_uid)["payload"].hex()}))
    restart_stores(case)
    with pytest.raises(ValueError, match="conflict"):
        case.store.bind_native_action(case.permit, changed)
    assert case.store.get_native_action_binding(case.action.action_uid)["action"] == case.action


def test_same_action_with_other_permanent_receipt_is_not_silently_reconciled(case):
    proof = case.store.prepare_native_action_confirmation(case.action.action_uid)
    other = replace(case.action, receipt_uid="99999999-9999-4999-8999-999999999999")
    case.safety.confirm_physical_action(other, outcome="EXECUTED", evidence_sha256=proof["evidence_sha256"],
        confirmation_basis="MCU_IDENTITY_BOUND_FACT")
    with pytest.raises(ValueError, match="does not match"):
        NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert case.store.get_native_action_confirmation(case.action.action_uid)["state"] == "PENDING"
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("failure", [None, "binding", "confirmation", "version"])
def test_schema27_upgrade_preserves_original_data_without_retroactive_action_binding(case, failure):
    path = case.store.db_path
    command = case.store.get_native_command(case.action.action_uid)
    event = case.store.get_native_actuator_event(1, 3)
    with case.store.transaction() as conn:
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DELETE FROM schema_version WHERE version>=28")
    case.store.close()
    case.store = EdgeStore(path)
    case.store._open_connection()
    blocked = {"binding": (sqlite3.SQLITE_CREATE_TABLE, "native_action_binding"),
        "confirmation": (sqlite3.SQLITE_CREATE_TABLE, "native_action_confirmation"),
        "version": (sqlite3.SQLITE_INSERT, "schema_version")}.get(failure)
    if blocked:
        case.store._conn.set_authorizer(lambda operation, first, *_:
            sqlite3.SQLITE_DENY if (operation, first) == blocked else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            case.store.initialize()
        case.store.close()
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 27
            assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('native_action_binding','native_action_confirmation')").fetchone()[0] == 0
        case.store = EdgeStore(path)
    case.store.initialize()
    assert case.store.get_native_command(case.action.action_uid) == command
    assert case.store.get_native_actuator_event(1, 3) == event
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.get_native_action_binding(case.action.action_uid) is None
    with pytest.raises(ValueError, match="after dispatch"):
        case.store.bind_native_action(case.permit, case.action)
    with pytest.raises(ValueError, match="binding missing"):
        NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)


@pytest.mark.parametrize("prepared", [False, True])
def test_restart_discovers_original_pending_action_from_occupied_work_without_caller_identity(case, prepared):
    if prepared:
        case.store.prepare_native_action_confirmation(case.action.action_uid)
    restart_stores(case)
    reconciler = NativeActionReconciler(case.store, case.safety)
    results = reconciler.reconcile_pending()
    assert len(results) == 1
    assert results[0]["action_uid"] == case.action.action_uid and results[0]["state"] == "CONFIRMED"
    assert reconciler.reconcile_pending() == []
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def active_action(store, tmp_path):
    updater, safety = make_real_job_safety(tmp_path)
    command = _command()
    command["payload"]["sessionUid"] = WORK_UID
    permit = safety.request_job(command, work_type="DELIVERY", work_uid=WORK_UID)
    safety.begin_job(permit, begin_uid="55555555-5555-4555-8555-555555555555", digest=permit.request_digest_sha256)
    record = store.get_native_command(COMMAND_UID)
    key = "delivery:first-open"
    digest = action_digest(work_uid=WORK_UID, command_uid=permit.command_uid, action_key=key,
        action_kind=record["message_name"], payload={"nativeUartPayloadHex": record["payload"].hex()})
    return updater, safety, permit, PhysicalAction(COMMAND_UID, "66666666-6666-4666-8666-666666666666", key, record["message_name"], digest)


def test_gate_commits_original_permit_action_and_receipt_before_any_uart_write(ready, tmp_path):
    store, _, _, write, boot = ready
    updater, safety, permit, action = active_action(store, tmp_path)
    observed = []
    def checked_write(frame):
        reader = EdgeStore(store.db_path)
        reader.initialize()
        try:
            binding = reader.get_native_action_binding(action.action_uid)
            assert binding["permit"] == permit and binding["action"] == action
            assert binding["command_payload"] == store.get_native_command(COMMAND_UID)["payload"]
            observed.append(binding)
        finally:
            reader.close()
        return write(frame)
    try:
        gate = NativePhysicalActionGate(safety, permit, action, store=store, revalidate=lambda _: None, deadline_ms=1000, clock=lambda: 0)
        client = McuCommandDispatcher(store, boot, checked_write, arm=gate, clock=lambda: 0)
        assert client.send_once(COMMAND_UID)
        assert len(observed) == 1
        assert safety.get_physical_action(COMMAND_UID)["state"] == "ARMED"
    finally:
        updater.close()


def test_binding_commit_failure_prevents_permanent_arm_and_serial_write(ready, tmp_path):
    store, _, _, write, boot = ready
    updater, safety, permit, action = active_action(store, tmp_path)
    sent = []
    def checked_write(frame):
        sent.append(frame)
        return write(frame)
    store._conn.execute("CREATE TEMP TRIGGER fail_binding BEFORE INSERT ON native_action_binding "
        "BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
    try:
        gate = NativePhysicalActionGate(safety, permit, action, store=store,
            revalidate=lambda _: None, deadline_ms=1000, clock=lambda: 0)
        client = McuCommandDispatcher(store, boot, checked_write, arm=gate, clock=lambda: 0)
        with pytest.raises(sqlite3.DatabaseError, match="disk failure"):
            client.send_once(COMMAND_UID)
        assert sent == []
        assert store.get_native_action_binding(COMMAND_UID) is None
        assert store.get_native_command(COMMAND_UID)["write_claimed"] == 0
        with pytest.raises(JobSafetyError):
            safety.get_physical_action(COMMAND_UID)
    finally:
        updater.close()
