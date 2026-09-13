"""Original recovery preparation retirement; no motion, completion or admission."""
import pytest
import sqlite3
import subprocess
import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator
from job_safety import JobSafetyError
from edge_store import EdgeStore
from local_control import LocalControlUnavailable


def test_durable_retirement_intent_prevents_original_live_sender(archived):
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    pending = case.store.prepare_native_recovery_close_retirement(uid)
    assert pending["state"] == "PENDING"
    assert case.store.prepare_native_recovery_close_retirement(uid) == pending
    with pytest.raises(JobSafetyError, match="RECOVERY_CLOSE_RETIRING"):
        owner.send_once(uid)
    assert not case.store.claim_native_command_write(uid)
    assert case.store.get_native_command(uid)["write_claimed"] == 0
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


@pytest.mark.parametrize("kind", ["collision", "query"])
def test_retirement_does_not_ignore_output_retained_only_as_conflict(archived, kind):
    case, wire, boot, *_ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    old = case.store.list_native_action_actuator_events(case.action.action_uid, "DELIVERY_DOOR_COMMAND_RESULT")[0]
    raw = uart.encode_payload("SAFE_CLOSE_RESULT", dict(mcuBootId=old["mcu_boot_id"],
        mcuEventSequence=old["event_sequence"], uptimeMs=100, mcuCommandUid=uid,
        scope="SINGLE_DELIVERY_DOOR", portNo=1, command="CLOSE", outputStatus="COMMAND_DISPATCHED",
        physicalDoorStateBasis="NOT_OBSERVABLE", faultCode="NONE"))
    if kind == "collision":
        with pytest.raises(ValueError, match="conflict"):
            case.store.save_native_actuator_event("SAFE_CLOSE_RESULT", raw)
    else:
        query = uart.encode_payload("ACTUATOR_EVENT_QUERY_REPLY", dict(queryId=1,
            targetMcuBootId=old["mcu_boot_id"], afterMcuEventSequence=0, currentMcuBootId=old["mcu_boot_id"],
            status="HELD", mcuEventSequence=old["event_sequence"], eventMessageType="SAFE_CLOSE_RESULT",
            eventDigestSha256="f" * 64))
        case.store.retain_native_actuator_query_conflict("SAFE_CLOSE_RESULT", raw, query)
    assert any(row["payload"] == raw for row in case.store.list_native_actuator_event_conflicts())
    with pytest.raises(ValueError):
        case.store.prepare_native_recovery_close_retirement(uid)


def test_already_retired_history_does_not_require_current_original_occupancy(archived):
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, issue, _ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    retire = NativeRecoveryCloseRetirement(case.store, case.safety)
    proof = retire.reconcile(prepared["action"].action_uid)
    # Explicit later lifecycle boundary, not proof that recovery/admission is integrated.
    assert case.store.release_work_slot(case.permit.work_uid)
    assert retire.reconcile(prepared["action"].action_uid) == proof
    assert case.store.get_work_slot() is None
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


@pytest.mark.parametrize("registered", [False, True])
def test_pi_restart_retires_only_original_never_armed_close(archived, tmp_path, monkeypatch, registered):
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    request = case.safety._client.request
    if registered:
        def lost_prepare_reply(op, payload):
            result = request(op, payload)
            if op == "PREPARE_NATIVE_RECOVERY_CLOSE":
                raise LocalControlUnavailable("lost original prepare reply")
            return result
        monkeypatch.setattr(case.safety._client, "request", lost_prepare_reply)
        with pytest.raises(JobSafetyError):
            owner.send_once(uid)
        monkeypatch.setattr(case.safety._client, "request", request)
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    before = list(wire.sent)
    result = NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    assert result["state"] == "RETIRED"
    ledger = case.safety.get_physical_action(uid)
    assert ledger["confirmedOutcome"] == "NOT_EXECUTED"
    assert ledger["confirmationBasis"] == "PREPARED_NOT_ARMED"
    assert ledger["receiptUid"] == prepared["action"].receipt_uid
    assert ledger["evidenceDigestSha256"] == result["evidence_sha256"]
    assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert not case.store.list_native_result_report_tasks()
    assert wire.sent == before


@pytest.mark.parametrize("committed", [False, True])
def test_lost_retirement_request_or_reply_recovers_same_pending_identity(archived, tmp_path, monkeypatch, committed):
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, issue, _ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    request = case.safety._client.request

    def lose(op, payload):
        if op == "RETIRE_NATIVE_RECOVERY_CLOSE":
            assert not case.store._conn.in_transaction
            with sqlite3.connect(tmp_path / "edge.db") as reader:
                row = reader.execute("SELECT evidence_sha256 FROM native_recovery_close_retirement WHERE action_uid=?", (uid,)).fetchone()
                assert row[0] == payload["retirementEvidenceSha256"]
            if committed:
                request(op, payload)
            raise LocalControlUnavailable("lost retirement RPC")
        return request(op, payload)

    monkeypatch.setattr(case.safety._client, "request", lose)
    with pytest.raises(JobSafetyError):
        NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    proof = case.store.get_native_recovery_close_retirement(uid)
    assert proof["state"] == "PENDING"
    monkeypatch.setattr(case.safety._client, "request", request)
    case.store.close(); case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    retire = NativeRecoveryCloseRetirement(case.store, case.safety)
    assert retire.reconcile_pending() == [proof | {"state": "RETIRED"}]
    assert retire.reconcile_pending() == []
    assert retire.reconcile(uid) == proof | {"state": "RETIRED"}
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


def test_armed_but_not_claimed_close_is_not_retired_as_unexecuted(archived, monkeypatch):
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    request = case.safety._client.request
    def lost_arm(op, payload):
        result = request(op, payload)
        if op == "ARM_PHYSICAL_ACTION":
            raise LocalControlUnavailable("lost ARM reply")
        return result
    monkeypatch.setattr(case.safety._client, "request", lost_arm)
    with pytest.raises(JobSafetyError):
        owner.send_once(uid)
    monkeypatch.setattr(case.safety._client, "request", request)
    assert case.store.get_native_command(uid)["write_claimed"] == 0
    with pytest.raises(JobSafetyError):
        NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    assert case.store.get_native_recovery_close_retirement(uid)["state"] == "PENDING"
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] is None
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


def test_actual_sent_close_cannot_be_retired(archived):
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    assert owner.send_once(uid)
    with pytest.raises(ValueError):
        NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    assert case.store.get_native_recovery_close_retirement(uid) is None
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"


def test_two_connections_cannot_both_retire_and_claim_same_close(archived, tmp_path):
    case, wire, boot, *_ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    other = EdgeStore(str(tmp_path / "edge.db")); other.initialize()
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=5)
        return other.claim_native_command_write(uid)
    def retire():
        barrier.wait(timeout=5)
        try:
            case.store.prepare_native_recovery_close_retirement(uid)
            return True
        except ValueError:
            return False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(claim), pool.submit(retire)
            claimed, retired = a.result(), b.result()
        assert claimed != retired
        assert bool(case.store.get_native_command(uid)["write_claimed"]) == claimed
        assert (case.store.get_native_recovery_close_retirement(uid) is not None) == retired
    finally:
        other.close()


@pytest.mark.parametrize("point", ["intent", "local_confirm"])
def test_retirement_storage_failure_keeps_original_retry_identity(archived, point):
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, *_ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    with case.store.transaction() as conn:
        op = "INSERT" if point == "intent" else "UPDATE"
        conn.execute("CREATE TEMP TRIGGER fail_retirement BEFORE " + op + " ON native_recovery_close_retirement "
            "BEGIN SELECT RAISE(ABORT,'injected retirement failure'); END")
    retire = NativeRecoveryCloseRetirement(case.store, case.safety)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        retire.reconcile(uid)
    if point == "intent":
        assert case.store.get_native_recovery_close_retirement(uid) is None
        with pytest.raises(JobSafetyError):
            case.safety.get_physical_action(uid)
    else:
        assert case.store.get_native_recovery_close_retirement(uid)["state"] == "PENDING"
        assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "NOT_EXECUTED"
    with case.store.transaction() as conn:
        conn.execute("DROP TRIGGER fail_retirement")
    assert retire.reconcile(uid)["state"] == "RETIRED"


@pytest.mark.parametrize("point", ["before", "after", "committed"])
def test_process_exit_retirement_intent_is_atomic_and_does_not_confirm_permanent(archived, tmp_path, point):
    case, wire, boot, *_ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    case.store.close()
    code = r'''
import os,sys
sys.path.insert(0,sys.argv[1])
from edge_store import EdgeStore
store=EdgeStore(sys.argv[2]);store.initialize()
if sys.argv[4]!='committed':
    store._conn.create_function('crash_now',0,lambda:os._exit(77))
    store._conn.execute('CREATE TEMP TRIGGER crash_retirement '+sys.argv[4].upper()+
        ' INSERT ON native_recovery_close_retirement BEGIN SELECT crash_now(); END')
assert store.prepare_native_recovery_close_retirement(sys.argv[3])['state']=='PENDING'
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve().parents[1]),
        str(tmp_path / "edge.db"), uid, point], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 77, run.stderr
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    proof = case.store.get_native_recovery_close_retirement(uid)
    assert (proof is not None) == (point == "committed")
    if proof:
        assert proof["state"] == "PENDING"
    with pytest.raises(JobSafetyError):
        case.safety.get_physical_action(uid)


@pytest.mark.parametrize("failure", [None, "table", "version"])
def test_v37_upgrade_retains_unretired_preparation_and_rolls_back_failures(archived, tmp_path, failure):
    from edge_store import CURRENT_SCHEMA_VERSION
    case, wire, boot, issue, _ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    with case.store.transaction() as conn:
        conn.execute("DROP TABLE native_recovery_close_retirement")
        conn.execute("DELETE FROM schema_version WHERE version>37")
    case.store.close(); case.store = EdgeStore(str(tmp_path / "edge.db"))
    if failure:
        case.store._open_connection()
        target = ((sqlite3.SQLITE_CREATE_TABLE, "native_recovery_close_retirement") if failure == "table"
            else (sqlite3.SQLITE_INSERT, "schema_version"))
        case.store._conn.set_authorizer(lambda op,name,*_:sqlite3.SQLITE_DENY if (op,name)==target else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            case.store.initialize()
        case.store.close()
        with sqlite3.connect(tmp_path / "edge.db") as conn:
            assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 37
            assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='native_recovery_close_retirement'").fetchone()[0] == 0
        case.store = EdgeStore(str(tmp_path / "edge.db"))
    case.store.initialize()
    assert CURRENT_SCHEMA_VERSION == 39
    assert case.store.get_native_recovery_close_retirement(uid) is None
    assert case.store.get_native_delivery_recovery_close(uid) == prepared
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


@pytest.mark.parametrize("kind", ["business", "runtime"])
def test_isolated_app_reopens_retirement_without_dispatching(archived, tmp_path, kind):
    from hardware.tests.test_native_release_custody import stage_app
    from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, *_ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    proof = NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    case.store.close()
    app = stage_app(tmp_path, BUSINESS_APP_FILES if kind == "business" else RUNTIME_APP_FILES)
    code = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from edge_store import EdgeStore
store=EdgeStore(sys.argv[2]);store.initialize()
assert store.get_native_recovery_close_retirement(sys.argv[3])==json.loads(sys.argv[4])
assert not store.claim_native_command_write(sys.argv[3])
assert not store.list_pending_native_recovery_close_retirements()
for name in ('edge_store','native_delivery_recovery_close'):
    assert Path(sys.modules[name].__file__).resolve().parent==Path(sys.argv[1]).resolve()
print(json.dumps(store.get_work_slot()));store.close()
'''
    run = subprocess.run([sys.executable,"-I","-c",code,str(app),str(tmp_path / "edge.db"),uid,json.dumps(proof)],
        cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == case.occupancy


@pytest.mark.parametrize("change", ["hash", "rehashed_bundle", "claimed"])
def test_reopen_refuses_retirement_with_changed_original_evidence(archived, tmp_path, change):
    from native_delivery_recovery_close import retirement_digest
    from work_recovery import canonical
    case, wire, boot, *_ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    proof = case.store.prepare_native_recovery_close_retirement(uid)
    case.store.close()
    with sqlite3.connect(tmp_path / "edge.db") as conn:
        if change == "hash":
            conn.execute("UPDATE native_recovery_close_retirement SET evidence_sha256=?", ("f" * 64,))
        elif change == "rehashed_bundle":
            bundle = json.loads(proof["bundle_json"])
            bundle["recovery"]["portNo"] = 2
            raw = canonical(bundle)
            conn.execute("UPDATE native_recovery_close_retirement SET bundle_json=?,evidence_sha256=?", (raw, retirement_digest(raw)))
        else:
            conn.execute("UPDATE native_mcu_command SET write_claimed=1 WHERE command_uid=?", (uid,))
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    with pytest.raises(ValueError):
        case.store.initialize()
    with pytest.raises(JobSafetyError):
        case.safety.get_physical_action(uid)
