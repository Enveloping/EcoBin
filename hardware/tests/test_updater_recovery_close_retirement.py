"""Pure cancellation fences for native closes; real permanent SQLite authority."""
import pytest
import sqlite3
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from hardware.tests.test_updater_native_recovery_close import active, recovery_request
from hardware.tests.test_updater_native_recovery_close import command_payload, wire_digest
from hardware.tests.test_updater_store import _uid
from job_safety import JobPermit, PhysicalAction, PermanentJobSafety
from updater_agent import UpdaterControlHandler, build_control_actions
from updater_store import UpdaterStoreError
from job_safety import JobSafetyError, NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS
from local_control import LocalControlUnavailable
from hardware.tests.test_updater_store import _operator_lock, _confirmation_payload, _store
from hardware.tests.test_updater_store import _unknown_effect_quarantine_payload


def retirement_request(source, armed):
    request = recovery_request(source, armed)
    request.pop("dispatchAttemptToken")
    return request | {"receiptUid": _uid(63), "retirementEvidenceSha256": "9" * 64}


def test_prepared_close_is_retired_without_old_token_or_releasing_original_job(active):
    store, source, armed = active
    prepared = recovery_request(source, armed)
    store.prepare_native_recovery_close(prepared)
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    result = store.retire_native_recovery_close(retirement_request(source, armed))
    assert result["state"] == "CONFIRMED"
    assert result["confirmedOutcome"] == "NOT_EXECUTED"
    assert result["confirmationBasis"] == "PREPARED_NOT_ARMED"
    assert result["receiptUid"] == _uid(63)
    assert result["evidenceDigestSha256"] == "9" * 64
    assert result["mayExecute"] is False
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"
    assert store.get_status()["jobGateState"] == "LOCKED"


def test_business_rpc_retires_exact_original_action_and_is_candidate_role_only(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102},
        business_uids={3102}, enable_stage4_candidate=True)
    class Client:
        def request(self, operation, body):
            rpc = actions[operation]
            assert rpc.allowed_uids == frozenset({3102})
            assert set(body) == rpc.payload_fields
            return rpc.handler(body)
    safety = PermanentJobSafety(Client())
    permit = JobPermit(source["permitUid"], source["workUid"], source["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(request["actionUid"], _uid(63), request["actionKey"], "SAFE_CLOSE", request["actionDigestSha256"])
    from job_safety import NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS
    evidence = {key: request[key] for key in NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS}
    result = safety.retire_native_recovery_close(permit, action=action, evidence=evidence,
        retirement_evidence_sha256="9" * 64)
    assert result["state"] == "CONFIRMED" and result["mayExecute"] is False
    assert "RETIRE_NATIVE_RECOVERY_CLOSE" not in build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102})


def test_absent_close_installs_atomic_fence_against_late_prepare_and_arm(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    retired = store.retire_native_recovery_close(retirement_request(source, armed))
    assert retired["state"] == "CONFIRMED" and retired["confirmedOutcome"] == "NOT_EXECUTED"
    assert store.get_native_recovery_close({"actionUid": request["actionUid"]}) == {
        key: value for key, value in request.items() if key != "dispatchAttemptToken"
    } | {"disposition": "FOUND"}
    late = store.prepare_native_recovery_close(request)
    assert late["state"] == "CONFIRMED" and late["mayExecute"] is False
    assert store.arm_physical_action({"actionUid": request["actionUid"],
        "dispatchAttemptToken": request["dispatchAttemptToken"]})["mayExecute"] is False
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"
    assert store.get_status()["jobGateState"] == "LOCKED"


def test_first_retirement_rejects_prepared_close_if_original_permit_is_no_longer_active(active):
    store, source, armed = active
    store.prepare_native_recovery_close(recovery_request(source, armed))
    # Adversarial externally changed custody, not a supported completion path.
    with sqlite3.connect(store.path) as connection:
        connection.execute("""UPDATE job_permit SET state='COMPLETED', completion_uid=?,
            completion_outcome='CANCELLED', completion_digest_sha256=? WHERE permit_uid=?""",
            (_uid(80), "a" * 64, source["permitUid"]))
    with pytest.raises(UpdaterStoreError, match="original active"):
        store.retire_native_recovery_close(retirement_request(source, armed))
    assert store.get_physical_action({"actionUid": _uid(60)})["state"] == "PREPARED"


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("stopped", [False, True])
def test_reply_loss_restart_and_operator_stop_keep_same_retirement_without_unlocking(active, prepared, stopped):
    store, source, armed = active
    if prepared:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    if stopped:
        _operator_lock(store, 99)
    status = store.get_status()
    first = store.retire_native_recovery_close(retirement_request(source, armed))
    store.close()
    store.initialize()
    again = store.retire_native_recovery_close(retirement_request(source, armed))
    assert again == first | {"disposition": "DUPLICATE"}
    assert store.get_status()["jobGateState"] == "LOCKED"
    if stopped:
        assert store.get_status()["blockReasonCode"] == status["blockReasonCode"]
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


@pytest.mark.parametrize("active", ["draining"], indirect=True)
def test_retirement_preserves_update_draining_owner(active):
    store, source, armed = active
    before = store.get_status()
    store.retire_native_recovery_close(retirement_request(source, armed))
    after = store.get_status()
    for key in ("maintenanceOwnerUid", "maintenancePhase", "jobGateState", "managementStateSequence"):
        assert after[key] == before[key]


def test_identical_historical_retirement_remains_queryable_after_original_job_completed(active):
    store, source, armed = active
    request = retirement_request(source, armed)
    original = store.retire_native_recovery_close(request)
    store.confirm_physical_action(_confirmation_payload())
    store.complete_job(dict(permitUid=source["permitUid"], completionUid=_uid(80),
        outcome="CANCELLED", completionDigestSha256="a" * 64))
    store.close()
    store.initialize()
    status = store.get_status()
    assert store.retire_native_recovery_close(request) == original | {"disposition": "DUPLICATE"}
    assert store.get_status() == status


@pytest.mark.parametrize("first", ["arm", "retire", "race"])
def test_arm_and_retirement_are_mutually_exclusive_across_two_sqlite_owners(active, first):
    store, source, armed = active
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    other = _store(store.path, "second-permanent", candidate=True)
    barrier = Barrier(2)
    def arm():
        if first == "race":
            barrier.wait(timeout=10)
        return store.arm_physical_action({"actionUid": request["actionUid"],
            "dispatchAttemptToken": request["dispatchAttemptToken"]})
    def retire():
        if first == "race":
            barrier.wait(timeout=10)
        try:
            return other.retire_native_recovery_close(retirement_request(source, armed))
        except UpdaterStoreError as error:
            return error
    try:
        if first == "race":
            with ThreadPoolExecutor(max_workers=2) as pool:
                armed_future, retired_future = pool.submit(arm), pool.submit(retire)
                armed_result, retired_result = armed_future.result(), retired_future.result()
        elif first == "arm":
            armed_result, retired_result = arm(), retire()
        else:
            retired_result, armed_result = retire(), arm()
        final = store.get_physical_action({"actionUid": request["actionUid"]})
        if armed_result["mayExecute"]:
            assert final["state"] == "ARMED" and final["confirmedOutcome"] is None
            assert isinstance(retired_result, UpdaterStoreError)
            assert retired_result.code == "PHYSICAL_ACTION_ALREADY_ARMED"
        else:
            assert retired_result["state"] == "CONFIRMED"
            assert final["state"] == "CONFIRMED" and final["confirmedOutcome"] == "NOT_EXECUTED"
        assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"
    finally:
        other.close()


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("key,value", [
    ("permitUid", _uid(90)), ("workUid", _uid(90)), ("commandUid", _uid(90)),
    ("sourceActionUid", _uid(90)), ("sourceActionDigestSha256", "f" * 64),
    ("expectedSourceLedgerSequence", 999), ("actionKind", "UNLOCK_CLEAN_DOOR"),
    ("sourceCommandPayloadHex", "00"), ("closeCommandPayloadHex", "00"),
    ("actionDigestSha256", "f" * 64), ("portNo", 2), ("reason", "UART_TIMEOUT"),
    ("retirementEvidenceSha256", "bad"), ("receiptUid", "bad"),
    ("recoveryEvidenceSha256", "bad"), ("dispatchAttemptToken", "B" * 43),
])
def test_invalid_retirement_cannot_cancel_or_install_a_fence(active, prepared, key, value):
    store, source, armed = active
    if prepared:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    with pytest.raises(UpdaterStoreError):
        store.retire_native_recovery_close(retirement_request(source, armed) | {key: value})
    if prepared:
        assert store.get_physical_action({"actionUid": _uid(60)})["state"] == "PREPARED"
    else:
        with pytest.raises(UpdaterStoreError):
            store.get_physical_action({"actionUid": _uid(60)})
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original


@pytest.mark.parametrize("key,value", [("receiptUid", _uid(80)), ("retirementEvidenceSha256", "8" * 64),
    ("recoveryEvidenceSha256", "8" * 64)])
def test_confirmed_retirement_is_immutable_even_for_same_action(active, key, value):
    store, source, armed = active
    request = retirement_request(source, armed)
    store.retire_native_recovery_close(request)
    before = store.get_physical_action({"actionUid": _uid(60)})
    with pytest.raises(UpdaterStoreError):
        store.retire_native_recovery_close(request | {key: value})
    assert store.get_physical_action({"actionUid": _uid(60)}) == before


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("outcome", ["NOT_EXECUTED", "FAILED_SAFE"])
def test_original_nonexecuted_result_does_not_prevent_pure_retirement_or_grant_new_execution(active, prepared, outcome):
    store, source, armed = active
    if prepared:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    if outcome == "NOT_EXECUTED":
        store.abort_physical_action_dispatch(dict(actionUid=source["actionUid"], receiptUid=_uid(7),
            dispatchAttemptToken=source["dispatchAttemptToken"], evidenceDigestSha256="d" * 64))
    else:
        store.confirm_physical_action(_confirmation_payload(outcome=outcome))
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    status = store.get_status()
    result = store.retire_native_recovery_close(retirement_request(source, armed))
    assert result["confirmedOutcome"] == "NOT_EXECUTED"
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert store.get_status()["jobGateState"] == status["jobGateState"] == "LOCKED"
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


@pytest.mark.parametrize("prepared", [False, True])
def test_original_manual_unknown_resolution_is_not_changed_by_retiring_close(active, prepared):
    store, source, armed = active
    if prepared:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    quarantine = _unknown_effect_quarantine_payload() | {
        "actionKind": source["actionKind"], "actionDigestSha256": source["actionDigestSha256"],
        "expectedLedgerSequence": armed["ledgerSequence"]}
    store.quarantine_unknown_physical_action(quarantine)
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    assert original["state"] == "ARMED" and original["unknownEffectResolution"] is not None
    before = store.get_status()
    result = store.retire_native_recovery_close(retirement_request(source, armed))
    assert result["confirmedOutcome"] == "NOT_EXECUTED"
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    for key in ("jobGateState", "blockReasonCode", "managementStateSequence"):
        assert store.get_status()[key] == before[key]


@pytest.mark.parametrize("method", ["cancel_prepared_physical_action", "abort_physical_action_dispatch"])
def test_old_cancellation_api_still_requires_original_live_token(active, method):
    store, source, armed = active
    store.prepare_native_recovery_close(recovery_request(source, armed))
    with pytest.raises(UpdaterStoreError):
        getattr(store, method)(dict(actionUid=_uid(60), receiptUid=_uid(63),
            dispatchAttemptToken="X" * 43, evidenceDigestSha256="9" * 64))
    assert store.get_physical_action({"actionUid": _uid(60)})["state"] == "PREPARED"


@pytest.mark.parametrize("prepared", [False, True])
def test_confirmation_write_failure_rolls_back_whole_retirement(active, prepared):
    store, source, armed = active
    if prepared:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    def deny(action, name, column, *_):
        if action == sqlite3.SQLITE_UPDATE and name == "physical_action_ledger" and column == "state":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    store._connection.set_authorizer(deny)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            store.retire_native_recovery_close(retirement_request(source, armed))
    finally:
        store._connection.set_authorizer(None)
    store.close()
    store.initialize()
    if prepared:
        assert store.get_physical_action({"actionUid": _uid(60)})["state"] == "PREPARED"
    else:
        with pytest.raises(UpdaterStoreError):
            store.get_physical_action({"actionUid": _uid(60)})
        with pytest.raises(UpdaterStoreError):
            store.get_native_recovery_close({"actionUid": _uid(60)})
    assert store.retire_native_recovery_close(retirement_request(source, armed))["state"] == "CONFIRMED"


def retirement_client(store, source, armed, transform):
    request = recovery_request(source, armed)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102},
        business_uids={3102}, enable_stage4_candidate=True)
    class Client:
        def request(self, operation, payload):
            return transform(operation, payload, lambda: actions[operation].handler(payload))
    permit = JobPermit(source["permitUid"], source["workUid"], source["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(request["actionUid"], _uid(63), request["actionKey"], "SAFE_CLOSE", request["actionDigestSha256"])
    evidence = {key: request[key] for key in NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS}
    def retire():
        return PermanentJobSafety(Client()).retire_native_recovery_close(permit,
            action=action, evidence=evidence, retirement_evidence_sha256="9" * 64)
    return retire


@pytest.mark.parametrize("committed", [False, True])
def test_lost_retirement_rpc_retries_same_receipt_after_restart(active, committed):
    store, source, armed = active
    lose = True
    attempted = []
    def transport(operation, payload, deliver):
        if operation == "RETIRE_NATIVE_RECOVERY_CLOSE" and lose:
            attempted.append(payload)
            if committed:
                deliver()
            raise LocalControlUnavailable("synthetic local RPC interruption")
        return deliver()
    retire = retirement_client(store, source, armed, transport)
    with pytest.raises(JobSafetyError):
        retire()
    assert attempted and all(payload == retirement_request(source, armed) for payload in attempted)
    store.close()
    store.initialize()
    lose = False
    result = retire()
    assert result["state"] == "CONFIRMED" and result["receiptUid"] == _uid(63)
    assert result["ledgerSequence"] == armed["ledgerSequence"] + 1
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


@pytest.mark.parametrize("key,value", [
    ("actionUid", _uid(99)), ("permitUid", _uid(99)), ("workUid", _uid(99)), ("commandUid", _uid(99)),
    ("actionKey", "old-open"), ("actionKind", "AUTHORIZE_DELIVERY_FIRST_OPEN"),
    ("actionDigestSha256", "f" * 64), ("receiptUid", _uid(99)),
    ("evidenceDigestSha256", "f" * 64), ("state", "PREPARED"), ("dispatchMode", "TWO_PHASE_V3"),
    ("confirmedOutcome", "EXECUTED"), ("confirmationBasis", "MCU_IDENTITY_BOUND_FACT"),
    ("mayExecute", True), ("mayExecute", 0), ("disposition", "DENIED"),
])
def test_client_rejects_retirement_ack_that_does_not_prove_exact_original_nonexecution(active, key, value):
    store, source, armed = active
    def transport(operation, payload, deliver):
        result = deliver()
        return result | {key: value} if operation == "RETIRE_NATIVE_RECOVERY_CLOSE" else result
    with pytest.raises(JobSafetyError, match="NATIVE_RECOVERY_NOT_RETIRED"):
        retirement_client(store, source, armed, transport)()


def test_client_rejects_conflicting_binding_after_exact_retirement_ack(active):
    store, source, armed = active
    def transport(operation, payload, deliver):
        result = deliver()
        return result | {"recoveryEvidenceSha256": "f" * 64} if operation == "GET_NATIVE_RECOVERY_CLOSE" else result
    with pytest.raises(JobSafetyError, match="NATIVE_RECOVERY_EVIDENCE_CONFLICT"):
        retirement_client(store, source, armed, transport)()


@pytest.mark.parametrize("boundary", ["before-action", "before-binding", "before-confirm", "after-commit"])
def test_process_exit_leaves_entire_retirement_fence_or_no_close(active, boundary):
    store, source, armed = active
    payload = retirement_request(source, armed)
    store.close()
    code = r'''
import json, os, sqlite3, sys
from updater_store import UpdaterStore
path, payload, boundary = sys.argv[1:]
store = UpdaterStore(path, release_version='retirement-crash', enable_stage4_candidate=True)
store.initialize()
def crash(action, name, column, *_):
    if (action == sqlite3.SQLITE_INSERT and
            ((boundary == 'before-action' and name == 'physical_action_ledger') or
             (boundary == 'before-binding' and name == 'native_recovery_close'))) or (
            boundary == 'before-confirm' and action == sqlite3.SQLITE_UPDATE and
            name == 'physical_action_ledger' and column == 'state'):
        os._exit(77)
    return sqlite3.SQLITE_OK
store._connection.set_authorizer(crash)
store.retire_native_recovery_close(json.loads(payload))
assert boundary == 'after-commit'
os._exit(77)
'''
    result = subprocess.run([sys.executable, "-c", code, str(store.path), json.dumps(payload), boundary],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 77, result.stdout + result.stderr
    store.initialize()
    if boundary == "after-commit":
        assert store.get_physical_action({"actionUid": _uid(60)})["state"] == "CONFIRMED"
    else:
        with pytest.raises(UpdaterStoreError):
            store.get_physical_action({"actionUid": _uid(60)})
        with pytest.raises(UpdaterStoreError):
            store.get_native_recovery_close({"actionUid": _uid(60)})
    retried = store.retire_native_recovery_close(payload)
    assert retried["ledgerSequence"] == armed["ledgerSequence"] + 1
    assert retried["confirmedOutcome"] == "NOT_EXECUTED"
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


def test_permanent_inventory_can_retire_without_replaceable_business_modules(active, tmp_path):
    from install.runtime_payload_manifest import DEVICE_UPDATER_FILES
    store, source, armed = active
    store.close()
    stage = tmp_path / "isolated"
    for name in DEVICE_UPDATER_FILES:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / name, target)
    code = r'''
import json, sys
from pathlib import Path
root, database, payload = sys.argv[1:]
root = Path(root).resolve()
sys.path.insert(0, str(root))
import updater_store, updater_agent, job_safety, uart2_protocol
for module in (updater_store, updater_agent, job_safety, uart2_protocol):
    assert Path(module.__file__).resolve().is_relative_to(root)
store = updater_store.UpdaterStore(database, release_version='isolated', enable_stage4_candidate=True)
store.initialize()
payload = json.loads(payload)
result = store.retire_native_recovery_close(payload)
assert result['state'] == 'CONFIRMED' and result['confirmedOutcome'] == 'NOT_EXECUTED'
assert result['mayExecute'] is False
assert store.get_job_permit({'permitUid': payload['permitUid']})['state'] == 'ACTIVE'
store.close()
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(stage), str(store.path),
        json.dumps(retirement_request(source, armed))], cwd=stage, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    store.initialize()


@pytest.mark.parametrize("prepared", [False, True])
def test_receipt_collision_cannot_partially_retire_or_create_close(active, prepared):
    store, source, armed = active
    if prepared:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    store.confirm_physical_action(_confirmation_payload(receipt_number=63))
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    with pytest.raises(UpdaterStoreError) as conflict:
        store.retire_native_recovery_close(retirement_request(source, armed))
    assert conflict.value.code == "PHYSICAL_ACTION_RECEIPT_CONFLICT"
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    if prepared:
        assert store.get_physical_action({"actionUid": _uid(60)})["state"] == "PREPARED"
    else:
        with pytest.raises(UpdaterStoreError):
            store.get_physical_action({"actionUid": _uid(60)})


def test_one_original_source_cannot_be_aliased_into_another_retirement(active):
    store, source, armed = active
    first = retirement_request(source, armed)
    original = store.retire_native_recovery_close(first)
    second = first | {"actionUid": _uid(80), "recoveryUid": _uid(81),
        "actionKey": f"native:recovery-close:{_uid(81)}", "receiptUid": _uid(82)}
    raw = command_payload("SAFE_CLOSE", second["actionUid"], 2,
        dict(scope="SINGLE_DELIVERY_DOOR", portNo=1, executionDeadlineMs=5000))
    second.update(closeCommandPayloadHex=raw.hex(), actionDigestSha256=wire_digest(second, raw))
    with pytest.raises(UpdaterStoreError) as conflict:
        store.retire_native_recovery_close(second)
    assert conflict.value.code == "NATIVE_RECOVERY_IDENTITY_CONFLICT"
    with pytest.raises(UpdaterStoreError):
        store.get_physical_action({"actionUid": _uid(80)})
    assert store.retire_native_recovery_close(first) == original | {"disposition": "DUPLICATE"}


def test_store_default_off_cannot_install_a_retirement_fence(active):
    store, source, armed = active
    store.close()
    disabled = _store(store.path, "default-off", candidate=False)
    try:
        with pytest.raises(UpdaterStoreError):
            disabled.retire_native_recovery_close(retirement_request(source, armed))
    finally:
        disabled.close()
    store.initialize()
    with pytest.raises(UpdaterStoreError):
        store.get_physical_action({"actionUid": _uid(60)})
