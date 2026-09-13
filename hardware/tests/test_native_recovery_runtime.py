"""Recovery-only runtime over frozen v1 Edge/permanent custody.

No test in this module compiles an old MCU, authorizes a historical mechanical
command, or emits SAFE_CLOSE.  The runtime may only inspect/query existing
rows and durably confirm, retire, withdraw, or isolate those rows.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import json

import pytest

from hardware.tests.native_recovery_runtime_fixture import (
    FIXTURE,
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    cold_reopen_runtime_history,
    load_native_recovery_runtime_history,
    poll_candidate as poll,
)
import uart2_protocol as uart


def test_frozen_prepared_close_is_retired_without_any_motion(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        status = poll(candidate_loop(case, serial), serial)
        assert status["state"] == "NEW_CLOSE_REQUIRED"
        assert case.store.get_native_recovery_close_retirement(uid)["state"] == "RETIRED"
        ledger = case.safety.get_physical_action(uid)
        assert (ledger["state"], ledger["dispatchMode"], ledger["confirmedOutcome"]) == (
            "CONFIRMED", "PREPARED_ONLY", "NOT_EXECUTED")
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only(status, serial)
    finally:
        case.close()


@pytest.mark.parametrize("corruption", ["command-payload", "binding-hash", "receipt", "saved-payload"])
def test_frozen_edge_corruption_is_rejected_without_repair(tmp_path, corruption):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    uid = fixture["variants"]["claimed-with-output"]["targetActionUid"]
    if corruption == "command-payload":
        table = fixture["edgeTableOverrides"]["claimed-with-output"]["native_mcu_command"]
        row = next(row for row in table["rows"]
                   if row[table["columns"].index("command_uid")] == uid)
        index = table["columns"].index("payload")
        last = row[index]["hex"][-2:]
        row[index]["hex"] = row[index]["hex"][:-2] + ("ff" if last != "ff" else "00")
    elif corruption == "binding-hash":
        table = fixture["edgeBaseTables"]["native_delivery_recovery_close"]
        table["rows"][0][table["columns"].index("binding_sha256")] = "0" * 64
    elif corruption == "receipt":
        table = fixture["edgeBaseTables"]["native_delivery_recovery_close"]
        row = table["rows"][0]
        bundle_index = table["columns"].index("binding_json")
        digest_index = table["columns"].index("binding_sha256")
        bundle = json.loads(row[bundle_index])
        bundle["action"]["receipt_uid"] = "00000000-0000-4000-8000-000000000099"
        row[bundle_index] = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
        row[digest_index] = hashlib.sha256(row[bundle_index].encode("ascii")).hexdigest()
    else:
        table = fixture["edgeTableOverrides"]["claimed-with-output"]["native_actuator_event"]
        row = next(row for row in table["rows"]
                   if row[table["columns"].index("reported_command_uid")] == uid)
        index = table["columns"].index("saved_payload")
        last = row[index]["hex"][-2:]
        row[index]["hex"] = row[index]["hex"][:-2] + ("ff" if last != "ff" else "00")
    fixture.pop("fixtureSha256")
    fixture["fixtureSha256"] = hashlib.sha256(json.dumps(
        fixture, sort_keys=True, separators=(",", ":")
    ).encode("ascii")).hexdigest()
    bad = tmp_path / "corrupt-runtime-history.json"
    bad.write_text(json.dumps(fixture), encoding="utf-8")
    original = bad.read_bytes()
    with pytest.raises(ValueError):
        load_native_recovery_runtime_history(
            tmp_path / "load", "claimed-with-output", fixture_path=bad
        )
    assert bad.read_bytes() == original


def test_frozen_armed_unclaimed_close_is_withdrawn_without_rewriting_authorization(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "armed-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        before = case.safety.get_physical_action(uid)
        serial = RecoverySerial()
        status = poll(candidate_loop(case, serial), serial)
        assert status["state"] == "NEW_CLOSE_REQUIRED"
        assert case.store.get_native_recovery_close_retirement(uid)["state"] == "RETIRED"
        disposition = case.safety.get_native_recovery_close_disposition(uid)
        assert disposition["state"] == "DISPATCH_WITHDRAWN"
        assert disposition["dispositionBasis"] == "AUTHORIZED_DISPATCH_WITHDRAWN_BEFORE_WRITE_CLAIM"
        assert case.safety.get_physical_action(uid) == before
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_not_seen_query_never_replays_retires_or_confirms_claimed_history(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        uid = case.variant["targetActionUid"]
        before = case.safety.get_physical_action(uid)
        serial = RecoverySerial(query_outcome="NOT_SEEN")
        status = poll(candidate_loop(case, serial), serial, count=18)
        assert any(row["outcome"] == "NOT_SEEN"
                   for row in case.store.list_native_command_observations(uid))
        assert case.store.get_native_recovery_close_retirement(uid) is None
        assert case.store.get_native_recovery_close_confirmation(uid) is None
        assert case.safety.get_physical_action(uid) == before
        assert_recovery_only(status, serial)
    finally:
        case.close()


@pytest.mark.parametrize("mcu", [None, 0], ids=["offline", "new-boot"])
def test_saved_historical_output_is_confirmed_without_replay_even_if_mcu_unavailable(tmp_path, mcu):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-with-output")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial(mcu_boot_id=mcu)
        status = poll(candidate_loop(case, serial), serial, count=12)
        assert case.store.get_native_recovery_close_confirmation(uid)["state"] == "CONFIRMED"
        ledger = case.safety.get_physical_action(uid)
        assert (ledger["state"], ledger["confirmedOutcome"]) == ("CONFIRMED", "EXECUTED")
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_timeout_does_not_trust_persisted_boot_or_retire_preparation(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial(mcu_boot_id=None)
        status = poll(candidate_loop(case, serial), serial, count=10, step=1000)
        assert status["state"] == "WAITING_BOOT"
        assert case.store.get_native_recovery_close_retirement(uid) is None
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert_recovery_only(status, serial)
    finally:
        case.close()


@pytest.mark.parametrize("change", ["release", "port"])
def test_changed_occupancy_prevents_retirement_and_actuator_ack(tmp_path, change):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        candidate = candidate_loop(case, serial)
        if change == "release":
            case.store.release_work_slot(case.permit.work_uid)
        else:
            with case.store.transaction() as connection:
                connection.execute("UPDATE work_slot SET port_no=2 WHERE slot_id=1")
        status = poll(candidate, serial, count=4)
        assert status["state"] == "ORIGINAL_OCCUPANCY_CHANGED"
        assert case.store.get_native_recovery_close_retirement(uid) is None
        assert {name for name, _ in serial.sent} <= {"BOOT_PROBE", "BIND_BOOT"}
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_late_final_is_preserved_only_as_issue_evidence(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        serial = RecoverySerial()
        serial.inject_mcu_frame(uart.encode_frame("WORK_RESULT", 800, case.saved["payload"]))
        status = candidate_loop(case, serial).poll()
        assert len(case.store.list_native_delivery_issue_results(case.issue["issueUid"])) == 1
        assert not case.store.list_native_result_report_tasks()
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only(status, serial)
    finally:
        case.close()


@pytest.mark.parametrize(
    ("variant", "operation", "expected_disposition"),
    [
        ("prepared-unclaimed", "RETIRE_NATIVE_RECOVERY_CLOSE", None),
        ("armed-unclaimed", "WITHDRAW_NATIVE_RECOVERY_CLOSE_DISPATCH", "DISPATCH_WITHDRAWN"),
    ],
)
def test_lost_permanent_reply_resumes_idempotently_in_a_new_owner(
    tmp_path, monkeypatch, variant, operation, expected_disposition
):
    from local_control import LocalControlUnavailable

    case = load_native_recovery_runtime_history(tmp_path, variant)
    try:
        uid = case.variant["targetActionUid"]
        client = case.safety._client
        request = client.request

        def lose_once(name, payload):
            result = request(name, payload)
            if name == operation:
                raise LocalControlUnavailable("reply lost after permanent commit")
            return result

        monkeypatch.setattr(client, "request", lose_once)
        first_serial = RecoverySerial()
        first = poll(candidate_loop(case, first_serial), first_serial, count=5)
        assert first["state"] == "WAITING_PERMANENT_LEDGER"
        pending = case.store.get_native_recovery_close_retirement(uid)
        assert pending["state"] == "PENDING"
        monkeypatch.setattr(client, "request", request)

        second_serial = RecoverySerial()
        second = poll(candidate_loop(case, second_serial), second_serial, count=5)
        assert second["state"] == "NEW_CLOSE_REQUIRED"
        assert case.store.get_native_recovery_close_retirement(uid) == pending | {"state": "RETIRED"}
        disposition = (None if expected_disposition is None
                       else case.safety.get_native_recovery_close_disposition(uid)["state"])
        assert disposition == expected_disposition
        assert_recovery_only(first, first_serial)
        assert_recovery_only(second, second_serial)
    finally:
        case.close()


def test_authorization_race_switches_prepared_retirement_to_withdrawal(tmp_path, monkeypatch):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        client = case.safety._client
        request = client.request
        raced = []

        def arm_after_read(operation, payload):
            result = request(operation, payload)
            if operation == "GET_PHYSICAL_ACTION" and payload["actionUid"] == uid and not raced:
                raced.append(True)
                case.permanent.store.arm_physical_action({
                    "actionUid": uid,
                    "dispatchAttemptToken": case.fixture["common"]["recovery"]["dispatchAttemptToken"],
                })
            return result

        monkeypatch.setattr(client, "request", arm_after_read)
        serial = RecoverySerial()
        candidate = candidate_loop(case, serial)
        first = poll(candidate, serial, count=5)
        assert first["state"] == "WAITING_PERMANENT_LEDGER"
        serial.advance(1000)
        second = poll(candidate, serial, count=5)
        assert second["state"] == "NEW_CLOSE_REQUIRED"
        assert case.safety.get_physical_action(uid)["state"] == "ARMED"
        assert case.safety.get_native_recovery_close_disposition(uid)["state"] == "DISPATCH_WITHDRAWN"
        assert_recovery_only(second, serial)
    finally:
        case.close()


def test_retirement_and_permanent_disposition_survive_sqlite_reopen(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "armed-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        status = poll(candidate_loop(case, serial), serial)
        retirement = case.store.get_native_recovery_close_retirement(uid)
        disposition = case.safety.get_native_recovery_close_disposition(uid)
        cold_reopen_runtime_history(case)
        assert case.store.get_native_recovery_close_retirement(uid) == retirement
        assert case.safety.get_native_recovery_close_disposition(uid) == disposition
        again_serial = RecoverySerial()
        again = poll(candidate_loop(case, again_serial), again_serial, count=4)
        assert case.store.get_native_recovery_close_retirement(uid) == retirement
        assert case.safety.get_native_recovery_close_disposition(uid) == disposition
        assert_recovery_only(status, serial)
        assert_recovery_only(again, again_serial)
    finally:
        case.close()


@pytest.mark.parametrize("mode", ["thread", "reentrant"])
def test_candidate_rejects_other_thread_and_reentrant_poll_without_extra_writes(tmp_path, monkeypatch, mode):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        serial = RecoverySerial()
        candidate = candidate_loop(case, serial)
        before = list(serial.sent)
        with pytest.raises(RuntimeError, match="foreground owner"):
            if mode == "thread":
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(candidate.poll).result()
            else:
                monkeypatch.setattr(candidate.transport, "poll", lambda now: candidate.poll())
                candidate.poll()
        assert serial.sent == before
    finally:
        case.close()


@pytest.mark.parametrize("variant", ["prepared-unclaimed", "armed-unclaimed"])
def test_real_main_entry_processes_only_existing_history(tmp_path, monkeypatch, variant):
    import main
    import native_recovery_entry as entry

    case = load_native_recovery_runtime_history(tmp_path, variant)
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        ticks = []
        monkeypatch.setattr(entry, "LocalControlClient", lambda *args, **kwargs: case.safety._client)

        def wait_once(seconds):
            assert seconds == 0.05
            ticks.append(seconds)
            serial.advance(50)

        result = main.run_gateway(
            [
                "--native-recovery-candidate",
                "--store-path", str(case.path),
                "--serial-device", "/dev/frozen-history",
                "--device-name", case.issue["deviceName"],
                "--updater-socket", str(tmp_path / "updater.sock"),
            ],
            legacy_factory=lambda: pytest.fail("candidate entered legacy gateway"),
            candidate_runner=partial(
                entry.main,
                port_factory=lambda **kwargs: serial,
                stop=lambda: len(ticks) == 8,
                wait=wait_once,
                clock=lambda: serial.now,
            ),
        )
        assert result["admissionAllowed"] is False
        assert case.store.get_native_recovery_close_retirement(uid)["state"] == "RETIRED"
        assert serial.is_open is False
        assert_recovery_only(result, serial)
    finally:
        case.close()
