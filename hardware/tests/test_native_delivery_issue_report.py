"""Real native issue -> durable, nonfinancial OneNet report and raw evidence."""
import hashlib
import json
import copy
import sqlite3
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from hardware.tests.test_mcu_simplified_execution import library, runtime
from hardware.tests.native_autonomous_recovery_fixture import autonomous_active_case as executed_action_case
from hardware.tests.test_native_work_recovery import RecoveryWire
from hardware.tests.test_native_result_report import original_command, validate_event
from hardware.tests.test_native_delivery_issue import without_final_packet
from work_recovery import NativeWorkRecovery, canonical
from onenet_wire import encode_event_post


def test_actual_archived_delivery_reports_only_issue_and_exact_original_evidence(runtime, tmp_path):
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        without_final_packet(case, wire)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        reporter = NativeDeliveryIssueReporter(case.store, device_name="device-1")
        ids = reporter.prepare(case.permit.work_uid)
        assert ids and ids[0] == issue["issueUid"]
        events = [json.loads(case.store.get_event(uid)["payload_json"]) for uid in ids]
        header = events[0]
        assert header["eventType"] == "DELIVERY_ISSUE_ARCHIVED"
        assert header["payload"]["reason"] == "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
        assert header["payload"]["archiveEvidenceSha256"] == hashlib.sha256(canonical(issue).encode("ascii")).hexdigest()
        assert all(e["eventType"] in {"DELIVERY_ISSUE_ARCHIVED", "DELIVERY_ISSUE_EVIDENCE_APPENDED"} for e in events)
        for event in events:
            assert event["payload"]["businessValue"] == "NONE"
            validate_event(event)
            encode_event_post(event["eventType"], event)
        parts = [e["payload"] for e in events[1:] if e["payload"]["evidenceKind"] == "ARCHIVE_CONTEXT"]
        rebuilt = b"".join(bytes.fromhex(p["dataHex"]) for p in sorted(parts, key=lambda p:p["partIndex"]))
        assert rebuilt == canonical(issue).encode("ascii")
        assert reporter.prepare(case.permit.work_uid) == []
        assert len(case.store.list_native_delivery_issue_reports(case.permit.work_uid)) == len(ids)
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_real_outbox_relays_frozen_issue_evidence_without_treating_transport_ack_as_business_success(runtime, tmp_path):
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    from business_outbox_relay import BusinessOutboxRelay
    from hardware.tests.test_business_outbox_relay import FakeCloudTransport
    from cloud_transport import CloudEventPlatformResult
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")
        ids = NativeDeliveryIssueReporter(case.store, device_name="device-1").prepare(case.permit.work_uid, limit=3)
        frozen = {uid: case.store.get_event(uid)["payload_json"] for uid in ids}
        transport = FakeCloudTransport()
        relay = BusinessOutboxRelay(case.store, transport)
        relay.relay_pending_events()
        assert {e.event_uid for e in transport.sent_events} == set(ids)
        for event in transport.sent_events:
            assert event.params == json.loads(frozen[event.event_uid])
            encode_event_post(event.event_type, event.params)
            relay.handle_platform_result(CloudEventPlatformResult(
                edge_event_sequence=event.params["edgeEventSequence"], code=200))
            relay.handle_transport_ack(event.event_uid)
            stored = case.store.get_event(event.event_uid)
            assert stored["payload_json"] == frozen[event.event_uid]
            assert stored["state"] == "PENDING" and stored["confirmed_at"] is None
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert not case.store.list_native_result_report_tasks()


@pytest.mark.parametrize("field,value", [("partIndex",2),("partCount",2),("evidenceSizeBytes",256),
    ("dataHex","00"),("evidenceIndex",1),("archiveEvidenceSha256","b"*64)])
def test_runtime_wire_encoder_rejects_inconsistent_issue_fragments(field,value):
    from generate_contracts import build_onenet_examples
    from onenet_wire import canonical_payload_sha256
    event = copy.deepcopy(build_onenet_examples()["delivery-issue-evidence-appended.event.json"][0])
    event["payload"][field] = value
    event["payloadSha256"] = canonical_payload_sha256(event["payload"])
    with pytest.raises(ValueError):
        encode_event_post(event["eventType"], event)


def test_bounded_batches_resume_after_restart_and_late_result_only_adds_new_evidence(runtime, tmp_path):
    from edge_store import EdgeStore
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        context = dict(case.occupancy["context"], diagnostic="称重记录"*3000)
        case.store.update_work_context(case.permit.work_uid, context)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        reporter = NativeDeliveryIssueReporter(case.store, device_name="device-1")
        first = reporter.prepare(case.permit.work_uid, limit=3)
        assert len(first) == 3
        original = [case.store.get_event(uid) for uid in first]
        case.store.close()
        case.store = EdgeStore(str(tmp_path/"edge.db")); case.store.initialize()
        reporter = NativeDeliveryIssueReporter(case.store, device_name="device-1")
        ids = first[:]
        while new := reporter.prepare(case.permit.work_uid, limit=100):
            assert len(new) <= 100
            ids.extend(new)
        assert len(ids) == len(set(ids)) and len(ids) > 100
        assert [case.store.get_event(uid) for uid in first] == original
        events = [json.loads(case.store.get_event(uid)["payload_json"]) for uid in ids]
        parts = [e["payload"] for e in events if e["payload"].get("evidenceKind") == "ARCHIVE_CONTEXT"]
        raw = b"".join(bytes.fromhex(p["dataHex"]) for p in sorted(parts,key=lambda p:p["partIndex"]))
        assert raw == canonical(issue).encode("ascii")
        case.store.save_native_mcu_result(saved["payload"])
        late = reporter.prepare(case.permit.work_uid)
        assert len(late) == 1
        event = json.loads(case.store.get_event(late[0])["payload_json"])
        assert event["payload"]["evidenceKind"] == "FINAL_RESULT"
        assert bytes.fromhex(event["payload"]["dataHex"]) == saved["payload"]
        assert reporter.prepare(case.permit.work_uid) == []
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
        assert case.store.get_work_slot()["context"] == context


@pytest.mark.parametrize("failure", ["link", "commit"])
def test_issue_report_batch_rolls_back_events_and_bindings_together(runtime, tmp_path, failure):
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        reporter = NativeDeliveryIssueReporter(case.store, device_name="device-1")
        target = (sqlite3.SQLITE_INSERT,"native_delivery_issue_report") if failure == "link" else (sqlite3.SQLITE_TRANSACTION,"COMMIT")
        case.store._conn.set_authorizer(lambda op, name, *_: sqlite3.SQLITE_DENY if (op,name)==target else sqlite3.SQLITE_OK)
        try:
            with pytest.raises(sqlite3.DatabaseError): reporter.prepare(case.permit.work_uid)
        finally: case.store._conn.set_authorizer(None)
        assert case.store.list_pending_events() == [] and case.store.list_native_delivery_issue_reports(case.permit.work_uid) == []
        assert reporter.prepare(case.permit.work_uid)[0] == issue["issueUid"]


def test_two_reporters_share_one_set_of_immutable_events(runtime, tmp_path):
    from edge_store import EdgeStore
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")
        other = EdgeStore(case.store.db_path); other.initialize()
        try:
            with ThreadPoolExecutor(2) as pool:
                jobs = [pool.submit(NativeDeliveryIssueReporter(s,device_name="device-1").prepare,case.permit.work_uid)
                    for s in (case.store,other)]
                ids = jobs[0].result()+jobs[1].result()
            assert len(ids) == len(set(ids)) == len(case.store.list_native_delivery_issue_reports(case.permit.work_uid))
        finally: other.close()


@pytest.mark.parametrize("point", ["before_event", "before_binding", "after_commit"])
def test_process_exit_preserves_only_committed_issue_report_batches(runtime, tmp_path, point):
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        script = '''
import os, sqlite3, sys
sys.path.insert(0,sys.argv[1])
from edge_store import EdgeStore
from native_delivery_issue_report import NativeDeliveryIssueReporter
s=EdgeStore(sys.argv[2]); s.initialize()
target={"before_event":(sqlite3.SQLITE_INSERT,"event_outbox"),
        "before_binding":(sqlite3.SQLITE_INSERT,"native_delivery_issue_report")}.get(sys.argv[4])
def crash(op,name,*args):
    if (op,name)==target: os._exit(76)
    return sqlite3.SQLITE_OK
s._conn.set_authorizer(crash)
assert len(NativeDeliveryIssueReporter(s,device_name="device-1").prepare(sys.argv[3],limit=3)) == 3
os._exit(76)
'''
        run = subprocess.run([sys.executable,"-I","-c",script,str(Path(__file__).resolve().parents[1]),
            case.store.db_path,case.permit.work_uid,point],cwd=tmp_path,capture_output=True,text=True,timeout=20)
        assert run.returncode == 76, run.stderr
        existing = case.store.list_native_delivery_issue_reports(case.permit.work_uid)
        assert len(existing) == (3 if point=="after_commit" else 0)
        assert len(case.store.list_pending_events()) == len(existing)
        reporter = NativeDeliveryIssueReporter(case.store,device_name="device-1")
        reporter.prepare(case.permit.work_uid)
        assert case.store.get_event(issue["issueUid"])["event_type"] == "DELIVERY_ISSUE_ARCHIVED"
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("failure", [None,"table","version"])
def test_schema33_upgrade_keeps_original_archive_without_automatically_reporting(runtime,tmp_path,failure):
    from edge_store import EdgeStore
    with executed_action_case(runtime,tmp_path,clean_work=False,cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case,runtime)
        issue = NativeWorkRecovery(case.store,wire.reset_mcu(),clock=lambda:wire.now).archive_delivery(
            case.permit,case.start["mcuCommandUid"],device_name="device-1")["issue"]
        with case.store.transaction() as conn:
            conn.execute("DROP TABLE native_delivery_issue_report")
            conn.execute("DELETE FROM schema_version WHERE version>33")
        case.store.close()
        case.store=EdgeStore(str(tmp_path/"edge.db")); case.store._open_connection()
        target={"table":(sqlite3.SQLITE_CREATE_TABLE,"native_delivery_issue_report"),
            "version":(sqlite3.SQLITE_INSERT,"schema_version")}.get(failure)
        if target:
            case.store._conn.set_authorizer(lambda op,name,*_:sqlite3.SQLITE_DENY if (op,name)==target else sqlite3.SQLITE_OK)
            with pytest.raises(sqlite3.DatabaseError): case.store.initialize()
            case.store.close()
            with sqlite3.connect(case.store.db_path) as conn:
                assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 33
                assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='native_delivery_issue_report'").fetchone()[0] == 0
            case.store=EdgeStore(str(tmp_path/"edge.db"))
        case.store.initialize()
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
        assert case.store.list_native_delivery_issue_reports(case.permit.work_uid) == []
        assert case.store.list_pending_events() == []
