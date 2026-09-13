"""Actual native foreground polling against the compiled autonomous MCU.

Only the serial endpoint, monotonic clock and hardware inputs are test doubles.
Protocol parsing, MCU execution, Pi persistence/reporting and permanent job
completion are the production implementations. No real device is opened.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from native_business_runtime import NativeBusinessRuntime
from job_safety import JobPermit, JobSafetyError, PermanentJobSafety, command_request_digest
from onenet_wire import canonical_payload_sha256
from photo_manager import PhotoManager, CLEAN_OPEN_SLOTS, DELIVERY_OPEN_SLOTS
from hardware.tests.test_command_processor import StoreBackedUpdaterClient
from hardware.tests.test_mcu_simplified_execution import library, runtime, select, tick, request
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_mcu_work_preparation import start_values, original_scope
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_job_safety import _command
from hardware.tests.test_native_result_report import original_command
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_simplified_mcu_pi_business import real_work, finish_delivery_round
import uart2_protocol as uart


class CSerial:
    def __init__(self, wire):
        self.wire = wire
        self.timeout, self.write_timeout, self.is_open = 0, 1, True
        self.rx = bytearray()
        self.drop = lambda decoded: False
        self.dropped = []

    @property
    def in_waiting(self):
        for reply in self.wire.take():
            decoded = uart.decode_frame(reply, sender_role="MCU")
            if self.drop(decoded):
                self.dropped.append(decoded)
                continue
            self.rx.extend(reply)
        return len(self.rx)

    def read(self, size):
        value = bytes(self.rx[:size])
        del self.rx[:size]
        return value

    def write(self, frame):
        assert self.is_open
        return self.wire.write(frame)

    def close(self):
        self.is_open = False


class RuntimeUpdaterClient(StoreBackedUpdaterClient):
    def request(self, action, payload):
        if action == "GET_STATUS":
            return self.store.get_status()
        return super().request(action, payload)


class AdvancingClock:
    def __init__(self):
        self.now = 0

    def read(self):
        self.now += 1  # Real time can advance inside a single foreground poll.
        return self.now


def open_owner(case, clock):
    # Extend the fixture's socket replacement with real maintenance status;
    # do not bypass the production runtime's maintenance-ownership check.
    case.safety = PermanentJobSafety(RuntimeUpdaterClient(case.safety._client.store))
    owner = NativeBusinessRuntime(case.store, case.safety, device_name="device-1",
        photo_manager=getattr(case, "photos", None), connected=lambda: True,
        clock=getattr(clock, "read", lambda: clock.now))
    serial = CSerial(case.wire)
    case.serial = serial
    owner.open(port="host-test-no-device", port_factory=lambda **options: serial)
    return owner


def poll_until(owner, clock, predicate, *, count=300):
    for _ in range(count):
        owner.poll()
        if predicate():
            return
        clock.now += 10
        time.sleep(0.001)  # Schedule the actual single RPC worker; never fabricate its reply.
    raise AssertionError("native runtime did not reach the expected public state: " + repr(dict(
        now=clock.now, uart=owner.uart_state, slot=owner.store.get_work_slot(),
        facts=owner.facts_query.observation(clock.now) if owner.facts_query else None)))


def retained_context(case):
    assert case.store.update_work_context(case.permit.work_uid, dict(native_protocol=2, phase="NATIVE_RUNNING",
        start_command_uid=case.permit.command_uid, start_mcu_command_uid=case.start["mcuCommandUid"],
        job_safety=asdict(case.permit) | {"begin_uid": case.permit.work_uid}))


@contextmanager
def completed_first_work(runtime, tmp_path, *, clock=None, drop_start_replies=False):
    with real_work(runtime, tmp_path, False) as case:
        case.clean = False
        retained_context(case)
        case.clock = clock or SimpleNamespace(now=0)
        owner = open_owner(case, case.clock)
        if drop_start_replies:
            case.serial.drop = lambda decoded: (decoded["messageName"] in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}
                and uart.decode_payload(decoded["messageName"], decoded["payload"])["mcuCommandUid"] == case.start["mcuCommandUid"])
        try:
            now = finish_delivery_round(runtime, case, case.wire.now, 2, 700)
            assert select(runtime, case.delivery, now, "END")
            case.wire.now = now
            poll_until(owner, case.clock, lambda: case.store.get_native_result_report(case.permit,
                case.start["mcuCommandUid"], device_name="device-1") is not None)
            report = case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1")
            _, _, confirmation = confirmation_wire(case, report["eventUid"])
            assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "ACCEPTED"
            poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
            yield case, owner
        finally:
            owner.close()


def configuration_command():
    path = Path(__file__).resolve().parents[2] / "contracts/examples/onenet/apply-native-configuration.command.json"
    command = json.loads(path.read_text(encoding="utf-8"))
    command.update(commandUid=str(uuid.uuid4()), targetDeviceName="device-1",
        issuedAt="2030-01-01T00:00:00.000Z", expiresAt="2030-01-01T00:01:00.000Z")
    command["payload"]["applicationUid"] = str(uuid.uuid4())
    command["target"]["uid"] = command["payload"]["applicationUid"]
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def apply_configuration(case, owner):
    command = configuration_command()
    assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
    assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
    owner.apply_configuration_command(command)
    poll_until(owner, case.clock, lambda: case.store.get_command(command["commandUid"])["state"] == "COMPLETED")
    return command


def start_command(clean=False):
    name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
    key = "operationUid" if clean else "sessionUid"
    start = start_values(name, **{key: str(uuid.uuid4())})
    command = original_command(_command() | dict(commandUid=str(uuid.uuid4()), commandType=name,
        payload={key: start[key], "portNo": 1}), start, inputs())
    if not clean:
        command["payload"]["unitPriceTenThousandths"] = start["unitPriceTenThousandths"]
        command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def await_start_facts(case, owner):
    def ready():
        facts = owner.facts_query.observation(case.clock.now) if owner.facts_query else None
        return (facts and facts["retainedWorkState"] in {"NONE", "RESULT_RELEASED"} and not facts["configStaging"]
            and owner.facts_query.latest_weight(case.clock.now) is not None)
    poll_until(owner, case.clock, ready)


@pytest.mark.parametrize("business", ["delivery", "continuous_delivery", "clean"])
def test_poll_takes_over_original_work_after_pi_restart_and_finishes_once(runtime, tmp_path, business):
    clean = business == "clean"
    with real_work(runtime, tmp_path, clean) as case:
        case.clean = clean
        retained_context(case)
        case.store.close()
        case.store.initialize()
        clock = SimpleNamespace(now=0)
        owner = open_owner(case, clock)
        try:
            poll_until(owner, clock, lambda: owner.uart_state == "READY")
            assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
            now = case.wire.now
            if clean:
                now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
                assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
                now = take_samples(runtime, [100] * 5, start=now, measurement=2)
            else:
                now = finish_delivery_round(runtime, case, now, 2, 700)
                if business == "continuous_delivery":
                    assert select(runtime, case.delivery, now, "CONTINUE")
                    owner.close()
                    case.store.close()
                    case.store.initialize()
                    owner = open_owner(case, clock)
                    now = finish_delivery_round(runtime, case, now, 3, 900)
                assert select(runtime, case.delivery, now, "END")
            case.wire.now = now
            poll_until(owner, clock, lambda: case.store.get_native_result_report(case.permit,
                case.start["mcuCommandUid"], device_name="device-1") is not None)
            report = case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1")
            assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
            event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
            assert event["payload"]["removedNetWeightGrams" if clean else "deliveryNetWeightGrams"] == (
                400 if business != "delivery" else 200)
            _, _, confirmation = confirmation_wire(case, report["eventUid"])
            assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "ACCEPTED"
            poll_until(owner, clock, lambda: case.store.get_work_slot() is None)
            assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "COMPLETED"
            assert case.store.get_command(case.permit.command_uid)["state"] == "COMPLETED"
            assert len(case.store.list_native_result_report_tasks()) == 1
            names = [frame["messageName"] for frame in case.wire.sent]
            assert names.count("START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION") == 1
            assert not any(name.startswith("AUTHORIZE_") for name in names)
            owner.close()
            case.store.close()
            case.store.initialize()
            owner = open_owner(case, clock)
            poll_until(owner, clock, lambda: owner.uart_state == "READY")
            assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") == report
            assert len(case.store.list_native_result_report_tasks()) == 1
            assert case.store.get_work_slot() is None
        finally:
            owner.close()


def test_configuration_is_pending_until_real_c_accepts_all_original_parts(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        before = len(case.wire.sent)
        owner.apply_configuration_command(command)
        assert len(case.wire.sent) == before
        assert case.store.get_command(command["commandUid"])["state"] == "WAITING_MCU_RESULT"
        assert case.store.get_latest_applied_configuration() is None
        poll_until(owner, case.clock, lambda: case.store.get_command(command["commandUid"])["state"] == "COMPLETED")
        applied = case.store.get_latest_applied_configuration()
        assert applied["application_uid"] == command["payload"]["applicationUid"]
        parts = [frame for frame in case.wire.sent[before:] if frame["messageName"].startswith("CONFIG_")]
        assert len(parts) == len(applied["part_command_uids"])
        for uid in applied["part_command_uids"]:
            assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
            assert case.store.list_native_command_observations(uid)


@pytest.mark.parametrize("business", ["delivery", "continuous_delivery", "clean"])
def test_new_business_returns_only_pending_until_real_c_accepts_and_finishes(runtime, tmp_path, business):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        clean = business == "clean"
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        command = start_command(clean)
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        before = len(case.wire.sent)
        pending = (owner.start_clean_command if clean else owner.start_delivery_command)(command)
        assert pending["native_pending"] is True
        assert len(case.wire.sent) == before
        uid = pending["mcu_command_uid"]
        record = case.store.get_native_command(uid)
        assert not record["write_claimed"] and record["decision_outcome"] is None
        assert case.store.get_command(command["commandUid"])["state"] == "PROCESSING"
        # Actual GRANT/BEGIN replies may need more than one foreground turn.
        # Stop at the write: the real C reply is received by a subsequent poll.
        poll_until(owner, case.clock, lambda: case.store.get_native_command(uid)["write_claimed"])
        assert case.store.get_native_command(uid)["write_claimed"]
        assert case.store.get_native_command(uid)["decision_outcome"] is None
        poll_until(owner, case.clock, lambda: case.store.get_native_command(uid)["decision_outcome"] is not None)
        assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
        case.start = uart.decode_payload(command["commandType"], record["payload"])
        case.clean = clean
        case.permit = JobPermit(permit_uid=command["commandUid"], command_uid=command["commandUid"],
            work_uid=command["payload"]["operationUid" if clean else "sessionUid"], work_type="CLEAN" if clean else "DELIVERY",
            request_digest_sha256=command_request_digest(command))
        now = tick(runtime, case.wire.now, 250)
        now = take_samples(runtime, [700] * 5, start=now, measurement=3)
        runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
        if clean:
            now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
            assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
            now = take_samples(runtime, [100] * 5, start=now, measurement=4)
        else:
            now = finish_delivery_round(runtime, case, now, 4, 1000)
            if business == "continuous_delivery":
                assert select(runtime, case.delivery, now, "CONTINUE")
                now = finish_delivery_round(runtime, case, now, 5, 1200)
            assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        poll_until(owner, case.clock, lambda: case.store.get_native_result_report(case.permit, uid, device_name="device-1") is not None)
        report = case.store.get_native_result_report(case.permit, uid, device_name="device-1")
        _, _, confirmation = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "ACCEPTED"
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["payload"]["removedNetWeightGrams" if clean else "deliveryNetWeightGrams"] == (
            600 if clean else 500 if business == "continuous_delivery" else 300)
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "COMPLETED"
        assert len(case.store.list_native_result_report_tasks()) == 2
        assert [frame["messageName"] for frame in case.wire.sent[before:]].count(command["commandType"]) == 1


def test_pi_restart_explicitly_fails_and_releases_a_start_that_was_only_prepared(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        command = start_command()
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        before = len(case.wire.sent)
        pending = owner.start_delivery_command(command)
        uid = pending["mcu_command_uid"]
        assert not case.store.get_native_command(uid)["write_claimed"]
        owner.close()
        case.store.close()
        case.store.initialize()
        restarted = open_owner(case, case.clock)
        try:
            poll_until(restarted, case.clock, lambda: case.store.get_work_slot() is None)
            assert not case.store.get_native_command(uid)["write_claimed"]
            assert case.store.get_native_command(uid)["decision_outcome"] is None
            assert case.store.list_native_command_observations(uid) == []
            assert "START_DELIVERY_SESSION" not in [frame["messageName"] for frame in case.wire.sent[before:]]
            assert len(case.store.list_native_result_report_tasks()) == 1  # Only the completed first work.
            failed = case.store.get_command(command["commandUid"])
            assert failed["state"] == "FAILED" and failed["last_error"] == "EDGE_RESTARTED_BEFORE_START"
            marker = failed["result"]["nativeControlFailure"]
            assert marker["state"] == "APPLIED" and marker["evidence"]["writeClaimed"] is False
            events = [json.loads(row["payload_json"]) for row in case.store.list_pending_events()
                if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
                and json.loads(row["payload_json"])["commandUid"] == command["commandUid"]]
            assert len(events) == 1
            assert events[0]["payload"]["stage"] == "PRE_START_FAILED"
            assert events[0]["payload"]["errorCode"] == "EDGE_RESTARTED_BEFORE_START"
            with pytest.raises(JobSafetyError) as missing:
                case.safety.get_job_permit(command["commandUid"])
            assert missing.value.code == "JOB_PERMIT_NOT_FOUND"
            assert case.store.get_state("native_blocking_fault") in {None, ""}
        finally:
            restarted.close()


def test_slow_optional_camera_does_not_block_native_poll_or_create_a_communication_fault(runtime, tmp_path, monkeypatch):
    with real_work(runtime, tmp_path, False) as case:
        retained_context(case)
        clock = SimpleNamespace(now=0)
        case.photos = PhotoManager(case.store, str(tmp_path / "photos"),
            outside_camera_source=0, inside_camera_source=1, device_name="device-1", start_upload_worker=False)
        capture_started, release_capture, watchdog_fired = threading.Event(), threading.Event(), threading.Event()
        def stalled_camera(path, source):
            capture_started.set()
            assert release_capture.wait(6)
            raise RuntimeError("injected optional camera failure")
        monkeypatch.setattr(case.photos, "_capture_camera_to_path", stalled_camera)
        def unblock_failed_sync_test():
            watchdog_fired.set()
            release_capture.set()
        watchdog = threading.Timer(5, unblock_failed_sync_test)
        owner = open_owner(case, clock)
        try:
            now = finish_delivery_round(runtime, case, case.wire.now, 2, 700)
            assert select(runtime, case.delivery, now, "END")
            case.wire.now = now
            watchdog.start()
            poll_until(owner, clock, lambda: bool(case.store.get_photos_by_work(case.permit.work_uid)))
            assert capture_started.wait(1)
            assert not watchdog_fired.is_set(), "optional capture blocked the sole native UART owner"
            assert any(row["state"] == "CAPTURE_PENDING" for row in case.store.get_photos_by_work(case.permit.work_uid))
            before = len(case.wire.sent)
            for _ in range(120):
                clock.now += 100
                owner.poll()
                time.sleep(0.001)
            assert len(case.wire.sent) > before
            assert not case.store.get_state("native_blocking_fault")
            assert not watchdog_fired.is_set()
            assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") is not None
        finally:
            release_capture.set()
            watchdog.cancel()
            case.photos.close()
            owner.close()


def test_archived_delivery_late_real_c_result_is_only_evidence_not_normal_completion(runtime, tmp_path):
    with real_work(runtime, tmp_path, False) as case:
        retained_context(case)
        now = finish_delivery_round(runtime, case, case.wire.now, 2, 700)
        assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        response = case.wire.exchange("QUERY_WORK", original_scope(case.start))[0]
        work = uart.decode_payload(response["messageName"], response["payload"])
        result_response = case.wire.exchange("QUERY_RESULT", dict(queryId=99, mcuBootId=case.boot,
            resultSequence=work["resultSequence"], resultDigestSha256=work["resultDigestSha256"],
            workUid=case.permit.work_uid))
        # C already produced these bytes, but the Pi did not receive/save them.
        raw = next(frame["payload"] for frame in result_response if frame["messageName"] == "WORK_RESULT")
        assert case.store.get_native_mcu_result(case.boot, work["resultSequence"]) is None
        lib, endpoint, preparation, replies, _, sink, guard = runtime
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        assert lib.ActuatorRuntime_SetDoorTarget(1)
        case.wire.now = 0  # Actual C initialization erased its previous boot/work.
        replies.clear()
        clock = SimpleNamespace(now=0)
        owner = open_owner(case, clock)
        try:
            poll_until(owner, clock, lambda: owner.boot.current_boot(clock.now) not in {None, case.boot})
            case.store.archive_native_delivery_issue(case.permit, case.start["mcuCommandUid"],
                device_name="device-1", current_boot=lambda: owner.boot.current_boot(clock.now))
            issue = case.store.get_native_delivery_issue(case.permit.work_uid)
            original_slot = case.store.get_work_slot()
            case.serial.rx.extend(uart.encode_frame("WORK_RESULT", 99, raw))
            owner.poll()
            assert case.store.get_native_mcu_result(case.boot, work["resultSequence"])["payload"] == raw
            assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
            assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
            assert case.store.get_work_slot() == original_slot
            assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
            assert case.store.get_command(case.permit.command_uid)["state"] != "COMPLETED"
        finally:
            owner.close()


def test_complete_real_result_succeeds_even_when_all_start_decision_replies_are_lost(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path, drop_start_replies=True) as (case, owner):
        assert case.serial.dropped
        assert case.store.list_native_command_observations(case.start["mcuCommandUid"]) == []
        assert case.store.get_native_command(case.start["mcuCommandUid"])["decision_outcome"] is None
        assert case.store.get_work_slot() is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "COMPLETED"
        report = case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert report["state"] == "REPORT_CREATED"
        assert [frame["messageName"] for frame in case.wire.sent].count("START_DELIVERY_SESSION") == 1


def test_time_advancing_inside_sql_and_permission_work_never_moves_protocol_clock_backwards(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path, clock=AdvancingClock()) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        command = start_command()
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        pending = owner.start_delivery_command(command)
        poll_until(owner, case.clock, lambda: case.store.get_native_command(pending["mcu_command_uid"])["decision_outcome"] is not None)
        assert case.store.get_native_command(pending["mcu_command_uid"])["decision_outcome"] == "ACCEPTED"
        assert not case.store.get_state("native_blocking_fault")


def test_restart_finds_configuration_committed_before_its_runtime_marker(tmp_path, runtime, monkeypatch):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        original = case.store.set_state
        def stop_before_marker(key, value):
            if key == "native_configuration_application":
                raise OSError("injected loss before runtime marker commit")
            return original(key, value)
        with monkeypatch.context() as patch:
            patch.setattr(case.store, "set_state", stop_before_marker)
            with pytest.raises(OSError, match="injected loss"):
                owner.apply_configuration_command(command)
        saved = case.store.get_configuration(command["payload"]["applicationUid"])
        assert saved["state"] == "EDGE_SAVED"
        assert not case.store.get_state("native_configuration_application")
        owner.close()
        case.store.close()
        case.store.initialize()
        restarted = open_owner(case, case.clock)
        try:
            poll_until(restarted, case.clock, lambda: case.store.get_command(command["commandUid"])["state"] == "COMPLETED")
            applied = case.store.get_latest_applied_configuration()
            assert applied["application_uid"] == saved["application_uid"]
            assert applied["part_command_uids"] == saved["part_command_uids"]
            for uid in saved["part_command_uids"]:
                record = case.store.get_native_command(uid)
                assert record["decision_outcome"] == "ACCEPTED"
                frames = [frame for frame in case.wire.sent if frame["messageName"].startswith("CONFIG_")
                    and uart.decode_payload(frame["messageName"], frame["payload"])["mcuCommandUid"] == uid]
                assert len(frames) == 1
        finally:
            restarted.close()


@pytest.mark.parametrize("expired", [False, True])
def test_inflight_facts_query_preserves_only_the_previous_fresh_actual_read(runtime, tmp_path, expired):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        observed = owner.facts_query.observation(case.clock.now)
        assert observed["scaleReadStatus"] == "VALID"
        request_started = owner.facts_query.request_started_ms
        case.serial.drop = lambda decoded: decoded["messageName"] == "DEVICE_FACTS_REPLY"
        case.clock.now = request_started + 250
        owner.poll()
        assert owner.facts_query.observation(case.clock.now) is None  # New request, no accepted reply.
        if expired:
            while case.clock.now < request_started + 751:
                case.clock.now += min(10, request_started + 751 - case.clock.now)
                owner.poll()
            poll_until(owner, case.clock, lambda: owner.boot.current_boot(case.clock.now) is not None)
        command = start_command()
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        before = len(case.wire.sent)
        if expired:
            with pytest.raises(JobSafetyError) as rejected:
                owner.start_delivery_command(command)
            assert rejected.value.code == "MCU_FACTS_UNAVAILABLE"
            assert case.store.get_work_slot() is None
        else:
            pending = owner.start_delivery_command(command)
            assert pending["native_pending"] is True
            assert case.store.get_work_slot()["work_uid"] == command["payload"]["sessionUid"]
        assert len(case.wire.sent) == before  # Admission itself never sends a START.


def test_new_real_mcu_boot_invalidates_even_a_younger_than_750ms_cached_read(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        while case.clock.now < 990:
            case.clock.now += min(10, 990 - case.clock.now)
            owner.poll()
        old_boot = owner.boot.current_boot(case.clock.now)
        old_request = owner.facts_query.request_started_ms
        assert owner.facts_query.observation(case.clock.now)["scaleReadStatus"] == "VALID"
        lib, endpoint, preparation, replies, _, sink, guard = runtime
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        assert lib.ActuatorRuntime_SetDoorTarget(1)
        case.wire.now = 0
        replies.clear()
        case.serial.rx.clear()
        poll_until(owner, case.clock, lambda: owner.boot.current_boot(case.clock.now) not in {None, old_boot})
        assert case.clock.now - old_request < 750
        assert owner.facts_query.observation(case.clock.now) is None  # New MCU has not supplied its own facts.
        command = start_command()
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        with pytest.raises(JobSafetyError) as rejected:
            owner.start_delivery_command(command)
        assert rejected.value.code == "MCU_FACTS_UNAVAILABLE"
        assert case.store.get_work_slot() is None


def test_confirmed_mcu_restart_ends_clean_and_latches_bag_confirmation(
    runtime,
    tmp_path,
):
    with real_work(runtime, tmp_path, True) as case:
        case.clean = True
        retained_context(case)
        case.clock = SimpleNamespace(now=0)
        owner = open_owner(case, case.clock)
        try:
            poll_until(owner, case.clock, lambda: owner.uart_state == "READY")
            old_boot = owner.boot.current_boot(case.clock.now)
            assert old_boot == case.boot

            lib, endpoint, preparation, replies, _, sink, guard = runtime
            lib.TestFacts_InitHardware()
            lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
            assert lib.McuWorkPreparation_Attach(
                preparation,
                endpoint,
                2,
                guard,
                None,
            )
            assert lib.ActuatorRuntime_SetDoorTarget(1)
            case.wire.now = 0
            replies.clear()
            case.serial.rx.clear()

            poll_until(
                owner,
                case.clock,
                lambda: owner.boot.current_boot(case.clock.now)
                not in {None, old_boot},
            )
            poll_until(
                owner,
                case.clock,
                lambda: case.store.get_work_slot() is None,
            )

            command = case.store.get_command(case.permit.command_uid)
            marker = command["result"]["nativeControlFailure"]
            assert command["state"] == "FAILED"
            assert command["last_error"] == (
                "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
            )
            assert marker["state"] == "APPLIED"
            assert marker["evidence"]["reason"] == (
                "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
            )
            assert marker["evidence"]["businessValue"] == "NONE"
            assert case.store.clean_restart_interlock_active(1)
            original_command = case.store.get_command(
                case.permit.command_uid
            )["payload"]
            assert case.store.get_clean_restart_interlock_metadata(1) == {
                "profile": "native-clean-bag-interlock-v1",
                "sourceWorkUid": original_command["payload"]["operationUid"],
                "portNo": 1,
                "oldBagUid": original_command["payload"]["oldBagUid"],
                "newBagUid": original_command["payload"]["newBagUid"],
                "sourceCommandUid": original_command["commandUid"],
            }
            permanent = case.safety.get_job_permit(
                case.permit.permit_uid
            )
            assert permanent["state"] == "COMPLETED"
            assert permanent["completionOutcome"] == "FAILED"
            observations = [
                json.loads(row["payload_json"])
                for row in case.store.list_pending_events()
                if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
            ]
            assert len(observations) == 1
            assert observations[0]["payload"]["errorCode"] == (
                "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
            )

            with pytest.raises(JobSafetyError) as blocked:
                owner.start_delivery_command(start_command())
            assert blocked.value.code == (
                "CLEAN_BAG_CONFIRMATION_REQUIRED"
            )
        finally:
            owner.close()


@pytest.mark.parametrize("clean", [False, True])
def test_before_photo_deadline_records_missing_and_allows_one_real_start_without_blocking_uart(runtime, tmp_path, monkeypatch, clean):
    with completed_first_work(runtime, tmp_path) as (case, previous_owner):
        apply_configuration(case, previous_owner)
        previous_owner.close()
        case.photos = PhotoManager(case.store, str(tmp_path / "photos"),
            outside_camera_source=0, inside_camera_source=1, device_name="device-1", start_upload_worker=False)
        started, released, watchdog_fired = threading.Event(), threading.Event(), threading.Event()
        def stall_camera(path, source):
            started.set()
            assert released.wait(6)
            raise RuntimeError("injected unavailable camera")
        monkeypatch.setattr(case.photos, "_capture_camera_to_path", stall_camera)
        def unblock_failed_sync_test():
            watchdog_fired.set()
            released.set()
        watchdog = threading.Timer(5, unblock_failed_sync_test)
        owner = open_owner(case, case.clock)
        try:
            await_start_facts(case, owner)
            command = start_command(clean)
            assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
            assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
            before = len(case.wire.sent)
            watchdog.start()
            pending = (owner.start_clean_command if clean else owner.start_delivery_command)(command)
            assert started.wait(1)
            assert not watchdog_fired.is_set()
            uid = pending["mcu_command_uid"]
            began = case.clock.now
            for _ in range(120):
                owner.poll()
                record = case.store.get_native_command(uid)
                if case.clock.now - began < 10000:
                    assert not record["write_claimed"]
                if record["decision_outcome"] is not None:
                    break
                case.clock.now += 100
                time.sleep(0.001)
            record = case.store.get_native_command(uid)
            assert record["decision_outcome"] == "ACCEPTED"
            assert not case.store.get_state("native_blocking_fault")
            assert not watchdog_fired.is_set()
            work_uid = command["payload"]["operationUid" if clean else "sessionUid"]
            facts = case.photos.get_completion_photo_facts(work_uid, "CLEAN_OPERATION" if clean else "DELIVERY_SESSION")
            before_slots = CLEAN_OPEN_SLOTS if clean else DELIVERY_OPEN_SLOTS
            assert all(row["status"] == "PERMANENTLY_MISSING" for row in facts if row["slot"] in before_slots)
            assert [frame["messageName"] for frame in case.wire.sent[before:]].count(command["commandType"]) == 1
        finally:
            released.set()
            watchdog.cancel()
            case.photos.close()
            owner.close()
