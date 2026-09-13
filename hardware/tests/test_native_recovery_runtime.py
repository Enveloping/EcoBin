"""Candidate loop uses actual local C endpoint, two ledgers and native byte transport."""
import pytest
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator
from uart2_transport import NativeUartTransport
from hardware.tests.test_native_recovery_close_confirmation import closed
from job_safety import JobSafetyError
import uart2_protocol as uart


def candidate_loop(case, wire, issue):
    from native_recovery_runtime import NativeRecoveryRuntime
    return NativeRecoveryRuntime(case.store, case.safety, NativeUartTransport(CSerial(wire)),
        device_name=issue["deviceName"], clock=lambda: wire.now)


class CSerial:
    timeout, write_timeout, is_open = 0, 0.1, True
    def __init__(self, wire):
        self.wire = wire
        self.inbound = bytearray()
    @property
    def in_waiting(self):
        replies = self.wire.runtime[3]
        self.inbound.extend(b"".join(replies))
        replies.clear()
        return len(self.inbound)
    def read(self, size):
        result = bytes(self.inbound[:size])
        del self.inbound[:size]
        return result
    def write(self, raw):
        return self.wire.write(raw)
    def close(self):
        self.is_open = False


def test_startup_discovers_and_retires_unarmed_preparation_without_any_motion(archived):
    from native_recovery_runtime import NativeRecoveryRuntime
    case, wire, boot, issue, _ = archived
    old = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    candidate = NativeRecoveryRuntime(case.store, case.safety, NativeUartTransport(CSerial(wire)),
        device_name=issue["deviceName"], clock=lambda: wire.now)
    before = list(wire.sent)
    for _ in range(8):
        status = candidate.poll()
    assert status["admissionAllowed"] is False
    assert status["state"] == "NEW_CLOSE_REQUIRED"
    assert status["workUid"] == case.permit.work_uid
    uid = old["action"].action_uid
    assert case.store.get_native_recovery_close_retirement(uid)["state"] == "RETIRED"
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "NOT_EXECUTED"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent[len(before):])


def test_restart_queries_original_sent_close_and_confirms_actual_output_without_replay(archived):
    from native_recovery_runtime import NativeRecoveryRuntime
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    old = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = old["action"].action_uid
    assert owner.send_once(uid)
    wire.advance(100)
    candidate = NativeRecoveryRuntime(case.store, case.safety, NativeUartTransport(CSerial(wire)),
        device_name=issue["deviceName"], clock=lambda: wire.now)
    for _ in range(10):
        status = candidate.poll()
        wire.advance(200)
    assert status["state"] == "CLOSE_OUTPUT_CONFIRMED"
    assert status["admissionAllowed"] is False
    assert case.store.get_native_recovery_close_confirmation(uid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "EXECUTED"
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1
    assert case.store.get_work_slot() == case.occupancy


def test_saved_close_output_is_reconciled_even_after_another_mcu_restart(closed):
    from native_recovery_runtime import NativeRecoveryRuntime
    case, wire, _, issue, old, _ = closed
    uid = old["action"].action_uid
    assert case.store.get_native_recovery_close_confirmation(uid) is None
    wire.reset_mcu()
    candidate = NativeRecoveryRuntime(case.store, case.safety, NativeUartTransport(CSerial(wire)),
        device_name=issue["deviceName"], clock=lambda: wire.now)
    for _ in range(6):
        status = candidate.poll()
        wire.advance(200)
    assert case.store.get_native_recovery_close_confirmation(uid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "EXECUTED"
    assert status["admissionAllowed"] is False
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1


def test_preparation_created_after_startup_is_not_retired_or_reported_as_absent(archived):
    from native_recovery_runtime import NativeRecoveryRuntime
    case, wire, boot, issue, _ = archived
    candidate = NativeRecoveryRuntime(case.store, case.safety, NativeUartTransport(CSerial(wire)),
        device_name=issue["deviceName"], clock=lambda: wire.now)
    live = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    for _ in range(4):
        status = candidate.poll()
    assert status["state"] == "PREPARATION_CREATED_AFTER_STARTUP"
    assert case.store.get_native_recovery_close_retirement(live["action"].action_uid) is None


def test_armed_without_claim_is_not_mislabeled_as_never_authorized(archived):
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    old = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = old["action"].action_uid
    case.safety.prepare_native_recovery_close(old["permit"], action=old["action"],
        evidence=old["evidence"], dispatch_attempt_token=owner.live[uid][0])
    case.safety.arm_physical_action(old["action"], dispatch_attempt_token=owner.live[uid][0])
    ledger = case.safety.get_physical_action(uid)
    candidate = candidate_loop(case, wire, issue)
    for _ in range(8):
        status = candidate.poll()
        wire.advance(200)
    assert status["state"] == "NEW_CLOSE_REQUIRED"
    assert case.store.get_native_recovery_close_retirement(uid)["state"] == "RETIRED"
    disposition = case.safety.get_native_recovery_close_disposition(uid)
    assert disposition["dispositionBasis"] == "AUTHORIZED_DISPATCH_WITHDRAWN_BEFORE_WRITE_CLAIM"
    assert status["admissionAllowed"] is False
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_physical_action(uid) == ledger
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_not_seen_query_is_not_a_license_to_replay_or_retire(archived, monkeypatch):
    case, wire, boot, issue, _ = archived
    original_write = wire.write
    def lost_motion(raw):
        decoded = uart.decode_frame(raw, sender_role="EDGE")
        if decoded["messageName"] == "SAFE_CLOSE":
            wire.sent.append(("SAFE_CLOSE", uart.decode_payload("SAFE_CLOSE", decoded["payload"])))
            return len(raw)  # Explicit serial boundary loss, never feed MCU.
        return original_write(raw)
    monkeypatch.setattr(wire, "write", lost_motion)
    owner = coordinator(case, wire, boot)
    old = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = old["action"].action_uid
    owner.send_once(uid)
    candidate = candidate_loop(case, wire, issue)
    for _ in range(18):
        status = candidate.poll()
        wire.advance(200)
    assert any(row["outcome"] == "NOT_SEEN" for row in case.store.list_native_command_observations(uid))
    assert case.store.get_native_recovery_close_retirement(uid) is None
    assert case.store.get_native_recovery_close_confirmation(uid) is None
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1
    assert status["admissionAllowed"] is False


def test_lost_withdrawal_reply_resumes_without_erasing_authorization_or_sending(archived, monkeypatch):
    from local_control import LocalControlUnavailable
    from hardware.tests.test_native_recovery_close_withdrawal import armed_unclaimed
    case, wire, boot, issue, _ = archived
    owner, binding = armed_unclaimed(case, wire, boot)
    uid = binding["action"].action_uid
    ledger = case.safety.get_physical_action(uid)
    request = case.safety._client.request
    def lose(operation, payload):
        result = request(operation, payload)
        if operation == "WITHDRAW_NATIVE_RECOVERY_CLOSE_DISPATCH":
            raise LocalControlUnavailable("reply lost after permanent commit")
        return result
    monkeypatch.setattr(case.safety._client, "request", lose)
    candidate = candidate_loop(case, wire, issue)
    for _ in range(5):
        status = candidate.poll()
    assert status["state"] == "WAITING_PERMANENT_LEDGER"
    pending = case.store.get_native_recovery_close_retirement(uid)
    assert pending["state"] == "PENDING"
    assert not case.store.claim_native_command_write(uid)
    monkeypatch.setattr(case.safety._client, "request", request)
    fresh = candidate_loop(case, wire, issue)
    for _ in range(5):
        status = fresh.poll()
    assert status["state"] == "NEW_CLOSE_REQUIRED"
    assert status["admissionAllowed"] is False
    assert case.store.get_native_recovery_close_retirement(uid) == pending | {"state": "RETIRED"}
    assert case.safety.get_physical_action(uid) == ledger
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_authorization_winning_after_prepared_query_switches_to_withdrawal_next_poll(archived, monkeypatch):
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    token = owner.live[uid][0]
    case.safety.prepare_native_recovery_close(binding["permit"], action=binding["action"],
        evidence=binding["evidence"], dispatch_attempt_token=token)
    request = case.safety._client.request
    raced = []
    def arm_after_read(operation, payload):
        result = request(operation, payload)
        if operation == "GET_PHYSICAL_ACTION" and payload["actionUid"] == uid and not raced:
            raced.append(True)
            case.safety.arm_physical_action(binding["action"], dispatch_attempt_token=token)
        return result
    monkeypatch.setattr(case.safety._client, "request", arm_after_read)
    candidate = candidate_loop(case, wire, issue)
    for _ in range(5):
        status = candidate.poll()
    assert status["state"] == "WAITING_PERMANENT_LEDGER"
    assert case.store.get_native_recovery_close_retirement(uid)["state"] == "PENDING"
    assert not case.store.claim_native_command_write(uid)
    wire.advance(1000)
    for _ in range(5):
        status = candidate.poll()
    assert status["state"] == "NEW_CLOSE_REQUIRED"
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    assert case.safety.get_native_recovery_close_disposition(uid)["state"] == "DISPATCH_WITHDRAWN"
    assert case.store.get_work_slot() == case.occupancy
    assert status["admissionAllowed"] is False
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_timeout_does_not_trust_persisted_boot_or_retire_preparation(archived):
    from native_recovery_runtime import NativeRecoveryRuntime
    case, wire, boot, issue, _ = archived
    old = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    class Offline(CSerial):
        def write(self, raw):
            return len(raw)
    candidate = NativeRecoveryRuntime(case.store, case.safety, NativeUartTransport(Offline(wire)),
        device_name=issue["deviceName"], clock=lambda: wire.now)
    for _ in range(10):
        status = candidate.poll()
        wire.advance(1000)
    assert status["state"] == "WAITING_BOOT"
    assert case.store.get_native_recovery_close_retirement(old["action"].action_uid) is None
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue


@pytest.mark.parametrize("change", ["release", "port"])
def test_changed_occupancy_prevents_retirement_and_actuator_ack(archived, change):
    case, wire, boot, issue, _ = archived
    old = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    candidate = candidate_loop(case, wire, issue)
    if change == "release":
        case.store.release_work_slot(case.permit.work_uid)
    else:
        with case.store.transaction() as conn:
            conn.execute("UPDATE work_slot SET port_no=2 WHERE slot_id=1")
    before = len(wire.sent)
    for _ in range(4):
        status = candidate.poll()
    assert status["state"] == "ORIGINAL_OCCUPANCY_CHANGED"
    assert case.store.get_native_recovery_close_retirement(old["action"].action_uid) is None
    assert all(name in {"BOOT_PROBE", "BIND_BOOT"} for name, _ in wire.sent[before:])


def test_late_final_reaching_candidate_loop_is_preserved_only_as_issue_evidence(archived):
    case, wire, boot, issue, saved = archived
    candidate = candidate_loop(case, wire, issue)
    wire.runtime[3].append(uart.encode_frame("WORK_RESULT", 800, saved["payload"]))
    candidate.poll()
    assert len(case.store.list_native_delivery_issue_results(issue["issueUid"])) == 1
    assert not case.store.list_native_result_report_tasks()
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("authorized", [False, True])
def test_real_main_entry_drives_original_retirement_with_actual_c_and_two_sqlite_connections(archived, monkeypatch, tmp_path, authorized):
    from functools import partial
    import main
    import native_recovery_entry as entry
    case, wire, boot, issue, _ = archived
    if authorized:
        from hardware.tests.test_native_recovery_close_withdrawal import armed_unclaimed
        _, old = armed_unclaimed(case, wire, boot)
    else:
        old = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    monkeypatch.setattr(entry, "LocalControlClient", lambda *args, **kwargs: case.safety._client)
    ticks, ports = [], []
    def wait_once(seconds):
        assert seconds == 0.05
        ticks.append(seconds)
        wire.advance(50)
    def port_factory(**kwargs):
        port = CSerial(wire)
        ports.append(port)
        return port
    def forbidden_legacy():
        pytest.fail("candidate entered legacy gateway")
    result = main.run_gateway(["--native-recovery-candidate", "--store-path", case.store.db_path,
        "--serial-device", "/dev/fake", "--device-name", issue["deviceName"],
        "--updater-socket", str(tmp_path / "updater.sock")], legacy_factory=forbidden_legacy,
        candidate_runner=partial(entry.main, port_factory=port_factory, stop=lambda: len(ticks) == 8,
            wait=wait_once, clock=lambda: wire.now))
    assert result["state"] == "NEW_CLOSE_REQUIRED" and result["admissionAllowed"] is False
    assert all(not port.is_open for port in ports)
    assert case.store.get_native_recovery_close_retirement(old["action"].action_uid)["state"] == "RETIRED"
    if authorized:
        assert case.safety.get_physical_action(old["action"].action_uid)["state"] == "ARMED"
        assert case.safety.get_native_recovery_close_disposition(old["action"].action_uid)["state"] == "DISPATCH_WITHDRAWN"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


def test_lost_retirement_response_is_recovered_by_a_new_candidate_process_owner(archived, monkeypatch):
    from local_control import LocalControlUnavailable
    case, wire, boot, issue, _ = archived
    old = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = old["action"].action_uid
    request = case.safety._client.request
    def lose(operation, payload):
        result = request(operation, payload)
        if operation == "RETIRE_NATIVE_RECOVERY_CLOSE":
            raise LocalControlUnavailable("response lost at local socket boundary")
        return result
    monkeypatch.setattr(case.safety._client, "request", lose)
    candidate = candidate_loop(case, wire, issue)
    for _ in range(4):
        status = candidate.poll()
    assert status["state"] == "WAITING_PERMANENT_LEDGER"
    proof = case.store.get_native_recovery_close_retirement(uid)
    assert proof["state"] == "PENDING"
    monkeypatch.setattr(case.safety._client, "request", request)
    fresh = candidate_loop(case, wire, issue)
    for _ in range(4):
        status = fresh.poll()
    assert status["state"] == "NEW_CLOSE_REQUIRED"
    assert case.store.get_native_recovery_close_retirement(uid) == proof | {"state": "RETIRED"}
    assert not any(name == "SAFE_CLOSE" for name, _ in wire.sent)


@pytest.mark.parametrize("mode", ["thread", "reentrant"])
def test_candidate_rejects_other_thread_and_reentrant_poll_without_extra_writes(archived, monkeypatch, mode):
    from concurrent.futures import ThreadPoolExecutor
    case, wire, _, issue, _ = archived
    candidate = candidate_loop(case, wire, issue)
    before = len(wire.sent)
    with pytest.raises(RuntimeError, match="foreground owner"):
        if mode == "thread":
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(candidate.poll).result()
        else:
            monkeypatch.setattr(candidate.transport, "poll", lambda now: candidate.poll())
            candidate.poll()
    assert len(wire.sent) == before


@pytest.mark.parametrize("current", ["offline", "new_boot"])
def test_retired_parent_does_not_starve_saved_successor_output_when_mcu_unavailable(archived, current):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    from native_delivery_recovery_close import NativeRecoveryCloseRetirement
    from native_recovery_runtime import NativeRecoveryRuntime
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    root = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(root["action"].action_uid)
    child = owner.prepare_successor(root["action"].action_uid, execution_window_ms=5000)
    uid = child["action"].action_uid
    owner.send_once(uid); wire.pump(owner); wire.advance(100)
    handoff = McuActuatorEventHandoff(case.store, wire.write, 2)
    handoff.poll(wire.now); wire.pump(handoff)
    if current == "new_boot":
        wire.reset_mcu()
        port = CSerial(wire)
    else:
        class Offline(CSerial):
            def write(self, raw):
                return len(raw)
        port = Offline(wire)
    candidate = NativeRecoveryRuntime(case.store, case.safety, NativeUartTransport(port),
        device_name=issue["deviceName"], clock=lambda: wire.now)
    for _ in range(12):
        status = candidate.poll()
        wire.advance(200)
    assert case.store.get_native_recovery_close_confirmation(uid)["state"] == "CONFIRMED"
    assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "EXECUTED"
    assert status["admissionAllowed"] is False
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1
