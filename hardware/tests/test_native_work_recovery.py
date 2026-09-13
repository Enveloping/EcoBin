"""Actual C work/results and boot handshakes, real Pi/permanent SQLite; no hardware."""
import uuid
import hashlib
import json
import sqlite3
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import pytest

import uart2_protocol as uart
from mcu_session import McuBootSession
from hardware.tests.native_autonomous_recovery_fixture import (
    AutonomousRecoveryWire,
    autonomous_active_case as executed_action_case,
)
from hardware.tests.test_mcu_simplified_execution import library, runtime
from hardware.tests.test_native_command_session import CSession, boot_response, command_response, mcu


RecoveryWire = AutonomousRecoveryWire


def test_recovery_wire_is_defined_by_neutral_autonomous_fixture():
    assert RecoveryWire.__module__ == "hardware.tests.native_autonomous_recovery_fixture"


def assert_start_only(case):
    """rc.23 recovery owns no Pi-side per-action authorization."""
    names = {row["message_name"] for row in case.store.list_native_commands()}
    assert ("START_CLEAN_OPERATION" if case.clean else "START_DELIVERY_SESSION") in names
    assert names.isdisjoint({"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR"})
    key = "clean:first-unlock" if case.clean else "delivery:first-open"
    assert case.store.get_native_action_by_key(case.permit.work_uid, key) is None


@pytest.fixture(params=[False, True], ids=["delivery", "clean"])
def active(runtime, tmp_path, request):
    with executed_action_case(runtime, tmp_path, clean_work=request.param) as case:
        case.wire = RecoveryWire(case, runtime)
        yield case


def test_saved_actual_complete_result_wins_after_confirmed_mcu_restart(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        wire = RecoveryWire(case, runtime)
        result = wire.finish_delivery()
        boot = wire.reset_mcu()
        assert boot.current_boot(wire.now) == 2
        before = len(wire.sent)
        from work_recovery import NativeWorkRecovery
        recovery = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        decision = recovery.evaluate(case.permit, case.start["mcuCommandUid"])
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert decision["result"]["payload"] == result["payload"]
        assert len(case.store.list_native_result_report_tasks()) == 1
        assert case.store.get_work_slot() == case.occupancy
        assert_start_only(case)
        assert len(wire.sent) == before  # no action, probe, receipt or cloud report from the evaluator


def test_confirmed_restart_without_complete_result_freezes_known_facts_once(runtime, tmp_path):
    from edge_store import EdgeStore
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        wire = RecoveryWire(case, runtime)
        boot = wire.reset_mcu()
        recovery = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        before = len(wire.sent)
        decision = recovery.evaluate(case.permit, case.start["mcuCommandUid"])
        assert decision["status"] == "RECOVERY_INTENT_RECORDED"
        intent = decision["intent"]
        evidence = intent["evidence"]
        assert evidence["reason"] == "MCU_RESTART_RESULT_UNAVAILABLE"
        assert evidence["dataLossClassified"] is False
        assert (evidence["sourceMcuBootId"], evidence["targetMcuBootId"]) == (1, 2)
        assert evidence["completeResultAtEvaluation"] == "ABSENT"
        facts = case.store.list_native_work_recovery_facts(intent["recovery_uid"])
        initial = next(row for row in facts if row["message_name"] == "WORK_PREOPEN_WEIGHT_READY")
        assert uart.decode_payload(initial["message_name"], initial["payload"])["reportedWeightGrams"] == 500
        assert not any(row["message_name"] == "WORK_POSTCLOSE_WEIGHT_READY" for row in facts)
        assert case.store.get_work_slot() == case.occupancy
        assert evidence["firstAction"] is None
        assert_start_only(case)
        assert case.store.list_native_result_report_tasks() == []
        assert case.store.list_pending_events() == []
        assert len(wire.sent) == before
        assert recovery.evaluate(case.permit, case.start["mcuCommandUid"]) == decision
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        assert case.store.get_native_work_recovery_intent(intent["recovery_uid"]) == intent
        boot = wire.handshake()
        recovery = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        assert recovery.evaluate(case.permit, case.start["mcuCommandUid"]) == decision
        assert len(case.store.list_native_work_recovery_intents(case.permit.work_uid)) == 1


@pytest.mark.parametrize("observation", ["none", "expired", "same_boot", "zero"])
def test_pi_restart_timeout_or_zero_does_not_create_data_loss_intent(active, observation):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    boot = McuBootSession(case.store, wire.write)
    expected = "WAIT_FOR_BOOT"
    if observation in {"same_boot", "expired"}:
        boot = wire.handshake()
        if observation == "expired":
            wire.now += 1000
        else:
            expected = "WAIT_FOR_ORIGINAL_WORK"
    elif observation == "zero":
        lib, endpoint, preparation, replies, _, sink, guard = wire.runtime
        replies.clear()
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        boot.poll(wire.now)
        assert boot.accept_frame(replies.pop(0), wire.now)  # leave bind reply undelivered
    before = len(wire.sent)
    result = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])
    assert result["status"] == expected
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    assert case.store.get_work_slot() == case.occupancy
    assert len(wire.sent) == before


def test_restart_retains_original_permit_without_requiring_optional_action_events(active):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    result = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])
    intent = result["intent"]
    assert intent["evidence"]["permit"]["permit_uid"] == case.permit.permit_uid
    facts = case.store.list_native_work_recovery_facts(intent["recovery_uid"])
    assert any(row["message_name"] in {"WORK_PREUNLOCK_WEIGHT_READY", "WORK_PREOPEN_WEIGHT_READY"}
        for row in facts)
    assert not any(row["message_name"] in {"CLEAN_LOCK_POWER_CHANGED", "DELIVERY_DOOR_COMMAND_RESULT"}
        for row in facts)
    assert not any(row["message_name"] == "CLEAN_COMPLETION_CONFIRMED" for row in facts)
    assert intent["evidence"]["firstAction"] is None
    assert_start_only(case)
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("kind", ["clean", "delivery_failed"])
def test_complete_clean_or_failed_measurement_result_is_not_reclassified_as_restart_loss(runtime, tmp_path, kind):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=kind == "clean") as case:
        wire = RecoveryWire(case, runtime)
        result = wire.finish_clean() if case.clean else wire.finish_delivery(unavailable=True)
        boot = wire.reset_mcu()
        decision = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert decision["result"]["payload"] == result["payload"]
        assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


def test_late_complete_result_after_intent_but_before_archive_takes_priority(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        wire = RecoveryWire(case, runtime)
        saved = wire.finish_delivery()
        # Storage boundary: model an undelivered complete UART result, not an archived issue.
        with case.store.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM native_result_report_outbox")
            conn.execute("DELETE FROM native_mcu_result")
        boot = wire.reset_mcu()
        owner = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        intent = owner.evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
        assert intent["evidence"]["dataLossClassified"] is False  # absence of final envelope is not loss of all measured data
        case.store.save_native_mcu_result(saved["payload"])
        result = owner.evaluate(case.permit, case.start["mcuCommandUid"])
        assert result["status"] == "COMPLETE_RESULT_AVAILABLE" and result["result"]["payload"] == saved["payload"]
        assert case.store.get_native_work_recovery_intent(intent["recovery_uid"]) == intent  # retains absence-at-the-time evidence
        assert len(case.store.list_native_result_report_tasks()) == 1
        assert case.store.get_work_slot() == case.occupancy
        assert case.store.list_pending_events() == []


@pytest.mark.parametrize("change", ["permit", "port", "kind", "dispatch"])
def test_rehashed_intent_cannot_replace_the_original_permanent_permit(active, change):
    from work_recovery import NativeWorkRecovery, canonical
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    intent = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
    changed = json.loads(intent["evidence_json"])
    if change == "permit":
        changed["permit"]["permit_uid"] = str(uuid.uuid4())
    elif change == "port":
        changed["portNo"] = 2
    elif change == "kind":
        changed["permit"]["work_type"] = "DELIVERY" if case.clean else "CLEAN"
    else:
        changed["originalStart"]["writeClaimed"] = False
    encoded = canonical(changed)
    with case.store.transaction(immediate=True) as conn:
        conn.execute("UPDATE native_work_recovery_intent SET evidence_json=?, evidence_sha256=? WHERE recovery_uid=?",
            (encoded, hashlib.sha256(encoded.encode("ascii")).hexdigest(), intent["recovery_uid"]))
    with pytest.raises(ValueError, match="permit|binding|identity|dispatch"):
        case.store.get_native_work_recovery_intent(intent["recovery_uid"])


def test_corrupt_result_work_projection_cannot_hide_the_original_complete_result(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        wire = RecoveryWire(case, runtime)
        wire.finish_delivery()
        with case.store.transaction(immediate=True) as conn:
            conn.execute("UPDATE native_mcu_result SET work_uid=?", (str(uuid.uuid4()),))
        boot = wire.reset_mcu()
        with pytest.raises(ValueError, match="result"):
            NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])
        assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


@pytest.mark.parametrize("change", ["permit", "work", "kind", "start"])
def test_other_work_or_permit_cannot_claim_original_occupancy(active, change):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    permit, uid = case.permit, case.start["mcuCommandUid"]
    if change == "permit":
        permit = replace(permit, permit_uid=str(uuid.uuid4()))
    elif change == "work":
        permit = replace(permit, work_uid=str(uuid.uuid4()))
    elif change == "kind":
        permit = replace(permit, work_type="DELIVERY" if case.clean else "CLEAN")
    else:
        uid = str(uuid.uuid4())
    with pytest.raises(ValueError):
        NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(permit, uid)
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    assert case.store.get_work_slot() == case.occupancy


def test_expired_boot_during_snapshot_rolls_back_the_entire_intent(active):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    times = iter((wire.now, wire.now + 1000))
    owner = NativeWorkRecovery(case.store, boot, clock=lambda: next(times))
    with pytest.raises(RuntimeError, match="expired"):
        owner.evaluate(case.permit, case.start["mcuCommandUid"])
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("point", ["intent", "facts", "commit"])
def test_sqlite_failure_does_not_leave_half_intent_or_release_occupancy(active, point):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    target = {"intent": (sqlite3.SQLITE_INSERT, "native_work_recovery_intent"),
        "facts": (sqlite3.SQLITE_INSERT, "native_work_recovery_fact"),
        "commit": (sqlite3.SQLITE_TRANSACTION, "COMMIT")}[point]
    case.store._conn.set_authorizer(lambda operation, first, *_:
        sqlite3.SQLITE_DENY if (operation, first) == target else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])
    finally:
        case.store._conn.set_authorizer(None)
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    assert case.store.get_work_slot() == case.occupancy
    assert_start_only(case)


@pytest.mark.parametrize("point", ["before_intent", "before_facts", "after_commit"])
def test_process_exit_preserves_only_whole_recovery_records(active, point):
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    assert boot.current_boot(wire.now) == 2  # actual C handshake before the child storage probe
    script = '''
import os, sqlite3, sys, json
from edge_store import EdgeStore
from job_safety import JobPermit
store=EdgeStore(sys.argv[1]); store.initialize()
target={"before_intent": (sqlite3.SQLITE_INSERT,"native_work_recovery_intent"),
        "before_facts": (sqlite3.SQLITE_INSERT,"native_work_recovery_fact")}.get(sys.argv[4])
def crash(operation, first, *args):
    if (operation, first)==target: os._exit(77)
    return sqlite3.SQLITE_OK
store._conn.set_authorizer(crash)
result=store.evaluate_native_work_recovery(JobPermit(**json.loads(sys.argv[2])),sys.argv[3],current_boot=lambda:2)
assert result["status"]=="RECOVERY_INTENT_RECORDED"
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-c", script, case.store.db_path, json.dumps(asdict(case.permit)),
        case.start["mcuCommandUid"], point], capture_output=True, text=True, timeout=15,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])) )
    assert run.returncode == 77, run.stderr
    rows = case.store.list_native_work_recovery_intents(case.permit.work_uid)
    assert len(rows) == int(point == "after_commit")
    if rows:
        assert rows[0]["evidence"]["knownFactCount"] == len(case.store.list_native_work_recovery_facts(rows[0]["recovery_uid"]))
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.list_pending_events() == []


def test_two_connections_prepare_one_immutable_intent(active):
    from edge_store import EdgeStore
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    second = EdgeStore(case.store.db_path)
    second.initialize()
    other_boot = McuBootSession(second, wire.write)
    other_boot.poll(wire.now)
    wire.pump(other_boot)
    owners = [NativeWorkRecovery(case.store, boot, clock=lambda: wire.now),
        NativeWorkRecovery(second, other_boot, clock=lambda: wire.now)]
    try:
        with ThreadPoolExecutor(2) as pool:
            decisions = list(pool.map(lambda owner: owner.evaluate(case.permit, case.start["mcuCommandUid"]), owners))
        assert decisions[0] == decisions[1]
        assert len(case.store.list_native_work_recovery_intents(case.permit.work_uid)) == 1
        assert case.store.get_work_slot() == case.occupancy
    finally:
        second.close()


def test_complete_result_racing_intent_remains_one_pending_classification(runtime, tmp_path):
    from edge_store import EdgeStore
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        wire = RecoveryWire(case, runtime)
        saved = wire.finish_delivery()
        with case.store.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM native_result_report_outbox")
            conn.execute("DELETE FROM native_mcu_result")
        boot = wire.reset_mcu()
        owner = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now)
        second = EdgeStore(case.store.db_path)
        second.initialize()
        try:
            with ThreadPoolExecutor(2) as pool:
                evaluating = pool.submit(owner.evaluate, case.permit, case.start["mcuCommandUid"])
                receiving = pool.submit(second.save_native_mcu_result, saved["payload"])
                assert evaluating.result()["status"] in {"COMPLETE_RESULT_AVAILABLE", "RECOVERY_INTENT_RECORDED"}
                receiving.result()
            assert owner.evaluate(case.permit, case.start["mcuCommandUid"])["status"] == "COMPLETE_RESULT_AVAILABLE"
            assert len(case.store.list_native_result_report_tasks()) == 1
            assert len(case.store.list_native_work_recovery_intents(case.permit.work_uid)) <= 1
            assert case.store.get_work_slot() == case.occupancy and case.store.list_pending_events() == []
        finally:
            second.close()


def test_missing_frozen_fact_is_detected_on_pi_restart(active):
    from edge_store import EdgeStore
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    boot = wire.reset_mcu()
    intent = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
    with case.store.transaction(immediate=True) as conn:
        conn.execute("DELETE FROM native_work_recovery_fact WHERE recovery_uid=? AND fact_sequence=1", (intent["recovery_uid"],))
    case.store.close()
    case.store = EdgeStore(case.store.db_path)
    with pytest.raises(ValueError, match="fact custody"):
        case.store.initialize()


@pytest.mark.parametrize("stage", ["prepared", "rejected", "unknown"])
def test_start_dispatch_knowledge_is_preserved_without_inventing_user_activity(tmp_path, mcu, stage):
    from edge_store import EdgeStore
    from hardware.tests.test_command_processor import make_real_job_safety
    from hardware.tests.test_job_safety import _command
    from hardware.tests.test_mcu_work_preparation import start_values
    from work_recovery import NativeWorkRecovery
    import ctypes as c

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    updater, safety = make_real_job_safety(tmp_path)
    session, sent = CSession(), []
    write = lambda frame: sent.append(frame) or len(frame)
    try:
        boot = McuBootSession(store, write)
        boot.poll(0)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 1)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 2)
        command = _command()
        work = command["payload"]["sessionUid"]
        permit = safety.request_job(command, work_type="DELIVERY", work_uid=work)
        safety.begin_job(permit, begin_uid=str(uuid.uuid4()), digest=permit.request_digest_sha256)
        store.acquire_work_slot("DELIVERY", work, 1, {"phase": "NATIVE_PREPARING",
            "job_safety": asdict(permit) | {"begin_uid": permit.work_uid}})
        values = start_values(targetMcuBootId=1, sessionUid=work)
        identity = {"mcuCommandUid", "targetMcuBootId", "commandSequence", "commandDigestSha256"}
        record = store.prepare_native_command("START_DELIVERY_SESSION", values["mcuCommandUid"], 1,
            {key: value for key, value in values.items() if key not in identity})
        if stage != "prepared":
            store.claim_native_command_write(record["command_uid"])
        if stage == "rejected":
            frame = uart.encode_frame(record["message_name"], 1, record["payload"])
            response, execute = command_response(mcu, session, frame, business_error=uart.REGISTRY["enums"]["NackError"]["values"]["BUSY"])
            assert execute == 0
            decoded = uart.decode_frame(response, sender_role="MCU")
            store.save_native_command_observation(decoded["messageName"], decoded["payload"])
        mcu.McuSession_Init(c.byref(session))
        boot.poll(1000)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 1001)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 1002)
        result = NativeWorkRecovery(store, boot, clock=lambda: 1002).evaluate(permit, record["command_uid"])
        assert result["status"] == {"prepared": "START_NOT_DISPATCHED", "rejected": "START_REJECTED", "unknown": "RECOVERY_INTENT_RECORDED"}[stage]
        if stage == "unknown":
            evidence = result["intent"]["evidence"]
            assert evidence["originalStart"]["writeClaimed"] and evidence["originalStart"]["decisionOutcome"] is None
            assert evidence["firstAction"] is None and evidence["knownFactCount"] == 1
            assert evidence["dataLossClassified"] is False
        else:
            assert store.list_native_work_recovery_intents(work) == []
        assert store.get_work_slot()["work_uid"] == work
        assert store.list_pending_events() == []
    finally:
        store.close()
        updater.close()


@pytest.mark.parametrize("failure", [None, "intent", "facts", "version"])
def test_schema29_upgrade_is_atomic_and_preserves_boot_work_and_commands(active, failure):
    from edge_store import EdgeStore, CURRENT_SCHEMA_VERSION
    case = active
    boot = case.store.get_native_boot_observation(1)
    commands = case.store.list_native_commands()
    with case.store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_work_recovery_fact")
        conn.execute("DROP TABLE native_work_recovery_intent")
        conn.execute("DELETE FROM schema_version WHERE version>=30")
    case.store.close()
    case.store = EdgeStore(case.store.db_path)
    case.store._open_connection()
    target = {"intent": (sqlite3.SQLITE_CREATE_TABLE, "native_work_recovery_intent"),
        "facts": (sqlite3.SQLITE_CREATE_TABLE, "native_work_recovery_fact"),
        "version": (sqlite3.SQLITE_INSERT, "schema_version")}.get(failure)
    if target:
        case.store._conn.set_authorizer(lambda operation, first, *_:
            sqlite3.SQLITE_DENY if (operation, first) == target else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            case.store.initialize()
        case.store.close()
        with sqlite3.connect(case.store.db_path) as conn:
            assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 29
            assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('native_work_recovery_fact','native_work_recovery_intent')").fetchone()[0] == 0
        case.store = EdgeStore(case.store.db_path)
    case.store.initialize()
    assert case.store._conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    assert case.store.get_native_boot_observation(1) == boot
    assert case.store.list_native_commands() == commands
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []


def test_result_identity_conflict_blocks_reclassification_and_retains_both_bodies(runtime, tmp_path):
    from work_recovery import NativeWorkRecovery
    with executed_action_case(runtime, tmp_path, clean_work=False) as case:
        wire = RecoveryWire(case, runtime)
        saved = wire.finish_delivery()
        value = uart.decode_payload("WORK_RESULT", saved["payload"])
        value["finalWeightGrams"] += 1
        value["resultDigestSha256"] = uart.compute_result_digest(value)
        other = uart.encode_payload("WORK_RESULT", value)
        with pytest.raises(ValueError, match="conflict"):
            case.store.save_native_mcu_result(other)
        boot = wire.reset_mcu()
        with pytest.raises(ValueError, match="conflict"):
            NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])
        assert case.store.get_native_mcu_result(1, saved["result_sequence"])["payload"] == saved["payload"]
        assert case.store.list_native_mcu_result_conflicts()[0]["payload"] == other
        assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
        assert len(case.store.list_native_result_report_tasks()) == 1


def test_subsequent_mcu_restart_creates_new_intent_without_rewriting_previous_one(active):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    first = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
    second = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
    assert first["recovery_uid"] != second["recovery_uid"]
    assert first["target_mcu_boot_id"] == 2 and second["target_mcu_boot_id"] == 3
    assert case.store.get_native_work_recovery_intent(first["recovery_uid"]) == first
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == [first, second]
    assert case.store.get_work_slot() == case.occupancy


def test_frozen_fact_pagination_returns_all_original_bytes_without_repeating_rows(active):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    intent = NativeWorkRecovery(case.store, wire.reset_mcu(), clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
    complete = case.store.list_native_work_recovery_facts(intent["recovery_uid"])
    rows, cursor = [], 0
    while page := case.store.list_native_work_recovery_facts(intent["recovery_uid"], after_sequence=cursor, limit=2):
        rows.extend(page)
        cursor = page[-1]["fact_sequence"]
    assert rows == complete and len(rows) == intent["evidence"]["knownFactCount"]
    for limit in (0, 1001, True):
        with pytest.raises(ValueError):
            case.store.list_native_work_recovery_facts(intent["recovery_uid"], limit=limit)


def test_start_only_recovery_retains_context_when_later_result_is_missing(active):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    assert_start_only(case)
    context = case.store.get_work_slot()["context"] | {"operatorUid": str(uuid.uuid4()), "reservedBagUid": str(uuid.uuid4())}
    assert case.store.update_work_context(case.permit.work_uid, context)
    occupied = case.store.get_work_slot()
    boot = wire.reset_mcu()
    intent = NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])["intent"]
    assert intent["evidence"]["firstAction"] is None
    assert_start_only(case)
    assert case.store.get_work_slot() == occupied  # original people/bag context is retained, not reconstructed
    assert case.store.list_pending_events() == []


def test_corrupt_measurement_custody_is_not_rehashed_into_recovery_evidence(active):
    from work_recovery import NativeWorkRecovery
    case, wire = active, active.wire
    table, sequence = "native_measurement_event", 1
    row = case.store.get_native_measurement_event(1, sequence)
    field = "reportedWeightGrams"
    value = uart.decode_payload(row["message_name"], row["payload"])
    value[field] += 1
    changed = uart.encode_payload(row["message_name"], value)
    with case.store.transaction(immediate=True) as conn:
        conn.execute(f"UPDATE {table} SET payload=? WHERE mcu_boot_id=1 AND event_sequence=?", (changed, sequence))
    boot = wire.reset_mcu()
    with pytest.raises(ValueError):
        NativeWorkRecovery(case.store, boot, clock=lambda: wire.now).evaluate(case.permit, case.start["mcuCommandUid"])
    assert case.store.list_native_work_recovery_intents(case.permit.work_uid) == []
    assert case.store.get_work_slot() == case.occupancy
