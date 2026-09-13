"""Independent close attempts retain their original diagnostic issue and ancestry."""
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import sqlite3
from edge_store import EdgeStore
from job_safety import JobSafetyError
from local_control import LocalControlUnavailable

from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator
from native_delivery_recovery_close import NativeRecoveryCloseRetirement


def test_retirement_releases_only_dispatch_slot_without_fabricating_mcu_reply(archived):
    case, wire, boot, issue, _ = archived
    first = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = first["action"].action_uid
    NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    command = case.store.get_native_command(uid)
    assert command["dispatch_retired"] == 1
    assert command["decision_outcome"] is None
    assert command["boot_retired"] == 0
    assert command["write_claimed"] == 0
    assert not case.store.claim_native_command_write(uid)
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


@pytest.mark.parametrize("boundary", ["prepare_read", "arm_rpc", "last_check"])
def test_successor_deadline_is_not_extended_by_slow_checks(archived, monkeypatch, boundary):
    case, wire, boot, *_ = archived
    owner, root = retired_root(case, wire, boot)
    request = case.safety._client.request
    expired = False
    def delay(operation, payload):
        nonlocal expired
        result = request(operation, payload)
        target = "GET_PHYSICAL_ACTION" if boundary == "prepare_read" else "ARM_PHYSICAL_ACTION"
        if operation == target and boundary != "last_check" and not expired:
            expired = True
            wire.advance(200)
        return result
    monkeypatch.setattr(case.safety._client, "request", delay)
    child = owner.prepare_successor(root["action"].action_uid, execution_window_ms=200)
    if boundary == "last_check":
        claim = case.store.claim_native_command_write
        def slow_claim(uid):
            result = claim(uid)
            wire.advance(200)
            return result
        monkeypatch.setattr(case.store, "claim_native_command_write", slow_claim)
    with pytest.raises(JobSafetyError, match="COMMAND_EXPIRED"):
        owner.send_once(child["action"].action_uid)
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_successor_preparation_does_not_change_target_after_another_mcu_boot(archived, monkeypatch):
    case, wire, boot, *_ = archived
    owner, root = retired_root(case, wire, boot)
    before = case.store.list_native_commands()
    monkeypatch.setattr(boot, "current_boot", lambda now: 3)
    with pytest.raises(ValueError, match="fresh original target boot"):
        owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
    assert case.store.list_native_commands() == before


def test_late_original_final_during_successor_close_remains_diagnostic_evidence(archived):
    case, wire, boot, issue, saved = archived
    owner, root = retired_root(case, wire, boot)
    child = owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
    case.store.save_native_mcu_result(saved["payload"])
    assert owner.send_once(child["action"].action_uid)
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert len(case.store.list_native_delivery_issue_results(issue["issueUid"])) == 1
    assert case.store.list_native_result_report_tasks() == []
    assert case.store.get_work_slot() == case.occupancy


def test_retired_preparation_can_have_one_independent_live_successor(archived):
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    first = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = first["action"].action_uid
    proof = NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    old = case.store.get_native_command(uid)
    second = owner.prepare_successor(uid, execution_window_ms=4000)
    action = second["action"]
    evidence = second["evidence"]
    assert action.action_uid != uid
    assert action.receipt_uid != first["action"].receipt_uid
    assert evidence["recoveryUid"] != first["evidence"]["recoveryUid"]
    assert evidence["predecessorActionUid"] == uid
    assert evidence["predecessorRetirementEvidenceSha256"] == proof["evidence_sha256"]
    assert evidence["predecessorReceiptUid"] == first["action"].receipt_uid
    assert case.store.get_native_command(action.action_uid)["command_sequence"] > old["command_sequence"]
    assert owner.prepare_successor(uid, execution_window_ms=3000) == second
    assert owner.prepare(case.permit.work_uid, execution_window_ms=3000) == first
    assert owner.send_once(action.action_uid)
    assert not owner.send_once(action.action_uid)
    assert case.store.get_native_command(uid) == old
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1


def retired_root(case, wire, boot):
    owner = coordinator(case, wire, boot)
    root = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(root["action"].action_uid)
    return owner, root


def test_multiple_retired_generations_survive_restart_and_final_c_output(archived, tmp_path):
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    from mcu_actuator_handoff import McuActuatorEventHandoff
    case, wire, boot, issue, _ = archived
    owner, root = retired_root(case, wire, boot)
    attempts = [root]
    for _ in range(3):
        child = owner.prepare_successor(attempts[-1]["action"].action_uid, execution_window_ms=5000)
        NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(child["action"].action_uid)
        attempts.append(child)
    commands = case.store.list_native_commands()
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    assert case.store.list_native_commands() == commands
    owner = coordinator(case, wire, boot)
    # Retry an ancestor returns its original immediate child, even if retired.
    assert owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000) == attempts[1]
    final = owner.prepare_successor(attempts[-1]["action"].action_uid, execution_window_ms=5000)
    uid = final["action"].action_uid
    assert owner.send_once(uid)
    wire.pump(owner)
    wire.advance(100)
    handoff = McuActuatorEventHandoff(case.store, wire.write, 2)
    handoff.poll(wire.now); wire.pump(handoff)
    proof = NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    assert proof["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "EXECUTED"
    for attempt in attempts:
        assert case.store.get_native_recovery_close_retirement(attempt["action"].action_uid)["state"] == "RETIRED"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    assert not case.store.list_native_result_report_tasks()


@pytest.mark.parametrize("committed", [False, True])
def test_lost_successor_prepare_does_not_regain_live_token_after_pi_restart(archived, tmp_path, monkeypatch, committed):
    case, wire, boot, *_ = archived
    owner, root = retired_root(case, wire, boot)
    child = owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
    uid = child["action"].action_uid
    request = case.safety._client.request
    def lose(operation, payload):
        if operation == "PREPARE_NATIVE_RECOVERY_CLOSE_SUCCESSOR":
            if committed:
                request(operation, payload)
            raise LocalControlUnavailable("lost successor prepare")
        return request(operation, payload)
    monkeypatch.setattr(case.safety._client, "request", lose)
    with pytest.raises(JobSafetyError):
        owner.send_once(uid)
    monkeypatch.setattr(case.safety._client, "request", request)
    case.store.close(); case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    owner = coordinator(case, wire, boot)
    assert owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000) == child
    with pytest.raises(JobSafetyError, match="RECOVERY_DISPATCH_NOT_LIVE"):
        owner.send_once(uid)
    NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    fresh = owner.prepare_successor(uid, execution_window_ms=5000)
    assert fresh["action"].action_uid != uid
    assert owner.send_once(fresh["action"].action_uid)
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1


@pytest.mark.parametrize("state", ["unretired", "pending", "armed"])
def test_successor_cannot_be_created_from_unconfirmed_or_authorized_parent(archived, state):
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    root = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = root["action"].action_uid
    if state == "pending":
        case.store.prepare_native_recovery_close_retirement(uid)
    if state == "armed":
        case.safety.prepare_native_recovery_close(root["permit"], action=root["action"],
            evidence=root["evidence"], dispatch_attempt_token=owner.live[uid][0])
        case.safety.arm_physical_action(root["action"], dispatch_attempt_token=owner.live[uid][0])
    before = case.store.list_native_commands()
    with pytest.raises((ValueError, JobSafetyError)):
        owner.prepare_successor(uid, execution_window_ms=5000)
    assert case.store.list_native_commands() == before
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_two_business_connections_create_only_one_successor_with_one_live_owner(archived, monkeypatch):
    case, wire, boot, *_ = archived
    _, root = retired_root(case, wire, boot)
    other = EdgeStore(case.store.db_path); other.initialize()
    try:
        owners = [coordinator(case, wire, boot), coordinator(SimpleNamespace(store=other, safety=case.safety), wire, boot)]
        barrier = Barrier(2)
        request = case.safety._client.request
        def synchronized(operation, payload):
            if operation == "GET_PHYSICAL_ACTION":
                barrier.wait(timeout=10)
            return request(operation, payload)
        monkeypatch.setattr(case.safety._client, "request", synchronized)
        with ThreadPoolExecutor(max_workers=2) as pool:
            children = list(pool.map(lambda owner: owner.prepare_successor(root["action"].action_uid,
                execution_window_ms=5000), owners))
        monkeypatch.setattr(case.safety._client, "request", request)
        assert children[0] == children[1]
        uid = children[0]["action"].action_uid
        eligible = []
        for owner in owners:
            owner.revalidate = lambda record: (_ for _ in ()).throw(RuntimeError("live prerequisite boundary"))
            try:
                owner.send_once(uid)
            except JobSafetyError as error:
                assert error.code == "RECOVERY_DISPATCH_NOT_LIVE"
                eligible.append(False)
            except RuntimeError as error:
                assert str(error) == "live prerequisite boundary"
                eligible.append(True)
        assert sum(eligible) == 1
        assert sum(command["message_name"] == "SAFE_CLOSE" for command in case.store.list_native_commands()) == 2
    finally:
        other.close()


def test_failed_successor_binding_rolls_back_new_command_and_sequence(archived):
    case, wire, boot, *_ = archived
    owner, root = retired_root(case, wire, boot)
    commands = case.store.list_native_commands()
    with case.store.transaction() as conn:
        conn.execute("""CREATE TEMP TRIGGER fail_child BEFORE INSERT ON native_delivery_recovery_close
            BEGIN SELECT RAISE(ABORT, 'injected successor failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected successor failure"):
        owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
    assert case.store.list_native_commands() == commands
    with case.store.transaction() as conn:
        conn.execute("DROP TRIGGER fail_child")
    child = owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
    assert case.store.get_native_command(child["action"].action_uid)["command_sequence"] == commands[-1]["command_sequence"] + 1


@pytest.mark.parametrize("operation", ["root_retry", "child_retry", "send", "reopen"])
def test_damaged_child_parent_index_cannot_create_or_send_another_attempt(archived, operation):
    import uuid
    case, wire, boot, *_ = archived
    owner, root = retired_root(case, wire, boot)
    child = owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
    with sqlite3.connect(case.store.db_path) as conn:
        conn.execute("UPDATE native_delivery_recovery_close SET predecessor_action_uid=? WHERE action_uid=?",
            (str(uuid.uuid4()), child["action"].action_uid))
    before = case.store.list_native_commands()
    with pytest.raises(ValueError, match="predecessor index"):
        if operation == "root_retry":
            owner.prepare(case.permit.work_uid, execution_window_ms=5000)
        elif operation == "child_retry":
            owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
        elif operation == "send":
            owner.send_once(child["action"].action_uid)
        else:
            case.store.close(); case.store.initialize()
    if operation != "reopen":
        assert case.store.list_native_commands() == before
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)
