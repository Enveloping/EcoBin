"""Staged source inventories must reopen actual native custody, without the repo.

Host C supplies UART evidence and only temporary SQLite databases are used.
This is not an ARM64 build, package approval, hardware probe or deployment.
"""
import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES
from hardware.tests.test_mcu_simplified_execution import library, request, runtime, select, tick
from hardware.tests.test_mcu_work_preparation import original_scope, take_samples
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_simplified_mcu_pi_business import real_work
from mcu_result_handoff import McuResultHandoff
from mcu_session import McuBootSession
from mcu_work_query import McuWorkQuery
import uart2_protocol as uart
from work_recovery import NativeWorkRecovery


class ReleaseWire:
    """Current rc.23 START-only MCU execution used by staged-release tests."""

    def __init__(self, case, runtime):
        self.case = case
        self.runtime = runtime
        self._wire = case.wire
        self.now = self._wire.now

    @property
    def sent(self):
        return self._wire.sent

    def write(self, frame):
        self._wire.now = self.now
        return self._wire.write(frame)

    def pump(self, client):
        while self.runtime[3]:
            client.accept_frame(self.runtime[3].pop(0), self.now)

    def reset_mcu(self):
        lib, endpoint, preparation, replies, _, sink, guard = self.runtime
        replies.clear()
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        assert lib.ActuatorRuntime_SetDoorTarget(1)
        self.now += 1
        self._wire.now = self.now
        return self.handshake()

    def handshake(self):
        boot = McuBootSession(self.case.store, self.write)
        boot.poll(self.now)
        self.pump(boot)
        return boot

    def handoff_result(self):
        scope = original_scope(self.case.start, clean=self.case.clean)
        del scope["queryId"]
        query = McuWorkQuery(self.case.store, self.write, scope)
        assert query.poll(self.now)
        self.pump(query)
        observed = query.observation(self.now)
        assert observed["status"] == "RESULT_HELD"
        identity = dict(mcuBootId=self.case.boot, workUid=self.case.permit.work_uid,
            resultSequence=observed["resultSequence"], resultDigestSha256=observed["resultDigestSha256"])
        handoff = McuResultHandoff(self.case.store, self.write, identity)
        assert handoff.poll(self.now)
        self.pump(handoff)
        return self.case.store.get_native_mcu_result(self.case.boot, identity["resultSequence"])

    def finish_delivery(self):
        start = self.case.start
        self.now = tick(self.runtime, self.now, 100)
        self.now = tick(self.runtime, self.now, start["deliveryAutoCloseMs"])
        self.now = tick(self.runtime, self.now, 100)
        self.now = tick(self.runtime, self.now, inputs()["device"]["deliveryDoorTravelWaitMs"])
        self.now = take_samples(self.runtime, [700] * 5, start=self.now, measurement=2)
        assert select(self.runtime, self.case.delivery, self.now, "END")
        self._wire.now = self.now
        return self.handoff_result()

    def finish_clean(self):
        start = self.case.start
        self.now = tick(self.runtime, self.now, inputs()["device"]["cleanSolenoidPulseMs"])
        assert request(self.runtime, self.case.cleanup, start, self.now, "CLEAN_UNLOCK_REQUESTED")
        self.now = tick(self.runtime, self.now, inputs()["device"]["cleanSolenoidPulseMs"])
        assert request(self.runtime, self.case.cleanup, start, self.now, "CLEAN_FINISH_REQUESTED")
        self.now = take_samples(self.runtime, [100] * 5, start=self.now, measurement=2)
        assert self.runtime[0].McuWorkPreparation_Poll(self.runtime[2], self.runtime[1], self.now)
        self._wire.now = self.now
        return self.handoff_result()


@contextmanager
def release_work_case(runtime, tmp_path, *, clean_work):
    """Adapt the release checks to the current autonomous START boundary.

    The old shared helper creates a second per-action authorization which rc.23
    deliberately rejects.  These tests care about release custody, so they use
    the public START flow and assert that no legacy action is introduced.
    """
    with real_work(runtime, tmp_path, clean_work) as case:
        case.clean = clean_work
        case.occupancy = case.store.get_work_slot()
        case.wire = ReleaseWire(case, runtime)
        yield case


def finish_with_samples(case, runtime, samples, *, window_expired=False):
    """Finish one autonomous rc.23 work with the requested final samples."""
    wire = case.wire
    if case.clean:
        wire.now = tick(runtime, wire.now, inputs()["device"]["cleanSolenoidPulseMs"])
        assert request(runtime, case.cleanup, case.start, wire.now, "CLEAN_UNLOCK_REQUESTED")
        wire.now = tick(runtime, wire.now, inputs()["device"]["cleanSolenoidPulseMs"])
        assert request(runtime, case.cleanup, case.start, wire.now, "CLEAN_FINISH_REQUESTED")
    else:
        wire.now = tick(runtime, wire.now, 100)
        wire.now = tick(runtime, wire.now, case.start["deliveryAutoCloseMs"])
        wire.now = tick(runtime, wire.now, 100)
        wire.now = tick(runtime, wire.now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = wire.now
    wire.now = take_samples(runtime, samples, start=began, measurement=2)
    if len(samples) != 5:
        wire.now = tick(runtime, wire.now, began + 5000 - wire.now)
    if not case.clean and samples:
        if window_expired:
            wire.now = tick(runtime, wire.now, case.start["continueDeliveryWaitMs"])
        else:
            assert select(runtime, case.delivery, wire.now, "END")
    assert runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], wire.now)
    wire._wire.now = wire.now
    return wire.handoff_result()


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
    from hardware.tests.test_native_delivery_issue import without_final_packet
    app = stage_app(tmp_path, files)
    with release_work_case(runtime, tmp_path, clean_work=False) as case:
        wire = case.wire
        saved = without_final_packet(case, wire)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        commands = [[row["command_uid"], row["message_name"]] for row in case.store.list_native_commands()]
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
print(json.dumps(dict(occupancy=store.get_work_slot(),
    nativeCommands=[[row["command_uid"], row["message_name"]] for row in store.list_native_commands()])))
store.close()
'''
        run = subprocess.run([sys.executable, "-I", "-c", script, str(app), str(tmp_path / "edge.db"),
            json.dumps(issue), saved["payload"].hex()], cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout) == dict(occupancy=case.occupancy, nativeCommands=commands)


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
def test_staged_release_resumes_exact_issue_fragments_without_settlement_or_slot_release(runtime, tmp_path, files):
    from hardware.tests.test_native_delivery_issue import without_final_packet
    from native_delivery_issue_report import NativeDeliveryIssueReporter
    app = stage_app(tmp_path, files)
    with release_work_case(runtime, tmp_path, clean_work=False) as case:
        wire = case.wire
        saved = without_final_packet(case, wire)
        issue = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")["issue"]
        first = NativeDeliveryIssueReporter(case.store, device_name="device-1").prepare(case.permit.work_uid, limit=3)
        original = [case.store.get_event(uid) for uid in first]
        commands = [[row["command_uid"], row["message_name"]] for row in case.store.list_native_commands()]
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
print(json.dumps(dict(occupancy=store.get_work_slot(),
    nativeCommands=[[row["command_uid"], row["message_name"]] for row in store.list_native_commands()])))
store.close()
'''
        run = subprocess.run([sys.executable, "-I", "-c", script, str(app), str(tmp_path / "edge.db"),
            json.dumps(issue), json.dumps(original), saved["payload"].hex()], cwd=tmp_path,
            capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout) == dict(occupancy=case.occupancy, nativeCommands=commands)


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
    for name in ("edge_store", "edge_store_prepare", "work_recovery", "mcu_session",
                 "job_safety", "uart2_protocol"):
        assert Path(sys.modules[name].__file__).resolve().parent == app, name
    print(json.dumps(dict(intent=intent, action_uid=None if binding is None else binding["action"].action_uid,
        facts=[dict(row, payload=row["payload"].hex()) for row in facts],
        occupancy=store.get_work_slot(), events=store.list_pending_events(),
        result_tasks=store.list_native_result_report_tasks(),
        native_commands=[[row["command_uid"], row["message_name"]] for row in store.list_native_commands()]),
        sort_keys=True))
finally:
    store.close()
"""


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean_work", [False, True], ids=["delivery", "clean"])
def test_staged_release_reopens_saved_recovery_without_losing_evidence(runtime, tmp_path, files, clean_work):
    app = stage_app(tmp_path, files)
    with release_work_case(runtime, tmp_path, clean_work=clean_work) as case:
        wire = case.wire
        boot = wire.reset_mcu()
        decision = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(
            case.permit, case.start["mcuCommandUid"])
        intent = decision["intent"]
        facts = case.store.list_native_work_recovery_facts(intent["recovery_uid"], limit=1000)
        commands = [[row["command_uid"], row["message_name"]] for row in case.store.list_native_commands()]
        case.store.close()
        before = len(wire.sent)
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN, str(app),
            str(tmp_path / "edge.db"), intent["recovery_uid"],
            "clean:first-unlock" if clean_work else "delivery:first-open"],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value == dict(intent=intent, action_uid=None,
            facts=[dict(row, payload=row["payload"].hex()) for row in facts],
            occupancy=case.occupancy, events=[], result_tasks=[], native_commands=commands)
        assert len(wire.sent) == before


REOPEN_RESULT = r"""
import json
from pathlib import Path
import sys
app = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(app))
from edge_store import EdgeStore
from job_safety import JobPermit
store = EdgeStore(sys.argv[2])
try:
    store.initialize()
    permit = JobPermit(**json.loads(sys.argv[3]))
    decision = store.evaluate_native_work_recovery(permit, sys.argv[4], current_boot=lambda: None)
    for name in ("edge_store", "work_recovery", "mcu_action_evidence", "native_result_evidence", "mcu_configuration", "uart2_protocol"):
        assert Path(sys.modules[name].__file__).resolve().parent == app, name
    print(json.dumps(dict(decision=decision, occupancy=store.get_work_slot(),
        events=store.list_pending_events(),
        nativeCommands=[[row["command_uid"], row["message_name"]] for row in store.list_native_commands()]),
        default=bytes.hex, sort_keys=True))
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
from job_safety import JobPermit
store = EdgeStore(sys.argv[2])
try:
    store.initialize()
    permit = JobPermit(**json.loads(sys.argv[3]))
    report = store.get_native_result_report(permit, sys.argv[4], device_name='device-1')
    if report is None:
        from native_result_report import supports_result_policy
        import uart2_protocol as uart
        # The timeout-clean case intentionally has no normal report. Reopen
        # and prove the exact complete result still has the same no-report
        # policy, without manufacturing an event or settlement.
        decision = store.evaluate_native_work_recovery(permit, sys.argv[4], current_boot=lambda: None)
        assert decision['status'] == 'COMPLETE_RESULT_AVAILABLE'
        assert not supports_result_policy(uart.decode_payload('WORK_RESULT', decision['result']['payload']))
        report = {'state': 'WAITING_FOR_RESULT_POLICY'}
    assert Path(sys.modules['native_result_report'].__file__).resolve().parent == app
    print(json.dumps(dict(report=report, event=(store.get_event(report['eventUid']) if 'eventUid' in report else None),
        occupancy=store.get_work_slot(),
        nativeCommands=[[row["command_uid"], row["message_name"]] for row in store.list_native_commands()])))
finally:
    store.close()
"""


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("samples", [[700, 1000] * 10, []], ids=["median", "timeout"])
def test_staged_release_reopens_original_reliable_report_without_source_tree_or_hardware(runtime, tmp_path, files, clean, samples):
    from native_result_report import NativeResultReporter
    app = stage_app(tmp_path, files)
    with release_work_case(runtime, tmp_path, clean_work=clean) as case:
        finish_with_samples(case, runtime, samples)
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        expected = dict(report=report, event=(case.store.get_event(report["eventUid"]) if "eventUid" in report else None),
            occupancy=case.store.get_work_slot(),
            nativeCommands=[[row["command_uid"], row["message_name"]] for row in case.store.list_native_commands()])
        if clean and not samples:
            assert report == {"state": "WAITING_FOR_RESULT_POLICY"}
            assert case.store.list_pending_events() == []
        case.store.close()
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN_REPORT, str(app),
            str(tmp_path / "edge.db"), json.dumps(asdict(case.permit)), case.start["mcuCommandUid"]],
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
from job_safety import JobPermit
store = EdgeStore(sys.argv[2])
try:
    store.initialize()
    permit = JobPermit(**json.loads(sys.argv[3]))
    decision = store.get_native_result_confirmation(permit, sys.argv[4], device_name='device-1')
    for name in ('edge_store', 'onenet_wire', 'native_result_report'):
        assert Path(sys.modules[name].__file__).resolve().parent == app
    print(json.dumps(dict(confirmation=decision, occupancy=store.get_work_slot(),
        nativeCommands=[[row["command_uid"], row["message_name"]] for row in store.list_native_commands()])))
finally:
    store.close()
"""


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean", [False, True])
def test_staged_release_reopens_the_original_backend_decision_without_releasing_work(runtime, tmp_path, files, clean):
    from hardware.tests.test_native_result_confirmation import confirmation_wire
    from native_result_report import NativeResultReporter
    app = stage_app(tmp_path, files)
    with release_work_case(runtime, tmp_path, clean_work=clean) as case:
        finish_with_samples(case, runtime, [700] * 5)
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        _, _, command = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        expected = dict(confirmation=case.store.get_native_result_confirmation(case.permit,
            case.start["mcuCommandUid"], device_name="device-1"), occupancy=case.store.get_work_slot(),
            nativeCommands=[[row["command_uid"], row["message_name"]] for row in case.store.list_native_commands()])
        case.store.close()
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN_CONFIRMATION, str(app),
            str(tmp_path / "edge.db"), json.dumps(asdict(case.permit)), case.start["mcuCommandUid"]],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("files", [BUSINESS_APP_FILES, RUNTIME_APP_FILES], ids=["business", "runtime"])
@pytest.mark.parametrize("clean_work", [False, True], ids=["delivery", "clean"])
def test_staged_release_reconciles_actual_complete_result_and_original_configuration(runtime, tmp_path, files, clean_work):
    app = stage_app(tmp_path, files)
    with release_work_case(runtime, tmp_path, clean_work=clean_work) as case:
        wire = case.wire
        wire.finish_clean() if clean_work else wire.finish_delivery()
        decision = case.store.evaluate_native_work_recovery(case.permit, case.start["mcuCommandUid"], current_boot=lambda: None)
        assert decision["evidence"]["state"] == "MATCHED"
        commands = [[row["command_uid"], row["message_name"]] for row in case.store.list_native_commands()]
        case.store.close()
        result = subprocess.run([sys.executable, "-I", "-c", REOPEN_RESULT, str(app),
            str(tmp_path / "edge.db"), json.dumps(asdict(case.permit)), case.start["mcuCommandUid"]],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        expected = dict(decision=decision, occupancy=case.occupancy, events=[],
            nativeCommands=commands)
        assert json.loads(result.stdout) == json.loads(json.dumps(expected, default=bytes.hex))
