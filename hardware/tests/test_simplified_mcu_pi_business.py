"""Autonomous production C -> actual Pi SQLite handoff -> unique business report.

Only the physical GPIO/scale and cloud-command origin are host fixtures. Every
WORK_RESULT comes from the compiled MCU execution/result builder, never a
simulated result packet. This is a component vertical test, not a claim that
the deployed Pi main loop or the physical RS485 wiring has been verified.
"""
from contextlib import contextmanager
import ctypes as c
from dataclasses import asdict
import json
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from edge_store import EdgeStore
from mcu_configuration import NativeMcuConfiguration
from mcu_result_handoff import McuResultHandoff
from mcu_work_query import McuWorkQuery
from native_result_report import NativeResultReporter
import uart2_protocol as uart
from hardware.tests.test_command_processor import make_real_job_safety
from hardware.tests.test_job_safety import _command
from hardware.tests.test_mcu_simplified_execution import library, runtime, tick, select, request
from hardware.tests.test_mcu_work_preparation import start_values, take_samples, original_scope
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_native_result_report import original_command, validate_event


IDENTITY = {"mcuCommandUid", "targetMcuBootId", "commandSequence", "commandDigestSha256"}


class RealCWire:
    """Actual C frame parser and encoder, with every Pi write observable."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.sent = []
        self.now = 0
        self.before_saved = None

    def write(self, frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        self.sent.append(decoded)
        if decoded["messageName"] == "RESULT_SAVED" and self.before_saved:
            self.before_saved(decoded["payload"])
        lib, endpoint, *_ = self.runtime
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), self.now) == 1
        return len(frame)

    def take(self):
        replies = self.runtime[3]
        copied = list(replies)
        replies.clear()
        return copied

    def exchange(self, name, value=None, *, payload=None):
        assert not self.runtime[3], "previous MCU response must be consumed or deliberately dropped"
        raw = payload if payload is not None else uart.encode_payload(name, value)
        self.write(uart.encode_frame(name, len(self.sent) + 1, raw))
        return [uart.decode_frame(frame, sender_role="MCU") for frame in self.take()]

    def deliver(self, receiver, now):
        # Receiving a full result synchronously sends SAVED and appends its C
        # reply. Consume that too without treating it as a fresh query result.
        accepted = []
        while self.runtime[3]:
            frame = self.runtime[3].pop(0)
            accepted.append((uart.decode_frame(frame)["messageName"], receiver.accept_frame(frame, now)))
        return accepted


@contextmanager
def real_work(runtime, tmp_path, clean):
    lib, endpoint, preparation, *_ = runtime
    delivery, cleanup = (c.c_uint64 * 64)(), (c.c_uint64 * 64)()
    runtime[4]["owners"] = (delivery, cleanup)
    assert lib.McuDeliveryExecution_Attach(delivery, preparation, endpoint)
    assert lib.McuCleanExecution_Attach(cleanup, preparation, endpoint)
    assert lib.TestSimple_EnableApply(preparation, endpoint)
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    updater, safety = make_real_job_safety(tmp_path)
    wire = RealCWire(runtime)
    try:
        probe = store.reserve_native_query_id()
        boot = store.reserve_native_boot_id(probe)
        wire.exchange("BOOT_PROBE", dict(probeId=probe))
        bound = wire.exchange("BIND_BOOT", dict(probeId=probe, proposedMcuBootId=boot))[0]
        assert store.save_native_boot_observation(bound["messageName"], bound["payload"])
        config = NativeMcuConfiguration(**inputs())
        application_uid = str(uuid.uuid4())
        for index in range(1, config.part_count + 1):
            name, raw = config.encode_part(index, application_uid=application_uid,
                mcu_command_uid=str(uuid.uuid4()), target_mcu_boot_id=boot, command_sequence=index)
            value = uart.decode_payload(name, raw)
            record = store.prepare_native_command(name, value["mcuCommandUid"], boot,
                {key: field for key, field in value.items() if key not in IDENTITY})
            assert store.claim_native_command_write(record["command_uid"])
            response = wire.exchange(name, payload=record["payload"])[0]
            assert uart.decode_payload(response["messageName"], response["payload"])["outcome"] == "ACCEPTED"
            assert store.save_native_command_observation(response["messageName"], response["payload"])

        name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
        key = "operationUid" if clean else "sessionUid"
        start = start_values(name, targetMcuBootId=boot, mcuCommandUid=str(uuid.uuid4()), **{key: str(uuid.uuid4())})
        cloud = _command() | dict(commandType=name, payload={key: start[key], "portNo": 1})
        cloud = original_command(cloud, start, inputs())
        assert store.receive_command(cloud["commandUid"], name, cloud) == "ACCEPTED"
        assert store.claim_next_command()
        permit = safety.request_job(cloud, work_type="CLEAN" if clean else "DELIVERY", work_uid=start[key])
        safety.begin_job(permit, begin_uid=permit.work_uid, digest=permit.request_digest_sha256)
        assert store.acquire_work_slot(permit.work_type, permit.work_uid, 1, {
            "phase": "NATIVE_RUNNING",
            "job_safety": asdict(permit) | {"begin_uid": permit.work_uid},
        })
        record = store.prepare_native_command(name, start["mcuCommandUid"], boot,
            {key: value for key, value in start.items() if key not in IDENTITY})
        assert store.claim_native_command_write(record["command_uid"])
        start = uart.decode_payload(name, record["payload"])
        response = wire.exchange(name, payload=record["payload"])[0]
        assert uart.decode_payload(response["messageName"], response["payload"])["outcome"] == "ACCEPTED"
        # Deliberate transport loss: do NOT save the C START decision to Pi.
        assert store.list_native_command_observations(record["command_uid"]) == []
        now = take_samples(runtime, [500] * 5)
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        wire.now = now
        yield SimpleNamespace(store=store, safety=safety, permit=permit, start=start, wire=wire,
            delivery=delivery, cleanup=cleanup, boot=boot, path=tmp_path / "edge.db")
    finally:
        store.close()
        updater.close()


def restored_work_query(case, clean, pi_now):
    # Rebuild identity only from the reliable old START, not the MCU's current
    # user/slot or a newly constructed mechanical command.
    retained = case.store.get_native_command(case.start["mcuCommandUid"])
    start = uart.decode_payload(retained["message_name"], retained["payload"])
    identity = original_scope(start, clean=clean)
    del identity["queryId"]
    query = McuWorkQuery(case.store, case.wire.write, identity)
    assert query.poll(pi_now)
    assert case.wire.deliver(query, pi_now) == [("WORK_QUERY_REPLY", True)]
    return query.observation(pi_now)


def finish_delivery_round(runtime, case, now, measurement_id, grams):
    now = tick(runtime, now, 100)
    now = tick(runtime, now, case.start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    return take_samples(runtime, [grams] * 5, start=now, measurement=measurement_id)


def no_process_evidence(case):
    assert not case.store.list_native_work_actuator_events(case.permit.work_uid)
    assert case.store.list_native_command_observations(case.start["mcuCommandUid"]) == []
    # The production query/ACK/store APIs must not create any diagnostic rows
    # as an implicit substitute for the absent Pi process traffic.
    for table in ("native_process_receipt", "native_actuator_event"):
        assert case.store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("business", ["delivery", "continuous_delivery", "clean"])
def test_autonomous_c_result_survives_pi_restart_and_reports_once_without_process_proofs(runtime, tmp_path, business):
    clean = business == "clean"
    with real_work(runtime, tmp_path, clean) as case:
        initial_slot = case.store.get_work_slot()
        initial_native_commands = case.store.list_native_commands()
        # Pi restarts while the autonomous MCU is still doing the original work.
        case.store.close()
        case.store.initialize()
        before_resume = len(case.wire.sent)
        observation = restored_work_query(case, clean, 0)
        assert observation["status"] == "RUNNING"
        assert [frame["messageName"] for frame in case.wire.sent[before_resume:]] == ["QUERY_WORK"]
        now = case.wire.now
        if clean:
            now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
            assert request(runtime, case.cleanup, case.start, now, "CLEAN_UNLOCK_REQUESTED")
            now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
            assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
            now = take_samples(runtime, [100] * 5, start=now, measurement=2)
        else:
            now = finish_delivery_round(runtime, case, now, 2, 700)
            if business == "continuous_delivery":
                assert select(runtime, case.delivery, now, "CONTINUE")
                now = finish_delivery_round(runtime, case, now, 3, 900)
            assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        observation = restored_work_query(case, clean, 1000)
        assert observation["status"] == "RESULT_HELD"
        identity = dict(mcuBootId=case.boot, resultSequence=observation["resultSequence"],
            resultDigestSha256=observation["resultDigestSha256"], workUid=case.permit.work_uid)
        no_process_evidence(case)

        committed = []
        def independently_committed(saved_identity):
            with sqlite3.connect(str(case.path)) as other:
                rows = other.execute("SELECT payload FROM native_mcu_result").fetchall()
                assert len(rows) == 1 and rows[0][0][:60] == saved_identity
                assert other.execute("SELECT COUNT(*) FROM native_result_report_outbox").fetchone()[0] == 1
                committed.append(rows[0][0])
        case.wire.before_saved = independently_committed
        handoff = McuResultHandoff(case.store, case.wire.write, identity)
        assert handoff.poll(1000)
        received = case.wire.deliver(handoff, 1000)
        assert received == [("RESULT_QUERY_REPLY", True), ("WORK_RESULT", True), ("RESULT_SAVED_REPLY", False)]
        assert committed and handoff.saved_receipt["savedPayload"] == committed[0][:60]
        # These are the bytes C built and sent, not a Python result constructor.
        result = uart.decode_payload("WORK_RESULT", committed[0])
        assert result["deliveryRoundCount"] == (0 if clean else 2 if business == "continuous_delivery" else 1)
        assert result["initialWeightGrams"] == 500
        assert result["finalWeightGrams"] == (100 if clean else 900 if business == "continuous_delivery" else 700)
        assert result["finishReason"] == ("CLEAN_CONFIRMED" if clean else "DELIVERY_END")

        # Restart after durable save/MCU ACK but before report preparation.
        case.store.close()
        case.store.initialize()
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        report = reporter.prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] == "REPORT_CREATED"
        rows = case.store.list_pending_events()
        assert len(rows) == 1
        event = json.loads(rows[0]["payload_json"])
        validate_event(event)
        payload = event["payload"]
        assert event["commandUid"] == case.permit.command_uid
        assert payload["operationUid" if clean else "sessionUid"] == case.permit.work_uid
        assert payload["removedNetWeightGrams" if clean else "deliveryNetWeightGrams"] == (
            400 if clean or business == "continuous_delivery" else 200)
        if clean:
            assert payload["newBaselineWeightGrams"] == 100
            assert payload["oldBagUid"] != payload["newBagUid"]
            assert payload["cleanLockAndManualDoorConfirmation"]["solenoidHealth"] == "UNKNOWN"
        else:
            assert payload["finalDoorCommand"] == dict(command="CLOSE", outputStatus="COMMAND_DISPATCHED",
                physicalStateBasis="NOT_OBSERVABLE")
        assert reporter.prepare(case.permit, case.start["mcuCommandUid"]) == report

        # Yet another Pi restart and duplicate original result cannot create a
        # second business event or a new mechanical START intention.
        case.store.close()
        case.store.initialize()
        case.store.save_native_mcu_result(committed[0])
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        assert reporter.prepare(case.permit, case.start["mcuCommandUid"]) == report
        assert case.store.list_pending_events() == rows
        assert len(case.store.list_native_result_report_tasks()) == 1
        assert case.store.get_work_slot() == initial_slot  # Reporting alone does not grant admission.
        assert case.store.list_native_commands() == initial_native_commands
        no_process_evidence(case)
        names = [frame["messageName"] for frame in case.wire.sent]
        assert names.count("START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION") == 1
        assert set(names[before_resume:]) <= {"QUERY_WORK", "QUERY_RESULT", "RESULT_SAVED"}
