"""Slow local-socket boundaries must not own the native UART foreground.

The permanent ledger is the real UpdaterStore, the business database is real
SQLite, and every MCU reply is emitted by the compiled autonomous C executor.
Only the local-socket reply is event-gated; no permit/receipt is fabricated.
Pi monotonic time is independently advanced while the RPC remains in flight.
"""
from collections import Counter
from contextlib import contextmanager
import threading
import time
from types import SimpleNamespace

import pytest

from job_safety import JobSafetyError, PermanentJobSafety
from local_control import LocalControlUnavailable
from native_business_runtime import NativeBusinessRuntime
from updater_store import UpdaterStoreError
from hardware.tests.test_mcu_simplified_execution import library, runtime, select, request, tick
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_native_business_runtime import (
    CSerial, RuntimeUpdaterClient, configuration_command, retained_context, start_command,
)
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_simplified_mcu_pi_business import real_work, finish_delivery_round


class DelayedUpdaterClient(RuntimeUpdaterClient):
    """An actual ledger request whose local-socket response can be delayed."""

    def __init__(self, store):
        super().__init__(store)
        self.calls = Counter()
        self.requests = []
        self.action = None
        self.after_commit = False
        self.lose_replies = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.watchdog_fired = threading.Event()

    def delay(self, action, *, after_commit=False, lose_replies=0):
        self.action, self.after_commit = action, after_commit
        self.lose_replies = lose_replies
        self.entered.clear()
        self.release.clear()
        self.returned.clear()
        self.watchdog_fired.clear()

    def request(self, action, payload):
        self.calls[action] += 1
        self.requests.append((action, dict(payload)))
        if action != self.action:
            return super().request(action, payload)
        result = super().request(action, payload) if self.after_commit else None
        self.entered.set()
        # A finite guard produces an assertion instead of hanging pytest if a
        # regression performs this call on the serial-owning foreground thread.
        if not self.release.wait(4):
            self.watchdog_fired.set()
        try:
            result = result if self.after_commit else super().request(action, payload)
            if self.lose_replies:
                self.lose_replies -= 1
                raise LocalControlUnavailable("injected lost actual ledger reply")
            return result
        finally:
            self.returned.set()


def poll_until(case, owner, predicate, *, count=600):
    for _ in range(count):
        owner.poll()
        if predicate():
            return
        case.clock.now += 10
        time.sleep(0.001)  # Allow the real RPC worker to run; never inject its result.
    raise AssertionError("native runtime did not converge: " + repr(dict(
        now=case.clock.now, uart=owner.uart_state, slot=case.store.get_work_slot(),
        fault=case.store.get_state("native_blocking_fault"))))


@contextmanager
def runtime_case(runtime, tmp_path, *, clean=False):
    with real_work(runtime, tmp_path, clean) as case:
        case.clean = clean
        retained_context(case)
        case.clock = SimpleNamespace(now=0)
        case.client = DelayedUpdaterClient(case.safety._client.store)
        case.safety = PermanentJobSafety(case.client)
        owner = NativeBusinessRuntime(case.store, case.safety, device_name="device-1",
            connected=lambda: True, clock=lambda: case.clock.now)
        case.serial = CSerial(case.wire)
        owner.open(port="host-test-no-device", port_factory=lambda **options: case.serial)
        try:
            if clean:
                now = tick(runtime, case.wire.now, inputs()["device"]["cleanSolenoidPulseMs"])
                assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
                now = take_samples(runtime, [100] * 5, start=now, measurement=2)
            else:
                now = finish_delivery_round(runtime, case, case.wire.now, 2, 700)
                assert select(runtime, case.delivery, now, "END")
            case.wire.now = now
            poll_until(case, owner, lambda: case.store.get_native_result_report(case.permit,
                case.start["mcuCommandUid"], device_name="device-1") is not None)
            yield case, owner
        finally:
            case.client.release.set()
            if case.client.entered.is_set():
                assert case.client.returned.wait(2), "test ledger request did not finish"
            owner.close()
            # Production shutdown is intentionally nonblocking; only the test
            # waits before destroying its in-process socket-server database.
            owner._rpc._thread.join(timeout=2)
            assert not owner._rpc._thread.is_alive()


def confirm_original_result(case):
    report = case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1")
    _, _, confirmation = confirmation_wire(case, report["eventUid"])
    assert case.store.receive_business_confirmation_and_create_receipt(
        command=confirmation, device_name="device-1") == "ACCEPTED"
    return report


def prepare_new_business(case, owner):
    confirm_original_result(case)
    poll_until(case, owner, lambda: case.store.get_work_slot() is None)
    command = configuration_command()
    assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
    assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
    owner.apply_configuration_command(command)
    poll_until(case, owner, lambda: case.store.get_command(command["commandUid"])["state"] == "COMPLETED")
    def idle_facts():
        facts = owner.facts_query.observation(case.clock.now) if owner.facts_query else None
        return (facts and facts["retainedWorkState"] in {"NONE", "RESULT_RELEASED"}
            and not facts["configStaging"] and owner.facts_query.latest_weight(case.clock.now) is not None)
    poll_until(case, owner, idle_facts)
    command = start_command()
    assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
    assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
    return command


def test_delayed_request_returns_pending_and_never_starts_without_real_active_permit(runtime, tmp_path):
    with runtime_case(runtime, tmp_path) as (case, owner):
        command = prepare_new_business(case, owner)
        case.client.delay("REQUEST_JOB_PERMIT")
        request_count = case.client.calls["REQUEST_JOB_PERMIT"]
        before = len(case.wire.sent)
        pending = owner.start_delivery_command(command)
        assert not case.client.watchdog_fired.is_set(), "START blocked on the permanent permit RPC"
        assert pending["native_pending"] is True
        uid = pending["mcu_command_uid"]
        poll_until(case, owner, case.client.entered.is_set)
        assert not case.client.returned.is_set()
        began = case.clock.now
        for _ in range(111):
            owner.poll()
            assert not case.store.get_native_command(uid)["write_claimed"]
            assert case.store.get_native_command(uid)["decision_outcome"] is None
            assert case.store.get_work_slot()["work_uid"] == command["payload"]["sessionUid"]
            case.clock.now += 100
            time.sleep(0.001)
        assert case.clock.now - began > 10000
        assert case.client.calls["REQUEST_JOB_PERMIT"] == request_count + 1
        with pytest.raises(UpdaterStoreError) as missing:
            case.client.store.get_job_permit({"permitUid": command["commandUid"]})
        assert missing.value.code == "JOB_PERMIT_NOT_FOUND"
        assert not case.client.watchdog_fired.is_set()
        assert not case.store.get_state("native_blocking_fault")
        names = [frame["messageName"] for frame in case.wire.sent[before:]]
        assert names.count("QUERY_DEVICE_FACTS") >= 10
        assert "START_DELIVERY_SESSION" not in names
        case.client.release.set()
        poll_until(case, owner, lambda: case.store.get_native_command(uid)["decision_outcome"] is not None)
        assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
        assert case.client.store.get_job_permit({"permitUid": command["commandUid"]})["state"] == "ACTIVE"
        assert case.client.calls["REQUEST_JOB_PERMIT"] == request_count + 1
        assert [frame["messageName"] for frame in case.wire.sent[before:]].count("START_DELIVERY_SESSION") == 1


@pytest.mark.parametrize("clean", [False, True])
def test_delayed_real_completion_receipt_keeps_slot_and_does_not_block_uart_queries(runtime, tmp_path, clean):
    with runtime_case(runtime, tmp_path, clean=clean) as (case, owner):
        # The permanent DB may already have committed; until its actual reply is
        # received and verified the business DB must retain the original slot.
        case.client.delay("COMPLETE_JOB", after_commit=True)
        report = confirm_original_result(case)
        new_bag_uid = case.store.get_command(case.permit.command_uid)["payload"]["payload"].get("newBagUid")
        before = len(case.wire.sent)
        owner.poll()
        assert not case.client.watchdog_fired.is_set(), "UART poll blocked on the permanent completion RPC"
        poll_until(case, owner, case.client.entered.is_set)
        assert not case.client.returned.is_set()
        assert case.client.store.get_job_permit({"permitUid": case.permit.permit_uid})["state"] == "COMPLETED"
        began = case.clock.now
        for _ in range(111):
            owner.poll()
            assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
            assert len(case.store.list_native_result_report_tasks()) == 1
            if clean:
                assert case.store.get_bag_baseline(new_bag_uid) is None
            case.clock.now += 100
            time.sleep(0.001)
        assert case.clock.now - began > 10000
        assert case.client.calls["COMPLETE_JOB"] == 1
        assert not case.client.watchdog_fired.is_set()
        assert not case.store.get_state("native_blocking_fault")
        names = [frame["messageName"] for frame in case.wire.sent[before:]]
        assert names.count("QUERY_DEVICE_FACTS") >= 10
        assert "START_DELIVERY_SESSION" not in names
        case.client.release.set()
        poll_until(case, owner, lambda: case.store.get_work_slot() is None)
        assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"],
            device_name="device-1") == report
        if clean:
            assert case.store.get_bag_baseline(new_bag_uid)["weight_grams"] == 100
        assert case.client.calls["COMPLETE_JOB"] == 1


@pytest.mark.parametrize("action,after_commit", [("REQUEST_JOB_PERMIT", False), ("BEGIN_JOB", True)])
def test_mcu_restart_during_original_authority_wait_never_dispatches_the_old_start(
        runtime, tmp_path, action, after_commit):
    with runtime_case(runtime, tmp_path) as (case, owner):
        command = prepare_new_business(case, owner)
        case.client.delay(action, after_commit=after_commit)
        before = len(case.wire.sent)
        pending = owner.start_delivery_command(command)
        uid = pending["mcu_command_uid"]
        poll_until(case, owner, case.client.entered.is_set)
        assert not case.client.returned.is_set()
        assert not case.store.get_native_command(uid)["write_claimed"]
        old_boot = owner.boot.current_boot(case.clock.now)
        lib, endpoint, preparation, replies, _, sink, guard = runtime
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        assert lib.ActuatorRuntime_SetDoorTarget(1)
        case.wire.now = 0
        replies.clear()
        case.serial.rx.clear()
        poll_until(case, owner, lambda: owner.boot.current_boot(case.clock.now) not in {None, old_boot})
        case.client.release.set()
        assert case.client.returned.wait(1)
        for _ in range(120):
            owner.poll()
            case.clock.now += 100
            time.sleep(0.001)
        assert not case.store.get_native_command(uid)["write_claimed"]
        assert case.store.get_native_command(uid)["decision_outcome"] is None
        assert case.store.get_native_command(uid)["mcu_boot_id"] == old_boot
        assert case.client.calls["REQUEST_JOB_PERMIT"] == 1
        assert not case.client.watchdog_fired.is_set()
        assert not case.store.get_state("native_blocking_fault")
        assert "START_DELIVERY_SESSION" not in [frame["messageName"] for frame in case.wire.sent[before:]]
        # S2 owns eventual failure/report/release; this test does not legitimise
        # indefinite occupancy. A returned old-boot permit grants no new START.


@pytest.mark.parametrize("action", ["REQUEST_JOB_PERMIT", "BEGIN_JOB"])
def test_lost_start_authority_reply_retries_only_the_original_payload(runtime, tmp_path, action):
    with runtime_case(runtime, tmp_path) as (case, owner):
        command = prepare_new_business(case, owner)
        case.client.delay(action, after_commit=True, lose_replies=1)
        before = len(case.client.requests)
        sent = len(case.wire.sent)
        pending = owner.start_delivery_command(command)
        uid = pending["mcu_command_uid"]
        original_record = case.store.get_native_command(uid)
        poll_until(case, owner, case.client.entered.is_set)
        assert not case.store.get_native_command(uid)["write_claimed"]
        case.client.release.set()
        poll_until(case, owner, lambda: case.store.get_native_command(uid)["decision_outcome"] is not None)
        attempts = [payload for operation, payload in case.client.requests[before:] if operation == action]
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert attempts[0]["permitUid"] == command["commandUid"]
        assert case.store.get_native_command(uid)["payload"] == original_record["payload"]
        assert case.store.get_native_command(uid)["decision_outcome"] == "ACCEPTED"
        assert [frame["messageName"] for frame in case.wire.sent[sent:]].count("START_DELIVERY_SESSION") == 1
        assert not case.store.get_state("native_blocking_fault")


def test_lost_completed_clean_receipts_and_pi_restart_reuse_exact_original_completion(runtime, tmp_path):
    with runtime_case(runtime, tmp_path, clean=True) as (case, owner):
        case.client.delay("COMPLETE_JOB", after_commit=True, lose_replies=2)
        report = confirm_original_result(case)
        new_bag_uid = case.store.get_command(case.permit.command_uid)["payload"]["payload"]["newBagUid"]
        poll_until(case, owner, case.client.entered.is_set)
        prepared = case.store.get_command(case.permit.command_uid)["result"]["nativeBusinessCompletion"]
        assert prepared["state"] == "PREPARED"
        case.client.release.set()
        with pytest.raises(JobSafetyError) as unknown:
            poll_until(case, owner, lambda: case.store.get_work_slot() is None)
        assert unknown.value.code == "JOB_GATE_UNAVAILABLE"
        assert case.client.calls["COMPLETE_JOB"] == 2
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_bag_baseline(new_bag_uid) is None
        assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") == report
        owner.close()
        owner._rpc._thread.join(timeout=2)
        assert not owner._rpc._thread.is_alive()
        case.store.close()
        case.store.initialize()
        case.client.action = None  # The replacement local socket now delivers real replies.
        restarted = NativeBusinessRuntime(case.store, case.safety, device_name="device-1",
            connected=lambda: True, clock=lambda: case.clock.now)
        case.serial = CSerial(case.wire)
        restarted.open(port="host-test-no-device", port_factory=lambda **options: case.serial)
        try:
            poll_until(case, restarted, lambda: case.store.get_work_slot() is None)
            attempts = [payload for action, payload in case.client.requests if action == "COMPLETE_JOB"]
            assert len(attempts) == 3 and all(payload == attempts[0] for payload in attempts)
            assert attempts[0]["completionUid"] == case.permit.command_uid
            assert attempts[0]["completionDigestSha256"] == prepared["evidenceSha256"]
            applied = case.store.get_command(case.permit.command_uid)["result"]["nativeBusinessCompletion"]
            assert applied == prepared | {"state": "APPLIED"}
            assert case.store.get_bag_baseline(new_bag_uid)["weight_grams"] == 100
            assert len(case.store.list_native_result_report_tasks()) == 1
            assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") == report
            assert [frame["messageName"] for frame in case.wire.sent].count("START_CLEAN_OPERATION") == 1
        finally:
            restarted.close()
            restarted._rpc._thread.join(timeout=2)
            assert not restarted._rpc._thread.is_alive()
