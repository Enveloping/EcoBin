"""Native candidate: real SQLite and MCU C; no physical UART or actuators."""
import ctypes as c
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import uuid
from pathlib import Path

import pytest
import uart2_protocol as uart

from edge_store import EdgeStore

ROOT = Path(__file__).resolve().parents[2]
COMMAND_UID = "11111111-1111-4111-8111-111111111111"
WORK_UID = "22222222-2222-4222-8222-222222222222"


def open_fields():
    return {"sessionUid": WORK_UID, "portNo": 1,
        "firstPreOpenMeasurementUid": "33333333-3333-4333-8333-333333333333",
        "parentStartCommandUid": "44444444-4444-4444-8444-444444444444",
        "remainingStartAuthorizationMs": 5000}


class CCommand(c.Structure):
    _fields_ = [("boot", c.c_uint64), ("sequence", c.c_uint32), ("uid", c.c_uint8 * 16), ("digest", c.c_uint8 * 32)]


class CSession(c.Structure):
    _fields_ = [("boot", c.c_uint64), ("probe", c.c_uint64), ("command", CCommand), ("highest", c.c_uint32), ("error", c.c_uint16)]


class CBindReply(c.Structure):
    _fields_ = [("boot", c.c_uint64), ("status", c.c_uint8)]


class CDecision(c.Structure):
    _fields_ = [("command", CCommand), ("boot", c.c_uint64), ("highest", c.c_uint32),
                ("error", c.c_uint16), ("outcome", c.c_uint8), ("execute", c.c_uint8)]


@pytest.fixture
def mcu(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    signatures = {
        "McuSession_Init": (None, [c.POINTER(CSession)]),
        "McuSession_Probe": (c.c_uint8, [c.POINTER(CSession), c.c_uint64, c.POINTER(c.c_uint64)]),
        "McuSession_Bind": (c.c_uint8, [c.POINTER(CSession), c.c_uint64, c.c_uint64, c.POINTER(CBindReply)]),
        "McuSession_ReceiveCommand": (c.c_uint8, [c.POINTER(CSession), c.POINTER(CCommand), c.c_uint16, c.POINTER(CDecision)]),
        "McuSession_QueryCommand": (c.c_uint8, [c.POINTER(CSession), c.POINTER(CCommand), c.POINTER(CDecision)]),
    }
    path = tmp_path / "session.dll"
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        str(ROOT / "hardware_mcu/USER/mcu_session.c"), *["-Wl,/EXPORT:" + name for name in signatures],
        "-o", str(path)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    library = c.CDLL(str(path))
    for name, (restype, argtypes) in signatures.items():
        fn = getattr(library, name)
        fn.restype, fn.argtypes = restype, argtypes
    return library


def boot_response(mcu, session, request):
    decoded = uart.decode_frame(request, sender_role="EDGE")
    name = decoded["messageName"]
    values = uart.decode_payload(name, decoded["payload"])
    if name == "BOOT_PROBE":
        boot = c.c_uint64()
        assert mcu.McuSession_Probe(c.byref(session), values["probeId"], c.byref(boot))
        values["mcuBootId"] = boot.value
        reply_name = "BOOT_PROBE_REPLY"
    else:
        assert name == "BIND_BOOT"
        result = CBindReply()
        assert mcu.McuSession_Bind(c.byref(session), values["probeId"], values["proposedMcuBootId"], c.byref(result))
        values |= {"mcuBootId": result.boot, "status": result.status}
        reply_name = "BIND_BOOT_REPLY"
    return uart.encode_frame(reply_name, 1, uart.encode_payload(reply_name, values))


def command_response(mcu, session, request, *, business_error=0):
    decoded = uart.decode_frame(request, sender_role="EDGE")
    name = decoded["messageName"]
    values = uart.decode_payload(name, decoded["payload"])
    identity = {key: values[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}
    command = CCommand(values["targetMcuBootId"], values["commandSequence"],
        (c.c_uint8 * 16).from_buffer_copy(uuid.UUID(values["mcuCommandUid"]).bytes),
        (c.c_uint8 * 32).from_buffer_copy(bytes.fromhex(values["commandDigestSha256"])))
    result = CDecision()
    if name == "QUERY_COMMAND":
        assert mcu.McuSession_QueryCommand(c.byref(session), c.byref(command), c.byref(result))
        identity |= {"queryId": values["queryId"], "highestCommandSequence": result.highest}
        reply_name = "COMMAND_QUERY_RESULT"
    else:
        assert mcu.McuSession_ReceiveCommand(c.byref(session), c.byref(command), business_error, c.byref(result))
        reply_name = "COMMAND_DECISION"
    identity |= {"currentMcuBootId": result.boot, "outcome": result.outcome, "errorCode": result.error}
    return uart.encode_frame(reply_name, 1, uart.encode_payload(reply_name, identity)), result.execute


def test_boot_reservations_survive_pi_restart_and_only_recognize_owned_ids(tmp_path):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    probe = store.reserve_native_query_id()
    first = store.reserve_native_boot_id(probe)
    assert first == 1
    assert store.get_native_boot(first)["probe_id"] == probe
    store.close()
    store = EdgeStore(path)
    store.initialize()
    try:
        assert store.recognize_native_boot_id(first)
        assert not store.recognize_native_boot_id(999)
        with pytest.raises(ValueError, match="probe"):
            store.reserve_native_boot_id(probe)
        second = store.reserve_native_boot_id(store.reserve_native_query_id())
        assert second > first and store.recognize_native_boot_id(second)
        assert not store.recognize_native_boot_id(first)  # retired boot cannot become current
    finally:
        store.close()


def test_lost_bind_reply_and_pi_restart_query_real_mcu_without_rebinding(tmp_path, mcu):
    from mcu_session import McuBootSession
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    session = CSession()
    mcu.McuSession_Init(c.byref(session))
    sent = []
    def write(frame):
        sent.append(frame)
        # Verify persistence is visible from a separate connection before write.
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        if decoded["messageName"] == "BIND_BOOT":
            values = uart.decode_payload("BIND_BOOT", decoded["payload"])
            reader = EdgeStore(path)
            reader.initialize()
            try:
                assert reader.get_native_boot(values["proposedMcuBootId"])["probe_id"] == values["probeId"]
            finally:
                reader.close()
        return len(frame)
    client = McuBootSession(store, write)
    assert client.poll(0)
    zero = boot_response(mcu, session, sent[-1])
    assert client.accept_frame(zero, 1)
    assert len(sent) == 2 and client.current_boot(1) is None
    assert not client.accept_frame(zero, 2)  # duplicate zero cannot allocate again
    old_bound = boot_response(mcu, session, sent[-1])  # Drop binding response.
    assert session.boot == 1
    store.close()
    store = EdgeStore(path)
    store.initialize()
    try:
        client = McuBootSession(store, write)
        assert client.current_boot(0) is None  # persisted selection alone is not fresh evidence
        assert not client.accept_frame(old_bound, 0)
        assert client.poll(0)
        assert client.accept_frame(boot_response(mcu, session, sent[-1]), 1)
        assert client.current_boot(1) == 1 and len(sent) == 3
        assert uart.decode_frame(sent[-1], sender_role="EDGE")["messageName"] == "BOOT_PROBE"
    finally:
        store.close()


def test_command_intent_and_single_write_claim_survive_restart(tmp_path):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    boot = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot)
    record = store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, boot, open_fields())
    assert record["command_sequence"] == 1 and record["write_claimed"] == 0
    assert store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, boot, open_fields()) == record
    with pytest.raises(ValueError, match="identity"):
        store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, boot, open_fields() | {"portNo": 2})
    assert store.claim_native_command_write(COMMAND_UID)
    store.close()
    store = EdgeStore(path)
    store.initialize()
    try:
        assert not store.claim_native_command_write(COMMAND_UID)
        assert store.get_native_command(COMMAND_UID)["payload"] == record["payload"]
        with pytest.raises(RuntimeError, match="unresolved"):
            store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN",
                "55555555-5555-4555-8555-555555555555", boot, open_fields())
        assert store.list_native_commands()[0]["write_claimed"] == 1
    finally:
        store.close()


def test_one_action_write_then_query_after_lost_decision_and_pi_restart(tmp_path, mcu):
    from mcu_session import McuBootSession, McuCommandDispatcher
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("DELIVERY", WORK_UID, 1, {"phase": "WAITING_FIRST_OPEN"})
    original_work = store.get_work_slot()
    session, sent, armed = CSession(), [], []
    now = [0]
    def write(frame):
        sent.append(frame)
        if uart.decode_frame(frame, sender_role="EDGE")["messageName"] == "AUTHORIZE_DELIVERY_FIRST_OPEN":
            reader = EdgeStore(path)
            reader.initialize()
            try:
                assert reader.get_native_command(COMMAND_UID)["write_claimed"] == 1
            finally:
                reader.close()
        return len(frame)
    boot = McuBootSession(store, write)
    boot.poll(0)
    boot.accept_frame(boot_response(mcu, session, sent[-1]), 0)
    boot.accept_frame(boot_response(mcu, session, sent[-1]), 0)
    store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, 1, open_fields())
    client = McuCommandDispatcher(store, boot, write, arm=lambda record: armed.append(record["command_uid"]) or (lambda: None), clock=lambda: now[0])
    assert client.send_once(COMMAND_UID)
    assert armed == [COMMAND_UID]
    lost, execute = command_response(mcu, session, sent[-1])
    assert execute == 1
    assert not client.send_once(COMMAND_UID)
    store.close()
    store = EdgeStore(path)
    store.initialize()
    try:
        boot = McuBootSession(store, write)
        boot.poll(0)
        boot.accept_frame(boot_response(mcu, session, sent[-1]), 0)
        client = McuCommandDispatcher(store, boot, write, arm=lambda _: pytest.fail("old action must not re-arm"), clock=lambda: now[0])
        assert not client.send_once(COMMAND_UID)
        assert client.poll(COMMAND_UID, 0)
        response, execute = command_response(mcu, session, sent[-1])
        assert execute == 0 and client.accept_frame(response, 1)
        assert store.get_native_command(COMMAND_UID)["decision_outcome"] == "ACCEPTED"
        assert client.accept_frame(lost, 2)  # durable original decision, not a new effect
        assert store.get_work_slot() == original_work
        assert store.list_native_result_report_tasks() == []
        messages = [uart.decode_frame(frame, sender_role="EDGE")["messageName"] for frame in sent]
        assert messages.count("AUTHORIZE_DELIVERY_FIRST_OPEN") == 1
        assert messages.count("QUERY_COMMAND") == 1
    finally:
        store.close()


@pytest.fixture
def ready(tmp_path, mcu):
    from mcu_session import McuBootSession
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    session, sent = CSession(), []
    write = lambda frame: sent.append(frame) or len(frame)
    boot = McuBootSession(store, write)
    boot.poll(0)
    boot.accept_frame(boot_response(mcu, session, sent[-1]), 0)
    boot.accept_frame(boot_response(mcu, session, sent[-1]), 0)
    store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, 1, open_fields())
    try:
        yield store, session, sent, write, boot
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["short", "raise"])
def test_short_or_failed_write_remains_claimed_and_query_never_replays(ready, mcu, failure):
    from mcu_session import McuCommandDispatcher
    store, session, sent, write, boot = ready
    def broken(frame):
        sent.append(frame)
        if failure == "raise":
            raise OSError("lost write result")
        return 3
    client = McuCommandDispatcher(store, boot, broken, arm=lambda _: lambda: None, clock=lambda: 0)
    assert client.send_once(COMMAND_UID) and client.last_write_error
    assert not client.send_once(COMMAND_UID)
    client = McuCommandDispatcher(store, boot, write, arm=lambda _: pytest.fail("must not re-arm"), clock=lambda: 0)
    assert not client.send_once(COMMAND_UID)
    assert client.poll(COMMAND_UID, 0)
    response, executed = command_response(mcu, session, sent[-1])
    assert not executed and client.accept_frame(response, 1)
    assert store.list_native_command_observations(COMMAND_UID)[0]["outcome"] == "NOT_SEEN"
    assert store.get_native_command(COMMAND_UID)["decision_outcome"] is None
    assert store.get_native_command(COMMAND_UID)["write_claimed"] == 1


@pytest.mark.parametrize("failure", ["denied", "slow"])
def test_failed_or_expired_authorization_cannot_write(ready, failure):
    from mcu_session import McuCommandDispatcher
    store, session, sent, write, boot = ready
    now = [0]
    def arm(_):
        if failure == "denied":
            raise RuntimeError("permanent gate denied")
        now[0] = 1000
        return lambda: None
    count = len(sent)
    client = McuCommandDispatcher(store, boot, write, arm=arm, clock=lambda: now[0])
    with pytest.raises(RuntimeError):
        client.send_once(COMMAND_UID)
    assert len(sent) == count and store.get_native_command(COMMAND_UID)["write_claimed"] == 0


def test_delayed_binding_after_mcu_reset_is_rejected_then_fresh_probe_can_bind(tmp_path, mcu):
    from mcu_session import McuBootSession
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    session, sent = CSession(), []
    client = McuBootSession(store, lambda frame: sent.append(frame) or len(frame))
    try:
        client.poll(0)
        zero = boot_response(mcu, session, sent[-1])
        client.accept_frame(zero, 1)
        old_bind = sent[-1]
        mcu.McuSession_Init(c.byref(session))
        assert client.accept_frame(boot_response(mcu, session, old_bind), 2)
        assert client.current_boot(2) is None and session.boot == 0
        client.poll(1000)
        assert not client.accept_frame(zero, 1001)
        assert client.accept_frame(boot_response(mcu, session, sent[-1]), 1002)
        assert client.accept_frame(boot_response(mcu, session, sent[-1]), 1003)
        assert client.current_boot(1003) == 2 and session.boot == 2
    finally:
        store.close()


def test_competing_connections_can_claim_only_one_write(ready):
    first, *_ = ready
    second = EdgeStore(first.db_path)
    second.initialize()
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda store: store.claim_native_command_write(COMMAND_UID), [first, second]))
        assert sorted(results) == [False, True]
    finally:
        second.close()


@pytest.mark.parametrize("after_commit", [False, True])
def test_process_exit_at_dispatch_claim_does_not_replay_committed_claim(ready, after_commit):
    store, *_ = ready
    child = '''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1]); store.initialize()
if sys.argv[3] == "before":
    store._conn.create_function("crash_now", 0, lambda: os._exit(77))
    store._conn.execute("CREATE TEMP TRIGGER crash_claim AFTER UPDATE OF write_claimed ON native_mcu_command "
        "BEGIN SELECT crash_now(); END")
assert store.claim_native_command_write(sys.argv[2])
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-c", child, store.db_path, COMMAND_UID, "after" if after_commit else "before"],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=15)
    assert run.returncode == 77, run.stderr
    assert store.get_native_command(COMMAND_UID)["write_claimed"] == int(after_commit)
    assert store.claim_native_command_write(COMMAND_UID) == (not after_commit)


def test_native_action_uses_real_permanent_ledger_and_stale_business_copy_cannot_rearm(ready, tmp_path):
    from mcu_session import McuCommandDispatcher, NativePhysicalActionGate
    from job_safety import PhysicalAction, action_digest, JobSafetyError
    from tests.test_command_processor import make_real_job_safety
    from tests.test_job_safety import _command
    store, session, sent, write, boot = ready
    updater, safety = make_real_job_safety(tmp_path)
    command = _command()
    command["payload"]["sessionUid"] = WORK_UID
    permit = safety.request_job(command, work_type="DELIVERY", work_uid=WORK_UID)
    safety.begin_job(permit, begin_uid="55555555-5555-4555-8555-555555555555", digest=permit.request_digest_sha256)
    record = store.get_native_command(COMMAND_UID)
    digest = action_digest(work_uid=WORK_UID, command_uid=permit.command_uid, action_key="delivery:first-open",
        action_kind=record["message_name"], payload={"nativeUartPayloadHex": record["payload"].hex()})
    action = PhysicalAction(COMMAND_UID, "66666666-6666-4666-8666-666666666666", "delivery:first-open", record["message_name"], digest)
    def checked_write(frame):
        assert safety.get_physical_action(COMMAND_UID)["state"] == "ARMED"
        return write(frame)
    gate = NativePhysicalActionGate(safety, permit, action, store=store, revalidate=lambda _: None, deadline_ms=1000, clock=lambda: 0)
    client = McuCommandDispatcher(store, boot, checked_write, arm=gate, clock=lambda: 0)
    try:
        assert client.send_once(COMMAND_UID)
        assert safety.get_physical_action(COMMAND_UID)["state"] == "ARMED"  # write is not an effect result
        # Test-only stale business row: permanent DB is not rolled back with it.
        with store.transaction(immediate=True) as conn:
            conn.execute("UPDATE native_mcu_command SET write_claimed=0")
        restored_gate = NativePhysicalActionGate(safety, permit, action, store=store, revalidate=lambda _: None, deadline_ms=1000, clock=lambda: 0)
        restored = McuCommandDispatcher(store, boot, checked_write, arm=restored_gate, clock=lambda: 0)
        count = len(sent)
        with pytest.raises(JobSafetyError):
            restored.send_once(COMMAND_UID)
        assert len(sent) == count
    finally:
        updater.close()


def test_dispatch_rechecks_deadline_after_sqlite_commit_before_write(ready):
    from mcu_session import McuCommandDispatcher
    store, session, sent, write, boot = ready
    now = [0]
    def check():
        if now[0] >= 100:
            raise RuntimeError("original business deadline expired")
    def advance():
        now[0] = 101
        return 0
    store._conn.create_function("advance_clock", 0, advance)
    store._conn.execute("CREATE TEMP TRIGGER slow_claim AFTER UPDATE OF write_claimed ON native_mcu_command "
                        "BEGIN SELECT advance_clock(); END")
    client = McuCommandDispatcher(store, boot, write, arm=lambda _: check, clock=lambda: now[0])
    count = len(sent)
    with pytest.raises(RuntimeError, match="deadline"):
        client.send_once(COMMAND_UID)
    assert len(sent) == count
    assert store.get_native_command(COMMAND_UID)["write_claimed"] == 1


def test_conflicting_decisions_keep_original_and_block_further_commands(ready, mcu):
    from mcu_session import McuCommandDispatcher
    store, session, sent, write, boot = ready
    client = McuCommandDispatcher(store, boot, write, arm=lambda _: lambda: None, clock=lambda: 0)
    assert client.send_once(COMMAND_UID)
    reply, _ = command_response(mcu, session, sent[-1])
    assert client.accept_frame(reply, 0)
    assert client.poll(COMMAND_UID, 0)
    query_reply, _ = command_response(mcu, session, sent[-1])
    assert client.accept_frame(query_reply, 1)
    values = uart.decode_payload("COMMAND_QUERY_RESULT", uart.decode_frame(query_reply)["payload"])
    conflict = uart.encode_frame("COMMAND_QUERY_RESULT", 1,
        uart.encode_payload("COMMAND_QUERY_RESULT", values | {"outcome": "REJECTED", "errorCode": "INTERNAL_FAULT"}))
    assert client.accept_frame(conflict, 2)
    stored = store.get_native_command(COMMAND_UID)
    assert stored["conflict"] == 1 and stored["decision_outcome"] == "ACCEPTED"
    assert len(store.list_native_command_observations(COMMAND_UID)) == 3
    with pytest.raises(RuntimeError, match="unresolved"):
        store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", str(uuid.uuid4()), 1, open_fields())


def test_rejection_retires_sequence_without_claiming_physical_completion(ready, mcu):
    from mcu_session import McuCommandDispatcher
    store, session, sent, write, boot = ready
    client = McuCommandDispatcher(store, boot, write, arm=lambda _: lambda: None, clock=lambda: 0)
    client.send_once(COMMAND_UID)
    rejected, execute = command_response(mcu, session, sent[-1], business_error=1)
    assert execute == 0 and client.accept_frame(rejected, 0)
    assert store.get_native_command(COMMAND_UID)["decision_outcome"] == "REJECTED"
    next_uid = str(uuid.uuid4())
    assert store.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", next_uid, 1, open_fields())["command_sequence"] == 2
    assert not client.send_once(COMMAND_UID)
    assert store.list_native_result_report_tasks() == []


def test_commit_denial_prevents_serial_write_and_rolls_back_claim(ready):
    from mcu_session import McuCommandDispatcher
    store, session, sent, write, boot = ready
    client = McuCommandDispatcher(store, boot, write, arm=lambda _: lambda: None, clock=lambda: 0)
    store._conn.set_authorizer(lambda action, arg1, *_: sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT" else sqlite3.SQLITE_OK)
    count = len(sent)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            client.send_once(COMMAND_UID)
    finally:
        store._conn.set_authorizer(None)
    assert len(sent) == count and store.get_native_command(COMMAND_UID)["write_claimed"] == 0


@pytest.mark.parametrize("counter", ["-1", "01", "9007199254740991", "9007199254740992"])
def test_bad_or_exhausted_boot_counter_cannot_offer_a_reused_id(tmp_path, counter):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        probe = store.reserve_native_query_id()
        store.set_state("native_boot_sequence", counter)
        with pytest.raises(ValueError):
            store.reserve_native_boot_id(probe)
        assert store.get_native_boot(1) is None
    finally:
        store.close()


def test_v19_to_v20_migration_is_atomic_and_preserves_results(tmp_path):
    from hardware.tests.test_native_result_handoff import result_payload
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    result = result_payload()
    store.save_native_mcu_result(result)
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DROP TABLE native_process_receipt_conflict")
        conn.execute("DROP TABLE native_process_receipt")
        conn.execute("DROP TABLE native_measurement_event_conflict")
        conn.execute("DROP TABLE native_measurement_event")
        conn.execute("DROP TABLE native_mcu_command_observation")
        conn.execute("DROP TABLE native_mcu_command")
        conn.execute("DROP TABLE native_mcu_boot")
        conn.execute("DELETE FROM schema_version WHERE version>=20")
    store.close()
    child = '''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1]); store._open_connection()
store._conn.create_function("crash_now", 0, lambda: os._exit(78))
store._conn.execute("CREATE TEMP TRIGGER crash_v20 AFTER INSERT ON schema_version "
    "WHEN new.version=20 BEGIN SELECT crash_now(); END")
store.initialize()
'''
    run = subprocess.run([sys.executable, "-c", child, path],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=15)
    assert run.returncode == 78, run.stderr
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 19
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='native_mcu_boot'").fetchone()
    store = EdgeStore(path)
    store.initialize()
    try:
        assert store.save_native_mcu_result(result)["savedPayload"] == result[:60]
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.reserve_native_boot_id(store.reserve_native_query_id()) == 1
    finally:
        store.close()
