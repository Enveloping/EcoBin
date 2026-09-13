"""Issue-only cloud confirmation custody, never settlement or admission authority."""
import json
import uuid
import sqlite3
import subprocess
import sys
from pathlib import Path
from edge_store import EdgeStore
import pytest

from business_message_handler import BusinessMessageHandler
from device_identity import DeviceIdentity
from native_delivery_issue_report import NativeDeliveryIssueReporter
from onenet_wire import canonical_payload_sha256, decode_service_command
from work_recovery import NativeWorkRecovery
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_native_result_report import original_command
from hardware.tests.test_native_work_recovery import RecoveryWire
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_business_message_handler import invoke


def archive_and_report(case, runtime):
    wire = RecoveryWire(case, runtime)
    issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
        case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
    ids = NativeDeliveryIssueReporter(case.store, device_name="device-1").prepare(case.permit.work_uid)
    return issue, ids


def issue_confirmation_wire(case, uid, *, quarantined=False):
    service, params, _ = confirmation_wire(case, uid, quarantined=quarantined)
    if not quarantined:
        params["scalarFields"]["effectKind"] = 2  # UPDATED, not normal order CREATED.
    params["resultReferences"] = []
    params["scalarFields"]["payloadSha256"] = canonical_payload_sha256(decode_service_command(service, params)["payload"])
    return service, params, decode_service_command(service, params)


def test_issue_confirmation_cannot_carry_normal_order_reference(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        before = case.store.get_event(issue["issueUid"])
        service, params, _ = confirmation_wire(case, issue["issueUid"])
        handler = BusinessMessageHandler(case.store, DeviceIdentity("device-1"), edge_boot_id=10)
        assert invoke(handler, service, params).data["receiptState"] == 3
        assert case.store.get_event(issue["issueUid"]) == before
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_archive_confirmation_keeps_original_command_and_receipt_without_releasing_work(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        service, params, command = issue_confirmation_wire(case, issue["issueUid"])
        handler = BusinessMessageHandler(case.store, DeviceIdentity("device-1"), edge_boot_id=10)
        assert invoke(handler, service, params).data["receiptState"] == 1
        saved = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")
        assert saved["issueUid"] == issue["issueUid"]
        assert saved["eventUid"] == issue["issueUid"]
        assert saved["evidenceKind"] == "ARCHIVE"
        assert saved["businessValue"] == "NONE"
        assert saved["outcome"] == "BUSINESS_APPLIED" and saved["effectKind"] == "UPDATED"
        assert saved["resultReferences"] == []
        assert saved["confirmationUid"] == command["payload"]["confirmationUid"]
        receipt = json.loads(case.store.get_event(saved["receiptEventUid"])["payload_json"])
        assert receipt["payload"]["originalEventUid"] == issue["issueUid"]
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_v34_migration_does_not_promote_legacy_confirmation_without_original_envelope(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        _, _, command = issue_confirmation_wire(case, issue["issueUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")
        with case.store.transaction() as conn:
            conn.execute("DROP TABLE native_recovery_close_retirement")
            conn.execute("DROP TABLE native_recovery_close_confirmation")
            conn.execute("DROP TABLE native_delivery_recovery_close")
            conn.execute("DROP TABLE native_delivery_issue_confirmation")
            conn.execute("DELETE FROM schema_version WHERE version>34")
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
        assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1") is None
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "DUPLICATE"
        assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1") == saved


def test_legacy_flag_only_confirmation_cannot_acknowledge_issue(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        with pytest.raises(ValueError, match="complete backend confirmation"):
            case.store.receive_business_confirmation(str(uuid.uuid4()), issue["issueUid"], "BUSINESS_APPLIED")
        assert case.store.get_event(issue["issueUid"])["state"] == "PENDING"


@pytest.mark.parametrize("kind", ["ARCHIVE", "ARCHIVE_CONTEXT", "PROCESS_FACT"])
@pytest.mark.parametrize("quarantined", [False, True])
def test_each_evidence_confirmation_is_separate_and_survives_restart(runtime, tmp_path, kind, quarantined):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        task = next(row for row in case.store.list_native_delivery_issue_reports(case.permit.work_uid)
            if row["evidence_kind"] == kind)
        _, _, command = issue_confirmation_wire(case, task["event_uid"], quarantined=quarantined)
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid,
            device_name="device-1", event_uid=task["event_uid"])
        assert saved["outcome"] == ("EVENT_QUARANTINED" if quarantined else "BUSINESS_APPLIED")
        assert saved["evidenceKind"] == kind and saved["resultReferences"] == []
        if kind != "ARCHIVE":
            assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1") is None
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
        assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid,
            device_name="device-1", event_uid=task["event_uid"]) == saved
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "DUPLICATE"
        _, _, conflicting = issue_confirmation_wire(case, task["event_uid"], quarantined=not quarantined)
        assert case.store.receive_business_confirmation_and_create_receipt(command=conflicting, device_name="device-1") == "CONFLICT"
        assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid,
            device_name="device-1", event_uid=task["event_uid"]) == saved
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("wrong", ["digest", "device", "created", "reference", "payload_digest", "missing_envelope"])
def test_invalid_confirmation_never_changes_issue_or_receipt(runtime, tmp_path, wrong):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        _, _, command = issue_confirmation_wire(case, issue["issueUid"])
        if wrong == "digest": command["payload"]["originalPayloadSha256"] = "f" * 64
        if wrong == "device": command["targetDeviceName"] = "other-device"
        if wrong == "created": command["payload"]["effectKind"] = "CREATED"
        if wrong == "reference": command["payload"]["resultReferences"] = [dict(type="DELIVERY_ORDER", key="DO-WRONG")]
        command["payloadSha256"] = canonical_payload_sha256(command["payload"])
        if wrong == "payload_digest": command["payloadSha256"] = "e" * 64
        before = case.store.get_event(issue["issueUid"])
        with pytest.raises(ValueError):
            if wrong == "missing_envelope":
                case.store.receive_business_confirmation_and_create_receipt(command_uid=command["commandUid"],
                    confirmation_payload=command["payload"], device_name="device-1")
            else:
                case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1")
        assert case.store.get_event(issue["issueUid"]) == before
        assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1") is None
        with case.store.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM confirmation_inbox").fetchone()[0] == 0


def test_failed_issue_confirmation_commit_is_atomic_and_retryable(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        _, _, command = issue_confirmation_wire(case, issue["issueUid"])
        before = case.store.get_event(issue["issueUid"])
        with case.store.transaction() as conn:
            conn.execute("""CREATE TRIGGER fail_issue_confirmation BEFORE INSERT ON native_delivery_issue_confirmation
                BEGIN SELECT RAISE(ABORT,'injected issue confirmation failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1")
        assert case.store.get_event(issue["issueUid"]) == before
        assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1") is None
        with case.store.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM confirmation_inbox").fetchone()[0] == 0
            conn.execute("DROP TRIGGER fail_issue_confirmation")
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"


@pytest.mark.parametrize("boundary", ["before_insert", "during_insert", "after_commit"])
def test_process_death_leaves_only_whole_issue_confirmations(runtime, tmp_path, boundary):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        _, _, command = issue_confirmation_wire(case, issue["issueUid"])
        case.store.close()
        child = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from edge_store import EdgeStore
store = EdgeStore(sys.argv[2]); store.initialize()
if sys.argv[4] != 'after_commit':
    store._conn.create_function('crash_now', 0, lambda: os._exit(73))
    timing = 'BEFORE' if sys.argv[4] == 'before_insert' else 'AFTER'
    store._conn.execute('CREATE TEMP TRIGGER crash_confirmation ' + timing +
        ' INSERT ON native_delivery_issue_confirmation BEGIN SELECT crash_now(); END')
assert store.receive_business_confirmation_and_create_receipt(command=json.loads(sys.argv[3]),device_name='device-1') == 'ACCEPTED'
os._exit(73)
'''
        result = subprocess.run([sys.executable, "-I", "-c", child, str(Path(__file__).resolve().parents[1]),
            str(tmp_path / "edge.db"), json.dumps(command), boundary], capture_output=True, text=True, timeout=25)
        assert result.returncode == 73, result.stdout + result.stderr
        case.store = EdgeStore(str(tmp_path / "edge.db")); case.store.initialize()
        saved = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")
        assert (saved is not None) == (boundary == "after_commit")
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == (
            "DUPLICATE" if saved else "ACCEPTED")
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("corruption", ["command", "receipt", "archive_event", "inbox"])
def test_qualified_confirmation_detects_corrupt_original_custody(runtime, tmp_path, corruption):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        _, _, command = issue_confirmation_wire(case, issue["issueUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")
        with case.store.transaction() as conn:
            if corruption == "command":
                conn.execute("UPDATE native_delivery_issue_confirmation SET command_sha256=?", ("f"*64,))
            elif corruption == "inbox":
                conn.execute("UPDATE confirmation_inbox SET payload_json='{}'")
            else:
                uid = saved["receiptEventUid"] if corruption == "receipt" else saved["eventUid"]
                body = json.loads(case.store.get_event(uid)["payload_json"])
                body["occurredAt"] = "2000-01-01T00:00:00.000Z"
                conn.execute("UPDATE event_outbox SET payload_json=? WHERE event_uid=?", (json.dumps(body), uid))
        with pytest.raises(ValueError):
            case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")


def test_late_final_packet_confirmation_only_acknowledges_added_evidence(runtime, tmp_path):
    from hardware.tests.test_native_delivery_issue import without_final_packet
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        final = without_final_packet(case, wire)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        reporter = NativeDeliveryIssueReporter(case.store, device_name="device-1")
        reporter.prepare(case.permit.work_uid)
        _, _, command = issue_confirmation_wire(case, issue["issueUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        archive_confirmation = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")
        case.store.save_native_mcu_result(final["payload"])
        late = reporter.prepare(case.permit.work_uid)
        assert len(late) == 1
        _, _, command = issue_confirmation_wire(case, late[0])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        evidence_confirmation = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid,
            device_name="device-1", event_uid=late[0])
        assert evidence_confirmation["evidenceKind"] == "FINAL_RESULT"
        assert evidence_confirmation["businessValue"] == "NONE" and evidence_confirmation["resultReferences"] == []
        assert case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1") == archive_confirmation
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
        assert not case.store.list_native_result_report_tasks()
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("release_kind", ["business", "runtime"])
def test_staged_app_reopens_qualified_issue_confirmation_without_repository(runtime, tmp_path, release_kind):
    from hardware.tests.test_native_release_custody import stage_app, BUSINESS_APP_FILES, RUNTIME_APP_FILES
    app = stage_app(tmp_path, BUSINESS_APP_FILES if release_kind == "business" else RUNTIME_APP_FILES)
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        issue, _ = archive_and_report(case, runtime)
        _, _, command = issue_confirmation_wire(case, issue["issueUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")
        case.store.close()
        child = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from edge_store import EdgeStore
store = EdgeStore(sys.argv[2]); store.initialize()
print(json.dumps(dict(confirmation=store.get_native_delivery_issue_confirmation(sys.argv[3],device_name='device-1'),
    occupancy=store.get_work_slot())))
'''
        result = subprocess.run([sys.executable, "-I", "-c", child, str(app), str(tmp_path / "edge.db"),
            case.permit.work_uid], cwd=tmp_path, capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == dict(confirmation=saved, occupancy=case.occupancy)
