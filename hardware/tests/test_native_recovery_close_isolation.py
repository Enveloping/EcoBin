"""A newer owned MCU boot isolates an old target, not its unknown past effect."""
import pytest
import uart2_protocol as uart
import json
import sqlite3
from edge_store import EdgeStore
from job_safety import JobSafetyError
from local_control import LocalControlUnavailable
from hardware.tests.test_native_recovery_close_confirmation import closed
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator


def reboot(case, wire):
    from hardware.tests.test_mcu_safe_close_execution import enable
    lib, endpoint, preparation, replies, _, sink, guard = wire.runtime
    replies.clear()
    lib.TestFacts_InitHardware()
    lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
    assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
    execution = enable(wire.runtime, bind=False)
    wire.recovery_execution = execution  # Keep ctypes storage alive while C owns its pointer.
    wire.now += 1
    wire.mcu_offset = wire.now
    boot = wire.handshake()
    assert execution
    return boot


def test_reboot_isolates_claimed_unknown_close_without_rewriting_history(archived):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    assert owner.send_once(uid)
    before = case.safety.get_physical_action(uid)
    boot = reboot(case, wire)
    sent = list(wire.sent)
    proof = NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    assert proof["state"] == "ISOLATED"
    assert case.safety.get_physical_action(uid) == before
    disposition = case.safety.get_native_recovery_close_disposition(uid)
    assert disposition["state"] == "ISOLATED_BY_REBOOT"
    assert disposition["pastEffect"] == "UNKNOWN"
    assert disposition["observedMcuBootId"] == 3
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.prepare_native_recovery_close_confirmation(uid) is None
    assert wire.sent == sent


def sent_and_rebooted(case, wire, boot):
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    assert owner.send_once(binding["action"].action_uid)
    wire.pump(owner)
    return binding, reboot(case, wire)


def output(uid, **changes):
    return uart.encode_payload("SAFE_CLOSE_RESULT", dict(mcuBootId=2, mcuEventSequence=1,
        uptimeMs=100, mcuCommandUid=uid, scope="SINGLE_DELIVERY_DOOR", portNo=1, command="CLOSE",
        outputStatus="COMMAND_DISPATCHED", physicalDoorStateBasis="NOT_OBSERVABLE", faultCode="NONE") | changes)


def test_wrong_output_scope_cannot_be_absorbed_as_unknown_isolation(archived):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    case, wire, boot, *_ = archived
    binding, boot = sent_and_rebooted(case, wire, boot)
    uid = binding["action"].action_uid
    case.store.save_native_actuator_event("SAFE_CLOSE_RESULT", output(uid, mcuBootId=3))
    with pytest.raises(ValueError, match="output.*identity"):
        NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    assert case.store.get_native_recovery_close_isolation(uid) is None


def test_later_conflicting_output_is_evidence_not_rewrite_of_frozen_isolation(archived):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    case, wire, boot, *_ = archived
    binding, boot = sent_and_rebooted(case, wire, boot)
    uid = binding["action"].action_uid
    owner = NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now)
    proof = owner.reconcile(uid)
    case.store.save_native_actuator_event("SAFE_CLOSE_RESULT", output(uid))
    with pytest.raises(ValueError, match="conflict"):
        case.store.save_native_actuator_event("SAFE_CLOSE_RESULT", output(uid, uptimeMs=101))
    assert case.store.get_native_recovery_close_isolation(uid) == proof
    assert case.store.prepare_native_recovery_close_confirmation(uid) is None
    assert owner.reconcile(uid) == proof
    assert case.store.list_native_actuator_event_conflicts()


def test_initial_isolation_rechecks_fresh_boot_before_commit(archived):
    case, wire, boot, *_ = archived
    binding, boot = sent_and_rebooted(case, wire, boot)
    calls = iter([3, None])
    with pytest.raises(ValueError, match="fresh"):
        case.store.prepare_native_recovery_close_isolation(binding["action"].action_uid,
            current_boot=lambda: next(calls))
    assert case.store.get_native_recovery_close_isolation(binding["action"].action_uid) is None


def test_isolated_old_close_allows_one_independent_new_boot_close(archived):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    from mcu_actuator_handoff import McuActuatorEventHandoff
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    old = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = old["action"].action_uid
    assert owner.send_once(uid)
    boot = reboot(case, wire)
    NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    case.store.close(); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    owner = coordinator(case, wire, boot)
    child = owner.prepare_successor(uid, execution_window_ms=5000)
    cid = child["action"].action_uid
    assert cid != uid
    assert child["evidence"]["targetMcuBootId"] == 3
    assert case.store.get_native_command(cid)["command_sequence"] == 1
    assert owner.prepare_successor(uid, execution_window_ms=4000) == child
    assert owner.send_once(cid)
    wire.pump(owner); wire.advance(100)
    handoff = McuActuatorEventHandoff(case.store, wire.write, 3)
    handoff.poll(wire.now); wire.pump(handoff)
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(cid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("confirmed", [False, True])
def test_reliable_saved_output_wins_before_isolation_even_after_new_boot(closed, confirmed):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, _, _, binding, _ = closed
    uid = binding["action"].action_uid
    if confirmed:
        NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    boot = reboot(case, wire)
    assert NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid) is None
    assert case.store.get_native_recovery_close_isolation(uid) is None
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "EXECUTED"


@pytest.mark.parametrize("closed", ["rejected"], indirect=True)
def test_known_failed_output_does_not_become_unknown_reboot_isolation(closed):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    case, wire, _, _, binding, _ = closed
    boot = reboot(case, wire)
    with pytest.raises(ValueError, match="known failed"):
        NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(binding["action"].action_uid)
    assert case.store.get_native_recovery_close_isolation(binding["action"].action_uid) is None


@pytest.mark.parametrize("committed", [False, True])
def test_lost_isolation_rpc_retries_frozen_proof_offline_after_pi_restart(archived, monkeypatch, committed):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    from mcu_session import McuBootSession
    case, wire, boot, issue, _ = archived
    binding, boot = sent_and_rebooted(case, wire, boot)
    uid = binding["action"].action_uid
    request = case.safety._client.request
    def lose(operation, payload):
        if operation == "ISOLATE_NATIVE_RECOVERY_CLOSE_AFTER_REBOOT":
            other = EdgeStore(case.store.db_path); other.initialize()
            try:
                assert other.get_native_recovery_close_isolation(uid)["evidence_sha256"] == payload["isolationEvidenceSha256"]
            finally:
                other.close()
            if committed:
                request(operation, payload)
            raise LocalControlUnavailable("lost isolation RPC")
        return request(operation, payload)
    monkeypatch.setattr(case.safety._client, "request", lose)
    with pytest.raises(JobSafetyError):
        NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    proof = case.store.get_native_recovery_close_isolation(uid)
    assert proof["state"] == "PENDING"
    monkeypatch.setattr(case.safety._client, "request", request)
    case.store.close(); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    offline = McuBootSession(case.store, wire.write)
    assert offline.current_boot(wire.now) is None
    owner = NativeRecoveryCloseIsolation(case.store, case.safety, offline, clock=lambda: wire.now)
    assert owner.reconcile(uid) == proof | {"state": "ISOLATED"}
    assert owner.reconcile(uid) == proof | {"state": "ISOLATED"}
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


def test_complete_late_output_and_acceptance_remain_only_evidence_after_isolation(archived):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    from native_delivery_recovery_close import NativeRecoveryCloseReconciler
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    assert owner.send_once(uid)
    decision = next(uart.decode_frame(frame, sender_role="MCU")["payload"] for frame in wire.runtime[3]
        if uart.decode_frame(frame, sender_role="MCU")["messageName"] == "COMMAND_DECISION")
    boot = reboot(case, wire)
    proof = NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    case.store.save_native_command_observation("COMMAND_DECISION", decision)
    case.store.save_native_actuator_event("SAFE_CLOSE_RESULT", output(uid))
    case.store.close(); case.store.initialize()
    assert case.store.get_native_recovery_close_isolation(uid) == proof
    assert NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid) is None
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] is None
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


@pytest.mark.parametrize("kind", ["same_boot", "timeout", "unclaimed", "withdrawn"])
def test_isolation_does_not_replace_other_recovery_boundaries(archived, kind):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    from native_delivery_recovery_close import NativeRecoveryCloseWithdrawal
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    if kind in {"same_boot", "timeout"}:
        assert owner.send_once(uid)
        if kind == "timeout":
            wire.now += 1000
    else:
        token = owner.live[uid][0]
        case.safety.prepare_native_recovery_close(binding["permit"], action=binding["action"],
            evidence=binding["evidence"], dispatch_attempt_token=token)
        case.safety.arm_physical_action(binding["action"], dispatch_attempt_token=token)
        if kind == "withdrawn":
            NativeRecoveryCloseWithdrawal(case.store, case.safety).reconcile(uid)
        boot = reboot(case, wire)
    with pytest.raises(ValueError):
        NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    assert case.store.get_native_recovery_close_isolation(uid) is None


@pytest.mark.parametrize("point", ["intent", "local_confirm"])
def test_isolation_storage_failure_preserves_original_retry_identity(archived, point):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    case, wire, boot, *_ = archived
    binding, boot = sent_and_rebooted(case, wire, boot)
    uid = binding["action"].action_uid
    before = case.safety.get_physical_action(uid)
    operation = "INSERT" if point == "intent" else "UPDATE"
    with case.store.transaction() as conn:
        conn.execute("CREATE TEMP TRIGGER fail_isolation BEFORE " + operation +
            " ON native_recovery_close_isolation BEGIN SELECT RAISE(ABORT,'injected isolation failure'); END")
    owner = NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        owner.reconcile(uid)
    assert case.safety.get_physical_action(uid) == before
    proof = case.store.get_native_recovery_close_isolation(uid)
    assert (proof is None) == (point == "intent")
    with case.store.transaction() as conn:
        conn.execute("DROP TRIGGER fail_isolation")
    assert owner.reconcile(uid)["state"] == "ISOLATED"


def test_multiple_boot_isolations_and_same_boot_retirement_keep_exact_ancestry(archived):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    case, wire, boot, issue, _ = archived
    old, boot = sent_and_rebooted(case, wire, boot)
    uid = old["action"].action_uid
    NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    owner = coordinator(case, wire, boot)
    middle = owner.prepare_successor(uid, execution_window_ms=5000)
    mid = middle["action"].action_uid
    assert owner.send_once(mid)
    boot = reboot(case, wire)
    assert boot.current_boot(wire.now) == 4
    NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(mid)
    owner = coordinator(case, wire, boot)
    unused = owner.prepare_successor(mid, execution_window_ms=5000)
    unused_uid = unused["action"].action_uid
    NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(unused_uid)
    case.store.close(); case.store.initialize()
    case.updater.close(); case.updater.initialize()
    owner = coordinator(case, wire, boot)
    final = owner.prepare_successor(unused_uid, execution_window_ms=5000)
    assert final["evidence"]["targetMcuBootId"] == 4
    assert case.store.get_native_command(final["action"].action_uid)["command_sequence"] == 2
    assert owner.send_once(final["action"].action_uid)
    assert case.store.get_native_delivery_recovery_close(mid) == middle
    assert case.store.get_native_delivery_recovery_close(unused_uid) == unused
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("change", ["hash", "duplicate_snapshot", "boot_witness", "missing_table", "claimed_marker"])
def test_schema40_reopen_rejects_changed_isolation_proof(archived, change):
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation, digest
    from work_recovery import canonical
    case, wire, boot, *_ = archived
    binding, boot = sent_and_rebooted(case, wire, boot)
    uid = binding["action"].action_uid
    proof = NativeRecoveryCloseIsolation(case.store, case.safety, boot, clock=lambda: wire.now).reconcile(uid)
    case.store.close()
    with sqlite3.connect(case.store.db_path) as conn:
        if change == "hash":
            conn.execute("UPDATE native_recovery_close_isolation SET evidence_sha256=?", ("f" * 64,))
        elif change == "duplicate_snapshot":
            value = json.loads(proof["bundle_json"])
            value["availableEvidence"]["observations"] *= 2
            raw = canonical(value)
            conn.execute("UPDATE native_recovery_close_isolation SET bundle_json=?,evidence_sha256=?", (raw, digest(raw)))
        elif change == "boot_witness":
            conn.execute("DELETE FROM native_mcu_boot_observation WHERE boot_id=3")
        elif change == "missing_table":
            conn.execute("DROP TABLE native_recovery_close_isolation")
        else:
            conn.execute("UPDATE native_mcu_command SET write_claimed=0 WHERE command_uid=?", (uid,))
    with pytest.raises(ValueError):
        case.store.initialize()


def test_schema39_upgrade_preserves_pending_original_close_and_creates_empty_isolation_table(archived):
    case, wire, boot, issue, _ = archived
    binding, boot = sent_and_rebooted(case, wire, boot)
    before = case.store.list_native_commands()
    case.store.close()
    with sqlite3.connect(case.store.db_path) as conn:
        conn.execute("DROP TABLE native_recovery_close_isolation")
        conn.execute("DELETE FROM schema_version WHERE version>39")
    case.store.initialize()
    assert case.store.list_native_commands() == before
    assert case.store.get_native_delivery_recovery_close(binding["action"].action_uid) == binding
    assert case.store.get_native_recovery_close_isolation(binding["action"].action_uid) is None
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
