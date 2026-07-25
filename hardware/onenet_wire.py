"""OneNet wire projection helpers for the F-10 target thing model.

This module only handles transport projection. Business state machines remain
in work_manager.py and persistent reliability remains in edge_store.py.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any


COMMAND_IDENTIFIERS = {
    "applyConfiguration": "APPLY_CONFIGURATION",
    "startDeliverySession": "START_DELIVERY_SESSION",
    "startCleanOperation": "START_CLEAN_OPERATION",
    "endCleanBeforeUnlock": "END_CLEAN_BEFORE_UNLOCK",
    "resumeCleanOperation": "RESUME_CLEAN_OPERATION",
    "sampleFullness": "SAMPLE_FULLNESS",
    "measureEmptyBagBaseline": "MEASURE_EMPTY_BAG_BASELINE",
    "confirmEdgeEvent": "CONFIRM_EDGE_EVENT",
    "providePhotoUploadGrant": "PROVIDE_PHOTO_UPLOAD_GRANT",
}

EVENT_IDENTIFIERS = {
    "BASELINE_MEASUREMENT_COMPLETE": "baselineMeasurementComplete",
    "BUSINESS_CONFIRMATION_RECEIPT": "businessConfirmationReceipt",
    "CLEAN_COMPLETE": "cleanComplete",
    "CONFIGURATION_PROGRESS": "configurationProgress",
    "DELIVERY_COMPLETE": "deliveryComplete",
    "DEVICE_COMMAND_OBSERVED": "deviceCommandObserved",
    "DEVICE_FAULT_OBSERVED": "deviceFaultObserved",
    "DEVICE_FAULT_RECOVERED": "deviceFaultRecovered",
    "DEVICE_RUNTIME_SNAPSHOT": "deviceRuntimeSnapshot",
    "FULLNESS_SAMPLE_COMPLETE": "fullnessSampleComplete",
    "PHOTO_STATUS_REPORTED": "photoStatusReported",
    "PHOTO_UPLOAD_GRANT_REQUESTED": "photoUploadGrantRequested",
}

COMMAND_TYPE_BY_CODE = {
    1: None,  # one enum per OneNet function; use topic identifier as authority.
}

EVENT_TYPE_TO_CODE = {name: 1 for name in EVENT_IDENTIFIERS}
DELIVERY_CLASS_TO_CODE = {
    "RELIABLE_FACT": 1,
    "CONTROL_RECEIPT": 1,
    "TELEMETRY_SNAPSHOT": 1,
}
OUTCOME_BY_CODE = {
    1: "BUSINESS_APPLIED",
    2: "EVENT_QUARANTINED",
}
OUTCOME_TO_CODE = {v: k for k, v in OUTCOME_BY_CODE.items()}
CONFIGURATION_STAGE_TO_CODE = {
    "EDGE_SAVED": 1,
    "APPLIED": 2,
    "FAILED": 3,
}
EFFECT_KIND_BY_CODE = {
    1: "CREATED",
    2: "UPDATED",
    3: "NO_ACTION_REQUIRED",
}
CLEAN_END_REASON_BY_CODE = {
    1: "CLEANER_CANCELLED",
    2: "START_AUTHORIZATION_EXPIRED",
    3: "PREUNLOCK_FAILURE",
}
SAMPLE_ROLE_BY_CODE = {
    1: "INITIAL",
    2: "CONFIRMATION",
    3: "MANUAL_RECHECK",
}
FULLNESS_TRIGGER_BY_CODE = {
    1: "DELIVERY_COMPLETE",
    2: "CLEAN_COMPLETE",
    3: "MANUAL_RECHECK",
}
FULLNESS_MODE_BY_CODE = {
    1: "INFRARED_ONLY",
    2: "WEIGHT_ONLY",
    3: "INFRARED_OR_WEIGHT",
}
WORK_TYPE_BY_CODE = {
    1: "DELIVERY_SESSION",
    2: "CLEAN_OPERATION",
}
PHOTO_SLOT_BY_CODE = {
    "DELIVERY_SESSION": {
        1: "BEFORE_INNER",
        2: "BEFORE_OUTER",
        3: "AFTER_INNER",
        4: "AFTER_OUTER",
    },
    "CLEAN_OPERATION": {
        1: "FIRST_OPEN_INNER",
        2: "FIRST_OPEN_OUTER",
        3: "FINAL_CLOSE_INNER",
        4: "FINAL_CLOSE_OUTER",
    },
}
TARGET_TYPE_BY_CODE = {
    1: None,  # decoded from command/event context where OneNet enum is local.
}
RECEIPT_STATE_TO_CODE = {
    "ACCEPTED": 1,
    "DUPLICATE_ACCEPTED": 2,
    "REJECTED": 3,
}
CLOCK_QUALITY_TO_CODE = {
    "SYNCED": 1,
    "ESTIMATED": 2,
    "UNAVAILABLE": 3,
}


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_payload_sha256(payload: dict[str, Any]) -> str:
    """Stable JSON digest for edge-side generated payloads.

    The F-10 contract uses RFC8785 JCS. For the current integer/string/null
    payloads generated on the edge, sorted-key compact JSON is equivalent
    enough for edge runtime bookkeeping; full contract validation stays in
    contracts/tools.
    """

    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def decode_service_command(identifier: str, params: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a command envelope from OneNet service params."""

    command_type = COMMAND_IDENTIFIERS.get(identifier)
    if not command_type:
        raise ValueError(f"unsupported OneNet service identifier: {identifier}")

    scalars = _merge_scalar_fields(params)
    payload = _extract_payload(identifier, scalars, params)
    cos_grant = _extract_cos_grant(scalars, params)
    target = dict(params.get("target") or {})
    target["type"] = _target_type_for_command(command_type, target.get("type"))

    return {
        "schemaVersion": scalars.get("schemaVersion"),
        "commandUid": scalars.get("commandUid") or params.get("commandUid"),
        "commandType": command_type,
        "deploymentCode": scalars.get("deploymentCode"),
        "target": target,
        "issuedAt": scalars.get("issuedAt"),
        "expiresAt": scalars.get("expiresAt"),
        "payloadSchemaVersion": scalars.get("payloadSchemaVersion"),
        "payloadSha256": scalars.get("payloadSha256"),
        "payload": payload,
        "cosGrant": cos_grant,
    }


def validate_command_envelope(command: dict[str, Any]) -> None:
    """Validate stable command facts before reliable inbox acceptance."""

    if command.get("schemaVersion") != 1:
        raise ValueError("unsupported schemaVersion")
    if command.get("payloadSchemaVersion") != 1:
        raise ValueError("unsupported payloadSchemaVersion")
    _require_uuid4(command.get("commandUid"), "commandUid")
    command_type = command.get("commandType")
    if command_type not in COMMAND_IDENTIFIERS.values():
        raise ValueError("unsupported commandType")
    deployment_code = command.get("deploymentCode")
    if not isinstance(deployment_code, str) or not deployment_code:
        raise ValueError("deploymentCode is required")
    target = command.get("target")
    if not isinstance(target, dict) or not target.get("type") or not target.get("uid"):
        raise ValueError("target is required")
    payload = command.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    payload_sha256 = command.get("payloadSha256")
    if not _is_sha256(payload_sha256):
        raise ValueError("payloadSha256 must be 64 lowercase hex characters")
    if canonical_payload_sha256(payload) != payload_sha256:
        raise ValueError("payloadSha256 mismatch")
    _parse_utc_instant(command.get("issuedAt"), "issuedAt")
    expires_at = _parse_utc_instant(command.get("expiresAt"), "expiresAt")
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("command expired")
    if command_type == "APPLY_CONFIGURATION":
        _validate_apply_configuration(command)


def encode_command_receipt(command_uid: str, receipt_state: str, edge_boot_id: int,
                           error_code: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "commandUid": command_uid,
        "receiptState": RECEIPT_STATE_TO_CODE[receipt_state],
        "errorCodePresent": bool(error_code),
        "errorCode": error_code or "",
        "edgeBootId": edge_boot_id,
    }


def build_business_confirmation_receipt(
    deployment_code: str,
    command_uid: str,
    confirmation_uid: str,
    original_event_uid: str,
    original_payload_sha256: str,
    outcome: str,
    edge_event_sequence: int,
) -> dict[str, Any]:
    payload = {
        "confirmationUid": confirmation_uid,
        "originalEventUid": original_event_uid,
        "originalPayloadSha256": original_payload_sha256,
        "outcome": outcome,
    }
    return {
        "schemaVersion": 1,
        "eventUid": str(uuid.uuid4()),
        "deploymentCode": deployment_code,
        "edgeEventSequence": edge_event_sequence,
        "eventType": "BUSINESS_CONFIRMATION_RECEIPT",
        "deliveryClass": "CONTROL_RECEIPT",
        "target": {"type": "BUSINESS_CONFIRMATION", "uid": confirmation_uid},
        "commandUid": command_uid,
        "occurredAt": utc_now_rfc3339(),
        "clockQuality": "SYNCED",
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
    }


def build_configuration_progress_event(
    *,
    deployment_code: str,
    command_uid: str,
    application_uid: str,
    stage: str,
    version: int,
    content_sha256: str,
    mcu_payload_sha256: str,
    edge_event_sequence: int,
    mcu_command_uid: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    payload = {
        "applicationUid": application_uid,
        "stage": stage,
        "version": version,
        "contentSha256": content_sha256,
        "mcuPayloadSha256": mcu_payload_sha256,
        "mcuCommandUid": mcu_command_uid,
        "errorCode": error_code,
    }
    return {
        "schemaVersion": 1,
        "eventUid": str(uuid.uuid4()),
        "deploymentCode": deployment_code,
        "edgeEventSequence": edge_event_sequence,
        "eventType": "CONFIGURATION_PROGRESS",
        "deliveryClass": "RELIABLE_FACT",
        "target": {"type": "CONFIGURATION_APPLICATION", "uid": application_uid},
        "commandUid": command_uid,
        "occurredAt": utc_now_rfc3339(),
        "clockQuality": "SYNCED",
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
    }


def encode_event_post(event_type: str, event: dict[str, Any]) -> dict[str, Any]:
    """Project an event envelope into OneNet OneJSON event/post payload."""

    identifier = EVENT_IDENTIFIERS.get(event_type)
    if not identifier:
        raise ValueError(f"unsupported OneNet event type: {event_type}")
    value = dict(event)
    payload = dict(value.pop("payload", {}) or {})
    value.update(payload)

    if "eventType" in value:
        value["eventType"] = EVENT_TYPE_TO_CODE.get(str(value["eventType"]), value["eventType"])
    if "deliveryClass" in value:
        value["deliveryClass"] = DELIVERY_CLASS_TO_CODE.get(
            str(value["deliveryClass"]), value["deliveryClass"]
        )
    if "clockQuality" in value:
        value["clockQuality"] = CLOCK_QUALITY_TO_CODE.get(
            str(value["clockQuality"]), value["clockQuality"]
        )
    if "outcome" in value:
        value["outcome"] = OUTCOME_TO_CODE.get(str(value["outcome"]), value["outcome"])
    if event_type == "CONFIGURATION_PROGRESS":
        value["stage"] = CONFIGURATION_STAGE_TO_CODE.get(
            str(value.get("stage")), value.get("stage")
        )
        value["mcuCommandUidPresent"] = value.get("mcuCommandUid") is not None
        value["mcuCommandUid"] = value.get("mcuCommandUid") or ""
        value["errorCodePresent"] = value.get("errorCode") is not None
        value["errorCode"] = value.get("errorCode") or ""
    if value.get("occurredAt") is None:
        value["occurredAtPresent"] = False
        value["occurredAt"] = ""
    else:
        value["occurredAtPresent"] = True
    value["target"] = _encode_target(value.get("target"))

    return {
        "id": str(event.get("eventUid") or event.get("event_uid") or uuid.uuid4()),
        "version": "1.0",
        "params": {identifier: {"value": value}},
    }


def _merge_scalar_fields(params: dict[str, Any]) -> dict[str, Any]:
    scalars: dict[str, Any] = {}
    for key, value in params.items():
        if key.startswith("scalarFields") and isinstance(value, dict):
            scalars.update(value)
    if not scalars:
        scalars.update(params)
    return scalars


def _extract_payload(identifier: str, scalars: dict[str, Any],
                     params: dict[str, Any]) -> dict[str, Any]:
    if identifier == "applyConfiguration":
        ports = []
        for item in params.get("ports") or []:
            port = dict(item)
            port["fullnessMode"] = FULLNESS_MODE_BY_CODE.get(
                port.get("fullnessMode"), port.get("fullnessMode")
            )
            ports.append(port)
        return {
            "applicationUid": scalars.get("applicationUid"),
            "config": params.get("config"),
            "deviceConfig": params.get("deviceConfig"),
            "ports": ports,
        }
    if identifier == "confirmEdgeEvent":
        result_references = params.get("resultReferences") or []
        return {
            "confirmationUid": scalars.get("confirmationUid"),
            "originalEventUid": scalars.get("originalEventUid"),
            "originalPayloadSha256": scalars.get("originalPayloadSha256"),
            "outcome": OUTCOME_BY_CODE.get(scalars.get("outcome"), scalars.get("outcome")),
            "effectKind": _decode_optional_enum(scalars, "effectKind", EFFECT_KIND_BY_CODE),
            "processedAt": scalars.get("processedAt"),
            "resultReferences": [_decode_reference(r) for r in result_references],
            "errorCode": _none_if_absent(scalars, "errorCode"),
            "quarantineUid": _none_if_absent(scalars, "quarantineUid"),
        }
    if identifier == "startDeliverySession":
        return {
            "sessionUid": scalars.get("sessionUid"),
            "portNo": scalars.get("portNo"),
            "bagUid": scalars.get("bagUid"),
            "config": params.get("config"),
            "unitPriceTenThousandths": scalars.get("unitPriceTenThousandths"),
            "continueDeliveryWaitMs": scalars.get("continueDeliveryWaitMs"),
            "negativeWeightThresholdGrams": scalars.get("negativeWeightThresholdGrams"),
            "deliveryAutoCloseMs": scalars.get("deliveryAutoCloseMs"),
        }
    if identifier == "startCleanOperation":
        return {
            "operationUid": scalars.get("operationUid"),
            "portNo": scalars.get("portNo"),
            "oldBagUid": _none_if_absent(scalars, "oldBagUid"),
            "oldBaselineWeightGrams": _none_if_absent(scalars, "oldBaselineWeightGrams"),
            "newBagUid": scalars.get("newBagUid"),
            "operationWindowMs": scalars.get("operationWindowMs"),
            # START is generation zero. The imported OneNet model transports
            # this const as enum code 1, so project it back to domain value 0.
            "recoveryGeneration": 0,
            "config": params.get("config"),
        }
    if identifier == "resumeCleanOperation":
        return {
            "operationUid": scalars.get("operationUid"),
            "portNo": scalars.get("portNo"),
            "newBagUid": scalars.get("newBagUid"),
            "config": params.get("config"),
            "operationWindowMs": scalars.get("operationWindowMs"),
            "recoveryGeneration": scalars.get("recoveryGeneration"),
            "originalCleanerConfirmedOnsite": scalars.get(
                "originalCleanerConfirmedOnsite"
            ),
        }
    if identifier == "endCleanBeforeUnlock":
        return {
            "operationUid": scalars.get("operationUid"),
            "portNo": scalars.get("portNo"),
            "reason": CLEAN_END_REASON_BY_CODE.get(
                scalars.get("reason"), scalars.get("reason")
            ),
        }
    if identifier == "sampleFullness":
        return {
            "detectionUid": scalars.get("detectionUid"),
            "portNo": scalars.get("portNo"),
            "sampleRole": SAMPLE_ROLE_BY_CODE.get(
                scalars.get("sampleRole"), scalars.get("sampleRole")
            ),
            "triggerType": FULLNESS_TRIGGER_BY_CODE.get(
                scalars.get("triggerType"), scalars.get("triggerType")
            ),
            "fullnessMode": FULLNESS_MODE_BY_CODE.get(
                scalars.get("fullnessMode"), scalars.get("fullnessMode")
            ),
            "currentBaselineWeightGrams": _none_if_absent(
                scalars, "currentBaselineWeightGram"
            ),
            "configuredFullWeightGrams": scalars.get("configuredFullWeightGrams"),
            "settleWaitMs": scalars.get("settleWaitMs"),
            "measurementTimeoutMs": scalars.get("measurementTimeoutMs"),
            "config": params.get("config"),
        }
    if identifier == "measureEmptyBagBaseline":
        return {
            "measurementUid": scalars.get("measurementUid"),
            "portNo": scalars.get("portNo"),
            "bagUid": scalars.get("bagUid"),
            "emptyBagConfirmed": scalars.get("emptyBagConfirmed"),
            "measurementTimeoutMs": scalars.get("measurementTimeoutMs"),
            "config": params.get("config"),
        }
    if identifier == "providePhotoUploadGrant":
        work_type = WORK_TYPE_BY_CODE.get(
            scalars.get("workType"), scalars.get("workType")
        )
        slots = PHOTO_SLOT_BY_CODE.get(work_type, {})
        return {
            "grantRequestEventUid": scalars.get("grantRequestEventUid"),
            "workType": work_type,
            "workUid": scalars.get("workUid"),
            "authorizedSlots": [
                slots.get(slot, slot) for slot in params.get("authorizedSlots") or []
            ],
        }
    payload = dict(params)
    payload.pop("target", None)
    payload.pop("cosGrantSessionTokenParts", None)
    for key in list(payload):
        if key.startswith("scalarFields"):
            payload.pop(key, None)
    payload.update({k: v for k, v in scalars.items() if not k.startswith("cosGrant")})
    return payload


def _extract_cos_grant(scalars: dict[str, Any], params: dict[str, Any]) -> dict[str, Any] | None:
    if not scalars.get("cosGrantPresent"):
        return None
    return {
        "grantUid": scalars.get("cosGrantGrantUid"),
        "tmpSecretId": scalars.get("cosGrantTmpSecretId"),
        "tmpSecretKey": scalars.get("cosGrantTmpSecretKey"),
        "sessionTokenParts": params.get("cosGrantSessionTokenParts") or [],
        "bucket": scalars.get("cosGrantBucket"),
        "region": scalars.get("cosGrantRegion"),
        "baseUrl": scalars.get("cosGrantBaseUrl"),
        "keyPrefix": scalars.get("cosGrantKeyPrefix"),
        "expiresAt": scalars.get("cosGrantExpiresAt"),
    }


def _none_if_absent(scalars: dict[str, Any], field: str) -> Any:
    present_key = f"{field}Present"
    if present_key in scalars and not scalars.get(present_key):
        return None
    return scalars.get(field)


def _decode_optional_enum(scalars: dict[str, Any], field: str,
                          mapping: dict[int, str]) -> Any:
    value = _none_if_absent(scalars, field)
    return mapping.get(value, value)


def _decode_reference(ref: dict[str, Any]) -> dict[str, Any]:
    result = dict(ref)
    if result.get("type") == 1:
        result["type"] = "DELIVERY_ORDER"
    return result


def _target_type_for_command(command_type: str, wire_value: Any) -> str:
    mapping = {
        "APPLY_CONFIGURATION": "CONFIGURATION_APPLICATION",
        "START_DELIVERY_SESSION": "DELIVERY_SESSION",
        "START_CLEAN_OPERATION": "CLEAN_OPERATION",
        "END_CLEAN_BEFORE_UNLOCK": "CLEAN_OPERATION",
        "RESUME_CLEAN_OPERATION": "CLEAN_OPERATION",
        "SAMPLE_FULLNESS": "FULLNESS_DETECTION",
        "MEASURE_EMPTY_BAG_BASELINE": "BASELINE_MEASUREMENT",
        "CONFIRM_EDGE_EVENT": "EDGE_EVENT",
        "PROVIDE_PHOTO_UPLOAD_GRANT": "PHOTO_GRANT_REQUEST",
    }
    return mapping.get(command_type, str(wire_value))


def _encode_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {"type": 1, "uid": ""}
    encoded = dict(target)
    encoded["type"] = 1
    return encoded


def _validate_apply_configuration(command: dict[str, Any]) -> None:
    payload = command["payload"]
    application_uid = payload.get("applicationUid")
    _require_uuid4(application_uid, "applicationUid")
    if command["target"].get("uid") != application_uid:
        raise ValueError("configuration target does not match applicationUid")

    config = payload.get("config")
    device_config = payload.get("deviceConfig")
    ports = payload.get("ports")
    if not isinstance(config, dict) or not isinstance(device_config, dict):
        raise ValueError("configuration blocks are required")
    if not isinstance(ports, list) or not 1 <= len(ports) <= 6:
        raise ValueError("ports must contain 1..6 entries")
    version = config.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("config.version must be an integer")
    if not 1 <= version <= 9_007_199_254_740_991:
        raise ValueError("config.version out of range")
    for field in ("contentSha256", "mcuPayloadSha256"):
        if not _is_sha256(config.get(field)):
            raise ValueError(f"config.{field} must be 64 lowercase hex characters")
    port_numbers = [port.get("portNo") for port in ports if isinstance(port, dict)]
    if port_numbers != list(range(1, len(ports) + 1)):
        raise ValueError("ports must be ordered and contiguous from 1")


def _require_uuid4(value: Any, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUID") from error
    if parsed.version != 4 or str(parsed) != str(value):
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return str(parsed)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdef" for char in value)


def _parse_utc_instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be a UTC instant")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{field} must be a UTC instant") from error
