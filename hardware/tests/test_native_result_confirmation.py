"""Autonomous C result and OneNet service -> durable native confirmation.

Historical v31 upgrade and clean missing-weight report cases load unchanged
3178a994 v1 snapshots; they never rerun obsolete mechanical authorization.
"""
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from business_message_handler import BusinessMessageHandler
from device_identity import DeviceIdentity
from edge_store import EdgeStore
from native_result_report import NativeResultReporter
from onenet_wire import canonical_payload_sha256, decode_service_command, validate_stored_confirmation_envelope
from hardware.tests.test_mcu_simplified_execution import library, runtime
from hardware.tests.test_business_message_handler import load_command, invoke, complete_reply
from hardware.tests.native_confirmation_fixture import autonomous_result_case, legacy_result_case


def confirmation_wire(case, event_uid, *, quarantined=False):
    service, params, _ = load_command("confirm-edge-event.service-wire.json")
    event = json.loads(case.store.get_event(event_uid)["payload_json"])
    fields = params["scalarFields"]
    fields.update(commandUid=str(uuid.uuid4()), confirmationUid=str(uuid.uuid4()),
        targetDeviceName="device-1", originalEventUid=event_uid,
        originalPayloadSha256=event["payloadSha256"])
    params["target"]["uid"] = event_uid
    params["resultReferences"] = [dict(type=2 if case.clean else 1, key="CR-TEST" if case.clean else "DO-TEST")]
    if quarantined:
        fields.update(outcome=2, effectKindPresent=False, errorCodePresent=True,
            errorCode="EVENT_EVIDENCE_CONFLICT", quarantineUidPresent=True, quarantineUid=str(uuid.uuid4()))
        params["resultReferences"] = []
    fields["payloadSha256"] = canonical_payload_sha256(decode_service_command(service, params)["payload"])
    return service, params, decode_service_command(service, params)


@pytest.mark.parametrize("clean", [False, True])
def test_actual_report_confirmation_is_bound_before_reply_without_releasing_the_work(runtime, tmp_path, clean):
    with autonomous_result_case(runtime, tmp_path, clean=clean) as case:
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        report = reporter.prepare(case.permit, case.start["mcuCommandUid"])
        service, params, command = confirmation_wire(case, report["eventUid"])
        handler = BusinessMessageHandler(case.store, DeviceIdentity("device-1"), edge_boot_id=10)
        response = invoke(handler, service, params)
        assert response.data["receiptState"] == 1
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert saved["outcome"] == "BUSINESS_APPLIED"
        assert saved["eventUid"] == report["eventUid"]
        assert saved["confirmationUid"] == command["payload"]["confirmationUid"]
        assert saved["resultReferences"] == command["payload"]["resultReferences"]
        assert case.store.get_event(report["eventUid"])["state"] == "CONFIRMED"
        receipt = json.loads(case.store.get_event(saved["receiptEventUid"])["payload_json"])
        assert receipt["payload"]["originalEventUid"] == report["eventUid"]
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        complete_reply(response)
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") == saved


@pytest.mark.parametrize("clean", [False, True])
def test_platform_acceptance_is_not_a_business_decision_and_quarantine_never_becomes_success(runtime, tmp_path, clean):
    # A v1 clean report could legitimately already be frozen with no final
    # weight. Today's C produces FAILED instead: do not fabricate a v2 success.
    original = (legacy_result_case(tmp_path, clean=True) if clean else
        autonomous_result_case(runtime, tmp_path, clean=False, samples=()))
    with original as case:
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        report = reporter.prepare(case.permit, case.start["mcuCommandUid"])
        event = case.store.get_event(report["eventUid"])
        if clean:
            payload = json.loads(event["payload_json"])["payload"]
            assert payload["cleanerConfirmedFinalMeasurement"]["reportedWeightGrams"] is None
            assert json.loads(case.store.list_native_result_report_tasks()[0]["report_json"])["version"] == "ecobin-native-result-report-v1"
        case.store.record_event_platform_reply(event["edge_event_sequence"], 200)
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        service, params, command = confirmation_wire(case, report["eventUid"], quarantined=True)
        handler = BusinessMessageHandler(case.store, DeviceIdentity("device-1"), edge_boot_id=10)
        assert invoke(handler, service, params).data["receiptState"] == 1
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert saved["outcome"] == "EVENT_QUARANTINED" and saved["effectKind"] is None
        assert saved["resultReferences"] == []
        assert saved["quarantineUid"] == command["payload"]["quarantineUid"]
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("wrong", ["reference", "effect", "missing_reference", "digest", "device"])
def test_unrelated_confirmation_never_marks_the_original_report_as_applied(runtime, tmp_path, clean, wrong):
    with autonomous_result_case(runtime, tmp_path, clean=clean) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        before = case.store.get_event(report["eventUid"])
        service, params, _ = confirmation_wire(case, report["eventUid"])
        if wrong == "reference": params["resultReferences"][0]["type"] = 1 if clean else 2
        elif wrong == "effect": params["scalarFields"]["effectKind"] = 3
        elif wrong == "missing_reference": params["resultReferences"] = []
        elif wrong == "digest": params["scalarFields"]["originalPayloadSha256"] = "f" * 64
        elif wrong == "device": params["scalarFields"]["targetDeviceName"] = "other-device"
        params["scalarFields"]["payloadSha256"] = canonical_payload_sha256(decode_service_command(service, params)["payload"])
        handler = BusinessMessageHandler(case.store, DeviceIdentity("device-1"), edge_boot_id=10)
        assert invoke(handler, service, params).data["receiptState"] == 3
        assert case.store.get_event(report["eventUid"]) == before
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("clean", [False, True])
def test_confirmation_commit_rolls_back_as_one_unit_then_can_be_retried(runtime, tmp_path, clean):
    with autonomous_result_case(runtime, tmp_path, clean=clean, samples=[700, 1000] * 10) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        _, _, command = confirmation_wire(case, report["eventUid"])
        before = case.store.get_event(report["eventUid"])
        with case.store.transaction() as conn:
            conn.execute("""CREATE TRIGGER reject_native_confirmation BEFORE INSERT ON native_result_confirmation
                BEGIN SELECT RAISE(ABORT, 'injected native confirmation disk failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1")
        assert case.store.get_event(report["eventUid"]) == before
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        assert len(case.store.list_pending_events()) == 1
        with case.store.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM confirmation_inbox").fetchone()[0] == 0
            conn.execute("DROP TRIGGER reject_native_confirmation")
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")["outcome"] == "BUSINESS_APPLIED"


@pytest.mark.parametrize("clean", [False, True])
def test_restart_and_duplicate_reuse_the_original_qualified_confirmation_and_receipt(runtime, tmp_path, clean):
    with autonomous_result_case(runtime, tmp_path, clean=clean) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        service, params, _ = confirmation_wire(case, report["eventUid"])
        handler = BusinessMessageHandler(case.store, DeviceIdentity("device-1"), edge_boot_id=10)
        assert invoke(handler, service, params).data["receiptState"] == 1
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert case.store.mark_control_receipt_published(saved["receiptEventUid"])
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        handler = BusinessMessageHandler(case.store, DeviceIdentity("device-1"), edge_boot_id=11)
        assert invoke(handler, service, params).data["receiptState"] == 2
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") == saved
        assert [row["event_uid"] for row in case.store.list_pending_events()] == [saved["receiptEventUid"]]
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("clean", [False, True])
def test_legacy_flag_only_confirmation_cannot_bypass_native_command_custody(runtime, tmp_path, clean):
    with autonomous_result_case(runtime, tmp_path, clean=clean) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        with pytest.raises(ValueError, match="complete backend confirmation"):
            case.store.receive_business_confirmation(str(uuid.uuid4()), report["eventUid"], "BUSINESS_APPLIED")
        assert case.store.get_event(report["eventUid"])["state"] == "PENDING"


@pytest.mark.parametrize("corruption", ["schema", "clock"])
def test_confirmation_does_not_hide_corruption_of_its_original_receipt(runtime, tmp_path, corruption):
    with autonomous_result_case(runtime, tmp_path, clean=False) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        _, _, command = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        body = json.loads(case.store.get_event(saved["receiptEventUid"])["payload_json"])
        body["schemaVersion" if corruption == "schema" else "occurredAt"] = 1 if corruption == "schema" else "2000-01-01T00:00:00.000Z"
        with case.store.transaction() as conn:
            conn.execute("UPDATE event_outbox SET payload_json=? WHERE event_uid=?", (json.dumps(body), saved["receiptEventUid"]))
        with pytest.raises(ValueError, match="receipt"):
            case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")


@pytest.mark.parametrize("clean", [False, True])
def test_v31_upgrade_does_not_promote_an_unqualified_flag_without_replayed_original_command(runtime, tmp_path, clean):
    with legacy_result_case(tmp_path, clean=clean) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        original_report = case.store.get_event(report["eventUid"])["payload_json"]
        _, _, command = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        with case.store.transaction() as conn:
            conn.execute("DROP TABLE native_result_confirmation")
            conn.execute("DELETE FROM schema_version WHERE version>31")
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        assert case.store.get_event(report["eventUid"])["payload_json"] == original_report
        assert json.loads(case.store.list_native_result_report_tasks()[0]["report_json"])["version"] == "ecobin-native-result-report-v1"
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "DUPLICATE"
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") == saved


@pytest.mark.parametrize("clean", [False, True])
def test_accepted_confirmation_does_not_expire_on_restart_but_new_expired_command_is_rejected(runtime, tmp_path, clean, monkeypatch):
    with autonomous_result_case(runtime, tmp_path, clean=clean) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        _, _, command = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        monkeypatch.setattr("onenet_wire.local_deadline_reference", lambda: datetime.now(timezone.utc) + timedelta(days=1))
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") == saved
        other = copy.deepcopy(command)
        other["commandUid"] = str(uuid.uuid4())
        with pytest.raises(ValueError, match="expired"):
            case.store.receive_business_confirmation_and_create_receipt(command=other, device_name="device-1")


@pytest.mark.parametrize("boundary", ["before_insert", "during_insert", "after_commit"])
@pytest.mark.parametrize("clean", [False, True])
def test_process_death_at_confirmation_commit_keeps_only_whole_decisions(runtime, tmp_path, boundary, clean):
    with autonomous_result_case(runtime, tmp_path, clean=clean) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        _, _, command = confirmation_wire(case, report["eventUid"])
        case.store.close()
        child = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from edge_store import EdgeStore
store = EdgeStore(sys.argv[2]); store.initialize()
boundary = sys.argv[4]
if boundary != 'after_commit':
    store._conn.create_function('crash_now', 0, lambda: os._exit(73))
    timing = 'BEFORE' if boundary == 'before_insert' else 'AFTER'
    store._conn.execute('CREATE TEMP TRIGGER crash_confirmation ' + timing +
        ' INSERT ON native_result_confirmation BEGIN SELECT crash_now(); END')
assert store.receive_business_confirmation_and_create_receipt(command=json.loads(sys.argv[3]),device_name='device-1') == 'ACCEPTED'
os._exit(73)
'''
        run = subprocess.run([sys.executable, "-I", "-c", child, str(Path(__file__).resolve().parents[1]),
            str(tmp_path / "edge.db"), json.dumps(command), boundary], capture_output=True, text=True, timeout=25)
        assert run.returncode == 73, run.stdout + run.stderr
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert (saved is not None) == (boundary == "after_commit")
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == (
            "DUPLICATE" if saved else "ACCEPTED")
        final = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert final["confirmationUid"] == command["payload"]["confirmationUid"]
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_stored_confirmation_validation_cannot_be_used_for_physical_commands():
    with pytest.raises(ValueError, match="CONFIRM_EDGE_EVENT"):
        validate_stored_confirmation_envelope({"commandType": "START_DELIVERY_SESSION"})


@pytest.mark.parametrize("clean", [False, True])
def test_conflicting_second_decision_cannot_replace_the_first_backend_conclusion(runtime, tmp_path, clean):
    with autonomous_result_case(runtime, tmp_path, clean=clean) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        _, _, first = confirmation_wire(case, report["eventUid"], quarantined=True)
        assert case.store.receive_business_confirmation_and_create_receipt(command=first, device_name="device-1") == "ACCEPTED"
        saved = case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        _, _, second = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=second, device_name="device-1") == "CONFLICT"
        assert case.store.get_native_result_confirmation(case.permit, case.start["mcuCommandUid"], device_name="device-1") == saved
        assert saved["outcome"] == "EVENT_QUARANTINED"
        assert case.store.get_work_slot() == case.occupancy
