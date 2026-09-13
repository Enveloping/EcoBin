"""First-opening admission only; real C state and exact SQLite receipt, no actuation."""
import ctypes as c

import pytest
import uart2_protocol as uart
from contracts.tests.test_uart_v2_command_guards import minimal_command
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import (
    library, runtime, configured, exchange, original_scope, start_values, take_samples,
)


class Limits(c.Structure):
    _fields_ = [("execution_deadline_ms", c.c_uint64), ("operation_deadline_ms", c.c_uint64),
                ("delivery_auto_close_ms", c.c_uint32), ("unlock_pulse_ms", c.c_uint32)]


def prepared(runtime, tmp_path, *, clean=False, saved=True, mode="stable", start_at=0, candidate=None, **start_changes):
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
        "eventMessageType": event_name, "stepSequence": 0 if clean else 1,
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
                uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:], event_name,
                uart.encode_payload(event_name, event))
            assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
            assert store.list_native_result_report_tasks() == []
        finally:
            store.close()
    return start, event, now


def grant(start, event, *, clean=False, **changes):
    name = "UNLOCK_CLEAN_DOOR" if clean else "AUTHORIZE_DELIVERY_FIRST_OPEN"
    values = minimal_command(uart.REGISTRY, uart.MESSAGE_SPECS[name] | {"name": name})
    values.update(targetMcuBootId=42, commandSequence=7, portNo=1,
                  mcuCommandUid="77777777-7777-4777-8777-777777777777")
    if clean:
        from hardware.tests.test_native_configuration import inputs
        values.update(operationUid=start["operationUid"], parentCommandUid=start["mcuCommandUid"],
                      cleanActionSequence=0, recoveryGeneration=0,
                      unlockPulseMs=inputs()["device"]["cleanSolenoidPulseMs"], remainingOperationWindowMs=200000)
    else:
        values.update(sessionUid=start["sessionUid"], parentStartCommandUid=start["mcuCommandUid"],
                      firstPreOpenMeasurementUid=event["measurementUid"], remainingStartAuthorizationMs=10000)
    values.update(changes)
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    return name, values


def evaluate(runtime, name, values, *, received=1020, now=1020, raw=None):
    lib, endpoint, owner, *_ = runtime
    payload = raw if raw is not None else uart.encode_payload(name, values)
    limits = Limits(91, 92, 93, 94)
    before = bytes(endpoint), bytes(owner), lib.TestFacts_Writes()
    error = lib.McuOpeningGate_Evaluate(owner, endpoint, uart.MESSAGE_SPECS[name]["id"],
                                       payload, len(payload), received, now, c.byref(limits))
    assert before == (bytes(endpoint), bytes(owner), lib.TestFacts_Writes())
    if error:
        assert bytes(limits) == bytes(Limits(91, 92, 93, 94))
    return error, limits


@pytest.mark.parametrize("saved", [False, True])
def test_delivery_requires_exact_saved_initial_measurement_without_accepting_or_actuating(runtime, tmp_path, saved):
    start, event, _ = prepared(runtime, tmp_path, saved=saved)
    name, values = grant(start, event)
    error, limits = evaluate(runtime, name, values)
    assert error == (0 if saved else 7)
    if saved:
        assert limits.execution_deadline_ms == 11020
        assert limits.operation_deadline_ms == limits.unlock_pulse_ms == 0
        assert limits.delivery_auto_close_ms == start["deliveryAutoCloseMs"]
    # The wire command still has no execution owner; gate success is not ACCEPTED.
    assert exchange(runtime, name, values, now=1020) == []
    assert exchange(runtime, "QUERY_WORK", original_scope(start), now=1020)[0][1]["phase"] == "DELIVERY_WAIT_FIRST_OPEN_AUTH"


@pytest.mark.parametrize("saved", [False, True])
def test_first_clean_unlock_requires_its_own_saved_weight_and_original_operation_window(runtime, tmp_path, saved):
    start, event, _ = prepared(runtime, tmp_path, clean=True, saved=saved)
    name, values = grant(start, event, clean=True)
    error, limits = evaluate(runtime, name, values)
    assert error == (0 if saved else 7)
    if saved:
        assert limits.execution_deadline_ms == 30000
        assert limits.operation_deadline_ms == 201020
        assert limits.unlock_pulse_ms == values["unlockPulseMs"]
        assert limits.delivery_auto_close_ms == 0
    assert exchange(runtime, name, values, now=1020) == []
    assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=1020)[0][1]["phase"] == "CLEAN_WAIT_FIRST_UNLOCK"


@pytest.mark.parametrize("clean,change,expected", [
    (False, {"targetMcuBootId": 43}, 8),
    (False, {"sessionUid": "99999999-9999-4999-8999-999999999999"}, 9),
    (False, {"parentStartCommandUid": "99999999-9999-4999-8999-999999999999"}, 9),
    (False, {"firstPreOpenMeasurementUid": "99999999-9999-4999-8999-999999999999"}, 8),
    (False, {"portNo": 2}, 9),
    (False, {"commandSequence": 6}, 10),
    (True, {"operationUid": "99999999-9999-4999-8999-999999999999"}, 9),
    (True, {"parentCommandUid": "99999999-9999-4999-8999-999999999999"}, 9),
    (True, {"cleanActionSequence": 1}, 8),
    (True, {"recoveryGeneration": 1}, 8),
    (True, {"unlockPulseMs": 5001}, 8),
    (True, {"unlockPulseMs": 1}, 8),
])
def test_other_work_boot_measurement_or_reopening_context_is_not_first_open_permission(runtime, tmp_path, clean, change, expected):
    start, event, _ = prepared(runtime, tmp_path, clean=clean)
    name, values = grant(start, event, clean=clean, **change)
    assert evaluate(runtime, name, values)[0] == expected


@pytest.mark.parametrize("clean", [False, True])
def test_start_command_uid_cannot_be_reused_for_a_new_opening_command(runtime, tmp_path, clean):
    start, event, _ = prepared(runtime, tmp_path, clean=clean)
    name, values = grant(start, event, clean=clean, mcuCommandUid=start["mcuCommandUid"])
    assert evaluate(runtime, name, values)[0] == 10


@pytest.mark.parametrize("clean", [False, True])
def test_rechecks_use_original_reception_time_and_never_renew_grant(runtime, tmp_path, clean):
    start, event, _ = prepared(runtime, tmp_path, clean=clean)
    key = "remainingOperationWindowMs" if clean else "remainingStartAuthorizationMs"
    name, values = grant(start, event, clean=clean, **{key: 500})
    for now, expected in [(1020, 0), (1519, 0), (1520, 6), (2500, 6)]:
        error, limits = evaluate(runtime, name, values, received=1020, now=now)
        assert error == expected
        if not error:
            assert limits.execution_deadline_ms == 1520
    # Rejected/read-only checks leave the wire command unknown, not ACCEPTED.
    query = {"queryId": 2, **{key: values[key] for key in
             ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}}
    reply = exchange(runtime, "QUERY_COMMAND", query, now=2500)[0][1]
    assert reply["outcome"] == "NOT_SEEN" and reply["highestCommandSequence"] == 6


@pytest.mark.parametrize("clean", [False, True])
def test_pi_grant_cannot_extend_original_start_deadline(runtime, tmp_path, clean):
    start, event, _ = prepared(runtime, tmp_path, clean=clean, startExecutionWindowMs=2000)
    name, values = grant(start, event, clean=clean)
    assert evaluate(runtime, name, values, now=1999)[1].execution_deadline_ms == 2000
    assert evaluate(runtime, name, values, now=2000)[0] == 6


def test_clean_operation_deadline_is_from_start_not_first_unlock(runtime, tmp_path):
    start, event, _ = prepared(runtime, tmp_path, clean=True, operationWindowMs=2000)
    name, values = grant(start, event, clean=True)
    error, limits = evaluate(runtime, name, values, now=1999)
    assert error == 0 and limits.operation_deadline_ms == limits.execution_deadline_ms == 2000
    assert evaluate(runtime, name, values, now=2000)[0] == 6


@pytest.mark.parametrize("received,now,expected", [
    (1019, 1020, 8), (1021, 1020, 8), (1020, 1019, 8),
    (2**64 - 100, 2**64 - 1, 6),
])
def test_invalid_time_or_absolute_deadline_overflow_cannot_authorize(runtime, tmp_path, received, now, expected):
    start, event, _ = prepared(runtime, tmp_path)
    name, values = grant(start, event)
    assert evaluate(runtime, name, values, received=received, now=now)[0] == expected


@pytest.mark.parametrize("error,expected", [(7, 7), (11, 11), (12, 12), (65535, 12)])
def test_remaining_application_guard_is_mandatory_and_cannot_be_bypassed(runtime, tmp_path, error, expected):
    start, event, _ = prepared(runtime, tmp_path)
    name, values = grant(start, event)
    runtime[4]["error"] = error
    assert evaluate(runtime, name, values)[0] == expected
    runtime[4]["error"] = 0
    assert evaluate(runtime, name, values)[0] == 0


@pytest.mark.parametrize("corruption", ["truncated", "trailing", "digest", "zero_remaining", "oversized"])
def test_full_payload_validation_precedes_reading_authorization_fields(runtime, tmp_path, corruption):
    start, event, _ = prepared(runtime, tmp_path)
    name, values = grant(start, event)
    raw = bytearray(uart.encode_payload(name, values))
    if corruption == "truncated":
        raw = raw[:60]
    elif corruption == "trailing":
        raw += b"\x00"
    elif corruption == "digest":
        raw[16] ^= 1
    elif corruption == "zero_remaining":
        raw[-4:] = bytes(4)
    else:
        raw += bytes(65536)
    assert evaluate(runtime, name, values, raw=bytes(raw))[0] == 5


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("mode,expected", [("median", 0), ("unavailable", 8)])
def test_timeout_median_is_usable_but_missing_weight_never_becomes_zero_permission(runtime, tmp_path, clean, mode, expected):
    start, event, now = prepared(runtime, tmp_path, clean=clean, mode=mode)
    name, values = grant(start, event, clean=clean)
    assert event["reportedWeightGrams"] == 0
    assert event["measurementKind"] == ("TIMEOUT_MEDIAN" if mode == "median" else "UNAVAILABLE")
    assert evaluate(runtime, name, values, received=now, now=now)[0] == expected


@pytest.mark.parametrize("clean", [False, True])
def test_configuration_application_is_rechecked_after_initial_weight_was_saved(runtime, tmp_path, clean):
    from hardware.tests.test_native_configuration import inputs
    from mcu_configuration import NativeMcuConfiguration
    start, event, _ = prepared(runtime, tmp_path, clean=clean)
    name, values = grant(start, event, clean=clean)
    data, candidate = inputs(), NativeMcuConfiguration(**inputs())
    lib, endpoint, *_ = runtime
    # Real fact API reports application no longer complete; config receipt is not proof.
    assert lib.McuDeviceFacts_PublishConfiguration(lib.TestPreparation_Facts(endpoint),
        data["config_version"], bytes.fromhex(data["content_sha256"]),
        bytes.fromhex(candidate.mcu_payload_sha256), 1)
    assert evaluate(runtime, name, values)[0] == 8


@pytest.mark.parametrize("clean", [False, True])
def test_empty_receipt_slot_is_not_proof_of_saved_data(runtime, tmp_path, clean):
    start, event, _ = prepared(runtime, tmp_path, clean=clean)
    name, values = grant(start, event, clean=clean)
    lib, endpoint, *_ = runtime
    # Isolated evidence-loss fixture, not a production reset/recovery recipe.
    # Clear only the actual receipt store via its public initializer.
    lib.McuProcessEventSlot_Init(lib.TestPreparation_Process(endpoint), 42)
    assert evaluate(runtime, name, values)[0] == 8


@pytest.mark.parametrize("clean", [False, True])
def test_old_sequence_and_latest_rejected_uid_cannot_be_repurposed(runtime, tmp_path, clean):
    start, event, _ = prepared(runtime, tmp_path, clean=clean)
    other = start_values(commandSequence=7, mcuCommandUid="88888888-8888-4888-8888-888888888888",
                         sessionUid="99999999-9999-4999-8999-999999999999")
    assert exchange(runtime, "START_DELIVERY_SESSION", other, now=1020)[0][1]["errorCode"] == "BUSY"
    name, values = grant(start, event, clean=clean)
    assert evaluate(runtime, name, values)[0] == 10
    name, values = grant(start, event, clean=clean, commandSequence=8, mcuCommandUid=other["mcuCommandUid"])
    assert evaluate(runtime, name, values)[0] == 10
    name, values = grant(start, event, clean=clean, commandSequence=8)
    assert evaluate(runtime, name, values)[0] == 0


@pytest.mark.parametrize("clean", [False, True])
def test_pi_reconnect_does_not_discard_saved_evidence_or_renew_first_start_window(runtime, tmp_path, clean):
    start, event, _ = prepared(runtime, tmp_path, clean=clean, startExecutionWindowMs=2000)
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 2}, now=1500)[0][1]["mcuBootId"] == 42
    assert exchange(runtime, "BIND_BOOT", {"probeId": 2, "proposedMcuBootId": 42}, now=1500)[0][1]["status"] == "ALREADY_BOUND"
    name, values = grant(start, event, clean=clean)
    assert evaluate(runtime, name, values, received=1500, now=1999)[1].execution_deadline_ms == 2000
    assert evaluate(runtime, name, values, received=1500, now=2000)[0] == 6


def test_bottom_pinch_input_does_not_invent_an_opening_fault(runtime, tmp_path):
    start, event, _ = prepared(runtime, tmp_path)
    lib = runtime[0]
    lib.TestFacts_Pinch(1)
    lib.ActuatorRuntime_Tick()
    name, values = grant(start, event)
    assert evaluate(runtime, name, values)[0] == 0


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("start_at", [100, 2**32 - 500])
def test_nonzero_start_and_32_bit_clock_wrap_keep_original_64_bit_deadlines(runtime, tmp_path, clean, start_at):
    changes = {"startExecutionWindowMs": 2000}
    if clean:
        changes["operationWindowMs"] = 2500
    start, event, received = prepared(runtime, tmp_path, clean=clean, start_at=start_at, **changes)
    name, values = grant(start, event, clean=clean)
    error, limits = evaluate(runtime, name, values, received=received, now=start_at + 1999)
    assert error == 0 and limits.execution_deadline_ms == start_at + 2000
    assert limits.operation_deadline_ms == (start_at + 2500 if clean else 0)
    assert evaluate(runtime, name, values, received=received, now=start_at + 2000)[0] == 6


@pytest.mark.parametrize("clean", [False, True])
def test_actual_mcu_context_reset_invalidates_old_grant_without_reconstructing_weight(runtime, tmp_path, clean):
    start, event, _ = prepared(runtime, tmp_path, clean=clean)
    name, values = grant(start, event, clean=clean)
    lib, endpoint, owner, _, _, sink, guard = runtime
    lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
    assert lib.McuWorkPreparation_Attach(owner, endpoint, 2, guard, None)
    assert evaluate(runtime, name, values)[0] == 8
