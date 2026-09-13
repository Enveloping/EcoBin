"""Actual C URL staging and result replay; no physical UART or GPIO action."""
from __future__ import annotations

import ctypes as c
import hashlib
import uuid

import pytest

import uart2_protocol as uart
from contracts.tests.test_uart_v2_command_guards import minimal_command
from hardware.tests.test_mcu_work_preparation import (
    URL_WRITER,
    configured,
    exchange,
    library,
    runtime,
    start_values,
)


APPLICATION_UID = "71000000-0000-4000-8000-000000000001"


def command(name, sequence, **body):
    values = {
        "mcuCommandUid": f"72000000-0000-4000-8000-{sequence:012d}",
        "commandDigestSha256": "0" * 64,
        "targetMcuBootId": 42,
        "commandSequence": sequence,
        **body,
    }
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    return values


def begin_values(url, sequence=1, *, application_uid=APPLICATION_UID, digest=None):
    digest = digest or hashlib.sha256(url.encode("ascii")).hexdigest()
    return command("DEVICE_ENTRY_URL_BEGIN", sequence,
        applicationUid=application_uid, urlLength=len(url), urlSha256=digest,
        partCount=(len(url) + 63) // 64)


def part_values(url, index, sequence, *, application_uid=APPLICATION_UID, digest=None, chunk=None):
    digest = digest or hashlib.sha256(url.encode("ascii")).hexdigest()
    parts = [url[offset:offset + 64] for offset in range(0, len(url), 64)]
    return command("DEVICE_ENTRY_URL_PART", sequence,
        applicationUid=application_uid, urlSha256=digest, partIndex=index,
        partCount=len(parts), urlChunk=parts[index - 1] if chunk is None else chunk)


def commit_values(url, sequence, *, application_uid=APPLICATION_UID, digest=None):
    digest = digest or hashlib.sha256(url.encode("ascii")).hexdigest()
    return command("DEVICE_ENTRY_URL_COMMIT", sequence,
        applicationUid=application_uid, urlLength=len(url), urlSha256=digest,
        partCount=(len(url) + 63) // 64)


@pytest.fixture
def attached(runtime):
    lib, endpoint, preparation, *_ = runtime
    state = (c.c_uint64 * 128)()
    accepted = {"value": True}
    attempts, queued = [], []

    def write(data, length, _):
        value = c.string_at(data, length)
        attempts.append(value)
        if not accepted["value"]:
            return 0
        queued.append(value)
        return 1

    writer = URL_WRITER(write)
    assert lib.McuWorkPreparation_AttachDeviceEntryUrl(
        preparation, endpoint, state, writer, None)
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 1})[0][1]["mcuBootId"] == 0
    assert exchange(runtime, "BIND_BOOT", {
        "probeId": 1, "proposedMcuBootId": 42,
    })[0][1]["status"] == "BOUND"
    yield runtime, state, accepted, attempts, queued, writer


def send_parts(attached, url, *, first_sequence=1, digest=None):
    runtime = attached[0]
    begin = begin_values(url, first_sequence, digest=digest)
    assert exchange(runtime, "DEVICE_ENTRY_URL_BEGIN", begin)[0][1]["outcome"] == "ACCEPTED"
    sequence = first_sequence + 1
    for index in range(1, (len(url) + 63) // 64 + 1):
        reply = exchange(runtime, "DEVICE_ENTRY_URL_PART",
            part_values(url, index, sequence, digest=digest))[0][1]
        assert reply["outcome"] == "ACCEPTED", (reply["errorCode"], reply)
        sequence += 1
    return commit_values(url, sequence, digest=digest)


def query_values(commit, query_id=91):
    return {"queryId": query_id, **{key: commit[key] for key in (
        "mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}}


def test_maximum_url_applies_once_and_query_only_replays_same_ram_result(attached):
    runtime, _, _, attempts, queued, _ = attached
    url = "https://" + "a" * 184
    commit = send_parts(attached, url)
    replies = exchange(runtime, "DEVICE_ENTRY_URL_COMMIT", commit, now=700)
    assert [name for name, _ in replies] == ["COMMAND_DECISION", "DEVICE_ENTRY_URL_APPLY_RESULT"]
    assert replies[0][1]["outcome"] == "ACCEPTED"
    result = replies[1][1]
    assert result["status"] == "APPLIED" and result["errorCode"] == "NONE"
    assert result["urlLength"] == 192
    assert result["urlSha256"] == hashlib.sha256(url.encode("ascii")).hexdigest()
    assert attempts == queued == [url.encode("ascii")]

    replay = exchange(runtime, "QUERY_COMMAND", query_values(commit), now=701)
    assert [name for name, _ in replay] == ["COMMAND_QUERY_RESULT", "DEVICE_ENTRY_URL_APPLY_RESULT"]
    assert replay[0][1]["outcome"] == "ACCEPTED"
    assert replay[1][1] == result
    assert attempts == queued == [url.encode("ascii")]  # no second HMI write


def test_query_replays_held_result_after_session_decision_cache_advances(attached):
    runtime, _, _, attempts, queued, _ = attached
    url = "https://device.example/entry"
    commit = send_parts(attached, url)
    applied = exchange(runtime, "DEVICE_ENTRY_URL_COMMIT", commit, now=700)[1][1]

    # The generic MCU session keeps only its latest command decision.  Advance
    # it with a later, safely rejected legacy command to prove the URL owner's
    # exact held result remains independently queryable.
    later_sequence = commit["commandSequence"] + 1
    values = minimal_command(
        uart.REGISTRY,
        uart.MESSAGE_SPECS["SAFE_CLOSE"] | {"name": "SAFE_CLOSE"},
    )
    values.update(
        mcuCommandUid=str(uuid.UUID(int=later_sequence)),
        targetMcuBootId=42,
        commandSequence=later_sequence,
    )
    values["commandDigestSha256"] = uart.compute_command_digest(
        "SAFE_CLOSE", values
    )
    decision = exchange(runtime, "SAFE_CLOSE", values, now=701)[0][1]
    assert decision["outcome"] == "REJECTED"
    assert decision["errorCode"] == "UNSUPPORTED_MESSAGE"

    replay = exchange(runtime, "QUERY_COMMAND", query_values(commit), now=702)
    assert [name for name, _ in replay] == [
        "COMMAND_QUERY_RESULT",
        "DEVICE_ENTRY_URL_APPLY_RESULT",
    ]
    assert replay[0][1]["outcome"] == "OLD_DETAILS_UNAVAILABLE"
    assert replay[1][1] == applied
    assert attempts == queued == [url.encode("ascii")]


def test_queue_busy_is_accepted_failed_result_with_zero_bytes_then_new_commit_can_apply(attached):
    runtime, _, accepted, attempts, queued, _ = attached
    url = "https://device.example/entry"
    commit = send_parts(attached, url)
    accepted["value"] = False
    failed = exchange(runtime, "DEVICE_ENTRY_URL_COMMIT", commit, now=50)
    assert failed[0][1]["outcome"] == "ACCEPTED"
    assert failed[1][1]["status"] == "FAILED"
    assert failed[1][1]["errorCode"] == "BUSY"
    assert len(attempts) == 1 and queued == []

    replay = exchange(runtime, "QUERY_COMMAND", query_values(commit), now=51)
    assert replay[1][1] == failed[1][1]
    assert len(attempts) == 1 and queued == []
    accepted["value"] = True
    retry = commit_values(url, commit["commandSequence"] + 1)
    applied = exchange(runtime, "DEVICE_ENTRY_URL_COMMIT", retry, now=52)
    assert applied[1][1]["status"] == "APPLIED"
    assert len(attempts) == 2 and queued == [url.encode("ascii")]


def test_parts_are_strictly_ordered_sized_and_idempotent(attached):
    runtime = attached[0]
    url = "https://" + "b" * 58  # 66 bytes, two parts
    begin = begin_values(url, 1)
    assert exchange(runtime, "DEVICE_ENTRY_URL_BEGIN", begin)[0][1]["outcome"] == "ACCEPTED"
    wrong_order = exchange(runtime, "DEVICE_ENTRY_URL_PART", part_values(url, 2, 2))[0][1]
    assert wrong_order["outcome"] == "REJECTED" and wrong_order["errorCode"] == "STATE_CONFLICT"
    short = exchange(runtime, "DEVICE_ENTRY_URL_PART",
        part_values(url, 1, 3, chunk=url[:63]))[0][1]
    assert short["outcome"] == "REJECTED" and short["errorCode"] == "STATE_CONFLICT"
    first = part_values(url, 1, 4)
    assert exchange(runtime, "DEVICE_ENTRY_URL_PART", first)[0][1]["outcome"] == "ACCEPTED"
    assert exchange(runtime, "DEVICE_ENTRY_URL_PART", first)[0][1]["outcome"] == "ACCEPTED"
    assert exchange(runtime, "DEVICE_ENTRY_URL_PART", part_values(url, 2, 5))[0][1]["outcome"] == "ACCEPTED"
    replies = exchange(runtime, "DEVICE_ENTRY_URL_COMMIT", commit_values(url, 6))
    assert replies[1][1]["status"] == "APPLIED"
    assert attached[4] == [url.encode("ascii")]


def test_sha_mismatch_and_reused_application_identity_do_not_replace_display(attached):
    runtime = attached[0]
    url = "https://device.example/one"
    wrong = hashlib.sha256(b"https://device.example/other").hexdigest()
    commit = send_parts(attached, url, digest=wrong)
    rejected = exchange(runtime, "DEVICE_ENTRY_URL_COMMIT", commit)[0][1]
    assert rejected["outcome"] == "REJECTED" and rejected["errorCode"] == "INVALID_FIELD"
    assert attached[4] == []

    retry = commit_values(url, commit["commandSequence"] + 1, digest=wrong)
    assert exchange(runtime, "DEVICE_ENTRY_URL_COMMIT", retry)[0][1]["outcome"] == "REJECTED"
    # A same-application BEGIN cannot relabel or restart the retained transaction.
    same = begin_values(url, retry["commandSequence"] + 1, digest=wrong)
    reply = exchange(runtime, "DEVICE_ENTRY_URL_BEGIN", same)[0][1]
    assert reply["outcome"] == "REJECTED" and reply["errorCode"] == "STATE_CONFLICT"


def test_real_running_work_rejects_url_staging_before_any_display_write(runtime):
    lib, endpoint, preparation, *_ = runtime
    state = (c.c_uint64 * 128)()
    attempts = []
    writer = URL_WRITER(lambda data, length, _: attempts.append(
        c.string_at(data, length)) or 1)
    assert lib.McuWorkPreparation_AttachDeviceEntryUrl(
        preparation, endpoint, state, writer, None)
    configured(runtime, applied=True)
    start = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", start)[0][1]["outcome"] == "ACCEPTED"
    url = "https://device.example/busy"
    reply = exchange(runtime, "DEVICE_ENTRY_URL_BEGIN", begin_values(url, 7))[0][1]
    assert reply["outcome"] == "REJECTED" and reply["errorCode"] == "BUSY"
    assert attempts == []


@pytest.mark.parametrize("name", [
    "AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR", "SAFE_CLOSE",
])
def test_deprecated_action_commands_are_explicitly_rejected_without_gpio(attached, name):
    runtime = attached[0]
    lib = runtime[0]
    before = lib.TestFacts_Writes()
    sequence = {"AUTHORIZE_DELIVERY_FIRST_OPEN": 1,
                "UNLOCK_CLEAN_DOOR": 2, "SAFE_CLOSE": 3}[name]
    spec = uart.MESSAGE_SPECS[name] | {"name": name}
    values = minimal_command(uart.REGISTRY, spec)
    values.update(mcuCommandUid=str(uuid.UUID(int=sequence)),
        targetMcuBootId=42, commandSequence=sequence)
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    reply = exchange(runtime, name, values)[0][1]
    assert reply["outcome"] == "REJECTED"
    assert reply["errorCode"] == "UNSUPPORTED_MESSAGE"
    assert lib.TestFacts_Writes() == before
    assert attached[3] == attached[4] == []
