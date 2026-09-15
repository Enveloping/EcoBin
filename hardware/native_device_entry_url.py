"""Durable UART-v2 device-entry URL application values.

This module only builds and validates immutable application journals.  Serial
ownership and SQLite transitions remain in ``NativeBusinessRuntime`` and
``EdgeStore`` respectively.
"""

from __future__ import annotations

import uuid
import hashlib

import uart2_protocol as uart


JOURNAL_KEY = "deviceEntryUrlApplication"
JOURNAL_SCHEMA_VERSION = 1
URL_CHUNK_BYTES = 64
URL_MAXIMUM_BYTES = 192
APPLICATION_BASIS = "UART3_COMMAND_ATOMICALLY_QUEUED"
APPLICATION_COMMANDS = frozenset({
    "SYNC_DEVICE_ENTRY_URL",
    "REQUEST_DEVICE_ACCEPTANCE",
})
CONTINUATIONS = APPLICATION_COMMANDS | {"LOCAL_RELOAD"}
ENROLLED_SOURCE_NAMESPACE = uuid.UUID(
    "9addc6d6-9751-5aa5-b9fc-1b511f560512"
)


def validate_url(url: str, sha256: str) -> bytes:
    if not isinstance(url, str):
        raise ValueError("device entry URL must be text")
    try:
        encoded = url.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("device entry URL must be ASCII") from error
    if (
        not 1 <= len(encoded) <= URL_MAXIMUM_BYTES
        or not encoded.startswith(b"https://")
        or any(
            byte < 0x21 or byte > 0x7E or byte in (0x22, 0x5C)
            for byte in encoded
        )
    ):
        raise ValueError(
            "device entry URL must be safe quoted ASCII HTTPS within 192 bytes"
        )
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
        or hashlib.sha256(encoded).hexdigest() != sha256
    ):
        raise ValueError("device entry URL SHA-256 mismatch")
    return encoded


def enrolled_source(device_name: str, url: str) -> dict:
    """Build the stable local authority installed by encrypted enrollment."""

    if (
        not isinstance(device_name, str)
        or not 1 <= len(device_name) <= 64
        or any(
            not (
                character.isascii()
                and (character.isalnum() or character in "_.:-")
            )
            for character in device_name
        )
    ):
        raise ValueError("enrolled device entry URL device name is invalid")
    if not isinstance(url, str):
        raise ValueError("enrolled device entry URL is invalid")
    try:
        digest = hashlib.sha256(url.encode("ascii")).hexdigest()
    except UnicodeEncodeError as error:
        raise ValueError("enrolled device entry URL must be ASCII") from error
    validate_url(url, digest)
    return {
        "deviceEntryUrl": url,
        "deviceEntryUrlSha256": digest,
        "sourceUid": str(
            uuid.uuid5(
                ENROLLED_SOURCE_NAMESPACE,
                f"{device_name}\n{digest}",
            )
        ),
    }


def _uuid(value: str, field: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a lowercase nonzero UUID") from error
    if not parsed.int or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase nonzero UUID")
    return value


def new_attempt(target_mcu_boot_id: int | None = None) -> dict:
    if target_mcu_boot_id is not None and (
        type(target_mcu_boot_id) is not int
        or not 1 <= target_mcu_boot_id <= 9007199254740991
    ):
        raise ValueError("device entry URL target boot is invalid")
    return {
        "applicationUid": str(uuid.uuid4()),
        "commandUids": [],
        "targetMcuBootId": target_mcu_boot_id,
    }


def new_journal(command: dict, stored_url: dict, *, target_mcu_boot_id: int | None) -> dict:
    command_type = command.get("commandType")
    if command_type not in APPLICATION_COMMANDS:
        raise ValueError("device entry URL source command is invalid")
    payload = command.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("device entry URL source payload is invalid")
    url = payload.get("deviceEntryUrl")
    digest = payload.get("deviceEntryUrlSha256")
    encoded = validate_url(url, digest)
    if not isinstance(stored_url, dict) or (
        stored_url.get("deviceEntryUrl") != url
        or stored_url.get("deviceEntryUrlSha256") != digest
        or stored_url.get("issuedAt") != command.get("issuedAt")
    ):
        raise ValueError("device entry URL journal differs from durable source")
    part_count = (len(encoded) + URL_CHUNK_BYTES - 1) // URL_CHUNK_BYTES
    journal = {
        "schemaVersion": JOURNAL_SCHEMA_VERSION,
        "state": "WAITING",
        "sourceCommandUid": _uuid(command.get("commandUid"), "sourceCommandUid"),
        "continuation": command_type,
        "deviceEntryUrl": url,
        "deviceEntryUrlSha256": digest,
        "issuedAt": command.get("issuedAt"),
        "urlLength": len(encoded),
        "partCount": part_count,
        "attempt": new_attempt(target_mcu_boot_id),
    }
    if target_mcu_boot_id is not None:
        journal["attempt"]["commandUids"] = [
            str(uuid.uuid4()) for _ in range(part_count + 2)
        ]
    return journal


def new_reload_journal(
    stored_url: dict,
    *,
    source_command_uid: str,
    target_mcu_boot_id: int,
) -> dict:
    if not isinstance(stored_url, dict):
        raise ValueError("device entry URL reload source is invalid")
    url = stored_url.get("deviceEntryUrl")
    digest = stored_url.get("deviceEntryUrlSha256")
    encoded = validate_url(url, digest)
    part_count = (len(encoded) + URL_CHUNK_BYTES - 1) // URL_CHUNK_BYTES
    journal = {
        "schemaVersion": JOURNAL_SCHEMA_VERSION,
        "state": "WAITING",
        # A completed cloud command remains the permanent authority.  The
        # reload gets new MCU identities, but never invents a new cloud fact.
        "sourceCommandUid": _uuid(source_command_uid, "sourceCommandUid"),
        "continuation": "LOCAL_RELOAD",
        "deviceEntryUrl": url,
        "deviceEntryUrlSha256": digest,
        "issuedAt": stored_url.get("issuedAt"),
        "urlLength": len(encoded),
        "partCount": part_count,
        "attempt": new_attempt(target_mcu_boot_id),
    }
    journal["attempt"]["commandUids"] = [
        str(uuid.uuid4()) for _ in range(part_count + 2)
    ]
    return validate_journal(journal)


def rebase_journal(journal: dict, target_mcu_boot_id: int) -> dict:
    current = validate_journal(journal)
    rebased = dict(current)
    rebased.pop("applyResultPayloadHex", None)
    rebased.pop("appliedEvidence", None)
    rebased["state"] = "WAITING"
    rebased["attempt"] = new_attempt(target_mcu_boot_id)
    rebased["attempt"]["commandUids"] = [
        str(uuid.uuid4()) for _ in range(current["partCount"] + 2)
    ]
    return rebased


def validate_journal(journal: dict) -> dict:
    if not isinstance(journal, dict):
        raise ValueError("device entry URL application journal is missing")
    required = {
        "schemaVersion",
        "state",
        "sourceCommandUid",
        "continuation",
        "deviceEntryUrl",
        "deviceEntryUrlSha256",
        "issuedAt",
        "urlLength",
        "partCount",
        "attempt",
    }
    optional = {"applyResultPayloadHex", "appliedEvidence"}
    if set(journal) - required - optional or not required.issubset(journal):
        raise ValueError("device entry URL application journal shape is invalid")
    if journal["schemaVersion"] != JOURNAL_SCHEMA_VERSION:
        raise ValueError("device entry URL application journal version is invalid")
    if journal["state"] not in {"WAITING", "APPLIED", "FAILED"}:
        raise ValueError("device entry URL application state is invalid")
    _uuid(journal["sourceCommandUid"], "sourceCommandUid")
    if journal["continuation"] not in CONTINUATIONS:
        raise ValueError("device entry URL continuation is invalid")
    encoded = validate_url(
        journal["deviceEntryUrl"],
        journal["deviceEntryUrlSha256"],
    )
    expected_count = (len(encoded) + URL_CHUNK_BYTES - 1) // URL_CHUNK_BYTES
    if (
        journal["urlLength"] != len(encoded)
        or journal["partCount"] != expected_count
        or not isinstance(journal["issuedAt"], str)
        or not journal["issuedAt"]
    ):
        raise ValueError("device entry URL application dimensions are invalid")
    attempt = journal["attempt"]
    if not isinstance(attempt, dict) or set(attempt) != {
        "applicationUid",
        "commandUids",
        "targetMcuBootId",
    }:
        raise ValueError("device entry URL application attempt is invalid")
    _uuid(attempt["applicationUid"], "applicationUid")
    boot_id = attempt["targetMcuBootId"]
    command_uids = attempt["commandUids"]
    if boot_id is None:
        if command_uids != [] or journal["state"] != "WAITING":
            raise ValueError("unbound device entry URL attempt cannot have commands")
    else:
        if (
            type(boot_id) is not int
            or not 1 <= boot_id <= 9007199254740991
            or not isinstance(command_uids, list)
            or len(command_uids) != expected_count + 2
            or len(set(command_uids)) != len(command_uids)
        ):
            raise ValueError("bound device entry URL attempt is incomplete")
        for command_uid in command_uids:
            _uuid(command_uid, "commandUid")
    if journal["state"] == "WAITING":
        if optional & set(journal):
            raise ValueError("waiting device entry URL has terminal evidence")
    else:
        if set(journal) != required | optional:
            raise ValueError("terminal device entry URL lacks evidence")
        raw = journal["applyResultPayloadHex"]
        evidence = journal["appliedEvidence"]
        if (
            not isinstance(raw, str)
            or len(raw) != 178
            or any(character not in "0123456789abcdef" for character in raw)
            or not isinstance(evidence, dict)
        ):
            raise ValueError("device entry URL result evidence is malformed")
    return journal


def encode_command(journal: dict, index: int, *, command_sequence: int) -> tuple[str, bytes]:
    current = validate_journal(journal)
    attempt = current["attempt"]
    boot_id = attempt["targetMcuBootId"]
    commands = attempt["commandUids"]
    if boot_id is None:
        raise ValueError("device entry URL attempt is not dispatchable")
    if type(index) is not int or not 0 <= index < len(commands):
        raise ValueError("device entry URL command index is invalid")
    common = {
        "mcuCommandUid": commands[index],
        "targetMcuBootId": boot_id,
        "commandSequence": command_sequence,
    }
    if index == 0:
        name = "DEVICE_ENTRY_URL_BEGIN"
        values = common | {
            "applicationUid": attempt["applicationUid"],
            "urlLength": current["urlLength"],
            "urlSha256": current["deviceEntryUrlSha256"],
            "partCount": current["partCount"],
        }
    elif index == len(commands) - 1:
        name = "DEVICE_ENTRY_URL_COMMIT"
        values = common | {
            "applicationUid": attempt["applicationUid"],
            "urlLength": current["urlLength"],
            "urlSha256": current["deviceEntryUrlSha256"],
            "partCount": current["partCount"],
        }
    else:
        name = "DEVICE_ENTRY_URL_PART"
        start = (index - 1) * URL_CHUNK_BYTES
        values = common | {
            "applicationUid": attempt["applicationUid"],
            "urlSha256": current["deviceEntryUrlSha256"],
            "partIndex": index,
            "partCount": current["partCount"],
            "urlChunk": current["deviceEntryUrl"][start:start + URL_CHUNK_BYTES],
        }
    # The generated encoder owns the digest profile and all semantic guards.
    values["commandDigestSha256"] = "0" * 64
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    return name, uart.encode_payload(name, values)


def terminal_journal(journal: dict, payload: bytes, values: dict) -> dict:
    current = validate_journal(journal)
    attempt = current["attempt"]
    commit_uid = attempt["commandUids"][-1]
    if (
        current["state"] != "WAITING"
        or values.get("mcuCommandUid") != commit_uid
        or values.get("applicationUid") != attempt["applicationUid"]
        or values.get("mcuBootId") != attempt["targetMcuBootId"]
        or values.get("urlLength") != current["urlLength"]
        or values.get("urlSha256") != current["deviceEntryUrlSha256"]
    ):
        raise ValueError("device entry URL apply result differs from active attempt")
    state = values.get("status")
    if state not in {"APPLIED", "FAILED"}:
        raise ValueError("device entry URL apply result status is invalid")
    evidence = {
        "deviceEntryUrlSha256": current["deviceEntryUrlSha256"],
        "applicationUid": attempt["applicationUid"],
        "mcuCommandUid": commit_uid,
        "mcuBootId": values["mcuBootId"],
        "mcuEventSequence": values["mcuEventSequence"],
        "status": state,
        "faultCode": None if state == "APPLIED" else values["errorCode"],
        "basis": APPLICATION_BASIS if state == "APPLIED" else "NOT_APPLIED",
    }
    result = dict(current)
    result["state"] = state
    result["applyResultPayloadHex"] = payload.hex()
    result["appliedEvidence"] = evidence
    validate_journal(result)
    return result
