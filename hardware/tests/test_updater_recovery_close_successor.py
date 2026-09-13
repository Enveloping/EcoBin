"""Real permanent SQLite custody for a new close after a retired attempt."""
import json
import sqlite3
import os
import shutil
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import uart2_protocol as uart

from hardware.tests.test_updater_native_recovery_close import active, recovery_request, wire_digest
from hardware.tests.test_updater_recovery_close_retirement import retirement_request
from hardware.tests.test_updater_store import _uid
from hardware.tests.test_updater_store import _store, _operator_lock
from job_safety import (JobPermit, PhysicalAction, PermanentJobSafety,
    JobSafetyError, NATIVE_RECOVERY_CLOSE_SUCCESSOR_EVIDENCE_FIELDS)
from local_control import LocalControlUnavailable
from updater_agent import UpdaterControlHandler, build_control_actions
from updater_store import UpdaterStoreError


def successor_request(parent, retired, generation=2):
    request = parent | dict(actionUid=_uid(100 + generation * 3),
        recoveryUid=_uid(101 + generation * 3), dispatchAttemptToken=chr(67 + generation) * 43,
        predecessorActionUid=parent["actionUid"],
        expectedPredecessorLedgerSequence=retired["ledgerSequence"],
        predecessorReceiptUid=retired["receiptUid"],
        predecessorRetirementEvidenceSha256=retired["evidenceDigestSha256"])
    request["actionKey"] = f"native:recovery-close:{request['recoveryUid']}"
    values = uart.decode_payload("SAFE_CLOSE", bytes.fromhex(parent["closeCommandPayloadHex"]))
    values.update(mcuCommandUid=request["actionUid"], commandSequence=values["commandSequence"] + 1)
    values["commandDigestSha256"] = uart.compute_command_digest("SAFE_CLOSE", values)
    raw = uart.encode_payload("SAFE_CLOSE", values)
    request.update(closeCommandPayloadHex=raw.hex(), actionDigestSha256=wire_digest(request, raw))
    return request


def retire_payload(request, generation=2):
    return {key: value for key, value in request.items() if key != "dispatchAttemptToken"} | {
        "receiptUid": _uid(102 + generation * 3), "retirementEvidenceSha256": str(generation) * 64}


def test_retired_attempt_permits_independent_successor_and_preserves_original_history(active):
    store, source, armed = active
    parent = recovery_request(source, armed)
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    request = successor_request(parent, retired)
    prepared = store.prepare_native_recovery_close_successor(request)
    assert prepared["state"] == "PREPARED" and prepared["mayExecute"] is False
    assert prepared["ledgerSequence"] > retired["ledgerSequence"]
    assert store.get_native_recovery_close({"actionUid": request["actionUid"]}) == {
        key: value for key, value in request.items() if key != "dispatchAttemptToken"
    } | {"disposition": "FOUND"}
    executed = store.arm_physical_action({"actionUid": request["actionUid"],
        "dispatchAttemptToken": request["dispatchAttemptToken"]})
    assert executed["state"] == "ARMED" and executed["mayExecute"] is True
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert store.get_physical_action({"actionUid": parent["actionUid"]})["receiptUid"] == retired["receiptUid"]
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"
    assert store.get_status()["jobGateState"] == "LOCKED"


def test_successor_retirement_without_prepare_fences_late_request_and_allows_third_generation(active):
    store, source, armed = active
    parent = recovery_request(source, armed)
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    second = successor_request(parent, retired)
    second_retired = store.retire_native_recovery_close_successor(retire_payload(second))
    assert second_retired["state"] == "CONFIRMED" and second_retired["confirmedOutcome"] == "NOT_EXECUTED"
    assert store.prepare_native_recovery_close_successor(second)["mayExecute"] is False
    assert store.arm_physical_action({"actionUid": second["actionUid"],
        "dispatchAttemptToken": second["dispatchAttemptToken"]})["mayExecute"] is False
    third = successor_request(second, second_retired, 3)
    assert store.prepare_native_recovery_close_successor(third)["state"] == "PREPARED"
    store.close()
    store.initialize()
    assert store.get_native_recovery_close({"actionUid": third["actionUid"]})["predecessorActionUid"] == second["actionUid"]
    assert store.get_status()["jobGateState"] == "LOCKED"


def test_business_interface_routes_exact_successor_fields_to_candidate_only_operations(active):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102},
        business_uids={3102}, enable_stage4_candidate=True)
    calls = []
    class Client:
        def request(self, operation, body):
            rpc = actions[operation]
            assert rpc.allowed_uids == frozenset({3102})
            assert set(body) == rpc.payload_fields
            calls.append(operation)
            return rpc.handler(body)
    safety = PermanentJobSafety(Client())
    permit = JobPermit(source["permitUid"], source["workUid"], source["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(request["actionUid"], retire_payload(request)["receiptUid"],
        request["actionKey"], "SAFE_CLOSE", request["actionDigestSha256"])
    evidence = {key: request[key] for key in NATIVE_RECOVERY_CLOSE_SUCCESSOR_EVIDENCE_FIELDS}
    safety.prepare_native_recovery_close(permit, action=action, evidence=evidence,
        dispatch_attempt_token=request["dispatchAttemptToken"])
    result = safety.retire_native_recovery_close(permit, action=action, evidence=evidence,
        retirement_evidence_sha256="2" * 64)
    assert result["state"] == "CONFIRMED" and result["mayExecute"] is False
    assert calls == ["PREPARE_NATIVE_RECOVERY_CLOSE_SUCCESSOR", "GET_NATIVE_RECOVERY_CLOSE",
        "RETIRE_NATIVE_RECOVERY_CLOSE_SUCCESSOR", "GET_NATIVE_RECOVERY_CLOSE"]
    disabled = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102})
    assert "PREPARE_NATIVE_RECOVERY_CLOSE_SUCCESSOR" not in disabled
    assert "RETIRE_NATIVE_RECOVERY_CLOSE_SUCCESSOR" not in disabled


def test_even_denied_arm_rechecks_retired_successor_ancestry(active):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    store.retire_native_recovery_close_successor(retire_payload(request))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE physical_action_ledger SET evidence_digest_sha256=? WHERE action_uid=?",
            ("f" * 64, request["predecessorActionUid"]))
    with pytest.raises(RuntimeError, match="native recovery"):
        store.arm_physical_action({"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]})


@pytest.mark.parametrize("key,value", [
    ("predecessorActionUid", _uid(199)), ("expectedPredecessorLedgerSequence", 999),
    ("predecessorReceiptUid", _uid(199)), ("predecessorRetirementEvidenceSha256", "f" * 64),
    ("recoveryEvidenceSha256", "f" * 64), ("sourceActionDigestSha256", "f" * 64),
])
@pytest.mark.parametrize("retire", [False, True])
def test_successor_cannot_replace_original_or_parent_evidence(active, key, value, retire):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired) | {key: value}
    before = store.get_status()
    with pytest.raises((UpdaterStoreError, RuntimeError)):
        if retire:
            store.retire_native_recovery_close_successor(retire_payload(request))
        else:
            store.prepare_native_recovery_close_successor(request)
    assert store.get_status() == before
    with pytest.raises(UpdaterStoreError):
        store.get_physical_action({"actionUid": request["actionUid"]})


@pytest.mark.parametrize("state", ["PREPARED", "ARMED", "EXECUTED"])
@pytest.mark.parametrize("retire", [False, True])
def test_successor_requires_actual_never_armed_parent_retirement(active, state, retire):
    store, source, armed = active
    parent = recovery_request(source, armed)
    previous = store.prepare_native_recovery_close(parent)
    if state != "PREPARED":
        store.arm_physical_action({"actionUid": parent["actionUid"], "dispatchAttemptToken": parent["dispatchAttemptToken"]})
    if state == "EXECUTED":
        store.confirm_physical_action(dict(actionUid=parent["actionUid"], receiptUid=_uid(63),
            outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="9" * 64))
    claimed = previous | {"receiptUid": _uid(63), "evidenceDigestSha256": "9" * 64}
    request = successor_request(parent, claimed)
    with pytest.raises(UpdaterStoreError):
        if retire:
            store.retire_native_recovery_close_successor(retire_payload(request))
        else:
            store.prepare_native_recovery_close_successor(request)
    assert store.get_physical_action({"actionUid": parent["actionUid"]})["state"] == ("CONFIRMED" if state == "EXECUTED" else state)


@pytest.mark.parametrize("change", ["sequence", "target", "port"])
def test_successor_wire_cannot_go_backwards_or_change_original_scope(active, change):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    values = uart.decode_payload("SAFE_CLOSE", bytes.fromhex(request["closeCommandPayloadHex"]))
    if change == "sequence":
        values["commandSequence"] = 1
    elif change == "target":
        values["targetMcuBootId"] = request["targetMcuBootId"] = 3
    else:
        values["portNo"] = request["portNo"] = 2
    values["commandDigestSha256"] = uart.compute_command_digest("SAFE_CLOSE", values)
    raw = uart.encode_payload("SAFE_CLOSE", values)
    request.update(closeCommandPayloadHex=raw.hex(), actionDigestSha256=wire_digest(request, raw))
    with pytest.raises(UpdaterStoreError):
        store.prepare_native_recovery_close_successor(request)


@pytest.mark.parametrize("retire", [False, True])
def test_old_operations_reject_successor_fields_and_new_operations_reject_missing_ancestry(active, retire):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    if retire:
        request = retire_payload(request)
        original, successor = store.retire_native_recovery_close, store.retire_native_recovery_close_successor
    else:
        original, successor = store.prepare_native_recovery_close, store.prepare_native_recovery_close_successor
    with pytest.raises(UpdaterStoreError, match="exact fields"):
        original(request)
    from job_safety import NATIVE_RECOVERY_CLOSE_SUCCESSOR_FIELDS
    for key in NATIVE_RECOVERY_CLOSE_SUCCESSOR_FIELDS:
        with pytest.raises(UpdaterStoreError, match="exact fields"):
            successor({name: value for name, value in request.items() if name != key})


@pytest.mark.parametrize("retire", [False, True])
def test_two_owners_cannot_create_two_successors_of_one_retirement(active, retire):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    parent = recovery_request(source, armed)
    requests = [successor_request(parent, retired, number) for number in (2, 3)]
    other = _store(store.path, "other-successor", candidate=True)
    barrier = Barrier(2)
    def create(owner, request):
        barrier.wait(timeout=10)
        try:
            return (owner.retire_native_recovery_close_successor(retire_payload(request)) if retire
                else owner.prepare_native_recovery_close_successor(request))
        except UpdaterStoreError as error:
            return error
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, owner, request) for owner, request in zip((store, other), requests)]
            outcomes = [future.result() for future in futures]
        assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, UpdaterStoreError) for outcome in outcomes) == 1
        store.close()
        store.initialize()
        assert store.get_status()["jobGateState"] == "LOCKED"
    finally:
        other.close()


def test_upgraded_chain_still_rejects_second_root_for_same_first_open(active):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    store.retire_native_recovery_close_successor(retire_payload(request))
    from job_safety import NATIVE_RECOVERY_CLOSE_SUCCESSOR_FIELDS
    root = successor_request(request, store.get_physical_action({"actionUid": request["actionUid"]}), 3)
    root = {key: value for key, value in root.items() if key not in NATIVE_RECOVERY_CLOSE_SUCCESSOR_FIELDS}
    with pytest.raises(UpdaterStoreError, match="already has an identity"):
        store.prepare_native_recovery_close(root)
    with pytest.raises(UpdaterStoreError, match="already has an identity"):
        store.retire_native_recovery_close(retire_payload(root, 3))


@pytest.mark.parametrize("armed_successor", [False, True])
def test_operator_stop_blocks_successor_execution_but_allows_never_armed_retirement(active, armed_successor):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    store.prepare_native_recovery_close_successor(request)
    arm = {"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]}
    if armed_successor:
        store.arm_physical_action(arm)
    _operator_lock(store, 99)
    before = store.get_status()
    with pytest.raises(UpdaterStoreError, match="gate"):
        store.arm_physical_action(arm)
    if not armed_successor:
        assert store.retire_native_recovery_close_successor(retire_payload(request))["confirmedOutcome"] == "NOT_EXECUTED"
    else:
        with pytest.raises(UpdaterStoreError, match="armed"):
            store.retire_native_recovery_close_successor(retire_payload(request))
    assert store.get_status()["blockReasonCode"] == before["blockReasonCode"]


def stored_extension(path):
    with sqlite3.connect(path) as connection:
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='native_recovery_close'").fetchone()[0]
        rows = connection.execute("SELECT recovery_uid, action_uid, source_action_uid, payload_json, created_at FROM native_recovery_close ORDER BY recovery_uid").fetchall()
        return schema, rows


@pytest.mark.parametrize("retire,boundary", [(retire, boundary)
    for retire in (False, True) for boundary in ("rename", "create", "copy", "drop", "action", "confirm")
    if retire or boundary != "confirm"])
def test_first_successor_upgrade_and_action_rollback_together(active, retire, boundary):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    before = stored_extension(store.path)
    def deny(action, name, column, *_):
        blocked = ((boundary == "rename" and action == sqlite3.SQLITE_ALTER_TABLE and column == "native_recovery_close")
            or (boundary == "create" and action == sqlite3.SQLITE_CREATE_TABLE and name == "native_recovery_close")
            or (boundary == "copy" and action == sqlite3.SQLITE_INSERT and name == "native_recovery_close")
            or (boundary == "drop" and action == sqlite3.SQLITE_DROP_TABLE and name == "native_recovery_close_previous")
            or (boundary == "action" and action == sqlite3.SQLITE_INSERT and name == "physical_action_ledger")
            or (boundary == "confirm" and action == sqlite3.SQLITE_UPDATE and name == "physical_action_ledger" and column == "state"))
        return sqlite3.SQLITE_DENY if blocked else sqlite3.SQLITE_OK
    store._connection.set_authorizer(deny)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            if retire:
                store.retire_native_recovery_close_successor(retire_payload(request))
            else:
                store.prepare_native_recovery_close_successor(request)
    finally:
        store._connection.set_authorizer(None)
    store.close()
    store.initialize()
    assert stored_extension(store.path) == before
    with pytest.raises(UpdaterStoreError):
        store.get_physical_action({"actionUid": request["actionUid"]})
    result = (store.retire_native_recovery_close_successor(retire_payload(request)) if retire
        else store.prepare_native_recovery_close_successor(request))
    assert result["state"] == ("CONFIRMED" if retire else "PREPARED")
    after = stored_extension(store.path)
    assert "predecessor_action_uid" in after[0] and all(row in after[1] for row in before[1])


@pytest.mark.parametrize("candidate", [False, True])
def test_old_extension_is_readable_without_eager_upgrade_on_startup(active, candidate):
    store, source, armed = active
    store.retire_native_recovery_close(retirement_request(source, armed))
    before = stored_extension(store.path)
    assert "predecessor_action_uid" not in before[0]
    store.close()
    reopened = _store(store.path, "read-legacy", candidate=candidate)
    try:
        assert stored_extension(store.path) == before
        if candidate:
            assert reopened.get_native_recovery_close({"actionUid": _uid(60)})["sourceActionUid"] == source["actionUid"]
        else:
            request = successor_request(recovery_request(source, armed),
                {"ledgerSequence": armed["ledgerSequence"] + 1, "receiptUid": _uid(63), "evidenceDigestSha256": "9" * 64})
            with pytest.raises(UpdaterStoreError):
                reopened.prepare_native_recovery_close_successor(request)
            with pytest.raises(UpdaterStoreError):
                reopened.retire_native_recovery_close_successor(retire_payload(request))
            assert stored_extension(store.path) == before
    finally:
        reopened.close()


@pytest.mark.parametrize("damage", ["missing-parent", "missing-child", "parent-receipt", "parent-hash", "child-ancestry", "child-index"])
@pytest.mark.parametrize("operation", ["get", "arm", "reopen"])
def test_corrupt_ancestor_or_link_blocks_queries_arming_and_restart(active, damage, operation):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    second = store.retire_native_recovery_close_successor(retire_payload(request))
    third = successor_request(request, second, 3)
    store.prepare_native_recovery_close_successor(third)
    with sqlite3.connect(store.path) as connection:
        if damage in {"missing-parent", "missing-child"}:
            connection.execute("DELETE FROM native_recovery_close WHERE action_uid=?",
                (request["actionUid"] if damage == "missing-parent" else third["actionUid"],))
        elif damage in {"parent-receipt", "parent-hash"}:
            column, value = (("receipt_uid", _uid(199)) if damage == "parent-receipt" else ("evidence_digest_sha256", "f" * 64))
            connection.execute(f"UPDATE physical_action_ledger SET {column}=? WHERE action_uid=?", (value, _uid(60)))
        elif damage == "child-index":
            connection.execute("UPDATE native_recovery_close SET predecessor_action_uid=? WHERE action_uid=?", (_uid(199), third["actionUid"]))
        else:
            raw = connection.execute("SELECT payload_json FROM native_recovery_close WHERE action_uid=?", (third["actionUid"],)).fetchone()[0]
            fields = json.loads(raw)
            fields["predecessorRetirementEvidenceSha256"] = "f" * 64
            connection.execute("UPDATE native_recovery_close SET payload_json=? WHERE action_uid=?",
                (json.dumps(fields, sort_keys=True, separators=(",", ":")), third["actionUid"]))
    with pytest.raises(RuntimeError, match="native recovery|integrity check"):
        if operation == "get":
            store.get_native_recovery_close({"actionUid": third["actionUid"]})
        elif operation == "arm":
            store.arm_physical_action({"actionUid": third["actionUid"], "dispatchAttemptToken": third["dispatchAttemptToken"]})
        else:
            store.close()
            store.initialize()


@pytest.mark.parametrize("boundary", ["before-copy", "before-action", "before-confirm", "after-commit"])
def test_process_exit_rolls_back_upgrade_or_preserves_whole_successor_fence(active, boundary):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    before = stored_extension(store.path)
    store.close()
    code = r'''
import json, os, sqlite3, sys
from updater_store import UpdaterStore
path, raw, boundary = sys.argv[1:]
store = UpdaterStore(path, release_version='successor-crash', enable_stage4_candidate=True)
store.initialize()
def crash(action, name, column, *_):
    if ((boundary == 'before-copy' and action == sqlite3.SQLITE_INSERT and name == 'native_recovery_close')
        or (boundary == 'before-action' and action == sqlite3.SQLITE_INSERT and name == 'physical_action_ledger')
        or (boundary == 'before-confirm' and action == sqlite3.SQLITE_UPDATE and name == 'physical_action_ledger' and column == 'state')):
        os._exit(77)
    return sqlite3.SQLITE_OK
store._connection.set_authorizer(crash)
store.retire_native_recovery_close_successor(json.loads(raw))
assert boundary == 'after-commit'
os._exit(77)
'''
    result = subprocess.run([sys.executable, "-c", code, str(store.path), json.dumps(retire_payload(request)), boundary],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])), capture_output=True, text=True, timeout=20)
    assert result.returncode == 77, result.stdout + result.stderr
    store.initialize()
    if boundary == "after-commit":
        assert store.get_physical_action({"actionUid": request["actionUid"]})["confirmedOutcome"] == "NOT_EXECUTED"
        assert "predecessor_action_uid" in stored_extension(store.path)[0]
    else:
        assert stored_extension(store.path) == before
        with pytest.raises(UpdaterStoreError):
            store.get_physical_action({"actionUid": request["actionUid"]})
    assert store.retire_native_recovery_close_successor(retire_payload(request))["state"] == "CONFIRMED"
    assert store.get_status()["jobGateState"] == "LOCKED"


@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("committed", [False, True])
def test_lost_successor_rpc_reuses_same_identity_after_permanent_restart(active, retire, committed):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102},
        business_uids={3102}, enable_stage4_candidate=True)
    lost = True
    attempted = []
    operation = "RETIRE_NATIVE_RECOVERY_CLOSE_SUCCESSOR" if retire else "PREPARE_NATIVE_RECOVERY_CLOSE_SUCCESSOR"
    class Client:
        def request(self, name, payload):
            if name == operation:
                attempted.append(dict(payload))
                if lost:
                    if committed:
                        actions[name].handler(payload)
                    raise LocalControlUnavailable("successor RPC interrupted")
            return actions[name].handler(payload)
    safety = PermanentJobSafety(Client())
    permit = JobPermit(source["permitUid"], source["workUid"], source["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(request["actionUid"], retire_payload(request)["receiptUid"],
        request["actionKey"], "SAFE_CLOSE", request["actionDigestSha256"])
    evidence = {key: request[key] for key in NATIVE_RECOVERY_CLOSE_SUCCESSOR_EVIDENCE_FIELDS}
    def invoke():
        if retire:
            return safety.retire_native_recovery_close(permit, action=action, evidence=evidence,
                retirement_evidence_sha256="2" * 64)
        return safety.prepare_native_recovery_close(permit, action=action, evidence=evidence,
            dispatch_attempt_token=request["dispatchAttemptToken"])
    with pytest.raises(JobSafetyError):
        invoke()
    store.close()
    store.initialize()
    lost = False
    invoke()
    assert len(attempted) >= 2
    assert all(payload == (retire_payload(request) if retire else request) for payload in attempted)
    actual = store.get_physical_action({"actionUid": request["actionUid"]})
    assert actual["ledgerSequence"] == retired["ledgerSequence"] + 1
    assert actual["state"] == ("CONFIRMED" if retire else "PREPARED")
    assert actual["receiptUid"] == (action.receipt_uid if retire else None)
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


def test_armed_successor_is_not_reauthorized_after_permanent_restart(active):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    store.prepare_native_recovery_close_successor(request)
    arm = {"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]}
    first = store.arm_physical_action(arm)
    assert first["mayExecute"] is True
    assert store.arm_physical_action(arm)["mayExecute"] is True
    store.close()
    store.initialize()
    assert store.arm_physical_action(arm)["mayExecute"] is False
    assert store.get_physical_action({"actionUid": request["actionUid"]})["state"] == "ARMED"
    with pytest.raises(UpdaterStoreError, match="armed"):
        store.retire_native_recovery_close_successor(retire_payload(request))


@pytest.mark.parametrize("active", ["draining"], indirect=True)
def test_successor_preserves_original_update_draining_owner(active):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    before = store.get_status()
    store.prepare_native_recovery_close_successor(request)
    store.retire_native_recovery_close_successor(retire_payload(request))
    after = store.get_status()
    for key in ("maintenanceOwnerUid", "maintenancePhase", "jobGateState", "managementStateSequence"):
        assert after[key] == before[key]


def test_permanent_inventory_prepares_and_retires_successor_without_business_modules(active, tmp_path):
    from install.runtime_payload_manifest import DEVICE_UPDATER_FILES
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    store.close()
    stage = tmp_path / "isolated-successor"
    for name in DEVICE_UPDATER_FILES:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / name, target)
    code = r'''
import json, sys
from pathlib import Path
root, database, raw, retirement = sys.argv[1:]
root = Path(root).resolve()
sys.path.insert(0, str(root))
import updater_store, updater_agent, job_safety, uart2_protocol
for module in (updater_store, updater_agent, job_safety, uart2_protocol):
    assert Path(module.__file__).resolve().is_relative_to(root)
store = updater_store.UpdaterStore(database, release_version='isolated-successor', enable_stage4_candidate=True)
store.initialize()
request = json.loads(raw)
actions = updater_agent.build_control_actions(updater_agent.UpdaterControlHandler(store),
    allowed_uids={0, 3102}, business_uids={3102}, enable_stage4_candidate=True)
result = actions['PREPARE_NATIVE_RECOVERY_CLOSE_SUCCESSOR'].handler(request)
assert result['state'] == 'PREPARED' and result['mayExecute'] is False
result = actions['RETIRE_NATIVE_RECOVERY_CLOSE_SUCCESSOR'].handler(json.loads(retirement))
assert result['state'] == 'CONFIRMED' and result['confirmedOutcome'] == 'NOT_EXECUTED'
assert result['mayExecute'] is False
store.close()
store.initialize()
assert store.get_native_recovery_close({'actionUid': request['actionUid']})['predecessorActionUid'] == request['predecessorActionUid']
assert store.get_job_permit({'permitUid': request['permitUid']})['state'] == 'ACTIVE'
store.close()
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(stage), str(store.path),
        json.dumps(request), json.dumps(retire_payload(request))], cwd=stage,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    store.initialize()
    assert store.get_physical_action({"actionUid": request["actionUid"]})["confirmedOutcome"] == "NOT_EXECUTED"


@pytest.mark.parametrize("upgraded", [False, True])
@pytest.mark.parametrize("operation", ["prepare", "retire", "reopen"])
def test_unknown_extension_ddl_is_rejected_without_repair_or_promotion(active, upgraded, operation):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    parent = recovery_request(source, armed)
    request = successor_request(parent, retired)
    if upgraded:
        retired = store.retire_native_recovery_close_successor(retire_payload(request))
        request = successor_request(request, retired, 3)
    with sqlite3.connect(store.path) as connection:
        connection.execute("ALTER TABLE native_recovery_close ADD COLUMN unrecognized TEXT")
    before = stored_extension(store.path)
    with pytest.raises(RuntimeError, match="native recovery"):
        if operation == "prepare":
            store.prepare_native_recovery_close_successor(request)
        elif operation == "retire":
            store.retire_native_recovery_close_successor(retire_payload(request, 3 if upgraded else 2))
        else:
            store.close()
            store.initialize()
    assert stored_extension(store.path) == before


def test_confirmed_original_open_stays_confirmed_when_retired_close_is_replaced(active):
    store, source, armed = active
    store.confirm_physical_action(dict(actionUid=source["actionUid"], receiptUid=_uid(198),
        outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="e" * 64))
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    store.prepare_native_recovery_close_successor(request)
    assert store.arm_physical_action({"actionUid": request["actionUid"],
        "dispatchAttemptToken": request["dispatchAttemptToken"]})["mayExecute"] is True
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original


def test_successor_retirement_receipt_cannot_reuse_parent_receipt(active):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    before = stored_extension(store.path)
    with pytest.raises(UpdaterStoreError, match="receipt"):
        store.retire_native_recovery_close_successor(retire_payload(request) | {"receiptUid": retired["receiptUid"]})
    assert stored_extension(store.path) == before
    with pytest.raises(UpdaterStoreError):
        store.get_physical_action({"actionUid": request["actionUid"]})


@pytest.mark.parametrize("upgraded", [False, True])
@pytest.mark.parametrize("object_kind", ["index", "trigger"])
@pytest.mark.parametrize("operation", ["prepare", "retire", "reopen"])
def test_unknown_extension_index_or_trigger_is_preserved_and_rejected(active, upgraded, object_kind, operation):
    store, source, armed = active
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    request = successor_request(recovery_request(source, armed), retired)
    if upgraded:
        retired = store.retire_native_recovery_close_successor(retire_payload(request))
        request = successor_request(request, retired, 3)
    with sqlite3.connect(store.path) as connection:
        if object_kind == "index":
            connection.execute("CREATE INDEX review_extra_index ON native_recovery_close(created_at)")
        else:
            connection.execute("CREATE TRIGGER review_extra_trigger AFTER INSERT ON native_recovery_close BEGIN SELECT 1; END")
    def snapshot():
        with sqlite3.connect(store.path) as connection:
            metadata = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name='native_recovery_close' ORDER BY type, name").fetchall()
            actions = connection.execute("SELECT * FROM physical_action_ledger ORDER BY action_uid").fetchall()
            return stored_extension(store.path), metadata, actions
    before = snapshot()
    with pytest.raises(RuntimeError, match="native recovery"):
        if operation == "prepare":
            store.prepare_native_recovery_close_successor(request)
        elif operation == "retire":
            store.retire_native_recovery_close_successor(retire_payload(request, 3 if upgraded else 2))
        else:
            store.close()
            store.initialize()
    assert snapshot() == before
