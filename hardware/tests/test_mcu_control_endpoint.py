"""Actual C byte-stream control endpoint paired with Python/SQLite clients."""
import ctypes as c
import re
import subprocess
from pathlib import Path

import pytest
import uart2_protocol as uart
from hardware.tests.test_mcu_actuator_event_journal import Reservation
from edge_store import EdgeStore
from mcu_session import McuBootSession

ROOT = Path(__file__).resolve().parents[2]
SINK = c.CFUNCTYPE(None, c.c_void_p, c.c_size_t, c.c_void_p)


def running_firmware_identity():
    """Read the exact generated identity compiled into the current candidate."""
    header = (ROOT / "hardware_mcu/USER/firmware_identity.h").read_text(
        encoding="ascii"
    )
    version = re.search(
        r'ECOBIN_MCU_FIRMWARE_VERSION\s+"([^"]+)"',
        header,
    )
    version_code = re.search(
        r"ECOBIN_MCU_FIRMWARE_VERSION_CODE\s+(\d+)UL",
        header,
    )
    identity = re.search(
        r"ECOBIN_MCU_FIRMWARE_IDENTITY_BYTES\s*\\\s*\{([^}]+)\}",
        header,
    )
    if version is None or version_code is None or identity is None:
        raise AssertionError("generated MCU firmware identity header is malformed")
    identity_bytes = bytes(
        int(value, 16)
        for value in re.findall(r"0x([0-9A-Fa-f]{2})", identity.group(1))
    )
    if len(identity_bytes) != 8:
        raise AssertionError("generated MCU firmware identity must be eight bytes")
    return {
        "firmwareVersionCode": int(version_code.group(1)),
        "firmwareIdentityHigh": int.from_bytes(identity_bytes[:4], "big"),
        "firmwareIdentityLow": int.from_bytes(identity_bytes[4:], "big"),
        "firmwareVersion": version.group(1),
        "firmwareIdentityHex": identity_bytes.hex(),
    }


@pytest.fixture
def endpoint(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    path = tmp_path / "endpoint.dll"
    signatures = {
        "McuControlEndpoint_Init": (None, [c.c_void_p, c.c_uint8, SINK, c.c_void_p]),
        "McuControlEndpoint_Feed": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64]),
        "McuControlEndpoint_ReserveEventSequence": (c.c_uint32, [c.c_void_p]),
        "McuControlEndpoint_ReserveActuatorEvents": (c.c_uint8, [c.c_void_p, c.c_uint8, c.POINTER(Reservation)]),
        "McuControlEndpoint_PublishActuatorEvent": (c.c_uint32, [c.c_void_p, c.POINTER(Reservation),
            c.c_uint8, c.c_uint8, c.c_void_p, c.c_size_t]),
        "McuControlEndpoint_CopyNextActuatorEvent": (c.c_size_t, [c.c_void_p, c.c_uint32,
            c.POINTER(c.c_uint8), c.c_void_p, c.c_size_t]),
        "McuControlEndpoint_CancelActuatorEvents": (c.c_uint8, [c.c_void_p, c.POINTER(Reservation)]),
        "McuControlEndpoint_ConfirmActuatorEventSaved": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_void_p, c.c_size_t]),
        "TestControl_SetEventSequence": (None, [c.c_void_p, c.c_uint32]),
        "TestFacts_InitHardware": (None, []),
        "TestFacts_Writes": (c.c_uint32, []),
        "TestControl_Work": (c.c_void_p, [c.c_void_p]),
        "TestControl_Session": (c.c_void_p, [c.c_void_p]),
        "TestControl_ProcessEvent": (c.c_void_p, [c.c_void_p]),
        "McuProcessEventSlot_Freeze": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint8, c.c_void_p, c.c_size_t]),
        "McuSession_ReceiveCommand": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint16, c.c_void_p]),
        "McuWorkState_BeginAccepted": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint8]),
        "McuWorkState_Complete": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_CopyHeld": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t]),
    }
    result = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-I", str(user),
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1", str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        *[str(user / (name + ".c")) for name in ("mcu_control_endpoint", "mcu_actuator_event_journal", "mcu_process_event_slot", "mcu_session", "mcu_work_state", "mcu_result_slot", "mcu_device_facts",
            "actuator_runtime", "door_control", "clean_lock", "runtime_clock", "scale_reader", "weight_measurement")],
        str(ROOT / "hardware_mcu/tests/device_facts_host.c"),
        str(ROOT / "hardware_mcu/tests/control_endpoint_host.c"),
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    lib = c.CDLL(str(path))
    for name, (restype, argtypes) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = restype, argtypes
    lib.TestFacts_InitHardware()
    memory, replies = (c.c_uint64 * 384)(), []  # C static budget <= 3072 bytes
    sink = SINK(lambda data, size, _: replies.append(c.string_at(data, size)))
    lib.McuControlEndpoint_Init(memory, 1, sink, None)
    yield lib, memory, replies, sink


def test_python_boot_binding_through_real_c_fragmented_frames(endpoint, tmp_path):
    lib, memory, replies, sink = endpoint
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    def write(frame):
        sent.append(frame)
        # Every byte passes through the actual C incremental parser.
        for byte in frame:
            lib.McuControlEndpoint_Feed(memory, bytes([byte]), 1, 0)
        return len(frame)
    client = McuBootSession(store, write)
    try:
        client.poll(0)
        assert len(replies) == 1
        assert client.accept_frame(replies.pop(0), 0)
        assert len(replies) == 1
        assert client.accept_frame(replies.pop(0), 0)
        assert client.current_boot(0) == 1
        assert len(sent) == 2
        # A new Pi observer sees the existing binding, not a synthetic boot.
        restarted = McuBootSession(store, write)
        restarted.poll(0)
        assert restarted.accept_frame(replies.pop(0), 0)
        assert restarted.current_boot(0) == 1 and len(sent) == 3
    finally:
        store.close()


def request(name, values, sequence=1):
    return uart.encode_frame(name, sequence, uart.encode_payload(name, values))


def test_process_query_reports_missing_data_without_accepting_a_new_command(endpoint):
    from contracts.tests.test_uart_v2_process_handoff import process_scope
    bind(endpoint)
    replies = exchange(endpoint, request("QUERY_PROCESS_EVENT", dict(queryId=23, **process_scope())))
    assert len(replies) == 1
    value = uart.decode_payload("PROCESS_EVENT_QUERY_REPLY", uart.decode_frame(replies[0])["payload"])
    assert value["status"] == "NOT_FOUND" and value["mcuEventSequence"] == 0


def test_process_query_reemits_original_then_saved_releases_only_process_data(endpoint):
    from hardware.tests.test_mcu_process_event_slot import freeze, saved, process_values, process_scope
    final, _ = seed_completed_work(endpoint)
    lib, memory, replies, sink = endpoint
    assert freeze(lib, lib.TestControl_ProcessEvent(memory)) == 1
    generic = request("ACK", {"senderBootId": 77, "referencedSenderBootId": 42,
        "referencedTxSequence": 1, "referencedMessageType": 48, "disposition": "ACCEPTED"})
    assert exchange(endpoint, generic) == []
    frames = exchange(endpoint, request("QUERY_PROCESS_EVENT", dict(queryId=23, **process_scope())))
    assert [uart.decode_frame(frame)["messageName"] for frame in frames] == [
        "PROCESS_EVENT_QUERY_REPLY", "WORK_PREOPEN_WEIGHT_READY"]
    event = uart.decode_frame(frames[1], sender_role="MCU")
    assert event["payload"] == uart.encode_payload("WORK_PREOPEN_WEIGHT_READY", process_values())
    assert event["flags"] == 1
    response = exchange(endpoint, uart.encode_frame("PROCESS_EVENT_SAVED", 24, saved()))
    assert len(response) == 1
    assert uart.decode_payload("PROCESS_EVENT_SAVED_REPLY", uart.decode_frame(response[0])["payload"])["status"] == "RELEASED"
    # Process custody cannot release the independent terminal work result.
    out = c.create_string_buffer(242)
    size = lib.McuWorkState_CopyHeld(lib.TestControl_Work(memory), out, len(out))
    assert out.raw[:size] == final
    frames = exchange(endpoint, request("QUERY_PROCESS_EVENT", dict(queryId=25, **process_scope())))
    assert len(frames) == 1
    assert uart.decode_payload("PROCESS_EVENT_QUERY_REPLY", uart.decode_frame(frames[0])["payload"])["status"] == "RELEASED"


def exchange(endpoint, frame, now=0):
    lib, memory, replies, sink = endpoint
    before = len(replies)
    lib.McuControlEndpoint_Feed(memory, frame, len(frame), now)
    return replies[before:]


def bind(endpoint):
    exchange(endpoint, request("BOOT_PROBE", {"probeId": 1}))
    bound = exchange(endpoint, request("BIND_BOOT", {"probeId": 1, "proposedMcuBootId": 42}))
    assert uart.decode_payload("BIND_BOOT_REPLY", uart.decode_frame(bound[0])["payload"])["mcuBootId"] == 42


def test_control_queries_return_actual_device_work_and_command_facts(endpoint):
    from hardware.tests.test_mcu_work_query import identity
    bind(endpoint)
    device = exchange(endpoint, request("QUERY_DEVICE_FACTS", {"queryId": 2, "targetMcuBootId": 42, "portNo": 1}))
    assert len(device) == 1
    values = uart.decode_payload("DEVICE_FACTS_REPLY", uart.decode_frame(device[0])["payload"])
    assert values["status"] == "AVAILABLE" and values["lastDeliveryDoorCommand"] == "NONE"
    assert values["scaleReadStatus"] == "NOT_OBSERVED" and values["retainedWorkState"] == "NONE"
    original = identity()
    work = exchange(endpoint, request("QUERY_WORK", original | {"queryId": 3}))
    assert uart.decode_payload("WORK_QUERY_REPLY", uart.decode_frame(work[0])["payload"])["status"] == "NOT_FOUND"
    command = {key: original[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence")}
    response = exchange(endpoint, request("QUERY_COMMAND", command | {"queryId": 4}))
    value = uart.decode_payload("COMMAND_QUERY_RESULT", uart.decode_frame(response[0])["payload"])
    assert value["outcome"] == "NOT_SEEN" and value["highestCommandSequence"] == 0


def test_identity_query_returns_the_actual_running_firmware_without_side_effects(endpoint):
    bind(endpoint)
    received = exchange(
        endpoint,
        request(
            "QUERY_DEVICE_IDENTITY",
            {"queryId": 5, "targetMcuBootId": 42},
        ),
    )
    assert len(received) == 1
    frame = uart.decode_frame(received[0], sender_role="MCU")
    assert frame["messageName"] == "DEVICE_IDENTITY_REPLY"
    values = uart.decode_payload("DEVICE_IDENTITY_REPLY", frame["payload"])
    expected_identity = running_firmware_identity()
    assert values == {
        "queryId": 5,
        "targetMcuBootId": 42,
        "currentMcuBootId": 42,
        "status": "AVAILABLE",
        "protocolMajor": 2,
        "protocolMinor": 0,
        "portCount": 1,
        "capabilityBitmap": 0x8100,
        "highestCommandSequence": 0,
        "firmwareVersionCode": expected_identity["firmwareVersionCode"],
        "firmwareIdentityHigh": expected_identity["firmwareIdentityHigh"],
        "firmwareIdentityLow": expected_identity["firmwareIdentityLow"],
        "firmwareVersion": expected_identity["firmwareVersion"],
    }

    mismatch = exchange(
        endpoint,
        request(
            "QUERY_DEVICE_IDENTITY",
            {"queryId": 6, "targetMcuBootId": 41},
        ),
    )
    mismatch_values = uart.decode_payload(
        "DEVICE_IDENTITY_REPLY", uart.decode_frame(mismatch[0])["payload"]
    )
    assert mismatch_values["status"] == "BOOT_MISMATCH"
    assert mismatch_values["currentMcuBootId"] == 42
    # The read-only query neither consumes the command sequence nor creates work.
    facts = exchange(
        endpoint,
        request(
            "QUERY_DEVICE_FACTS",
            {"queryId": 7, "targetMcuBootId": 42, "portNo": 1},
        ),
    )
    fact_values = uart.decode_payload(
        "DEVICE_FACTS_REPLY", uart.decode_frame(facts[0])["payload"]
    )
    assert fact_values["retainedWorkState"] == "NONE"


def seed_completed_work(endpoint):
    import uuid
    from hardware.tests.test_native_command_session import CCommand, CDecision
    from hardware.tests.test_mcu_work_state_c import query_payload
    from hardware.tests.test_native_result_handoff import result_payload
    lib, memory, replies, sink = endpoint
    bind(endpoint)
    query = query_payload()
    values = uart.decode_payload("QUERY_WORK", query)
    command = CCommand(values["targetMcuBootId"], values["commandSequence"],
        (c.c_uint8 * 16).from_buffer_copy(uuid.UUID(values["mcuCommandUid"]).bytes),
        (c.c_uint8 * 32).from_buffer_copy(bytes.fromhex(values["commandDigestSha256"])))
    decision = CDecision()
    assert lib.McuSession_ReceiveCommand(lib.TestControl_Session(memory), c.byref(command), 0, c.byref(decision))
    assert decision.execute
    work = lib.TestControl_Work(memory)
    assert lib.McuWorkState_BeginAccepted(work, query[8:], 78, 16)
    result = result_payload()
    assert lib.McuWorkState_Complete(work, result, len(result))
    return result, values


def test_identity_query_reports_the_actual_consumed_command_sequence(endpoint):
    _, original = seed_completed_work(endpoint)
    frames = exchange(
        endpoint,
        request(
            "QUERY_DEVICE_IDENTITY",
            {"queryId": 111, "targetMcuBootId": 42},
        ),
    )
    assert len(frames) == 1
    values = uart.decode_payload(
        "DEVICE_IDENTITY_REPLY", uart.decode_frame(frames[0])["payload"]
    )
    assert values["status"] == "AVAILABLE"
    assert values["highestCommandSequence"] == original["commandSequence"]


def test_complete_result_flows_through_c_wire_and_sqlite_before_precise_release(endpoint, tmp_path):
    lib, memory, replies, sink = endpoint
    result, work_query = seed_completed_work(endpoint)
    values = uart.decode_payload("WORK_RESULT", result)
    result_identity = {key: values[key] for key in ("mcuBootId", "resultSequence", "workUid", "resultDigestSha256")}
    query = request("QUERY_RESULT", {"queryId": 5} | result_identity)
    received = exchange(endpoint, query)
    assert [uart.decode_frame(frame)["messageName"] for frame in received] == ["RESULT_QUERY_REPLY", "WORK_RESULT"]
    assert uart.decode_frame(received[1])["payload"] == result
    held = c.create_string_buffer(199)
    work = lib.TestControl_Work(memory)
    assert lib.McuWorkState_CopyHeld(work, held, len(held)) == 199
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        saved = store.save_native_mcu_result(uart.decode_frame(received[1])["payload"])
        confirmation = uart.encode_frame("RESULT_SAVED", 8, saved["savedPayload"])
        released = exchange(endpoint, confirmation)
        assert uart.decode_payload("RESULT_SAVED_REPLY", uart.decode_frame(released[0])["payload"])["status"] == "RELEASED"
        assert lib.McuWorkState_CopyHeld(work, held, len(held)) == 0
        again = exchange(endpoint, confirmation)
        assert uart.decode_payload("RESULT_SAVED_REPLY", uart.decode_frame(again[0])["payload"])["status"] == "ALREADY_RELEASED"
        after = exchange(endpoint, query)
        assert len(after) == 1  # released reference, not another result
        assert uart.decode_payload("RESULT_QUERY_REPLY", uart.decode_frame(after[0])["payload"])["status"] == "RELEASED"
        snapshot = exchange(endpoint, request("QUERY_WORK", work_query | {"queryId": 6}))
        assert uart.decode_payload("WORK_QUERY_REPLY", uart.decode_frame(snapshot[0])["payload"])["status"] == "RESULT_RELEASED"
        assert len(store.list_native_result_report_tasks()) == 1
    finally:
        store.close()


def test_corrupt_partial_wrong_direction_and_wrong_saved_identity_do_not_release(endpoint):
    lib, memory, replies, sink = endpoint
    result, _ = seed_completed_work(endpoint)
    correct = uart.encode_frame("RESULT_SAVED", 8, result[:60])
    bad = correct[:-1] + bytes([correct[-1] ^ 1])
    assert exchange(endpoint, bad) == []
    assert exchange(endpoint, correct[:20]) == []
    assert exchange(endpoint, b"", 101) == []  # partial frame times out
    assert exchange(endpoint, correct[20:], 102) == []
    wrong_direction = request("BOOT_PROBE_REPLY", {"probeId": 5, "mcuBootId": 42})
    assert exchange(endpoint, wrong_direction, 103) == []
    saved_identity = uart.decode_payload("RESULT_SAVED", result[:60])
    conflict = exchange(endpoint, request("RESULT_SAVED", saved_identity | {"resultDigestSha256": "ff" * 32}), 104)
    assert uart.decode_payload("RESULT_SAVED_REPLY", uart.decode_frame(conflict[0])["payload"])["status"] == "IDENTITY_CONFLICT"
    held = c.create_string_buffer(199)
    assert lib.McuWorkState_CopyHeld(lib.TestControl_Work(memory), held, len(held)) == 199
    assert held.raw == result
    # Even an already-bound handshake cannot reset the held result.
    already = exchange(endpoint, request("BIND_BOOT", {"probeId": 999, "proposedMcuBootId": 999}), 105)
    assert uart.decode_payload("BIND_BOOT_REPLY", uart.decode_frame(already[0])["payload"])["status"] == "ALREADY_BOUND"
    assert lib.McuWorkState_CopyHeld(lib.TestControl_Work(memory), held, len(held)) == 199


def test_large_stream_emits_each_probe_once_and_rejects_zero_identity(endpoint):
    lib, memory, replies, sink = endpoint
    frames = [request("BOOT_PROBE", {"probeId": number}, number) for number in range(1, 101)]
    combined = b"".join(frames)
    assert lib.McuControlEndpoint_Feed(memory, combined, len(combined), 0) == 100
    assert [uart.decode_payload("BOOT_PROBE_REPLY", uart.decode_frame(frame)["payload"])["probeId"] for frame in replies] == list(range(1, 101))
    malformed = uart.encode_frame("BOOT_PROBE", 101, bytes(8))  # CRC correct, invalid content
    assert exchange(endpoint, malformed, 1) == []
    bind_last = exchange(endpoint, request("BIND_BOOT", {"probeId": 100, "proposedMcuBootId": 42}), 2)
    assert uart.decode_payload("BIND_BOOT_REPLY", uart.decode_frame(bind_last[0])["payload"])["status"] == "BOUND"


def test_generic_ack_and_old_saved_confirmation_cannot_release_new_boot_result(endpoint):
    from hardware.tests.test_mcu_work_state_c import query_payload
    from hardware.tests.test_native_result_handoff import result_payload
    lib, memory, replies, sink = endpoint
    result, _ = seed_completed_work(endpoint)
    generic = request("ACK", {"senderBootId": 77, "referencedSenderBootId": 42,
        "referencedTxSequence": 1, "referencedMessageType": uart.MESSAGE_SPECS["WORK_RESULT"]["id"],
        "disposition": "ACCEPTED"})
    assert exchange(endpoint, generic) == []
    held = c.create_string_buffer(199)
    work = lib.TestControl_Work(memory)
    assert lib.McuWorkState_CopyHeld(work, held, len(held)) == 199
    lib.McuControlEndpoint_Init(memory, 1, sink, None)  # actual-reset boundary, RAM cleared
    assert lib.McuWorkState_CopyHeld(work, held, len(held)) == 0
    stale_bind = exchange(endpoint, request("BIND_BOOT", {"probeId": 1, "proposedMcuBootId": 42}))
    assert uart.decode_payload("BIND_BOOT_REPLY", uart.decode_frame(stale_bind[0])["payload"])["status"] == "PROBE_MISMATCH"
    old_saved = uart.encode_frame("RESULT_SAVED", 1, result[:60])
    mismatch = exchange(endpoint, old_saved)
    values = uart.decode_payload("RESULT_SAVED_REPLY", uart.decode_frame(mismatch[0])["payload"])
    assert values["status"] == "BOOT_MISMATCH" and values["currentMcuBootId"] == 0
    exchange(endpoint, request("BOOT_PROBE", {"probeId": 2}))
    exchange(endpoint, request("BIND_BOOT", {"probeId": 2, "proposedMcuBootId": 43}))
    next_result = result_payload(mcuBootId=43, initialSourceMcuBootId=43, finalSourceMcuBootId=43)
    original = query_payload(targetMcuBootId=43)[8:]
    assert lib.McuWorkState_BeginAccepted(work, original, len(original), 16)
    assert lib.McuWorkState_Complete(work, next_result, len(next_result))
    mismatch = exchange(endpoint, old_saved)
    assert uart.decode_payload("RESULT_SAVED_REPLY", uart.decode_frame(mismatch[0])["payload"])["status"] == "BOOT_MISMATCH"
    assert lib.McuWorkState_CopyHeld(work, held, len(held)) == 199 and held.raw == next_result


def test_endpoint_target_compile(tmp_path):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required")
    run = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
        "-I", str(ROOT / "hardware_mcu/USER"), "-c", str(ROOT / "hardware_mcu/USER/mcu_control_endpoint.c"),
        "-o", str(tmp_path / "endpoint.o")], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not run.stdout.strip() and not run.stderr.strip()
    sizes = subprocess.run([str(compiler.with_name("fromelf.exe")), "--text", "-z", str(tmp_path / "endpoint.o")],
        capture_output=True, text=True, timeout=30)
    assert sizes.returncode == 0, sizes.stdout + sizes.stderr
    print(sizes.stdout)
