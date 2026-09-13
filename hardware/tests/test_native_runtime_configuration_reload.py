"""A real rebooted MCU reloads standing configuration without a new cloud job.

This module compiles the same autonomous executor as the normal entry tests,
additionally exporting its existing idle-scale APIs. The health sample goes
through the real decoder/observation publisher, never a fabricated facts reply.
"""
import ctypes as c
import json
from pathlib import Path
import subprocess

import pytest

from job_safety import JobPermit, command_request_digest
from hardware.tests.test_mcu_work_preparation import SINK, GUARD, runtime, take_samples
from hardware.tests.test_mcu_simplified_execution import select, tick
from hardware.tests.test_mcu_config_collection import WeightPolicy
from hardware.tests.test_mcu_device_facts import ScaleObservation, scale_frame
from hardware.tests.test_native_business_runtime import (
    completed_first_work, apply_configuration, poll_until, start_command, open_owner, await_start_facts,
)
from hardware.tests.test_native_business_completion import put_baseline
from hardware.tests.test_native_delivery_issue_confirmation import issue_confirmation_wire
from hardware.tests.test_mcu_work_preparation import original_scope
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_simplified_mcu_pi_business import finish_delivery_round
import uart2_protocol as uart


ROOT = Path(__file__).resolve().parents[2]
IDENTITY = {"mcuCommandUid", "targetMcuBootId", "commandSequence", "commandDigestSha256"}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("cached Clang required")
    user = ROOT / "hardware_mcu/USER"
    target = tmp_path_factory.mktemp("configuration-reload-executor") / "execution.dll"
    signatures = {
        "McuControlEndpoint_Init": (None, [c.c_void_p, c.c_uint8, SINK, c.c_void_p]),
        "McuControlEndpoint_Feed": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64]),
        "McuWorkPreparation_Attach": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint8, GUARD, c.c_void_p]),
        "McuWorkPreparation_Poll": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint64]),
        "McuDeliveryExecution_Attach": (c.c_uint8, [c.c_void_p] * 3),
        "McuCleanExecution_Attach": (c.c_uint8, [c.c_void_p] * 3),
        "McuDeliveryExecution_Select": (c.c_uint8, [c.c_void_p] * 3 + [c.c_uint8, c.c_uint64]),
        "TestFacts_InitHardware": (None, []),
        "ActuatorRuntime_Tick": (None, []),
        "ActuatorRuntime_SetDoorTarget": (c.c_uint8, [c.c_uint8]),
        "RuntimeClock_Advance": (None, [c.c_uint32]),
        "TestPreparation_Weight": (c.c_void_p, [c.c_void_p]),
        "TestPreparation_Facts": (c.c_void_p, [c.c_void_p]),
        "TestSimple_DeliveryMeasurement": (c.c_void_p, [c.c_void_p]),
        "TestSimple_EnableApply": (c.c_uint8, [c.c_void_p, c.c_void_p]),
        "McuWeightRun_StartOwnedAttempt": (c.c_uint32, [c.c_void_p, c.c_uint64]),
        "McuWeightRun_FinishOwnedAttempt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint32,
            c.c_uint64, c.c_uint64, c.c_void_p, c.c_size_t]),
        "McuConfiguration_ReadWeightPolicy": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_void_p]),
        "McuWeightRun_StartIdleAttempt": (c.c_uint32, [c.c_void_p, c.c_void_p, c.c_uint64]),
        "McuWeightRun_FinishIdleAttempt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint64, c.c_uint64, c.c_void_p, c.c_size_t]),
        "McuWeightRun_CopyObservation": (c.c_uint8, [c.c_void_p, c.c_void_p]),
        "McuDeviceFacts_PublishScaleObservation": (c.c_uint8, [c.c_void_p, c.c_void_p]),
    }
    sources = ("mcu_control_endpoint", "mcu_actuator_event_journal", "mcu_work_preparation", "mcu_opening_gate",
        "mcu_delivery_execution", "mcu_clean_execution", "mcu_configuration", "mcu_config_collection",
        "mcu_session", "mcu_work_state", "mcu_result_slot", "mcu_result_builder", "mcu_process_measurement",
        "mcu_process_event_slot", "mcu_device_facts", "mcu_weight_run", "weight_measurement", "scale_reader",
        "actuator_runtime", "door_control", "clean_lock", "runtime_clock", "mcu_fullness_run",
        "ultrasonic_reader", "mcu_environment_ultrasonic")
    result = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-I", str(user), "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1",
        *[str(user / f"{name}.c") for name in sources],
        str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        *[str(ROOT / f"hardware_mcu/tests/{name}.c") for name in
            ("device_facts_host", "work_preparation_host", "ultrasonic_host", "simplified_execution_host")],
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(target)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    lib = c.CDLL(str(target))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    return lib


def reboot_mcu(runtime, case, owner):
    old_boot = owner.boot.current_boot(case.clock.now)
    lib, endpoint, preparation, replies, _, sink, guard = runtime
    lib.TestFacts_InitHardware()
    lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
    assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
    assert lib.McuDeliveryExecution_Attach(case.delivery, preparation, endpoint)
    assert lib.McuCleanExecution_Attach(case.cleanup, preparation, endpoint)
    assert lib.TestSimple_EnableApply(preparation, endpoint)
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    case.wire.now = 0
    replies.clear()
    case.serial.rx.clear()
    poll_until(owner, case.clock, lambda: owner.boot.current_boot(case.clock.now) not in {None, old_boot})
    return owner.boot.current_boot(case.clock.now)


def applied_facts(case, owner, configuration):
    facts = owner.facts_query.observation(case.clock.now) if owner.facts_query else None
    expected = configuration["payload"]["config"]
    return bool(facts and facts["currentMcuBootId"] == owner.boot.current_boot(case.clock.now)
        and not facts["configStaging"] and facts["appliedConfigVersion"] == expected["version"]
        and facts["appliedContentSha256"] == expected["contentSha256"]
        and facts["appliedMcuPayloadSha256"] == expected["mcuPayloadSha256"])


def actual_idle_weight(runtime, case, grams=700):
    lib, endpoint, preparation, *_ = runtime
    policy = WeightPolicy()
    # McuWorkPreparation's first member is the real McuConfiguration.
    assert lib.McuConfiguration_ReadWeightPolicy(preparation, 1, c.byref(policy))
    reader = lib.TestPreparation_Weight(preparation)
    attempt = lib.McuWeightRun_StartIdleAttempt(reader, c.byref(policy), case.wire.now)
    assert attempt
    raw = scale_frame(grams)
    case.wire.now += 20
    lib.RuntimeClock_Advance(20)
    assert lib.McuWeightRun_FinishIdleAttempt(reader, attempt, case.wire.now, case.wire.now, raw, len(raw))
    observation = ScaleObservation()
    assert lib.McuWeightRun_CopyObservation(reader, c.byref(observation))
    assert observation.grams == grams and observation.captured == case.wire.now
    assert lib.McuDeviceFacts_PublishScaleObservation(lib.TestPreparation_Facts(endpoint), c.byref(observation))


def assert_original_cloud_configuration_unchanged(case, command, configuration, original_command, events):
    assert case.store.get_configuration(command["payload"]["applicationUid"]) == configuration
    current = case.store.get_command(command["commandUid"])
    for key, value in original_command.items():
        if key not in {"result", "result_json"}:
            assert current[key] == value
    for key, value in (original_command["result"] or {}).items():
        assert current["result"][key] == value
    assert case.store.list_pending_events() == events


def close_owner(owner):
    owner.close()
    owner._rpc._thread.join(timeout=2)
    assert not owner._rpc._thread.is_alive()


def reload_parts(case, boot_id):
    return [row for row in case.store.list_native_commands()
        if row["mcu_boot_id"] == boot_id and row["message_name"].startswith("CONFIG_")]


def start_and_measure_delivery(runtime, case, owner, measurement):
    business = start_command()
    assert case.store.receive_command(business["commandUid"], business["commandType"], business) == "ACCEPTED"
    assert case.store.claim_next_command()["command_uid"] == business["commandUid"]
    pending = owner.start_delivery_command(business)
    uid = pending["mcu_command_uid"]
    poll_until(owner, case.clock, lambda: case.store.get_native_command(uid)["decision_outcome"] is not None)
    assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
    case.start = uart.decode_payload("START_DELIVERY_SESSION", case.store.get_native_command(uid)["payload"])
    case.permit = JobPermit(business["commandUid"], business["payload"]["sessionUid"],
        business["commandUid"], "DELIVERY", command_request_digest(business))
    now = tick(runtime, case.wire.now, 250)
    now = take_samples(runtime, [700] * 5, start=now, measurement=measurement)
    runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
    case.wire.now = now
    return business, uid


def held_result_frame(case):
    case.wire.take()  # Old read-query replies may be lost; no result has been saved.
    reply = case.wire.exchange("QUERY_WORK", original_scope(case.start))[0]
    work = uart.decode_payload(reply["messageName"], reply["payload"])
    assert work["status"] == "RESULT_HELD"
    query_id = case.store.reserve_native_query_id()
    query = dict(queryId=query_id, mcuBootId=case.start["targetMcuBootId"], resultSequence=work["resultSequence"],
        resultDigestSha256=work["resultDigestSha256"], workUid=case.permit.work_uid)
    case.wire.write(uart.encode_frame("QUERY_RESULT", query_id, uart.encode_payload("QUERY_RESULT", query)))
    return next(frame for frame in case.wire.take() if uart.decode_frame(frame)["messageName"] == "WORK_RESULT")


def test_reboot_reloads_same_standing_configuration_and_next_actual_delivery_succeeds(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = apply_configuration(case, owner)
        configuration = case.store.get_configuration(command["payload"]["applicationUid"])
        original_command = case.store.get_command(command["commandUid"])
        events = case.store.list_pending_events()
        new_boot = reboot_mcu(runtime, case, owner)
        poll_until(owner, case.clock, lambda: applied_facts(case, owner, configuration))
        reloaded = [row for row in case.store.list_native_commands()
            if row["mcu_boot_id"] == new_boot and row["message_name"].startswith("CONFIG_")]
        assert len(reloaded) == len(configuration["part_command_uids"])
        assert not {row["command_uid"] for row in reloaded} & set(configuration["part_command_uids"])
        for old_uid, new in zip(configuration["part_command_uids"], reloaded):
            old = case.store.get_native_command(old_uid)
            assert old["message_name"] == new["message_name"]
            old_values = uart.decode_payload(old["message_name"], old["payload"])
            new_values = uart.decode_payload(new["message_name"], new["payload"])
            assert {k: v for k, v in old_values.items() if k not in IDENTITY} == {
                k: v for k, v in new_values.items() if k not in IDENTITY}
            assert new["decision_outcome"] == "ACCEPTED"
        assert_original_cloud_configuration_unchanged(case, command, configuration, original_command, events)
        actual_idle_weight(runtime, case)
        poll_until(owner, case.clock, lambda: owner.facts_query.latest_weight(case.clock.now) == 700)
        business = start_command()
        assert case.store.receive_command(business["commandUid"], business["commandType"], business) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == business["commandUid"]
        pending = owner.start_delivery_command(business)
        uid = pending["mcu_command_uid"]
        poll_until(owner, case.clock, lambda: case.store.get_native_command(uid)["decision_outcome"] is not None)
        assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
        case.start = uart.decode_payload("START_DELIVERY_SESSION", case.store.get_native_command(uid)["payload"])
        case.permit = JobPermit(business["commandUid"], business["payload"]["sessionUid"],
            business["commandUid"], "DELIVERY", command_request_digest(business))
        now = tick(runtime, case.wire.now, 250)
        now = take_samples(runtime, [700] * 5, start=now, measurement=1)
        runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
        now = finish_delivery_round(runtime, case, now, 2, 1000)
        assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        poll_until(owner, case.clock, lambda: case.store.get_native_result_report(case.permit, uid, device_name="device-1") is not None)
        report = case.store.get_native_result_report(case.permit, uid, device_name="device-1")
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["payload"]["deliveryNetWeightGrams"] == 300
        _, _, confirmation = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "ACCEPTED"
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
        starts = [row for row in case.wire.sent if row["messageName"] == "START_DELIVERY_SESSION"]
        assert len(starts) == 2 and starts[0]["payload"] != starts[1]["payload"]


def test_pi_restart_mid_reload_queries_claimed_original_part_without_new_ids_or_resend(runtime, tmp_path):
    from native_configuration_reload import read
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = apply_configuration(case, owner)
        configuration = case.store.get_configuration(command["payload"]["applicationUid"])
        original_command = case.store.get_command(command["commandUid"])
        events = case.store.list_pending_events()
        new_boot = reboot_mcu(runtime, case, owner)
        poll_until(owner, case.clock, lambda: any(row["write_claimed"] for row in reload_parts(case, new_boot)))
        first = reload_parts(case, new_boot)[0]
        assert first["message_name"] == "CONFIG_BEGIN" and first["decision_outcome"] is None
        prepared = read(case.store, configuration["application_uid"], target_mcu_boot_id=new_boot, device_name="device-1")
        assert prepared["state"] == "PREPARED"
        case.wire.take()  # The real MCU accepted; its reply did not reach SQLite.
        close_owner(owner)
        case.store.close()
        case.store.initialize()
        restarted = open_owner(case, case.clock)
        try:
            poll_until(restarted, case.clock, lambda: applied_facts(case, restarted, configuration))
            after = read(case.store, configuration["application_uid"], target_mcu_boot_id=new_boot, device_name="device-1")
            assert after == prepared | {"state": "APPLIED"}
            current = case.store.get_native_command(first["command_uid"])
            for key in ("command_uid", "mcu_boot_id", "command_sequence", "payload"):
                assert current[key] == first[key]
            assert current["decision_outcome"] == "ACCEPTED"
            assert sum(frame["messageName"] == "CONFIG_BEGIN" and frame["payload"] == first["payload"]
                for frame in case.wire.sent) == 1
            assert any(frame["messageName"] == "QUERY_COMMAND" and uart.decode_payload("QUERY_COMMAND", frame["payload"])["mcuCommandUid"]
                == first["command_uid"] for frame in case.wire.sent)
            assert_original_cloud_configuration_unchanged(case, command, configuration, original_command, events)
        finally:
            close_owner(restarted)


def test_issue_cancel_reload_and_next_result_handoff_keep_late_old_result_separate(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        config_command = apply_configuration(case, owner)
        configuration = case.store.get_configuration(config_command["payload"]["applicationUid"])
        await_start_facts(case, owner)
        _, old_uid = start_and_measure_delivery(runtime, case, owner, 3)
        now = finish_delivery_round(runtime, case, case.wire.now, 4, 900)
        assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        old_frame = held_result_frame(case)
        old_raw = uart.decode_frame(old_frame)["payload"]
        old_permit = case.permit
        # MCU had completed, but Pi has not received/saved that final packet.
        assert case.store.get_native_result_report(old_permit, old_uid, device_name="device-1") is None
        new_boot = reboot_mcu(runtime, case, owner)
        poll_until(owner, case.clock, lambda: case.store.get_native_delivery_issue(old_permit.work_uid) is not None)
        issue = case.store.get_native_delivery_issue(old_permit.work_uid)
        poll_until(owner, case.clock, lambda: case.store.get_event(issue["issueUid"]) is not None)
        assert not reload_parts(case, new_boot)  # The original issue still owns the device.
        _, _, issue_confirmation = issue_confirmation_wire(case, issue["issueUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(
            command=issue_confirmation, device_name="device-1") == "ACCEPTED"
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
        assert case.safety.get_job_permit(old_permit.permit_uid)["completionOutcome"] == "CANCELLED"
        poll_until(owner, case.clock, lambda: applied_facts(case, owner, configuration))
        actual_idle_weight(runtime, case)
        poll_until(owner, case.clock, lambda: owner.facts_query.latest_weight(case.clock.now) == 700)
        new_command, new_uid = start_and_measure_delivery(runtime, case, owner, 1)
        new_permit = case.permit
        baseline = put_baseline(case.store, new_command["payload"]["bagUid"], source_work_uid=new_permit.work_uid, grams=83)
        now = finish_delivery_round(runtime, case, case.wire.now, 2, 1100)
        assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        # Drop only the NEW result's transmission until its actual QUERY_WORK
        # reply has created a real handoff. The C slot continues retaining it.
        case.serial.drop = lambda reply: reply["messageName"] == "WORK_RESULT" and uart.decode_payload(
            "WORK_RESULT", reply["payload"])["workUid"] == new_permit.work_uid
        poll_until(owner, case.clock, lambda: owner._handoff is not None and owner._query_start_uid == new_uid)
        assert owner._handoff.saved_receipt is None
        assert owner._handoff._key["workUid"] == new_permit.work_uid
        slot = case.store.get_work_slot()
        case.serial.rx.extend(old_frame)
        def old_evidence_ready():
            return any(row["evidence_kind"] == "FINAL_RESULT"
                for row in case.store.list_native_delivery_issue_reports(old_permit.work_uid))
        poll_until(owner, case.clock, old_evidence_ready)
        assert owner._handoff.saved_receipt is None  # The old packet never satisfies the new handoff.
        assert case.store.get_work_slot() == slot
        assert case.store.get_bag_baseline(baseline["bag_uid"]) == baseline
        late = case.store.list_native_delivery_issue_results(issue["issueUid"])
        assert len(late) == 1 and late[0]["payload"] == old_raw
        assert case.store.get_native_result_report(old_permit, old_uid, device_name="device-1") is None
        assert case.store.get_native_result_report(new_permit, new_uid, device_name="device-1") is None
        case.serial.drop = lambda reply: False
        poll_until(owner, case.clock, lambda: case.store.get_native_result_report(new_permit, new_uid, device_name="device-1") is not None)
        report = case.store.get_native_result_report(new_permit, new_uid, device_name="device-1")
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["payload"]["sessionUid"] == new_permit.work_uid
        assert event["payload"]["deliveryNetWeightGrams"] == 400
        _, _, confirmation = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "ACCEPTED"
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
        assert case.safety.get_job_permit(new_permit.permit_uid)["completionOutcome"] == "SUCCEEDED"
        assert case.safety.get_job_permit(old_permit.permit_uid)["completionOutcome"] == "CANCELLED"
        assert case.store.get_native_delivery_issue(old_permit.work_uid) == issue
        assert case.store.get_bag_baseline(baseline["bag_uid"]) == baseline
        starts = [frame for frame in case.wire.sent if frame["messageName"] == "START_DELIVERY_SESSION"]
        assert len(starts) == 3 and len({frame["payload"] for frame in starts}) == 3
