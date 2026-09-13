"""Confirmed MCU restart archives evidence, never reconstructs a delivery."""
import pytest
import uuid
from dataclasses import replace
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_native_work_recovery import RecoveryWire
from hardware.tests.test_native_result_report import original_command


def without_final_packet(case, wire):
    saved = wire.finish_delivery()
    # Receive-boundary fixture: all process facts arrived, final packet did not.
    with case.store.transaction(immediate=True) as conn:
        conn.execute("DELETE FROM native_result_report_outbox")
        conn.execute("DELETE FROM native_mcu_result")
    return saved


def test_complete_process_records_without_final_packet_are_archived_not_reconstructed(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        without_final_packet(case, wire)
        boot = wire.reset_mcu()
        owner = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        result = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert result["status"] == "DELIVERY_ISSUE_ARCHIVED"
        issue = result["issue"]
        assert issue["reason"] == "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
        assert issue["reasonText"] == "单片机重启，未取得最终结果包"
        assert issue["settlementAllowed"] is False
        facts = case.store.list_native_delivery_issue_facts(issue["issueUid"])
        first = next(f for f in facts if f["message_name"] == "WORK_PREOPEN_WEIGHT_READY")
        final = next(f for f in facts if f["message_name"] == "WORK_POSTCLOSE_WEIGHT_READY")
        assert uart.decode_payload(first["message_name"], first["payload"])["reportedWeightGrams"] == 500
        assert uart.decode_payload(final["message_name"], final["payload"])["reportedWeightGrams"] == 700
        assert any(f["message_name"] == "DELIVERY_SELECTION" for f in facts)
        assert case.store.list_native_result_report_tasks() == []
        assert case.store.list_pending_events() == []  # issue cloud path is not a normal business result
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1") == result


@pytest.mark.parametrize("before_intent", [False, True])
def test_final_packet_committed_before_archive_always_wins(runtime, tmp_path, before_intent):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        boot = wire.reset_mcu()
        owner = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        if not before_intent:
            assert owner.evaluate(case.permit, case.start["mcuCommandUid"])["status"] == "RECOVERY_INTENT_RECORDED"
        case.store.save_native_mcu_result(saved["payload"])
        decision = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE" and decision["result"]["payload"] == saved["payload"]
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None


@pytest.mark.parametrize("observation", ["no_reply", "expired", "same_boot"])
def test_timeout_or_pi_only_restart_never_archives_a_delivery(runtime, tmp_path, observation):
    from edge_store import EdgeStore
    from mcu_session import McuBootSession
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        boot = McuBootSession(case.store, wire.write) if observation == "no_reply" else wire.handshake()
        if observation == "expired":
            wire.now += 1000
        result = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).archive_delivery(
            case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert result["status"] == ("WAIT_FOR_ORIGINAL_WORK" if observation == "same_boot" else "WAIT_FOR_BOOT")
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        assert case.store.get_work_slot() == case.occupancy


def test_clean_recovery_cannot_use_delivery_archive(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=True, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        with pytest.raises(ValueError, match="only to delivery"):
            owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"])["status"] == "RECOVERY_INTENT_RECORDED"
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None


def test_nonrestart_final_weight_failure_keeps_existing_complete_result_policy(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = wire.finish_delivery(unavailable=True)
        owner = NativeWorkRecovery(case.store, wire.handshake(), clock=lambda: wire.now)
        result = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert result["status"] == "COMPLETE_RESULT_AVAILABLE" and result["result"]["payload"] == saved["payload"]
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None


def test_archive_preserves_original_context_and_late_result_cannot_overwrite_next_bag(runtime, tmp_path):
    from edge_store import EdgeStore
    from work_recovery import NativeWorkRecovery
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        context = dict(case.occupancy["context"], bagUid=str(uuid.uuid4()), customerEvidence="original-session")
        assert case.store.update_work_context(case.permit.work_uid, context)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        archived = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        assert archived["issue"]["originalWorkContext"] == context
        # Model the independently completed close/check + later business boundary.
        # This is NOT proof that this patch implements the physical recovery.
        assert case.store.release_work_slot(case.permit.work_uid)
        later_uid, bag_uid = str(uuid.uuid4()), str(uuid.uuid4())
        assert case.store.acquire_work_slot("CLEAN", later_uid, 1, dict(newBagUid=bag_uid, baselineWeightGrams=80))
        later = case.store.get_work_slot()
        with case.store.transaction() as conn:
            conn.execute("""INSERT INTO bag_baseline(bag_uid,weight_grams,source_kind,measurement_uid,updated_at)
                VALUES (?,80,'TEST',?, '2026-09-13T00:00:00Z')""", (bag_uid, str(uuid.uuid4())))
        baseline = case.store.get_bag_baseline(bag_uid)
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        case.store.save_native_mcu_result(saved["payload"])
        owner = NativeWorkRecovery(case.store, wire.handshake(), clock=lambda: wire.now)
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == archived
        assert owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1") == archived
        assert NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
            case.permit, case.start["mcuCommandUid"])["state"] == "DELIVERY_ISSUE_ARCHIVED"
        assert case.store.get_work_slot() == later and case.store.get_bag_baseline(bag_uid) == baseline
        assert case.store.list_pending_events() == []


@pytest.mark.parametrize("field,value", [("workUid", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ("originCommandUid", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), ("originCommandSequence", 99),
    ("portNo", 2), ("mcuBootId", 2), ("configVersion", 99)])
def test_late_result_with_wrong_original_identity_is_retained_but_not_trusted(runtime, tmp_path, field, value):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        archived = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        wrong = uart.decode_payload("WORK_RESULT", saved["payload"])
        wrong[field] = value
        if field == "mcuBootId":
            wrong["initialSourceMcuBootId"] = wrong["finalSourceMcuBootId"] = value
        wrong["resultDigestSha256"] = uart.compute_result_digest(wrong)
        raw = uart.encode_payload("WORK_RESULT", wrong)
        with pytest.raises(ValueError, match="identity"):
            case.store.save_native_mcu_result(raw)
        assert case.store.list_native_delivery_issue_results(archived["issue"]["issueUid"]) == []
        assert case.store.get_native_mcu_result(wrong["mcuBootId"], wrong["resultSequence"])["payload"] == raw
        assert case.store.list_native_result_report_tasks() == []
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == archived
        assert owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1") == archived


def test_late_complete_result_only_appends_evidence_and_never_reopens_normal_reporting(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        boot = wire.reset_mcu()
        owner = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        archived = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        receipt = case.store.save_native_mcu_result(saved["payload"])
        assert receipt["savedPayload"] == saved["payload"][:60]
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == archived
        facts = case.store.list_native_delivery_issue_results(archived["issue"]["issueUid"])
        assert len(facts) == 1 and facts[0]["payload"] == saved["payload"]
        assert case.store.save_native_mcu_result(saved["payload"]) == receipt
        assert case.store.list_native_delivery_issue_results(archived["issue"]["issueUid"]) == facts
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        assert reporter.prepare(case.permit, case.start["mcuCommandUid"])["state"] == "DELIVERY_ISSUE_ARCHIVED"
        with pytest.raises(ValueError, match="archived"):
            case.store.create_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1", ledgers={})
        assert case.store.list_pending_events() == []
        assert case.store.get_work_slot() == case.occupancy
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("different_sequence", [False, True])
def test_conflicting_late_packet_keeps_first_evidence_and_archive_verdict(runtime, tmp_path, different_sequence):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        archived = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        case.store.save_native_mcu_result(saved["payload"])
        evidence = case.store.list_native_delivery_issue_results(archived["issue"]["issueUid"])
        wrong = uart.decode_payload("WORK_RESULT", saved["payload"])
        wrong["resultSequence" if different_sequence else "finalWeightGrams"] += 1
        wrong["resultDigestSha256"] = uart.compute_result_digest(wrong)
        raw = uart.encode_payload("WORK_RESULT", wrong)
        with pytest.raises(ValueError, match="conflict"):
            case.store.save_native_mcu_result(raw)
        assert case.store.list_native_delivery_issue_results(archived["issue"]["issueUid"]) == evidence
        if different_sequence:
            assert case.store.get_native_mcu_result(wrong["mcuBootId"], wrong["resultSequence"])["payload"] == raw
        else:
            assert case.store.list_native_mcu_result_conflicts()[0]["payload"] == raw
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == archived
        assert case.store.list_native_result_report_tasks() == [] and case.store.list_pending_events() == []


@pytest.mark.parametrize("point", ["archive", "facts", "commit"])
def test_failed_archive_transaction_does_not_hide_a_subsequently_received_result(runtime, tmp_path, point):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        owner.evaluate(case.permit, case.start["mcuCommandUid"])
        target = {"archive": (sqlite3.SQLITE_INSERT, "native_delivery_issue"),
            "facts": (sqlite3.SQLITE_INSERT, "native_delivery_issue_fact"),
            "commit": (sqlite3.SQLITE_TRANSACTION, "COMMIT")}[point]
        case.store._conn.set_authorizer(lambda op, first, *_: sqlite3.SQLITE_DENY if (op, first) == target else sqlite3.SQLITE_OK)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        finally:
            case.store._conn.set_authorizer(None)
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        case.store.save_native_mcu_result(saved["payload"])
        assert owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")["status"] == "COMPLETE_RESULT_AVAILABLE"


@pytest.mark.parametrize("attempt", range(4))
def test_result_and_archive_race_has_exactly_one_terminal_classification(runtime, tmp_path, attempt):
    from edge_store import EdgeStore
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        second = EdgeStore(case.store.db_path)
        second.initialize()
        try:
            with ThreadPoolExecutor(2) as pool:
                archiving = pool.submit(owner.archive_delivery, case.permit, case.start["mcuCommandUid"], device_name="device-1")
                receiving = pool.submit(second.save_native_mcu_result, saved["payload"])
                decision = archiving.result()
                receiving.result()
            if decision["status"] == "DELIVERY_ISSUE_ARCHIVED":
                assert len(case.store.list_native_delivery_issue_results(decision["issue"]["issueUid"])) == 1
                assert case.store.list_native_result_report_tasks() == []
                assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == decision
            else:
                assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
                assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
                assert len(case.store.list_native_result_report_tasks()) == 1
            assert case.store.get_work_slot() == case.occupancy
        finally:
            second.close()


@pytest.mark.parametrize("point", ["before_archive", "before_facts", "after_commit"])
def test_process_death_never_leaves_half_an_archive(runtime, tmp_path, point):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        owner.evaluate(case.permit, case.start["mcuCommandUid"])
        script = '''
import os, sqlite3, sys, json
sys.path.insert(0, sys.argv[1])
from edge_store import EdgeStore
from job_safety import JobPermit
store=EdgeStore(sys.argv[2]); store.initialize()
target={"before_archive": (sqlite3.SQLITE_INSERT,"native_delivery_issue"),
        "before_facts": (sqlite3.SQLITE_INSERT,"native_delivery_issue_fact")}.get(sys.argv[5])
def crash(operation, first, *args):
    if (operation, first)==target: os._exit(77)
    return sqlite3.SQLITE_OK
store._conn.set_authorizer(crash)
result=store.archive_native_delivery_issue(JobPermit(**json.loads(sys.argv[3])),sys.argv[4],
    device_name="device-1",current_boot=lambda:2)
assert result["status"]=="DELIVERY_ISSUE_ARCHIVED"
os._exit(77)
'''
        run = subprocess.run([sys.executable, "-I", "-c", script, str(Path(__file__).resolve().parents[1]),
            case.store.db_path, json.dumps(asdict(case.permit)), case.start["mcuCommandUid"], point],
            cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert run.returncode == 77, run.stderr
        archived = case.store.get_native_delivery_issue(case.permit.work_uid)
        assert (archived is not None) == (point == "after_commit")
        case.store.save_native_mcu_result(saved["payload"])
        result = owner.evaluate(case.permit, case.start["mcuCommandUid"])
        assert result["status"] == ("DELIVERY_ISSUE_ARCHIVED" if archived else "COMPLETE_RESULT_AVAILABLE")
        assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []


def test_restart_before_any_measurement_archives_no_fabricated_weight(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command,
            stop_before_measurement=True) as case:
        wire = RecoveryWire(case, runtime)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        result = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        issue = result["issue"]
        assert issue["reasonText"] == "单片机重启，未取得最终结果包"
        facts = case.store.list_native_delivery_issue_facts(issue["issueUid"])
        assert facts and not any(f["source_kind"] in {"MEASUREMENT", "ACTUATOR"} for f in facts)
        assert case.store.get_work_slot() == case.occupancy
        assert case.store.list_pending_events() == []


@pytest.mark.parametrize("point", ["link", "commit"])
def test_late_evidence_receipt_requires_atomic_result_and_issue_link(runtime, tmp_path, point):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        result = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        target = (sqlite3.SQLITE_INSERT, "native_delivery_issue_result") if point == "link" else (sqlite3.SQLITE_TRANSACTION, "COMMIT")
        case.store._conn.set_authorizer(lambda op, first, *_: sqlite3.SQLITE_DENY if (op, first) == target else sqlite3.SQLITE_OK)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                case.store.save_native_mcu_result(saved["payload"])
        finally:
            case.store._conn.set_authorizer(None)
        assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"]) is None
        assert case.store.list_native_delivery_issue_results(result["issue"]["issueUid"]) == []
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == result
        case.store.save_native_mcu_result(saved["payload"])
        assert len(case.store.list_native_delivery_issue_results(result["issue"]["issueUid"])) == 1


@pytest.mark.parametrize("change", ["device", "permit", "start"])
def test_archive_cannot_be_reused_by_another_identity(runtime, tmp_path, change):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        result = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        with pytest.raises(ValueError, match="identity"):
            owner.archive_delivery(replace(case.permit, permit_uid=str(uuid.uuid4())) if change == "permit" else case.permit,
                str(uuid.uuid4()) if change == "start" else case.start["mcuCommandUid"],
                device_name="other-device" if change == "device" else "device-1")
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == result


@pytest.mark.parametrize("failure", [None, "archive", "facts", "evidence", "version"])
def test_schema32_upgrade_is_atomic_and_never_reclassifies_existing_intents(runtime, tmp_path, failure):
    from edge_store import EdgeStore, CURRENT_SCHEMA_VERSION
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        intent = owner.evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
        with case.store.transaction() as conn:
            conn.execute("DROP TABLE native_recovery_close_retirement")
            conn.execute("DROP TABLE native_recovery_close_confirmation")
            conn.execute("DROP TABLE native_delivery_recovery_close")
            conn.execute("DROP TABLE native_delivery_issue_confirmation")
            conn.execute("DROP TABLE native_delivery_issue_report")
            conn.execute("DROP TABLE native_delivery_issue_result")
            conn.execute("DROP TABLE native_delivery_issue_fact")
            conn.execute("DROP TABLE native_delivery_issue")
            conn.execute("DELETE FROM schema_version WHERE version>32")
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store._open_connection()
        target = {"archive": (sqlite3.SQLITE_CREATE_TABLE, "native_delivery_issue"),
            "facts": (sqlite3.SQLITE_CREATE_TABLE, "native_delivery_issue_fact"),
            "evidence": (sqlite3.SQLITE_CREATE_TABLE, "native_delivery_issue_result"),
            "version": (sqlite3.SQLITE_INSERT, "schema_version")}.get(failure)
        if target:
            case.store._conn.set_authorizer(lambda op, first, *_: sqlite3.SQLITE_DENY if (op, first) == target else sqlite3.SQLITE_OK)
            with pytest.raises(sqlite3.DatabaseError):
                case.store.initialize()
            case.store.close()
            with sqlite3.connect(case.store.db_path) as conn:
                assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 32
                assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'native_delivery_issue%'").fetchone()[0] == 0
            case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        assert CURRENT_SCHEMA_VERSION == 39
        assert case.store.get_native_work_recovery_intent(intent["recovery_uid"]) == intent
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("corruption", ["verdict", "fact", "late_body"])
def test_restart_detects_corrupted_archival_evidence_instead_of_restoring_normal_business(runtime, tmp_path, corruption):
    from edge_store import EdgeStore
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = without_final_packet(case, wire)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        case.store.save_native_mcu_result(saved["payload"])
        with case.store.transaction() as conn:
            if corruption == "verdict":
                conn.execute("UPDATE native_delivery_issue SET evidence_json='{}'")
            elif corruption == "fact":
                conn.execute("DELETE FROM native_delivery_issue_fact WHERE fact_sequence=1")
            else:
                value = uart.decode_payload("WORK_RESULT", saved["payload"])
                value["finalWeightGrams"] += 1
                value["resultDigestSha256"] = uart.compute_result_digest(value)
                conn.execute("UPDATE native_mcu_result SET payload=?", (uart.encode_payload("WORK_RESULT", value),))
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        with pytest.raises(ValueError):
            case.store.initialize()


@pytest.mark.parametrize("entry", ["edge", "mcu", "fixed_frame"])
def test_legacy_completion_entries_cannot_bypass_the_archived_delivery_verdict(runtime, tmp_path, entry):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        owner = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now)
        result = owner.archive_delivery(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        uid = case.permit.work_uid
        with pytest.raises(ValueError, match="archived"):
            if entry == "fixed_frame":
                case.store.complete_fixed_frame_work(work_type="DELIVERY", work_uid=uid,
                    command_uid=case.permit.command_uid, command_result={}, context={"port_no":1}, observation={},
                    event_uid=str(uuid.uuid4()), event_type="DELIVERY_COMPLETE", event_payload={"sessionUid":uid},
                    device_name="device-1", target_type="DELIVERY_SESSION")
            else:
                method = case.store.create_edge_event if entry == "edge" else case.store.receive_mcu_event
                method(str(uuid.uuid4()), "DELIVERY_COMPLETE", {"sessionUid":uid}, work_uid=uid)
        assert owner.evaluate(case.permit, case.start["mcuCommandUid"]) == result
        assert case.store.list_pending_events() == [] and case.store.get_work_slot() == case.occupancy
