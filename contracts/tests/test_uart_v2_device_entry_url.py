"""Contract tests for reliable UART v2 device-entry URL application."""
from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from contractlib import (  # noqa: E402
    ContractError,
    compute_uart_command_digest,
    decode_uart_payload,
    encode_uart_payload,
    load_uart_registry,
    uart_fenced_command_names,
    uart_message_specs,
    validate_uart_registry,
)


REGISTRY = load_uart_registry()
SPECS = uart_message_specs(REGISTRY)
APPLICATION_UID = "71000000-0000-4000-8000-000000000001"


def command_values(name: str, sequence: int, **body):
    values = {
        "mcuCommandUid": str(uuid.UUID(int=sequence)),
        "commandDigestSha256": "0" * 64,
        "targetMcuBootId": 42,
        "commandSequence": sequence,
        **body,
    }
    values["commandDigestSha256"] = compute_uart_command_digest(
        REGISTRY, name, values
    )
    return values


def test_lifecycle_groups_are_complete_disjoint_and_deprecated_commands_have_no_capability_gate():
    validate_uart_registry(REGISTRY)
    policy = REGISTRY["messageLifecyclePolicy"]
    groups = [set(names) for names in policy.values()]
    assert set.union(*groups) == set(SPECS)
    for index, group in enumerate(groups):
        assert all(not group.intersection(other) for other in groups[index + 1 :])

    deprecated = set(policy["deprecatedRejectedMessages"])
    assert {
        "SAFE_CLOSE",
        "AUTHORIZE_DELIVERY_FIRST_OPEN",
        "UNLOCK_CLEAN_DOOR",
    } <= deprecated
    assert deprecated.isdisjoint(REGISTRY["capabilityPolicy"]["messageRequirements"])
    assert "DELIVERY_DOOR_COMMAND_RESULT" not in REGISTRY["capabilityPolicy"][
        "messageRequirements"
    ]["START_DELIVERY_SESSION"]
    assert "自主开门" in next(
        message["notes"]
        for message in REGISTRY["messages"]
        if message["name"] == "START_DELIVERY_SESSION"
    )


def test_url_transaction_is_fenced_and_fits_existing_frame_budget():
    commands = set(uart_fenced_command_names(REGISTRY))
    assert {
        "DEVICE_ENTRY_URL_BEGIN",
        "DEVICE_ENTRY_URL_PART",
        "DEVICE_ENTRY_URL_COMMIT",
    } <= commands
    assert SPECS["DEVICE_ENTRY_URL_PART"]["maximumPayloadLength"] == 175
    assert SPECS["DEVICE_ENTRY_URL_PART"]["maximumPayloadLength"] <= 242
    assert SPECS["DEVICE_ENTRY_URL_APPLY_RESULT"]["maximumPayloadLength"] == 89


def test_three_parts_round_trip_a_maximum_safe_url():
    url = "https://" + "a" * 184
    digest = hashlib.sha256(url.encode("ascii")).hexdigest()
    parts = [url[index : index + 64] for index in range(0, len(url), 64)]
    assert len(url) == 192
    assert len(parts) == 3

    begin = command_values(
        "DEVICE_ENTRY_URL_BEGIN",
        1,
        applicationUid=APPLICATION_UID,
        urlLength=len(url),
        urlSha256=digest,
        partCount=len(parts),
    )
    assert decode_uart_payload(
        REGISTRY,
        "DEVICE_ENTRY_URL_BEGIN",
        encode_uart_payload(REGISTRY, "DEVICE_ENTRY_URL_BEGIN", begin),
    ) == begin

    for index, chunk in enumerate(parts, 1):
        values = command_values(
            "DEVICE_ENTRY_URL_PART",
            index + 1,
            applicationUid=APPLICATION_UID,
            urlSha256=digest,
            partIndex=index,
            partCount=len(parts),
            urlChunk=chunk,
        )
        assert decode_uart_payload(
            REGISTRY,
            "DEVICE_ENTRY_URL_PART",
            encode_uart_payload(REGISTRY, "DEVICE_ENTRY_URL_PART", values),
        ) == values

    commit = command_values(
        "DEVICE_ENTRY_URL_COMMIT",
        5,
        applicationUid=APPLICATION_UID,
        urlLength=len(url),
        urlSha256=digest,
        partCount=len(parts),
    )
    assert decode_uart_payload(
        REGISTRY,
        "DEVICE_ENTRY_URL_COMMIT",
        encode_uart_payload(REGISTRY, "DEVICE_ENTRY_URL_COMMIT", commit),
    ) == commit


@pytest.mark.parametrize(
    "chunk",
    ["http://device.example/q", "https://bad path", 'https://bad"path', "https://bad\\path", "https://设备"],
)
def test_first_url_part_rejects_non_https_or_unsafe_hmi_text(chunk: str):
    values = command_values(
        "DEVICE_ENTRY_URL_PART",
        10,
        applicationUid=APPLICATION_UID,
        urlSha256="11" * 32,
        partIndex=1,
        partCount=1,
        urlChunk=chunk,
    )
    with pytest.raises(ContractError):
        encode_uart_payload(REGISTRY, "DEVICE_ENTRY_URL_PART", values)


def test_url_part_count_and_result_status_are_not_ambiguous():
    bad_begin = command_values(
        "DEVICE_ENTRY_URL_BEGIN",
        20,
        applicationUid=APPLICATION_UID,
        urlLength=65,
        urlSha256="22" * 32,
        partCount=1,
    )
    with pytest.raises(ContractError, match="ceil"):
        encode_uart_payload(REGISTRY, "DEVICE_ENTRY_URL_BEGIN", bad_begin)

    result = {
        "mcuBootId": 42,
        "mcuEventSequence": 7,
        "uptimeMs": 900,
        "mcuCommandUid": str(uuid.UUID(int=20)),
        "applicationUid": APPLICATION_UID,
        "urlLength": 28,
        "urlSha256": "33" * 32,
        "status": "APPLIED",
        "errorCode": "BUSY",
    }
    with pytest.raises(ContractError, match="status/error/hash"):
        encode_uart_payload(REGISTRY, "DEVICE_ENTRY_URL_APPLY_RESULT", result)

    failed = result | {"status": "FAILED"}
    payload = encode_uart_payload(
        REGISTRY, "DEVICE_ENTRY_URL_APPLY_RESULT", failed
    )
    assert decode_uart_payload(
        REGISTRY, "DEVICE_ENTRY_URL_APPLY_RESULT", payload
    ) == failed
