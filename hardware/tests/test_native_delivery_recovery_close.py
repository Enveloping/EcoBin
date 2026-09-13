"""Archived delivery -> permanent new-close authority -> actual C output custody."""
import pytest
import sqlite3
import uuid
import subprocess
import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_native_work_recovery import RecoveryWire
from hardware.tests.test_native_result_report import original_command
from hardware.tests.test_native_delivery_issue import without_final_packet


class CloseWire(RecoveryWire):
    mcu_offset = 0

    def write(self, frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        values = uart.decode_payload(decoded["messageName"], decoded["payload"])
        self.sent.append((decoded["messageName"], values))
        if decoded["messageName"] == "SAFE_CLOSE":
            uid = values["mcuCommandUid"]
            assert self.case.store.get_native_command(uid)["write_claimed"] == 1
            assert self.case.safety.get_physical_action(uid)["state"] == "ARMED"
        lib, endpoint, *_ = self.runtime
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), self.now - self.mcu_offset)
        return len(frame)

    def advance(self, elapsed):
        lib, endpoint, preparation, *_ = self.runtime
        lib.RuntimeClock_Advance(elapsed)
        lib.ActuatorRuntime_Tick()
        self.now += elapsed
        lib.McuWorkPreparation_Poll(preparation, endpoint, self.now - self.mcu_offset)


@pytest.fixture
def archived(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    from hardware.tests.test_mcu_safe_close_execution import enable
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = CloseWire(case, runtime)
        saved = without_final_packet(case, wire)
        lib, endpoint, preparation, replies, _, sink, guard = runtime
        replies.clear()
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        execution = enable(runtime, bind=False)
        wire.now += 1
        wire.mcu_offset = wire.now
        boot = wire.handshake()
        issue = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        yield case, wire, boot, issue, saved
        assert execution


def coordinator(case, wire, boot, revalidate=lambda record: None):
    from native_delivery_recovery_close import NativeDeliveryRecoveryClose
    return NativeDeliveryRecoveryClose(case.store, case.safety, boot, wire.write,
        revalidate=revalidate, clock=lambda: wire.now)


def test_concurrent_preparation_grants_only_the_creator_live_dispatch(archived, monkeypatch):
    from job_safety import JobSafetyError
    case, wire, boot, *_ = archived
    owners = [coordinator(case, wire, boot), coordinator(case, wire, boot)]
    barrier = Barrier(2)
    request = case.safety._client.request

    def synchronized_request(operation, payload):
        if operation == "GET_PHYSICAL_ACTION":
            barrier.wait(timeout=5)
        return request(operation, payload)

    monkeypatch.setattr(case.safety._client, "request", synchronized_request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        prepared = list(pool.map(lambda owner: owner.prepare(case.permit.work_uid, execution_window_ms=5000), owners))
    monkeypatch.setattr(case.safety._client, "request", request)
    assert prepared[0] == prepared[1]
    uid = prepared[0]["action"].action_uid
    # Calling only authorization is not a public operation; instead fail the
    # physical prerequisite before any permanent write to probe both owners.
    eligible = []
    for owner in owners:
        owner.revalidate = lambda record: (_ for _ in ()).throw(RuntimeError("live prerequisite probe"))
        try:
            owner.send_once(uid)
        except JobSafetyError as error:
            assert error.code == "RECOVERY_DISPATCH_NOT_LIVE"
            eligible.append(False)
        except RuntimeError as error:
            assert str(error) == "live prerequisite probe"
            eligible.append(True)
    assert sorted(eligible) == [False, True]
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


@pytest.mark.parametrize("sent", [False, True])
def test_pi_restart_keeps_original_close_identity_and_never_rearms_it(archived, tmp_path, sent):
    from edge_store import EdgeStore
    from job_safety import JobSafetyError
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = prepared["action"].action_uid
    if sent:
        assert owner.send_once(uid)
        wire.runtime[3].clear()  # reply lost, not proof of failure
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    case.store.initialize()
    restarted = coordinator(case, wire, wire.handshake())
    assert restarted.prepare(case.permit.work_uid, execution_window_ms=9000) == prepared
    if sent:
        assert not restarted.send_once(uid)
        restarted.poll(uid, wire.now)
        wire.pump(restarted)
        assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
    else:
        with pytest.raises(JobSafetyError, match="RECOVERY_DISPATCH_NOT_LIVE"):
            restarted.send_once(uid)
    assert [n for n, _ in wire.sent].count("SAFE_CLOSE") == int(sent)
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy


def test_reprepare_cannot_extend_the_original_live_deadline(archived):
    from job_safety import JobSafetyError
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=200)
    wire.advance(200)
    assert owner.prepare(case.permit.work_uid, execution_window_ms=5000) == prepared
    with pytest.raises(JobSafetyError, match="COMMAND_EXPIRED"):
        owner.send_once(prepared["action"].action_uid)
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


@pytest.mark.parametrize("boundary", ["PREPARE_NATIVE_RECOVERY_CLOSE", "ARM_PHYSICAL_ACTION"])
def test_slow_permanent_authorization_does_not_outlive_deadline(archived, monkeypatch, boundary):
    from job_safety import JobSafetyError
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=200)
    request = case.safety._client.request

    def delayed(operation, payload):
        result = request(operation, payload)
        if operation == boundary:
            wire.advance(200)
        return result

    monkeypatch.setattr(case.safety._client, "request", delayed)
    with pytest.raises(JobSafetyError, match="COMMAND_EXPIRED"):
        owner.send_once(prepared["action"].action_uid)
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)
    assert case.store.get_work_slot() == case.occupancy


def test_late_final_is_only_evidence_even_while_the_new_close_is_being_prepared(archived):
    case, wire, boot, issue, saved = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    case.store.save_native_mcu_result(saved["payload"])
    assert owner.send_once(prepared["action"].action_uid)
    wire.pump(owner)
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.list_native_result_report_tasks() == []
    assert len(case.store.list_native_delivery_issue_results(issue["issueUid"])) == 1


def test_deadline_expiring_after_dispatch_claim_still_prevents_uart_write(archived, monkeypatch):
    from job_safety import JobSafetyError
    case, wire, boot, *_ = archived
    owner = coordinator(case, wire, boot)
    prepared = owner.prepare(case.permit.work_uid, execution_window_ms=200)
    claim = case.store.claim_native_command_write

    def slow_commit(uid):
        result = claim(uid)
        wire.advance(200)
        return result

    monkeypatch.setattr(case.store, "claim_native_command_write", slow_commit)
    uid = prepared["action"].action_uid
    with pytest.raises(JobSafetyError, match="COMMAND_EXPIRED"):
        owner.send_once(uid)
    assert case.store.get_native_command(uid)["write_claimed"] == 1
    assert case.safety.get_physical_action(uid)["state"] == "ARMED"
    assert not owner.send_once(uid)
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


def test_generic_command_preparation_cannot_bypass_recovery_authority(archived):
    case, wire, boot, *_ = archived
    with pytest.raises(ValueError, match="recovery authority"):
        case.store.prepare_native_command("SAFE_CLOSE", str(uuid.uuid4()), 2,
            dict(scope="SINGLE_DELIVERY_DOOR", portNo=1, executionDeadlineMs=5000))
    assert not any(n == "SAFE_CLOSE" for n, _ in wire.sent)


def test_binding_and_new_command_roll_back_together_if_storage_fails(archived):
    case, wire, boot, *_ = archived
    with case.store.transaction() as conn:
        before = conn.execute("SELECT COUNT(*) FROM native_mcu_command").fetchone()[0]
        conn.execute("""CREATE TEMP TRIGGER fail_close BEFORE INSERT ON native_delivery_recovery_close
            BEGIN SELECT RAISE(ABORT, 'injected recovery storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected recovery"):
        coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    with case.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM native_mcu_command").fetchone()[0] == before
        assert conn.execute("SELECT COUNT(*) FROM native_delivery_recovery_close").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_close")
    owner = coordinator(case, wire, boot)
    assert owner.send_once(owner.prepare(case.permit.work_uid, execution_window_ms=5000)["action"].action_uid)


@pytest.mark.parametrize("corruption", ["hash", "command", "missing_issue", "missing_binding"])
def test_reopening_database_rejects_corrupted_recovery_authority(archived, tmp_path, corruption):
    from edge_store import EdgeStore
    case, wire, boot, *_ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    case.store.close()
    with sqlite3.connect(tmp_path / "edge.db") as conn:
        if corruption == "hash":
            conn.execute("UPDATE native_delivery_recovery_close SET binding_sha256=?", ("f" * 64,))
        elif corruption == "command":
            conn.execute("UPDATE native_mcu_command SET payload=zeroblob(length(payload)) WHERE command_uid=?", (prepared["action"].action_uid,))
        elif corruption == "missing_issue":
            conn.execute("DELETE FROM native_delivery_issue")
        else:
            conn.execute("DELETE FROM native_delivery_recovery_close")
    case.store = EdgeStore(str(tmp_path / "edge.db"))
    with pytest.raises((ValueError, RuntimeError)):
        case.store.initialize()


@pytest.mark.parametrize("point", ["before_binding", "after_binding", "committed"])
def test_process_exit_keeps_command_and_recovery_binding_atomic(archived, tmp_path, point):
    from edge_store import EdgeStore
    case, wire, boot, issue, _ = archived
    ledger = case.safety.get_physical_action(case.action.action_uid)
    case.store.close()
    code = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from edge_store import EdgeStore
store=EdgeStore(sys.argv[2]); store.initialize()
if sys.argv[5] != 'committed':
    store._conn.create_function('crash_now', 0, lambda: os._exit(77))
    at = 'BEFORE' if sys.argv[5]=='before_binding' else 'AFTER'
    store._conn.execute('CREATE TEMP TRIGGER crash_close '+at+
        ' INSERT ON native_delivery_recovery_close BEGIN SELECT crash_now(); END')
store.prepare_native_delivery_recovery_close(sys.argv[3], json.loads(sys.argv[4]),
    current_boot=lambda:2, execution_window_ms=5000)
os._exit(77)
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve().parents[1]),
        str(tmp_path / "edge.db"), case.permit.work_uid, json.dumps(ledger), point],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 77, result.stderr
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    with case.store.transaction() as conn:
        rows = conn.execute("SELECT action_uid FROM native_delivery_recovery_close").fetchall()
        assert len(rows) == int(point == "committed")
        assert conn.execute("SELECT COUNT(*) FROM native_mcu_command WHERE message_name='SAFE_CLOSE'").fetchone()[0] == len(rows)
    if rows:
        assert case.store.get_native_delivery_recovery_close(rows[0][0])["permit"] == case.permit
        assert case.store.get_native_command(rows[0][0])["write_claimed"] == 0
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy


def test_v35_migration_preserves_issue_without_creating_recovery_authority(archived, tmp_path):
    from edge_store import EdgeStore, CURRENT_SCHEMA_VERSION
    case, wire, boot, issue, _ = archived
    with case.store.transaction() as conn:
        conn.execute("DROP TABLE native_recovery_close_retirement")
        conn.execute("DROP TABLE native_recovery_close_confirmation")
        conn.execute("DROP TABLE native_delivery_recovery_close")
        conn.execute("DELETE FROM schema_version WHERE version>35")
    case.store.close()
    case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
    assert CURRENT_SCHEMA_VERSION == 39
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    with case.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM native_delivery_recovery_close").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["business", "runtime"])
def test_isolated_installed_inventory_reopens_original_close_without_replaying(archived, tmp_path, kind):
    from hardware.tests.test_native_release_custody import stage_app
    from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES
    case, wire, boot, issue, _ = archived
    prepared = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    case.store.close()
    app = stage_app(tmp_path, BUSINESS_APP_FILES if kind == "business" else RUNTIME_APP_FILES)
    code = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from edge_store import EdgeStore
store=EdgeStore(sys.argv[2]); store.initialize()
binding=store.get_native_delivery_recovery_close(sys.argv[3])
assert binding['action'].action_uid == sys.argv[3]
assert store.get_native_command(sys.argv[3])['write_claimed']==0
assert store.get_native_delivery_issue(binding['permit'].work_uid)['settlementAllowed'] is False
for name in ('edge_store','native_delivery_recovery_close','mcu_session','work_recovery'):
    assert Path(sys.modules[name].__file__).resolve().parent==Path(sys.argv[1]).resolve()
print(json.dumps(store.get_work_slot())); store.close()
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(app), str(tmp_path / "edge.db"),
        prepared["action"].action_uid], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == case.occupancy


def test_archived_delivery_gets_one_new_close_without_replaying_original_open(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    from native_delivery_recovery_close import NativeDeliveryRecoveryClose
    from hardware.tests.test_mcu_safe_close_execution import enable
    from mcu_actuator_handoff import McuActuatorEventHandoff

    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = CloseWire(case, runtime)
        without_final_packet(case, wire)
        lib, endpoint, preparation, replies, _, sink, guard = runtime
        replies.clear()
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        execution = enable(runtime, bind=False)
        wire.now += 1
        wire.mcu_offset = wire.now
        boot = wire.handshake()
        issue = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        owner = NativeDeliveryRecoveryClose(case.store, case.safety, boot, wire.write,
            revalidate=lambda record: None, clock=lambda: wire.now)
        prepared = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
        uid = prepared["action"].action_uid
        assert case.store.get_native_command(uid)["write_claimed"] == 0
        assert owner.send_once(uid)
        wire.pump(owner)
        assert not owner.send_once(uid)
        assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
        assert case.safety.get_native_recovery_close(uid)["recoveryUid"] == issue["recoveryUid"]
        wire.advance(100)
        handoff = McuActuatorEventHandoff(case.store, wire.write, 2)
        handoff.poll(wire.now)
        wire.pump(handoff)
        output = case.store.get_native_actuator_event(2, 1)
        value = uart.decode_payload(output["message_name"], output["payload"])
        assert output["message_name"] == "SAFE_CLOSE_RESULT"
        assert value["mcuCommandUid"] == uid and value["command"] == "CLOSE"
        assert value["outputStatus"] == "COMMAND_DISPATCHED"
        assert [n for n, _ in wire.sent].count("SAFE_CLOSE") == 1
        assert not any(n == "AUTHORIZE_DELIVERY_FIRST_OPEN" for n, _ in wire.sent)
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
        assert execution
