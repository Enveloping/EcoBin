"""Original native result -> durable business report; no serial or admission.

An explicit candidate entry point, not a legacy UART event adapter. Preparing a
report does not release occupancy, complete the permanent job or apply a bag.
"""
from dataclasses import asdict
import json
import uuid

from job_safety import JobPermit, PermanentJobSafety, command_request_digest
from mcu_action_evidence import NativeActionReconciler, check_ledger
from onenet_wire import canonical_payload_sha256, WORK_PHOTO_SLOTS
from native_result_evidence import terminal_weight_failure_candidate
import uart2_protocol as uart


def supports_result_policy(result):
    return (result["finishReason"] in {"DELIVERY_END", "DELIVERY_WINDOW_EXPIRED", "CLEAN_CONFIRMED"}
        or terminal_weight_failure_candidate(result))


def original_authority(store, permit, start, evidence, device_name):
    row = store.get_command(permit.command_uid)
    if row is None:
        return None
    command = row["payload"]
    stable = {key: value for key, value in command.items() if key != "cosGrant"}
    work_type = "DELIVERY_SESSION" if permit.work_type == "DELIVERY" else "CLEAN_OPERATION"
    name = "START_" + work_type
    payload = command["payload"]
    def integer(value, minimum, maximum):
        return type(value) is int and minimum <= value <= maximum
    def uuid4(value):
        try:
            return isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4
        except ValueError:
            return False
    if command.get("schemaVersion") != 2 or command.get("payloadSchemaVersion") != 2:
        raise ValueError("native report requires the original cloud schema")
    if permit.work_type == "DELIVERY":
        fields = {"sessionUid", "portNo", "bagUid", "config", "unitPriceTenThousandths",
            "continueDeliveryWaitMs", "negativeWeightThresholdGrams", "deliveryAutoCloseMs"}
        valid = (uuid4(payload.get("bagUid")) and integer(payload.get("unitPriceTenThousandths"), 1, 4294967295))
        for field in ("continueDeliveryWaitMs", "deliveryAutoCloseMs", "negativeWeightThresholdGrams"):
            valid = valid and integer(payload.get(field), 0, 4294967295)
    else:
        fields = {"operationUid", "portNo", "oldBagUid", "oldBaselineWeightGrams", "newBagUid",
            "config", "operationWindowMs", "recoveryGeneration"}
        valid = (uuid4(payload.get("newBagUid"))
            and (payload.get("oldBagUid") is None or uuid4(payload["oldBagUid"]))
            and payload.get("oldBagUid") != payload.get("newBagUid")
            and (payload.get("oldBaselineWeightGrams") is None or integer(payload["oldBaselineWeightGrams"], -2147483648, 2147483647))
            and integer(payload.get("operationWindowMs"), 1, 4294967295)
            and type(payload.get("recoveryGeneration")) is int and payload["recoveryGeneration"] == 0)
    if set(payload) != fields or not valid or not integer(payload.get("portNo"), 1, 6):
        raise ValueError("native report original cloud payload is malformed")
    if (row["command_uid"] != permit.command_uid or row["command_type"] != name
            or command["commandUid"] != permit.command_uid or command["commandType"] != name
            or command["targetDeviceName"] != device_name
            or command["target"] != dict(type=work_type, uid=permit.work_uid)
            or command_request_digest(command) != permit.request_digest_sha256
            or canonical_payload_sha256(stable) != row["canonical_sha256"]
            or canonical_payload_sha256(payload) != command["payloadSha256"]):
        raise ValueError("native report differs from original cloud authority")
    config = evidence["configuration"]
    expected_config = dict(version=config["configVersion"], contentSha256=config["contentSha256"],
        mcuPayloadSha256=config["mcuPayloadSha256"])
    key = "sessionUid" if permit.work_type == "DELIVERY" else "operationUid"
    if payload[key] != permit.work_uid or payload["portNo"] != start["portNo"] or payload["config"] != expected_config:
        raise ValueError("native report cloud context differs from original START/configuration")
    if permit.work_type == "DELIVERY":
        for field in ("deliveryAutoCloseMs", "continueDeliveryWaitMs", "negativeWeightThresholdGrams"):
            if payload[field] != start[field]:
                raise ValueError("native report START differs from original cloud policy")
    elif payload["operationWindowMs"] != start["operationWindowMs"] or payload["recoveryGeneration"] != 0:
        raise ValueError("native report differs from original clean policy")
    return payload


def measurement(source):
    value = uart.decode_payload(source["messageName"], source["payload"])
    kind = value["measurementKind"]
    if kind == "UNAVAILABLE" and value["faultCode"] == "WEIGHT_TIMEOUT":
        return dict(measurementUid=value["measurementUid"], status="TIMEOUT", weightValueAvailable=False,
            reportedWeightGrams=None, weightValueKind="NONE", measurementElapsedMs=value["measurementElapsedMs"],
            sampleCount=value["sampleCount"], calibrationVersion=value["calibrationVersion"], sensorHealth="TIMEOUT",
            faultCode=value["faultCode"], mcuBootId=value["mcuBootId"], mcuEventSequence=value["mcuEventSequence"])
    if kind not in {"STABLE_MEAN", "TIMEOUT_MEDIAN"}:
        raise ValueError("native report measurement requires its explicit result policy")
    return dict(measurementUid=value["measurementUid"], status="STABLE" if kind == "STABLE_MEAN" else "UNSTABLE",
        weightValueAvailable=True, reportedWeightGrams=value["reportedWeightGrams"],
        weightValueKind="STABLE_WINDOW_MEAN" if kind == "STABLE_MEAN" else "TIMEOUT_MEDIAN",
        measurementElapsedMs=value["measurementElapsedMs"], sampleCount=value["sampleCount"],
        calibrationVersion=value["calibrationVersion"], sensorHealth="OK", faultCode=None,
        mcuBootId=value["mcuBootId"], mcuEventSequence=value["mcuEventSequence"])


def pending_photos(work_type):
    return [dict(slot=slot, status="UPLOAD_PENDING", photoUid=None, url=None, sha256=None,
        sizeBytes=None, capturedAt=None, missingReason="PHOTO_METADATA_PENDING") for slot in WORK_PHOTO_SLOTS[work_type]]


def report_payload(result, evidence, command, photos):
    first, final = measurement(evidence["initial"]), measurement(evidence["final"])
    close = uart.decode_payload(evidence["execution"]["events"][-1]["message_name"],
        evidence["execution"]["events"][-1]["payload"])
    if result["workType"] == "CLEAN_OPERATION":
        if not result["physicalCloseConfirmed"] or close["lockPowerState"] != "DEENERGIZED":
            raise ValueError("native clean report requires lock-off and original manual confirmation")
        return dict(operationUid=result["workUid"], portNo=result["portNo"], oldBagUid=command["oldBagUid"],
            newBagUid=command["newBagUid"], preUnlockMeasurement=first, cleanerConfirmedFinalMeasurement=final,
            removedNetWeightGrams=(first["reportedWeightGrams"] - final["reportedWeightGrams"]
                if first["weightValueAvailable"] and final["weightValueAvailable"] else None),
            newBaselineWeightGrams=final["reportedWeightGrams"], cleanerCompletionConfirmed=True,
            cleanActionSequence=result["cleanActionSequence"],
            cleanLockAndManualDoorConfirmation=dict(lockPowerState=close["lockPowerState"],
                solenoidHealth=close["solenoidHealth"], physicalDoorStateBasis="CLEANER_CONFIRMATION",
                cleanerPhysicalCloseConfirmed=True), frozenConfig=command["config"], photos=photos)
    return dict(sessionUid=result["workUid"], portNo=result["portNo"], firstPreOpenMeasurement=first,
        finalPostCloseMeasurement=final, deliveryNetWeightGrams=(final["reportedWeightGrams"] - first["reportedWeightGrams"]
            if first["weightValueAvailable"] and final["weightValueAvailable"] else None),
        finalDoorCommand=dict(command=close["command"], outputStatus=close["outputStatus"],
            physicalStateBasis=close["physicalDoorStateBasis"]),
        completionReason=("TERMINAL_WEIGHT_FAILURE" if terminal_weight_failure_candidate(result)
            else "USER_ENDED" if result["finishReason"] == "DELIVERY_END" else "SELECTION_WINDOW_EXPIRED"),
        manualReviewRequired=False, negativeWeightAnomaly=result["negativeWeightAnomaly"],
        frozenConfig=command["config"], unitPriceTenThousandths=command["unitPriceTenThousandths"], photos=photos)


def report_binding(permit, start_uid, result, event, device_name):
    return dict(version="ecobin-native-result-report-v1", permit=asdict(permit), startCommandUid=start_uid,
        resultDigest=result["result_digest"], eventUid=event["eventUid"],
        eventSha256=canonical_payload_sha256(event), deviceName=device_name)


def checked_report(store, conn, row, result, event):
    if row["state"] != "REPORT_CREATED":
        return None
    binding = json.loads(row["report_json"])
    if (result is None or binding.get("version") != "ecobin-native-result-report-v1"
            or binding["resultDigest"] != result["result_digest"] or row["event_uid"] != binding["eventUid"]
            or row["task_uid"] != row["event_uid"]
            or event is None or binding["eventSha256"] != canonical_payload_sha256(event)
            or event["payloadSha256"] != canonical_payload_sha256(event["payload"])):
        raise ValueError("native report custody is corrupt")
    permit = JobPermit(**binding["permit"])
    first = store.get_native_action_by_key(permit.work_uid,
        "clean:first-unlock" if permit.work_type == "CLEAN" else "delivery:first-open")
    if first is None or first["permit"] != permit:
        raise ValueError("native report lost its original permit binding")
    record = store.get_native_command(binding["startCommandUid"])
    if record is None or record["conflict"]:
        raise ValueError("native report lost its original START")
    start = uart.decode_payload(record["message_name"], record["payload"])
    from work_recovery import complete_result
    decision = complete_result(store, conn, permit, record, start, validate_report=False)
    if decision is None or decision["evidence"]["state"] != "MATCHED":
        raise ValueError("native report lost its original result evidence")
    evidence = decision["evidence"]
    command = original_authority(store, permit, start, evidence, binding["deviceName"])
    value = uart.decode_payload("WORK_RESULT", result["payload"])
    if not supports_result_policy(value):
        raise ValueError("native report has no supported result policy")
    if command is None or report_payload(value, evidence, command, event["payload"]["photos"]) != event["payload"]:
        raise ValueError("native report no longer matches original business evidence")
    expected_type = "CLEAN_COMPLETE" if permit.work_type == "CLEAN" else "DELIVERY_COMPLETE"
    if (event["eventType"] != expected_type or event["commandUid"] != permit.command_uid
            or event["target"] != dict(type=value["workType"], uid=permit.work_uid)):
        raise ValueError("native report envelope differs from original work")
    return binding


def confirmation_for_report(store, conn, task, command, device_name, *, persisted=False):
    """A backend decision about this exact report, not local completion authority."""
    from onenet_wire import validate_command_envelope, validate_stored_confirmation_envelope
    binding = store._native_report_binding(conn, task)
    if not isinstance(command, dict):
        raise ValueError("native confirmation requires its original command envelope")
    stable = {key: value for key, value in command.items() if key != "cosGrant"}
    if (set(stable) != {"schemaVersion", "payloadSchemaVersion", "commandUid", "commandType",
            "targetDeviceName", "target", "issuedAt", "expiresAt", "payloadSha256", "payload"}
            or command.get("cosGrant") is not None or command.get("commandType") != "CONFIRM_EDGE_EVENT"):
        raise ValueError("native confirmation command shape is invalid")
    (validate_stored_confirmation_envelope if persisted else validate_command_envelope)(stable)
    payload = stable["payload"]
    event = json.loads(store.get_event(task["event_uid"])["payload_json"])
    if (device_name != binding["deviceName"] or stable["targetDeviceName"] != device_name
            or payload["originalEventUid"] != task["event_uid"]
            or payload["originalPayloadSha256"] != event["payloadSha256"]):
        raise ValueError("native confirmation differs from the original report or device")
    if payload["outcome"] == "BUSINESS_APPLIED":
        expected_type = "CLEAN_RECORD" if event["eventType"] == "CLEAN_COMPLETE" else "DELIVERY_ORDER"
        refs = payload["resultReferences"]
        if (payload["effectKind"] != "CREATED" or len(refs) != 1
                or refs[0]["type"] != expected_type or not refs[0]["key"].strip()):
            raise ValueError("native confirmation lacks the original business result reference")
    return stable, binding


class NativeResultReporter:
    def __init__(self, store, safety, *, device_name, photo_manager=None):
        if not isinstance(safety, PermanentJobSafety) or not safety.enabled:
            raise ValueError("native result reporting requires permanent job safety")
        if not isinstance(device_name, str) or not device_name:
            raise ValueError("native result reporting requires the device identity")
        self.store, self.safety, self.device_name, self.photo = store, safety, device_name, photo_manager

    def prepare(self, permit, start_command_uid):
        if not isinstance(permit, JobPermit):
            raise ValueError("native report requires the original job permit")
        existing = self.store.get_native_result_report(permit, start_command_uid, device_name=self.device_name)
        if existing is not None:
            return existing
        decision = self.store.evaluate_native_work_recovery(permit, start_command_uid, current_boot=lambda: None)
        if decision["status"] == "DELIVERY_ISSUE_ARCHIVED":
            if decision["issue"]["deviceName"] != self.device_name:
                raise ValueError("native archived delivery belongs to another device")
            return dict(state="DELIVERY_ISSUE_ARCHIVED", issueUid=decision["issue"]["issueUid"])
        if decision["status"] != "COMPLETE_RESULT_AVAILABLE":
            return dict(state="WAITING_FOR_COMPLETE_RESULT")
        evidence = decision["evidence"]
        if evidence["state"] != "MATCHED":
            return dict(state=evidence["state"], missing=evidence["missing"])
        result = uart.decode_payload("WORK_RESULT", decision["result"]["payload"])
        if not supports_result_policy(result):
            return dict(state="WAITING_FOR_RESULT_POLICY")
        record = self.store.get_native_command(start_command_uid)
        start = uart.decode_payload(record["message_name"], record["payload"])
        if original_authority(self.store, permit, start, evidence, self.device_name) is None:
            return dict(state="WAITING_FOR_CLOUD_COMMAND_CUSTODY")
        reconciler = NativeActionReconciler(self.store, self.safety)
        ledgers = {}
        for command in evidence["execution"]["commands"]:
            uid = command["binding"]["action"]["action_uid"]
            confirmed = reconciler.reconcile(uid)
            if confirmed is None or confirmed["state"] != "CONFIRMED":
                return dict(state="WAITING_FOR_ACTION_CONFIRMATION")
            ledgers[uid] = self.safety.get_physical_action(uid)
            check_ledger(self.store.get_native_action_binding(uid), ledgers[uid],
                self.store.get_native_action_confirmation(uid))
        return self.store.create_native_result_report(permit, start_command_uid,
            device_name=self.device_name, ledgers=ledgers, photo_manager=self.photo)
