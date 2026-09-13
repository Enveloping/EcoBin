"""Close one confirmed reboot issue locally, never as a normal business result.

The caller supplies exact permanent-permit snapshots. These two transactions
never call the updater/UART, reconstruct actions, change bags or enable admission.
"""
from dataclasses import asdict
import json

from job_safety import JobPermit
from onenet_wire import canonical_payload_sha256


MARKER = "nativeIssueCompletion"
PROFILE = "ecobin-native-issue-completion-v1"
REASON = "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"


def _source(store, permit, start_uid, device_name):
    if not isinstance(permit, JobPermit) or permit.work_type != "DELIVERY":
        raise ValueError("native issue completion requires the original delivery permit")
    if not isinstance(device_name, str) or not device_name:
        raise ValueError("native issue completion requires the original device")
    issue = store.get_native_delivery_issue(permit.work_uid)
    if issue is None:
        return dict(state="ISSUE_NOT_ARCHIVED")
    if (issue["permit"] != asdict(permit) or issue["startCommandUid"] != start_uid
            or issue["deviceName"] != device_name or issue["reason"] != REASON
            or issue["settlementAllowed"] is not False):
        raise ValueError("native issue completion differs from the original archive")
    reports = store.list_native_delivery_issue_reports(permit.work_uid)
    if not any(row["event_uid"] == issue["issueUid"] and row["evidence_kind"] == "ARCHIVE" for row in reports):
        return dict(state="WAITING_FOR_ISSUE_REPORT")
    confirmation = store.get_native_delivery_issue_confirmation(permit.work_uid, device_name=device_name)
    if confirmation is None:
        return dict(state="WAITING_FOR_BACKEND_CONFIRMATION")
    if confirmation["outcome"] != "BUSINESS_APPLIED":
        return dict(state="BACKEND_ISSUE_NOT_APPLIED")
    event = json.loads(store.get_event(issue["issueUid"])["payload_json"])
    payload = event["payload"]
    evidence = dict(profile=PROFILE, deviceName=device_name, permit=asdict(permit),
        startCommandUid=start_uid, issueUid=issue["issueUid"], reason=REASON, businessValue="NONE",
        eventUid=event["eventUid"], eventPayloadSha256=event["payloadSha256"],
        archiveEvidenceSha256=payload["archiveEvidenceSha256"], portNo=payload["portNo"],
        sourceMcuBootId=issue["sourceMcuBootId"], targetMcuBootId=issue["targetMcuBootId"],
        confirmation=confirmation)
    return dict(state="QUALIFIED", evidence=evidence)


def _permit_identity(permit, snapshot):
    expected = dict(permitUid=permit.permit_uid, commandUid=permit.command_uid,
        workUid=permit.work_uid, workType=permit.work_type, requestDigestSha256=permit.request_digest_sha256)
    if not isinstance(snapshot, dict) or any(snapshot.get(key) != value for key, value in expected.items()):
        raise ValueError("native issue completion differs from the original permanent permit")


def _completed_permit(permit, marker, snapshot):
    _permit_identity(permit, snapshot)
    expected = dict(state="COMPLETED", completionUid=permit.command_uid,
        completionOutcome="CANCELLED", completionDigestSha256=marker["evidenceSha256"])
    if any(snapshot.get(key) != value for key, value in expected.items()):
        raise ValueError("native issue completion lacks its exact cancelled permanent receipt")


def _original_slot(store, permit, port_no):
    slot = store.get_work_slot()
    if (slot is None or (slot["work_uid"], slot["work_type"], slot["port_no"])
            != (permit.work_uid, permit.work_type, port_no)):
        raise ValueError("native issue completion original work slot has changed")
    return slot


def _slot_receipt(marker):
    evidence = marker["evidence"]
    return dict(issueUid=evidence["issueUid"], startCommandUid=evidence["startCommandUid"],
        evidenceSha256=marker["evidenceSha256"])


def _check_slot_receipt(slot, marker):
    if slot["work_state"] != "COMPLETING" or slot["context"].get(MARKER) != _slot_receipt(marker):
        raise ValueError("native issue completion original slot receipt has changed")


def _marker(store, permit, evidence):
    command = store.get_command(permit.command_uid)
    if command is None:
        raise ValueError("native issue completion original cloud command is missing")
    result = {} if command["result"] is None else command["result"]
    if not isinstance(result, dict):
        raise ValueError("native issue completion command result is malformed")
    old = result.get(MARKER)
    digest = canonical_payload_sha256(evidence)
    if MARKER in result and (not isinstance(old, dict) or set(old) != {"state", "evidence", "evidenceSha256"}
            or old["state"] not in {"PREPARED", "APPLIED"} or old["evidence"] != evidence
            or old["evidenceSha256"] != digest or command["state"] != "FAILED"
            or command["last_error"] != REASON):
        raise ValueError("native issue completion receipt is corrupt or conflicts")
    return command, result, old, digest


def _result(marker):
    evidence = marker["evidence"]
    return dict(state="COMPLETED" if marker["state"] == "APPLIED" else "PREPARED",
        workUid=evidence["permit"]["work_uid"], issueUid=evidence["issueUid"], eventUid=evidence["eventUid"],
        completionUid=evidence["permit"]["command_uid"], completionOutcome="CANCELLED",
        evidenceSha256=marker["evidenceSha256"])


def prepare(store, permit, start_command_uid, *, device_name, permit_snapshot):
    """Freeze one cancelled receipt; keep the original local slot occupied."""
    with store._standalone_native_transaction() as conn:
        source = _source(store, permit, start_command_uid, device_name)
        if source["state"] != "QUALIFIED":
            return source
        evidence = source["evidence"]
        command, result, old, digest = _marker(store, permit, evidence)
        _permit_identity(permit, permit_snapshot)
        if old is not None and old["state"] == "APPLIED":
            _completed_permit(permit, old, permit_snapshot)
            return _result(old)
        if permit_snapshot.get("state") != "ACTIVE":
            if old is None:
                raise ValueError("native issue completion needs its original active permanent permit")
            _completed_permit(permit, old, permit_snapshot)
        slot = _original_slot(store, permit, evidence["portNo"])
        if old is not None:
            _check_slot_receipt(slot, old)
            return _result(old)
        if MARKER in slot["context"]:
            raise ValueError("native issue completion has an orphan original slot receipt")
        if command["state"] in {"COMPLETED", "FAILED", "REJECTED"}:
            raise ValueError("native issue completion cannot replace another terminal command result")
        marker = dict(state="PREPARED", evidence=evidence, evidenceSha256=digest)
        result[MARKER] = marker
        context = dict(slot["context"])
        context[MARKER] = _slot_receipt(marker)
        updated = conn.execute("""UPDATE command_inbox SET state='FAILED', processed_at=?,
            processing_started_at=NULL, result_json=?, last_error=? WHERE command_uid=? AND state=?""",
            (store._now(), json.dumps(result, ensure_ascii=False, sort_keys=True), REASON,
             permit.command_uid, command["state"]))
        if updated.rowcount != 1:
            raise ValueError("native issue completion original command changed")
        updated = conn.execute("""UPDATE work_slot SET work_state='COMPLETING', context_json=?, updated_at=?
            WHERE slot_id=1 AND work_uid=? AND work_type=? AND port_no=?""",
            (json.dumps(context, ensure_ascii=False, sort_keys=True), store._now(),
             permit.work_uid, permit.work_type, evidence["portNo"]))
        if updated.rowcount != 1:
            raise ValueError("native issue completion original work slot changed")
        return _result(marker)


def apply(store, permit, start_command_uid, *, device_name, permit_snapshot):
    """Release only the original slot, after its exact CANCELLED receipt."""
    with store._standalone_native_transaction() as conn:
        source = _source(store, permit, start_command_uid, device_name)
        if source["state"] != "QUALIFIED":
            return source
        evidence = source["evidence"]
        _, result, marker, _ = _marker(store, permit, evidence)
        if marker is None:
            raise ValueError("native issue completion has not frozen its original receipt")
        _completed_permit(permit, marker, permit_snapshot)
        if marker["state"] == "APPLIED":
            return _result(marker)
        _check_slot_receipt(_original_slot(store, permit, evidence["portNo"]), marker)
        marker["state"] = "APPLIED"
        updated = conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=? AND state='FAILED'",
            (json.dumps(result, ensure_ascii=False, sort_keys=True), permit.command_uid))
        if updated.rowcount != 1:
            raise ValueError("native issue completion original command changed")
        released = conn.execute("""UPDATE work_slot SET work_type='NONE', work_uid=NULL, work_state=NULL,
            port_no=NULL, context_json=NULL, updated_at=?
            WHERE slot_id=1 AND work_uid=? AND work_type=? AND port_no=?""",
            (store._now(), permit.work_uid, permit.work_type, evidence["portNo"]))
        if released.rowcount != 1:
            raise ValueError("native issue completion original work slot changed")
        return _result(marker)
