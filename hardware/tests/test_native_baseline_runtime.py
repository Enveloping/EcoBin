"""Durable Pi ownership for the native UART-v2 empty-bag baseline."""

from dataclasses import asdict
import json
import time
from types import SimpleNamespace
import uuid

import pytest

from edge_store import EdgeStore
from job_safety import JobPermit, command_request_digest
from native_business_runtime import NativeBusinessRuntime
from native_job_rpc import NativeJobRpc
from onenet_wire import canonical_payload_sha256, encode_event_post
from hardware.tests.test_command_processor import (
    make_real_job_safety,
    valid_service_command,
)
from contracts.tests.test_uart_v2_process_measurement import (
    process_message_values,
)
import uart2_protocol as uart


def _command_timeout(case):
    record = case.store.get_native_command(case.native_uid)
    values = uart.decode_payload(record["message_name"], record["payload"])
    return SimpleNamespace(
        critical=True,
        identity={
            key: values[key]
            for key in (
                "mcuCommandUid",
                "commandDigestSha256",
                "targetMcuBootId",
                "commandSequence",
            )
        },
    )


def _structured_interlock():
    return {
        "profile": "native-clean-bag-interlock-v1",
        "sourceWorkUid": "11111111-1111-4111-8111-111111111111",
        "portNo": 1,
        "oldBagUid": "22222222-2222-4222-8222-222222222222",
        "newBagUid": "33333333-3333-4333-8333-333333333333",
        "sourceCommandUid": "44444444-4444-4444-8444-444444444444",
    }


def _baseline_case(tmp_path, *, interlock=False):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    _, safety = make_real_job_safety(tmp_path)
    command = valid_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    command_uid = str(uuid.uuid4())
    measurement_uid = str(uuid.uuid4())
    bag_uid = str(uuid.uuid4())
    command.update(
        commandUid=command_uid,
        targetDeviceName="device-1",
    )
    command["target"]["uid"] = measurement_uid
    command["payload"].update(
        measurementUid=measurement_uid,
        bagUid=bag_uid,
        portNo=1,
        emptyBagConfirmed=True,
        measurementTimeoutMs=5000,
    )
    command["payload"]["config"] = {
        "version": 7,
        "contentSha256": "a" * 64,
        "mcuPayloadSha256": "b" * 64,
    }
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    assert store.receive_command(command_uid, command["commandType"], command) == "ACCEPTED"
    assert store.claim_next_command()["command_uid"] == command_uid

    query_id = store.reserve_native_query_id()
    boot_id = store.reserve_native_boot_id(query_id)
    assert store.recognize_native_boot_id(boot_id)
    native_uid = str(uuid.uuid4())
    record = store.prepare_native_command(
        "MEASURE_BASELINE",
        native_uid,
        boot_id,
        {
            "measurementUid": measurement_uid,
            "portNo": 1,
            "configVersion": 7,
            "configContentSha256": "a" * 64,
            "startExecutionWindowMs": 5000,
            "measurementTimeoutMs": 5000,
        },
    )
    permit = safety.request_job(
        command,
        work_type="BASELINE",
        work_uid=measurement_uid,
    )
    safety.begin_job(
        permit,
        begin_uid=measurement_uid,
        digest=permit.request_digest_sha256,
    )
    metadata = _structured_interlock() if interlock else None
    if metadata is not None:
        with store.transaction():
            store._set_clean_restart_interlock_in_tx(
                store._conn,
                1,
                True,
                metadata=metadata,
            )
    context = {
        "native_protocol": 2,
        "phase": "NATIVE_BASELINE_RUNNING",
        "start_command_uid": command_uid,
        "start_mcu_command_uid": native_uid,
        "start_runtime_instance_uid": str(uuid.uuid4()),
        "bag_uid": bag_uid,
        "empty_bag_confirmed": True,
        "config": dict(command["payload"]["config"]),
        "job_safety": asdict(permit) | {"begin_uid": measurement_uid},
    }
    assert store.acquire_work_slot("BASELINE", measurement_uid, 1, context)
    assert store.mark_command_waiting_mcu(command_uid, native_uid, {"native_pending": True})
    assert store.claim_native_command_write(native_uid)
    case = SimpleNamespace(
        store=store,
        safety=safety,
        command=command,
        permit=permit,
        record=record,
        native_uid=native_uid,
        boot_id=boot_id,
        measurement_uid=measurement_uid,
        bag_uid=bag_uid,
        interlock=metadata,
    )
    return case


def _save_decision(case, outcome="ACCEPTED", error="NONE"):
    identity = uart.decode_payload("MEASURE_BASELINE", case.record["payload"])
    payload = uart.encode_payload(
        "COMMAND_DECISION",
        {
            key: identity[key]
            for key in (
                "mcuCommandUid",
                "commandDigestSha256",
                "targetMcuBootId",
                "commandSequence",
            )
        }
        | {
            "currentMcuBootId": case.boot_id,
            "outcome": outcome,
            "errorCode": error,
        },
    )
    assert case.store.save_native_command_observation(
        "COMMAND_DECISION",
        payload,
    )


def _result_payload(case, *, kind="STABLE_MEAN", weight=1250, fault="NONE"):
    values = process_message_values(
        uart.REGISTRY,
        "BASELINE_MEASUREMENT_RESULT",
    )
    values.update(
        mcuBootId=case.boot_id,
        mcuEventSequence=3,
        uptimeMs=6000,
        mcuCommandUid=case.native_uid,
        measurementUid=case.measurement_uid,
        portNo=1,
        weightMeasurementUid=str(uuid.uuid4()),
        measurementKind=kind,
        reportedWeightGrams=weight,
        measurementElapsedMs=1000 if kind == "STABLE_MEAN" else 5000,
        sampleCount=5 if kind == "STABLE_MEAN" else 0,
        sampleSpanGrams=50 if kind == "STABLE_MEAN" else 0,
        calibrationVersion=1,
        faultCode=fault,
        configVersion=7,
    )
    return uart.encode_payload("BASELINE_MEASUREMENT_RESULT", values)


def _save_result(case, *, kind="STABLE_MEAN", weight=1250, fault="NONE"):
    payload = _result_payload(case, kind=kind, weight=weight, fault=fault)
    scope = case.store._native_baseline_scope(
        case.store.get_native_command(case.native_uid)
    )
    raw_scope = uart.encode_payload(
        "QUERY_PROCESS_EVENT",
        scope | {"queryId": 1},
    )[8:]
    saved = case.store.save_native_process_receipt(
        raw_scope,
        "BASELINE_MEASUREMENT_RESULT",
        payload,
    )
    # A duplicate result frame/query is idempotent and returns the same receipt.
    assert case.store.save_native_process_receipt(
        raw_scope,
        "BASELINE_MEASUREMENT_RESULT",
        payload,
    ) == saved


def _finish(case, event_uid):
    pending = case.store.prepare_native_baseline_completion(
        case.permit,
        case.native_uid,
        device_name="device-1",
        event_uid=event_uid,
    )
    assert pending["state"] == "PREPARED"
    case.store.confirm_native_baseline_mcu_release(
        case.permit,
        case.native_uid,
        release_observation=_released_observation(case),
    )
    case.safety.complete_job(
        case.permit,
        completion_uid=case.permit.command_uid,
        outcome="SUCCEEDED",
        completion_digest_sha256=pending["evidenceSha256"],
    )
    snapshot = case.safety.get_job_permit(case.permit.permit_uid)
    case.store.apply_native_baseline_completion(
        case.permit,
        case.native_uid,
        device_name="device-1",
        permit_snapshot=snapshot,
    )
    return json.loads(case.store.get_event(event_uid)["payload_json"])


def _released_observation(case, *, query_id=99):
    scope = case.store._native_baseline_scope(
        case.store.get_native_command(case.native_uid)
    )
    raw_scope = uart.encode_payload(
        "QUERY_PROCESS_EVENT",
        scope | {"queryId": 1},
    )[8:]
    saved = case.store.get_native_process_receipt(raw_scope)
    body = bytes(saved["payload"])
    measured = uart.decode_payload("BASELINE_MEASUREMENT_RESULT", body)
    return scope | {
        "queryId": query_id,
        "currentMcuBootId": case.boot_id,
        "status": "RELEASED",
        "mcuEventSequence": measured["mcuEventSequence"],
        "eventDigestSha256": uart.compute_process_event_digest(
            "BASELINE_MEASUREMENT_RESULT",
            body,
        ),
    }


def test_native_baseline_survives_prepare_crash_and_preserves_clean_interlock(tmp_path):
    case = _baseline_case(tmp_path, interlock=True)
    try:
        _save_decision(case)
        _save_result(case)
        event_uid = str(uuid.uuid4())
        first = case.store.prepare_native_baseline_completion(
            case.permit,
            case.native_uid,
            device_name="device-1",
            event_uid=event_uid,
        )
        assert first["state"] == "PREPARED"
        assert case.store.get_work_slot()["work_state"] == "COMPLETING"

        # Crash after the local atomic prepare: reopen and finish the same event.
        case.store.close()
        case.store.initialize()
        again = case.store.prepare_native_baseline_completion(
            case.permit,
            case.native_uid,
            device_name="device-1",
            event_uid=event_uid,
        )
        assert again == first
        event = _finish(case, event_uid)
        encode_event_post("BASELINE_MEASUREMENT_COMPLETE", event)
        assert event["payload"]["totalWeightMeasurement"]["reportedWeightGrams"] == 1250
        assert case.store.get_bag_baseline(case.bag_uid)["weight_grams"] == 1250
        assert case.store.get_work_slot() is None
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
        events = [
            row
            for row in case.store.list_pending_events(limit=100)
            if row["event_type"] == "BASELINE_MEASUREMENT_COMPLETE"
        ]
        assert len(events) == 1
    finally:
        case.store.close()


@pytest.mark.parametrize(
    ("kind", "weight", "fault", "expected_status"),
    [
        ("UNAVAILABLE", 0, "WEIGHT_TIMEOUT", "TIMEOUT"),
        ("STABLE_MEAN", -5, "NONE", "STABLE"),
    ],
)
def test_failed_or_negative_native_baseline_reports_but_does_not_update_tare(
    tmp_path,
    kind,
    weight,
    fault,
    expected_status,
):
    case = _baseline_case(tmp_path)
    try:
        _save_decision(case)
        _save_result(case, kind=kind, weight=weight, fault=fault)
        event = _finish(case, str(uuid.uuid4()))
        encode_event_post("BASELINE_MEASUREMENT_COMPLETE", event)
        measurement = event["payload"]["totalWeightMeasurement"]
        assert measurement["status"] == expected_status
        assert measurement["reportedWeightGrams"] == (
            weight if kind == "STABLE_MEAN" else None
        )
        assert case.store.get_bag_baseline(case.bag_uid) is None
        assert case.store.get_work_slot() is None
    finally:
        case.store.close()


def test_result_before_command_decision_retires_dispatch_without_fabricating_ack(
    tmp_path,
):
    case = _baseline_case(tmp_path)
    try:
        _save_result(case)
        _finish(case, str(uuid.uuid4()))
        original = case.store.get_native_command(case.native_uid)
        assert original["decision_outcome"] is None
        assert original["dispatch_retired"] == 1
        assert case.store.get_work_slot() is None

        case.store.close()
        case.store.initialize()
        successor = case.store.prepare_native_command(
            "MEASURE_BASELINE",
            str(uuid.uuid4()),
            case.boot_id,
            {
                "measurementUid": str(uuid.uuid4()),
                "portNo": 1,
                "configVersion": 7,
                "configContentSha256": "a" * 64,
                "startExecutionWindowMs": 5000,
                "measurementTimeoutMs": 5000,
            },
        )
        assert successor["command_sequence"] == original["command_sequence"] + 1
    finally:
        case.store.close()


def test_saved_result_without_mcu_release_latches_fault_and_retains_result_path(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    owner = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    owner.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    owner._rpc = NativeJobRpc()
    try:
        _save_result(case)
        slot = case.store.get_work_slot()
        context = dict(slot["context"])
        context["start_runtime_instance_uid"] = owner._runtime_instance_uid
        assert case.store.update_work_context(case.permit.work_uid, context)
        with case.store.transaction():
            case.store._conn.execute(
                """UPDATE native_process_receipt
                   SET created_at=datetime('now', '-3 seconds')"""
            )
        timeout = SimpleNamespace(critical=False)
        owner._control_failure_poll(0, request_timeouts=(timeout,))
        command = case.store.get_command(case.command["commandUid"])
        assert command["state"] == "WAITING_MCU_RESULT"
        assert "nativeControlFailure" not in (command["result"] or {})
        assert case.store.get_work_slot() is not None
        assert case.store.get_state("native_blocking_fault") == (
            "MCU_COMMUNICATION_UNAVAILABLE"
        )
        assert case.store.get_active_edge_fault(
            "UART",
            "UART_PROTOCOL",
        )["severity"] == "BLOCK_DEVICE"
        assert case.store.get_bag_baseline(case.bag_uid) is None
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        owner._rpc.close()
        case.store.close()


def test_aged_saved_result_after_pi_restart_queries_before_release_fault(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    _save_result(case)
    with case.store.transaction():
        case.store._conn.execute(
            """UPDATE native_process_receipt
               SET created_at=datetime('now', '-20 seconds')"""
        )
    case.store.close()
    case.store.initialize()
    sent = []
    restarted = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    restarted.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    restarted.transport = SimpleNamespace(
        write=lambda frame: sent.append(frame) or len(frame)
    )
    restarted.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    restarted._rpc = NativeJobRpc()
    try:
        restarted._control_failure_poll(0)
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        restarted._work_poll(0)
        query = next(
            frame
            for frame in sent
            if uart.decode_frame(frame)["messageName"]
            == "QUERY_PROCESS_EVENT"
        )
        assert restarted._handoff.accept_frame(
            _process_reply(query, case, "HELD"),
            0,
        )
        restarted._control_failure_poll(999)
        assert case.store.get_state("native_blocking_fault") in {None, ""}

        # Match NativeBusinessRuntime.poll(): control failure decisions run
        # before work/query progress on every tick.  The first HELD answer has
        # been consumed, so the second query must still get its bounded chance
        # to observe the MCU's RELEASED custody state.
        restarted._control_failure_poll(1000)
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        restarted._work_poll(1000)
        query = [
            frame
            for frame in sent
            if uart.decode_frame(frame)["messageName"]
            == "QUERY_PROCESS_EVENT"
        ][-1]
        assert restarted._handoff.accept_frame(
            _process_reply(query, case, "RELEASED"),
            1000,
        )
        restarted._control_failure_poll(1001)
        for _ in range(100):
            restarted._work_poll(1001)
            if case.store.get_work_slot() is None:
                break
            time.sleep(0.001)
        assert case.store.get_work_slot() is None
        assert case.store.get_command(case.command["commandUid"])["state"] == (
            "COMPLETED"
        )
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        assert not any(
            uart.decode_frame(frame)["messageName"] == "MEASURE_BASELINE"
            for frame in sent
        )
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        restarted._rpc.close()
        case.store.close()


def test_persistent_exact_held_replies_do_not_create_a_communication_fault(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    _save_result(case)
    with case.store.transaction():
        case.store._conn.execute(
            """UPDATE native_process_receipt
               SET created_at=datetime('now', '-20 seconds')"""
        )
    case.store.close()
    case.store.initialize()
    sent = []
    restarted = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    restarted.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    restarted.transport = SimpleNamespace(
        write=lambda frame: sent.append(frame) or len(frame)
    )
    restarted.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    restarted._rpc = NativeJobRpc()
    try:
        # First real poll-order round: control permits recovery, work asks, MCU
        # still owns the already-saved result and answers HELD.
        restarted._control_failure_poll(0)
        restarted._work_poll(0)
        first_query = next(
            frame
            for frame in sent
            if uart.decode_frame(frame)["messageName"]
            == "QUERY_PROCESS_EVENT"
        )
        assert restarted._handoff.accept_frame(
            _process_reply(first_query, case, "HELD"),
            0,
        )

        # A second exact HELD answer proves communication, but does not invent
        # the missing RELEASED custody fact or release the work slot.
        restarted._control_failure_poll(1000)
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        restarted._work_poll(1000)
        second_query = [
            frame
            for frame in sent
            if uart.decode_frame(frame)["messageName"]
            == "QUERY_PROCESS_EVENT"
        ][-1]
        assert restarted._handoff.accept_frame(
            _process_reply(second_query, case, "HELD"),
            1000,
        )
        restarted._control_failure_poll(1999)
        assert case.store.get_state("native_blocking_fault") in {None, ""}

        restarted._control_failure_poll(2000)
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        assert case.store.get_active_edge_fault(
            "UART",
            "UART_PROTOCOL",
        ) is None
        assert case.store.get_work_slot() is not None
        assert case.store.get_command(case.command["commandUid"])["state"] in {
            "WAITING_MCU_RESULT",
            "COMPLETED",
        }
        assert not any(
            uart.decode_frame(frame)["messageName"] == "MEASURE_BASELINE"
            for frame in sent
        )
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        restarted._rpc.close()
        case.store.close()


def test_aged_saved_result_uses_recognized_new_boot_without_latching_fault(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    _save_result(case)
    with case.store.transaction():
        case.store._conn.execute(
            """UPDATE native_process_receipt
               SET created_at=datetime('now', '-20 seconds')"""
        )
    query_id = case.store.reserve_native_query_id()
    replacement_boot = case.store.reserve_native_boot_id(query_id)
    assert replacement_boot > case.boot_id
    assert case.store.recognize_native_boot_id(replacement_boot)
    sent = []
    owner = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    owner.boot = SimpleNamespace(current_boot=lambda now: replacement_boot)
    owner.transport = SimpleNamespace(
        write=lambda frame: sent.append(frame) or len(frame)
    )
    owner.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    owner._rpc = NativeJobRpc()
    try:
        owner._control_failure_poll(0)
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        for _ in range(100):
            owner._work_poll(0)
            if case.store.get_work_slot() is None:
                break
            time.sleep(0.001)
        assert case.store.get_work_slot() is None
        assert case.store.get_command(case.command["commandUid"])["state"] == (
            "COMPLETED"
        )
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        assert not any(
            uart.decode_frame(frame)["messageName"] == "MEASURE_BASELINE"
            for frame in sent
        )
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        owner._rpc.close()
        case.store.close()


def _drain_failure(owner, case):
    for _ in range(100):
        owner._work_poll(1000)
        if case.store.get_work_slot() is None:
            return
        time.sleep(0.001)
    raise AssertionError("permanent failure receipt did not release baseline slot")


def _process_reply(query_frame, case, status):
    query = uart.decode_frame(query_frame)
    assert query["messageName"] == "QUERY_PROCESS_EVENT"
    values = uart.decode_payload("QUERY_PROCESS_EVENT", query["payload"])
    scope = case.store._native_baseline_scope(
        case.store.get_native_command(case.native_uid)
    )
    raw_scope = uart.encode_payload(
        "QUERY_PROCESS_EVENT",
        scope | {"queryId": 1},
    )[8:]
    saved = case.store.get_native_process_receipt(raw_scope)
    body = bytes(saved["payload"])
    measured = uart.decode_payload("BASELINE_MEASUREMENT_RESULT", body)
    response = values | {
        "currentMcuBootId": case.boot_id,
        "status": status,
        "mcuEventSequence": measured["mcuEventSequence"],
        "eventDigestSha256": uart.compute_process_event_digest(
            "BASELINE_MEASUREMENT_RESULT",
            body,
        ),
    }
    return uart.encode_frame(
        "PROCESS_EVENT_QUERY_REPLY",
        100,
        uart.encode_payload("PROCESS_EVENT_QUERY_REPLY", response),
    )


@pytest.mark.parametrize("saved_failure", ["SHORT_WRITE", "WRITE_FAILED"])
def test_baseline_waits_for_fresh_mcu_release_after_saved_write_failure_and_pi_restart(
    tmp_path,
    saved_failure,
):
    case = _baseline_case(tmp_path, interlock=True)
    _save_decision(case)
    _save_result(case)
    first_sent = []

    def broken_write(frame):
        first_sent.append(frame)
        if uart.decode_frame(frame)["messageName"] == "PROCESS_EVENT_SAVED":
            if saved_failure == "WRITE_FAILED":
                raise OSError("simulated saved write failure")
            return 1
        return len(frame)

    first = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
    )
    first.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    first.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    first.transport = SimpleNamespace(write=broken_write)
    first._rpc = NativeJobRpc()
    try:
        slot = case.store.get_work_slot()
        record = case.store.get_native_command(case.native_uid)
        first._baseline_poll(slot, case.permit, record, 0)
        query = next(
            frame
            for frame in first_sent
            if uart.decode_frame(frame)["messageName"] == "QUERY_PROCESS_EVENT"
        )
        assert first._handoff.accept_frame(
            _process_reply(query, case, "HELD"),
            0,
        )
        assert first._handoff.last_write_error == saved_failure
        for _ in range(30):
            first._baseline_poll(
                case.store.get_work_slot(),
                case.permit,
                case.store.get_native_command(case.native_uid),
                1,
            )
            time.sleep(0.001)
        assert case.store.get_work_slot() is not None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
    finally:
        first._rpc.close()

    # A new Pi process queries the same immutable command/receipt.  It neither
    # measures nor sends MEASURE_BASELINE again, and may finish only after a
    # subsequent fresh query says that the MCU released the event slot.
    case.store.close()
    case.store.initialize()
    recovered = case.store.recover_interrupted_commands()
    assert recovered["physical_failed"] == 0
    assert case.store.get_command(case.command["commandUid"])["state"] == "COMPLETED"
    assert case.store.get_work_slot()["work_state"] == "COMPLETING"
    resumed_sent = []

    def resumed_write(frame):
        resumed_sent.append(frame)
        return len(frame)

    resumed = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
    )
    resumed.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    resumed.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    resumed.transport = SimpleNamespace(write=resumed_write)
    resumed._rpc = NativeJobRpc()
    try:
        resumed._baseline_poll(
            case.store.get_work_slot(),
            case.permit,
            case.store.get_native_command(case.native_uid),
            0,
        )
        query = next(
            frame
            for frame in resumed_sent
            if uart.decode_frame(frame)["messageName"] == "QUERY_PROCESS_EVENT"
        )
        assert resumed._handoff.accept_frame(
            _process_reply(query, case, "HELD"),
            0,
        )
        assert case.store.get_work_slot() is not None
        resumed._baseline_poll(
            case.store.get_work_slot(),
            case.permit,
            case.store.get_native_command(case.native_uid),
            1000,
        )
        query = [
            frame
            for frame in resumed_sent
            if uart.decode_frame(frame)["messageName"] == "QUERY_PROCESS_EVENT"
        ][-1]
        assert resumed._handoff.accept_frame(
            _process_reply(query, case, "RELEASED"),
            1000,
        )
        for _ in range(100):
            resumed._baseline_poll(
                case.store.get_work_slot(),
                case.permit,
                case.store.get_native_command(case.native_uid),
                1000,
            )
            if case.store.get_work_slot() is None:
                break
            time.sleep(0.001)
        assert case.store.get_work_slot() is None
        assert not any(
            uart.decode_frame(frame)["messageName"] == "MEASURE_BASELINE"
            for frame in first_sent + resumed_sent
        )
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        resumed._rpc.close()
        case.store.close()


def test_explicit_mcu_rejection_is_not_disguised_as_timeout(tmp_path):
    case = _baseline_case(tmp_path, interlock=True)
    owner = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    owner.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    owner._rpc = NativeJobRpc()
    try:
        _save_decision(case, "REJECTED", "SAFETY_BLOCKED")
        owner._control_failure_poll(0)
        _drain_failure(owner, case)
        command = case.store.get_command(case.command["commandUid"])
        assert command["state"] == "REJECTED"
        assert command["last_error"] == "SAFETY_BLOCKED"
        marker = command["result"]["nativeControlFailure"]
        assert marker["state"] == "APPLIED"
        assert marker["evidence"]["stage"] == "REJECTED"
        observed = [
            json.loads(row["payload_json"])
            for row in case.store.list_pending_events(limit=100)
            if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
            and json.loads(row["payload_json"])["commandUid"]
            == case.command["commandUid"]
        ]
        assert len(observed) == 1
        assert observed[0]["payload"] == {
            "observedCommandType": "MEASURE_EMPTY_BAG_BASELINE",
            "stage": "REJECTED",
            "mcuCommandUid": None,
            "errorCode": "SAFETY_BLOCKED",
        }
        encode_event_post("DEVICE_COMMAND_OBSERVED", observed[0])
        assert case.safety.get_job_permit(case.permit.permit_uid)[
            "completionOutcome"
        ] == "FAILED"
        assert case.store.get_bag_baseline(case.bag_uid) is None
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
        assert case.store.get_state("native_blocking_fault") in {None, ""}
    finally:
        owner._rpc.close()
        case.store.close()


def test_accepted_baseline_without_result_waits_for_communication_deadline(tmp_path):
    case = _baseline_case(tmp_path, interlock=True)
    owner = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    owner.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    owner._rpc = NativeJobRpc()
    try:
        _save_decision(case)
        owner._control_failure_poll(999)
        assert case.store.get_command(case.command["commandUid"])["state"] == "WAITING_MCU_RESULT"
        assert case.store.get_work_slot() is not None

        timeout = type("Timeout", (), {"critical": False})()
        owner._control_failure_poll(1000, request_timeouts=(timeout,))
        _drain_failure(owner, case)
        command = case.store.get_command(case.command["commandUid"])
        assert command["state"] == "FAILED"
        assert command["last_error"] == "MCU_COMMUNICATION_UNAVAILABLE"
        assert command["result"]["nativeControlFailure"]["state"] == "APPLIED"
        assert case.store.get_bag_baseline(case.bag_uid) is None
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
        assert case.store.get_state("native_blocking_fault") == "MCU_COMMUNICATION_UNAVAILABLE"
    finally:
        owner._rpc.close()
        case.store.close()


def test_write_claimed_timeout_retires_dispatch_and_releases_successor_gate(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    owner = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    owner.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    owner._rpc = NativeJobRpc()
    try:
        owner._control_failure_poll(0)
        owner._control_failure_poll(
            1000,
            request_timeouts=(_command_timeout(case),),
        )
        _drain_failure(owner, case)
        command = case.store.get_command(case.command["commandUid"])
        assert command["state"] == "FAILED"
        assert command["last_error"] == "MCU_COMMUNICATION_UNAVAILABLE"
        original = case.store.get_native_command(case.native_uid)
        assert original["decision_outcome"] is None
        assert original["write_claimed"] == 1
        assert original["dispatch_retired"] == 1
        assert case.store.get_work_slot() is None

        # Operator handling of the independent communication fault may admit
        # future work; the permanently failed command cannot keep the SQLite
        # one-pending gate occupied afterward.
        case.store.close()
        case.store.initialize()
        assert case.store.get_native_command(case.native_uid)[
            "dispatch_retired"
        ] == 1
        case.store.set_state("native_blocking_fault", "")
        successor = case.store.prepare_native_command(
            "MEASURE_BASELINE",
            str(uuid.uuid4()),
            case.boot_id,
            {
                "measurementUid": str(uuid.uuid4()),
                "portNo": 1,
                "configVersion": 7,
                "configContentSha256": "a" * 64,
                "startExecutionWindowMs": 5000,
                "measurementTimeoutMs": 5000,
            },
        )
        assert successor["command_sequence"] == original["command_sequence"] + 1
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        owner._rpc.close()
        case.store.close()


def test_reopen_backfills_legacy_applied_control_failure_dispatch_retirement(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    owner = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    owner.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    owner._rpc = NativeJobRpc()
    try:
        owner._control_failure_poll(0)
        owner._control_failure_poll(
            1000,
            request_timeouts=(_command_timeout(case),),
        )
        _drain_failure(owner, case)
        command = case.store.get_command(case.command["commandUid"])
        assert command["result"]["nativeControlFailure"]["state"] == "APPLIED"
        assert case.store.get_work_slot() is None

        # Simulate the exact old-store shape: the terminal proof and released
        # slot predate dispatch_retired, so the newly added column still has
        # its default zero value and decision_outcome remains unknown.
        with case.store.transaction():
            case.store._conn.execute(
                """UPDATE native_mcu_command
                   SET dispatch_retired=0
                   WHERE command_uid=?""",
                (case.native_uid,),
            )
        assert case.store.get_native_command(case.native_uid)[
            "decision_outcome"
        ] is None
        case.store.close()

        case.store.initialize()
        assert case.store.get_native_command(case.native_uid)[
            "dispatch_retired"
        ] == 1
        case.store.close()
        case.store.initialize()
        assert case.store.get_native_command(case.native_uid)[
            "dispatch_retired"
        ] == 1

        successor = case.store.prepare_native_command(
            "MEASURE_BASELINE",
            str(uuid.uuid4()),
            case.boot_id,
            {
                "measurementUid": str(uuid.uuid4()),
                "portNo": 1,
                "configVersion": 7,
                "configContentSha256": "a" * 64,
                "startExecutionWindowMs": 5000,
                "measurementTimeoutMs": 5000,
            },
        )
        assert successor["command_sequence"] == case.record["command_sequence"] + 1
        case.store.close()
        case.store.initialize()
        assert case.store.get_native_command(successor["command_uid"])[
            "dispatch_retired"
        ] == 0
    finally:
        owner._rpc.close()
        case.store.close()


def test_dispatch_backfill_proof_scan_is_bounded_to_the_pending_gate_candidate(
    tmp_path,
    monkeypatch,
):
    case = _baseline_case(tmp_path)
    try:
        _save_decision(case)
        historical_uids = [case.native_uid]
        for _ in range(32):
            uid = str(uuid.uuid4())
            record = case.store.prepare_native_command(
                "MEASURE_BASELINE",
                uid,
                case.boot_id,
                {
                    "measurementUid": str(uuid.uuid4()),
                    "portNo": 1,
                    "configVersion": 7,
                    "configContentSha256": "a" * 64,
                    "startExecutionWindowMs": 5000,
                    "measurementTimeoutMs": 5000,
                },
            )
            assert case.store.claim_native_command_write(uid)
            _save_decision(
                SimpleNamespace(
                    store=case.store,
                    record=record,
                    boot_id=case.boot_id,
                )
            )
            historical_uids.append(uid)

        pending_uid = str(uuid.uuid4())
        case.store.prepare_native_command(
            "MEASURE_BASELINE",
            pending_uid,
            case.boot_id,
            {
                "measurementUid": str(uuid.uuid4()),
                "portNo": 1,
                "configVersion": 7,
                "configContentSha256": "a" * 64,
                "startExecutionWindowMs": 5000,
                "measurementTimeoutMs": 5000,
            },
        )
        case.store.close()

        failure_scans = []
        completion_scans = []
        original_failure = (
            case.store._native_control_failure_proves_dispatch_retirement
        )
        original_completion = (
            case.store._native_baseline_completion_proves_dispatch_retirement
        )

        def failure_proof(conn, record):
            failure_scans.append(record["command_uid"])
            return original_failure(conn, record)

        def completion_proof(conn, record):
            completion_scans.append(record["command_uid"])
            return original_completion(conn, record)

        monkeypatch.setattr(
            case.store,
            "_native_control_failure_proves_dispatch_retirement",
            failure_proof,
        )
        monkeypatch.setattr(
            case.store,
            "_native_baseline_completion_proves_dispatch_retirement",
            completion_proof,
        )
        case.store.initialize()

        assert failure_scans == [pending_uid]
        assert completion_scans == [pending_uid]
        assert case.store.get_native_command(pending_uid)["dispatch_retired"] == 0
        assert all(
            case.store.get_native_command(uid)["dispatch_retired"] == 0
            for uid in historical_uids
        )
    finally:
        case.store.close()


def test_retired_baseline_verification_decodes_only_its_exact_cloud_command(
    tmp_path,
    monkeypatch,
):
    case = _baseline_case(tmp_path)
    try:
        _save_result(case)
        _finish(case, str(uuid.uuid4()))
        noise = []
        for _ in range(64):
            command_uid = str(uuid.uuid4())
            measurement_uid = str(uuid.uuid4())
            command = json.loads(json.dumps(case.command))
            command["commandUid"] = command_uid
            command["target"]["uid"] = measurement_uid
            command["payload"]["measurementUid"] = measurement_uid
            command["payload"]["bagUid"] = str(uuid.uuid4())
            command["payloadSha256"] = canonical_payload_sha256(
                command["payload"]
            )
            assert case.store.receive_command(
                command_uid,
                command["commandType"],
                command,
            ) == "ACCEPTED"
            noise.append((str(uuid.uuid4()), command_uid))
        with case.store.transaction():
            case.store._conn.executemany(
                """UPDATE command_inbox
                   SET state='COMPLETED', result_json='{}', mcu_command_uid=?
                   WHERE command_uid=?""",
                noise,
            )

        decoded = []
        original_decode = case.store._decode_command_row

        def decode(row):
            decoded.append(row["command_uid"])
            return original_decode(row)

        monkeypatch.setattr(case.store, "_decode_command_row", decode)
        successor = case.store.prepare_native_command(
            "MEASURE_BASELINE",
            str(uuid.uuid4()),
            case.boot_id,
            {
                "measurementUid": str(uuid.uuid4()),
                "portNo": 1,
                "configVersion": 7,
                "configContentSha256": "a" * 64,
                "startExecutionWindowMs": 5000,
                "measurementTimeoutMs": 5000,
            },
        )
        assert successor["command_sequence"] == case.record["command_sequence"] + 1
        assert decoded == [case.command["commandUid"]]

        baseline_plan = case.store._conn.execute(
            """EXPLAIN QUERY PLAN SELECT * FROM command_inbox
               WHERE state='COMPLETED' AND result_json IS NOT NULL
                 AND mcu_command_uid=?""",
            (case.native_uid,),
        ).fetchall()
        assert any(
            "idx_cmd_inbox_mcu_command_state" in row["detail"]
            for row in baseline_plan
        )
        failure_plan = case.store._conn.execute(
            """EXPLAIN QUERY PLAN SELECT * FROM command_inbox
               WHERE state IN ('FAILED','REJECTED')
                 AND result_json IS NOT NULL
                 AND json_extract(
                       result_json,
                       '$.nativeControlFailure.evidence.startCommandUid'
                     )=?""",
            (case.native_uid,),
        ).fetchall()
        assert any(
            "idx_cmd_inbox_native_failure_start" in row["detail"]
            for row in failure_plan
        )
    finally:
        case.store.close()


def test_healthy_link_cannot_keep_accepted_baseline_without_result_forever_after_pi_restart(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    first = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    first.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    first._rpc = NativeJobRpc()
    try:
        _save_decision(case)
        first._control_failure_poll(999)
        assert case.store.get_command(case.command["commandUid"])["state"] == "WAITING_MCU_RESULT"
    finally:
        first._rpc.close()

    # The first Pi process is gone.  Age the immutable ACCEPTED observation
    # beyond 5 s measurement + 1 s communication closure; a new process must
    # use that original persisted basis rather than start another timer.
    with case.store.transaction():
        case.store._conn.execute(
            """UPDATE native_mcu_command_observation
               SET created_at=datetime('now', '-7 seconds')
               WHERE command_uid=? AND outcome='ACCEPTED'""",
            (case.native_uid,),
        )
    restarted = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    restarted.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    sent = []
    restarted.transport = SimpleNamespace(
        write=lambda frame: sent.append(frame) or len(frame)
    )
    restarted.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    restarted._rpc = NativeJobRpc()
    try:
        restarted._control_failure_poll(0)
        assert case.store.get_command(case.command["commandUid"])["state"] == "WAITING_MCU_RESULT"
        restarted._work_poll(0)
        assert any(
            uart.decode_frame(frame)["messageName"] == "QUERY_PROCESS_EVENT"
            for frame in sent
        )
        restarted._control_failure_poll(999)
        assert case.store.get_command(case.command["commandUid"])["state"] == "WAITING_MCU_RESULT"
        restarted._control_failure_poll(1000)
        failed = case.store.get_command(case.command["commandUid"])
        assert failed["state"] == "FAILED"
        assert failed["last_error"] == "MCU_BASELINE_RESULT_UNAVAILABLE"
        assert len(case.store.list_native_commands()) == 1
        assert case.store.get_native_command(case.native_uid)["write_claimed"]
        _drain_failure(restarted, case)
        assert case.store.get_work_slot() is None
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        restarted._rpc.close()
        case.store.close()


def test_expired_accepted_baseline_gets_query_only_chance_to_recover_held_result(
    tmp_path,
):
    case = _baseline_case(tmp_path, interlock=True)
    _save_decision(case)
    with case.store.transaction():
        case.store._conn.execute(
            """UPDATE native_mcu_command_observation
               SET created_at=datetime('now', '-20 seconds')
               WHERE command_uid=? AND outcome='ACCEPTED'""",
            (case.native_uid,),
        )
    sent = []

    def write(frame):
        sent.append(frame)
        return len(frame)

    restarted = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    restarted.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    restarted.transport = SimpleNamespace(write=write)
    restarted.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    restarted._rpc = NativeJobRpc()
    try:
        restarted._control_failure_poll(0)
        assert case.store.get_command(case.command["commandUid"])["state"] == "WAITING_MCU_RESULT"
        restarted._work_poll(0)
        query_frame = next(
            frame
            for frame in sent
            if uart.decode_frame(frame)["messageName"] == "QUERY_PROCESS_EVENT"
        )
        query = uart.decode_payload(
            "QUERY_PROCESS_EVENT",
            uart.decode_frame(query_frame)["payload"],
        )
        body = _result_payload(case)
        measured = uart.decode_payload("BASELINE_MEASUREMENT_RESULT", body)
        held = query | {
            "currentMcuBootId": case.boot_id,
            "status": "HELD",
            "mcuEventSequence": measured["mcuEventSequence"],
            "eventDigestSha256": uart.compute_process_event_digest(
                "BASELINE_MEASUREMENT_RESULT",
                body,
            ),
        }
        assert restarted._handoff.accept_frame(
            uart.encode_frame(
                "PROCESS_EVENT_QUERY_REPLY",
                101,
                uart.encode_payload("PROCESS_EVENT_QUERY_REPLY", held),
            ),
            0,
        )
        assert restarted._handoff.accept_frame(
            uart.encode_frame("BASELINE_MEASUREMENT_RESULT", 102, body),
            0,
        )
        assert case.store.get_command(case.command["commandUid"])["state"] == "WAITING_MCU_RESULT"
        restarted._control_failure_poll(1000)
        restarted._work_poll(1000)
        query_frame = [
            frame
            for frame in sent
            if uart.decode_frame(frame)["messageName"] == "QUERY_PROCESS_EVENT"
        ][-1]
        assert restarted._handoff.accept_frame(
            _process_reply(query_frame, case, "RELEASED"),
            1000,
        )
        for _ in range(100):
            restarted._work_poll(1000)
            if case.store.get_work_slot() is None:
                break
            time.sleep(0.001)
        assert case.store.get_work_slot() is None
        command = case.store.get_command(case.command["commandUid"])
        assert command["state"] == "COMPLETED"
        assert command["last_error"] is None
        assert case.store.get_bag_baseline(case.bag_uid)["weight_grams"] == 1250
        assert not any(
            uart.decode_frame(frame)["messageName"] == "MEASURE_BASELINE"
            for frame in sent
        )
    finally:
        restarted._rpc.close()
        case.store.close()


@pytest.mark.parametrize(
    ("state", "expected_error"),
    [
        ("UNCLAIMED", "EDGE_RESTARTED_BEFORE_START"),
        ("ACCEPTED_NO_RESULT", "MCU_BASELINE_RESULT_UNAVAILABLE"),
    ],
)
def test_explicit_interrupted_command_recovery_preserves_native_baseline_state(
    tmp_path,
    state,
    expected_error,
):
    case = _baseline_case(tmp_path, interlock=True)
    if state == "UNCLAIMED":
        with case.store.transaction():
            case.store._conn.execute(
                """UPDATE native_mcu_command
                   SET write_claimed=0
                   WHERE command_uid=?""",
                (case.native_uid,),
            )
    else:
        _save_decision(case)
        with case.store.transaction():
            case.store._conn.execute(
                """UPDATE native_mcu_command_observation
                   SET created_at=datetime('now', '-20 seconds')
                   WHERE command_uid=? AND outcome='ACCEPTED'""",
                (case.native_uid,),
            )

    case.store.close()
    case.store.initialize()
    recovered = case.store.recover_interrupted_commands()
    assert recovered["physical_locked"] == 1
    command = case.store.get_command(case.command["commandUid"])
    assert command["state"] == "WAITING_MCU_RESULT"
    assert case.store.get_work_slot()["work_state"] == "ACTIVE"

    restarted = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    restarted.boot = SimpleNamespace(current_boot=lambda now: case.boot_id)
    sent = []
    restarted.transport = SimpleNamespace(
        write=lambda frame: sent.append(frame) or len(frame)
    )
    restarted.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    restarted._rpc = NativeJobRpc()
    try:
        restarted._control_failure_poll(0)
        if state == "ACCEPTED_NO_RESULT":
            restarted._work_poll(0)
            assert any(
                uart.decode_frame(frame)["messageName"]
                == "QUERY_PROCESS_EVENT"
                for frame in sent
            )
            restarted._control_failure_poll(1000)
        _drain_failure(restarted, case)
        command = case.store.get_command(case.command["commandUid"])
        assert command["state"] == "FAILED"
        assert command["last_error"] == expected_error
        assert len(case.store.list_native_commands()) == 1
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
    finally:
        restarted._rpc.close()
        case.store.close()


def test_mcu_restart_closes_baseline_without_creating_or_clearing_bag_lock(tmp_path):
    case = _baseline_case(tmp_path, interlock=True)
    owner = NativeBusinessRuntime(
        case.store,
        case.safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    owner.boot = SimpleNamespace(current_boot=lambda now: case.boot_id + 1)
    owner._rpc = NativeJobRpc()
    try:
        _save_decision(case)
        owner._control_failure_poll(1)
        _drain_failure(owner, case)
        command = case.store.get_command(case.command["commandUid"])
        assert command["state"] == "FAILED"
        assert command["last_error"] == "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
        assert case.store.get_work_slot() is None
        assert case.store.get_bag_baseline(case.bag_uid) is None
        assert case.store.get_clean_restart_interlock_metadata(1) == case.interlock
        assert case.store.get_state("native_blocking_fault") in {None, ""}
    finally:
        owner._rpc.close()
        case.store.close()


def test_atomic_baseline_intent_survives_commit_boundary_and_retires_without_send(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    _, safety = make_real_job_safety(tmp_path)
    command = valid_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    command_uid = str(uuid.uuid4())
    measurement_uid = str(uuid.uuid4())
    command.update(commandUid=command_uid, targetDeviceName="device-1")
    command["target"]["uid"] = measurement_uid
    command["payload"].update(
        measurementUid=measurement_uid,
        bagUid=str(uuid.uuid4()),
        portNo=1,
        emptyBagConfirmed=True,
        measurementTimeoutMs=5000,
    )
    command["payload"]["config"] = {
        "version": 7,
        "contentSha256": "a" * 64,
        "mcuPayloadSha256": "b" * 64,
    }
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    assert store.receive_command(
        command_uid,
        command["commandType"],
        command,
    ) == "ACCEPTED"
    assert store.claim_next_command()["command_uid"] == command_uid
    query_id = store.reserve_native_query_id()
    boot_id = store.reserve_native_boot_id(query_id)
    assert store.recognize_native_boot_id(boot_id)
    native_uid = str(uuid.uuid4())
    permit = JobPermit(
        permit_uid=command_uid,
        command_uid=command_uid,
        work_uid=measurement_uid,
        work_type="BASELINE",
        request_digest_sha256=command_request_digest(command),
    )

    # This method return is the only commit boundary.  A process death here
    # must leave the cloud command, native bytes and work slot as one exact
    # recoverable unit; the volatile permission to send has not been created.
    record = store.prepare_native_baseline_work(
        command,
        permit,
        native_uid,
        boot_id,
        runtime_instance_uid=str(uuid.uuid4()),
    )
    assert record["write_claimed"] == 0
    assert store.get_command(command_uid)["state"] == "WAITING_MCU_RESULT"
    assert store.get_work_slot()["context"]["start_mcu_command_uid"] == native_uid

    store.close()
    store.initialize()
    sent = []
    restarted = NativeBusinessRuntime(
        store,
        safety,
        device_name="device-1",
        connected=lambda: True,
        communication_timeout_ms=1000,
    )
    restarted.boot = SimpleNamespace(current_boot=lambda now: boot_id)
    restarted.transport = SimpleNamespace(
        write=lambda frame: sent.append(frame) or len(frame)
    )
    restarted.dispatcher = SimpleNamespace(poll=lambda uid, now: None)
    restarted._rpc = NativeJobRpc()
    case = SimpleNamespace(store=store)
    try:
        restarted._control_failure_poll(0)
        _drain_failure(restarted, case)
        failed = store.get_command(command_uid)
        assert failed["state"] == "FAILED"
        assert failed["last_error"] == "EDGE_RESTARTED_BEFORE_START"
        assert store.get_work_slot() is None
        old = store.get_native_command(native_uid)
        assert old["write_claimed"] == 0
        assert old["decision_outcome"] is None
        assert old["dispatch_retired"] == 1
        assert sent == []

        # Retirement is a local dispatch fence, not a fabricated MCU reply,
        # and it must release the one-pending-command gate for later work.
        store.close()
        store.initialize()
        assert store.get_native_command(native_uid)["dispatch_retired"] == 1
        successor = store.prepare_native_command(
            "MEASURE_BASELINE",
            str(uuid.uuid4()),
            boot_id,
            {
                "measurementUid": str(uuid.uuid4()),
                "portNo": 1,
                "configVersion": 7,
                "configContentSha256": "a" * 64,
                "startExecutionWindowMs": 5000,
                "measurementTimeoutMs": 5000,
            },
        )
        assert successor["command_sequence"] == record["command_sequence"] + 1
        assert successor["write_claimed"] == 0
    finally:
        restarted._rpc.close()
        store.close()
