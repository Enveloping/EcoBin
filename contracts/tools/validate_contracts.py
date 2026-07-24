"""Validate EcoBin F-10 OneNet and UART machine contracts."""

from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Mapping


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from contractlib import (  # noqa: E402
    CONTRACTS_ROOT,
    ContractError,
    JsonSchemaSubsetValidator,
    UartStreamParser,
    canonical_json_bytes,
    decode_uart_frame,
    decode_uart_payload,
    encode_uart_frame,
    encode_uart_payload,
    load_json,
    load_uart_registry,
    onenet_command_canonical_preimage,
    onenet_command_canonical_sha256,
    onenet_event_canonical_preimage,
    onenet_event_canonical_sha256,
    parse_json_text,
    payload_sha256,
    source_sha256,
    uart_command_digest_preimage,
    uart_message_specs,
    validate_uart_registry,
)
from generate_contracts import apply_outputs, build_outputs  # noqa: E402


class ValidationSummary:
    def __init__(self) -> None:
        self.checks: list[str] = []
        self.notes: list[str] = []

    def passed(self, message: str) -> None:
        self.checks.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


WORK_PHOTO_SLOTS = {
    "DELIVERY_SESSION": (
        "BEFORE_INNER",
        "BEFORE_OUTER",
        "AFTER_INNER",
        "AFTER_OUTER",
    ),
    "CLEAN_OPERATION": (
        "FIRST_OPEN_INNER",
        "FIRST_OPEN_OUTER",
        "FINAL_CLOSE_INNER",
        "FINAL_CLOSE_OUTER",
    ),
}

WORK_PATH_SEGMENT = {
    "DELIVERY_SESSION": "delivery-session",
    "CLEAN_OPERATION": "clean-operation",
}


def _validate_ordered_subset(
    values: list[str],
    expected_order: tuple[str, ...],
    label: str,
) -> None:
    expected_subset = [value for value in expected_order if value in values]
    if values != expected_subset:
        raise ContractError(f"{label}: slots must use registered canonical order")


def _validate_photo_url(
    photo: Mapping[str, Any],
    *,
    deployment_code: str,
    work_type: str,
    work_uid: str,
    trusted_cos: Mapping[str, Any],
) -> None:
    if photo["status"] != "AVAILABLE":
        return
    parsed = urllib.parse.urlsplit(photo["url"])
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ContractError("AVAILABLE photo URL must be credential-free HTTPS")
    trusted_base = urllib.parse.urlsplit(trusted_cos["baseUrl"])
    if (
        parsed.scheme != trusted_base.scheme
        or parsed.netloc != trusted_base.netloc
        or trusted_base.path not in {"", "/"}
        or trusted_base.query
        or trusted_base.fragment
    ):
        raise ContractError("AVAILABLE photo URL origin differs from trusted COS environment")
    expected_path = (
        f"/ecobin/{deployment_code}/{WORK_PATH_SEGMENT[work_type]}/{work_uid}/"
        f"{photo['slot']}/{photo['photoUid']}.jpg"
    )
    if parsed.path != expected_path:
        raise ContractError("AVAILABLE photo URL path differs from registered identity")


def _validate_cos_grant(
    command: Mapping[str, Any],
    *,
    work_type: str,
    work_uid: str,
    trusted_cos: Mapping[str, Any],
) -> None:
    grant = command.get("cosGrant")
    if grant is None:
        return
    for field in ("bucket", "region", "baseUrl"):
        if grant[field] != trusted_cos[field]:
            raise ContractError(
                f"{command['commandType']}: COS {field} differs from trusted environment"
            )
    expected_prefix = (
        f"ecobin/{command['deploymentCode']}/{WORK_PATH_SEGMENT[work_type]}/"
        f"{work_uid}/"
    )
    if grant["keyPrefix"] != expected_prefix:
        raise ContractError(
            f"{command['commandType']}: COS keyPrefix differs from deployment/work"
        )


def _walk_values(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_values(child, f"{path}[{index}]")


def _ensure_no_float(value: Any, label: str) -> None:
    for path, child in _walk_values(value):
        if isinstance(child, float):
            raise ContractError(f"{label}{path}: floating-point value is forbidden")


def _resolve_schema_ref(base_path: Path, ref: str) -> tuple[Path, Any]:
    if ref.startswith("http://") or ref.startswith("https://"):
        raise ContractError(f"remote $ref is forbidden: {ref}")
    file_part, separator, fragment = ref.partition("#")
    path = (base_path.parent / file_part).resolve() if file_part else base_path.resolve()
    target = load_json(path)
    if separator and fragment:
        if not fragment.startswith("/"):
            raise ContractError(f"unsupported $ref fragment: {ref}")
        for raw in fragment[1:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or token not in target:
                raise ContractError(f"unresolved $ref: {ref}")
            target = target[token]
    return path, target


def _validate_all_schema_refs(schema_paths: list[Path]) -> None:
    seen: set[tuple[Path, str]] = set()

    def visit(node: Any, current_path: Path) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                key = (current_path.resolve(), node["$ref"])
                if key not in seen:
                    seen.add(key)
                    target_path, target = _resolve_schema_ref(current_path, node["$ref"])
                    visit(target, target_path)
            for child in node.values():
                visit(child, current_path)
        elif isinstance(node, list):
            for child in node:
                visit(child, current_path)

    for schema_path in schema_paths:
        visit(load_json(schema_path), schema_path)


def validate_sources(summary: ValidationSummary) -> None:
    schema_paths = sorted((CONTRACTS_ROOT / "onenet").rglob("*.schema.json"))
    schema_paths.append(CONTRACTS_ROOT / "uart" / "uart-registry.schema.json")
    ids: list[str] = []
    for path in schema_paths:
        schema = load_json(path)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ContractError(f"{path}: Draft 2020-12 declaration is required")
        if "$id" in schema:
            ids.append(schema["$id"])
    if len(ids) != len(set(ids)):
        raise ContractError("JSON Schema $id values must be unique")
    _validate_all_schema_refs(schema_paths)
    summary.passed(f"{len(schema_paths)} JSON Schema sources parse and all local $ref resolve")

    registry = load_uart_registry()
    report = validate_uart_registry(registry)
    if not report:
        raise ContractError("UART registry validation produced no messages")
    summary.passed(
        f"UART Registry has {len(report)} unique bounded messages and valid enums/capabilities"
    )

    mapping = load_json(CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml")
    common = load_json(CONTRACTS_ROOT / "onenet" / "common.schema.json")
    event_envelope = load_json(CONTRACTS_ROOT / "onenet" / "event-envelope.schema.json")
    command_envelope = load_json(CONTRACTS_ROOT / "onenet" / "command-envelope.schema.json")
    mapped_events = {item["eventType"] for item in mapping["events"].values()}
    mapped_commands = {item["commandType"] for item in mapping["services"].values()}
    schema_events = set(event_envelope["properties"]["eventType"]["enum"])
    schema_commands = set(command_envelope["properties"]["commandType"]["enum"])
    if mapped_events != schema_events:
        raise ContractError(
            f"OneNet event mapping differs from envelope: {mapped_events ^ schema_events}"
        )
    if mapped_commands != schema_commands:
        raise ContractError(
            f"OneNet command mapping differs from envelope: "
            f"{mapped_commands ^ schema_commands}"
        )
    known_target_types = set(
        common["$defs"]["target"]["properties"]["type"]["enum"]
    )
    for collection in ("events", "services"):
        for identifier, definition in mapping[collection].items():
            _resolve_schema_ref(
                CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml",
                definition["schemaRef"],
            )
            if not identifier or not identifier[0].islower():
                raise ContractError(f"OneNet identifier must be lowerCamelCase: {identifier}")
            has_single_target = "targetType" in definition
            has_multiple_targets = "targetTypes" in definition
            if has_single_target == has_multiple_targets:
                raise ContractError(
                    f"{identifier}: define exactly one of targetType or targetTypes"
                )
            target_types = (
                [definition["targetType"]]
                if has_single_target
                else definition["targetTypes"]
            )
            if (
                not target_types
                or len(target_types) != len(set(target_types))
                or not set(target_types) <= known_target_types
            ):
                raise ContractError(
                    f"{identifier}: unknown or duplicate target type {target_types}"
                )
    summary.passed(
        f"OneNet mapping covers {len(mapped_commands)} commands and "
        f"{len(mapped_events)} events exactly"
    )


def validate_onenet_thing_model(summary: ValidationSummary) -> None:
    mapping = load_json(CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml")
    model = load_json(
        CONTRACTS_ROOT
        / "onenet"
        / "generated"
        / "onenet-thing-model.candidate.json"
    )
    wire_mapping = load_json(
        CONTRACTS_ROOT
        / "onenet"
        / "generated"
        / "onenet-wire-mapping.json"
    )
    functions = [*model["properties"], *model["services"], *model["events"]]
    expected_identifiers = {
        *mapping["services"].keys(),
        *mapping["events"].keys(),
    }
    identifiers = [item["identifier"] for item in functions]
    if len(functions) > mapping["oneNetProjection"]["functionPointLimit"]:
        raise ContractError("OneNet candidate exceeds the documented function point limit")
    if len(identifiers) != len(set(identifiers)):
        raise ContractError("OneNet candidate has duplicate function identifiers")
    if set(identifiers) != expected_identifiers:
        raise ContractError("OneNet candidate functions differ from mapping registry")
    if set(wire_mapping["functions"]) != expected_identifiers:
        raise ContractError("OneNet wire projection differs from mapping registry")

    allowed_primitive_types = {"int32", "int64", "string", "bool", "enum"}

    def validate_descriptor(
        descriptor: Mapping[str, Any],
        *,
        context: str,
        inside_struct: bool = False,
    ) -> None:
        identifier = descriptor["identifier"]
        name = descriptor["name"]
        if not identifier or not identifier[0].islower():
            raise ContractError(f"{context}: invalid identifier {identifier!r}")
        if len(identifier) > 50:
            raise ContractError(
                f"{context}.{identifier}: OneNet identifier exceeds 50 chars"
            )
        if not 1 <= len(name) <= 30:
            raise ContractError(f"{context}.{identifier}: OneNet name exceeds 30 chars")
        data_type = descriptor["dataType"]
        type_name = data_type["type"]
        if type_name in {"float", "double"}:
            raise ContractError(f"{context}.{identifier}: floating point is forbidden")
        if type_name in allowed_primitive_types:
            if type_name == "string":
                length_value = data_type["specs"].get("length")
                if not isinstance(length_value, int) or not 1 <= length_value <= 512:
                    raise ContractError(
                        f"{context}.{identifier}: string length must be integer 1..512"
                    )
            return
        if type_name == "struct":
            if inside_struct:
                raise ContractError(f"{context}.{identifier}: nested struct is forbidden")
            members = data_type["specs"]
            if len(members) > 20:
                raise ContractError(
                    f"{context}.{identifier}: struct exceeds 20 members"
                )
            member_ids = [member["identifier"] for member in members]
            if len(member_ids) != len(set(member_ids)):
                raise ContractError(f"{context}.{identifier}: duplicate struct member")
            for member in members:
                validate_descriptor(
                    member,
                    context=f"{context}.{identifier}",
                    inside_struct=True,
                )
            return
        if type_name == "array":
            if inside_struct:
                raise ContractError(
                    f"{context}.{identifier}: struct member array is forbidden"
                )
            specs = data_type["specs"]
            if not isinstance(specs.get("length"), int) or specs["length"] < 1:
                raise ContractError(f"{context}.{identifier}: array length is invalid")
            item_type = specs.get("type")
            if item_type is None:
                raise ContractError(f"{context}.{identifier}: array item must have type")
            if item_type == "struct":
                members = specs["specs"]
                for member in members:
                    validate_descriptor(
                        member,
                        context=f"{context}.{identifier}[]",
                        inside_struct=True,
                    )
            elif item_type not in allowed_primitive_types:
                raise ContractError(
                    f"{context}.{identifier}: unsupported array item {item_type}"
                )
            return
        raise ContractError(f"{context}.{identifier}: unsupported OneNet type {type_name}")

    receipt_identifiers = {
        "schemaVersion",
        "commandUid",
        "receiptState",
        "errorCodePresent",
        "errorCode",
        "edgeBootId",
    }
    for service in model["services"]:
        if service["callType"] != "sync":
            raise ContractError(f"{service['identifier']}: immediate receipt needs sync call")
        if len(service["input"]) > 20 or len(service["output"]) > 20:
            raise ContractError(
                f"{service['identifier']}: OneNet service parameter limit exceeded"
            )
        if {item["identifier"] for item in service["output"]} != receipt_identifiers:
            raise ContractError(f"{service['identifier']}: receipt output fields differ")
        for collection_name in ("input", "output"):
            descriptors = service[collection_name]
            ids = [item["identifier"] for item in descriptors]
            if len(ids) != len(set(ids)):
                raise ContractError(
                    f"{service['identifier']}: duplicate {collection_name} identifier"
                )
            for descriptor in descriptors:
                validate_descriptor(
                    descriptor,
                    context=f"service.{service['identifier']}.{collection_name}",
                )
    for event in model["events"]:
        descriptors = event["outputData"]
        if len(descriptors) > 50:
            raise ContractError(
                f"{event['identifier']}: OneNet event parameter limit exceeded"
            )
        ids = [item["identifier"] for item in descriptors]
        if len(ids) != len(set(ids)):
            raise ContractError(f"{event['identifier']}: duplicate event parameter")
        for descriptor in descriptors:
            validate_descriptor(
                descriptor,
                context=f"event.{event['identifier']}",
            )
    summary.passed(
        f"OneNet import candidate has {len(functions)} typed function points, "
        "sync receipts and no unsupported nested struct/float"
    )


def validate_onenet_wire_examples(summary: ValidationSummary) -> None:
    root = CONTRACTS_ROOT / "examples" / "onenet-wire"
    manifest = load_json(root / "manifest.json")
    model = load_json(
        CONTRACTS_ROOT
        / "onenet"
        / "generated"
        / "onenet-thing-model.candidate.json"
    )
    services = {
        service["identifier"]: service for service in model["services"]
    }
    events = {event["identifier"]: event for event in model["events"]}
    expected_count = len(services) + len(events)
    if len(manifest["examples"]) != expected_count:
        raise ContractError("OneNet wire samples must cover every service and event")

    def validate_bare_type(type_name: str, value: Any, path: str) -> None:
        if type_name == "bool" and not isinstance(value, bool):
            raise ContractError(f"{path}: expected boolean")
        if type_name in {"int32", "int64"} and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ContractError(f"{path}: expected integer")
        if type_name == "enum" and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ContractError(f"{path}: expected integer enum")
        if type_name == "string" and not isinstance(value, str):
            raise ContractError(f"{path}: expected string")

    def validate_value(data_type: Mapping[str, Any], value: Any, path: str) -> None:
        type_name = data_type["type"]
        specs = data_type["specs"]
        if not specs:
            return validate_bare_type(type_name, value, path)
        if type_name == "bool":
            if not isinstance(value, bool):
                raise ContractError(f"{path}: expected bool")
        elif type_name in {"int32", "int64"}:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(f"{path}: expected integer")
            if "min" in specs and "max" in specs:
                if not int(specs["min"]) <= value <= int(specs["max"]):
                    raise ContractError(f"{path}: integer outside OneNet range")
        elif type_name == "enum":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(f"{path}: expected integer enum")
            if specs and str(value) not in specs:
                raise ContractError(f"{path}: unknown OneNet enum value")
        elif type_name == "string":
            if not isinstance(value, str):
                raise ContractError(f"{path}: expected string")
            if "length" in specs:
                if not isinstance(specs["length"], int) or len(value) > specs["length"]:
                    raise ContractError(f"{path}: invalid OneNet string")
        elif type_name == "struct":
            if not isinstance(value, dict):
                raise ContractError(f"{path}: expected struct object")
            expected = {member["identifier"] for member in specs}
            if set(value) != expected:
                raise ContractError(f"{path}: struct fields differ from model")
            for member in specs:
                validate_value(
                    member["dataType"],
                    value[member["identifier"]],
                    f"{path}.{member['identifier']}",
                )
        elif type_name == "array":
            if not isinstance(value, list):
                raise ContractError(f"{path}: invalid OneNet array")
            item_type = specs["type"]
            if item_type == "struct":
                item_data_type = {"type": "struct", "specs": specs["specs"]}
            else:
                item_data_type = {
                    "type": item_type,
                    "specs": {k: v for k, v in specs.items() if k not in ("length", "type")},
                }
            for index, item in enumerate(value):
                validate_value(item_data_type, item, f"{path}[{index}]")
        else:
            raise ContractError(f"{path}: unsupported OneNet type {type_name}")

    def validate_parameters(
        descriptors: list[Mapping[str, Any]],
        values: Mapping[str, Any],
        path: str,
    ) -> None:
        expected = {descriptor["identifier"] for descriptor in descriptors}
        if set(values) != expected:
            raise ContractError(f"{path}: parameters differ from imported model")
        for descriptor in descriptors:
            validate_value(
                descriptor["dataType"],
                values[descriptor["identifier"]],
                f"{path}.{descriptor['identifier']}",
            )

    covered: set[str] = set()
    for filename in manifest["examples"]:
        example = load_json(root / filename)
        identifier = example["identifier"]
        if identifier in covered:
            raise ContractError(f"duplicate OneNet wire sample {identifier}")
        covered.add(identifier)
        if example["kind"] == "SYNC_SERVICE":
            service = services[identifier]
            body = example["callServiceApiBodyTemplate"]
            if body["identifier"] != identifier:
                raise ContractError(f"{filename}: service body identifier mismatch")
            validate_parameters(service["input"], body["params"], filename)
            reply = example["deviceAcceptedReplyTemplate"]
            if reply["code"] != 200:
                raise ContractError(f"{filename}: accepted reply must use code 200")
            validate_parameters(service["output"], reply["data"], f"{filename}.reply")
        elif example["kind"] == "EVENT":
            event = events[identifier]
            payload = example["oneJsonPayload"]
            if (
                not payload["id"].isdigit()
                or not 1 <= len(payload["id"]) <= 13
                or payload["version"] != "1.0"
            ):
                raise ContractError(f"{filename}: invalid OneJSON message envelope")
            event_wrapper = payload["params"]
            if set(event_wrapper) != {identifier}:
                raise ContractError(f"{filename}: event identifier mismatch")
            validate_parameters(
                event["outputData"],
                event_wrapper[identifier]["value"],
                filename,
            )
        else:
            raise ContractError(f"{filename}: unknown OneNet wire sample kind")
    if covered != {*services, *events}:
        raise ContractError("OneNet wire samples do not cover the imported model")
    summary.passed(
        f"{len(covered)} OneNet service/event wire samples match the import candidate"
    )


def _validate_event_semantics(instance: Mapping[str, Any], mapping: Mapping[str, Any]) -> None:
    event_type = instance["eventType"]
    definition = next(
        item for item in mapping["events"].values() if item["eventType"] == event_type
    )
    if instance["deliveryClass"] != definition["deliveryClass"]:
        raise ContractError(f"{event_type}: deliveryClass downgrade or mismatch")
    allowed_target_types = set(
        definition.get("targetTypes", [definition.get("targetType")])
    )
    if instance["target"]["type"] not in allowed_target_types:
        raise ContractError(f"{event_type}: target type is not registered")
    if payload_sha256(instance["payload"]) != instance["payloadSha256"]:
        raise ContractError(f"{event_type}: payloadSha256 mismatch")
    payload = instance["payload"]

    uid_field = {
        "DELIVERY_COMPLETE": "sessionUid",
        "CLEAN_COMPLETE": "operationUid",
        "FULLNESS_SAMPLE_COMPLETE": "detectionUid",
        "BASELINE_MEASUREMENT_COMPLETE": "measurementUid",
        "CONFIGURATION_PROGRESS": "applicationUid",
    }.get(event_type)
    if uid_field and instance["target"]["uid"] != payload[uid_field]:
        raise ContractError(f"{event_type}: target UID differs from payload")

    command_bound_events = {
        "DEVICE_COMMAND_OBSERVED",
        "CONFIGURATION_PROGRESS",
        "DELIVERY_COMPLETE",
        "CLEAN_COMPLETE",
        "FULLNESS_SAMPLE_COMPLETE",
        "BASELINE_MEASUREMENT_COMPLETE",
        "BUSINESS_CONFIRMATION_RECEIPT",
    }
    if event_type in command_bound_events and instance["commandUid"] is None:
        raise ContractError(f"{event_type}: originating commandUid is required")
    if (
        event_type == "DEVICE_COMMAND_OBSERVED"
        and instance["target"]["uid"] != instance["commandUid"]
    ):
        raise ContractError("DEVICE_COMMAND_OBSERVED target must be the commandUid")

    if event_type == "DELIVERY_COMPLETE":
        slots = [photo["slot"] for photo in payload["photos"]]
        expected = WORK_PHOTO_SLOTS["DELIVERY_SESSION"]
        if tuple(slots) != expected:
            raise ContractError(
                "DELIVERY_COMPLETE must contain four session slots in canonical order"
            )
        forbidden = {
            "rounds",
            "cycles",
            "cycleUid",
            "intermediateWeights",
            "negativeWeightTriggerGrams",
        }
        if forbidden & set(payload):
            raise ContractError("DELIVERY_COMPLETE leaks intermediate delivery data")
        first = payload["firstPreOpenMeasurement"]
        final = payload["finalPostCloseMeasurement"]
        expected_net = (
            final["stableWeightGrams"] - first["stableWeightGrams"]
            if first["status"] == "STABLE" and final["status"] == "STABLE"
            else None
        )
        if payload["deliveryNetWeightGrams"] != expected_net:
            raise ContractError("DELIVERY_COMPLETE net weight is not whole-session delta")
        successful_reasons = {"USER_ENDED", "SELECTION_WINDOW_EXPIRED"}
        if (
            payload["completionReason"] in successful_reasons
            and final["status"] != "STABLE"
        ):
            raise ContractError("normal delivery completion requires stable final weight")
        if (
            payload["completionReason"] == "TERMINAL_WEIGHT_FAILURE"
            and final["status"] == "STABLE"
        ):
            raise ContractError("terminal weight failure cannot carry a stable final weight")
        if (
            payload["finalDeliveryDoor"]["state"] != "CLOSED"
            or payload["finalDeliveryDoor"]["health"] != "OK"
        ):
            raise ContractError("DELIVERY_COMPLETE requires a proven closed delivery door")
        for photo in payload["photos"]:
            _validate_photo_url(
                photo,
                deployment_code=instance["deploymentCode"],
                work_type="DELIVERY_SESSION",
                work_uid=payload["sessionUid"],
                trusted_cos=mapping["trustedCosEnvironment"]["contractTestProfile"],
            )

    if event_type == "CLEAN_COMPLETE":
        slots = [photo["slot"] for photo in payload["photos"]]
        expected = WORK_PHOTO_SLOTS["CLEAN_OPERATION"]
        if tuple(slots) != expected:
            raise ContractError(
                "CLEAN_COMPLETE must contain four cleaning slots in canonical order"
            )
        lock = payload["cleanLockAndInferredDoor"]
        if (
            not payload["cleanerCompletionConfirmed"]
            or lock["lockPowerState"] != "DEENERGIZED"
            or lock["inferredDoorState"] != "CLOSED"
            or lock["stateBasis"] != "INFERRED_FROM_LOCK_POWER"
        ):
            raise ContractError("CLEAN_COMPLETE violates manual-close/lock inference boundary")
        if any("magnet" in key.lower() for key in lock):
            raise ContractError("CLEAN_COMPLETE must not expose a cleaning-door sensor")
        first = payload["preUnlockMeasurement"]
        final = payload["cleanerConfirmedFinalMeasurement"]
        if first["status"] != "STABLE":
            raise ContractError("CLEAN_COMPLETE pre-unlock measurement must be stable")
        if final["status"] == "STABLE":
            expected_removed = first["stableWeightGrams"] - final["stableWeightGrams"]
            if payload["removedNetWeightGrams"] != expected_removed:
                raise ContractError("CLEAN_COMPLETE removed weight differs from measurements")
            if payload["newBaselineWeightGrams"] != final["stableWeightGrams"]:
                raise ContractError("CLEAN_COMPLETE new baseline differs from final weight")
        elif (
            payload["removedNetWeightGrams"] is not None
            or payload["newBaselineWeightGrams"] is not None
        ):
            raise ContractError("failed final clean measurement cannot establish weights")
        for photo in payload["photos"]:
            _validate_photo_url(
                photo,
                deployment_code=instance["deploymentCode"],
                work_type="CLEAN_OPERATION",
                work_uid=payload["operationUid"],
                trusted_cos=mapping["trustedCosEnvironment"]["contractTestProfile"],
            )

    if event_type == "BUSINESS_CONFIRMATION_RECEIPT":
        if instance["target"]["uid"] != payload["confirmationUid"]:
            raise ContractError("confirmation receipt target differs from payload")

    if event_type in {"PHOTO_STATUS_REPORTED", "PHOTO_UPLOAD_GRANT_REQUESTED"}:
        if (
            instance["target"]["type"] != payload["workType"]
            or instance["target"]["uid"] != payload["workUid"]
        ):
            raise ContractError(f"{event_type}: target differs from original work")
        expected_slots = WORK_PHOTO_SLOTS[payload["workType"]]
        if event_type == "PHOTO_STATUS_REPORTED":
            photo = payload["photo"]
            if photo["slot"] not in expected_slots:
                raise ContractError("PHOTO_STATUS_REPORTED uses a slot from another work type")
            if photo["status"] == "UPLOAD_PENDING":
                raise ContractError("PHOTO_STATUS_REPORTED must be a terminal photo update")
            _validate_photo_url(
                photo,
                deployment_code=instance["deploymentCode"],
                work_type=payload["workType"],
                work_uid=payload["workUid"],
                trusted_cos=mapping["trustedCosEnvironment"]["contractTestProfile"],
            )
        else:
            _validate_ordered_subset(
                payload["requestedSlots"],
                expected_slots,
                "PHOTO_UPLOAD_GRANT_REQUESTED",
            )

    if event_type in {"DEVICE_FAULT_OBSERVED", "DEVICE_FAULT_RECOVERED"}:
        if instance["target"]["uid"] != instance["deploymentCode"]:
            raise ContractError(f"{event_type}: deployment target differs from envelope")
        fault_codes_by_component = {
            "UART": {"UART_PROTOCOL", "UART_STORAGE"},
            "DELIVERY_DOOR": {
                "DELIVERY_DOOR_TIMEOUT",
                "DELIVERY_DOOR_ACTUATOR",
                "DELIVERY_DOOR_SWITCH",
            },
            "CLEAN_SOLENOID": {"CLEAN_SOLENOID_DRIVER"},
            "WEIGHT_SENSOR": {
                "WEIGHT_UNSTABLE",
                "WEIGHT_TIMEOUT",
                "WEIGHT_SENSOR",
                "WEIGHT_OVERLOAD",
            },
            "INFRARED_SENSOR": {"INFRARED_TIMEOUT", "INFRARED_SENSOR"},
            "SMOKE_SENSOR": {"SMOKE_SENSOR"},
            "MCU_STORAGE": {"MCU_STORAGE"},
            "MCU_INTERNAL": {"MCU_INTERNAL"},
            "EDGE_STORAGE": {"EDGE_STORAGE"},
            "CAMERA": {"CAMERA_CAPTURE", "CAMERA_STORAGE"},
            "NETWORK": {"NETWORK_CONNECTIVITY"},
            "CLOCK": {"CLOCK_UNSYNCED"},
        }
        if payload["faultCode"] not in fault_codes_by_component[payload["component"]]:
            raise ContractError(f"{event_type}: fault code/component mismatch")
        if (payload["mcuBootId"] is None) != (payload["mcuEventSequence"] is None):
            raise ContractError(f"{event_type}: MCU boot and event sequence form one identity")

    if event_type == "DEVICE_RUNTIME_SNAPSHOT":
        if instance["target"]["uid"] != instance["deploymentCode"]:
            raise ContractError(
                "DEVICE_RUNTIME_SNAPSHOT target differs from current deployment"
            )
        ports = [port["portNo"] for port in payload["ports"]]
        if ports != list(range(1, len(ports) + 1)):
            raise ContractError(
                "DEVICE_RUNTIME_SNAPSHOT ports must be unique, ordered and contiguous"
            )


def _validate_command_semantics(
    instance: Mapping[str, Any],
    mapping: Mapping[str, Any] | None = None,
) -> None:
    if mapping is None:
        mapping = load_json(
            CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml"
        )
    if payload_sha256(instance["payload"]) != instance["payloadSha256"]:
        raise ContractError(f"{instance['commandType']}: payloadSha256 mismatch")
    issued = datetime.datetime.fromisoformat(
        instance["issuedAt"].removesuffix("Z") + "+00:00"
    )
    expires = datetime.datetime.fromisoformat(
        instance["expiresAt"].removesuffix("Z") + "+00:00"
    )
    if issued >= expires:
        raise ContractError(f"{instance['commandType']}: issuedAt must precede expiresAt")
    uid_field = {
        "APPLY_CONFIGURATION": "applicationUid",
        "START_DELIVERY_SESSION": "sessionUid",
        "START_CLEAN_OPERATION": "operationUid",
        "END_CLEAN_BEFORE_UNLOCK": "operationUid",
        "RESUME_CLEAN_OPERATION": "operationUid",
        "SAMPLE_FULLNESS": "detectionUid",
        "MEASURE_EMPTY_BAG_BASELINE": "measurementUid",
        "CONFIRM_EDGE_EVENT": "originalEventUid",
        "PROVIDE_PHOTO_UPLOAD_GRANT": "grantRequestEventUid",
    }[instance["commandType"]]
    if instance["target"]["uid"] != instance["payload"][uid_field]:
        raise ContractError(f"{instance['commandType']}: target UID differs from payload")

    command_type = instance["commandType"]
    payload = instance["payload"]
    if command_type == "APPLY_CONFIGURATION":
        ports = payload["ports"]
        port_numbers = [port["portNo"] for port in ports]
        if port_numbers != list(range(1, len(ports) + 1)):
            raise ContractError(
                "APPLY_CONFIGURATION ports must be unique, ordered and contiguous"
            )
        for port in ports:
            if port["weightMinimumGrams"] >= port["weightMaximumGrams"]:
                raise ContractError(
                    "APPLY_CONFIGURATION weightMinimumGrams must be below maximum"
                )

    work_type: str | None = None
    work_uid: str | None = None
    if command_type == "START_DELIVERY_SESSION":
        work_type = "DELIVERY_SESSION"
        work_uid = payload["sessionUid"]
    elif command_type in {"START_CLEAN_OPERATION", "RESUME_CLEAN_OPERATION"}:
        work_type = "CLEAN_OPERATION"
        work_uid = payload["operationUid"]
    elif command_type == "PROVIDE_PHOTO_UPLOAD_GRANT":
        work_type = payload["workType"]
        work_uid = payload["workUid"]
        expected_slots = WORK_PHOTO_SLOTS[work_type]
        _validate_ordered_subset(
            payload["authorizedSlots"],
            expected_slots,
            "PROVIDE_PHOTO_UPLOAD_GRANT",
        )
    if work_type is not None and work_uid is not None:
        _validate_cos_grant(
            instance,
            work_type=work_type,
            work_uid=work_uid,
            trusted_cos=mapping["trustedCosEnvironment"]["contractTestProfile"],
        )


def validate_onenet_examples(summary: ValidationSummary) -> None:
    validator = JsonSchemaSubsetValidator()
    examples_root = CONTRACTS_ROOT / "examples" / "onenet"
    manifest = load_json(examples_root / "manifest.json")
    mapping = load_json(CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml")
    event_count = 0
    command_count = 0
    for entry in manifest["examples"]:
        path = examples_root / entry["file"]
        schema_path = (examples_root / entry["schema"]).resolve()
        instance = load_json(path)
        _ensure_no_float(instance, entry["file"])
        validator.validate(instance, schema_path)
        if "eventType" in instance:
            _validate_event_semantics(instance, mapping)
            event_count += 1
        elif "commandType" in instance:
            _validate_command_semantics(instance, mapping)
            command_count += 1
    cos_profile = mapping["trustedCosEnvironment"]["contractTestProfile"]
    if set(cos_profile) != {"bucket", "region", "baseUrl"}:
        raise ContractError("trusted COS contract profile must define exact environment")
    command_probe = load_json(examples_root / "start-clean-operation.command.json")
    command_probe = copy.deepcopy(command_probe)
    command_probe["cosGrant"]["baseUrl"] = "https://attacker.example"
    try:
        _validate_command_semantics(command_probe, mapping)
    except ContractError:
        pass
    else:
        raise ContractError("untrusted COS grant environment was accepted")
    event_probe = load_json(examples_root / "photo-status-reported.event.json")
    event_probe = copy.deepcopy(event_probe)
    event_probe["payload"]["photo"]["url"] = event_probe["payload"]["photo"][
        "url"
    ].replace(cos_profile["baseUrl"], "https://attacker.example")
    event_probe["payloadSha256"] = payload_sha256(event_probe["payload"])
    try:
        _validate_event_semantics(event_probe, mapping)
    except ContractError:
        pass
    else:
        raise ContractError("untrusted COS photo origin was accepted")
    summary.passed("trusted COS bucket/region/baseUrl and photo origin fail closed")
    summary.passed(
        f"{len(manifest['examples'])} OneNet examples validate "
        f"({command_count} commands, {event_count} events/receipts)"
    )

    vectors = load_json(examples_root / "canonicalization-vectors.json")
    for vector in vectors["vectors"]:
        canonical = canonical_json_bytes(vector["payload"])
        if canonical.hex() != vector["canonicalUtf8Hex"]:
            raise ContractError(f"{vector['name']}: canonical bytes differ")
        if payload_sha256(vector["payload"]) != vector["sha256"]:
            raise ContractError(f"{vector['name']}: canonical SHA-256 differs")
    summary.passed(
        f"{len(vectors['vectors'])} OneNet canonicalization vectors reproduce exactly"
    )

    identity_vectors = load_json(examples_root / "stable-identity-vectors.json")
    command_digests: dict[str, str] = {}
    event_digests: dict[str, str] = {}
    for vector in identity_vectors["vectors"]:
        if vector["kind"] == "COMMAND":
            preimage = onenet_command_canonical_preimage(vector["input"])
            digest = onenet_command_canonical_sha256(vector["input"])
            command_digests[vector["name"]] = digest
        elif vector["kind"] == "EVENT":
            source = vector["trustedSource"]
            preimage = onenet_event_canonical_preimage(
                vector["input"],
                trusted_product_id=source["productId"],
                trusted_device_name=source["deviceName"],
            )
            digest = onenet_event_canonical_sha256(
                vector["input"],
                trusted_product_id=source["productId"],
                trusted_device_name=source["deviceName"],
            )
            event_digests[vector["name"]] = digest
        else:
            raise ContractError(f"{vector['name']}: unknown identity vector kind")
        if preimage.hex() != vector["preimageHex"] or digest != vector["sha256"]:
            raise ContractError(f"{vector['name']}: stable identity vector differs")
    base = command_digests["command_without_cos_grant"]
    if not (
        base == command_digests["command_with_cos_grant_refresh_1"]
        == command_digests["command_with_cos_grant_refresh_2"]
    ):
        raise ContractError("COS credential refresh changed stable command digest")
    if (
        base == command_digests["command_target_changed"]
        or base == command_digests["command_expiry_changed"]
    ):
        raise ContractError("command target or expiry is absent from stable digest")
    if (
        event_digests["event_authenticated_source"]
        == event_digests["event_authenticated_device_changed"]
    ):
        raise ContractError("trusted OneNet device identity is absent from event digest")
    summary.passed(
        f"{len(identity_vectors['vectors'])} OneNet stable command/event identity "
        "vectors reproduce exactly"
    )

    rejected = 0
    for invalid in (0.5, 9007199254740992, "\ud800"):
        try:
            canonical_json_bytes(invalid)
        except ContractError:
            rejected += 1
    try:
        parse_json_text('{"same":1,"same":2}', "duplicate-key-vector")
    except ContractError:
        rejected += 1
    if rejected != 4:
        raise ContractError("JCS negative vectors were not all rejected")
    summary.passed("JCS rejects floats, unsafe integers, surrogates and duplicate keys")


def _load_generated_python() -> Any:
    path = (
        CONTRACTS_ROOT
        / "uart"
        / "generated"
        / "python"
        / "ecobin_uart_protocol.py"
    )
    spec = importlib.util.spec_from_file_location("ecobin_uart_generated", path)
    if spec is None or spec.loader is None:
        raise ContractError("cannot load generated Python UART module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_uart_vectors(summary: ValidationSummary) -> None:
    registry = load_uart_registry()
    specs = uart_message_specs(registry)
    vectors_document = load_json(
        CONTRACTS_ROOT / "examples" / "uart" / "golden-vectors.json"
    )
    if vectors_document["registrySha256"] != source_sha256(registry):
        raise ContractError("UART golden vectors use a stale registry digest")
    generated = _load_generated_python()
    if generated.REGISTRY_SHA256 != vectors_document["registrySha256"]:
        raise ContractError("generated Python uses a stale registry digest")

    known = {message["id"]: message["name"] for message in registry["messages"]}
    for vector in vectors_document["vectors"]:
        frame = bytes.fromhex(vector["frameHex"])
        if vector["expected"] == "CRC_INVALID":
            for decoder in (
                lambda: decode_uart_frame(registry, frame),
                lambda: generated.decode_frame(frame, require_known_message=False),
            ):
                try:
                    decoder()
                except ValueError:
                    pass
                else:
                    raise ContractError(f"{vector['name']}: invalid CRC was accepted")
            continue

        decoded = decode_uart_frame(registry, frame)
        generated_decoded = generated.decode_frame(
            frame,
            require_known_message=vector["expected"] == "VALID",
        )
        if decoded["messageType"] != generated_decoded["messageType"]:
            raise ContractError(f"{vector['name']}: generated Python type differs")
        reencoded = encode_uart_frame(
            registry,
            decoded["messageType"],
            decoded["flags"],
            decoded["txSequence"],
            decoded["payload"],
        )
        if reencoded != frame:
            raise ContractError(f"{vector['name']}: frame did not round-trip")
        if vector["expected"] == "VALID":
            message_name = known[decoded["messageType"]]
            values = decode_uart_payload(registry, message_name, decoded["payload"])
            payload = encode_uart_payload(registry, message_name, values)
            generated_values = generated.decode_payload(message_name, payload)
            generated_payload = generated.encode_payload(message_name, generated_values)
            if generated_payload != payload:
                raise ContractError(
                    f"{vector['name']}: generated Python payload did not round-trip"
                )
            expected_ack = specs[message_name]["ackRequired"]
            if bool(decoded["flags"] & 0x01) != expected_ack:
                raise ContractError(f"{vector['name']}: ACK_REQUIRED differs")
    summary.passed(
        f"{len(vectors_document['vectors'])} UART frame/CRC/payload vectors pass Python"
    )

    traces_document = load_json(
        CONTRACTS_ROOT / "examples" / "uart" / "stream-traces.json"
    )
    if traces_document["registrySha256"] != source_sha256(registry):
        raise ContractError("UART stream traces use a stale registry digest")
    for trace in traces_document["traces"]:
        reference = UartStreamParser(
            registry,
            sender_role=trace["senderRole"],
        )
        generated_parser = generated.StreamParser(sender_role=trace["senderRole"])
        reference_names: list[str] = []
        generated_names: list[str] = []
        for chunk in trace["chunks"]:
            raw = bytes.fromhex(chunk["hex"])
            reference_names.extend(
                frame["messageName"]
                for frame in reference.feed(raw, now_ms=chunk["atMs"])
            )
            generated_names.extend(
                frame["messageName"]
                for frame in generated_parser.feed(raw, now_ms=chunk["atMs"])
            )
        if reference_names != trace["expectedMessageNames"]:
            raise ContractError(f"{trace['name']}: reference stream frames differ")
        if generated_names != trace["expectedMessageNames"]:
            raise ContractError(f"{trace['name']}: generated stream frames differ")
        if reference.diagnostics != trace["expectedDiagnostics"]:
            raise ContractError(
                f"{trace['name']}: reference diagnostics differ: "
                f"{reference.diagnostics!r}"
            )
        if generated_parser.diagnostics != trace["expectedDiagnostics"]:
            raise ContractError(
                f"{trace['name']}: generated diagnostics differ: "
                f"{generated_parser.diagnostics!r}"
            )
        if reference.buffered_bytes != generated_parser.buffered_bytes:
            raise ContractError(f"{trace['name']}: parser buffer state differs")
    summary.passed(
        f"{len(traces_document['traces'])} bounded UART stream/resync traces pass "
        "reference and generated Python parsers"
    )

    digest_document = load_json(
        CONTRACTS_ROOT / "examples" / "uart" / "digest-vectors.json"
    )
    if digest_document["registrySha256"] != source_sha256(registry):
        raise ContractError("UART digest vectors use a stale registry digest")
    for vector in digest_document["vectors"]:
        preimage = bytes.fromhex(vector["preimageHex"])
        if hashlib.sha256(preimage).hexdigest() != vector["sha256"]:
            raise ContractError(f"{vector['name']}: SHA-256 vector differs")
        if vector["profile"] == "commandDigestSha256":
            components = vector["components"]
            rebuilt = uart_command_digest_preimage(
                registry,
                components["message"],
                components["payload"],
            )
            if rebuilt != preimage:
                raise ContractError(
                    f"{vector['name']}: command digest preimage projection differs"
                )
    summary.passed(
        f"{len(digest_document['vectors'])} UART command/config/snapshot digest "
        "preimages reproduce in Python"
    )


def validate_generated_c(summary: ValidationSummary, run_compiler: bool) -> None:
    registry = load_uart_registry()
    registry_digest = source_sha256(registry)
    header_path = (
        CONTRACTS_ROOT / "uart" / "generated" / "c" / "ecobin_uart_protocol.h"
    )
    test_path = (
        CONTRACTS_ROOT / "uart" / "generated" / "c" / "ecobin_uart_golden_test.c"
    )
    header = header_path.read_text(encoding="utf-8")
    test = test_path.read_text(encoding="utf-8")
    if registry_digest not in header:
        raise ContractError("generated C header has stale registry digest")
    physical_constants = (
        f"#define ECOBIN_UART_BAUD_RATE {registry['physicalLink']['baudRate']}u",
        f"#define ECOBIN_UART_DATA_BITS {registry['physicalLink']['dataBits']}u",
        f"#define ECOBIN_UART_STOP_BITS {registry['physicalLink']['stopBits']}u",
        "#define ECOBIN_UART_PARITY_NONE 1u",
        "#define ECOBIN_UART_FLOW_CONTROL_NONE 1u",
    )
    for expected in physical_constants:
        if expected not in header:
            raise ContractError(f"generated C header misses physical link: {expected}")
    for message in registry["messages"]:
        expected = f"ECOBIN_UART_MESSAGE_{message['name']} = 0x{message['id']:02X}u"
        if expected not in header:
            raise ContractError(f"C header misses {message['name']}")
    vectors = load_json(
        CONTRACTS_ROOT / "examples" / "uart" / "golden-vectors.json"
    )["vectors"]
    for vector in vectors:
        if vector["name"] not in test:
            raise ContractError(f"C golden test misses {vector['name']}")
    digest_vectors = load_json(
        CONTRACTS_ROOT / "examples" / "uart" / "digest-vectors.json"
    )["vectors"]
    for vector in digest_vectors:
        if vector["name"] not in test:
            raise ContractError(f"C golden test misses digest {vector['name']}")
    summary.passed("generated C header and golden test match every registry message/vector")

    if not run_compiler:
        summary.note("C compiler execution skipped by option")
        return
    compiler = shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        summary.note("No local C compiler; MCU toolchain compile remains the F-10 HITL gate")
        return
    with tempfile.TemporaryDirectory(
        prefix=".ecobin-f10-c-",
        dir=CONTRACTS_ROOT,
    ) as temp:
        executable = Path(temp) / ("uart-golden.exe" if os.name == "nt" else "uart-golden")
        command = [
            compiler,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(header_path.parent),
            str(test_path),
            "-o",
            str(executable),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        result = subprocess.run(
            [str(executable)], check=True, capture_output=True, text=True
        )
        summary.passed(result.stdout.strip())


def validate_generated_java(summary: ValidationSummary, run_compiler: bool) -> None:
    if not run_compiler:
        summary.note("Java compiler execution skipped by option")
        return
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        summary.note("Java 21 toolchain unavailable; generated sources only checked as text")
        return
    uart_java_root = CONTRACTS_ROOT / "uart" / "generated" / "java"
    onenet_java_root = CONTRACTS_ROOT / "onenet" / "generated" / "java"
    with tempfile.TemporaryDirectory(
        prefix=".ecobin-f10-java-",
        dir=CONTRACTS_ROOT,
    ) as temp:
        compile_result = subprocess.run(
            [
                javac,
                "--release",
                "21",
                "-d",
                temp,
                str(uart_java_root / "EcobinUartProtocol.java"),
                str(uart_java_root / "EcobinUartGoldenTest.java"),
                str(onenet_java_root / "EcobinCanonicalJson.java"),
                str(onenet_java_root / "EcobinCanonicalJsonGoldenTest.java"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        expected_classes = [
            Path(temp) / "EcobinUartProtocol.class",
            Path(temp) / "EcobinUartGoldenTest.class",
            Path(temp) / "EcobinCanonicalJson.class",
            Path(temp) / "EcobinCanonicalJsonGoldenTest.class",
        ]
        if compile_result.returncode != 0 and not all(
            path.is_file() for path in expected_classes
        ):
            raise subprocess.CalledProcessError(
                compile_result.returncode,
                compile_result.args,
                output=compile_result.stdout,
                stderr=compile_result.stderr,
            )
        if compile_result.returncode != 0:
            summary.note(
                "local javac returned non-zero after writing complete class files; "
                "golden-test execution is used as the definitive source check"
            )
        elif compile_result.stderr:
            summary.note(compile_result.stderr.strip())
        for test_class in (
            "EcobinUartGoldenTest",
            "EcobinCanonicalJsonGoldenTest",
        ):
            run_result = subprocess.run(
                [java, "-cp", temp, test_class],
                check=True,
                capture_output=True,
                text=True,
            )
            summary.passed(run_result.stdout.strip())


def validate_generation(summary: ValidationSummary) -> None:
    outputs = build_outputs()
    drift = apply_outputs(outputs, check=True)
    if drift:
        relative = ", ".join(
            str(path.relative_to(CONTRACTS_ROOT.parent)) for path in drift
        )
        raise ContractError(f"generated artifact drift: {relative}")
    summary.passed(f"{len(outputs)} generated files exactly match authoritative sources")


def run_validation(
    *,
    run_java: bool = True,
    run_c: bool = True,
) -> ValidationSummary:
    summary = ValidationSummary()
    validate_generation(summary)
    validate_sources(summary)
    validate_onenet_thing_model(summary)
    validate_onenet_wire_examples(summary)
    validate_onenet_examples(summary)
    validate_uart_vectors(summary)
    validate_generated_java(summary, run_java)
    validate_generated_c(summary, run_c)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-java", action="store_true")
    parser.add_argument("--skip-c", action="store_true")
    args = parser.parse_args()
    try:
        summary = run_validation(
            run_java=not args.skip_java,
            run_c=not args.skip_c,
        )
    except (ContractError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Contract validation failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            if exc.stdout:
                print(exc.stdout, file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
        return 1
    for check in summary.checks:
        print(f"PASS: {check}")
    for note in summary.notes:
        print(f"NOTE: {note}")
    print(
        f"Contract validation complete: {len(summary.checks)} passed, "
        f"{len(summary.notes)} notes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
