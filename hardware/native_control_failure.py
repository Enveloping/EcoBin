"""Durably stop one native business after control loss or an explicit failed result.

The local receipt is frozen before any permanent-ledger mutation.  A process
restart therefore continues the same failure disposition instead of reviving
the old business or reporting a late result as a normal completion.
"""
from dataclasses import asdict
import json

from job_safety import JobPermit, command_request_digest
from onenet_wire import canonical_payload_sha256
from work_recovery import complete_result
import uart2_protocol as uart


MARKER = "nativeControlFailure"
PROFILE = "ecobin-native-control-failure-v1"
RESULT_PROFILE = "ecobin-native-result-failure-v1"
COMMUNICATION_REASON = "MCU_COMMUNICATION_UNAVAILABLE"
RESTART_REASON = "EDGE_RESTARTED_BEFORE_START"
MCU_RESTART_REASON = "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
BASELINE_RESULT_REASON = "MCU_BASELINE_RESULT_UNAVAILABLE"
RESULT_REASONS = {
    "MCU_INITIAL_WEIGHT_UNAVAILABLE",
    "MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE",
    "MCU_WORK_CANCELLED",
    "MCU_WORK_FAILED",
}
REASONS = {
    COMMUNICATION_REASON,
    RESTART_REASON,
    MCU_RESTART_REASON,
    BASELINE_RESULT_REASON,
    "UART_SHORT_WRITE",
    "UART_WRITE_FAILED",
}
STAGES = {"PRE_START_FAILED", "FAILED", "REJECTED"}
INTERLOCK_PROFILE = "native-clean-bag-interlock-v1"


def _rejection_reasons():
    """Use only stable rejection symbols carried by the UART contract."""
    return set(uart.REGISTRY["enums"]["NackError"]["values"]) - {"NONE"}


def _original(store, permit, start_uid, device_name):
    if not isinstance(permit, JobPermit) or permit.work_type not in {
        "DELIVERY",
        "CLEAN",
        "BASELINE",
    }:
        raise ValueError("native control failure requires an original business permit")
    if not isinstance(device_name, str) or not device_name:
        raise ValueError("native control failure requires the original device")
    record = store.get_native_command(start_uid)
    expected_name = {
        "CLEAN": "START_CLEAN_OPERATION",
        "DELIVERY": "START_DELIVERY_SESSION",
        "BASELINE": "MEASURE_BASELINE",
    }[permit.work_type]
    expected_cloud_name = {
        "CLEAN": "START_CLEAN_OPERATION",
        "DELIVERY": "START_DELIVERY_SESSION",
        "BASELINE": "MEASURE_EMPTY_BAG_BASELINE",
    }[permit.work_type]
    if record is None or record["message_name"] != expected_name or record["conflict"]:
        raise ValueError("native control failure lost its original START")
    start = uart.decode_payload(expected_name, record["payload"])
    work_key = {
        "CLEAN": "operationUid",
        "DELIVERY": "sessionUid",
        "BASELINE": "measurementUid",
    }[permit.work_type]
    command = store.get_command(permit.command_uid)
    if (command is None or command["payload"].get("targetDeviceName") != device_name
            or command["payload"].get("commandUid") != permit.command_uid
            or command["payload"].get("commandType") != expected_cloud_name
            or command["payload"].get("payload", {}).get(work_key) != permit.work_uid
            or command_request_digest(command["payload"]) != permit.request_digest_sha256
            or start[work_key] != permit.work_uid):
        raise ValueError("native control failure differs from its original cloud authority")
    return record, start, command


def _baseline_result_saved(store, record):
    """Let an exact committed measurement beat a later technical timeout."""
    if record["message_name"] != "MEASURE_BASELINE":
        return False
    values = uart.decode_payload("MEASURE_BASELINE", record["payload"])
    scope = dict(
        mcuCommandUid=values["mcuCommandUid"],
        commandDigestSha256=values["commandDigestSha256"],
        targetMcuBootId=values["targetMcuBootId"],
        commandSequence=values["commandSequence"],
        workUid=values["measurementUid"],
        workType="BASELINE_MEASUREMENT",
        portNo=values["portNo"],
        eventMessageType="BASELINE_MEASUREMENT_RESULT",
        stepSequence=0,
        configVersion=values["configVersion"],
    )
    raw = uart.encode_payload("QUERY_PROCESS_EVENT", scope | {"queryId": 1})[8:]
    return store.get_native_process_receipt(raw) is not None


def _slot(store, permit, port_no):
    slot = store.get_work_slot()
    if (slot is None or (slot["work_uid"], slot["work_type"], slot["port_no"])
            != (permit.work_uid, permit.work_type, port_no)):
        raise ValueError("native control failure original work slot has changed")
    return slot


def _expected_evidence(permit, record, start, device_name, stage, reason, event):
    identity = uart.decode_payload(record["message_name"], record["payload"])
    return dict(profile=PROFILE, deviceName=device_name, permit=asdict(permit),
        startCommandUid=record["command_uid"], startMessageName=record["message_name"],
        sourceMcuBootId=record["mcu_boot_id"], sourceCommandSequence=record["command_sequence"],
        sourceCommandDigestSha256=identity["commandDigestSha256"], portNo=start["portNo"],
        writeClaimed=bool(record["write_claimed"]), stage=stage, reason=reason,
        businessValue="NONE", observationEventUid=event["eventUid"],
        observationPayloadSha256=event["payloadSha256"])


def _result_failure_source(store, conn, permit, record, start):
    decision = complete_result(store, conn, permit, record, start)
    if decision is None:
        return dict(state="WAITING_FOR_COMPLETE_RESULT")
    if decision["evidence"]["state"] != "MATCHED":
        return dict(state=decision["evidence"]["state"])
    value = uart.decode_payload("WORK_RESULT", decision["result"]["payload"])
    from native_result_evidence import terminal_failure_disposition
    disposition = terminal_failure_disposition(value)
    if disposition is None:
        return dict(state="OTHER_TERMINAL_PATH")
    return dict(state="QUALIFIED", decision=decision, value=value,
        disposition=disposition)


def _expected_result_evidence(permit, record, start, device_name, source, event):
    value, disposition = source["value"], source["disposition"]
    task = source["decision"]["task"]
    return dict(profile=RESULT_PROFILE, deviceName=device_name, permit=asdict(permit),
        startCommandUid=record["command_uid"], startMessageName=record["message_name"],
        sourceMcuBootId=record["mcu_boot_id"], sourceCommandSequence=record["command_sequence"],
        sourceCommandDigestSha256=start["commandDigestSha256"], portNo=start["portNo"],
        writeClaimed=True, stage="FAILED", reason=disposition["reason"],
        businessValue="NONE", resultTaskUid=task["task_uid"],
        resultMcuBootId=value["mcuBootId"], resultSequence=value["resultSequence"],
        resultDigestSha256=value["resultDigestSha256"], finishReason=value["finishReason"],
        failureKind=disposition["kind"], initialKind=value["initialKind"],
        initialFaultCode=disposition["initialFaultCode"], finalKind=value["finalKind"],
        finalFaultCode=disposition["finalFaultCode"],
        cleanBagInterlockRequired=disposition["cleanBagInterlockRequired"],
        observationEventUid=event["eventUid"], observationPayloadSha256=event["payloadSha256"])


def _event(store, command_uid, start_uid, message_name, stage, reason):
    row = store._conn.execute("""SELECT event_uid FROM command_observation
        WHERE command_uid=? AND stage=? AND error_code=?""", (command_uid, stage, reason)).fetchone()
    if row is None:
        raise ValueError("native control failure lacks its command observation")
    event_row = store.get_event(row["event_uid"])
    if event_row is None:
        raise ValueError("native control failure observation event disappeared")
    event = json.loads(event_row["payload_json"])
    expected_payload = dict(
        observedCommandType=(
            "MEASURE_EMPTY_BAG_BASELINE"
            if message_name == "MEASURE_BASELINE"
            else message_name
        ),
        stage=stage,
        mcuCommandUid=None if stage == "REJECTED" else start_uid,
        errorCode=reason,
    )
    if (event.get("eventType") != "DEVICE_COMMAND_OBSERVED"
            or event.get("commandUid") != command_uid or event.get("payload") != expected_payload):
        raise ValueError("native control failure observation differs from its original command")
    return event


def _checked_marker(store, permit, record, start, command, device_name):
    result = {} if command["result"] is None else command["result"]
    if not isinstance(result, dict):
        raise ValueError("native control failure command result is malformed")
    marker = result.get(MARKER)
    if marker is None:
        return result, None
    if (not isinstance(marker, dict) or set(marker) != {"state", "evidence", "evidenceSha256"}
            or marker.get("state") not in {"PREPARED", "APPLIED"} or not isinstance(marker.get("evidence"), dict)):
        raise ValueError("native control failure receipt is malformed")
    evidence = marker["evidence"]
    stage, reason = evidence.get("stage"), evidence.get("reason")
    profile = evidence.get("profile")
    if profile == PROFILE:
        if stage not in STAGES or (
            reason not in REASONS
            and not (stage == "REJECTED" and reason in _rejection_reasons())
        ):
            raise ValueError("native control failure receipt has an unsupported disposition")
    elif profile == RESULT_PROFILE:
        if stage != "FAILED" or reason not in RESULT_REASONS:
            raise ValueError("native result failure receipt has an unsupported disposition")
    else:
        raise ValueError("native control failure receipt has an unsupported profile")
    event = _event(store, permit.command_uid, record["command_uid"], record["message_name"], stage, reason)
    if profile == RESULT_PROFILE:
        source = _result_failure_source(store, store._conn, permit, record, start)
        if source["state"] != "QUALIFIED":
            raise ValueError("native result failure lost its complete result")
        expected = _expected_result_evidence(permit, record, start, device_name, source, event)
    else:
        expected = _expected_evidence(permit, record, start, device_name, stage, reason, event)
    if (evidence != expected or marker.get("evidenceSha256") != canonical_payload_sha256(expected)
            or command["state"] != ("REJECTED" if stage == "REJECTED" else "FAILED")
            or command["last_error"] != reason
            or bool(record["write_claimed"])
            != (stage in {"FAILED", "REJECTED"})
            or (reason == RESTART_REASON and stage != "PRE_START_FAILED")
            or (reason == MCU_RESTART_REASON and stage != "FAILED")
            or (stage == "REJECTED" and (
                record["decision_outcome"] != "REJECTED"
                or record["decision_error"] != reason
            ))):
        raise ValueError("native control failure receipt conflicts with its original work")
    return result, marker


def _slot_receipt(marker):
    evidence = marker["evidence"]
    return dict(startCommandUid=evidence["startCommandUid"], stage=evidence["stage"],
        reason=evidence["reason"], evidenceSha256=marker["evidenceSha256"])


def _clean_bag_interlock(permit, start, command):
    """Keep only identities proven by the original cloud and MCU STARTs."""
    cloud = command["payload"]
    payload = cloud["payload"]
    metadata = dict(
        profile=INTERLOCK_PROFILE,
        sourceWorkUid=payload["operationUid"],
        portNo=start["portNo"],
        oldBagUid=payload.get("oldBagUid"),
        newBagUid=payload["newBagUid"],
        sourceCommandUid=cloud["commandUid"],
    )
    # A first bag installation has no old bag. Preserve that authoritative
    # null instead of inventing an identity or dropping the field.
    if (
        metadata["sourceWorkUid"] != permit.work_uid
        or metadata["sourceCommandUid"] != permit.command_uid
        or metadata["portNo"] != payload["portNo"]
        or not isinstance(metadata["newBagUid"], str)
        or not metadata["newBagUid"]
        or (
            metadata["oldBagUid"] is not None
            and (
                not isinstance(metadata["oldBagUid"], str)
                or not metadata["oldBagUid"]
            )
        )
    ):
        raise ValueError(
            "native clean bag interlock differs from original authority"
        )
    return metadata


def _check_slot_receipt(slot, marker):
    if slot["work_state"] != "COMPLETING" or slot["context"].get(MARKER) != _slot_receipt(marker):
        raise ValueError("native control failure original slot receipt has changed")


def _result(marker):
    evidence = marker["evidence"]
    return dict(state="COMPLETED" if marker["state"] == "APPLIED" else "PREPARED",
        workUid=evidence["permit"]["work_uid"], startCommandUid=evidence["startCommandUid"],
        eventUid=evidence["observationEventUid"], stage=evidence["stage"], reason=evidence["reason"],
        completionUid=evidence["permit"]["command_uid"], completionOutcome="FAILED",
        evidenceSha256=marker["evidenceSha256"], writeClaimed=evidence["writeClaimed"])


def prepare(store, permit, start_uid, *, device_name, stage, reason):
    """Freeze the failure before touching the permanent ledger or releasing occupancy."""
    if stage not in STAGES or (
        reason not in REASONS
        and not (stage == "REJECTED" and reason in _rejection_reasons())
    ):
        raise ValueError("native control failure stage or reason is unsupported")
    with store._standalone_native_transaction() as conn:
        record, start, command = _original(store, permit, start_uid, device_name)
        result, marker = _checked_marker(store, permit, record, start, command, device_name)
        if marker is not None:
            _check_slot_receipt(_slot(store, permit, start["portNo"]), marker)
            return _result(marker)
        if permit.work_type == "BASELINE" and _baseline_result_saved(store, record):
            return dict(state="OTHER_TERMINAL_PATH")
        if permit.work_type == "DELIVERY" and store.get_native_delivery_issue(permit.work_uid) is not None:
            return dict(state="OTHER_TERMINAL_PATH")
        if permit.work_type in {"DELIVERY", "CLEAN"}:
            completed = complete_result(store, conn, permit, record, start)
            if completed is not None:
                return completed
        expected_stage = (
            "REJECTED"
            if record["decision_outcome"] == "REJECTED"
            else "FAILED" if record["write_claimed"] else "PRE_START_FAILED"
        )
        if (stage != expected_stage
                or (reason == RESTART_REASON and record["write_claimed"])
                or (reason == MCU_RESTART_REASON
                    and (permit.work_type not in {"CLEAN", "BASELINE"}
                         or not record["write_claimed"]))
                or (stage == "REJECTED" and record["decision_error"] != reason)):
            raise ValueError("native control failure stage does not match the durable write fence")
        if command["state"] in {"COMPLETED", "FAILED", "REJECTED"}:
            raise ValueError("native control failure cannot replace another terminal command result")
        observation = store._record_command_observation_in_tx(
            conn,
            command["payload"],
            stage,
            mcu_command_uid=None if stage == "REJECTED" else start_uid,
            error_code=reason,
        )
        if observation == "CONFLICT":
            raise ValueError("native control failure command observation conflicts")
        event = _event(store, permit.command_uid, record["command_uid"], record["message_name"], stage, reason)
        evidence = _expected_evidence(permit, record, start, device_name, stage, reason, event)
        marker = dict(state="PREPARED", evidence=evidence, evidenceSha256=canonical_payload_sha256(evidence))
        result[MARKER] = marker
        slot = _slot(store, permit, start["portNo"])
        if MARKER in slot["context"]:
            raise ValueError("native control failure has an orphan slot receipt")
        context = dict(slot["context"])
        context[MARKER] = _slot_receipt(marker)
        terminal_state = "REJECTED" if stage == "REJECTED" else "FAILED"
        updated = conn.execute("""UPDATE command_inbox SET state=?, processed_at=?,
            processing_started_at=NULL, result_json=?, last_error=? WHERE command_uid=? AND state=?""",
            (terminal_state, store._now(), json.dumps(result, ensure_ascii=False, sort_keys=True), reason,
             permit.command_uid, command["state"]))
        if updated.rowcount != 1:
            raise ValueError("native control failure original command changed")
        updated = conn.execute("""UPDATE work_slot SET work_state='COMPLETING', context_json=?, updated_at=?
            WHERE slot_id=1 AND work_uid=? AND work_type=? AND port_no=?""",
            (json.dumps(context, ensure_ascii=False, sort_keys=True), store._now(),
             permit.work_uid, permit.work_type, start["portNo"]))
        if updated.rowcount != 1:
            raise ValueError("native control failure original slot changed")
        if permit.work_type == "CLEAN" and stage == "FAILED":
            # A clean START may already have changed the physical bag.  The
            # original work can be closed, but admitting another business
            # before a human confirms bag/tare would project the old bag onto
            # an unknown physical state.  Keep this in the same transaction as
            # the frozen failure receipt so a crash cannot release an unlocked
            # clean interruption.
            store._set_clean_restart_interlock_in_tx(
                conn,
                start["portNo"],
                True,
                metadata=_clean_bag_interlock(
                    permit,
                    start,
                    command,
                ),
            )
        return _result(marker)


def prepare_result(store, permit, start_uid, *, device_name):
    """Freeze one complete MCU FAILED/CANCELLED packet as a value-free failure."""
    with store._standalone_native_transaction() as conn:
        record, start, command = _original(store, permit, start_uid, device_name)
        result, marker = _checked_marker(store, permit, record, start, command, device_name)
        if marker is not None:
            _check_slot_receipt(_slot(store, permit, start["portNo"]), marker)
            return _result(marker)
        source = _result_failure_source(store, conn, permit, record, start)
        if source["state"] != "QUALIFIED":
            return {"state": source["state"]}
        disposition = source["disposition"]
        reason = disposition["reason"]
        if command["state"] in {"COMPLETED", "FAILED", "REJECTED"}:
            raise ValueError("native result failure cannot replace another terminal command result")
        observation = store._record_command_observation_in_tx(
            conn,
            command["payload"],
            "FAILED",
            mcu_command_uid=start_uid,
            error_code=reason,
        )
        if observation == "CONFLICT":
            raise ValueError("native result failure command observation conflicts")
        event = _event(store, permit.command_uid, record["command_uid"],
            record["message_name"], "FAILED", reason)
        evidence = _expected_result_evidence(permit, record, start, device_name, source, event)
        marker = dict(state="PREPARED", evidence=evidence,
            evidenceSha256=canonical_payload_sha256(evidence))
        result[MARKER] = marker
        slot = _slot(store, permit, start["portNo"])
        if MARKER in slot["context"]:
            raise ValueError("native result failure has an orphan slot receipt")
        context = dict(slot["context"])
        context[MARKER] = _slot_receipt(marker)
        updated = conn.execute("""UPDATE command_inbox SET state='FAILED', processed_at=?,
            processing_started_at=NULL, result_json=?, last_error=? WHERE command_uid=? AND state=?""",
            (store._now(), json.dumps(result, ensure_ascii=False, sort_keys=True), reason,
             permit.command_uid, command["state"]))
        if updated.rowcount != 1:
            raise ValueError("native result failure original command changed")
        updated = conn.execute("""UPDATE work_slot SET work_state='COMPLETING', context_json=?, updated_at=?
            WHERE slot_id=1 AND work_uid=? AND work_type=? AND port_no=?""",
            (json.dumps(context, ensure_ascii=False, sort_keys=True), store._now(),
             permit.work_uid, permit.work_type, start["portNo"]))
        if updated.rowcount != 1:
            raise ValueError("native result failure original slot changed")
        if permit.work_type == "CLEAN" and disposition["cleanBagInterlockRequired"]:
            store._set_clean_restart_interlock_in_tx(conn, start["portNo"], True,
                metadata=_clean_bag_interlock(permit, start, command))
        return _result(marker)


def _permit_identity(permit, snapshot):
    expected = dict(permitUid=permit.permit_uid, commandUid=permit.command_uid,
        workUid=permit.work_uid, workType=permit.work_type,
        requestDigestSha256=permit.request_digest_sha256)
    return isinstance(snapshot, dict) and all(snapshot.get(key) == value for key, value in expected.items())


def _terminal_permit(permit, marker, snapshot):
    evidence = marker["evidence"]
    if snapshot == {"state": "NOT_FOUND", "permitUid": permit.permit_uid}:
        if evidence["writeClaimed"]:
            raise ValueError("a write-claimed START cannot lack its permanent permit")
        return
    if not _permit_identity(permit, snapshot):
        raise ValueError("native control failure differs from its permanent permit")
    if snapshot.get("state") == "ABANDONED":
        if (evidence["writeClaimed"] or snapshot.get("dispositionUid") != permit.command_uid
                or snapshot.get("abandonEvidenceSha256") != marker["evidenceSha256"]):
            raise ValueError("native control failure lacks its exact abandoned permit receipt")
        return
    expected = dict(state="COMPLETED", completionUid=permit.command_uid,
        completionOutcome="FAILED", completionDigestSha256=marker["evidenceSha256"])
    if any(snapshot.get(key) != value for key, value in expected.items()):
        raise ValueError("native control failure lacks its exact failed permit receipt")


def apply(store, permit, start_uid, *, device_name, permit_snapshot):
    """Release only the original slot after exact permanent disposition readback."""
    with store._standalone_native_transaction() as conn:
        record, start, command = _original(store, permit, start_uid, device_name)
        result, marker = _checked_marker(store, permit, record, start, command, device_name)
        if marker is None:
            raise ValueError("native control failure has not frozen its original receipt")
        _terminal_permit(permit, marker, permit_snapshot)
        if marker["state"] == "APPLIED":
            return _result(marker)
        _check_slot_receipt(_slot(store, permit, start["portNo"]), marker)
        marker["state"] = "APPLIED"
        terminal_state = (
            "REJECTED"
            if marker["evidence"]["stage"] == "REJECTED"
            else "FAILED"
        )
        updated = conn.execute(
            "UPDATE command_inbox SET result_json=? WHERE command_uid=? AND state=?",
            (
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                permit.command_uid,
                terminal_state,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("native control failure original command changed")
        retired = conn.execute(
            """UPDATE native_mcu_command SET dispatch_retired=1
               WHERE command_uid=? AND dispatch_retired=0""",
            (start_uid,),
        )
        if retired.rowcount != 1:
            raise ValueError(
                "native control failure dispatch fence changed"
            )
        released = conn.execute("""UPDATE work_slot SET work_type='NONE', work_uid=NULL, work_state=NULL,
            port_no=NULL, context_json=NULL, updated_at=?
            WHERE slot_id=1 AND work_uid=? AND work_type=? AND port_no=?""",
            (store._now(), permit.work_uid, permit.work_type, start["portNo"]))
        if released.rowcount != 1:
            raise ValueError("native control failure original slot changed during application")
        return _result(marker)
