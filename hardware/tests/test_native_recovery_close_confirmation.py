"""Actual new-close output -> durable proof -> permanent ledger, no admission."""
import pytest
import json
from edge_store import EdgeStore
from job_safety import PermanentJobSafety, JobSafetyError
from local_control import LocalControlUnavailable
import sqlite3
import subprocess
import sys
from pathlib import Path
import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator
from mcu_actuator_handoff import McuActuatorEventHandoff


@pytest.fixture
def closed(archived, request):
    case, wire, boot, issue, saved = archived
    mode = getattr(request, "param", "normal")
    lib = wire.runtime[0]
    if mode == "coalesced":
        assert lib.ActuatorRuntime_SetDoorTarget(1)
        wire.advance(100)
    if mode == "pinched":
        lib.TestFacts_Pinch(1)
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    assert owner.send_once(uid)
    wire.pump(owner)
    if mode == "rejected":
        lib.ActuatorRuntime_StopForUpdate()
    wire.advance(100)
    handoff = McuActuatorEventHandoff(case.store, wire.write, 2)
    handoff.poll(wire.now)
    wire.pump(handoff)
    output = case.store.get_native_actuator_event(2, 1)
    assert output["message_name"] == "SAFE_CLOSE_RESULT"
    yield case, wire, boot, issue, prepared, output


def test_saved_actual_close_confirms_only_new_action_and_keeps_original_issue(closed):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    before = list(wire.sent)
    proof = NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    assert proof["state"] == "CONFIRMED"
    assert case.store.get_native_recovery_close_confirmation(uid) == proof
    ledger = case.safety.get_physical_action(uid)
    assert ledger["state"] == "CONFIRMED" and ledger["confirmedOutcome"] == "EXECUTED"
    assert ledger["evidenceDigestSha256"] == proof["evidence_sha256"]
    assert ledger["receiptUid"] == prepared["action"].receipt_uid
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert not case.store.list_native_result_report_tasks()
    assert wire.sent == before


@pytest.mark.parametrize("committed", [False, True])
def test_lost_permanent_request_or_reply_recovers_original_pending_proof(closed, tmp_path, committed):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    from hardware.tests.test_command_processor import StoreBackedUpdaterClient
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid

    class LostRpc:
        def request(self, operation, payload):
            if operation == "CONFIRM_PHYSICAL_ACTION":
                if committed:
                    case.updater.confirm_physical_action(payload)
                raise LocalControlUnavailable("lost new-close confirmation")
            return StoreBackedUpdaterClient(case.updater).request(operation, payload)

    with pytest.raises(JobSafetyError):
        NativeRecoveryCloseReconciler(case.store, PermanentJobSafety(LostRpc())).reconcile(uid)
    pending = case.store.get_native_recovery_close_confirmation(uid)
    assert pending["state"] == "PENDING"
    assert case.safety.get_physical_action(uid)["state"] == ("CONFIRMED" if committed else "ARMED")
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    before = list(wire.sent)
    reconciler = NativeRecoveryCloseReconciler(case.store, case.safety)
    assert reconciler.reconcile_pending() == [pending | {"state": "CONFIRMED"}]
    assert reconciler.reconcile_pending() == []
    assert reconciler.reconcile(uid) == pending | {"state": "CONFIRMED"}
    assert wire.sent == before and case.store.get_work_slot() == case.occupancy
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


@pytest.mark.parametrize("missing", ["output", "acceptance"])
def test_missing_original_custody_cannot_confirm_new_close(closed, missing):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    with case.store.transaction() as conn:
        if missing == "output":
            conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=2")
        else:
            conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (uid,))
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid) is None
    assert case.store.get_native_recovery_close_confirmation(uid) is None
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"


@pytest.mark.parametrize("closed", ["coalesced", "pinched", "rejected"], indirect=True)
def test_actual_output_status_never_claims_physical_door_position(closed):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    value = uart.decode_payload("SAFE_CLOSE_RESULT", output["payload"])
    result = NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    if value["outputStatus"] == "OUTPUT_REJECTED":
        assert result is None
        assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    else:
        assert result["state"] == "CONFIRMED"
        bundle = json.loads(result["bundle_json"])
        assert bytes.fromhex(bundle["output"]["payloadHex"]) == output["payload"]
        assert value["physicalDoorStateBasis"] == "NOT_OBSERVABLE"
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert case.store.get_work_slot() == case.occupancy


def test_confirmed_original_action_is_not_relabelled_by_new_close_reconciliation(closed):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    from mcu_action_evidence import NativeActionReconciler
    case, wire, boot, issue, prepared, output = closed
    original = NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert original["state"] == "CONFIRMED"
    before = case.safety.get_physical_action(case.action.action_uid)
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(prepared["action"].action_uid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(case.action.action_uid) == before
    assert case.updater.get_status()["jobGateState"] == "LOCKED"
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert not case.store.list_native_result_report_tasks()


@pytest.mark.parametrize("change", ["port", "boot", "second_output", "copied_index"])
def test_output_identity_or_multiple_claims_cannot_confirm_recovery(closed, change):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    value = uart.decode_payload("SAFE_CLOSE_RESULT", output["payload"])
    # Receive/storage boundary corruption, not a synthetic success witness.
    if change == "copied_index":
        with case.store.transaction() as conn:
            conn.execute("UPDATE native_actuator_event SET reported_command_uid=? WHERE mcu_boot_id=2", (case.action.action_uid,))
    else:
        if change == "second_output":
            value["mcuEventSequence"] = 2
        else:
            with case.store.transaction() as conn:
                conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=2")
            value["portNo" if change == "port" else "mcuBootId"] = 3
        case.store.save_native_actuator_event("SAFE_CLOSE_RESULT", uart.encode_payload("SAFE_CLOSE_RESULT", value))
    with pytest.raises(ValueError, match="identity|corrupt|multiple"):
        NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("already_pending", [False, True])
def test_conflicting_original_output_is_retained_without_confirmation(closed, already_pending):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    if already_pending:
        case.store.prepare_native_recovery_close_confirmation(uid)
    value = uart.decode_payload("SAFE_CLOSE_RESULT", output["payload"])
    raw = uart.encode_payload("SAFE_CLOSE_RESULT", value | {"uptimeMs": value["uptimeMs"] + 1})
    with pytest.raises(ValueError, match="conflict"):
        case.store.save_native_actuator_event("SAFE_CLOSE_RESULT", raw)
    with pytest.raises(ValueError, match="conflict"):
        NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    assert any(row["payload"] == raw for row in case.store.list_native_actuator_event_conflicts())
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"


def test_new_query_acceptance_keeps_original_pinned_proof(closed):
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    pending = case.store.prepare_native_recovery_close_confirmation(uid)
    record = case.store.get_native_command(uid)
    identity = uart.decode_payload("QUERY_COMMAND", (1).to_bytes(8, "big") + record["payload"][:60])
    raw = uart.encode_payload("COMMAND_QUERY_RESULT", identity | dict(currentMcuBootId=2, outcome="ACCEPTED",
        errorCode="NONE", highestCommandSequence=record["command_sequence"]))
    assert case.store.save_native_command_observation("COMMAND_QUERY_RESULT", raw)
    assert case.store.prepare_native_recovery_close_confirmation(uid) == pending


@pytest.mark.parametrize("operation", ["GET_NATIVE_RECOVERY_CLOSE", "GET_JOB_PERMIT", "GET_PHYSICAL_ACTION"])
def test_permanent_identity_mismatch_never_commits_local_confirmation(closed, monkeypatch, operation):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    request = case.safety._client.request
    confirmed = []

    def mismatched(op, payload):
        if op == "CONFIRM_PHYSICAL_ACTION":
            confirmed.append(payload)
        result = request(op, payload)
        if op == operation:
            result = result | {"workUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
        return result

    monkeypatch.setattr(case.safety._client, "request", mismatched)
    with pytest.raises(ValueError, match="mismatch|conflict"):
        NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    assert not confirmed
    assert case.store.get_native_recovery_close_confirmation(uid)["state"] == "PENDING"


def test_new_mcu_restart_does_not_invalidate_saved_historical_close_or_send_again(closed):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    assert wire.reset_mcu().current_boot(wire.now) == 3
    before = list(wire.sent)
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)["state"] == "CONFIRMED"
    assert wire.sent == before
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("point", ["proof_insert", "local_confirm"])
def test_storage_failure_preserves_retryable_two_store_boundary(closed, point):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    with case.store.transaction() as conn:
        operation = "INSERT" if point == "proof_insert" else "UPDATE"
        conn.execute("CREATE TEMP TRIGGER fail_proof BEFORE " + operation + " ON native_recovery_close_confirmation "
            "BEGIN SELECT RAISE(ABORT, 'injected proof storage failure'); END")
    reconciler = NativeRecoveryCloseReconciler(case.store, case.safety)
    with pytest.raises(sqlite3.IntegrityError, match="injected proof"):
        reconciler.reconcile(uid)
    assert case.safety.get_physical_action(uid)["state"] == ("ARMED" if point == "proof_insert" else "CONFIRMED")
    with case.store.transaction() as conn:
        conn.execute("DROP TRIGGER fail_proof")
    assert reconciler.reconcile(uid)["state"] == "CONFIRMED"


@pytest.mark.parametrize("point", ["before", "after", "committed"])
def test_process_exit_during_proof_storage_never_confirms_without_commit(closed, tmp_path, point):
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    case.store.close()
    code = r'''
import os, sys
sys.path.insert(0,sys.argv[1])
from edge_store import EdgeStore
store=EdgeStore(sys.argv[2]);store.initialize()
if sys.argv[4]!='committed':
    store._conn.create_function('crash_now',0,lambda:os._exit(77))
    store._conn.execute('CREATE TEMP TRIGGER crash_proof '+sys.argv[4].upper()+
        ' INSERT ON native_recovery_close_confirmation BEGIN SELECT crash_now(); END')
assert store.prepare_native_recovery_close_confirmation(sys.argv[3])['state']=='PENDING'
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve().parents[1]),
        str(tmp_path / "edge.db"), uid, point], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 77, run.stderr
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    proof = case.store.get_native_recovery_close_confirmation(uid)
    assert (proof is not None) == (point == "committed")
    if proof:
        assert proof["state"] == "PENDING"
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("failure", [None, "table", "version"])
def test_v36_upgrade_is_atomic_and_does_not_promote_saved_output_to_confirmation(closed, tmp_path, failure):
    from edge_store import CURRENT_SCHEMA_VERSION
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    with case.store.transaction() as conn:
        conn.execute("DROP TABLE native_recovery_close_retirement")
        conn.execute("DROP TABLE native_recovery_close_confirmation")
        conn.execute("DELETE FROM schema_version WHERE version>36")
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    if failure:
        case.store._open_connection()
        target = ((sqlite3.SQLITE_CREATE_TABLE, "native_recovery_close_confirmation") if failure == "table"
            else (sqlite3.SQLITE_INSERT, "schema_version"))
        case.store._conn.set_authorizer(lambda op, name, *_: sqlite3.SQLITE_DENY if (op, name) == target else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            case.store.initialize()
        case.store.close()
        with sqlite3.connect(tmp_path / "edge.db") as conn:
            assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 36
            assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='native_recovery_close_confirmation'").fetchone()[0] == 0
        case.store = EdgeStore(str(tmp_path / "edge.db"))
    case.store.initialize()
    assert CURRENT_SCHEMA_VERSION == 39
    assert case.store.get_native_recovery_close_confirmation(uid) is None
    assert case.store.get_native_delivery_recovery_close(uid) == prepared
    assert case.store.get_native_actuator_event(2, 1) == output
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"


@pytest.mark.parametrize("corruption", ["proof_hash", "rehash_modified_proof", "lost_output", "lost_acceptance"])
def test_reopen_rejects_proof_that_no_longer_matches_original_custody(closed, tmp_path, corruption):
    from mcu_action_evidence import bundle_digest
    from work_recovery import canonical
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    proof = case.store.prepare_native_recovery_close_confirmation(uid)
    case.store.close()
    with sqlite3.connect(tmp_path / "edge.db") as conn:
        if corruption == "proof_hash":
            conn.execute("UPDATE native_recovery_close_confirmation SET evidence_sha256=?", ("f" * 64,))
        elif corruption == "rehash_modified_proof":
            bundle = json.loads(proof["bundle_json"])
            bundle["recovery"]["portNo"] = 3
            raw = canonical(bundle)
            conn.execute("UPDATE native_recovery_close_confirmation SET bundle_json=?,evidence_sha256=?", (raw, bundle_digest(raw)))
        elif corruption == "lost_output":
            conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=2")
        else:
            conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (uid,))
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    with pytest.raises((ValueError, RuntimeError)):
        case.store.initialize()
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"


@pytest.mark.parametrize("kind", ["business", "runtime"])
def test_isolated_inventory_retains_original_confirmation_proof(closed, tmp_path, kind):
    from hardware.tests.test_native_release_custody import stage_app
    from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, prepared, output = closed
    uid = prepared["action"].action_uid
    proof = NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    case.store.close()
    app = stage_app(tmp_path, BUSINESS_APP_FILES if kind == "business" else RUNTIME_APP_FILES)
    code = r'''
import json, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from edge_store import EdgeStore
store=EdgeStore(sys.argv[2]);store.initialize()
assert store.get_native_recovery_close_confirmation(sys.argv[3])==json.loads(sys.argv[4])
assert not store.list_pending_native_recovery_close_uids()
for name in ('edge_store','native_delivery_recovery_close','mcu_action_evidence','uart2_protocol'):
    assert Path(sys.modules[name].__file__).resolve().parent==Path(sys.argv[1]).resolve()
print(json.dumps(store.get_work_slot()));store.close()
'''
    run = subprocess.run([sys.executable, "-I", "-c", code, str(app), str(tmp_path / "edge.db"), uid, json.dumps(proof)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == case.occupancy
