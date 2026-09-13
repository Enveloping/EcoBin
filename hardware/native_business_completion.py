"""Finish an original normal native business after its exact backend decision.

There are only two local steps: freeze one completion receipt in the original
command, then apply it after the permanent job is completed. No mechanical
recovery, new completion event, financial decision, or implicit admission.
"""
from dataclasses import asdict
import json

from job_safety import JobPermit, PermanentJobSafety
from onenet_wire import canonical_payload_sha256
import uart2_protocol as uart


MARKER = "nativeBusinessCompletion"
PROFILE = "ecobin-native-business-completion-v1"
NORMAL_FINISH = {"DELIVERY_END", "DELIVERY_WINDOW_EXPIRED", "CLEAN_CONFIRMED"}
AVAILABLE = {"STABLE_MEAN", "TIMEOUT_MEDIAN"}


def _permit_identity(permit, snapshot):
    expected = dict(permitUid=permit.permit_uid, commandUid=permit.command_uid,
        workUid=permit.work_uid, workType=permit.work_type,
        requestDigestSha256=permit.request_digest_sha256)
    if not isinstance(snapshot, dict) or any(snapshot.get(key) != value for key, value in expected.items()):
        raise ValueError("native completion differs from the original permanent permit")


def _completed_permit(permit, marker, snapshot):
    _permit_identity(permit, snapshot)
    expected = dict(state="COMPLETED", completionUid=permit.command_uid,
        completionOutcome="SUCCEEDED", completionDigestSha256=marker["evidenceSha256"])
    if any(snapshot.get(key) != value for key, value in expected.items()):
        raise ValueError("native completion lacks its exact completed permanent receipt")


def _source(store, conn, permit, start_uid, device_name):
    if store.get_native_delivery_issue(permit.work_uid) is not None:
        return dict(state="ISSUE_ARCHIVED")
    report = store.get_native_result_report(permit, start_uid, device_name=device_name)
    if report is None:
        return dict(state="WAITING_FOR_REPORT")
    task = conn.execute("SELECT * FROM native_result_report_outbox WHERE event_uid=?", (report["eventUid"],)).fetchone()
    binding = store._native_report_binding(conn, task)
    if binding["version"] != "ecobin-native-result-report-v2":
        return dict(state="LEGACY_RESULT_REQUIRES_ITS_ORIGINAL_HANDLER")
    saved = store.get_native_mcu_result(task["mcu_boot_id"], task["result_sequence"])
    result = uart.decode_payload("WORK_RESULT", saved["payload"])
    if result["finishReason"] not in NORMAL_FINISH or any(result[key + "Kind"] not in AVAILABLE for key in ("initial", "final")):
        return dict(state="RESULT_NOT_NORMAL")
    confirmation = store.get_native_result_confirmation(permit, start_uid, device_name=device_name)
    if confirmation is None:
        return dict(state="WAITING_FOR_BACKEND_CONFIRMATION")
    if confirmation["outcome"] != "BUSINESS_APPLIED":
        return dict(state="BACKEND_RESULT_NOT_APPLIED")
    event = json.loads(store.get_event(report["eventUid"])["payload_json"])
    payload = event["payload"]
    baseline = None
    if permit.work_type == "CLEAN":
        # Values and bag identity come from the immutable, revalidated original
        # report, never current configuration/a later bag or a caller argument.
        measurement = payload["cleanerConfirmedFinalMeasurement"]
        if not payload["cleanerCompletionConfirmed"] or payload["newBaselineWeightGrams"] != result["finalWeightGrams"]:
            raise ValueError("native clean completion differs from its final weight")
        baseline = dict(bag_uid=payload["newBagUid"], weight_grams=result["finalWeightGrams"],
            source_kind="NATIVE_CLEAN_POST", source_work_type="CLEAN_OPERATION", source_work_uid=permit.work_uid,
            source_mcu_boot_id=measurement["mcuBootId"], source_mcu_event_sequence=measurement["mcuEventSequence"],
            source_observed_at=None, measurement_uid=measurement["measurementUid"])
    evidence = dict(profile=PROFILE, deviceName=device_name, permit=asdict(permit), startCommandUid=start_uid,
        eventUid=report["eventUid"], eventPayloadSha256=event["payloadSha256"],
        mcuBootId=result["mcuBootId"], resultSequence=result["resultSequence"], resultDigestSha256=result["resultDigestSha256"],
        portNo=result["portNo"], confirmation=confirmation, baseline=baseline)
    return dict(state="QUALIFIED", evidence=evidence)


def _original_slot(store, permit, port_no):
    slot = store.get_work_slot()
    if (slot is None or slot["work_uid"] != permit.work_uid or slot["work_type"] != permit.work_type
            or slot["port_no"] != port_no):
        raise ValueError("native completion original work slot has changed")
    return slot


def _baseline_available(store, baseline):
    if baseline is None:
        return
    current = store.get_bag_baseline(baseline["bag_uid"])
    if current is not None and any(current[key] != value for key, value in baseline.items()):
        raise ValueError("native completion cannot overwrite a conflicting bag baseline")


def _marker(store, permit, evidence):
    command = store.get_command(permit.command_uid)
    if command is None:
        raise ValueError("native completion original cloud command is missing")
    result = {} if command["result"] is None else command["result"]
    if not isinstance(result, dict):
        raise ValueError("native completion command result is malformed")
    old = result.get(MARKER)
    digest = canonical_payload_sha256(evidence)
    if old is not None and (not isinstance(old, dict) or set(old) != {"state", "evidence", "evidenceSha256"}
            or old["state"] not in {"PREPARED", "APPLIED"} or old["evidence"] != evidence
            or old["evidenceSha256"] != digest or command["state"] != "COMPLETED"):
        raise ValueError("native completion original receipt is corrupt or conflicts")
    return command, result, old, digest


def _result(marker):
    evidence = marker["evidence"]
    return dict(state="COMPLETED" if marker["state"] == "APPLIED" else "PREPARED",
        workUid=evidence["permit"]["work_uid"], eventUid=evidence["eventUid"],
        completionUid=evidence["permit"]["command_uid"], evidenceSha256=marker["evidenceSha256"],
        baselineApplied=marker["state"] == "APPLIED" and evidence["baseline"] is not None)


def prepare(store, permit, start_uid, *, device_name, permit_snapshot):
    """Atomic local intent; a completed cloud command still owns its work slot."""
    if not isinstance(permit, JobPermit):
        raise ValueError("native completion requires the original job permit")
    with store._standalone_native_transaction() as conn:
        source = _source(store, conn, permit, start_uid, device_name)
        if source["state"] != "QUALIFIED":
            return source
        evidence = source["evidence"]
        command, result, old, digest = _marker(store, permit, evidence)
        _permit_identity(permit, permit_snapshot)
        if old is not None and old["state"] == "APPLIED":
            _completed_permit(permit, old, permit_snapshot)
            return _result(old)  # Never inspect or overwrite any later bag/slot.
        if permit_snapshot.get("state") != "ACTIVE":
            if old is None:
                raise ValueError("native completion needs its original active permanent permit")
            _completed_permit(permit, old, permit_snapshot)
        slot = _original_slot(store, permit, evidence["portNo"])
        _baseline_available(store, evidence["baseline"])
        if old is not None:
            return _result(old)
        if command["state"] in {"COMPLETED", "FAILED", "REJECTED"}:
            raise ValueError("native completion cannot replace another terminal command result")
        marker = dict(state="PREPARED", evidence=evidence, evidenceSha256=digest)
        result[MARKER] = marker
        context = dict(slot["context"])
        context[MARKER] = dict(eventUid=evidence["eventUid"], startCommandUid=start_uid, evidenceSha256=digest)
        conn.execute("""UPDATE command_inbox SET state='COMPLETED', processed_at=?, processing_started_at=NULL,
            result_json=?, last_error=NULL WHERE command_uid=?""",
            (store._now(), json.dumps(result, ensure_ascii=False, sort_keys=True), permit.command_uid))
        conn.execute("""UPDATE work_slot SET work_state='COMPLETING', context_json=?, updated_at=?
            WHERE slot_id=1 AND work_uid=? AND work_type=? AND port_no=?""",
            (json.dumps(context, ensure_ascii=False, sort_keys=True), store._now(), permit.work_uid,
             permit.work_type, evidence["portNo"]))
        return _result(marker)


def apply(store, permit, start_uid, *, device_name, permit_snapshot):
    """Apply exactly once, only after the permanent job receipt is verified."""
    if not isinstance(permit, JobPermit):
        raise ValueError("native completion requires the original job permit")
    with store._standalone_native_transaction() as conn:
        source = _source(store, conn, permit, start_uid, device_name)
        if source["state"] != "QUALIFIED":
            return source
        evidence = source["evidence"]
        _, result, marker, _ = _marker(store, permit, evidence)
        if marker is None:
            raise ValueError("native completion has not frozen its original receipt")
        _completed_permit(permit, marker, permit_snapshot)
        if marker["state"] == "APPLIED":
            return _result(marker)
        _original_slot(store, permit, evidence["portNo"])
        baseline = evidence["baseline"]
        _baseline_available(store, baseline)
        if baseline is not None and store.get_bag_baseline(baseline["bag_uid"]) is None:
            store._upsert_bag_baseline_in_tx(conn, baseline | dict(updated_at=store._now()))
        marker["state"] = "APPLIED"
        result[MARKER] = marker
        conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=?",
            (json.dumps(result, ensure_ascii=False, sort_keys=True), permit.command_uid))
        released = conn.execute("""UPDATE work_slot SET work_type='NONE', work_uid=NULL, work_state=NULL,
            port_no=NULL, context_json=NULL, updated_at=?
            WHERE slot_id=1 AND work_uid=? AND work_type=? AND port_no=?""",
            (store._now(), permit.work_uid, permit.work_type, evidence["portNo"]))
        if released.rowcount != 1:
            raise ValueError("native completion original work slot changed during application")
        return _result(marker)


class NativeBusinessCompleter:
    def __init__(self, store, safety, *, device_name):
        if not isinstance(safety, PermanentJobSafety) or not safety.enabled:
            raise ValueError("native completion requires permanent job safety")
        if not isinstance(device_name, str) or not device_name:
            raise ValueError("native completion requires the device identity")
        self.store, self.safety, self.device_name = store, safety, device_name

    def complete(self, permit, start_command_uid):
        if not isinstance(permit, JobPermit):
            raise ValueError("native completion requires the original job permit")
        snapshot = self.safety.get_job_permit(permit.permit_uid)
        pending = self.store.prepare_native_business_completion(permit, start_command_uid,
            device_name=self.device_name, permit_snapshot=snapshot)
        if pending["state"] != "PREPARED":
            return pending
        # The immutable original command UUID and evidence digest survive a
        # timeout/crash after the RPC applied. Repeating it cannot complete a
        # different job; no local slot is freed before its exact confirmation.
        self.safety.complete_job(permit, completion_uid=permit.command_uid,
            outcome="SUCCEEDED", completion_digest_sha256=pending["evidenceSha256"])
        snapshot = self.safety.get_job_permit(permit.permit_uid)
        return self.store.apply_native_business_completion(permit, start_command_uid,
            device_name=self.device_name, permit_snapshot=snapshot)
