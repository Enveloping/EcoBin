"""OneNet wire projection helpers for the F-10 target thing model.

This module only handles transport projection. Business state machines remain
in work_manager.py and persistent reliability remains in edge_store.py.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


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
    "requestDeviceAcceptance": "REQUEST_DEVICE_ACCEPTANCE",
    "syncDeviceEntryUrl": "SYNC_DEVICE_ENTRY_URL",
}

COMMAND_TYPE_BY_CODE = {
    1: None,  # one enum per OneNet function; use topic identifier as authority.
}

OUTCOME_BY_CODE = {
    1: "BUSINESS_APPLIED",
    2: "EVENT_QUARANTINED",
}
EFFECT_KIND_BY_CODE = {
    1: "CREATED",
    2: "UPDATED",
    3: "NO_ACTION_REQUIRED",
}
RESULT_REFERENCE_TYPE_BY_CODE = {
    1: "DELIVERY_ORDER",
    2: "CLEAN_RECORD",
    3: "FULLNESS_DETECTION",
    4: "BASELINE_MEASUREMENT",
    5: "CONFIGURATION_APPLICATION",
    6: "PHOTO_SLOT",
    7: "DEVICE_FAULT",
    8: "PORT_FULLNESS_STATE",
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
    1: "SENSOR_ONLY",
    2: "WEIGHT_ONLY",
    3: "SENSOR_OR_WEIGHT",
}
FULLNESS_SENSOR_KIND_BY_CODE = {
    1: "ULTRASONIC",
    2: "DIGITAL_INFRARED",
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
WORK_PHOTO_SLOTS = {
    work_type: tuple(slots[index] for index in sorted(slots))
    for work_type, slots in PHOTO_SLOT_BY_CODE.items()
}
WORK_TYPE_PATH = {
    "DELIVERY_SESSION": "delivery-session",
    "CLEAN_OPERATION": "clean-operation",
    "DEVICE_ACCEPTANCE": "device-acceptance",
}
TARGET_TYPE_BY_CODE = {
    1: None,  # decoded from command/event context where OneNet enum is local.
}
RECEIPT_STATE_TO_CODE = {
    "ACCEPTED": 1,
    "DUPLICATE_ACCEPTED": 2,
    "REJECTED": 3,
}
_PROJECTION_MODEL = json.loads(
    Path(__file__).with_name("onenet_projection_model.json").read_text(
        encoding="utf-8"
    )
)
_EVENT_PROJECTIONS = {
    definition["eventType"]: (identifier, definition)
    for identifier, definition in _PROJECTION_MODEL["events"].items()
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
        # OneNet encodes a JSON-Schema const as the local enum value 1.  The
        # domain envelope remains v2 after decoding.
        "schemaVersion": 2,
        "commandUid": scalars.get("commandUid") or params.get("commandUid"),
        "commandType": command_type,
        "targetDeviceName": scalars.get("targetDeviceName"),
        "target": target,
        "issuedAt": scalars.get("issuedAt"),
        "expiresAt": scalars.get("expiresAt"),
        "payloadSchemaVersion": 2,
        "payloadSha256": scalars.get("payloadSha256"),
        "payload": payload,
        "cosGrant": cos_grant,
    }


def validate_command_envelope(
    command: dict[str, Any],
    *,
    trusted_environment: dict[str, str] | None = None,
) -> None:
    """Validate stable command facts before reliable inbox acceptance."""

    if command.get("schemaVersion") != 2:
        raise ValueError("unsupported schemaVersion")
    if command.get("payloadSchemaVersion") != 2:
        raise ValueError("unsupported payloadSchemaVersion")
    _require_uuid4(command.get("commandUid"), "commandUid")
    command_type = command.get("commandType")
    if command_type not in COMMAND_IDENTIFIERS.values():
        raise ValueError("unsupported commandType")
    device_name = command.get("targetDeviceName")
    if not isinstance(device_name, str) or not device_name:
        raise ValueError("targetDeviceName is required")
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
    _validate_command_target(command)
    if command_type == "APPLY_CONFIGURATION":
        _validate_apply_configuration(command)
    elif command_type == "CONFIRM_EDGE_EVENT":
        _validate_confirm_edge_event(command)
    elif command_type == "PROVIDE_PHOTO_UPLOAD_GRANT":
        _validate_photo_upload_grant_command(
            command,
            trusted_environment=trusted_environment,
        )
    elif command_type == "REQUEST_DEVICE_ACCEPTANCE":
        _validate_device_acceptance_command(
            command,
            trusted_environment=trusted_environment,
        )
    elif command_type == "SYNC_DEVICE_ENTRY_URL":
        _validate_device_entry_url_payload(command["payload"])
    elif command_type == "START_DELIVERY_SESSION" and command.get("cosGrant"):
        validate_cos_grant(
            command["cosGrant"],
            device_name=device_name,
            work_type="DELIVERY_SESSION",
            work_uid=payload.get("sessionUid"),
            trusted_environment=trusted_environment,
        )
    elif command_type in {
        "START_CLEAN_OPERATION",
        "RESUME_CLEAN_OPERATION",
    } and command.get("cosGrant"):
        validate_cos_grant(
            command["cosGrant"],
            device_name=device_name,
            work_type="CLEAN_OPERATION",
            work_uid=payload.get("operationUid"),
            trusted_environment=trusted_environment,
        )


def _validate_command_target(command: dict[str, Any]) -> None:
    command_type = command["commandType"]
    payload = command["payload"]
    target_fields = {
        "APPLY_CONFIGURATION": (
            "CONFIGURATION_APPLICATION",
            "applicationUid",
        ),
        "START_DELIVERY_SESSION": (
            "DELIVERY_SESSION",
            "sessionUid",
        ),
        "START_CLEAN_OPERATION": (
            "CLEAN_OPERATION",
            "operationUid",
        ),
        "END_CLEAN_BEFORE_UNLOCK": (
            "CLEAN_OPERATION",
            "operationUid",
        ),
        "RESUME_CLEAN_OPERATION": (
            "CLEAN_OPERATION",
            "operationUid",
        ),
        "SAMPLE_FULLNESS": (
            "FULLNESS_DETECTION",
            "detectionUid",
        ),
        "MEASURE_EMPTY_BAG_BASELINE": (
            "BASELINE_MEASUREMENT",
            "measurementUid",
        ),
        "CONFIRM_EDGE_EVENT": (
            "EDGE_EVENT",
            "originalEventUid",
        ),
        "PROVIDE_PHOTO_UPLOAD_GRANT": (
            "PHOTO_GRANT_REQUEST",
            "grantRequestEventUid",
        ),
    }
    if command_type in {
        "REQUEST_DEVICE_ACCEPTANCE",
        "SYNC_DEVICE_ENTRY_URL",
    }:
        if command_type == "REQUEST_DEVICE_ACCEPTANCE":
            _require_uuid4(payload.get("challengeUid"), "challengeUid")
        if command["target"] != {
            "type": "DEVICE_ASSET",
            "uid": command["targetDeviceName"],
        }:
            raise ValueError(
                "device-asset target differs from targetDeviceName"
            )
        return
    target_type, payload_uid_field = target_fields[command_type]
    payload_uid = payload.get(payload_uid_field)
    _require_uuid4(payload_uid, payload_uid_field)
    if command["target"] != {
        "type": target_type,
        "uid": payload_uid,
    }:
        raise ValueError(
            f"target differs from payload.{payload_uid_field}"
        )


def _validate_confirm_edge_event(command: dict[str, Any]) -> None:
    payload = command["payload"]
    required = {
        "confirmationUid",
        "originalEventUid",
        "originalPayloadSha256",
        "processedAt",
        "outcome",
        "effectKind",
        "resultReferences",
        "errorCode",
        "quarantineUid",
    }
    if set(payload) != required:
        raise ValueError("confirmation payload fields are invalid")
    _require_uuid4(payload["confirmationUid"], "confirmationUid")
    _require_uuid4(payload["originalEventUid"], "originalEventUid")
    if not _is_sha256(payload["originalPayloadSha256"]):
        raise ValueError("originalPayloadSha256 is invalid")
    _parse_utc_instant(payload["processedAt"], "processedAt")
    outcome = payload["outcome"]
    references = payload["resultReferences"]
    if not isinstance(references, list):
        raise ValueError("resultReferences must be an array")
    if any(
        not isinstance(reference, dict)
        or set(reference) != {"type", "key"}
        or not isinstance(reference["type"], str)
        or not reference["type"]
        or not isinstance(reference["key"], str)
        or not reference["key"]
        for reference in references
    ):
        raise ValueError("resultReferences are invalid")
    if outcome == "BUSINESS_APPLIED":
        if payload["effectKind"] not in EFFECT_KIND_BY_CODE.values():
            raise ValueError("effectKind is invalid")
        if payload["errorCode"] is not None:
            raise ValueError("BUSINESS_APPLIED errorCode must be null")
        if payload["quarantineUid"] is not None:
            raise ValueError(
                "BUSINESS_APPLIED quarantineUid must be null"
            )
    elif outcome == "EVENT_QUARANTINED":
        if payload["effectKind"] is not None:
            raise ValueError(
                "EVENT_QUARANTINED effectKind must be null"
            )
        if references:
            raise ValueError(
                "EVENT_QUARANTINED resultReferences must be empty"
            )
        error_code = payload["errorCode"]
        if (
            not isinstance(error_code, str)
            or not 1 <= len(error_code) <= 64
            or error_code != error_code.upper()
        ):
            raise ValueError(
                "EVENT_QUARANTINED errorCode is invalid"
            )
        quarantine_uid = payload["quarantineUid"]
        if quarantine_uid is not None:
            _require_uuid4(quarantine_uid, "quarantineUid")
    else:
        raise ValueError("confirmation outcome is invalid")


def validate_cos_grant(
    grant: dict[str, Any],
    *,
    device_name: str,
    work_type: str,
    work_uid: str,
    trusted_environment: dict[str, str] | None = None,
) -> None:
    """Validate a scoped STS grant without retaining any secret fields."""
    if not isinstance(grant, dict):
        raise ValueError("cosGrant is required")
    required = {
        "grantUid",
        "tmpSecretId",
        "tmpSecretKey",
        "sessionTokenParts",
        "bucket",
        "region",
        "baseUrl",
        "keyPrefix",
        "expiresAt",
    }
    if set(grant) != required:
        raise ValueError("cosGrant fields are invalid")
    _require_uuid4(grant["grantUid"], "cosGrant.grantUid")
    for field, maximum in (
        ("tmpSecretId", 128),
        ("tmpSecretKey", 128),
        ("bucket", 128),
        ("region", 32),
    ):
        value = grant[field]
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise ValueError(f"cosGrant.{field} is invalid")
    parts = grant["sessionTokenParts"]
    if (
        not isinstance(parts, list)
        or not 1 <= len(parts) <= 8
        or any(
            not isinstance(part, str) or not 1 <= len(part) <= 512
            for part in parts
        )
    ):
        raise ValueError("cosGrant.sessionTokenParts is invalid")
    _require_uuid4(work_uid, "photo workUid")
    work_path = WORK_TYPE_PATH.get(work_type)
    if work_path is None:
        raise ValueError("photo workType is invalid")
    expected_prefix = f"ecobin/{work_path}/{work_uid}/"
    if grant["keyPrefix"] != expected_prefix:
        raise ValueError("cosGrant.keyPrefix differs from work identity")
    expected_base_url = (
        f"https://{grant['bucket']}.cos."
        f"{grant['region']}.myqcloud.com"
    )
    if grant["baseUrl"] != expected_base_url:
        raise ValueError("cosGrant.baseUrl differs from bucket and region")
    if trusted_environment is not None:
        trusted = {
            "bucket": trusted_environment.get("bucket"),
            "region": trusted_environment.get("region"),
            "baseUrl": trusted_environment.get("baseUrl"),
        }
        if not all(trusted.values()):
            raise ValueError(
                "trusted runtime environment is incomplete"
            )
        actual = {
            "bucket": grant["bucket"],
            "region": grant["region"],
            "baseUrl": grant["baseUrl"],
        }
        if actual != trusted:
            raise ValueError(
                "cosGrant differs from trusted runtime environment"
            )
    parsed = urlsplit(grant["baseUrl"])
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("cosGrant.baseUrl is not a canonical HTTPS origin")
    if _parse_utc_instant(
        grant["expiresAt"],
        "cosGrant.expiresAt",
    ) <= datetime.now(timezone.utc):
        raise ValueError("cosGrant expired")


def _validate_photo_upload_grant_command(
    command: dict[str, Any],
    *,
    trusted_environment: dict[str, str] | None = None,
) -> None:
    payload = command["payload"]
    required = {
        "grantRequestEventUid",
        "workType",
        "workUid",
        "authorizedSlots",
    }
    if set(payload) != required:
        raise ValueError("photo grant payload fields are invalid")
    request_uid = _require_uuid4(
        payload["grantRequestEventUid"],
        "grantRequestEventUid",
    )
    if command["target"] != {
        "type": "PHOTO_GRANT_REQUEST",
        "uid": request_uid,
    }:
        raise ValueError("photo grant target differs from request")
    work_type = payload["workType"]
    slots = WORK_PHOTO_SLOTS.get(work_type)
    authorized_slots = payload["authorizedSlots"]
    if (
        slots is None
        or not isinstance(authorized_slots, list)
        or len(authorized_slots) != len(slots)
        or set(authorized_slots) != set(slots)
    ):
        raise ValueError("photo grant authorizedSlots are invalid")
    validate_cos_grant(
        command.get("cosGrant"),
        device_name=command["targetDeviceName"],
        work_type=work_type,
        work_uid=payload["workUid"],
        trusted_environment=trusted_environment,
    )


def _validate_device_acceptance_command(
    command: dict[str, Any],
    *,
    trusted_environment: dict[str, str] | None = None,
) -> None:
    payload = command["payload"]
    if set(payload) != {
        "challengeUid",
        "expectedPortCount",
        "deviceEntryUrl",
        "deviceEntryUrlSha256",
    }:
        raise ValueError("acceptance challenge payload fields are invalid")
    challenge_uid = _require_uuid4(
        payload["challengeUid"], "challengeUid"
    )
    expected_port_count = payload["expectedPortCount"]
    if (
        isinstance(expected_port_count, bool)
        or not isinstance(expected_port_count, int)
        or not 1 <= expected_port_count <= 6
    ):
        raise ValueError("expectedPortCount is outside 1..6")
    _validate_device_entry_url_payload(
        {
            "deviceEntryUrl": payload["deviceEntryUrl"],
            "deviceEntryUrlSha256": payload["deviceEntryUrlSha256"],
        }
    )
    validate_cos_grant(
        command.get("cosGrant"),
        device_name=command["targetDeviceName"],
        work_type="DEVICE_ACCEPTANCE",
        work_uid=challenge_uid,
        trusted_environment=trusted_environment,
    )


def _validate_device_entry_url_payload(payload: dict[str, Any]) -> None:
    if set(payload) != {"deviceEntryUrl", "deviceEntryUrlSha256"}:
        raise ValueError("device entry URL payload fields are invalid")
    url = payload.get("deviceEntryUrl")
    digest = payload.get("deviceEntryUrlSha256")
    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 192
        or any(
            ord(character) < 0x21 or ord(character) > 0x7E
            for character in url
        )
        or not url.startswith("https://")
    ):
        raise ValueError("deviceEntryUrl must be printable ASCII HTTPS within 192 bytes")
    if not _is_sha256(digest):
        raise ValueError("deviceEntryUrlSha256 is invalid")
    if hashlib.sha256(url.encode("ascii")).hexdigest() != digest:
        raise ValueError("deviceEntryUrlSha256 mismatch")


def encode_command_receipt(command_uid: str, receipt_state: str, edge_boot_id: int,
                           error_code: str | None = None) -> dict[str, Any]:
    return {
        # OneNet 枚举在线上使用编号；编号 1 对应语义协议版本 "2"。
        "schemaVersion": 1,
        "commandUid": command_uid,
        "receiptState": RECEIPT_STATE_TO_CODE[receipt_state],
        "errorCodePresent": bool(error_code),
        "errorCode": error_code or "",
        "edgeBootId": edge_boot_id,
    }


def build_business_confirmation_receipt(
    device_name: str,
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
        "schemaVersion": 2,
        "eventUid": str(uuid.uuid4()),
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
    device_name: str,
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
        "schemaVersion": 2,
        "eventUid": str(uuid.uuid4()),
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


def build_event_envelope(
    *,
    event_uid: str,
    device_name: str,
    edge_event_sequence: int,
    event_type: str,
    target_type: str,
    target_uid: str,
    payload: dict[str, Any],
    command_uid: str | None = None,
    delivery_class: str = "RELIABLE_FACT",
) -> dict[str, Any]:
    """Build the stable reliable envelope persisted before OneNet publish."""
    return {
        "schemaVersion": 2,
        "eventUid": event_uid,
        "edgeEventSequence": edge_event_sequence,
        "eventType": event_type,
        "deliveryClass": delivery_class,
        "target": {"type": target_type, "uid": target_uid},
        "commandUid": command_uid,
        "occurredAt": utc_now_rfc3339(),
        "clockQuality": "SYNCED",
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
    }


def encode_event_post(event_type: str, event: dict[str, Any]) -> dict[str, Any]:
    """Project an event envelope into OneNet OneJSON event/post payload."""

    projection = _EVENT_PROJECTIONS.get(event_type)
    if projection is None:
        raise ValueError(f"unsupported OneNet event type: {event_type}")
    transport_id = event.get("edgeEventSequence")
    if (
        isinstance(transport_id, bool)
        or not isinstance(transport_id, int)
        or not 1 <= transport_id <= 9_999_999_999_999
    ):
        raise ValueError(
            "edgeEventSequence must fit the OneNet 13-digit message id"
        )
    identifier, definition = projection
    value = _encode_function_parameters(
        definition["outputData"],
        definition["outputMappings"],
        event,
        _PROJECTION_MODEL.get("enumDisplay", {}),
    )

    return {
        "id": str(transport_id),
        "version": "1.0",
        "params": {identifier: {"value": value}},
    }


def _json_path_value(instance: Any, json_path: str) -> Any:
    if not json_path.startswith("$."):
        raise ValueError(f"unsupported generated JSON path {json_path!r}")
    current = instance
    for token in json_path[2:].split("."):
        if current is None:
            return None
        current = current[token]
    return current


def _upper_camel(value: str) -> str:
    return value[:1].upper() + value[1:]


def _flatten_json_members(
    value: Mapping[str, Any],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, child in value.items():
        identifier = f"{prefix}{_upper_camel(key)}" if prefix else key
        if isinstance(child, dict):
            flattened.update(_flatten_json_members(child, prefix=identifier))
        else:
            flattened[identifier] = child
    return flattened


def _one_net_placeholder(data_type: Mapping[str, Any]) -> Any:
    type_name = data_type["type"]
    specs = data_type["specs"]
    if type_name == "bool":
        return False
    if type_name == "string":
        return ""
    if type_name in {"int32", "int64"}:
        return int(specs["min"])
    if type_name == "enum":
        return int(next(iter(specs)))
    if type_name == "array":
        return []
    if type_name == "struct":
        return {
            member["identifier"]: _one_net_placeholder(member["dataType"])
            for member in specs
        }
    raise ValueError(f"unsupported OneNet placeholder type {type_name}")


def _encode_one_net_value(
    data_type: Mapping[str, Any],
    value: Any,
    enum_display: Mapping[str, str],
) -> Any:
    if value is None:
        return _one_net_placeholder(data_type)
    type_name = data_type["type"]
    specs = data_type["specs"]
    if type_name == "enum":
        expected = str(value)
        for wire_value, symbol in specs.items():
            if symbol == expected:
                return int(wire_value)
        abbreviated = enum_display.get(expected)
        if abbreviated is not None:
            for wire_value, symbol in specs.items():
                if symbol == abbreviated:
                    return int(wire_value)
        raise ValueError(
            f"enum symbol {value!r} is absent from generated OneNet model"
        )
    if type_name == "bool":
        return bool(value)
    if type_name in {"int32", "int64"}:
        return int(value)
    if type_name == "string":
        return str(value)
    if type_name == "struct":
        if not isinstance(value, dict):
            raise ValueError("OneNet struct source must be an object")
        flattened = _flatten_json_members(value)
        encoded: dict[str, Any] = {}
        for member in specs:
            identifier = member["identifier"]
            if identifier.endswith("Present"):
                source_name = identifier.removesuffix("Present")
                encoded[identifier] = flattened.get(source_name) is not None
            else:
                encoded[identifier] = _encode_one_net_value(
                    member["dataType"],
                    flattened.get(identifier),
                    enum_display,
                )
        return encoded
    if type_name == "array":
        if not isinstance(value, list):
            raise ValueError("OneNet array source must be a list")
        item_type = specs["type"]
        if item_type == "struct":
            item_descriptor = {"type": "struct", "specs": specs["specs"]}
        else:
            item_descriptor = {
                "type": item_type,
                "specs": {
                    key: child
                    for key, child in specs.items()
                    if key not in ("length", "type")
                },
            }
        return [
            _encode_one_net_value(item_descriptor, item, enum_display)
            for item in value
        ]
    raise ValueError(f"unsupported OneNet data type {type_name}")


def _encode_function_parameters(
    descriptors: list[Mapping[str, Any]],
    field_mappings: list[Mapping[str, Any]],
    instance: Mapping[str, Any],
    enum_display: Mapping[str, str],
) -> dict[str, Any]:
    mapping_by_identifier = {
        item["wireIdentifier"]: item for item in field_mappings
    }
    encoded: dict[str, Any] = {}
    for descriptor in descriptors:
        identifier = descriptor["identifier"]
        direct_mapping = mapping_by_identifier.get(identifier)
        if direct_mapping and direct_mapping.get("encoding") == "GROUPED_SCALARS":
            member_mappings = {
                member["wireIdentifier"]: member
                for member in direct_mapping["members"]
            }
            encoded_members: dict[str, Any] = {}
            for member_descriptor in descriptor["dataType"]["specs"]:
                member_identifier = member_descriptor["identifier"]
                member_mapping = member_mappings[member_identifier]
                source_value = _json_path_value(
                    instance,
                    member_mapping["jsonPath"],
                )
                if member_mapping["presenceFlag"]:
                    encoded_members[member_identifier] = source_value is not None
                else:
                    encoded_members[member_identifier] = _encode_one_net_value(
                        member_descriptor["dataType"],
                        source_value,
                        enum_display,
                    )
            encoded[identifier] = encoded_members
            continue
        source_identifier = (
            identifier.removesuffix("Present")
            if identifier.endswith("Present")
            else identifier
        )
        field_mapping = mapping_by_identifier[source_identifier]
        source_value = _json_path_value(instance, field_mapping["jsonPath"])
        if identifier.endswith("Present"):
            encoded[identifier] = source_value is not None
        else:
            encoded[identifier] = _encode_one_net_value(
                descriptor["dataType"],
                source_value,
                enum_display,
            )
    return encoded


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
            port["fullnessSensorKind"] = FULLNESS_SENSOR_KIND_BY_CODE.get(
                port.get("fullnessSensorKind"), port.get("fullnessSensorKind")
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
    if identifier == "requestDeviceAcceptance":
        return {
            "challengeUid": scalars.get("challengeUid"),
            "expectedPortCount": scalars.get("expectedPortCount"),
            "deviceEntryUrl": scalars.get("deviceEntryUrl"),
            "deviceEntryUrlSha256": scalars.get("deviceEntryUrlSha256"),
        }
    if identifier == "syncDeviceEntryUrl":
        return {
            "deviceEntryUrl": scalars.get("deviceEntryUrl"),
            "deviceEntryUrlSha256": scalars.get("deviceEntryUrlSha256"),
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
    presence = scalars.get("cosGrantPresent")
    if presence is False:
        return None
    grant_fields_present = any(
        scalars.get(field) is not None
        for field in (
            "cosGrantGrantUid",
            "cosGrantTmpSecretId",
            "cosGrantTmpSecretKey",
            "cosGrantBucket",
            "cosGrantRegion",
            "cosGrantBaseUrl",
            "cosGrantKeyPrefix",
            "cosGrantExpiresAt",
        )
    )
    token_parts = params.get("cosGrantSessionTokenParts") or []
    if presence is not True and not grant_fields_present and not token_parts:
        return None
    return {
        "grantUid": scalars.get("cosGrantGrantUid"),
        "tmpSecretId": scalars.get("cosGrantTmpSecretId"),
        "tmpSecretKey": scalars.get("cosGrantTmpSecretKey"),
        "sessionTokenParts": token_parts,
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
    result["type"] = RESULT_REFERENCE_TYPE_BY_CODE.get(
        result.get("type"),
        result.get("type"),
    )
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
        "REQUEST_DEVICE_ACCEPTANCE": "DEVICE_ASSET",
        "SYNC_DEVICE_ENTRY_URL": "DEVICE_ASSET",
    }
    return mapping.get(command_type, str(wire_value))


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
    heartbeat_interval = device_config.get(
        "edgeHeartbeatIntervalMs",
        3_600_000,
    )
    heartbeat_misses = device_config.get(
        "edgeHeartbeatMissThreshold",
        3,
    )
    for field, value in (
        ("edgeHeartbeatIntervalMs", heartbeat_interval),
        ("edgeHeartbeatMissThreshold", heartbeat_misses),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 4_294_967_295
        ):
            raise ValueError(f"deviceConfig.{field} out of range")
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
