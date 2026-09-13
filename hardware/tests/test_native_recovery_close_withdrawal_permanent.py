"""Authorized-but-unclaimed closes retain authorization history after withdrawal."""
import pytest
import sqlite3
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from hardware.tests.test_updater_store import _uid, _operator_lock, _store, _unknown_effect_quarantine_payload
from job_safety import JobPermit, PhysicalAction, PermanentJobSafety, NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS
from updater_agent import UpdaterControlHandler, build_control_actions
from updater_store import UpdaterStoreError

from hardware.tests.test_updater_native_recovery_close import active, recovery_request
from hardware.tests.test_updater_recovery_close_retirement import retirement_request
from hardware.tests.test_updater_recovery_close_successor import successor_request, retire_payload


def armed_close(active):
    store, source, source_armed = active
    request = recovery_request(source, source_armed)
    store.prepare_native_recovery_close(request)
    store.arm_physical_action({"actionUid": request["actionUid"],
        "dispatchAttemptToken": request["dispatchAttemptToken"]})
    return store, request, retirement_request(source, source_armed)


def test_withdrawal_preserves_original_authorization_and_original_job(active):
    store, request, withdrawal = armed_close(active)
    before = store.get_physical_action({"actionUid": request["actionUid"]})
    status = store.get_status()
    result = store.withdraw_native_recovery_close_dispatch(withdrawal)
    assert result["state"] == "DISPATCH_WITHDRAWN"
    assert result["dispositionBasis"] == "AUTHORIZED_DISPATCH_WITHDRAWN_BEFORE_WRITE_CLAIM"
    assert result["ledgerSequence"] == before["ledgerSequence"]
    assert result["receiptUid"] == withdrawal["receiptUid"]
    assert result["evidenceDigestSha256"] == withdrawal["retirementEvidenceSha256"]
    assert result["mayExecute"] is False
    assert store.get_physical_action({"actionUid": request["actionUid"]}) == before
    assert store.get_status() == status
    assert store.get_job_permit({"permitUid": request["permitUid"]})["state"] == "ACTIVE"
    assert store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]}) == result | {"disposition": "FOUND"}
    assert store.withdraw_native_recovery_close_dispatch(withdrawal) == result | {"disposition": "DUPLICATE"}


def test_withdrawn_authority_cannot_be_rearmed_or_rewritten(active):
    store, request, withdrawal = armed_close(active)
    store.withdraw_native_recovery_close_dispatch(withdrawal)
    before = store.get_physical_action({"actionUid": request["actionUid"]})
    assert store.arm_physical_action({"actionUid": request["actionUid"],
        "dispatchAttemptToken": request["dispatchAttemptToken"]})["mayExecute"] is False
    assert store.prepare_native_recovery_close(request)["mayExecute"] is False
    with pytest.raises(UpdaterStoreError, match="withdrawn"):
        store.confirm_physical_action(dict(actionUid=request["actionUid"], receiptUid=withdrawal["receiptUid"],
            outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="a" * 64))
    assert store.get_physical_action({"actionUid": request["actionUid"]}) == before


def test_authorized_withdrawn_parent_allows_independent_successor_only(active):
    store, request, withdrawal = armed_close(active)
    first = store.withdraw_native_recovery_close_dispatch(withdrawal)
    child = successor_request(request, first)
    assert store.prepare_native_recovery_close_successor(child)["state"] == "PREPARED"
    assert store.arm_physical_action({"actionUid": child["actionUid"],
        "dispatchAttemptToken": child["dispatchAttemptToken"]})["mayExecute"] is True
    assert store.get_physical_action({"actionUid": request["actionUid"]})["state"] == "ARMED"
    second = store.withdraw_native_recovery_close_successor_dispatch(retire_payload(child))
    third = successor_request(child, second, 3)
    assert store.prepare_native_recovery_close_successor(third)["state"] == "PREPARED"
    assert store.arm_physical_action({"actionUid": third["actionUid"],
        "dispatchAttemptToken": third["dispatchAttemptToken"]})["mayExecute"] is True
    store.close()
    store.initialize()
    assert store.get_native_recovery_close_disposition({"actionUid": child["actionUid"]}) == second | {"disposition": "FOUND"}


@pytest.mark.parametrize("operation", ["abort", "live", "quarantine", "generic_prepare"])
def test_all_legacy_mutations_preserve_withdrawn_authorization(active, operation):
    store, request, withdrawal = armed_close(active)
    store.withdraw_native_recovery_close_dispatch(withdrawal)
    before = store.get_physical_action({"actionUid": request["actionUid"]})
    payload = dict(actionUid=request["actionUid"], receiptUid=withdrawal["receiptUid"],
        dispatchAttemptToken=request["dispatchAttemptToken"], evidenceDigestSha256="8" * 64)
    if operation == "generic_prepare":
        assert store.prepare_physical_action(request)["mayExecute"] is False
    else:
        with pytest.raises(UpdaterStoreError, match="withdrawn"):
            if operation == "abort":
                store.abort_physical_action_dispatch(payload)
            elif operation == "live":
                store.confirm_live_physical_action_result(payload | {"outcome": "EXECUTED"})
            else:
                store.quarantine_unknown_physical_action(dict(resolutionUid=_uid(190), actionUid=request["actionUid"],
                    permitUid=request["permitUid"], workUid=request["workUid"], commandUid=request["commandUid"],
                    actionKey=request["actionKey"], actionKind=request["actionKind"],
                    actionDigestSha256=request["actionDigestSha256"], expectedLedgerSequence=before["ledgerSequence"],
                    evidenceDigestSha256="8" * 64))
    assert store.get_physical_action({"actionUid": request["actionUid"]}) == before


@pytest.mark.parametrize("column,value", [("armed_at", "2000-01-01T00:00:00Z"),
    ("dispatch_attempt_token_sha256", "f" * 64), ("arm_runtime_instance_uid", _uid(195)),
    ("updated_at", "2000-01-01T00:00:00Z")])
def test_withdrawal_rejects_changed_original_authorization_on_restart_and_arm(active, column, value):
    store, request, withdrawal = armed_close(active)
    store.withdraw_native_recovery_close_dispatch(withdrawal)
    with sqlite3.connect(store.path) as db:
        db.execute(f"UPDATE physical_action_ledger SET {column}=? WHERE action_uid=?", (value, request["actionUid"]))
    with pytest.raises(RuntimeError, match="native recovery close disposition"):
        store.arm_physical_action({"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]})
    store.close()
    with pytest.raises(RuntimeError, match="native recovery close disposition|database integrity"):
        store.initialize()


@pytest.mark.parametrize("key,value", [("mayExecute", 0), ("portNo", True), ("sourceMcuBootId", True),
    ("ledgerSequence", 2.0), ("dispositionBasis", "PREPARED_NOT_ARMED")])
def test_rehashed_disposition_type_or_basis_changes_are_not_accepted(active, key, value):
    store, request, withdrawal = armed_close(active)
    store.withdraw_native_recovery_close_dispatch(withdrawal)
    with sqlite3.connect(store.path) as db:
        raw = json.loads(db.execute("SELECT payload_json FROM native_recovery_close_disposition").fetchone()[0])
        raw[key] = value
        text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        db.execute("UPDATE native_recovery_close_disposition SET payload_json=?, payload_sha256=?",
            (text, hashlib.sha256(text.encode("ascii")).hexdigest()))
    with pytest.raises(RuntimeError, match="native recovery close disposition"):
        store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]})


def test_lost_reply_restart_and_operator_stop_keep_same_fact_without_opening_gate(active):
    store, request, withdrawal = armed_close(active)
    _operator_lock(store, 199)
    before = store.get_status()
    result = store.withdraw_native_recovery_close_dispatch(withdrawal)
    store.close()
    store.initialize()
    assert store.withdraw_native_recovery_close_dispatch(withdrawal) == result | {"disposition": "DUPLICATE"}
    assert store.get_status()["jobGateState"] == "LOCKED"
    assert store.get_status()["blockReasonCode"] == before["blockReasonCode"]
    with pytest.raises(UpdaterStoreError, match="job gate"):
        store.prepare_native_recovery_close_successor(successor_request(request, result))


def test_candidate_business_only_client_routes_exact_withdrawal_and_readback(active):
    store, request, withdrawal = armed_close(active)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102},
        business_uids={3102}, enable_stage4_candidate=True)
    class Client:
        def request(self, operation, body):
            rpc = actions[operation]
            assert rpc.allowed_uids == frozenset({3102})
            assert set(body) == rpc.payload_fields
            return rpc.handler(body)
    safety = PermanentJobSafety(Client())
    permit = JobPermit(request["permitUid"], request["workUid"], request["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(request["actionUid"], withdrawal["receiptUid"], request["actionKey"],
        "SAFE_CLOSE", request["actionDigestSha256"])
    result = safety.withdraw_native_recovery_close_dispatch(permit, action=action,
        evidence={key: request[key] for key in NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS}, retirement_evidence_sha256="9" * 64)
    assert result["state"] == "DISPATCH_WITHDRAWN"
    disabled = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102})
    assert not {"WITHDRAW_NATIVE_RECOVERY_CLOSE_DISPATCH", "WITHDRAW_NATIVE_RECOVERY_CLOSE_SUCCESSOR_DISPATCH",
        "GET_NATIVE_RECOVERY_CLOSE_DISPOSITION"} & set(disabled)


@pytest.mark.parametrize("key,value", [("receiptUid", _uid(199)), ("retirementEvidenceSha256", "a" * 64),
    ("expectedSourceLedgerSequence", 999), ("workUid", _uid(198)), ("recoveryEvidenceSha256", "b" * 64)])
def test_repeat_with_changed_identity_or_evidence_cannot_replace_original_fact(active, key, value):
    store, request, withdrawal = armed_close(active)
    original = store.withdraw_native_recovery_close_dispatch(withdrawal)
    with pytest.raises(UpdaterStoreError):
        store.withdraw_native_recovery_close_dispatch(withdrawal | {key: value})
    assert store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]}) == original | {"disposition": "FOUND"}


@pytest.mark.parametrize("stage", ["absent", "prepared", "confirmed"])
def test_withdrawal_does_not_relabel_other_authorization_states(active, stage):
    store, source, source_armed = active
    request = recovery_request(source, source_armed)
    if stage != "absent":
        store.prepare_native_recovery_close(request)
    if stage == "confirmed":
        store.arm_physical_action({"actionUid": request["actionUid"], "dispatchAttemptToken": request["dispatchAttemptToken"]})
        store.confirm_physical_action(dict(actionUid=request["actionUid"], receiptUid=_uid(190),
            outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="b" * 64))
    with pytest.raises(UpdaterStoreError):
        store.withdraw_native_recovery_close_dispatch(retirement_request(source, source_armed))
    with pytest.raises(UpdaterStoreError, match="does not exist"):
        store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]})


@pytest.mark.parametrize("manual", [False, True])
def test_withdrawal_receipt_cannot_be_reused_by_original_source_confirmation_or_manual_resolution(active, manual):
    store, request, withdrawal = armed_close(active)
    store.withdraw_native_recovery_close_dispatch(withdrawal)
    source = active[1]
    with pytest.raises(UpdaterStoreError, match="receipt"):
        if manual:
            store.quarantine_unknown_physical_action(dict(resolutionUid=withdrawal["receiptUid"],
                **{key: source[key] for key in ("actionUid", "permitUid", "workUid", "commandUid", "actionKey", "actionKind", "actionDigestSha256")},
                expectedLedgerSequence=active[2]["ledgerSequence"], evidenceDigestSha256="b" * 64))
        else:
            store.confirm_physical_action(dict(actionUid=source["actionUid"], receiptUid=withdrawal["receiptUid"],
                outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="b" * 64))
    assert store.get_physical_action({"actionUid": source["actionUid"]})["state"] == "ARMED"


def test_receipt_already_used_for_original_source_cannot_become_withdrawal_receipt(active):
    store, request, withdrawal = armed_close(active)
    store.confirm_physical_action(dict(actionUid=active[1]["actionUid"], receiptUid=withdrawal["receiptUid"],
        outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="b" * 64))
    with pytest.raises(UpdaterStoreError, match="receipt"):
        store.withdraw_native_recovery_close_dispatch(withdrawal)


def test_damaged_receipt_index_cannot_hide_withdrawal_from_later_confirmation(active):
    store, request, withdrawal = armed_close(active)
    store.withdraw_native_recovery_close_dispatch(withdrawal)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_recovery_close_disposition SET receipt_uid=?", (_uid(197),))
    with pytest.raises(RuntimeError, match="native recovery close disposition"):
        store.confirm_physical_action(dict(actionUid=active[1]["actionUid"], receiptUid=withdrawal["receiptUid"],
            outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="b" * 64))


def test_withdrawal_and_actual_confirmation_serialize_across_two_permanent_owners(active):
    store, request, withdrawal = armed_close(active)
    second = _store(store.path, "competing-updater", candidate=True)
    barrier = Barrier(2)
    def withdraw():
        barrier.wait()
        try:
            return store.withdraw_native_recovery_close_dispatch(withdrawal)["state"]
        except UpdaterStoreError:
            return "DENIED"
    def confirm():
        barrier.wait()
        try:
            return second.confirm_physical_action(dict(actionUid=request["actionUid"], receiptUid=_uid(191),
                outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="a" * 64))["state"]
        except UpdaterStoreError:
            return "DENIED"
    try:
        with ThreadPoolExecutor(2) as pool:
            one, two = pool.submit(withdraw), pool.submit(confirm)
            outcomes = (one.result(), two.result())
        assert outcomes in {("DISPATCH_WITHDRAWN", "DENIED"), ("DENIED", "CONFIRMED")}
        if outcomes[0] == "DISPATCH_WITHDRAWN":
            assert store.get_physical_action({"actionUid": request["actionUid"]})["state"] == "ARMED"
        else:
            with pytest.raises(UpdaterStoreError, match="does not exist"):
                store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]})
    finally:
        second.close()
