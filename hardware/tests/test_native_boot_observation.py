"""Fresh boot witnesses from real MCU C and durable Pi SQLite, no physical I/O."""
import ctypes as c
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from edge_store import EdgeStore
from mcu_session import McuBootSession
import uart2_protocol as uart
from hardware.tests.test_native_command_session import (
    CSession, CBindReply, boot_response, command_response, mcu, ready, open_fields, COMMAND_UID, WORK_UID,
)  # noqa: F401


@pytest.fixture
def pending(tmp_path, mcu):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    session, sent = CSession(), []
    write = lambda frame: sent.append(frame) or len(frame)
    boot = McuBootSession(store, write)
    boot.poll(0)
    boot.accept_frame(boot_response(mcu, session, sent[-1]), 1)
    boot.accept_frame(boot_response(mcu, session, sent[-1]), 2)
    store.acquire_work_slot("DELIVERY", WORK_UID, 1, {"phase": "WAITING_FIRST_OPEN"})
    store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, 1, open_fields())
    store.claim_native_command_write(COMMAND_UID)
    case = SimpleNamespace(store=store, session=session, sent=sent, write=write, boot=boot)
    try:
        yield case
    finally:
        case.store.close()


def reset_offer(case, mcu):
    mcu.McuSession_Init(c.byref(case.session))
    case.boot.poll(1000)
    assert case.boot.accept_frame(boot_response(mcu, case.session, case.sent[-1]), 1001)
    return boot_response(mcu, case.session, case.sent[-1])


def test_first_positive_boot_reply_survives_pi_restart_without_becoming_fresh(tmp_path, mcu):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    session, sent = CSession(), []
    write = lambda frame: sent.append(frame) or len(frame)
    try:
        boot = McuBootSession(store, write)
        boot.poll(0)
        assert boot.accept_frame(boot_response(mcu, session, sent[-1]), 1)
        assert store.get_native_boot_observation(1) is None  # zero only offers an ID
        positive = boot_response(mcu, session, sent[-1])
        assert boot.accept_frame(positive, 2)
        witness = store.get_native_boot_observation(1)
        assert witness["message_name"] == "BIND_BOOT_REPLY"
        assert witness["payload"] == uart.decode_frame(positive, sender_role="MCU")["payload"]
        assert witness["probe_id"] == 1
        assert boot.current_boot(2) == 1
        store.close()
        store = EdgeStore(path)
        store.initialize()
        boot = McuBootSession(store, write)
        assert store.get_native_boot_observation(1) == witness
        assert boot.current_boot(0) is None
        assert not boot.accept_frame(positive, 0)
        boot.poll(0)
        assert boot.accept_frame(boot_response(mcu, session, sent[-1]), 1)
        assert boot.current_boot(1) == 1
        assert store.get_native_boot_observation(1) == witness  # bounded first witness
    finally:
        store.close()


def test_foreign_positive_boot_is_consumed_without_becoming_owned(tmp_path, mcu):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    session, sent = CSession(), []
    try:
        previous, bound = c.c_uint64(), CBindReply()
        assert mcu.McuSession_Probe(c.byref(session), 1, c.byref(previous))
        assert mcu.McuSession_Bind(c.byref(session), 1, 99, c.byref(bound))
        boot = McuBootSession(store, lambda frame: sent.append(frame) or len(frame))
        boot.poll(0)
        response = boot_response(mcu, session, sent[-1])
        assert boot.accept_frame(response, 1)
        assert boot.current_boot(1) is None
        assert not boot.accept_frame(response, 2)
        assert store.get_native_boot_observation(99) is None
        assert store.get_native_boot(99) is None
        assert len(sent) == 1
    finally:
        store.close()


def test_pristine_production_ledger_claims_one_fresh_factory_namespace_boot(tmp_path, mcu):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    session, sent = CSession(), []
    write = lambda frame: sent.append(frame) or len(frame)
    factory_boot = 8_000_000_000_000_123
    try:
        previous, bound = c.c_uint64(), CBindReply()
        assert mcu.McuSession_Probe(c.byref(session), 77, c.byref(previous))
        assert previous.value == 0
        assert mcu.McuSession_Bind(
            c.byref(session),
            77,
            factory_boot,
            c.byref(bound),
        )
        boot = McuBootSession(store, write)
        probe_id = boot.poll(0)
        response = boot_response(mcu, session, sent[-1])
        assert boot.accept_frame(response, 1)
        assert boot.current_boot(1) == factory_boot
        assert store.get_native_boot(factory_boot)["probe_id"] == probe_id
        witness = store.get_native_boot_observation(factory_boot)
        assert witness["probe_id"] == probe_id
        assert witness["payload"] == uart.decode_frame(
            response,
            sender_role="MCU",
        )["payload"]

        factory_work_uid = str(uuid.uuid4())
        assert store.recognize_factory_released_result_baseline(
            factory_boot, 9, factory_work_uid
        )
        assert store.recognize_factory_released_result_baseline(
            factory_boot, 9, factory_work_uid
        )
        assert not store.recognize_factory_released_result_baseline(
            factory_boot, 10, factory_work_uid
        )
        assert not store.recognize_factory_released_result_baseline(
            factory_boot, 9, str(uuid.uuid4())
        )

        # A later real MCU reset allocates above the imported high namespace;
        # importing the factory fact never lets the allocator move backward.
        mcu.McuSession_Init(c.byref(session))
        boot.poll(1000)
        assert boot.accept_frame(
            boot_response(mcu, session, sent[-1]),
            1001,
        )
        bind = uart.decode_payload(
            "BIND_BOOT",
            uart.decode_frame(sent[-1], sender_role="EDGE")["payload"],
        )
        assert bind["proposedMcuBootId"] == factory_boot + 1
    finally:
        store.close()


def test_confirmed_mcu_restart_retires_transport_wait_without_resolving_old_action(tmp_path, mcu):
    from mcu_configuration import NativeMcuConfiguration
    from hardware.tests.test_native_configuration import inputs

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    session, sent = CSession(), []
    write = lambda frame: sent.append(frame) or len(frame)
    try:
        assert store.acquire_work_slot("DELIVERY", WORK_UID, 1, {"phase": "WAITING_FIRST_OPEN"})
        occupancy = store.get_work_slot()
        boot = McuBootSession(store, write)
        boot.poll(0)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 1)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 2)
        old = store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, 1, open_fields())
        assert store.claim_native_command_write(COMMAND_UID)
        mcu.McuSession_Init(c.byref(session))  # actual MCU RAM reset, no invented reply
        boot.poll(1000)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 1001)
        assert boot.current_boot(1001) is None  # zero/offer alone cannot retire anything
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 1002)
        assert boot.current_boot(1002) == 2
        name, payload = NativeMcuConfiguration(**inputs()).encode_part(1,
            application_uid=str(uuid.uuid4()), mcu_command_uid=str(uuid.uuid4()),
            target_mcu_boot_id=2, command_sequence=1)
        fields = uart.decode_payload(name, payload)
        uid = fields.pop("mcuCommandUid")
        for key in ("targetMcuBootId", "commandSequence", "commandDigestSha256"):
            fields.pop(key)
        prepared = store.prepare_native_command(name, uid, 2, fields)
        assert prepared["command_sequence"] == 1 and prepared["write_claimed"] == 0
        retired = store.get_native_command(COMMAND_UID)
        assert retired["boot_retired"] == 1
        assert retired["payload"] == old["payload"]
        assert retired["write_claimed"] == 1 and retired["decision_outcome"] is None
        assert not store.claim_native_command_write(COMMAND_UID)
        with pytest.raises(RuntimeError, match="unresolved"):
            store.prepare_native_command(name, str(uuid.uuid4()), 2, fields)
        store.close()
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()  # two unknown rows across boots must survive schema recheck
        assert store.get_native_command(COMMAND_UID) == retired
        assert store.get_native_command(uid) == prepared
        assert store.get_work_slot() == occupancy
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["timeout", "same_boot", "zero", "stale", "wrong_probe", "bind_mismatch"])
def test_absence_of_fresh_positive_new_boot_preserves_old_pending_command(pending, mcu, mode):
    case = pending
    original = case.store.get_native_command(COMMAND_UID)
    if mode in {"timeout", "same_boot"}:
        case.boot = McuBootSession(case.store, case.write)  # Pi-only restart
        assert case.boot.current_boot(0) is None
        case.boot.poll(0)
        if mode == "same_boot":
            case.boot.accept_frame(boot_response(mcu, case.session, case.sent[-1]), 1)
            assert case.boot.current_boot(1) == 1
        else:
            assert case.boot.current_boot(1000) is None
    else:
        response = reset_offer(case, mcu)
        if mode == "stale":
            assert not case.boot.accept_frame(response, 2000)
        elif mode == "wrong_probe":
            decoded = uart.decode_frame(response, sender_role="MCU")
            values = uart.decode_payload("BIND_BOOT_REPLY", decoded["payload"])
            values["probeId"] += 1
            wrong = uart.encode_frame("BIND_BOOT_REPLY", 1, uart.encode_payload("BIND_BOOT_REPLY", values))
            assert not case.boot.accept_frame(wrong, 1002)
        elif mode == "bind_mismatch":
            mcu.McuSession_Init(c.byref(case.session))
            mismatch = boot_response(mcu, case.session, case.sent[-1])
            assert case.boot.accept_frame(mismatch, 1002)
            assert case.boot.current_boot(1002) is None
    assert case.store.get_native_command(COMMAND_UID) == original
    assert case.store.get_native_boot_observation(2) is None
    with pytest.raises(RuntimeError, match="unresolved"):
        case.store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", str(uuid.uuid4()), 1, open_fields())
    assert case.store.get_work_slot()["work_uid"] == WORK_UID
    assert case.store.list_native_result_report_tasks() == []


def test_binding_refusal_records_actual_owned_boot_not_proposed_boot(pending, mcu):
    case = pending
    old = case.store.get_native_command(COMMAND_UID)
    witness = case.store.get_native_boot_observation(1)
    # A second owner has an outstanding offer, but this MCU is already bound.
    probe = case.store.reserve_native_query_id()
    offered = case.store.reserve_native_boot_id(probe)
    request = uart.encode_frame("BIND_BOOT", 1,
        uart.encode_payload("BIND_BOOT", {"probeId": probe, "proposedMcuBootId": offered}))
    reply = uart.decode_frame(boot_response(mcu, case.session, request), sender_role="MCU")
    values = uart.decode_payload(reply["messageName"], reply["payload"])
    assert values["mcuBootId"] == 1 and offered == 2
    assert case.store.save_native_boot_observation(reply["messageName"], reply["payload"])
    assert case.store.get_native_boot_observation(1) == witness
    assert case.store.get_native_boot_observation(2) is None
    assert case.store.get_native_command(COMMAND_UID) == old


def test_late_old_acceptance_is_retained_after_restart_without_reopening_transport(pending, mcu):
    case = pending
    command = case.store.get_native_command(COMMAND_UID)
    request = uart.encode_frame(command["message_name"], 1, command["payload"])
    delayed, executed = command_response(mcu, case.session, request)
    assert executed == 1
    positive = reset_offer(case, mcu)
    assert case.boot.accept_frame(positive, 1002)
    decoded = uart.decode_frame(delayed, sender_role="MCU")
    assert case.store.save_native_command_observation(decoded["messageName"], decoded["payload"])
    old = case.store.get_native_command(COMMAND_UID)
    assert old["boot_retired"] == 1 and old["decision_outcome"] == "ACCEPTED"
    assert not case.store.claim_native_command_write(COMMAND_UID)
    assert case.store.list_native_command_observations(COMMAND_UID)[0]["payload"] == decoded["payload"]
    assert case.store.get_work_slot()["work_uid"] == WORK_UID


@pytest.mark.parametrize("table", ["native_mcu_boot_observation", "native_mcu_command"])
def test_failed_witness_or_retirement_commit_consumes_reply_and_rolls_back_both(pending, mcu, table):
    case = pending
    positive = reset_offer(case, mcu)
    original = case.store.get_native_command(COMMAND_UID)
    operation = sqlite3.SQLITE_INSERT if table == "native_mcu_boot_observation" else sqlite3.SQLITE_UPDATE
    case.store._conn.set_authorizer(lambda action, name, *_:
        sqlite3.SQLITE_DENY if (action, name) == (operation, table) else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            case.boot.accept_frame(positive, 1002)
    finally:
        case.store._conn.set_authorizer(None)
    assert case.boot.current_boot(1002) is None
    assert not case.boot.accept_frame(positive, 1003)
    assert case.store.get_native_command(COMMAND_UID) == original
    assert case.store.get_native_boot_observation(2) is None
    case.boot.poll(2000)
    assert case.boot.accept_frame(boot_response(mcu, case.session, case.sent[-1]), 2001)
    assert case.boot.current_boot(2001) == 2
    assert case.store.get_native_boot_observation(2)["message_name"] == "BOOT_PROBE_REPLY"
    assert case.store.get_native_command(COMMAND_UID)["boot_retired"] == 1


@pytest.mark.parametrize("point", ["before_witness", "before_retirement", "after_commit"])
def test_process_exit_keeps_witness_current_boot_and_retirement_atomic(pending, mcu, point):
    case = pending
    positive = reset_offer(case, mcu)
    raw = uart.decode_frame(positive, sender_role="MCU")["payload"]
    script = '''
import os, sqlite3, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1]); store.initialize()
point = sys.argv[3]
target = {"before_witness": (sqlite3.SQLITE_INSERT, "native_mcu_boot_observation"),
          "before_retirement": (sqlite3.SQLITE_UPDATE, "native_mcu_command")}.get(point)
def fail(action, table, *args):
    if (action, table) == target: os._exit(77)
    return sqlite3.SQLITE_OK
store._conn.set_authorizer(fail)
assert store.save_native_boot_observation("BIND_BOOT_REPLY", bytes.fromhex(sys.argv[2]))
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-c", script, case.store.db_path, raw.hex(), point],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
        capture_output=True, text=True, timeout=15)
    assert run.returncode == 77, run.stderr
    committed = point == "after_commit"
    assert (case.store.get_native_boot_observation(2) is not None) == committed
    assert case.store.get_native_command(COMMAND_UID)["boot_retired"] == int(committed)
    # Legacy numeric recognition cannot move backward after a committed witness.
    assert case.store.recognize_native_boot_id(1) == (not committed)
    assert case.store.get_native_command(COMMAND_UID)["decision_outcome"] is None
    assert case.store.get_work_slot()["work_uid"] == WORK_UID


def test_retired_command_without_its_newer_boot_witness_cannot_restart(pending, mcu):
    case = pending
    assert case.boot.accept_frame(reset_offer(case, mcu), 1002)
    with case.store.transaction(immediate=True) as conn:
        conn.execute("DELETE FROM native_mcu_boot_observation WHERE boot_id=2")
    case.store.close()
    case.store = EdgeStore(case.store.db_path)
    with pytest.raises(ValueError, match="retirement.*witness"):
        case.store.initialize()


@pytest.mark.parametrize("failure", [None, "witness", "index", "version"])
def test_schema28_upgrade_preserves_unknown_command_and_never_invents_witness(pending, failure):
    case = pending
    original = case.store.get_native_command(COMMAND_UID)
    occupancy = case.store.get_work_slot()
    with case.store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_mcu_boot_observation")
        conn.execute("DROP INDEX native_mcu_one_pending_command")
        conn.execute("ALTER TABLE native_mcu_command DROP COLUMN boot_retired")
        conn.execute("CREATE UNIQUE INDEX native_mcu_one_pending_command ON native_mcu_command ((1)) WHERE decision_outcome IS NULL")
        conn.execute("DELETE FROM schema_version WHERE version>=29")
    case.store.close()
    case.store = EdgeStore(case.store.db_path)
    case.store._open_connection()
    target = {"witness": (sqlite3.SQLITE_CREATE_TABLE, "native_mcu_boot_observation"),
        "index": (sqlite3.SQLITE_CREATE_INDEX, "native_mcu_one_pending_command"),
        "version": (sqlite3.SQLITE_INSERT, "schema_version")}.get(failure)
    if target:
        case.store._conn.set_authorizer(lambda action, name, *_:
            sqlite3.SQLITE_DENY if (action, name) == target else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            case.store.initialize()
        case.store.close()
        with sqlite3.connect(case.store.db_path) as conn:
            assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 28
            assert "boot_retired" not in {row[1] for row in conn.execute("PRAGMA table_info(native_mcu_command)")}
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='native_mcu_boot_observation'").fetchone() is None
        case.store = EdgeStore(case.store.db_path)
    case.store.initialize()
    assert case.store.get_native_command(COMMAND_UID) == original
    assert case.store.get_native_boot_observation(1) is None
    assert case.store.get_work_slot() == occupancy
    with pytest.raises(RuntimeError, match="unresolved"):
        case.store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", str(uuid.uuid4()), 1, open_fields())


def test_new_boot_transport_capacity_does_not_override_real_permanent_action_lock(ready, tmp_path, mcu):
    from hardware.tests.test_command_processor import make_real_job_safety
    from hardware.tests.test_job_safety import _command
    from job_safety import JobSafetyError, PhysicalAction, action_digest
    from mcu_session import McuCommandDispatcher, NativePhysicalActionGate

    store, session, sent, write, boot = ready
    updater, safety = make_real_job_safety(tmp_path)
    command = _command()
    command["payload"]["sessionUid"] = WORK_UID
    permit = safety.request_job(command, work_type="DELIVERY", work_uid=WORK_UID)
    safety.begin_job(permit, begin_uid=str(uuid.uuid4()), digest=permit.request_digest_sha256)
    store.acquire_work_slot("DELIVERY", WORK_UID, 1, {"phase": "WAITING_FIRST_OPEN"})
    def gate_for(record, key, now):
        digest = action_digest(work_uid=WORK_UID, command_uid=permit.command_uid,
            action_key=key, action_kind=record["message_name"], payload={"nativeUartPayloadHex": record["payload"].hex()})
        action = PhysicalAction(record["command_uid"], str(uuid.uuid4()), key, record["message_name"], digest)
        return NativePhysicalActionGate(safety, permit, action, store=store,
            revalidate=lambda _: None, deadline_ms=5000, clock=lambda: now)
    try:
        old = store.get_native_command(COMMAND_UID)
        dispatch = McuCommandDispatcher(store, boot, write, arm=gate_for(old, "delivery:first-open", 2), clock=lambda: 2)
        assert dispatch.send_once(COMMAND_UID)
        case = SimpleNamespace(store=store, session=session, sent=sent, write=write, boot=boot)
        assert boot.accept_frame(reset_offer(case, mcu), 1002)
        new = store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", str(uuid.uuid4()), 2, open_fields())
        count = len(sent)
        dispatch = McuCommandDispatcher(store, boot, write,
            arm=gate_for(new, "delivery:unrelated-open", 1002), clock=lambda: 1002)
        with pytest.raises(JobSafetyError):
            dispatch.send_once(new["command_uid"])
        assert len(sent) == count and store.get_native_command(new["command_uid"])["write_claimed"] == 0
        assert safety.get_physical_action(COMMAND_UID)["state"] == "ARMED"
        assert store.get_native_command(COMMAND_UID)["decision_outcome"] is None
        assert store.get_work_slot()["work_uid"] == WORK_UID
        assert store.list_native_result_report_tasks() == []
    finally:
        updater.close()


def test_retired_unwritten_command_cannot_arm_through_stale_in_memory_boot_owner(ready, mcu):
    from mcu_session import McuCommandDispatcher

    store, session, sent, write, old_boot = ready
    mcu.McuSession_Init(c.byref(session))
    new_boot = McuBootSession(store, write)
    new_boot.poll(0)
    new_boot.accept_frame(boot_response(mcu, session, sent[-1]), 1)
    new_boot.accept_frame(boot_response(mcu, session, sent[-1]), 2)
    assert store.get_native_command(COMMAND_UID)["boot_retired"] == 1
    assert old_boot.current_boot(2) == 1  # stale owner has not polled again
    count = len(sent)
    dispatch = McuCommandDispatcher(store, old_boot, write,
        arm=lambda _: pytest.fail("retired command must not reach ARM"), clock=lambda: 2)
    assert not dispatch.send_once(COMMAND_UID)
    assert len(sent) == count
    assert store.get_native_command(COMMAND_UID)["write_claimed"] == 0
