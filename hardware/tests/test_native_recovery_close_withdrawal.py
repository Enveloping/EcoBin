"""Authorized but unclaimed recovery closes withdraw dispatch, not history."""
import pytest
from edge_store import EdgeStore
from job_safety import JobSafetyError
from local_control import LocalControlUnavailable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator


def armed_unclaimed(case, wire, boot):
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    token = owner.live[uid][0]
    case.safety.prepare_native_recovery_close(binding["permit"], action=binding["action"],
        evidence=binding["evidence"], dispatch_attempt_token=token)
    case.safety.arm_physical_action(binding["action"], dispatch_attempt_token=token)
    return owner, binding


def test_authorized_withdrawal_preserves_armed_history_and_blocks_original_sender(archived):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, issue, _ = archived
    owner, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    original_ledger = case.safety.get_physical_action(uid)
    proof = NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
    assert proof["state"] == "RETIRED"
    assert case.safety.get_physical_action(uid) == original_ledger
    assert original_ledger["state"] == "ARMED"
    assert original_ledger["confirmedOutcome"] is None
    disposition = case.safety.get_native_recovery_close_disposition(uid)
    assert disposition["dispositionBasis"] == "AUTHORIZED_DISPATCH_WITHDRAWN_BEFORE_WRITE_CLAIM"
    assert disposition["evidenceDigestSha256"] == proof["evidence_sha256"]
    assert not case.store.claim_native_command_write(uid)
    with pytest.raises(JobSafetyError, match="RECOVERY_CLOSE_RETIRING"):
        owner.send_once(uid)
    assert case.store.get_native_command(uid)["dispatch_retired"] == 1
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert not case.store.list_native_result_report_tasks()
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


@pytest.mark.parametrize("key,value", [("mayExecute", 0), ("ledgerSequence", 2.0), ("portNo", True)])
def test_local_confirmation_rejects_type_coerced_disposition(archived, key, value):
    case, wire, boot, *_ = archived
    _, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    proof = case.store.prepare_native_recovery_close_retirement(uid)
    case.safety.withdraw_native_recovery_close_dispatch(binding["permit"], action=binding["action"],
        evidence=binding["evidence"], retirement_evidence_sha256=proof["evidence_sha256"])
    disposition = case.safety.get_native_recovery_close_disposition(uid)
    if key == "ledgerSequence":
        value = float(disposition[key])
    with pytest.raises(ValueError, match="disposition mismatch"):
        case.store.confirm_native_recovery_close_withdrawal(uid, case.safety.get_physical_action(uid),
            disposition | {key: value})
    assert case.store.get_native_recovery_close_retirement(uid)["state"] == "PENDING"


def test_withdrawn_authorized_parent_allows_independent_successor_not_replay(archived, tmp_path):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal, NativeRecoveryCloseReconciler
    from mcu_actuator_handoff import McuActuatorEventHandoff
    case, wire, boot, issue, _ = archived
    owner, parent = armed_unclaimed(case, wire, boot)
    uid = parent["action"].action_uid
    before = case.safety.get_physical_action(uid)
    proof = NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
    case.store.close(); case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    owner = coordinator(case, wire, boot)
    child = owner.prepare_successor(uid, execution_window_ms=5000)
    child_uid = child["action"].action_uid
    assert child_uid != uid
    assert child["evidence"]["predecessorRetirementEvidenceSha256"] == proof["evidence_sha256"]
    assert owner.prepare_successor(uid, execution_window_ms=4000) == child
    assert owner.send_once(child_uid)
    assert not owner.send_once(child_uid)
    wire.pump(owner); wire.advance(100)
    handoff = McuActuatorEventHandoff(case.store, wire.write, 2)
    handoff.poll(wire.now); wire.pump(handoff)
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(child_uid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(uid) == before
    assert case.safety.get_physical_action(child_uid)["confirmedOutcome"] == "EXECUTED"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1


@pytest.mark.parametrize("committed", [False, True])
def test_lost_withdrawal_request_or_reply_retains_one_committed_identity(archived, tmp_path, monkeypatch, committed):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, issue, _ = archived
    _, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    ledger = case.safety.get_physical_action(uid)
    request = case.safety._client.request
    def lose(operation, payload):
        if operation == "WITHDRAW_NATIVE_RECOVERY_CLOSE_DISPATCH":
            other = EdgeStore(str(tmp_path / "edge.db")); other.initialize()
            try:
                assert other.get_native_recovery_close_retirement(uid)["evidence_sha256"] == payload["retirementEvidenceSha256"]
                assert not other.claim_native_command_write(uid)
            finally:
                other.close()
            if committed:
                request(operation, payload)
            raise LocalControlUnavailable("lost withdrawal RPC")
        return request(operation, payload)
    monkeypatch.setattr(case.safety._client, "request", lose)
    with pytest.raises(JobSafetyError):
        NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
    proof = case.store.get_native_recovery_close_retirement(uid)
    assert proof["state"] == "PENDING"
    monkeypatch.setattr(case.safety._client, "request", request)
    case.store.close(); case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    withdraw = NativeRecoveryCloseWithdrawal(case.store, case.safety)
    assert withdraw.reconcile(uid) == proof | {"state": "RETIRED"}
    assert withdraw.reconcile(uid) == proof | {"state": "RETIRED"}
    assert case.safety.get_physical_action(uid) == ledger
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_lost_arm_reply_can_be_withdrawn_but_never_rearmed_or_sent(archived, monkeypatch):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    request = case.safety._client.request
    def lose(operation, payload):
        result = request(operation, payload)
        if operation == "ARM_PHYSICAL_ACTION":
            raise LocalControlUnavailable("lost ARM reply")
        return result
    monkeypatch.setattr(case.safety._client, "request", lose)
    with pytest.raises(JobSafetyError):
        owner.send_once(uid)
    monkeypatch.setattr(case.safety._client, "request", request)
    assert case.store.get_native_command(uid)["write_claimed"] == 0
    before = case.safety.get_physical_action(uid)
    NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
    with pytest.raises(JobSafetyError):
        case.safety.arm_physical_action(binding["action"], dispatch_attempt_token=owner.live[uid][0])
    assert case.safety.get_physical_action(uid) == before
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_authorized_claim_and_withdrawal_have_only_one_winner(archived):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    _, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    other = EdgeStore(case.store.db_path); other.initialize()
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=5)
        return other.claim_native_command_write(uid)
    def withdraw():
        barrier.wait(timeout=5)
        try:
            NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
            return True
        except ValueError:
            return False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(claim), pool.submit(withdraw)
            claimed, withdrawn = a.result(), b.result()
        assert claimed != withdrawn
        command = case.store.get_native_command(uid)
        assert bool(command["write_claimed"]) == claimed
        assert bool(command["dispatch_retired"]) == withdrawn
        assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    finally:
        other.close()


def test_mixed_never_authorized_and_withdrawn_ancestors_preserve_each_fact(archived, tmp_path):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal, NativeRecoveryCloseRetirement
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    first = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = first["action"].action_uid
    NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    second = owner.prepare_successor(uid, execution_window_ms=5000)
    sid = second["action"].action_uid
    case.safety.prepare_native_recovery_close(second["permit"], action=second["action"],
        evidence=second["evidence"], dispatch_attempt_token=owner.live[sid][0])
    case.safety.arm_physical_action(second["action"], dispatch_attempt_token=owner.live[sid][0])
    NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(sid)
    third = owner.prepare_successor(sid, execution_window_ms=5000)
    tid = third["action"].action_uid
    NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(tid)
    case.store.close(); case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    owner = coordinator(case, wire, boot)
    final = owner.prepare_successor(tid, execution_window_ms=5000)
    assert owner.send_once(final["action"].action_uid)
    assert case.safety.get_physical_action(uid)["confirmationBasis"] == "PREPARED_NOT_ARMED"
    assert case.safety.get_physical_action(sid)["state"] == "ARMED"
    assert case.safety.get_physical_action(sid)["confirmedOutcome"] is None
    assert case.safety.get_physical_action(tid)["confirmationBasis"] == "PREPARED_NOT_ARMED"
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1


@pytest.mark.parametrize("point", ["intent", "local_confirm"])
def test_storage_failure_keeps_authorization_history_and_same_retry_identity(archived, point):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    _, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    original = case.safety.get_physical_action(uid)
    with case.store.transaction() as conn:
        operation = "INSERT" if point == "intent" else "UPDATE"
        conn.execute("CREATE TEMP TRIGGER fail_withdrawal BEFORE " + operation +
            " ON native_recovery_close_retirement BEGIN SELECT RAISE(ABORT,'injected withdrawal failure'); END")
    withdraw = NativeRecoveryCloseWithdrawal(case.store, case.safety)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        withdraw.reconcile(uid)
    assert case.safety.get_physical_action(uid) == original
    assert case.store.get_native_command(uid)["dispatch_retired"] == 0
    proof = case.store.get_native_recovery_close_retirement(uid)
    assert (proof is None) == (point == "intent")
    with case.store.transaction() as conn:
        conn.execute("DROP TRIGGER fail_withdrawal")
    assert withdraw.reconcile(uid)["state"] == "RETIRED"
    assert case.safety.get_physical_action(uid) == original


def test_delayed_original_arm_response_cannot_pass_committed_withdrawal(archived, monkeypatch):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    request = case.safety._client.request
    def delayed_arm(operation, payload):
        result = request(operation, payload)
        if operation == "ARM_PHYSICAL_ACTION":
            NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
        return result
    monkeypatch.setattr(case.safety._client, "request", delayed_arm)
    with pytest.raises(JobSafetyError, match="RECOVERY_CLOSE_RETIRING"):
        owner.send_once(uid)
    assert case.store.get_native_command(uid)["write_claimed"] == 0
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


@pytest.mark.parametrize("sent", [False, True])
def test_claimed_command_cannot_use_authorized_unclaimed_disposition(archived, sent):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000) if sent else armed_unclaimed(case, wire, boot)[1]
    uid = binding["action"].action_uid
    if sent:
        assert owner.send_once(uid)
    else:
        assert case.store.claim_native_command_write(uid)
    with pytest.raises(ValueError, match="write-claimed"):
        NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
    assert case.store.get_native_recovery_close_retirement(uid) is None
    with pytest.raises(JobSafetyError, match="NATIVE_RECOVERY_DISPOSITION_NOT_FOUND"):
        case.safety.get_native_recovery_close_disposition(uid)


def test_withdrawal_does_not_permit_successor_for_changed_target_boot(archived, monkeypatch):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    owner, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
    before = case.store.list_native_commands()
    monkeypatch.setattr(boot, "current_boot", lambda now: 3)
    with pytest.raises(ValueError, match="fresh original target boot"):
        owner.prepare_successor(uid, execution_window_ms=5000)
    assert case.store.list_native_commands() == before


@pytest.mark.parametrize("field", ["receiptUid", "evidenceDigestSha256", "sourceActionUid", "state"])
def test_changed_permanent_disposition_cannot_release_local_dispatch_slot(archived, field):
    case, wire, boot, *_ = archived
    _, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    proof = case.store.prepare_native_recovery_close_retirement(uid)
    case.safety.withdraw_native_recovery_close_dispatch(binding["permit"], action=binding["action"],
        evidence=binding["evidence"], retirement_evidence_sha256=proof["evidence_sha256"])
    disposition = case.safety.get_native_recovery_close_disposition(uid)
    with pytest.raises(ValueError, match="disposition mismatch"):
        case.store.confirm_native_recovery_close_withdrawal(uid, case.safety.get_physical_action(uid),
            disposition | {field: "changed"})
    assert case.store.get_native_recovery_close_retirement(uid) == proof
    assert case.store.get_native_command(uid)["dispatch_retired"] == 0


@pytest.mark.parametrize("deleted", ["row", "table"])
def test_missing_permanent_disposition_cannot_bypass_local_send_fence_or_authorize_successor(archived, deleted):
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    owner, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    original_ledger = case.safety.get_physical_action(uid)
    proof = NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
    case.updater.close()
    # Simulate coherent loss of the extension, not a supported database edit.
    # ARMED intentionally preserves history and cannot itself reveal this loss.
    with sqlite3.connect(case.updater.path) as conn:
        if deleted == "row":
            conn.execute("DELETE FROM native_recovery_close_disposition WHERE action_uid=?", (uid,))
        else:
            conn.execute("DROP TABLE native_recovery_close_disposition")
    case.updater.initialize()
    case.store.close(); case.store.initialize()
    before = case.store.list_native_commands()
    assert case.safety.get_physical_action(uid) == original_ledger
    assert case.store.get_native_recovery_close_retirement(uid) == proof
    assert not case.store.claim_native_command_write(uid)
    with pytest.raises(JobSafetyError, match="RECOVERY_CLOSE_RETIRING"):
        owner.send_once(uid)
    with pytest.raises(JobSafetyError, match="NATIVE_RECOVERY_DISPOSITION_NOT_FOUND"):
        owner.prepare_successor(uid, execution_window_ms=5000)
    assert case.store.list_native_commands() == before
    assert case.store.get_work_slot() == case.occupancy
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)
