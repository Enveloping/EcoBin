"""Validate the EcoBin F-09 OpenAPI 3.1 transport contract.

The source file intentionally uses JSON syntax, which is a valid YAML 1.2
subset. This keeps contract validation available on Python 3.11 without
installing a YAML or OpenAPI package on an edge-development machine.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from contractlib import CONTRACTS_ROOT, ContractError, load_json  # noqa: E402


OPENAPI_PATH = CONTRACTS_ROOT / "http" / "openapi.yaml"
HTTP_EXAMPLES_ROOT = CONTRACTS_ROOT / "examples" / "http"
HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
}
SIGNED_NOTIFICATION_SECURITY = {
    "wechatPaySignature",
    "wechatPayTimestamp",
    "wechatPayNonce",
    "wechatPaySerial",
}


def load_openapi() -> dict[str, Any]:
    document = load_json(OPENAPI_PATH)
    if not isinstance(document, dict):
        raise ContractError("HTTP OpenAPI source must be an object")
    return document


def resolve_local_ref(document: Mapping[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ContractError(f"OpenAPI only permits local $ref in F-09: {ref}")
    target: Any = document
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or token not in target:
            raise ContractError(f"unresolved OpenAPI $ref: {ref}")
        target = target[token]
    return target


def _walk(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    values: list[tuple[str, Any]] = [(path, value)]
    if isinstance(value, Mapping):
        for key, child in value.items():
            values.extend(_walk(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            values.extend(_walk(child, f"{path}[{index}]"))
    return values


def _type_matches(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    raise ContractError(f"unsupported HTTP example schema type: {type_name}")


def validate_instance(
    instance: Any,
    schema: Mapping[str, Any],
    document: Mapping[str, Any],
    *,
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_instance(
            instance,
            resolve_local_ref(document, str(schema["$ref"])),
            document,
            path=path,
        )
        return

    if "allOf" in schema:
        for item in schema["allOf"]:
            validate_instance(instance, item, document, path=path)
    if "oneOf" in schema:
        matches = 0
        for item in schema["oneOf"]:
            try:
                validate_instance(instance, item, document, path=path)
            except ContractError:
                continue
            matches += 1
        if matches != 1:
            raise ContractError(f"{path}: expected exactly one matching oneOf branch")
        return

    if "const" in schema and instance != schema["const"]:
        raise ContractError(f"{path}: value differs from const")
    if "enum" in schema and instance not in schema["enum"]:
        raise ContractError(f"{path}: value is outside enum")

    type_name = schema.get("type")
    if type_name is not None and not _type_matches(instance, str(type_name)):
        raise ContractError(f"{path}: expected {type_name}")

    if isinstance(instance, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if minimum_length is not None and len(instance) < minimum_length:
            raise ContractError(f"{path}: string is shorter than minLength")
        if maximum_length is not None and len(instance) > maximum_length:
            raise ContractError(f"{path}: string is longer than maxLength")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(str(pattern), instance) is None:
            raise ContractError(f"{path}: string does not match pattern")
        if schema.get("format") == "uuid":
            try:
                uuid.UUID(instance)
            except ValueError as exc:
                raise ContractError(f"{path}: invalid UUID") from exc
        if schema.get("format") == "date-time":
            try:
                datetime.datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ContractError(f"{path}: invalid date-time") from exc

    if isinstance(instance, int) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and instance < minimum:
            raise ContractError(f"{path}: integer is below minimum")
        if maximum is not None and instance > maximum:
            raise ContractError(f"{path}: integer is above maximum")

    if isinstance(instance, list):
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise ContractError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(instance):
                validate_instance(
                    item,
                    item_schema,
                    document,
                    path=f"{path}[{index}]",
                )

    if isinstance(instance, Mapping):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise ContractError(f"{path}: missing required fields {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(instance) - set(properties)
            if unknown:
                raise ContractError(f"{path}: unknown fields {sorted(unknown)}")
        for key, child in instance.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, Mapping):
                validate_instance(
                    child,
                    child_schema,
                    document,
                    path=f"{path}.{key}",
                )


def _operation_security(operation: Mapping[str, Any]) -> set[frozenset[str]]:
    security = operation.get("security")
    if not isinstance(security, list):
        raise ContractError(f"{operation.get('operationId')}: security must be explicit")
    result: set[frozenset[str]] = set()
    for requirement in security:
        if not isinstance(requirement, Mapping):
            raise ContractError("OpenAPI security requirement must be an object")
        result.add(frozenset(str(key) for key in requirement))
    return result


def _assert_security(
    document: Mapping[str, Any],
    path: str,
    method: str,
    expected: set[frozenset[str]],
) -> None:
    operation = document["paths"][path][method]
    actual = _operation_security(operation)
    if actual != expected:
        raise ContractError(
            f"{method.upper()} {path}: security {actual} differs from {expected}"
        )


def _validate_refs(document: Mapping[str, Any]) -> int:
    count = 0
    for path, value in _walk(document):
        if isinstance(value, Mapping) and "$ref" in value:
            resolve_local_ref(document, str(value["$ref"]))
            count += 1
        if path.endswith(".example") and isinstance(value, float):
            raise ContractError(f"{path}: floating-point HTTP example is forbidden")
    return count


def _validate_operations(document: Mapping[str, Any]) -> int:
    operation_ids: list[str] = []
    paths = document.get("paths")
    if not isinstance(paths, Mapping) or not paths:
        raise ContractError("OpenAPI paths must be a non-empty object")
    for path, path_item in paths.items():
        if not str(path).startswith("/api/v1/"):
            raise ContractError(f"target HTTP path is outside /api/v1: {path}")
        if not isinstance(path_item, Mapping):
            raise ContractError(f"{path}: path item must be an object")
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            if not isinstance(operation, Mapping):
                raise ContractError(f"{method.upper()} {path}: operation must be an object")
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                raise ContractError(f"{method.upper()} {path}: operationId is required")
            if "responses" not in operation:
                raise ContractError(f"{operation_id}: responses are required")
            _operation_security(operation)
            operation_ids.append(operation_id)
    if len(operation_ids) != len(set(operation_ids)):
        raise ContractError("OpenAPI operationId values must be unique")
    return len(operation_ids)


def _validate_security(document: Mapping[str, Any]) -> None:
    schemes = document["components"]["securitySchemes"]
    expected_schemes = {
        "webSessionCookie",
        "webCsrfToken",
        "miniappBearer",
        *SIGNED_NOTIFICATION_SECURITY,
    }
    if set(schemes) != expected_schemes:
        raise ContractError("HTTP security schemes differ from the frozen three entry types")
    cookie = schemes["webSessionCookie"]
    if (
        cookie.get("type") != "apiKey"
        or cookie.get("in") != "cookie"
        or cookie.get("name") != "__Host-ecobin-web-session"
    ):
        raise ContractError("Web session Cookie contract changed")
    if schemes["webCsrfToken"]["name"] != "X-CSRF-TOKEN":
        raise ContractError("Web CSRF header must be X-CSRF-TOKEN")
    bearer = schemes["miniappBearer"]
    if bearer.get("type") != "http" or bearer.get("scheme") != "bearer":
        raise ContractError("miniapp security must be HTTP Bearer")

    anonymous: set[frozenset[str]] = set()
    _assert_security(document, "/api/v1/web/auth/csrf-token", "get", anonymous)
    csrf_only = {frozenset({"webCsrfToken"})}
    _assert_security(document, "/api/v1/web/auth/sessions", "post", csrf_only)
    _assert_security(
        document,
        "/api/v1/web/platform/auth/sessions",
        "post",
        csrf_only,
    )
    cookie_only = {frozenset({"webSessionCookie"})}
    cookie_csrf = {frozenset({"webSessionCookie", "webCsrfToken"})}
    for prefix in (
        "/api/v1/web/auth/sessions/current",
        "/api/v1/web/platform/auth/sessions/current",
    ):
        _assert_security(document, prefix, "get", cookie_only)
        _assert_security(document, prefix, "delete", cookie_csrf)

    _assert_security(document, "/api/v1/miniapp/auth/sessions", "post", anonymous)
    bearer_only = {frozenset({"miniappBearer"})}
    for prefix in (
        "/api/v1/miniapp/auth/sessions/current",
        "/api/v1/miniapp-staff/auth/sessions/current",
    ):
        _assert_security(document, prefix, "get", bearer_only)
        _assert_security(document, prefix, "delete", bearer_only)

    signed = {frozenset(SIGNED_NOTIFICATION_SECURITY)}
    for path in (
        "/api/v1/wechat-pay/notifications/native-payments",
        "/api/v1/wechat-pay/notifications/merchant-transfers",
    ):
        _assert_security(document, path, "post", signed)


def _validate_common_schemas(document: Mapping[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    required = {
        "UuidV4",
        "ProblemDetail",
        "AcceptedOperation",
        "AcceptedOperationEnvelope",
        "PageData",
        "CursorPageData",
        "MoneyCny",
        "UnitPriceCnyPerKg",
        "BusinessWeightKg",
        "RawWeightGrams",
        "UtcTimestamp",
        "PublicUid",
        "ExpectedVersion",
        "MiniappSessionCreated",
        "MiniappSessionView",
        "MiniappSessionCreatedEnvelope",
        "MiniappSessionViewEnvelope",
    }
    missing = required - set(schemas)
    if missing:
        raise ContractError(f"OpenAPI common schemas are missing {sorted(missing)}")
    if "Uuid" in schemas:
        raise ContractError("generic UUID schema is forbidden; frozen identities use UUIDv4")
    for name in ("MoneyCny", "UnitPriceCnyPerKg", "BusinessWeightKg"):
        if schemas[name].get("type") != "string":
            raise ContractError(f"{name} must remain a lossless string")
    if schemas["RawWeightGrams"].get("type") != "integer":
        raise ContractError("RawWeightGrams must remain a signed integer")

    problem_required = set(schemas["ProblemDetail"].get("required", []))
    if problem_required != {"code", "message", "requestId", "retryable", "details"}:
        raise ContractError("ProblemDetail required fields changed")
    accepted_required = set(schemas["AcceptedOperation"].get("required", []))
    if accepted_required != {
        "operationId",
        "resourceId",
        "status",
        "statusUrl",
        "recommendedPollAfterMs",
    }:
        raise ContractError("AcceptedOperation required fields changed")

    web_login = schemas["WebLoginRequest"]
    if set(web_login.get("properties", {})) != {"loginName", "password"}:
        raise ContractError("Web login must only submit loginName and password")
    for session_name in ("MiniappSessionCreated", "MiniappSessionView"):
        miniapp_session = schemas[session_name]["properties"]
        if set(miniapp_session["audience"]["enum"]) != {
            "miniapp",
            "miniapp-staff",
        }:
            raise ContractError(f"{session_name} audience values changed")
        if set(miniapp_session["entryMode"]["enum"]) != {
            "USER",
            "CLEANING",
            "MANAGEMENT",
        }:
            raise ContractError(f"{session_name} entryMode values changed")
    created_session = schemas["MiniappSessionCreated"]
    access_token = created_session["properties"].get("accessToken", {})
    if (
        "accessToken" not in created_session.get("required", [])
        or access_token.get("readOnly") is not True
        or "writeOnly" in access_token
    ):
        raise ContractError("created miniapp session must return a read-only accessToken")
    current_properties = schemas["MiniappSessionView"]["properties"]
    if {"accessToken", "tokenType", "isNewRegistration"} & set(current_properties):
        raise ContractError("current miniapp session view must not return login credentials")

    responses = document["components"]["responses"]
    created_ref = responses["MiniappSessionCreated"]["content"][
        "application/json"
    ]["schema"].get("$ref")
    current_ref = responses["MiniappSessionCurrent"]["content"][
        "application/json"
    ]["schema"].get("$ref")
    if created_ref != "#/components/schemas/MiniappSessionCreatedEnvelope":
        raise ContractError("miniapp login must use the credential-bearing created model")
    if current_ref != "#/components/schemas/MiniappSessionViewEnvelope":
        raise ContractError("miniapp current session must use the credential-free view")
    accepted = responses["AcceptedOperation"]
    if "Location" not in accepted.get("headers", {}):
        raise ContractError("202 AcceptedOperation must declare Location")
    for response_name in (
        "UnauthorizedProblem",
        "ConflictProblem",
        "BusinessRuleProblem",
        "TooManyRequests",
        "DependencyUnavailable",
    ):
        media = responses[response_name].get("content", {})
        if "application/problem+json" not in media:
            raise ContractError(f"{response_name} must use application/problem+json")


def validate_openapi_document(document: Mapping[str, Any]) -> list[str]:
    if document.get("openapi") != "3.1.0":
        raise ContractError("HTTP source must declare OpenAPI 3.1.0")
    if (
        document.get("jsonSchemaDialect")
        != "https://json-schema.org/draft/2020-12/schema"
    ):
        raise ContractError("HTTP source must use JSON Schema Draft 2020-12")
    if document.get("info", {}).get("version") != "1.0.0-rc.1":
        raise ContractError("unexpected HTTP contract version")
    cutover = document.get("x-ecobin-legacy-credential-cutover", {})
    if cutover.get("webLocalStorageKeysToDelete") != ["ecobin-auth"]:
        raise ContractError("legacy Web Bearer cleanup contract changed")
    if cutover.get("miniappStorageKeysToDelete") != [
        "ecobin_token",
        "ecobin_role",
        "ecobin_user_info",
    ]:
        raise ContractError("legacy miniapp Bearer cleanup contract changed")
    if not cutover.get("serverLegacyBearerPolicy"):
        raise ContractError("legacy Bearer server invalidation policy is required")
    components = document.get("components")
    if not isinstance(components, Mapping):
        raise ContractError("OpenAPI components are required")
    for key in ("securitySchemes", "parameters", "headers", "responses", "schemas"):
        if not isinstance(components.get(key), Mapping):
            raise ContractError(f"OpenAPI components.{key} is required")

    reference_count = _validate_refs(document)
    operation_count = _validate_operations(document)
    _validate_security(document)
    _validate_common_schemas(document)
    return [
        f"HTTP OpenAPI 3.1 source has {operation_count} uniquely identified operations",
        f"HTTP OpenAPI resolves {reference_count} local references",
        "HTTP Cookie+CSRF, miniapp Bearer, and signed notification security are distinct",
        "HTTP ProblemDetail, 202, paging, version, UUIDv4, UTC, and decimal schemas are fixed",
    ]


def validate_http_examples(document: Mapping[str, Any]) -> str:
    manifest = load_json(HTTP_EXAMPLES_ROOT / "manifest.json")
    examples = manifest.get("examples")
    if not isinstance(examples, list) or not examples:
        raise ContractError("HTTP example manifest must be non-empty")
    seen: set[str] = set()
    for entry in examples:
        filename = entry["file"]
        if filename in seen:
            raise ContractError(f"duplicate HTTP example {filename}")
        seen.add(filename)
        instance = load_json(HTTP_EXAMPLES_ROOT / filename)
        schema = resolve_local_ref(document, entry["schemaRef"])
        validate_instance(instance, schema, document, path=filename)
        for path, value in _walk(instance):
            if isinstance(value, float):
                raise ContractError(f"{filename}{path}: floating-point value is forbidden")
    actual = {
        path.name
        for path in HTTP_EXAMPLES_ROOT.glob("*.json")
        if path.name != "manifest.json"
    }
    if actual != seen:
        raise ContractError(
            f"HTTP example manifest differs from files: {sorted(actual ^ seen)}"
        )
    return f"{len(examples)} HTTP examples validate against OpenAPI component schemas"


def validate_http_contract() -> list[str]:
    document = load_openapi()
    checks = validate_openapi_document(document)
    checks.append(validate_http_examples(document))
    return checks


def main() -> int:
    try:
        checks = validate_http_contract()
    except (ContractError, OSError, ValueError) as exc:
        print(f"HTTP contract validation failed: {exc}", file=sys.stderr)
        return 1
    for check in checks:
        print(f"PASS: {check}")
    print(f"HTTP contract validation complete: {len(checks)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
