"""Validate EcoBin F-10 OneNet and UART machine contracts."""

from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import re
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
    ONENET_IDENTIFIER_PATTERN,
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
    uart_result_digest_preimage,
    uart_process_event_digest_preimage,
    uart_message_specs,
    validate_uart_registry,
)
from generate_contracts import apply_outputs, build_outputs  # noqa: E402
from http_contract import validate_http_contract  # noqa: E402


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

ONENET_IMPORT_FILE_BYTE_LIMIT = 256 * 1024


def _utc_instant_key(value: str) -> tuple[datetime.datetime, int]:
    """Return an exact UTC instant key without losing nanoseconds."""
    body = value.removesuffix("Z")
    whole_seconds, separator, fraction = body.partition(".")
    parsed_seconds = datetime.datetime.fromisoformat(
        whole_seconds + "+00:00"
    )
    nanoseconds = int(fraction.ljust(9, "0")) if separator else 0
    return parsed_seconds, nanoseconds


ONENET_ENUM_DESCRIPTION_PATTERN = re.compile(
    r"[A-Za-z0-9_\-\u4e00-\u9fa5]{1,20}"
)

WORK_PATH_SEGMENT = {
    "DELIVERY_SESSION": "delivery-session",
    "CLEAN_OPERATION": "clean-operation",
    "DEVICE_ACCEPTANCE": "device-acceptance",
    "MCU_FIRMWARE_RELEASE": "mcu-firmware",
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
        f"/ecobin/{WORK_PATH_SEGMENT[work_type]}/{work_uid}/"
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
        f"ecobin/{WORK_PATH_SEGMENT[work_type]}/"
        f"{work_uid}/"
    )
    if grant["keyPrefix"] != expected_prefix:
        raise ContractError(
            f"{command['commandType']}: COS keyPrefix differs from work"
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
    candidate_path = (
        CONTRACTS_ROOT
        / "onenet"
        / "generated"
        / "onenet-thing-model.candidate.json"
    )
    candidate_bytes = candidate_path.read_bytes()
    if len(candidate_bytes) >= ONENET_IMPORT_FILE_BYTE_LIMIT:
        raise ContractError(
            "OneNet candidate must be smaller than 256 KiB: "
            f"{len(candidate_bytes)} >= {ONENET_IMPORT_FILE_BYTE_LIMIT} bytes"
        )
    if b"\r" in candidate_bytes:
        raise ContractError("OneNet candidate must use LF line endings")
    model = load_json(candidate_path)
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
    for identifier in identifiers:
        if ONENET_IDENTIFIER_PATTERN.fullmatch(identifier) is None:
            raise ContractError(
                f"OneNet function identifier {identifier!r} must be 1..32 "
                "ASCII letters/digits/underscore/hyphen characters and start "
                "with an ASCII letter"
            )
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
        if ONENET_IDENTIFIER_PATTERN.fullmatch(identifier) is None:
            raise ContractError(
                f"{context}: invalid OneNet identifier {identifier!r}; expected "
                "1..32 ASCII letters/digits/underscore/hyphen characters "
                "starting with an ASCII letter"
            )
        if not 1 <= len(name) <= 30:
            raise ContractError(f"{context}.{identifier}: OneNet name exceeds 30 chars")
        data_type = descriptor["dataType"]
        type_name = data_type["type"]
        if type_name in {"float", "double"}:
            raise ContractError(f"{context}.{identifier}: floating point is forbidden")
        if type_name in allowed_primitive_types:
            if type_name == "enum":
                for enum_value, description in data_type["specs"].items():
                    if (
                        not isinstance(description, str)
                        or ONENET_ENUM_DESCRIPTION_PATTERN.fullmatch(
                            description
                        )
                        is None
                    ):
                        raise ContractError(
                            f"{context}.{identifier}: enum {enum_value} "
                            "description must be 1..20 Chinese/English/"
                            "digit/underscore/hyphen characters"
                        )
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
        f"OneNet import candidate is {len(candidate_bytes)} bytes with "
        f"{len(functions)} typed function points, valid enum descriptions, "
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
    if len(manifest["examples"]) != len(set(manifest["examples"])):
        raise ContractError("OneNet wire sample filenames must be unique")

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
    covered_variants: set[tuple[str, str]] = set()
    for filename in manifest["examples"]:
        example = load_json(root / filename)
        identifier = example["identifier"]
        variant = "default"
        if identifier == "applyConfiguration" and example["kind"] == "SYNC_SERVICE":
            params = example["callServiceApiBodyTemplate"]["params"]
            variant = "native" if params.get("mcuConfigurationProfilePresent") is True else "legacy"
        if (identifier, variant) in covered_variants:
            raise ContractError(f"duplicate OneNet wire sample {identifier}/{variant}")
        covered_variants.add((identifier, variant))
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
    if not {("applyConfiguration", "legacy"), ("applyConfiguration", "native")} <= covered_variants:
        raise ContractError("OneNet wire samples must cover both explicit configuration profiles")
    summary.passed(
        f"{len(manifest['examples'])} OneNet wire samples cover {len(covered)} services/events and both configuration profiles"
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
        "DELIVERY_ISSUE_ARCHIVED": "sessionUid",
        "DELIVERY_ISSUE_EVIDENCE_APPENDED": "sessionUid",
        "DELIVERY_RECOVERY_QUARANTINED": "sessionUid",
        "CLEAN_COMPLETE": "operationUid",
        "FULLNESS_SAMPLE_COMPLETE": "detectionUid",
        "BASELINE_MEASUREMENT_COMPLETE": "measurementUid",
        "CONFIGURATION_PROGRESS": "applicationUid",
        "MCU_FIRMWARE_UPDATE_PROGRESS": "deploymentUid",
        "BUSINESS_RUNTIME_UPDATE_PROGRESS": "deploymentUid",
        "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT": "deploymentUid",
    }.get(event_type)
    if uid_field and instance["target"]["uid"] != payload[uid_field]:
        raise ContractError(f"{event_type}: target UID differs from payload")

    command_bound_events = {
        "DEVICE_COMMAND_OBSERVED",
        "CONFIGURATION_PROGRESS",
        "DELIVERY_COMPLETE",
        "DELIVERY_ISSUE_ARCHIVED",
        "DELIVERY_ISSUE_EVIDENCE_APPENDED",
        "DELIVERY_RECOVERY_QUARANTINED",
        "CLEAN_COMPLETE",
        "FULLNESS_SAMPLE_COMPLETE",
        "BASELINE_MEASUREMENT_COMPLETE",
        "BUSINESS_CONFIRMATION_RECEIPT",
        "DEVICE_ACCEPTANCE_EVIDENCE",
        "REMOTE_SUPPORT_TUNNEL_STATUS",
        "FACTORY_SEAL_COMPLETED",
        "BUSINESS_RUNTIME_UPDATE_PROGRESS",
        "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT",
    }
    if event_type in command_bound_events and instance["commandUid"] is None:
        raise ContractError(f"{event_type}: originating commandUid is required")
    if event_type == "DELIVERY_ISSUE_ARCHIVED":
        if (instance["eventUid"] != payload["issueUid"] or instance["commandUid"] != payload["originalCommandUid"]
                or not payload["sourceMcuBootId"] < payload["targetMcuBootId"]):
            raise ContractError("delivery issue archive identity or reboot differs")
        try:
            registry = load_uart_registry()
            start = decode_uart_payload(registry, "START_DELIVERY_SESSION", bytes.fromhex(payload["originalStartPayloadHex"]))
            boot = decode_uart_payload(registry, payload["bootObservationType"], bytes.fromhex(payload["bootObservationPayloadHex"]))
        except (ValueError, KeyError) as exc:
            raise ContractError("delivery issue archive wire evidence is invalid") from exc
        if (start["sessionUid"] != payload["sessionUid"] or start["portNo"] != payload["portNo"]
                or start["targetMcuBootId"] != payload["sourceMcuBootId"] or boot["mcuBootId"] != payload["targetMcuBootId"]):
            raise ContractError("delivery issue archive wire evidence differs from original identity")
    if event_type == "DELIVERY_ISSUE_EVIDENCE_APPENDED":
        raw = bytes.fromhex(payload["dataHex"])
        size, part, count = payload["evidenceSizeBytes"], payload["partIndex"], payload["partCount"]
        if (instance["commandUid"] != payload["originalCommandUid"] or instance["eventUid"] == payload["issueUid"]
                or count != (size+255)//256 or not 1 <= part <= count
                or len(raw) != min(256, size-(part-1)*256)
                or (count == 1 and hashlib.sha256(raw).hexdigest() != payload["evidenceSha256"])
                or ((payload["evidenceKind"] == "PROCESS_FACT") != (payload["evidenceIndex"] > 0))
                or (payload["evidenceKind"] == "ARCHIVE_CONTEXT" and payload["evidenceSha256"] != payload["archiveEvidenceSha256"])):
            raise ContractError("delivery issue evidence fragment identity/size/digest is inconsistent")
    if (
        event_type == "DEVICE_COMMAND_OBSERVED"
        and instance["target"]["uid"] != instance["commandUid"]
    ):
        raise ContractError("DEVICE_COMMAND_OBSERVED target must be the commandUid")

    if event_type == "MCU_FIRMWARE_UPDATE_PROGRESS":
        if payload["source"] == "CLOUD" and instance["commandUid"] is None:
            raise ContractError(
                "cloud MCU firmware progress requires the originating commandUid"
            )
        if payload["source"] == "LOCAL" and instance["commandUid"] is not None:
            raise ContractError(
                "local MCU firmware progress must not claim a cloud commandUid"
            )
        installed_fields = (
            payload["installedFirmwareVersion"],
            payload["installedFirmwareVersionCode"],
            payload["installedFirmwareIdentityHex"],
        )
        if any(value is None for value in installed_fields) and not all(
            value is None for value in installed_fields
        ):
            raise ContractError(
                "installed MCU firmware identity must be wholly present or null"
            )
        if payload["stage"] == "SUCCEEDED" and installed_fields != (
            payload["firmwareVersion"],
            payload["firmwareVersionCode"],
            payload["firmwareIdentityHex"],
        ):
            raise ContractError(
                "successful MCU progress must report the target as installed"
            )

    if event_type == "BUSINESS_RUNTIME_UPDATE_PROGRESS":
        installed_fields = (
            payload["installedReleaseUid"],
            payload["installedVersionName"],
            payload["installedReleaseSequence"],
            payload["installedPackageSha256"],
        )
        if any(value is None for value in installed_fields) and not all(
            value is None for value in installed_fields
        ):
            raise ContractError(
                "installed business runtime identity must be wholly present or null"
            )
        if payload["stage"] == "SUCCEEDED" and installed_fields != (
            payload["releaseUid"],
            payload["versionName"],
            payload["releaseSequence"],
            payload["packageSha256"],
        ):
            raise ContractError(
                "successful business runtime progress must report the target as installed"
            )
        error_stages = {
            "ROLLED_BACK",
            "DEFERRED",
            "REJECTED",
            "FAILED_LOCKED",
            "DOWNLOAD_AUTHORIZATION_REQUIRED",
        }
        if (payload["errorCode"] is not None) != (payload["stage"] in error_stages):
            raise ContractError(
                "business runtime progress errorCode differs from its stage"
            )

    if event_type == "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT":
        expected_error = (
            None
            if payload["result"] == "CANCELLED"
            else "BUSINESS_UPDATE_CANCEL_TOO_LATE"
        )
        if payload["errorCode"] != expected_error:
            raise ContractError(
                "business runtime cancellation errorCode differs from its result"
            )

    if event_type == "DEVICE_SOFTWARE_STATE_REPORTED":
        if instance["commandUid"] is not None:
            raise ContractError(
                "DEVICE_SOFTWARE_STATE_REPORTED must not claim a commandUid"
            )
        negotiated = payload["negotiatedProtocols"]
        if payload["businessReady"] and (
            payload["businessProcessState"] != "RUNNING"
            or payload["activeBusinessRelease"] is None
            or not negotiated["agentBusinessNegotiated"]
            or not negotiated["updaterBusinessNegotiated"]
        ):
            raise ContractError(
                "ready business software requires its active release and "
                "business-facing negotiated protocols"
            )

    if event_type == "FACTORY_SEAL_COMPLETED":
        if (
            instance["target"]["uid"] != payload["hardwareSn"]
            or instance["commandUid"]
            != payload["authorizationCommandUid"]
        ):
            raise ContractError(
                "FACTORY_SEAL_COMPLETED differs from its device or authorization"
            )
        binding = {
            "commandUid": payload["authorizationCommandUid"],
            "hardwareSn": payload["hardwareSn"],
            "acceptanceGeneration": payload["acceptanceGeneration"],
            "acceptanceEvidenceUid": payload["acceptanceEvidenceUid"],
            "acceptanceChallengeUid": payload["acceptanceChallengeUid"],
            "acceptanceEvidenceSha256": payload[
                "acceptanceEvidenceSha256"
            ],
            "factoryBagRevision": payload["factoryBagRevision"],
            "factoryBagSetSha256": payload["factoryBagSetSha256"],
            "imageReleaseId": payload["imageReleaseId"],
            "imageReleaseSha256": payload["imageReleaseSha256"],
            "factoryReportSha256": payload["factoryReportSha256"],
        }
        if payload_sha256(binding) != payload["authorizationBindingSha256"]:
            raise ContractError(
                "FACTORY_SEAL_COMPLETED authorization binding differs"
            )
        sealed_at = _utc_instant_key(payload["sealedAt"])
        cleanup_at = _utc_instant_key(payload["cleanupCompletedAt"])
        occurred_at = _utc_instant_key(instance["occurredAt"])
        if cleanup_at < sealed_at:
            raise ContractError(
                "FACTORY_SEAL_COMPLETED timestamps are not monotonic"
            )
        if occurred_at != cleanup_at:
            raise ContractError(
                "FACTORY_SEAL_COMPLETED occurredAt must equal "
                "cleanupCompletedAt"
            )

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
        first_weight = (
            first["reportedWeightGrams"]
            if first
            and first["weightValueAvailable"]
            and first["status"] in {"STABLE", "UNSTABLE"}
            and first["sensorHealth"] == "OK"
            else None
        )
        final_weight = (
            final["reportedWeightGrams"]
            if final
            and final["weightValueAvailable"]
            and final["status"] in {"STABLE", "UNSTABLE"}
            and final["sensorHealth"] == "OK"
            else None
        )
        expected_net = (
            final_weight - first_weight
            if first_weight is not None and final_weight is not None
            else None
        )
        if payload["deliveryNetWeightGrams"] != expected_net:
            raise ContractError("DELIVERY_COMPLETE net weight is not whole-session delta")
        successful_reasons = {"USER_ENDED", "SELECTION_WINDOW_EXPIRED"}
        if (
            payload["completionReason"] in successful_reasons
            and (first_weight is None or final_weight is None)
        ):
            raise ContractError("normal delivery completion requires usable weights")
        if (
            payload["completionReason"] == "TERMINAL_WEIGHT_FAILURE"
            and final_weight is not None
        ):
            raise ContractError("terminal weight failure cannot carry a usable final weight")
        if (
            payload["completionReason"] in successful_reasons
            and (
                payload["finalDoorCommand"] is None
                or payload["finalDoorCommand"]["command"] != "CLOSE"
                or payload["finalDoorCommand"]["physicalStateBasis"]
                != "NOT_OBSERVABLE"
            )
        ):
            raise ContractError("DELIVERY_COMPLETE requires a close-output fact")
        if (
            payload["completionReason"] == "DEVICE_INTERRUPTED"
            and not payload["manualReviewRequired"]
        ):
            raise ContractError("interrupted delivery requires manual review")
        for photo in payload["photos"]:
            _validate_photo_url(
                photo,
                work_type="DELIVERY_SESSION",
                work_uid=payload["sessionUid"],
                trusted_cos=mapping["trustedCosEnvironment"]["contractTestProfile"],
            )

    if event_type == "DELIVERY_RECOVERY_QUARANTINED":
        evidence = payload["deviceEvidence"]
        if (
            instance["eventUid"] != payload["recoveryUid"]
            or instance["commandUid"] == payload["originalCommandUid"]
            or evidence["previousBootIdentity"]
            == evidence["currentBootIdentity"]
            or evidence["workUid"] != payload["sessionUid"]
            or evidence["commandUid"] != payload["originalCommandUid"]
            or evidence["permitUid"] != payload["originalCommandUid"]
        ):
            raise ContractError(
                "DELIVERY_RECOVERY_QUARANTINED identity or reboot evidence mismatch"
            )
        slots = [photo["slot"] for photo in payload["photos"]]
        expected = WORK_PHOTO_SLOTS["DELIVERY_SESSION"]
        if tuple(slots) != expected:
            raise ContractError(
                "DELIVERY_RECOVERY_QUARANTINED photos are not in canonical order"
            )
        for photo in payload["photos"]:
            _validate_photo_url(
                photo,
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
        lock = payload["cleanLockAndManualDoorConfirmation"]
        if (
            not payload["cleanerCompletionConfirmed"]
            or lock["lockPowerState"] != "DEENERGIZED"
            or not lock["cleanerPhysicalCloseConfirmed"]
            or lock["physicalDoorStateBasis"] != "CLEANER_CONFIRMATION"
        ):
            raise ContractError("CLEAN_COMPLETE lacks manual close confirmation")
        if any("magnet" in key.lower() for key in lock):
            raise ContractError("CLEAN_COMPLETE must not expose a cleaning-door sensor")
        first = payload["preUnlockMeasurement"]
        final = payload["cleanerConfirmedFinalMeasurement"]
        first_weight = (
            first["reportedWeightGrams"]
            if first["weightValueAvailable"]
            and first["status"] in {"STABLE", "UNSTABLE"}
            and first["sensorHealth"] == "OK"
            else None
        )
        final_weight = (
            final["reportedWeightGrams"]
            if final["weightValueAvailable"]
            and final["status"] in {"STABLE", "UNSTABLE"}
            and final["sensorHealth"] == "OK"
            else None
        )
        if first_weight is None:
            raise ContractError("normal CLEAN_COMPLETE requires usable pre-unlock weight")
        # Decision 2026-09-13: removal is this operation's before minus after.
        # Never substitute the previous bag's baseline for an unavailable after.
        expected_removed = first_weight - final_weight if final_weight is not None else None
        if payload["removedNetWeightGrams"] != expected_removed:
            raise ContractError("CLEAN_COMPLETE removed weight differs from before/after measurements")
        if payload["newBaselineWeightGrams"] != final_weight:
            raise ContractError("CLEAN_COMPLETE new baseline differs from final weight")
        for photo in payload["photos"]:
            _validate_photo_url(
                photo,
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
        fault_codes_by_component = {
            "UART": {"UART_PROTOCOL", "UART_STORAGE"},
            "DELIVERY_DOOR": {
                "DELIVERY_DOOR_OUTPUT_REJECTED",
                "DELIVERY_DOOR_HIL_NOT_QUALIFIED",
            },
            "CLEAN_SOLENOID": {"CLEAN_SOLENOID_DRIVER"},
            "WEIGHT_SENSOR": {
                "WEIGHT_UNSTABLE",
                "WEIGHT_TIMEOUT",
                "WEIGHT_SENSOR",
                "WEIGHT_OVERLOAD",
                "WEIGHT_PROTOCOL",
                "WEIGHT_CONFIG",
                "WEIGHT_DISCONNECTED",
            },
            "FULLNESS_SENSOR": {"FULLNESS_SENSOR_DIAGNOSTIC"},
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
        ports = [port["portNo"] for port in payload["ports"]]
        if ports != list(range(1, len(ports) + 1)):
            raise ContractError(
                "DEVICE_RUNTIME_SNAPSHOT ports must be unique, ordered and contiguous"
            )

    if event_type == "DEVICE_ACCEPTANCE_EVIDENCE":
        if instance["target"]["uid"] == "":
            raise ContractError(
                "DEVICE_ACCEPTANCE_EVIDENCE requires a device target"
            )
        for field in (
            "sensorSampleSha256",
            "cameraCaptureSha256",
            "cameraUploadSha256",
        ):
            if payload[field] == "0" * 64:
                raise ContractError(
                    f"DEVICE_ACCEPTANCE_EVIDENCE {field} is empty"
                )
        if (
            payload["deviceEntryUrlStored"]
            and payload["deviceEntryUrlSha256"] == "0" * 64
        ):
            raise ContractError(
                "stored device entry URL requires a non-zero digest"
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
    command_type = instance["commandType"]
    uid_field = {
        "APPLY_CONFIGURATION": "applicationUid",
        "START_DELIVERY_SESSION": "sessionUid",
        "QUARANTINE_DELIVERY_RECOVERY": "sessionUid",
        "START_CLEAN_OPERATION": "operationUid",
        "END_CLEAN_BEFORE_UNLOCK": "operationUid",
        "RESUME_CLEAN_OPERATION": "operationUid",
        "SAMPLE_FULLNESS": "detectionUid",
        "MEASURE_EMPTY_BAG_BASELINE": "measurementUid",
        "CONFIRM_EDGE_EVENT": "originalEventUid",
        "PROVIDE_PHOTO_UPLOAD_GRANT": "grantRequestEventUid",
        "OPEN_REMOTE_SUPPORT_TUNNEL": "sessionUid",
        "CLOSE_REMOTE_SUPPORT_TUNNEL": "sessionUid",
        "START_MCU_FIRMWARE_UPDATE": "deploymentUid",
        "START_BUSINESS_RUNTIME_UPDATE": "deploymentUid",
        "CANCEL_BUSINESS_RUNTIME_UPDATE": "deploymentUid",
    }.get(command_type)
    if command_type in {
        "REQUEST_DEVICE_ACCEPTANCE",
        "AUTHORIZE_FACTORY_SEAL",
        "SYNC_DEVICE_ENTRY_URL",
    }:
        if instance["target"]["uid"] != instance["targetDeviceName"]:
            raise ContractError(
                f"{command_type} target differs from device name"
            )
    elif instance["target"]["uid"] != instance["payload"][uid_field]:
        raise ContractError(f"{command_type}: target UID differs from payload")

    payload = instance["payload"]

    def check_native_measurement_identity(value):
        if isinstance(value, dict):
            uid = value.get("measurementUid")
            if isinstance(uid, str) and uid.startswith("45424d31-00"):
                boot, sequence = value.get("mcuBootId"), value.get("mcuEventSequence")
                if (type(boot) is not int or not 1 <= boot <= 9007199254740991
                        or type(sequence) is not int or not 1 <= sequence <= 4294967295):
                    raise ContractError("native measurement requires its original boot/event")
                raw = f"45424d31{boot:016x}{sequence:08x}"
                expected = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
                if uid != expected:
                    raise ContractError("native measurement identity differs from its boot/event")
            for item in value.values():
                check_native_measurement_identity(item)
        elif isinstance(value, list):
            for item in value:
                check_native_measurement_identity(item)

    check_native_measurement_identity(payload)
    if (
        command_type != "START_BUSINESS_RUNTIME_UPDATE"
        and instance.get("downloadGrant") is not None
    ):
        raise ContractError(
            f"{command_type}: transient download authorization is forbidden"
        )
    if command_type == "AUTHORIZE_FACTORY_SEAL":
        if payload["hardwareSn"] != instance["targetDeviceName"]:
            raise ContractError(
                "AUTHORIZE_FACTORY_SEAL hardwareSn differs from device name"
            )
        if instance["cosGrant"] is not None:
            raise ContractError(
                "AUTHORIZE_FACTORY_SEAL must not carry COS credentials"
            )
    if command_type in {
        "REQUEST_DEVICE_ACCEPTANCE",
        "SYNC_DEVICE_ENTRY_URL",
    }:
        url = payload["deviceEntryUrl"]
        try:
            encoded_url = url.encode("ascii")
        except UnicodeEncodeError as error:
            raise ContractError(
                f"{command_type}: deviceEntryUrl must be ASCII"
            ) from error
        if len(encoded_url) > 192 or not url.startswith("https://"):
            raise ContractError(
                f"{command_type}: deviceEntryUrl is outside the fixed-frame contract"
            )
        expected_url_sha256 = hashlib.sha256(encoded_url).hexdigest()
        if payload["deviceEntryUrlSha256"] != expected_url_sha256:
            raise ContractError(
                f"{command_type}: deviceEntryUrlSha256 mismatch"
            )
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
            if (
                port["fullnessMinimumValidSampleCount"]
                > port["fullnessSampleCount"]
            ):
                raise ContractError(
                    "APPLY_CONFIGURATION minimum valid fullness samples exceed total"
                )

    if command_type == "QUARANTINE_DELIVERY_RECOVERY":
        if expires > issued + datetime.timedelta(minutes=5):
            raise ContractError(
                "QUARANTINE_DELIVERY_RECOVERY lifetime exceeds 5 minutes"
            )
        if instance["cosGrant"] is not None:
            raise ContractError(
                "QUARANTINE_DELIVERY_RECOVERY must not carry COS credentials"
            )
        if len({
            instance["commandUid"],
            payload["recoveryUid"],
            payload["originalCommandUid"],
        }) != 3:
            raise ContractError(
                "QUARANTINE_DELIVERY_RECOVERY identities must be distinct"
            )

    if command_type == "START_MCU_FIRMWARE_UPDATE":
        expected_key = (
            f"ecobin/mcu-firmware/{payload['releaseUid']}/"
            f"{payload['packageSha256']}.efw"
        )
        if payload["objectKey"] != expected_key:
            raise ContractError(
                "START_MCU_FIRMWARE_UPDATE object key differs from signed identity"
            )

    if command_type == "START_BUSINESS_RUNTIME_UPDATE":
        expected_key = (
            f"edge-runtime/releases/{payload['releaseUid']}/package.tar.gz"
        )
        if payload["objectKey"] != expected_key:
            raise ContractError(
                "START_BUSINESS_RUNTIME_UPDATE object key differs from release identity"
            )
        grant = instance["downloadGrant"]
        grant_expiry = datetime.datetime.fromisoformat(
            grant["expiresAt"].removesuffix("Z") + "+00:00"
        )
        if grant_expiry <= issued:
            raise ContractError(
                "START_BUSINESS_RUNTIME_UPDATE download grant is already expired"
            )
        parsed = urllib.parse.urlsplit(grant["url"])
        trusted = urllib.parse.urlsplit(
            mapping["trustedBusinessReleaseDownloadEnvironment"]
            ["contractTestProfile"]["baseUrl"]
        )
        if (
            parsed.scheme != "https"
            or parsed.scheme != trusted.scheme
            or parsed.netloc != trusted.netloc
            or parsed.path != "/" + expected_key
            or parsed.fragment
            or trusted.path not in {"", "/"}
            or trusted.query
            or trusted.fragment
        ):
            raise ContractError(
                "START_BUSINESS_RUNTIME_UPDATE URL differs from trusted object location"
            )

    if command_type == "CANCEL_BUSINESS_RUNTIME_UPDATE":
        if expires > issued + datetime.timedelta(minutes=15):
            raise ContractError(
                "CANCEL_BUSINESS_RUNTIME_UPDATE lifetime exceeds 15 minutes"
            )
        if instance.get("downloadGrant") is not None:
            raise ContractError(
                "CANCEL_BUSINESS_RUNTIME_UPDATE must not carry download authority"
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
    elif command_type == "REQUEST_DEVICE_ACCEPTANCE":
        work_type = "DEVICE_ACCEPTANCE"
        work_uid = payload["challengeUid"]
    elif command_type == "START_MCU_FIRMWARE_UPDATE":
        work_type = "MCU_FIRMWARE_RELEASE"
        work_uid = payload["releaseUid"]
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
        if vector["profile"] == "resultDigestSha256":
            if uart_result_digest_preimage(registry, vector["components"]["payload"]) != preimage:
                raise ContractError(f"{vector['name']}: result digest preimage projection differs")
        if vector["profile"] == "actuatorEventDigestSha256":
            from contractlib import uart_actuator_event_digest_preimage
            components = vector["components"]
            raw = encode_uart_payload(registry, components["message"], components["payload"])
            if uart_actuator_event_digest_preimage(registry, components["message"], raw) != preimage:
                raise ContractError(f"{vector['name']}: actuator event digest preimage projection differs")
        if vector["profile"] == "eventDigestSha256":
            components = vector["components"]
            raw = encode_uart_payload(registry, components["message"], components["payload"])
            if uart_process_event_digest_preimage(registry, components["message"], raw) != preimage:
                raise ContractError(f"{vector['name']}: process event digest preimage projection differs")
    summary.passed(
        f"{len(digest_document['vectors'])} UART command/config/snapshot/result/process digest "
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
        summary.note(
            "No local C compiler; target MCU compilation is optional evidence only "
            "when explicitly selecting the uart-v1 implementation"
        )
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

        # Same generated function body, externally linked once as on ARMCC5.
        # Keep the standalone-header check above for host consumers too.
        shared = command[:-2] + ["-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1",
            str(header_path.with_suffix(".c")), "-o", str(executable)]
        subprocess.run(shared, check=True, capture_output=True, text=True)
        result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
        summary.passed("shared-validator " + result.stdout.strip())


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


def validate_http(summary: ValidationSummary) -> None:
    for check in validate_http_contract():
        summary.passed(check)


def run_validation(
    *,
    run_java: bool = True,
    run_c: bool = True,
) -> ValidationSummary:
    summary = ValidationSummary()
    validate_generation(summary)
    validate_http(summary)
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
