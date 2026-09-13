"""Staged source inventories must reopen actual native custody, without the repo.

Host C supplies UART evidence and only temporary SQLite databases are used.
This is not an ARM64 build, package approval, hardware probe or deployment.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_work_recovery import RecoveryWire
from work_recovery import NativeWorkRecovery


def stage_app(root, files):
    source = Path(__file__).resolve().parents[1]
    app = root / "isolated-app"
    for name in files:
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return app


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
def test_staged_release_preserves_issue_archive_and_appends_late_result_only(runtime, tmp_path, files):
    from hardware.tests.test_native_result_report import original_command
    from hardware.tests.test_native_delivery_issue import without_final_packet
    app = stage_app(tmp_path, files)
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        case.store.close()
        script = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from edge_store import EdgeStore
from job_safety import JobPermit
store=EdgeStore(sys.argv[2]); store.initialize()
original=json.loads(sys.argv[3])
assert store.get_native_delivery_issue(original["workUid"]) == original
store.save_native_mcu_result(bytes.fromhex(sys.argv[4]))
decision=store.evaluate_native_work_recovery(JobPermit(**original["permit"]), original["startCommandUid"],current_boot=lambda:None)
assert decision == dict(status="DELIVERY_ISSUE_ARCHIVED", issue=original)
evidence=store.list_native_delivery_issue_results(original["issueUid"])
assert len(evidence)==1 and evidence[0]["payload"].hex()==sys.argv[4]
assert not store.list_pending_events() and not store.list_native_result_report_tasks()
for name in ("edge_store","work_recovery","native_result_report","native_result_evidence","uart2_protocol"):
    assert Path(sys.modules[name].__file__).resolve().parent == Path(sys.argv[1]).resolve()
print(json.dumps(store.get_work_slot()))
store.close()
'''
        run = subprocess.run([sys.executable, "-I", "-c", script, str(app), str(tmp_path / "edge.db"),
            json.dumps(issue), saved["payload"].hex()], cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout) == case.occupancy


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
def test_staged_release_resumes_exact_issue_fragments_without_settlement_or_slot_release(runtime, tmp_path, files):
    from hardware.tests.test_native_result_report import original_command
    from hardware.tests.test_native_delivery_issue import without_final_packet
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    app = stage_app(tmp_path, files)
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        first = NativeDeliveryIssueReporter(case.store, device_name="device-1").prepare(case.permit.work_uid, limit=3)
        original = [case.store.get_event(uid) for uid in first]
        case.store.close()
        script = r'''
import hashlib, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
app = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(app))
from edge_store import EdgeStore
from native_delivery_issue_report import NativeDeliveryIssueReporter
from onenet_wire import encode_event_post
from work_recovery import canonical
issue, original = json.loads(sys.argv[3]), json.loads(sys.argv[4])
store = EdgeStore(sys.argv[2]); store.initialize()
reporter = NativeDeliveryIssueReporter(store, device_name="device-1")
while reporter.prepare(issue["workUid"], limit=3): pass
assert [store.get_event(e["event_uid"]) for e in original] == original
before = store.list_native_delivery_issue_reports(issue["workUid"])
store.save_native_mcu_result(bytes.fromhex(sys.argv[5]))
late = reporter.prepare(issue["workUid"])
assert len(late) == 1 and not reporter.prepare(issue["workUid"])
rows = store.list_native_delivery_issue_reports(issue["workUid"])
assert len(rows) == len(before)+1
parts = []
for row in rows:
    event = json.loads(store.get_event(row["event_uid"])["payload_json"])
    assert event["eventType"] in {"DELIVERY_ISSUE_ARCHIVED", "DELIVERY_ISSUE_EVIDENCE_APPENDED"}
    assert event["payload"]["businessValue"] == "NONE"
    encode_event_post(event["eventType"], event)
    if event["payload"].get("evidenceKind") == "ARCHIVE_CONTEXT": parts.append(event["payload"])
raw = b"".join(bytes.fromhex(p["dataHex"]) for p in sorted(parts, key=lambda p: p["partIndex"]))
assert raw == canonical(issue).encode("ascii")
assert all(p["evidenceSha256"] == hashlib.sha256(raw).hexdigest() for p in parts)
assert store.get_native_delivery_issue(issue["workUid"]) == issue
assert not store.list_native_result_report_tasks()
for name in ("edge_store", "native_delivery_issue_report", "onenet_wire", "work_recovery", "uart2_protocol"):
    assert Path(sys.modules[name].__file__).resolve().parent == app
print(json.dumps(store.get_work_slot()))
store.close()
'''
        run = subprocess.run([sys.executable, "-I", "-c", script, str(app), str(tmp_path / "edge.db"),
            json.dumps(issue), json.dumps(original), saved["payload"].hex()], cwd=tmp_path,
            capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout) == case.occupancy


REOPEN = r"""
import json
from pathlib import Path
import sys

app = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(app))
from edge_store import EdgeStore
from edge_store_prepare import main as prepare_schema

assert prepare_schema(["--database", sys.argv[2]]) == 0
store = EdgeStore(sys.argv[2])
try:
    store.initialize()
    intent = store.get_native_work_recovery_intent(sys.argv[3])
    work_uid = intent["work_uid"]
    binding = store.get_native_action_by_key(work_uid, sys.argv[4])
    facts = store.list_native_work_recovery_facts(intent["recovery_uid"], limit=1000)
    # Prove the lazy validators are loaded from the staged app, not an
    # inherited PYTHONPATH, the source checkout or a developer site package.
    for name in ("edge_store", "edge_store_prepare", "mcu_action_evidence", "work_recovery",
                 "mcu_session", "job_safety", "uart2_protocol"):
        assert Path(sys.modules[name].__file__).resolve().parent == app, name
    print(json.dumps(dict(intent=intent, action_uid=binding["action"].action_uid,
        facts=[dict(row, payload=row["payload"].hex()) for row in facts],
        occupancy=store.get_work_slot(), events=store.list_pending_events(),
        result_tasks=store.list_native_result_report_tasks()), sort_keys=True))
finally:
    store.close()
"""


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean_work", [False, True], ids=["delivery", "clean"])
def test_staged_release_reopens_saved_recovery_without_losing_evidence(runtime, tmp_path, files, clean_work):
    app = stage_app(tmp_path, files)
    with executed_action_case(runtime, tmp_path, clean_work=clean_work) as case:
        wire = RecoveryWire(case, runtime)
        boot = wire.reset_mcu()
        decision = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(
            case.permit, case.start["mcuCommandUid"])
        intent = decision["intent"]
        facts = case.store.list_native_work_recovery_facts(intent["recovery_uid"], limit=1000)
        case.store.close()
        before = len(wire.sent)
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN, str(app),
            str(tmp_path / "edge.db"), intent["recovery_uid"], case.action.action_key],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value == dict(intent=intent, action_uid=case.action.action_uid,
            facts=[dict(row, payload=row["payload"].hex()) for row in facts],
            occupancy=case.occupancy, events=[], result_tasks=[])
        assert len(wire.sent) == before
        assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"


REOPEN_RESULT = r"""
import json
from pathlib import Path
import sys
app = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(app))
from edge_store import EdgeStore
store = EdgeStore(sys.argv[2])
try:
    store.initialize()
    binding = store.get_native_action_by_key(sys.argv[3], sys.argv[4])
    decision = store.evaluate_native_work_recovery(binding["permit"], sys.argv[5], current_boot=lambda: None)
    for name in ("edge_store", "work_recovery", "mcu_action_evidence", "native_result_evidence", "mcu_configuration", "uart2_protocol"):
        assert Path(sys.modules[name].__file__).resolve().parent == app, name
    print(json.dumps(dict(decision=decision, occupancy=store.get_work_slot(),
        events=store.list_pending_events()), default=bytes.hex, sort_keys=True))
finally:
    store.close()
"""


REOPEN_REPORT = r"""
import json
from pathlib import Path
import sys
app = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(app))
from edge_store import EdgeStore
store = EdgeStore(sys.argv[2])
try:
    store.initialize()
    binding = store.get_native_action_by_key(sys.argv[3], sys.argv[4])
    report = store.get_native_result_report(binding['permit'], sys.argv[5], device_name='device-1')
    assert Path(sys.modules['native_result_report'].__file__).resolve().parent == app
    print(json.dumps(dict(report=report, event=store.get_event(report['eventUid']), occupancy=store.get_work_slot())))
finally:
    store.close()
"""


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("samples", [[700, 1000] * 10, []], ids=["median", "timeout"])
def test_staged_release_reopens_original_reliable_report_without_source_tree_or_hardware(runtime, tmp_path, files, clean, samples):
    from hardware.tests.test_native_result_report import original_command, finish_with_samples
    from native_result_report import NativeResultReporter
    app = stage_app(tmp_path, files)
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        finish_with_samples(case, runtime, samples)
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        expected = dict(report=report, event=case.store.get_event(report["eventUid"]), occupancy=case.store.get_work_slot())
        case.store.close()
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN_REPORT, str(app),
            str(tmp_path / "edge.db"), case.permit.work_uid, case.action.action_key, case.start["mcuCommandUid"]],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected


REOPEN_CONFIRMATION = r"""
import json, sys
from pathlib import Path
app = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(app))
from edge_store import EdgeStore
store = EdgeStore(sys.argv[2])
try:
    store.initialize()
    binding = store.get_native_action_by_key(sys.argv[3], sys.argv[4])
    decision = store.get_native_result_confirmation(binding['permit'], sys.argv[5], device_name='device-1')
    for name in ('edge_store', 'onenet_wire', 'native_result_report'):
        assert Path(sys.modules[name].__file__).resolve().parent == app
    print(json.dumps(dict(confirmation=decision, occupancy=store.get_work_slot())))
finally:
    store.close()
"""


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean", [False, True])
def test_staged_release_reopens_the_original_backend_decision_without_releasing_work(runtime, tmp_path, files, clean):
    from hardware.tests.test_native_result_report import original_command, finish_with_samples
    from hardware.tests.test_native_result_confirmation import confirmation_wire
    from native_result_report import NativeResultReporter
    app = stage_app(tmp_path, files)
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        finish_with_samples(case, runtime, [700] * 5)
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        _, _, command = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        expected = dict(confirmation=case.store.get_native_result_confirmation(case.permit,
            case.start["mcuCommandUid"], device_name="device-1"), occupancy=case.store.get_work_slot())
        case.store.close()
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN_CONFIRMATION, str(app),
            str(tmp_path / "edge.db"), case.permit.work_uid, case.action.action_key, case.start["mcuCommandUid"]],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean_work", [False, True], ids=["delivery", "clean"])
def test_staged_release_reconciles_actual_complete_result_and_original_configuration(runtime, tmp_path, files, clean_work):
    app = stage_app(tmp_path, files)
    with executed_action_case(runtime, tmp_path, clean_work=clean_work) as case:
        wire = RecoveryWire(case, runtime)
        wire.finish_clean() if clean_work else wire.finish_delivery()
        decision = case.store.evaluate_native_work_recovery(case.permit, case.start["mcuCommandUid"], current_boot=lambda: None)
        assert decision["evidence"]["state"] == "MATCHED"
        case.store.close()
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN_RESULT, str(app),
            str(tmp_path / "edge.db"), case.permit.work_uid, case.action.action_key, case.start["mcuCommandUid"]],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        expected = dict(decision=decision, occupancy=case.occupancy, events=[])
        assert json.loads(result.stdout) == json.loads(json.dumps(expected, default=bytes.hex))
