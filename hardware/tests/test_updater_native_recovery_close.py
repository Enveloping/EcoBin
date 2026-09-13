"""Permanent SQLite recovery-close authority; no UART or simulated door facts."""
import pytest
import sqlite3
import shutil
import subprocess
import sys
import os
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import uart2_protocol as uart
from job_safety import action_digest
from job_safety import JobPermit, PhysicalAction, PermanentJobSafety, JobSafetyError
from local_control import LocalControlUnavailable
from updater_agent import UpdaterControlHandler, build_control_actions
from hardware.tests.test_native_command_session import open_fields

from updater_store import UpdaterStoreError
from hardware.tests.test_updater_store import (
    _store, _activate_candidate, _permit_payload, _begin_payload,
    _action_payload, _uid, _operator_lock,
)


@pytest.fixture
def active(tmp_path, request):
    store = _store(tmp_path / "updater.db", "native-recovery-candidate", candidate=True)
    _activate_candidate(store)
    store.request_job_permit(_permit_payload())
    store.begin_job(_begin_payload())
    if getattr(request, "param", None) == "draining":
        store.transition_job_gate("DRAINING", owner_update_uid=_uid(97), maintenance_type="MCU_FIRMWARE_UPDATE")
    source = _action_payload() | {"actionKind": "AUTHORIZE_DELIVERY_FIRST_OPEN"}
    raw = command_payload("AUTHORIZE_DELIVERY_FIRST_OPEN", source["actionUid"], 1,
        open_fields() | {"sessionUid": source["workUid"]})
    source["sourceCommandPayloadHex"] = raw.hex()
    source["actionDigestSha256"] = wire_digest(source, raw)
    store.prepare_physical_action(source)
    armed = store.arm_physical_action({"actionUid": source["actionUid"], "dispatchAttemptToken": source["dispatchAttemptToken"]})
    yield store, source, armed
    store.close()


def command_payload(name, uid, boot, fields):
    values = dict(mcuCommandUid=uid, targetMcuBootId=boot, commandSequence=1, **fields)
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    return uart.encode_payload(name, values)


def wire_digest(action, raw):
    return action_digest(work_uid=action["workUid"], command_uid=action["commandUid"],
        action_key=action["actionKey"], action_kind=action["actionKind"], payload={"nativeUartPayloadHex": raw.hex()})


def recovery_request(source, armed):
    request = source | dict(actionUid=_uid(60), actionKey=f"native:recovery-close:{_uid(61)}",
        actionKind="SAFE_CLOSE", actionDigestSha256="6" * 64, dispatchAttemptToken="B" * 43,
        recoveryUid=_uid(61), sourceActionUid=source["actionUid"], sourceActionDigestSha256=source["actionDigestSha256"],
        expectedSourceLedgerSequence=armed["ledgerSequence"], sourceMcuBootId=1, targetMcuBootId=2,
        portNo=1, reason="MCU_RESTART_DATA_LOSS", recoveryEvidenceSha256="7" * 64)
    raw = command_payload("SAFE_CLOSE", request["actionUid"], 2, dict(scope="SINGLE_DELIVERY_DOOR", portNo=1, executionDeadlineMs=5000))
    request["closeCommandPayloadHex"] = raw.hex()
    request["actionDigestSha256"] = wire_digest(request, raw)
    return request


def test_unknown_original_allows_only_one_explicit_new_close_without_unlocking_the_job(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    prepared = store.prepare_native_recovery_close(request)
    assert prepared["state"] == "PREPARED" and prepared["mayExecute"] is False
    result = store.arm_physical_action({"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]})
    assert result["mayExecute"] is True and result["state"] == "ARMED"
    assert not store.arm_physical_action({"actionUid": request["actionUid"], "dispatchAttemptToken": "C" * 43})["mayExecute"]
    old = store.get_physical_action({"actionUid": source["actionUid"]})
    assert old["state"] == "ARMED" and old["confirmedOutcome"] is None and old["unknownEffectResolution"] is None
    with pytest.raises(UpdaterStoreError, match="previous physical action"):
        store.prepare_physical_action(source | {"actionUid": _uid(70), "actionKey": "new-open"})
    assert store.get_status()["jobGateState"] == "LOCKED"
    assert store.get_status()["unreconciledPhysicalActionCount"] == 2
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


def test_recovery_cannot_relabel_the_original_port_while_authorizing_different_close_bytes(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    raw = command_payload("SAFE_CLOSE", request["actionUid"], 2, dict(scope="SINGLE_DELIVERY_DOOR", portNo=2, executionDeadlineMs=5000))
    request.update(closeCommandPayloadHex=raw.hex(), actionDigestSha256=wire_digest(request, raw))
    with pytest.raises(UpdaterStoreError, match="wire|scope"):
        store.prepare_native_recovery_close(request)
    assert store.get_status()["unreconciledPhysicalActionCount"] == 1


@pytest.mark.parametrize("column,value", [("action_kind", "AUTHORIZE_DELIVERY_FIRST_OPEN"), ("action_digest_sha256", "8" * 64)])
def test_restart_refuses_recovery_authority_whose_actual_ledger_identity_changed(active, column, value):
    store, source, armed = active
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    store.close()
    with sqlite3.connect(store.path) as conn:
        conn.execute(f"UPDATE physical_action_ledger SET {column}=? WHERE action_uid=?", (value, request["actionUid"]))
    with pytest.raises(RuntimeError, match="native recovery"):
        store.initialize()


@pytest.mark.parametrize("already_armed", [False, True])
def test_operator_stop_cannot_be_bypassed_even_by_retrying_the_same_live_recovery_arm(active, already_armed):
    store, source, armed = active
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    arm = {"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]}
    if already_armed:
        assert store.arm_physical_action(arm)["mayExecute"]
    _operator_lock(store, 99)
    with pytest.raises(UpdaterStoreError) as stopped:
        store.arm_physical_action(arm)
    assert stopped.value.code == "JOB_GATE_CLOSED"
    assert store.get_status()["jobGateState"] == "LOCKED"


def test_business_client_uses_restricted_rpc_and_can_query_exact_evidence_after_updater_restart(active):
    store, source, armed = active
    payload = recovery_request(source, armed)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102}, business_uids={3102}, enable_stage4_candidate=True)
    class Client:
        def request(self, operation, body):
            action = actions[operation]
            assert action.allowed_uids == frozenset({3102})
            assert set(body) == action.payload_fields
            return action.handler(body)
    safety = PermanentJobSafety(Client())
    permit = JobPermit(source["permitUid"], source["workUid"], source["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(payload["actionUid"], _uid(63), payload["actionKey"], "SAFE_CLOSE", payload["actionDigestSha256"])
    ordinary = {"actionUid", "permitUid", "workUid", "commandUid", "actionKey", "actionKind", "actionDigestSha256", "dispatchAttemptToken"}
    evidence = {key: value for key, value in payload.items() if key not in ordinary}
    safety.prepare_native_recovery_close(permit, action=action, evidence=evidence, dispatch_attempt_token=payload["dispatchAttemptToken"])
    store.close()
    store.initialize()
    saved = safety.get_native_recovery_close(action.action_uid)
    assert saved == {key: value for key, value in payload.items() if key != "dispatchAttemptToken"} | {"disposition": "FOUND"}
    assert safety.get_physical_action(source["actionUid"])["state"] == "ARMED"
    assert safety.get_physical_action(action.action_uid)["state"] == "PREPARED"


@pytest.mark.parametrize("active", ["draining"], indirect=True)
def test_update_waiting_for_active_work_does_not_deadlock_the_original_recovery_close(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    owner = store.get_status()["maintenanceOwnerUid"]
    assert store.get_status()["maintenancePhase"] == "DRAINING"
    store.prepare_native_recovery_close(request)
    assert store.arm_physical_action({"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]})["mayExecute"]
    assert store.get_status()["maintenanceOwnerUid"] == owner
    assert store.get_status()["maintenancePhase"] == "DRAINING"
    with pytest.raises(UpdaterStoreError):
        store.transition_job_gate("MAINTENANCE")


def test_permanent_inventory_starts_without_importing_the_replaceable_business_directory(tmp_path):
    from install.runtime_payload_manifest import DEVICE_UPDATER_FILES
    hardware = Path(__file__).resolve().parents[1]
    stage = tmp_path / "permanent"
    for name in DEVICE_UPDATER_FILES:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(hardware / name, target)
    code = r'''
import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import updater_agent, job_safety, uart2_protocol
for module in (updater_agent, job_safety, uart2_protocol):
    assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
(root / 'state').mkdir(mode=0o700)
store = updater_agent.UpdaterStore(root / 'state' / 'updater.db', release_version='isolated', enable_stage4_candidate=False)
store.initialize()
assert store.get_status()['stage4CandidateEnabled'] is False
assert store.get_status()['jobGateState'] == 'LOCKED'
store.close()
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(stage)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("lost", ["row", "table"])
def test_missing_recovery_custody_cannot_fall_back_to_ordinary_action_authority_after_restart(active, lost):
    store, source, armed = active
    payload = recovery_request(source, armed)
    store.prepare_native_recovery_close(payload)
    store.close()
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM native_recovery_close" if lost == "row" else "DROP TABLE native_recovery_close")
    with pytest.raises(RuntimeError, match="native recovery"):
        store.initialize()


@pytest.mark.parametrize("key,value", [
    ("actionKind", "UNLOCK_CLEAN_DOOR"), ("reason", "UART_TIMEOUT"),
    ("targetMcuBootId", 1), ("sourceMcuBootId", True), ("targetMcuBootId", 2**53),
    ("portNo", 0), ("portNo", 7), ("expectedSourceLedgerSequence", 999),
    ("sourceActionDigestSha256", "f" * 64), ("actionDigestSha256", "f" * 64),
    ("actionKey", "new-open"), ("sourceCommandPayloadHex", ""), ("closeCommandPayloadHex", "00"),
])
def test_invalid_scope_or_unknown_source_never_creates_an_action(active, key, value):
    store, source, armed = active
    request = recovery_request(source, armed) | {key: value}
    with pytest.raises(UpdaterStoreError):
        store.prepare_native_recovery_close(request)
    assert store.get_status()["unreconciledPhysicalActionCount"] == 1
    assert store.get_physical_action({"actionUid": source["actionUid"]})["state"] == "ARMED"


def test_old_source_cannot_be_reconstructed_with_different_native_bytes(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    request["sourceCommandPayloadHex"] = command_payload("AUTHORIZE_DELIVERY_FIRST_OPEN", source["actionUid"], 1,
        open_fields() | {"sessionUid": source["workUid"], "firstPreOpenMeasurementUid": _uid(88)}).hex()
    with pytest.raises(UpdaterStoreError, match="source wire"):
        store.prepare_native_recovery_close(request)
    assert store.get_status()["unreconciledPhysicalActionCount"] == 1


@pytest.mark.parametrize("armed_close", [False, True])
def test_restart_queries_original_authority_but_does_not_grant_a_new_dispatch_token(active, armed_close):
    store, source, armed = active
    payload = recovery_request(source, armed)
    before = store.prepare_native_recovery_close(payload)
    if armed_close:
        store.arm_physical_action({"actionUid": payload["actionUid"], "dispatchAttemptToken": payload["dispatchAttemptToken"]})
    store.close()
    store.initialize()
    assert store.get_native_recovery_close({"actionUid": payload["actionUid"]}) == {
        key: value for key, value in payload.items() if key != "dispatchAttemptToken"} | {"disposition": "FOUND"}
    denied = store.prepare_native_recovery_close(payload | {"dispatchAttemptToken": "C" * 43})
    assert denied["disposition"] == "DENIED" and denied["mayExecute"] is False
    assert denied["ledgerSequence"] == before["ledgerSequence"]
    assert not store.arm_physical_action({"actionUid": payload["actionUid"], "dispatchAttemptToken": "C" * 43})["mayExecute"]
    if armed_close:
        assert not store.arm_physical_action({"actionUid": payload["actionUid"], "dispatchAttemptToken": payload["dispatchAttemptToken"]})["mayExecute"]
    assert store.get_status()["unreconciledPhysicalActionCount"] == 2


def test_recovery_output_receipt_alone_does_not_resolve_original_unknown_effect_or_complete_job(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    store.arm_physical_action({"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]})
    # Synthetic receipt tests permanent separation only, not actual MCU feedback.
    store.confirm_physical_action(dict(actionUid=request["actionUid"], receiptUid=_uid(63), outcome="EXECUTED",
        confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="9" * 64))
    with pytest.raises(UpdaterStoreError) as incomplete:
        store.complete_job(dict(permitUid=source["permitUid"], completionUid=_uid(64), outcome="CANCELLED", completionDigestSha256="a" * 64))
    assert incomplete.value.code == "PHYSICAL_ACTION_UNCONFIRMED"
    old = store.get_physical_action({"actionUid": source["actionUid"]})
    assert old["unknownEffectResolution"] is None and old["confirmedOutcome"] is None
    assert store.get_status()["jobGateState"] == "LOCKED"


@pytest.mark.parametrize("boundary", ["before-action", "before-evidence", "after-commit"])
def test_process_exit_leaves_both_recovery_reservation_and_evidence_or_neither(active, boundary):
    store, source, armed = active
    payload = recovery_request(source, armed)
    store.close()
    code = r'''
import sys, json, os, sqlite3
from updater_store import UpdaterStore
path, payload, boundary = sys.argv[1:]
store = UpdaterStore(path, release_version='recovery-crash', enable_stage4_candidate=True)
store.initialize()
def crash(action, name, *rest):
    if action == sqlite3.SQLITE_INSERT and ((boundary == 'before-action' and name == 'physical_action_ledger') or
            (boundary == 'before-evidence' and name == 'native_recovery_close')):
        os._exit(77)
    return sqlite3.SQLITE_OK
store._connection.set_authorizer(crash)
store.prepare_native_recovery_close(json.loads(payload))
assert boundary == 'after-commit'
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-c", code, str(store.path), json.dumps(payload), boundary],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])), capture_output=True, text=True, timeout=20)
    assert run.returncode == 77, run.stdout + run.stderr
    store.initialize()
    assert store.get_status()["unreconciledPhysicalActionCount"] == (2 if boundary == "after-commit" else 1)
    if boundary != "after-commit":
        with pytest.raises(UpdaterStoreError) as absent:
            store.get_native_recovery_close({"actionUid": payload["actionUid"]})
        assert absent.value.code == "NATIVE_RECOVERY_NOT_FOUND"
    result = store.prepare_native_recovery_close(payload)
    assert result["ledgerSequence"] == armed["ledgerSequence"] + 1
    assert result["disposition"] == ("DUPLICATE" if boundary == "after-commit" else "ACCEPTED")


def test_competing_connections_cannot_take_over_an_existing_recovery_token(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    second = _store(store.path, "parallel", candidate=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: pair[0].prepare_native_recovery_close(pair[1]),
                [(store, request), (second, request | {"dispatchAttemptToken": "C" * 43})]))
        assert sorted(result["disposition"] for result in results) == ["ACCEPTED", "DENIED"]
        assert results[0]["ledgerSequence"] == results[1]["ledgerSequence"]
        assert store.get_status()["unreconciledPhysicalActionCount"] == 2
    finally:
        second.close()


@pytest.mark.parametrize("committed", [False, True], ids=["request-lost", "reply-lost"])
def test_lost_local_rpc_does_not_change_recovery_identity_or_implicitly_arm(active, committed):
    store, source, armed = active
    payload = recovery_request(source, armed)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102}, business_uids={3102}, enable_stage4_candidate=True)
    requests = []
    class Client:
        lose = True
        def request(self, operation, body):
            if operation == "PREPARE_NATIVE_RECOVERY_CLOSE" and self.lose:
                requests.append(dict(body))
                if committed:
                    actions[operation].handler(body)
                raise LocalControlUnavailable("synthetic local interruption")
            return actions[operation].handler(body)
    client = Client()
    safety = PermanentJobSafety(client)
    permit = JobPermit(source["permitUid"], source["workUid"], source["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(payload["actionUid"], _uid(63), payload["actionKey"], "SAFE_CLOSE", payload["actionDigestSha256"])
    ordinary = {"actionUid", "permitUid", "workUid", "commandUid", "actionKey", "actionKind", "actionDigestSha256", "dispatchAttemptToken"}
    evidence = {key: value for key, value in payload.items() if key not in ordinary}
    with pytest.raises(JobSafetyError):
        safety.prepare_native_recovery_close(permit, action=action, evidence=evidence, dispatch_attempt_token=payload["dispatchAttemptToken"])
    assert requests == [payload, payload]
    assert store.get_status()["unreconciledPhysicalActionCount"] == (2 if committed else 1)
    store.close()
    store.initialize()
    client.lose = False  # Same live business attempt; only the updater restarted.
    safety.prepare_native_recovery_close(permit, action=action, evidence=evidence, dispatch_attempt_token=payload["dispatchAttemptToken"])
    assert safety.get_native_recovery_close(action.action_uid) == {
        key: value for key, value in payload.items() if key != "dispatchAttemptToken"} | {"disposition": "FOUND"}
    ledger = safety.get_physical_action(action.action_uid)
    assert ledger["state"] == "PREPARED" and ledger["mayExecute"] is False
    assert ledger["ledgerSequence"] == armed["ledgerSequence"] + 1
