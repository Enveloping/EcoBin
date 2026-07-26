"""Standard-library helpers for EcoBin machine contracts.

This module intentionally stays compatible with Python 3.11 and has no third-party
dependencies.  It implements the JSON Schema subset used by this repository, the
UART registry expansion rules, RFC 8785-compatible canonical bytes for the
integer-only EcoBin payload profile, and the UART 1.0 frame/payload codec used to
produce golden vectors.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import re
import struct
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


CONTRACTS_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = CONTRACTS_ROOT.parent
UART_REGISTRY_PATH = CONTRACTS_ROOT / "uart" / "uart-registry.yaml"
UART_REGISTRY_SCHEMA_PATH = CONTRACTS_ROOT / "uart" / "uart-registry.schema.json"
JCS_SAFE_INTEGER_MAX = 9_007_199_254_740_991


class ContractError(ValueError):
    """Base error for a malformed contract or instance."""


class SchemaValidationError(ContractError):
    """Raised when an instance does not match the repository JSON Schema subset."""

    def __init__(self, message: str, instance_path: str = "$") -> None:
        super().__init__(f"{instance_path}: {message}")
        self.instance_path = instance_path
        self.message = message


def _reject_duplicate_object_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        try:
            return json.load(stream, object_pairs_hook=_reject_duplicate_object_keys)
        except json.JSONDecodeError as exc:
            raise ContractError(f"{path}: invalid JSON: {exc}") from exc


def parse_json_text(value: str, label: str = "<json>") -> Any:
    try:
        return json.loads(value, object_pairs_hook=_reject_duplicate_object_keys)
    except json.JSONDecodeError as exc:
        raise ContractError(f"{label}: invalid JSON: {exc}") from exc


def _reject_non_jcs_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str, int)):
        if isinstance(value, str):
            for char in value:
                if 0xD800 <= ord(char) <= 0xDFFF:
                    raise ContractError(f"{path}: unpaired UTF-16 surrogate is forbidden")
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and abs(value) > JCS_SAFE_INTEGER_MAX
        ):
            raise ContractError(
                f"{path}: integer exceeds the I-JSON/JCS safe range "
                f"±{JCS_SAFE_INTEGER_MAX}"
            )
        return
    if isinstance(value, float):
        raise ContractError(f"{path}: floating-point JSON numbers are forbidden")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_jcs_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{path}: object key must be a string")
            _reject_non_jcs_value(key, f"{path}.<key>")
            _reject_non_jcs_value(item, f"{path}.{key}")
        return
    raise ContractError(f"{path}: unsupported JSON value {type(value).__name__}")


def _canonical_json_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json_text(item) for item in value) + "]"
    if isinstance(value, dict):
        ordered = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, allow_nan=False)
            + ":"
            + _canonical_json_text(value[key])
            for key in ordered
        ) + "}"
    raise ContractError(f"unsupported canonical JSON value {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return canonical UTF-8 for EcoBin's RFC 8785 integer-only profile.

    RFC 8785 delegates strings to ECMAScript JSON serialization and sorts object
    member names lexicographically by UTF-16 code units. Stable payload numbers
    are restricted to the I-JSON interoperable integer range. Values outside that
    profile are rejected instead of being silently approximated.
    """

    _reject_non_jcs_value(value)
    return _canonical_json_text(value).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def payload_sha256(payload: Any) -> str:
    return sha256_hex(canonical_json_bytes(payload))


ONENET_COMMAND_CANONICAL_DOMAIN = b"ECOBIN:ONENET:COMMAND:v1\x00"
ONENET_EVENT_CANONICAL_DOMAIN = b"ECOBIN:ONENET:EVENT:v1\x00"


def onenet_command_canonical_projection(
    command: Mapping[str, Any],
) -> dict[str, Any]:
    """Project stable OneNet command identity, excluding attempt-only credentials."""

    fields = (
        "schemaVersion",
        "commandUid",
        "commandType",
        "deploymentCode",
        "target",
        "issuedAt",
        "expiresAt",
        "payloadSchemaVersion",
        "payloadSha256",
    )
    missing = [field for field in fields if field not in command]
    if missing:
        raise ContractError(
            "OneNet command canonical projection misses " + ", ".join(missing)
        )
    return {field: command[field] for field in fields}


def onenet_command_canonical_preimage(command: Mapping[str, Any]) -> bytes:
    return ONENET_COMMAND_CANONICAL_DOMAIN + canonical_json_bytes(
        onenet_command_canonical_projection(command)
    )


def onenet_command_canonical_sha256(command: Mapping[str, Any]) -> str:
    return sha256_hex(onenet_command_canonical_preimage(command))


def onenet_event_canonical_projection(
    event: Mapping[str, Any],
    *,
    trusted_product_id: str,
    trusted_device_name: str,
) -> dict[str, Any]:
    """Project an event plus the authenticated OneNet source observed by backend."""

    if not trusted_product_id or not trusted_device_name:
        raise ContractError("trusted OneNet product/device identity is required")
    fields = (
        "schemaVersion",
        "eventUid",
        "deploymentCode",
        "edgeEventSequence",
        "eventType",
        "deliveryClass",
        "target",
        "commandUid",
        "occurredAt",
        "clockQuality",
        "payloadSha256",
    )
    missing = [field for field in fields if field not in event]
    if missing:
        raise ContractError(
            "OneNet event canonical projection misses " + ", ".join(missing)
        )
    return {
        "trustedSource": {
            "productId": trusted_product_id,
            "deviceName": trusted_device_name,
        },
        **{field: event[field] for field in fields},
    }


def onenet_event_canonical_preimage(
    event: Mapping[str, Any],
    *,
    trusted_product_id: str,
    trusted_device_name: str,
) -> bytes:
    return ONENET_EVENT_CANONICAL_DOMAIN + canonical_json_bytes(
        onenet_event_canonical_projection(
            event,
            trusted_product_id=trusted_product_id,
            trusted_device_name=trusted_device_name,
        )
    )


def onenet_event_canonical_sha256(
    event: Mapping[str, Any],
    *,
    trusted_product_id: str,
    trusted_device_name: str,
) -> str:
    return sha256_hex(
        onenet_event_canonical_preimage(
            event,
            trusted_product_id=trusted_product_id,
            trusted_device_name=trusted_device_name,
        )
    )


def source_sha256(value: Any) -> str:
    """Hash source metadata deterministically without applying JCS number limits."""

    source = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_hex(source)


def _json_type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return (
            isinstance(instance, (int, float))
            and not isinstance(instance, bool)
        )
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    raise ContractError(f"unsupported JSON Schema type {expected!r}")


class JsonSchemaSubsetValidator:
    """Validator for the Draft 2020-12 keywords used by contracts/.

    It is not advertised as a general JSON Schema implementation.  The validator
    fails when the repository introduces an unsupported keyword with validation
    semantics, keeping dependency-free checks strict instead of silently ignoring
    new rules.
    """

    _ANNOTATION_KEYWORDS = {
        "$schema",
        "$id",
        "$defs",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
    _VALIDATION_KEYWORDS = {
        "$ref",
        "type",
        "required",
        "additionalProperties",
        "properties",
        "items",
        "const",
        "enum",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "oneOf",
        "allOf",
        "anyOf",
        "not",
        "if",
        "then",
        "else",
    }

    def __init__(self) -> None:
        self._schema_cache: dict[Path, Any] = {}

    def validate(self, instance: Any, schema_path: Path) -> None:
        schema_path = schema_path.resolve()
        schema = self._load_schema(schema_path)
        self._validate(instance, schema, schema_path, "$")

    def _load_schema(self, path: Path) -> Any:
        resolved = path.resolve()
        if resolved not in self._schema_cache:
            self._schema_cache[resolved] = load_json(resolved)
        return self._schema_cache[resolved]

    def _resolve_ref(self, ref: str, current_path: Path) -> tuple[Any, Path]:
        if ref.startswith("http://") or ref.startswith("https://"):
            raise ContractError(f"remote JSON Schema reference is forbidden: {ref}")
        file_part, separator, fragment = ref.partition("#")
        target_path = (
            (current_path.parent / file_part).resolve()
            if file_part
            else current_path.resolve()
        )
        target = self._load_schema(target_path)
        if separator and fragment:
            if not fragment.startswith("/"):
                raise ContractError(f"unsupported JSON pointer in $ref: {ref}")
            for raw_token in fragment[1:].split("/"):
                token = raw_token.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or token not in target:
                    raise ContractError(f"unresolved JSON pointer in $ref: {ref}")
                target = target[token]
        return target, target_path

    def _validate(
        self,
        instance: Any,
        schema: Any,
        schema_path: Path,
        instance_path: str,
    ) -> None:
        if isinstance(schema, bool):
            if not schema:
                raise SchemaValidationError("boolean schema is false", instance_path)
            return
        if not isinstance(schema, dict):
            raise ContractError(f"{schema_path}: schema node must be object or boolean")

        unsupported = set(schema) - self._ANNOTATION_KEYWORDS - self._VALIDATION_KEYWORDS
        if unsupported:
            raise ContractError(
                f"{schema_path}: unsupported JSON Schema keywords: "
                + ", ".join(sorted(unsupported))
            )

        if "$ref" in schema:
            referred, referred_path = self._resolve_ref(schema["$ref"], schema_path)
            self._validate(instance, referred, referred_path, instance_path)

        for child in schema.get("allOf", []):
            self._validate(instance, child, schema_path, instance_path)

        if "oneOf" in schema:
            matches = 0
            errors: list[str] = []
            for child in schema["oneOf"]:
                try:
                    self._validate(instance, child, schema_path, instance_path)
                    matches += 1
                except SchemaValidationError as exc:
                    errors.append(str(exc))
            if matches != 1:
                detail = errors[0] if errors else "multiple branches matched"
                raise SchemaValidationError(
                    f"oneOf expected exactly one match, got {matches}; {detail}",
                    instance_path,
                )

        if "anyOf" in schema:
            for child in schema["anyOf"]:
                try:
                    self._validate(instance, child, schema_path, instance_path)
                    break
                except SchemaValidationError:
                    continue
            else:
                raise SchemaValidationError("no anyOf branch matched", instance_path)

        if "not" in schema:
            try:
                self._validate(instance, schema["not"], schema_path, instance_path)
            except SchemaValidationError:
                pass
            else:
                raise SchemaValidationError("instance matched forbidden schema", instance_path)

        if "if" in schema:
            try:
                self._validate(instance, schema["if"], schema_path, instance_path)
                condition = True
            except SchemaValidationError:
                condition = False
            branch = schema.get("then") if condition else schema.get("else")
            if branch is not None:
                self._validate(instance, branch, schema_path, instance_path)

        if "const" in schema and instance != schema["const"]:
            raise SchemaValidationError(
                f"expected constant {schema['const']!r}", instance_path
            )
        if "enum" in schema and instance not in schema["enum"]:
            raise SchemaValidationError(
                f"value {instance!r} is not in enum", instance_path
            )

        declared_type = schema.get("type")
        if declared_type is not None:
            expected_types = (
                declared_type if isinstance(declared_type, list) else [declared_type]
            )
            if not any(_json_type_matches(instance, value) for value in expected_types):
                raise SchemaValidationError(
                    f"expected type {expected_types}, got {type(instance).__name__}",
                    instance_path,
                )

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for name in required:
                if name not in instance:
                    raise SchemaValidationError(
                        f"missing required property {name!r}", instance_path
                    )
            properties = schema.get("properties", {})
            for name, child_schema in properties.items():
                if name in instance:
                    self._validate(
                        instance[name],
                        child_schema,
                        schema_path,
                        f"{instance_path}.{name}",
                    )
            additional = schema.get("additionalProperties", True)
            for name, value in instance.items():
                if name in properties:
                    continue
                if additional is False:
                    raise SchemaValidationError(
                        f"unexpected property {name!r}", instance_path
                    )
                if isinstance(additional, (dict, bool)):
                    self._validate(
                        value,
                        additional,
                        schema_path,
                        f"{instance_path}.{name}",
                    )
            if "minProperties" in schema and len(instance) < schema["minProperties"]:
                raise SchemaValidationError("too few object properties", instance_path)
            if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
                raise SchemaValidationError("too many object properties", instance_path)

        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                raise SchemaValidationError("too few array items", instance_path)
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                raise SchemaValidationError("too many array items", instance_path)
            if schema.get("uniqueItems"):
                canonical = [canonical_json_bytes(item) for item in instance]
                if len(set(canonical)) != len(canonical):
                    raise SchemaValidationError("array items must be unique", instance_path)
            if "items" in schema:
                for index, item in enumerate(instance):
                    self._validate(
                        item,
                        schema["items"],
                        schema_path,
                        f"{instance_path}[{index}]",
                    )

        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                raise SchemaValidationError("string is shorter than minLength", instance_path)
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                raise SchemaValidationError("string is longer than maxLength", instance_path)
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                raise SchemaValidationError(
                    f"string does not match pattern {schema['pattern']!r}",
                    instance_path,
                )
            if "format" in schema:
                self._validate_format(instance, schema["format"], instance_path)

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                raise SchemaValidationError("number is below minimum", instance_path)
            if "maximum" in schema and instance > schema["maximum"]:
                raise SchemaValidationError("number is above maximum", instance_path)
            if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
                raise SchemaValidationError(
                    "number is not above exclusiveMinimum", instance_path
                )
            if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
                raise SchemaValidationError(
                    "number is not below exclusiveMaximum", instance_path
                )

    @staticmethod
    def _validate_format(value: str, format_name: str, instance_path: str) -> None:
        try:
            if format_name == "uuid":
                uuid.UUID(value)
                return
            if format_name == "date-time":
                if not value.endswith("Z"):
                    raise ValueError("UTC Z suffix is required")
                _datetime.datetime.fromisoformat(value[:-1] + "+00:00")
                return
            if format_name == "uri":
                parsed = urllib.parse.urlparse(value)
                if not parsed.scheme or not parsed.netloc:
                    raise ValueError("absolute URI is required")
                return
        except (ValueError, AttributeError) as exc:
            raise SchemaValidationError(
                f"invalid {format_name} format: {exc}", instance_path
            ) from exc
        raise ContractError(f"unsupported format {format_name!r}")


def load_uart_registry() -> dict[str, Any]:
    registry = load_json(UART_REGISTRY_PATH)
    if not isinstance(registry, dict):
        raise ContractError("UART registry must be an object")
    return registry


def expand_uart_fields(
    registry: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    stack: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    groups = registry["fieldGroups"]
    for entry in entries:
        if "group" not in entry:
            fields.append(dict(entry))
            continue
        group_name = entry["group"]
        if group_name in stack:
            raise ContractError(
                "recursive UART field group: " + " -> ".join(stack + (group_name,))
            )
        if group_name not in groups:
            raise ContractError(f"unknown UART field group {group_name!r}")
        fields.extend(
            expand_uart_fields(
                registry,
                groups[group_name],
                stack + (group_name,),
            )
        )
    return fields


def uart_message_specs(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    wire_types = registry["wireTypes"]
    specs: dict[str, dict[str, Any]] = {}
    for message in registry["messages"]:
        fields = expand_uart_fields(registry, message["payload"])
        minimum_length = 0
        maximum_length = 0
        fixed_offset: int | None = 0
        annotated: list[dict[str, Any]] = []
        for field in fields:
            wire_type = wire_types[field["type"]]
            minimum_size = wire_type["minimumSize"]
            maximum_size = wire_type["maximumSize"]
            if field["type"] == "string_u8":
                maximum_size = 1 + field["maxLength"]
            annotated_field = dict(field)
            annotated_field["minimumSize"] = minimum_size
            annotated_field["maximumSize"] = maximum_size
            annotated_field["offset"] = fixed_offset
            annotated.append(annotated_field)
            minimum_length += minimum_size
            maximum_length += maximum_size
            if fixed_offset is not None and minimum_size == maximum_size:
                fixed_offset += minimum_size
            else:
                fixed_offset = None
        specs[message["name"]] = {
            "id": message["id"],
            "direction": message["direction"],
            "category": message["category"],
            "ackRequired": message["ackRequired"],
            "criticalEvent": message.get("criticalEvent", False),
            "minimumPayloadLength": minimum_length,
            "maximumPayloadLength": maximum_length,
            "fields": annotated,
        }
    return specs


def validate_uart_registry(
    registry: Mapping[str, Any],
    schema_validator: JsonSchemaSubsetValidator | None = None,
) -> list[str]:
    validator = schema_validator or JsonSchemaSubsetValidator()
    validator.validate(registry, UART_REGISTRY_SCHEMA_PATH)
    messages = registry["messages"]
    message_names = [message["name"] for message in messages]
    message_ids = [message["id"] for message in messages]
    if len(set(message_names)) != len(message_names):
        raise ContractError("UART message names must be unique")
    if len(set(message_ids)) != len(message_ids):
        raise ContractError("UART message IDs must be unique")

    for enum_name, enum in registry["enums"].items():
        values = list(enum["values"].values())
        if len(set(values)) != len(values):
            raise ContractError(f"enum {enum_name} contains duplicate values")
        wire = registry["wireTypes"][enum["wireType"]]
        for symbol, value in enum["values"].items():
            if value < wire.get("minimum", 0) or value > wire.get("maximum", value):
                raise ContractError(
                    f"enum {enum_name}.{symbol} does not fit {enum['wireType']}"
                )

    for bitmap_name, bitmap in registry["bitmaps"].items():
        bits = list(bitmap["bits"].values())
        if len(set(bits)) != len(bits):
            raise ContractError(f"bitmap {bitmap_name} contains duplicate bits")
        wire = registry["wireTypes"][bitmap["wireType"]]
        maximum_bit = wire["maximumSize"] * 8 - 1
        if any(bit > maximum_bit for bit in bits):
            raise ContractError(f"bitmap {bitmap_name} contains an out-of-range bit")
        known_mask = sum(1 << bit for bit in bits)
        expected_hex_length = wire["maximumSize"] * 2
        if (
            len(bitmap["knownMaskHex"]) != expected_hex_length
            or int(bitmap["knownMaskHex"], 16) != known_mask
        ):
            raise ContractError(f"bitmap {bitmap_name} knownMaskHex differs from bits")

    capability_bits = list(registry["capabilities"].values())
    if len(set(capability_bits)) != len(capability_bits):
        raise ContractError("UART capability bits must be unique")
    forbidden_capabilities = {
        "CLEAN_DOOR_SENSOR",
        "CLEAN_DOOR_AUTO_CLOSE",
        "CLEAN_SAFE_CLOSE",
    }
    unexpected = forbidden_capabilities & set(registry["capabilities"])
    if unexpected:
        raise ContractError(
            "forbidden clean-door capabilities present: " + ", ".join(sorted(unexpected))
        )

    specs = uart_message_specs(registry)
    maximum_payload = registry["protocol"]["maximumPayloadLength"]
    no_ack = {"HELLO", "HELLO_ACK", "ACK", "NACK"}
    required_messages = {
        "HELLO",
        "HELLO_ACK",
        "ACK",
        "NACK",
        "QUERY_STATE",
        "SAFE_CLOSE",
        "CONFIG_BEGIN",
        "CONFIG_DEVICE_BLOCK",
        "CONFIG_PORT_BLOCK",
        "CONFIG_COMMIT",
        "CONFIG_APPLY_RESULT",
        "START_DELIVERY_SESSION",
        "AUTHORIZE_DELIVERY_FIRST_OPEN",
        "START_CLEAN_OPERATION",
        "UNLOCK_CLEAN_DOOR",
        "RESUME_CLEAN_OPERATION",
        "END_CLEAN_BEFORE_UNLOCK",
        "SAMPLE_FULLNESS",
        "MEASURE_BASELINE",
        "CONFIRM_NO_ACTIVE_WORK",
        "WORK_PREOPEN_WEIGHT_READY",
        "DELIVERY_DOOR_COMMAND_RESULT",
        "WORK_POSTCLOSE_WEIGHT_READY",
        "DELIVERY_SELECTION",
        "WORK_PREUNLOCK_WEIGHT_READY",
        "CLEAN_LOCK_POWER_CHANGED",
        "CLEAN_UNLOCK_REQUESTED",
        "CLEAN_FINISH_REQUESTED",
        "CLEAN_FINAL_WEIGHT_READY",
        "FULLNESS_SAMPLE_RESULT",
        "BASELINE_MEASUREMENT_RESULT",
        "FAULT_OBSERVED",
        "SAFETY_SENSOR_EVENT",
        "SAFE_CLOSE_RESULT",
        "CLEAN_COMPLETION_CONFIRMED",
        "BOOT_RECONCILIATION_RESULT",
        "STATE_SNAPSHOT_BEGIN",
        "STATE_SNAPSHOT_PORT",
        "STATE_SNAPSHOT_END",
    }
    missing = required_messages - set(specs)
    if missing:
        raise ContractError("UART registry misses messages: " + ", ".join(sorted(missing)))

    report: list[str] = []
    for message in messages:
        name = message["name"]
        spec = specs[name]
        if spec["maximumPayloadLength"] > maximum_payload:
            raise ContractError(
                f"{name} maximum payload {spec['maximumPayloadLength']} exceeds "
                f"{maximum_payload}"
            )
        expected_ack = name not in no_ack
        if message["ackRequired"] != expected_ack:
            raise ContractError(f"{name} ackRequired must be {expected_ack}")
        if message.get("criticalEvent"):
            if message["direction"] != "MCU_TO_EDGE" or not message["ackRequired"]:
                raise ContractError(
                    f"critical event {name} must be MCU_TO_EDGE and ACK_REQUIRED"
                )
        names = [field["name"] for field in spec["fields"]]
        if len(set(names)) != len(names):
            raise ContractError(f"{name} contains duplicate expanded field names")
        by_field_name = {field["name"]: field for field in spec["fields"]}
        for field_index, field in enumerate(spec["fields"]):
            if field["type"] not in registry["wireTypes"]:
                raise ContractError(f"{name}.{field['name']} has unknown wire type")
            wire = registry["wireTypes"][field["type"]]
            wire_minimum = wire.get("minimum")
            wire_maximum = wire.get("maximum")
            minimum = field.get("minimum", wire.get("minimum"))
            maximum = field.get("maximum", wire.get("maximum"))
            if (
                minimum is not None
                and maximum is not None
                and minimum > maximum
            ):
                raise ContractError(f"{name}.{field['name']} has minimum above maximum")
            if (
                wire_minimum is not None
                and minimum is not None
                and minimum < wire_minimum
            ) or (
                wire_maximum is not None
                and maximum is not None
                and maximum > wire_maximum
            ):
                raise ContractError(
                    f"{name}.{field['name']} range exceeds its wire type"
                )
            if field["type"] == "string_u8":
                if "maxLength" not in field or field["maxLength"] > 254:
                    raise ContractError(
                        f"{name}.{field['name']} needs a bounded string length"
                    )
            elif "maxLength" in field:
                raise ContractError(
                    f"{name}.{field['name']} maxLength is only valid for string_u8"
                )
            if ("invalidWhen" in field) != ("invalidEncoding" in field):
                raise ContractError(
                    f"{name}.{field['name']} invalid condition/encoding must be paired"
                )
            if "const" in field and field["type"] in {
                "u8",
                "u16",
                "u32",
                "u64",
                "i32",
            }:
                const = field["const"]
                if (
                    isinstance(const, bool)
                    or not isinstance(const, int)
                    or (minimum is not None and const < minimum)
                    or (maximum is not None and const > maximum)
                ):
                    raise ContractError(
                        f"{name}.{field['name']} constant does not fit its range"
                    )
            if "const" in field and field["type"] == "bool" and not isinstance(
                field["const"], bool
            ):
                raise ContractError(
                    f"{name}.{field['name']} boolean constant must be boolean"
                )
            if (
                field["type"] == "u64"
                and field["name"] != "capabilityBitmap"
                and field.get("maximum") != 9007199254740991
            ):
                raise ContractError(
                    f"{name}.{field['name']} must use the JSON/JCS safe-integer maximum"
                )
            enum_name = field.get("enum")
            if enum_name:
                if enum_name not in registry["enums"]:
                    raise ContractError(f"{name}.{field['name']} has unknown enum")
                if registry["enums"][enum_name]["wireType"] != field["type"]:
                    raise ContractError(
                        f"{name}.{field['name']} wire type differs from {enum_name}"
                    )
            bitmap_name = field.get("bitmap")
            if bitmap_name:
                if bitmap_name not in registry["bitmaps"]:
                    raise ContractError(f"{name}.{field['name']} has unknown bitmap")
                if registry["bitmaps"][bitmap_name]["wireType"] != field["type"]:
                    raise ContractError(
                        f"{name}.{field['name']} wire type differs from {bitmap_name}"
                    )
            if field.get("zeroAllowed") and field.get("zeroAllowedWhen"):
                raise ContractError(
                    f"{name}.{field['name']} cannot use two zero-UUID policies"
                )
            if (field.get("zeroAllowed") or field.get("zeroAllowedWhen")) and (
                field["type"] != "uuid"
            ):
                raise ContractError(
                    f"{name}.{field['name']} zero policy is only valid for UUID"
                )
            for condition_name in ("invalidWhen", "zeroAllowedWhen"):
                condition = field.get(condition_name)
                if not condition:
                    continue
                match = re.fullmatch(
                    r"([a-z][A-Za-z0-9]*)=([A-Z][A-Z0-9_]*|true|false)",
                    condition,
                )
                if match is None:
                    raise ContractError(
                        f"{name}.{field['name']} has unsupported {condition_name}"
                    )
                controller_name = match.group(1)
                controller = by_field_name.get(controller_name)
                if controller is None:
                    raise ContractError(
                        f"{name}.{field['name']} references unknown {controller_name}"
                    )
                controller_index = spec["fields"].index(controller)
                if controller_index >= field_index:
                    raise ContractError(
                        f"{name}.{field['name']} condition must reference an earlier field"
                    )
                expected = match.group(2)
                if expected in {"true", "false"}:
                    if controller["type"] != "bool":
                        raise ContractError(
                            f"{name}.{field['name']} boolean condition has non-bool source"
                        )
                elif not controller.get("enum") or expected not in registry["enums"][
                    controller["enum"]
                ]["values"]:
                    raise ContractError(
                        f"{name}.{field['name']} condition uses an unknown enum symbol"
                    )
        report.append(
            f"{name}=0x{message['id']:02X} "
            f"payload[{spec['minimumPayloadLength']}..{spec['maximumPayloadLength']}]"
        )

    if "NONE" not in registry["enums"]["NackError"]["values"]:
        raise ContractError("NackError.NONE=0 is required for successful HELLO_ACK")
    if registry["enums"]["NackError"]["values"]["NONE"] != 0:
        raise ContractError("NackError.NONE must be zero")

    known_capability_mask = sum(
        1 << bit for bit in registry["capabilities"].values()
    )
    policy = registry["capabilityPolicy"]
    if int(policy["knownMaskHex"], 16) != known_capability_mask:
        raise ContractError("capabilityPolicy.knownMaskHex differs from capability bits")
    for mask_name in ("requiredEdgeMaskHex", "requiredMcuMaskHex"):
        if int(policy[mask_name], 16) & ~known_capability_mask:
            raise ContractError(f"capabilityPolicy.{mask_name} contains unknown bits")
    for message_name, capability_names in policy["messageRequirements"].items():
        if message_name not in specs:
            raise ContractError(
                f"capabilityPolicy references unknown message {message_name}"
            )
        unknown_capabilities = set(capability_names) - set(registry["capabilities"])
        if unknown_capabilities:
            raise ContractError(
                f"{message_name} references unknown capabilities "
                + ", ".join(sorted(unknown_capabilities))
            )

    expected_domains = {
        "commandDigestSha256": b"ECOBIN:UART:COMMAND:v1\0",
        "mcuPayloadSha256": b"ECOBIN:UART:MCU-CONFIG:v1\0",
        "snapshotSha256": b"ECOBIN:UART:SNAPSHOT:v1\0",
    }
    for profile_name, expected_domain in expected_domains.items():
        profile = registry["digestProfiles"][profile_name]
        if bytes.fromhex(profile["domainHex"]) != expected_domain:
            raise ContractError(f"{profile_name} has an unexpected domain separator")
        if not profile["preimage"] or not profile["ordering"]:
            raise ContractError(f"{profile_name} must define its exact preimage order")

    rule_ids = [rule["id"] for rule in registry["semanticRules"]]
    if len(rule_ids) != len(set(rule_ids)):
        raise ContractError("UART semantic rule IDs must be unique")
    for rule in registry["semanticRules"]:
        unknown_messages = set(rule["messages"]) - set(specs)
        if unknown_messages:
            raise ContractError(
                f"{rule['id']} references unknown messages "
                + ", ".join(sorted(unknown_messages))
            )

    start_notes = next(
        item for item in messages if item["name"] == "START_DELIVERY_SESSION"
    ).get("notes", "")
    authorize_notes = next(
        item
        for item in messages
        if item["name"] == "AUTHORIZE_DELIVERY_FIRST_OPEN"
    ).get("notes", "")
    if "不得" not in start_notes or "SQLite" not in authorize_notes:
        raise ContractError(
            "delivery first-open authorization must be distinct from START/weight ACK"
        )

    clean_finish_fields = {
        field["name"] for field in specs["CLEAN_FINISH_REQUESTED"]["fields"]
    }
    clean_final_fields = {
        field["name"] for field in specs["CLEAN_FINAL_WEIGHT_READY"]["fields"]
    }
    if "cleanActionSequence" not in clean_finish_fields or (
        "cleanActionSequence" not in clean_final_fields
    ):
        raise ContractError(
            "clean final weight must bind the exact cleanActionSequence"
        )
    clean_confirmation_fields = {
        field["name"] for field in specs["CLEAN_COMPLETION_CONFIRMED"]["fields"]
    }
    if not {
        "cleanActionSequence",
        "finalMeasurementUid",
        "cleanerPhysicalCloseConfirmed",
    } <= clean_confirmation_fields:
        raise ContractError(
            "clean completion confirmation must bind its generation and final measurement"
        )

    required_command_context = {
        "RESUME_CLEAN_OPERATION": {
            "configVersion",
            "configContentSha256",
            "recoveryGeneration",
            "nextCleanActionSequence",
        },
        "END_CLEAN_BEFORE_UNLOCK": {
            "parentCommandUid",
            "recoveryGeneration",
            "executionDeadlineMs",
        },
        "SAMPLE_FULLNESS": {
            "configVersion",
            "configContentSha256",
            "startExecutionWindowMs",
        },
        "MEASURE_BASELINE": {
            "configVersion",
            "configContentSha256",
            "startExecutionWindowMs",
        },
        "UNLOCK_CLEAN_DOOR": {
            "cleanActionSequence",
            "recoveryGeneration",
            "remainingOperationWindowMs",
            "parentCommandUid",
        },
    }
    for message_name, required_fields in required_command_context.items():
        actual_fields = {field["name"] for field in specs[message_name]["fields"]}
        if not required_fields <= actual_fields:
            raise ContractError(
                f"{message_name} lacks context/deadline fields: "
                + ", ".join(sorted(required_fields - actual_fields))
            )

    queue_range_fields = {
        field["name"] for field in specs["STATE_SNAPSHOT_END"]["fields"]
    }
    required_queue_range = {
        "pendingCriticalEventCount",
        "oldestPendingEventBootId",
        "oldestPendingEventSequence",
        "latestPendingEventBootId",
        "latestPendingEventSequence",
    }
    if not required_queue_range <= queue_range_fields:
        raise ContractError("state snapshot must preserve pending event boot/sequence range")

    config_device_fields = {
        field["name"] for field in specs["CONFIG_DEVICE_BLOCK"]["fields"]
    }
    config_port_fields = {
        field["name"] for field in specs["CONFIG_PORT_BLOCK"]["fields"]
    }
    required_device_config_fields = {
        "deliveryDoorOpenCommandSignalMs",
        "deliveryDoorCloseCommandSignalMs",
        "deliveryDoorTravelWaitMs",
    }
    required_port_config_fields = {
        "fullnessDistanceThresholdMm",
        "fullnessSampleCount",
        "fullnessMinimumValidSampleCount",
    }
    if not required_device_config_fields <= config_device_fields or (
        not required_port_config_fields <= config_port_fields
    ):
        raise ContractError(
            "configuration blocks miss configurable fullness/door timing fields"
        )
    return report


def _enum_wire_value(
    registry: Mapping[str, Any], field: Mapping[str, Any], value: Any
) -> Any:
    enum_name = field.get("enum")
    if not enum_name:
        return value
    values = registry["enums"][enum_name]["values"]
    if isinstance(value, str):
        if value not in values:
            raise ContractError(
                f"{field['name']}: unknown {enum_name} symbol {value!r}"
            )
        return values[value]
    if value not in values.values():
        raise ContractError(f"{field['name']}: invalid {enum_name} value {value!r}")
    return value


def _check_numeric_field(field: Mapping[str, Any], wire: Mapping[str, Any], value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field['name']}: integer value required")
    minimum = field.get("minimum", wire.get("minimum"))
    maximum = field.get("maximum", wire.get("maximum"))
    if minimum is not None and value < minimum:
        raise ContractError(f"{field['name']}: value is below minimum")
    if maximum is not None and value > maximum:
        raise ContractError(f"{field['name']}: value is above maximum")
    if "const" in field and value != field["const"]:
        raise ContractError(f"{field['name']}: expected constant {field['const']}")
    return value


_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_ZERO_SHA256 = "0" * 64


def _uart_condition_matches(
    values: Mapping[str, Any],
    expression: str,
) -> bool:
    match = re.fullmatch(
        r"([a-z][A-Za-z0-9]*)=([A-Z][A-Z0-9_]*|true|false)",
        expression,
    )
    if match is None:
        raise ContractError(f"unsupported UART field condition {expression!r}")
    expected: Any = match.group(2)
    if expected == "true":
        expected = True
    elif expected == "false":
        expected = False
    return values[match.group(1)] == expected


def _is_zero_uart_slot(field: Mapping[str, Any], value: Any) -> bool:
    field_type = field["type"]
    if field_type in {"u8", "u16", "u32", "u64", "i32"}:
        return value == 0
    if field_type == "bool":
        return value is False
    if field_type == "uuid":
        try:
            return uuid.UUID(str(value)).int == 0
        except ValueError:
            return False
    if field_type == "sha256":
        if isinstance(value, bytes):
            return value == bytes(32)
        return str(value) == _ZERO_SHA256
    return False


def uart_command_digest_preimage(
    registry: Mapping[str, Any],
    message_name: str,
    values: Mapping[str, Any],
) -> bytes:
    """Build the Registry command-digest preimage."""

    message = next(
        (item for item in registry["messages"] if item["name"] == message_name),
        None,
    )
    if message is None:
        raise ContractError(f"unknown UART message {message_name!r}")
    fields = expand_uart_fields(registry, message["payload"])
    names = [field["name"] for field in fields]
    if names[:2] != ["mcuCommandUid", "commandDigestSha256"]:
        raise ContractError(f"{message_name} does not use the commandIdentity prefix")
    normalized = dict(values)
    normalized["commandDigestSha256"] = _ZERO_SHA256
    payload = encode_uart_payload(
        registry,
        message_name,
        normalized,
        validate_semantics=False,
    )
    semantic_payload = payload[48:]
    profile = registry["digestProfiles"]["commandDigestSha256"]
    return (
        bytes.fromhex(profile["domainHex"])
        + bytes([message["id"]])
        + len(semantic_payload).to_bytes(2, "big")
        + semantic_payload
    )


def compute_uart_command_digest(
    registry: Mapping[str, Any],
    message_name: str,
    values: Mapping[str, Any],
) -> str:
    """Compute the Registry command digest, excluding its identity prefix."""

    return hashlib.sha256(
        uart_command_digest_preimage(registry, message_name, values)
    ).hexdigest()


def _validate_measurement_semantics(
    message_name: str,
    values: Mapping[str, Any],
) -> None:
    if "measurementStatus" not in values:
        return
    status = values["measurementStatus"]
    value_present = values["weightValuePresent"]
    value_kind = values["weightValueKind"]
    health = values["weightSensorHealth"]
    fault = values["faultCode"]
    if status == "STABLE":
        if (
            not value_present
            or value_kind != "STABLE_WINDOW_MEAN"
            or health != "OK"
            or fault != "NONE"
        ):
            raise ContractError(
                f"{message_name}: STABLE requires a stable-window value, OK health "
                "and NONE fault"
            )
        if values["sampleCount"] < 1:
            raise ContractError(f"{message_name}: STABLE requires at least one sample")
        return
    if value_present == (value_kind == "NONE"):
        raise ContractError(
            f"{message_name}: weightValuePresent must be the inverse of WeightValueKind.NONE"
        )
    if status == "UNSTABLE" and (
        not value_present
        or value_kind not in {"LAST_FOUR_MEAN", "AVAILABLE_SAMPLES_MEAN"}
    ):
        raise ContractError(
            f"{message_name}: UNSTABLE must preserve a fallback mean"
        )
    expected: dict[str, tuple[set[str], str]] = {
        "UNSTABLE": ({"OK"}, "WEIGHT_UNSTABLE"),
        "TIMEOUT": ({"TIMEOUT"}, "WEIGHT_TIMEOUT"),
        "SENSOR_FAULT": ({"SENSOR_FAULT", "UNKNOWN"}, "WEIGHT_SENSOR"),
        "OVERLOAD": ({"OVERLOAD"}, "WEIGHT_OVERLOAD"),
        "PROTOCOL_ERROR": ({"PROTOCOL_ERROR"}, "WEIGHT_PROTOCOL"),
        "CONFIG_ERROR": ({"CONFIG_ERROR"}, "WEIGHT_CONFIG"),
        "DISCONNECTED": ({"DISCONNECTED"}, "WEIGHT_DISCONNECTED"),
    }
    allowed_health, expected_fault = expected[status]
    if health not in allowed_health or fault != expected_fault:
        raise ContractError(
            f"{message_name}: {status} requires health {sorted(allowed_health)} "
            f"and fault {expected_fault}"
        )


def _validate_clean_manual_confirmation(
    message_name: str,
    values: Mapping[str, Any],
) -> None:
    if (
        "cleanDoorStateBasis" not in values
        or "cleanerPhysicalCloseConfirmed" not in values
    ):
        return
    expected_basis = (
        "CLEANER_CONFIRMATION"
        if values["cleanerPhysicalCloseConfirmed"]
        else "NOT_OBSERVABLE"
    )
    if values["cleanDoorStateBasis"] != expected_basis:
        raise ContractError(
            f"{message_name}: clean door basis must be {expected_basis}"
        )


def validate_uart_payload_semantics(
    registry: Mapping[str, Any],
    message_name: str,
    values: Mapping[str, Any],
    *,
    verify_command_digest: bool = True,
) -> None:
    message = next(
        (item for item in registry["messages"] if item["name"] == message_name),
        None,
    )
    if message is None:
        raise ContractError(f"unknown UART message {message_name!r}")
    fields = expand_uart_fields(registry, message["payload"])
    for field in fields:
        invalid_when = field.get("invalidWhen")
        if invalid_when and _uart_condition_matches(values, invalid_when) and not (
            _is_zero_uart_slot(field, values[field["name"]])
        ):
            raise ContractError(
                f"{message_name}.{field['name']}: invalid slot must use all-zero bytes"
            )
        if field["type"] == "uuid":
            is_zero = _is_zero_uart_slot(field, values[field["name"]])
            if field.get("zeroAllowed"):
                continue
            conditional = field.get("zeroAllowedWhen")
            if conditional:
                must_be_zero = _uart_condition_matches(values, conditional)
                if is_zero != must_be_zero:
                    raise ContractError(
                        f"{message_name}.{field['name']}: zero UUID differs from "
                        f"{conditional}"
                    )
            elif is_zero:
                raise ContractError(
                    f"{message_name}.{field['name']}: all-zero UUID is reserved"
                )

    if "measurementStatus" in values:
        _validate_measurement_semantics(message_name, values)
    if message_name in {"STATE_SNAPSHOT_PORT", "CLEAN_COMPLETION_CONFIRMED"}:
        _validate_clean_manual_confirmation(message_name, values)

    if message_name == "HELLO":
        bitmap = values["capabilityBitmap"]
        known = int(registry["capabilityPolicy"]["knownMaskHex"], 16)
        if bitmap & ~known:
            raise ContractError("HELLO capabilityBitmap contains unknown bits")
        mask_name = (
            "requiredEdgeMaskHex"
            if values["senderRole"] == "EDGE"
            else "requiredMcuMaskHex"
        )
        required = int(registry["capabilityPolicy"][mask_name], 16)
        if bitmap & required != required:
            raise ContractError("HELLO capabilityBitmap misses required capabilities")

    if message_name == "HELLO_ACK":
        bitmap = values["capabilityBitmap"]
        known = int(registry["capabilityPolicy"]["knownMaskHex"], 16)
        accepted = values["status"] == "ACCEPTED"
        if bitmap & ~known:
            raise ContractError("HELLO_ACK capabilityBitmap contains unknown bits")
        if accepted:
            required = int(registry["capabilityPolicy"]["requiredMcuMaskHex"], 16)
            if bitmap & required != required or values["errorCode"] != "NONE":
                raise ContractError(
                    "HELLO_ACK ACCEPTED requires the baseline capability mask and NONE"
                )
        elif values["errorCode"] == "NONE":
            raise ContractError("HELLO_ACK INCOMPATIBLE requires a non-NONE error")

    if message_name == "NACK" and values["errorCode"] == "NONE":
        raise ContractError("NACK cannot use errorCode NONE")

    if message_name == "SAFE_CLOSE":
        if values["scope"] == "ALL_DELIVERY_DOORS" and values["portNo"] != 0:
            raise ContractError("SAFE_CLOSE ALL_DELIVERY_DOORS requires portNo=0")
        if values["scope"] == "SINGLE_DELIVERY_DOOR" and values["portNo"] == 0:
            raise ContractError("SAFE_CLOSE SINGLE_DELIVERY_DOOR requires a port")

    if message_name in {"DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT"}:
        if values["physicalDoorStateBasis"] != "NOT_OBSERVABLE":
            raise ContractError(
                f"{message_name}: delivery-door physical state is not observable"
            )
        if values["command"] == "NONE":
            raise ContractError(f"{message_name}: command NONE is snapshot-only")
        status = values["outputStatus"]
        if status == "COMMAND_DISPATCHED":
            if values["faultCode"] != "NONE":
                raise ContractError(
                    f"{message_name}: successful output result requires faultCode NONE"
                )
            if values["actualOutputMs"] == 0:
                raise ContractError(
                    f"{message_name}: dispatched output must report nonzero duration"
                )
        elif status == "COALESCED_WITH_EXISTING_CLOSE":
            if values["faultCode"] != "NONE" or values["actualOutputMs"] != 0:
                raise ContractError(
                    f"{message_name}: coalesced close must report zero new output"
                )
        elif status == "PARTIAL_OUTPUT_INTERRUPTED":
            if values["faultCode"] != "DELIVERY_DOOR_OUTPUT_INTERRUPTED":
                raise ContractError(
                    f"{message_name}: interrupted output has the wrong fault"
                )
            if values["actualOutputMs"] == 0:
                raise ContractError(
                    f"{message_name}: partial output must report nonzero duration"
                )
        elif status == "OUTPUT_REJECTED":
            if values["faultCode"] not in {
                "DELIVERY_DOOR_OUTPUT_REJECTED",
                "DELIVERY_DOOR_HIL_NOT_QUALIFIED",
            }:
                raise ContractError(
                    f"{message_name}: rejected output has the wrong fault"
                )
            if values["actualOutputMs"] != 0:
                raise ContractError(
                    f"{message_name}: rejected output must report zero duration"
                )
        elif status == "NOT_DISPATCHED":
            raise ContractError(
                f"{message_name}: NOT_DISPATCHED is reserved for snapshots"
            )
        if (
            message_name == "SAFE_CLOSE_RESULT"
            and values["command"] != "CLOSE"
        ):
            raise ContractError("SAFE_CLOSE_RESULT must report a CLOSE command")

    if message_name == "CONFIG_BEGIN":
        if values["partCount"] != values["expectedPortCount"] + 3:
            raise ContractError("CONFIG_BEGIN partCount must equal expectedPortCount+3")

    if message_name == "CONFIG_DEVICE_BLOCK":
        if (
            values["deliveryDoorOpenCommandSignalMs"]
            >= values["deliveryDoorTravelWaitMs"]
            or values["deliveryDoorCloseCommandSignalMs"]
            >= values["deliveryDoorTravelWaitMs"]
        ):
            raise ContractError(
                "CONFIG_DEVICE_BLOCK door signal duration must be below travel wait"
            )

    if message_name == "CONFIG_PORT_BLOCK":
        if values["partIndex"] != values["portNo"] + 2:
            raise ContractError("CONFIG_PORT_BLOCK partIndex must equal portNo+2")
        if values["weightMinimumGrams"] >= values["weightMaximumGrams"]:
            raise ContractError(
                "CONFIG_PORT_BLOCK weightMinimumGrams must be below maximum"
            )
        if (
            values["fullnessMinimumValidSampleCount"]
            > values["fullnessSampleCount"]
        ):
            raise ContractError(
                "CONFIG_PORT_BLOCK minimum valid fullness samples exceed total samples"
            )

    if message_name == "CONFIG_COMMIT":
        if values["partIndex"] != values["partCount"]:
            raise ContractError("CONFIG_COMMIT partIndex must equal partCount")

    if message_name in {"FAULT_OBSERVED", "SAFETY_SENSOR_EVENT"}:
        zero_work = uuid.UUID(str(values["workUid"])).int == 0
        if (values["workType"] == "NONE") != zero_work:
            raise ContractError(
                f"{message_name}: workType NONE must match the all-zero workUid"
            )

    if message_name == "FAULT_OBSERVED":
        faults_by_component = {
            "UART": {"UART_PROTOCOL", "UART_STORAGE"},
            "DELIVERY_DOOR": {
                "DELIVERY_DOOR_OUTPUT_INTERRUPTED",
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
        }
        if values["faultCode"] not in faults_by_component[values["component"]]:
            raise ContractError("FAULT_OBSERVED faultCode differs from component")

    if message_name in {"FULLNESS_SAMPLE_RESULT", "STATE_SNAPSHOT_PORT"}:
        basis = values["fullnessSampleBasis"]
        distance_present = values["representativeDistancePresent"]
        if basis == "MEASURED_MEDIAN":
            if not distance_present:
                raise ContractError(
                    f"{message_name}: measured fullness requires representative distance"
                )
        elif (
            values["fullnessSensorValue"] != "CLEAR"
            or distance_present
        ):
            raise ContractError(
                f"{message_name}: no-echo/insufficient fullness fallback must be CLEAR "
                "without a distance"
            )
        if message_name == "FULLNESS_SAMPLE_RESULT":
            if values["validSampleCount"] > values["requestedSampleCount"]:
                raise ContractError(
                    "FULLNESS_SAMPLE_RESULT valid samples exceed requested samples"
                )

    if message_name in {"SAFETY_SENSOR_EVENT", "STATE_SNAPSHOT_PORT"}:
        smoke_health_name = (
            "smokeSensorHealth"
            if "smokeSensorHealth" in values
            else "sensorHealth"
        )
        if values[smoke_health_name] == "OK":
            if values["smokeState"] == "UNKNOWN":
                raise ContractError("healthy smoke sample cannot be UNKNOWN")
            if (
                message_name == "SAFETY_SENSOR_EVENT"
                and values["faultCode"] != "NONE"
            ):
                raise ContractError(
                    "healthy SAFETY_SENSOR_EVENT requires faultCode NONE"
                )
        else:
            if values["smokeState"] != "UNKNOWN":
                raise ContractError("failed smoke sample must be UNKNOWN")
            if (
                message_name == "SAFETY_SENSOR_EVENT"
                and values["faultCode"] != "SMOKE_SENSOR"
            ):
                raise ContractError(
                    "failed SAFETY_SENSOR_EVENT requires SMOKE_SENSOR fault"
                )

    if message_name == "STATE_SNAPSHOT_BEGIN":
        zero_work = uuid.UUID(str(values["activeWorkUid"])).int == 0
        if values["activeWorkType"] == "NONE":
            if not zero_work or values["activePortNo"] != 0:
                raise ContractError(
                    "STATE_SNAPSHOT_BEGIN idle state requires zero work UID/port"
                )
            if values["activeWorkPhase"] != "IDLE":
                raise ContractError("STATE_SNAPSHOT_BEGIN idle work requires IDLE phase")
        elif values["activeWorkType"] == "CONFIG_APPLICATION":
            if (
                zero_work
                or values["activePortNo"] != 0
                or values["activeWorkPhase"] != "CONFIG_STAGING"
            ):
                raise ContractError(
                    "STATE_SNAPSHOT_BEGIN configuration work requires "
                    "nonzero UID, port 0 and CONFIG_STAGING"
                )
        elif zero_work or values["activePortNo"] == 0 or values["activeWorkPhase"] == "IDLE":
            raise ContractError(
                "STATE_SNAPSHOT_BEGIN active work requires nonzero UID/port/phase"
            )
        if values["stagingValid"]:
            if (
                uuid.UUID(str(values["stagingApplicationUid"])).int == 0
                or values["stagingConfigVersion"] == 0
                or values["stagingMcuPayloadSha256"] == _ZERO_SHA256
                or not 4 <= values["stagingPartCount"] <= 9
                or values["stagingPartCount"] != values["portCount"] + 3
                or not values["stagingReceivedPartBitmap"] & 0x0001
                or values["stagingReceivedPartBitmap"]
                & ~((1 << values["stagingPartCount"]) - 1)
            ):
                raise ContractError(
                    "valid configuration staging has an invalid identity/part bitmap"
                )
        applied_digest_zero_flags = (
            values["appliedContentSha256"] == _ZERO_SHA256,
            values["appliedMcuPayloadSha256"] == _ZERO_SHA256,
        )
        if (
            values["appliedConfigVersion"] == 0
            and not all(applied_digest_zero_flags)
        ) or (
            values["appliedConfigVersion"] > 0
            and any(applied_digest_zero_flags)
        ):
            raise ContractError(
                "STATE_SNAPSHOT_BEGIN applied configuration tuple is not all-or-none"
            )
        if values["partCount"] != values["portCount"] + 2:
            raise ContractError(
                "STATE_SNAPSHOT_BEGIN partCount must equal portCount+2"
            )

    if message_name == "STATE_SNAPSHOT_PORT":
        bitmap = registry["bitmaps"]["PortFaultBitmap"]
        if values["faultBitmap"] & ~int(bitmap["knownMaskHex"], 16):
            raise ContractError("STATE_SNAPSHOT_PORT faultBitmap contains reserved bits")
        if values["deliveryDoorPhysicalStateBasis"] != "NOT_OBSERVABLE":
            raise ContractError(
                "STATE_SNAPSHOT_PORT cannot claim delivery-door physical state"
            )
        no_door_command = values["lastDeliveryDoorCommand"] == "NONE"
        if no_door_command != (
            values["lastDeliveryDoorOutputStatus"] == "NOT_DISPATCHED"
            and values["lastDeliveryDoorActualOutputMs"] == 0
        ):
            raise ContractError(
                "STATE_SNAPSHOT_PORT last door command/result tuple is inconsistent"
            )
        expected_fault_bitmap = 0
        if values["lastDeliveryDoorOutputStatus"] in {
            "PARTIAL_OUTPUT_INTERRUPTED",
            "OUTPUT_REJECTED",
        }:
            expected_fault_bitmap |= (
                1 << bitmap["bits"]["DELIVERY_DOOR_OUTPUT_FAULT"]
            )
        health_fields = (
            ("CLEAN_SOLENOID_FAULT", "cleanSolenoidHealth"),
            ("WEIGHT_SENSOR_FAULT", "weightSensorHealth"),
            ("SMOKE_SENSOR_FAULT", "smokeSensorHealth"),
        )
        for bit_name, health_name in health_fields:
            if values[health_name] != "OK":
                expected_fault_bitmap |= 1 << bitmap["bits"][bit_name]
        if values["faultBitmap"] != expected_fault_bitmap:
            raise ContractError(
                "STATE_SNAPSHOT_PORT faultBitmap differs from typed health fields"
            )
        if values["partIndex"] != values["portNo"] + 1:
            raise ContractError(
                "STATE_SNAPSHOT_PORT partIndex must equal portNo+1"
            )

    if message_name == "BOOT_RECONCILIATION_RESULT":
        no_work = values["decision"] == "CONFIRM_NO_ACTIVE_WORK"
        if no_work:
            if (
                values["activeWorkType"] != "NONE"
                or values["activePortNo"] != 0
                or values["recoveryGeneration"] != 0
                or values["nextCleanActionSequence"] != 0
            ):
                raise ContractError(
                    "CONFIRM_NO_ACTIVE_WORK result must not expose active work"
                )
        elif (
            values["activeWorkType"] != "CLEAN_OPERATION"
            or values["activePortNo"] == 0
            or values["recoveryGeneration"] == 0
            or values["nextCleanActionSequence"] == 0
        ):
            raise ContractError(
                "RESUME_CLEAN_OPERATION result lacks clean recovery context"
            )
        if (
            values["status"] == "ACCEPTED"
            and values["faultCode"] != "NONE"
        ) or (
            values["status"] == "REJECTED"
            and values["faultCode"] == "NONE"
        ):
            raise ContractError(
                "BOOT_RECONCILIATION_RESULT status/faultCode mismatch"
            )

    if message_name == "STATE_SNAPSHOT_END":
        if values["partIndex"] != values["partCount"]:
            raise ContractError(
                "STATE_SNAPSHOT_END partIndex must equal partCount"
            )
        queue_fields = (
            "oldestPendingEventBootId",
            "oldestPendingEventSequence",
            "latestPendingEventBootId",
            "latestPendingEventSequence",
        )
        if values["pendingCriticalEventCount"] == 0:
            if any(values[name] != 0 for name in queue_fields):
                raise ContractError("empty pending-event queue requires zero range")
        elif any(values[name] == 0 for name in queue_fields):
            raise ContractError("nonempty pending-event queue requires complete range")

    if message_name == "CONFIG_APPLY_RESULT":
        if values["status"] == "APPLIED" and values["faultCode"] != "NONE":
            raise ContractError("CONFIG_APPLY_RESULT APPLIED requires NONE fault")
        if values["status"] == "FAILED" and values["faultCode"] == "NONE":
            raise ContractError("CONFIG_APPLY_RESULT FAILED requires a fault")

    if message_name == "CLEAN_COMPLETION_CONFIRMED":
        if (
            values["lockPowerState"] != "DEENERGIZED"
            or values["cleanDoorStateBasis"] != "CLEANER_CONFIRMATION"
            or not values["cleanerPhysicalCloseConfirmed"]
        ):
            raise ContractError(
                "CLEAN_COMPLETION_CONFIRMED requires a deenergized output and explicit "
                "cleaner confirmation"
            )

    if verify_command_digest and "commandDigestSha256" in values:
        expected = compute_uart_command_digest(registry, message_name, values)
        actual = values["commandDigestSha256"]
        if isinstance(actual, bytes):
            actual = actual.hex()
        if actual != expected:
            raise ContractError(f"{message_name}: commandDigestSha256 mismatch")


def encode_uart_payload(
    registry: Mapping[str, Any],
    message_name: str,
    values: Mapping[str, Any],
    *,
    validate_semantics: bool = True,
) -> bytes:
    message = next(
        (item for item in registry["messages"] if item["name"] == message_name),
        None,
    )
    if message is None:
        raise ContractError(f"unknown UART message {message_name!r}")
    fields = expand_uart_fields(registry, message["payload"])
    expected = {field["name"] for field in fields}
    if set(values) != expected:
        missing = expected - set(values)
        extra = set(values) - expected
        raise ContractError(
            f"{message_name} payload keys differ; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    output = bytearray()
    for field in fields:
        wire = registry["wireTypes"][field["type"]]
        value = _enum_wire_value(registry, field, values[field["name"]])
        field_type = field["type"]
        if field_type in {"u8", "u16", "u32", "u64", "i32"}:
            number = _check_numeric_field(field, wire, value)
            size = wire["minimumSize"]
            output.extend(number.to_bytes(size, "big", signed=field_type == "i32"))
        elif field_type == "bool":
            if not isinstance(value, bool):
                raise ContractError(f"{field['name']}: boolean value required")
            if "const" in field and value != field["const"]:
                raise ContractError(f"{field['name']}: constant differs")
            output.append(1 if value else 0)
        elif field_type == "uuid":
            try:
                output.extend(uuid.UUID(str(value)).bytes)
            except ValueError as exc:
                raise ContractError(f"{field['name']}: invalid UUID") from exc
        elif field_type == "sha256":
            if isinstance(value, bytes):
                raw = value
            else:
                try:
                    raw = bytes.fromhex(str(value))
                except ValueError as exc:
                    raise ContractError(f"{field['name']}: invalid SHA-256 hex") from exc
            if len(raw) != 32:
                raise ContractError(f"{field['name']}: SHA-256 must be 32 bytes")
            output.extend(raw)
        elif field_type == "string_u8":
            if not isinstance(value, str):
                raise ContractError(f"{field['name']}: string value required")
            raw = value.encode("utf-8")
            if len(raw) > field["maxLength"]:
                raise ContractError(f"{field['name']}: UTF-8 value is too long")
            output.append(len(raw))
            output.extend(raw)
        else:
            raise ContractError(f"{field['name']}: unsupported wire type {field_type}")
    maximum = registry["protocol"]["maximumPayloadLength"]
    if len(output) > maximum:
        raise ContractError(f"{message_name}: payload exceeds {maximum} bytes")
    if validate_semantics:
        validate_uart_payload_semantics(registry, message_name, values)
    return bytes(output)


def decode_uart_payload(
    registry: Mapping[str, Any],
    message_name: str,
    payload: bytes,
) -> dict[str, Any]:
    message = next(
        (item for item in registry["messages"] if item["name"] == message_name),
        None,
    )
    if message is None:
        raise ContractError(f"unknown UART message {message_name!r}")
    fields = expand_uart_fields(registry, message["payload"])
    offset = 0
    result: dict[str, Any] = {}
    for field in fields:
        field_type = field["type"]
        wire = registry["wireTypes"][field_type]
        if field_type == "string_u8":
            if offset >= len(payload):
                raise ContractError(f"{field['name']}: missing string length")
            length = payload[offset]
            offset += 1
            if length > field["maxLength"] or offset + length > len(payload):
                raise ContractError(f"{field['name']}: invalid string length")
            try:
                value: Any = payload[offset : offset + length].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContractError(f"{field['name']}: invalid UTF-8") from exc
            offset += length
        else:
            size = wire["minimumSize"]
            if offset + size > len(payload):
                raise ContractError(f"{field['name']}: truncated payload")
            raw = payload[offset : offset + size]
            offset += size
            if field_type in {"u8", "u16", "u32", "u64", "i32"}:
                value = int.from_bytes(raw, "big", signed=field_type == "i32")
            elif field_type == "bool":
                if raw[0] not in (0, 1):
                    raise ContractError(f"{field['name']}: invalid boolean")
                value = raw[0] == 1
            elif field_type == "uuid":
                value = str(uuid.UUID(bytes=raw))
            elif field_type == "sha256":
                value = raw.hex()
            else:
                raise ContractError(f"{field['name']}: unsupported wire type")

        if field.get("enum"):
            values = registry["enums"][field["enum"]]["values"]
            by_value = {number: symbol for symbol, number in values.items()}
            if value not in by_value:
                raise ContractError(f"{field['name']}: unknown enum value {value}")
            value = by_value[value]
        elif field_type in {"u8", "u16", "u32", "u64", "i32"}:
            _check_numeric_field(field, wire, value)
        if "const" in field and value != field["const"]:
            raise ContractError(f"{field['name']}: constant differs")
        result[field["name"]] = value
    if offset != len(payload):
        raise ContractError(f"{message_name}: trailing payload bytes")
    validate_uart_payload_semantics(registry, message_name, result)
    return result


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_uart_frame(
    registry: Mapping[str, Any],
    message_type: int,
    flags: int,
    tx_sequence: int,
    payload: bytes,
) -> bytes:
    protocol = registry["protocol"]
    if not 0 <= message_type <= 255:
        raise ContractError("messageType must fit uint8")
    if flags & ~0x01:
        raise ContractError("UART 1.0 only defines ACK_REQUIRED flag bit")
    if not 1 <= tx_sequence <= 0xFFFFFFFF:
        raise ContractError("txSequence must be a positive uint32")
    if len(payload) > protocol["maximumPayloadLength"]:
        raise ContractError("payload exceeds protocol maximum")
    body = struct.pack(
        ">BBBBHI",
        protocol["major"],
        protocol["minor"],
        message_type,
        flags,
        len(payload),
        tx_sequence,
    ) + payload
    crc = crc16_ccitt_false(body)
    return bytes.fromhex(protocol["magicHex"]) + body + struct.pack(">H", crc)


def decode_uart_frame(
    registry: Mapping[str, Any],
    frame: bytes,
    require_known_message: bool = False,
    sender_role: str | None = None,
) -> dict[str, Any]:
    protocol = registry["protocol"]
    if len(frame) < 14 or len(frame) > protocol["maximumFrameLength"]:
        raise ContractError("invalid UART frame length")
    if frame[:2] != bytes.fromhex(protocol["magicHex"]):
        raise ContractError("invalid UART magic")
    major, minor, message_type, flags, payload_length, tx_sequence = struct.unpack(
        ">BBBBHI", frame[2:12]
    )
    if payload_length > protocol["maximumPayloadLength"]:
        raise ContractError("invalid UART payload length")
    if len(frame) != 14 + payload_length:
        raise ContractError("UART frame length does not match header")
    expected_crc = int.from_bytes(frame[-2:], "big")
    actual_crc = crc16_ccitt_false(frame[2:-2])
    if expected_crc != actual_crc:
        raise ContractError("UART CRC mismatch")
    if major != protocol["major"] or minor != protocol["minor"]:
        raise ContractError("unsupported UART protocol version")
    if flags & ~0x01:
        raise ContractError("unsupported UART flags")
    if tx_sequence == 0:
        raise ContractError("txSequence zero is reserved")
    by_id = {message["id"]: message for message in registry["messages"]}
    message = by_id.get(message_type)
    message_name = message["name"] if message else None
    if require_known_message and message_name is None:
        raise ContractError("unsupported UART message type")
    if message is not None:
        expected_ack = message["ackRequired"]
        if bool(flags & 0x01) != expected_ack:
            raise ContractError("ACK_REQUIRED flag differs from the message Registry")
        if sender_role is not None:
            if sender_role not in {"EDGE", "MCU"}:
                raise ContractError("sender_role must be EDGE or MCU")
            allowed_direction = (
                "EDGE_TO_MCU" if sender_role == "EDGE" else "MCU_TO_EDGE"
            )
            if message["direction"] not in {"BIDIRECTIONAL", allowed_direction}:
                raise ContractError("message direction differs from the sender role")
    payload = frame[12:-2]
    return {
        "protocolMajor": major,
        "protocolMinor": minor,
        "messageType": message_type,
        "messageName": message_name,
        "flags": flags,
        "payloadLength": payload_length,
        "txSequence": tx_sequence,
        "payload": payload,
        "crc16": actual_crc,
    }


class UartStreamParser:
    """Bounded UART 1.0 stream parser shared by tests and generated codecs."""

    def __init__(
        self,
        registry: Mapping[str, Any],
        *,
        sender_role: str,
    ) -> None:
        if sender_role not in {"EDGE", "MCU"}:
            raise ContractError("sender_role must be EDGE or MCU")
        self._registry = registry
        self._sender_role = sender_role
        self._buffer = bytearray()
        self._candidate_started_ms: int | None = None
        self.diagnostics: list[str] = []

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def _discard_candidate_prefix(self, diagnostic: str) -> None:
        self.diagnostics.append(diagnostic)
        del self._buffer[0]
        self._candidate_started_ms = None

    def feed(self, data: bytes, *, now_ms: int) -> list[dict[str, Any]]:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ContractError("now_ms must be a non-negative integer")
        self._buffer.extend(data)
        limit = self._registry["streamParser"]["inputBufferLimit"]
        if len(self._buffer) > limit:
            overflow = len(self._buffer) - limit
            del self._buffer[:overflow]
            self._candidate_started_ms = None
            self.diagnostics.append("BUFFER_OVERFLOW")

        magic = bytes.fromhex(self._registry["protocol"]["magicHex"])
        maximum_payload = self._registry["protocol"]["maximumPayloadLength"]
        deadline = self._registry["streamParser"]["frameAssemblyDeadlineMs"]
        frames: list[dict[str, Any]] = []
        while True:
            magic_index = self._buffer.find(magic)
            if magic_index < 0:
                keep = 1 if self._buffer.endswith(magic[:1]) else 0
                if len(self._buffer) > keep:
                    del self._buffer[: len(self._buffer) - keep]
                self._candidate_started_ms = None
                break
            if magic_index:
                del self._buffer[:magic_index]
                self._candidate_started_ms = None
                self.diagnostics.append("NOISE_DISCARDED")

            if self._candidate_started_ms is None:
                self._candidate_started_ms = now_ms
            if len(self._buffer) < 12:
                if now_ms - self._candidate_started_ms >= deadline:
                    self._discard_candidate_prefix("FRAME_TIMEOUT")
                    continue
                break

            payload_length = int.from_bytes(self._buffer[6:8], "big")
            if payload_length > maximum_payload:
                self._discard_candidate_prefix("INVALID_LENGTH")
                continue
            frame_length = 14 + payload_length
            if len(self._buffer) < frame_length:
                if now_ms - self._candidate_started_ms >= deadline:
                    self._discard_candidate_prefix("FRAME_TIMEOUT")
                    continue
                break

            candidate = bytes(self._buffer[:frame_length])
            expected_crc = int.from_bytes(candidate[-2:], "big")
            actual_crc = crc16_ccitt_false(candidate[2:-2])
            if expected_crc != actual_crc:
                self._discard_candidate_prefix("CRC_INVALID")
                continue
            try:
                decoded = decode_uart_frame(
                    self._registry,
                    candidate,
                    require_known_message=True,
                    sender_role=self._sender_role,
                )
            except ContractError as exc:
                # CRC-valid protocol violations consume the entire declared frame;
                # scanning its payload for another magic would create a false frame.
                self.diagnostics.append(
                    "SEMANTIC_REJECTED:" + str(exc).split(":", 1)[-1].strip()
                )
                del self._buffer[:frame_length]
                self._candidate_started_ms = None
                continue
            frames.append(decoded)
            del self._buffer[:frame_length]
            self._candidate_started_ms = None
        return frames


def flag_bitmap(flag_names: Iterable[str]) -> int:
    bitmap = 0
    for name in flag_names:
        if name != "ACK_REQUIRED":
            raise ContractError(f"unknown UART flag {name!r}")
        bitmap |= 0x01
    return bitmap
