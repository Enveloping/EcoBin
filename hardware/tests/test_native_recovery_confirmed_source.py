"""Confirmed original output must not prevent independent recovery close."""
import pytest
import json
import subprocess
import sys
from edge_store import EdgeStore
from job_safety import JobSafetyError, PermanentJobSafety
from local_control import LocalControlUnavailable
from hardware.tests.test_command_processor import StoreBackedUpdaterClient
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator
from mcu_action_evidence import NativeActionReconciler
from mcu_actuator_handoff import McuActuatorEventHandoff
from native_delivery_recovery_close import NativeRecoveryCloseReconciler


def test_confirmed_original_still_allows_new_close_without_restoring_business(archived):
    case, wire, boot, issue, _ = archived
    original = NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    assert original["state"] == "CONFIRMED"
    before = case.safety.get_physical_action(case.action.action_uid)
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    assert owner.send_once(uid)
    wire.pump(owner)
    wire.advance(100)
    handoff = McuActuatorEventHandoff(case.store, wire.write, 2)
    handoff.poll(wire.now)
    wire.pump(handoff)
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(case.action.action_uid) == before
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert not case.store.list_native_result_report_tasks()
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert [n for n, _ in wire.sent].count("SAFE_CLOSE") == 1


@pytest.mark.parametrize("boundary", ["prepare", "send"])
def test_confirmed_original_missing_raw_output_cannot_authorize_close(archived, boundary):
    case, wire, boot, *_ = archived
    NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    owner = coordinator(case, wire, boot)
    if boundary == "send":
        prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    with case.store.transaction() as conn:
        conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=1")
    with pytest.raises(ValueError):
        if boundary == "send":
            owner.send_once(prepared["action"].action_uid)
        else:
            owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


def test_last_prerequisite_callback_cannot_hide_lost_original_evidence(archived):
    case, wire, boot, *_ = archived
    NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)

    def last_check(record):
        if case.store.get_native_command(record["command_uid"])["write_claimed"]:
            with case.store.transaction() as conn:
                conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=1")

    owner = coordinator(case, wire, boot, revalidate=last_check)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    with pytest.raises(ValueError):
        owner.send_once(prepared["action"].action_uid)
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


def test_lost_original_confirmation_reply_allows_close_with_same_pending_proof(archived, tmp_path):
    case, wire, boot, issue, _ = archived

    class LostReply:
        def request(self, op, payload):
            result = StoreBackedUpdaterClient(case.updater).request(op, payload)
            if op == "CONFIRM_PHYSICAL_ACTION":
                raise LocalControlUnavailable("lost original confirmation reply")
            return result

    with pytest.raises(JobSafetyError):
        NativeActionReconciler(case.store, PermanentJobSafety(LostReply())).reconcile(case.action.action_uid)
    pending = case.store.get_native_action_confirmation(case.action.action_uid)
    assert pending["state"] == "PENDING"
    old = case.safety.get_physical_action(case.action.action_uid)
    assert old["state"] == "CONFIRMED"
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    owner = coordinator(case, wire, wire.handshake())
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    assert owner.send_once(prepared["action"].action_uid)
    assert case.safety.get_physical_action(case.action.action_uid) == old
    assert case.store.get_native_action_confirmation(case.action.action_uid) == pending
    assert NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid) == pending | {"state": "CONFIRMED"}
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


@pytest.mark.parametrize("boundary", ["prepared", "permanent_prepare", "permanent_arm"])
def test_original_confirmation_can_finish_during_close_authorization(archived, monkeypatch, boundary):
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    snapshots = []

    def confirm():
        NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
        snapshots.append(case.safety.get_physical_action(case.action.action_uid))

    if boundary == "prepared":
        confirm()
    else:
        request = case.safety._client.request
        target = "PREPARE_NATIVE_RECOVERY_CLOSE" if boundary == "permanent_prepare" else "ARM_PHYSICAL_ACTION"

        def with_original_confirmation(op, payload):
            assert not case.store._conn.in_transaction  # Never hold SQLite across a permanent RPC.
            result = request(op, payload)
            if op == target:
                confirm()
            return result

        monkeypatch.setattr(case.safety._client, "request", with_original_confirmation)
    assert owner.send_once(prepared["action"].action_uid)
    assert len(snapshots) == 1
    assert case.safety.get_physical_action(case.action.action_uid) == snapshots[0]
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("key,value", [
    ("receiptUid", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ("evidenceDigestSha256", "f" * 64), ("state", "ARMED"),
    ("confirmationBasis", "LIVE_FIXED_FRAME_RESULT"), ("confirmedOutcome", "FAILED_SAFE"),
])
def test_original_permanent_reply_must_match_saved_confirmation(archived, monkeypatch, key, value):
    case, wire, boot, *_ = archived
    NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    request = case.safety._client.request

    def wrong(op, payload):
        result = request(op, payload)
        if op == "GET_PHYSICAL_ACTION" and payload["actionUid"] == case.action.action_uid:
            return result | {key: value}
        return result

    monkeypatch.setattr(case.safety._client, "request", wrong)
    with pytest.raises(ValueError):
        coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


@pytest.mark.parametrize("lost", ["confirmation", "acceptance", "initial_saved_reply"])
def test_confirmed_original_missing_custody_is_not_a_new_close_permission(archived, lost):
    case, wire, boot, *_ = archived
    NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    with case.store.transaction() as conn:
        if lost == "confirmation":
            conn.execute("DELETE FROM native_action_confirmation WHERE action_uid=?", (case.action.action_uid,))
        elif lost == "acceptance":
            conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (case.action.action_uid,))
        else:
            conn.execute("UPDATE native_process_receipt SET saved_payload=zeroblob(length(saved_payload))")
    with pytest.raises(ValueError):
        coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


@pytest.mark.parametrize("outcome", ["EXECUTED", "FAILED_SAFE"])
def test_last_callback_source_confirmation_uses_current_permanent_state(archived, outcome):
    case, wire, boot, *_ = archived

    def finish_source(record):
        if not case.store.get_native_command(record["command_uid"])["write_claimed"]:
            return
        if outcome == "EXECUTED":
            NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
        else:
            # Negative permanent-response boundary, not a synthetic success witness.
            case.safety.confirm_physical_action(case.action, outcome="FAILED_SAFE",
                evidence_sha256="f" * 64, confirmation_basis="MCU_IDENTITY_BOUND_FACT")

    owner = coordinator(case, wire, boot, revalidate=finish_source)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    if outcome == "EXECUTED":
        assert owner.send_once(prepared["action"].action_uid)
        assert [n for n, _ in wire.sent].count("SAFE_CLOSE") == 1
    else:
        with pytest.raises(ValueError):
            owner.send_once(prepared["action"].action_uid)
        assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)
    assert case.safety.get_physical_action(case.action.action_uid)["confirmedOutcome"] == outcome
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("kind", ["business", "runtime"])
def test_isolated_app_validates_original_confirmed_source_snapshot(archived, tmp_path, kind):
    from hardware.tests.test_native_release_custody import stage_app
    from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES
    case, wire, boot, issue, _ = archived
    NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    ledger = case.safety.get_physical_action(case.action.action_uid)
    case.store.close()
    app = stage_app(tmp_path, BUSINESS_APP_FILES if kind == "business" else RUNTIME_APP_FILES)
    code = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from edge_store import EdgeStore
store=EdgeStore(sys.argv[2]);store.initialize()
result=store.validate_native_recovery_close_source(sys.argv[3],json.loads(sys.argv[4]))
assert result['action'].action_uid==sys.argv[3]
for name in ('edge_store','native_delivery_recovery_close','mcu_action_evidence'):
    assert Path(sys.modules[name].__file__).resolve().parent==Path(sys.argv[1]).resolve()
print(json.dumps(store.get_work_slot()));store.close()
'''
    run = subprocess.run([sys.executable, "-I", "-c", code, str(app), str(tmp_path / "edge.db"),
        prepared["action"].action_uid, json.dumps(ledger)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == case.occupancy
