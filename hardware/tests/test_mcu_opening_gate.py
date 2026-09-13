"""START admission and the explicit rc.23 rejection boundary for old grants."""
import ctypes as c
import uuid

import pytest
import uart2_protocol as uart
from contracts.tests.test_uart_v2_command_guards import minimal_command
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import (
    configured,
    exchange,
    library,
    original_scope,
    runtime,
    start_values,
    take_samples,
)


class Limits(c.Structure):
    _fields_ = [
        ("execution_deadline_ms", c.c_uint64),
        ("operation_deadline_ms", c.c_uint64),
        ("delivery_auto_close_ms", c.c_uint32),
        ("unlock_pulse_ms", c.c_uint32),
    ]


def prepared(runtime, tmp_path, *, clean=False, saved=True, mode="stable", start_at=0,
             candidate=None, **start_changes):
    """Historical process-event fixture retained for its remaining consumers."""
    configured(runtime, applied=True, candidate=candidate)
    name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
    start = start_values(name, **start_changes)
    lib, endpoint, owner, *_ = runtime
    lib.RuntimeClock_Advance(start_at)
    assert exchange(runtime, name, start, now=start_at)[0][1]["outcome"] == "ACCEPTED"
    if mode == "stable":
        now = take_samples(runtime, [500] * 5, start=start_at)
    else:
        now = take_samples(runtime, [-500, 500] * 10, start=start_at) if mode == "median" else start_at
        lib.RuntimeClock_Advance(start_at + 5000 - now)
        now = start_at + 5000
        assert lib.McuWorkPreparation_Poll(owner, endpoint, now)
    event_name = "WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY"
    scope = original_scope(start, clean=clean) | {
        "eventMessageType": event_name,
        "stepSequence": 0 if clean else 1,
        "configVersion": start["configVersion"],
    }
    replies = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert replies[0][1]["status"] == "HELD"
    event = replies[1][1]
    if saved:
        store = EdgeStore(str(tmp_path / "opening.db"))
        store.initialize()
        try:
            receipt = store.save_native_process_receipt(
                uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:],
                event_name,
                uart.encode_payload(event_name, event),
            )
            assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
            assert store.list_native_result_report_tasks() == []
        finally:
            store.close()
    return start, event, now


def grant(start, event, *, clean=False, **changes):
    """Build a legacy grant only for compatibility/rejection tests."""
    name = "UNLOCK_CLEAN_DOOR" if clean else "AUTHORIZE_DELIVERY_FIRST_OPEN"
    values = minimal_command(uart.REGISTRY, uart.MESSAGE_SPECS[name] | {"name": name})
    values.update(
        targetMcuBootId=42,
        commandSequence=7,
        portNo=1,
        mcuCommandUid="77777777-7777-4777-8777-777777777777",
    )
    if clean:
        from hardware.tests.test_native_configuration import inputs
        values.update(
            operationUid=start["operationUid"],
            parentCommandUid=start["mcuCommandUid"],
            cleanActionSequence=0,
            recoveryGeneration=0,
            unlockPulseMs=inputs()["device"]["cleanSolenoidPulseMs"],
            remainingOperationWindowMs=200000,
        )
    else:
        values.update(
            sessionUid=start["sessionUid"],
            parentStartCommandUid=start["mcuCommandUid"],
            firstPreOpenMeasurementUid=event["measurementUid"],
            remainingStartAuthorizationMs=10000,
        )
    values.update(changes)
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    return name, values


def evaluate(runtime, name, values, *, received=1020, now=1020, raw=None):
    """Historical pure opening-gate evaluator retained for component consumers."""
    lib, endpoint, owner, *_ = runtime
    payload = raw if raw is not None else uart.encode_payload(name, values)
    limits = Limits(91, 92, 93, 94)
    before = bytes(endpoint), bytes(owner), lib.TestFacts_Writes()
    error = lib.McuOpeningGate_Evaluate(
        owner,
        endpoint,
        uart.MESSAGE_SPECS[name]["id"],
        payload,
        len(payload),
        received,
        now,
        c.byref(limits),
    )
    assert before == (bytes(endpoint), bytes(owner), lib.TestFacts_Writes())
    if error:
        assert bytes(limits) == bytes(Limits(91, 92, 93, 94))
    return error, limits


@pytest.fixture(scope="module")
def autonomous_library(tmp_path_factory):
    """Lazily reuse the current host to avoid the grant-helper import cycle."""
    from hardware.tests.test_mcu_simplified_execution import library as current_library
    return current_library.__wrapped__(tmp_path_factory)


@pytest.fixture
def autonomous_runtime(autonomous_library):
    from hardware.tests.test_mcu_work_preparation import runtime as base_runtime
    yield from base_runtime.__wrapped__(autonomous_library)


def current_helpers():
    from hardware.tests import test_mcu_simplified_execution as current
    from hardware.tests.test_mcu_delivery_execution import facts
    return current, facts


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("mode", ["stable", "median"])
def test_valid_start_uses_its_own_initial_weight_then_autonomously_actuates(
        autonomous_runtime, clean, mode):
    current, facts = current_helpers()
    _, _, start, now = current.setup(autonomous_runtime, clean=clean, initial=False)
    samples = [500] * 5 if mode == "stable" else [-500, 1500] * 10
    now = take_samples(autonomous_runtime, samples, start=now)
    if mode == "median":
        now = current.tick(autonomous_runtime, now, 5000 - now)
    now = current.tick(autonomous_runtime, now, 0)

    observed = facts(autonomous_runtime, now)
    assert observed["measurementWeightGrams"] == (500 if mode == "stable" else 500)
    assert observed["measurementSampleCount"] == len(samples)
    if clean:
        assert current.state(autonomous_runtime, start, now, clean=True)["phase"] == "CLEAN_UNLOCK_PULSE"
        assert observed["cleanLockPowered"]
    else:
        assert current.state(autonomous_runtime, start, now)["phase"] == "DELIVERY_OPEN_COMMAND"
        now = current.tick(autonomous_runtime, now, 100)
        assert facts(autonomous_runtime, now)["pb6Output"]


@pytest.mark.parametrize("clean", [False, True])
def test_missing_initial_weight_fails_without_open_or_unlock(autonomous_runtime, clean):
    current, facts = current_helpers()
    _, _, start, now = current.setup(autonomous_runtime, clean=clean, initial=False)
    now = current.tick(autonomous_runtime, now, 5000)
    now = current.tick(autonomous_runtime, now, 0)
    result = current.final(autonomous_runtime, start, now, clean=clean)
    assert result["finishReason"] == "FAILED"
    assert result["initialKind"] == "UNAVAILABLE"
    assert result["initialWeightGrams"] == 0
    assert result["finalKind"] == "NOT_TAKEN"
    assert not facts(autonomous_runtime, now)["pb6Output"]
    assert not facts(autonomous_runtime, now)["cleanLockPowered"]


@pytest.mark.parametrize("clean", [False, True])
def test_legacy_grant_is_explicitly_unsupported_and_cannot_actuate(autonomous_runtime, clean):
    current, facts = current_helpers()
    _, _, start, now = current.setup(autonomous_runtime, clean=clean, initial=False)
    event = {"measurementUid": str(uuid.uuid4())}
    name, command = grant(start, event, clean=clean)
    response = exchange(autonomous_runtime, name, command, now=now)[0][1]
    assert response["outcome"] == "REJECTED"
    assert response["errorCode"] == "UNSUPPORTED_MESSAGE"
    observed = facts(autonomous_runtime, now)
    assert not observed["pb6Output"] and not observed["cleanLockPowered"]


@pytest.mark.parametrize(
    "change",
    [
        {"configVersion": 9},
        {"configContentSha256": "ff" * 32},
        {"portNo": 2},
        {"continueDeliveryWaitMs": 1234},
    ],
)
def test_start_must_match_applied_configuration_and_port(autonomous_runtime, change):
    current, facts = current_helpers()
    lib, endpoint, preparation, *_ = autonomous_runtime
    delivery, cleanup = (c.c_uint64 * 64)(), (c.c_uint64 * 64)()
    autonomous_runtime[4]["owners"] = (delivery, cleanup)
    assert lib.McuDeliveryExecution_Attach(delivery, preparation, endpoint)
    assert lib.McuCleanExecution_Attach(cleanup, preparation, endpoint)
    assert lib.TestSimple_EnableApply(preparation, endpoint)
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    configured(autonomous_runtime)
    start = start_values(**change)
    response = exchange(autonomous_runtime, "START_DELIVERY_SESSION", start)[0][1]
    assert response["outcome"] == "REJECTED"
    assert response["errorCode"] == "STATE_CONFLICT"
    assert exchange(autonomous_runtime, "QUERY_WORK", original_scope(start))[0][1]["status"] == "NOT_FOUND"
    observed = facts(autonomous_runtime, 0)
    assert not observed["pb6Output"] and not observed["cleanLockPowered"]


def test_duplicate_start_is_idempotent_but_a_different_start_is_busy(autonomous_runtime):
    current, _ = current_helpers()
    _, _, start, now = current.setup(autonomous_runtime, initial=False)
    assert exchange(autonomous_runtime, "START_DELIVERY_SESSION", start, now=now)[0][1]["outcome"] == "ACCEPTED"
    other = start_values(
        commandSequence=7,
        mcuCommandUid=str(uuid.uuid4()),
        sessionUid=str(uuid.uuid4()),
    )
    response = exchange(autonomous_runtime, "START_DELIVERY_SESSION", other, now=now)[0][1]
    assert response["outcome"] == "REJECTED" and response["errorCode"] == "BUSY"
    assert exchange(autonomous_runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
