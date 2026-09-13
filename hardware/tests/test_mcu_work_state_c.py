"""Native MCU retained work: actual C runtime, same contract bytes as Python."""
import ctypes
import subprocess
from pathlib import Path

import pytest
import uart2_protocol as uart
from hardware.tests.test_native_result_handoff import ROOT, result_payload


def query_payload(**changes):
    result = uart.decode_payload("WORK_RESULT", result_payload())
    return uart.encode_payload("QUERY_WORK", {
        "queryId": 101, "mcuCommandUid": result["originCommandUid"],
        "commandDigestSha256": "ab" * 32, "targetMcuBootId": result["mcuBootId"],
        "commandSequence": result["originCommandSequence"], "workUid": result["workUid"],
        "workType": result["workType"], "portNo": result["portNo"],
    } | changes)


def test_mcu_work_lifecycle_host(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    executable = tmp_path / "work.exe"
    compiled = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic",
        "-I", str(user), str(user / "mcu_result_slot.c"), str(user / "mcu_work_state.c"),
        str(ROOT / "hardware_mcu/tests/test_mcu_work_state.c"), "-o", str(executable)],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    run = subprocess.run([str(executable), query_payload().hex(), result_payload().hex()],
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr
    replies = [uart.decode_payload("WORK_QUERY_REPLY", bytes.fromhex(line)) for line in run.stdout.splitlines()]
    assert [item["status"] for item in replies] == ["RUNNING", "RESULT_HELD", "RESULT_RELEASED"]
    assert [item["resultSequence"] for item in replies] == [0, 3, 3]
    result = uart.decode_payload("WORK_RESULT", result_payload())
    assert replies[1]["resultDigestSha256"] == result["resultDigestSha256"]


def test_mcu_work_state_target_compile(tmp_path):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required")
    user = ROOT / "hardware_mcu/USER"
    result = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
        "-I", str(user), "-c", str(user / "mcu_work_state.c"), "-o", str(tmp_path / "work.o")],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stdout.strip() and not result.stderr.strip()


@pytest.mark.parametrize("work_type", ["DELIVERY_SESSION", "CLEAN_OPERATION"])
def test_python_query_to_real_c_work_and_sqlite_saved_receipt(tmp_path, work_type):
    """Execute real C in-process, not a Python replacement of the MCU state."""
    from edge_store import EdgeStore
    from mcu_work_query import McuWorkQuery
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    library = tmp_path / "work.dll"
    exports = ("Init", "BeginAccepted", "Complete", "Query", "Saved", "CopyHeld")
    compiled = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-I", str(user), str(user / "mcu_work_state.c"), str(user / "mcu_result_slot.c"),
        *["-Wl,/EXPORT:McuWorkState_" + name for name in exports], "-o", str(library)],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    mcu = ctypes.CDLL(str(library))
    pointer, size, byte = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint8
    signatures = {
        "Init": (None, [pointer, ctypes.c_uint64]),
        "BeginAccepted": (byte, [pointer, pointer, size, byte]),
        "Complete": (byte, [pointer, pointer, size]),
        "Query": (size, [pointer, pointer, size, pointer, size]),
        "Saved": (byte, [pointer, pointer, size]),
        "CopyHeld": (size, [pointer, pointer, size]),
    }
    for name, (restype, args) in signatures.items():
        function = getattr(mcu, "McuWorkState_" + name)
        function.restype, function.argtypes = restype, args
    state = (ctypes.c_uint64 * 38)()  # aligned 304 bytes, enforced by C static RAM budget
    mcu.McuWorkState_Init(state, 42)
    request = query_payload(workType=work_type)
    phases = uart.REGISTRY["enums"]["McuWorkPhase"]["values"]
    phase = "DELIVERY_PREPARING" if work_type == "DELIVERY_SESSION" else "CLEAN_PREPARING"
    assert mcu.McuWorkState_BeginAccepted(state, request[8:], 78, phases[phase]) == 1
    original = uart.decode_payload("QUERY_WORK", request)
    del original["queryId"]
    sent, incoming = [], []
    def write(frame):
        sent.append(frame)
        query = uart.decode_frame(frame, sender_role="EDGE")
        assert query["messageName"] == "QUERY_WORK"
        out = ctypes.create_string_buffer(132)
        assert mcu.McuWorkState_Query(state, query["payload"], 86, out, len(out)) == 132
        incoming.append(uart.encode_frame("WORK_QUERY_REPLY", 1, out.raw))
        return len(frame)
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        client = McuWorkQuery(store, write, original)
        assert client.poll(0) == 1
        assert client.accept_frame(incoming.pop(), 1)
        assert client.observation(1)["status"] == "RUNNING"
        changes = {} if work_type == "DELIVERY_SESSION" else {
            "workType": work_type, "finishReason": "CLEAN_CONFIRMED", "deliveryRoundCount": 0,
            "cleanActionSequence": 1, "physicalCloseConfirmed": True}
        result = result_payload(**changes)
        assert mcu.McuWorkState_Complete(state, result, len(result)) == 1
        assert client.poll(1000) == 2
        assert client.accept_frame(incoming.pop(), 1001)
        observed = client.observation(1001)
        assert observed["status"] == "RESULT_HELD"
        output = ctypes.create_string_buffer(199)
        assert mcu.McuWorkState_CopyHeld(state, output, len(output)) == 199
        assert output.raw == result
        receipt = store.save_native_mcu_result(output.raw)
        store.close()  # Pi-only restart BEFORE saved receipt reaches MCU
        store = EdgeStore(path)
        store.initialize()
        client = McuWorkQuery(store, write, original)
        assert client.poll(0) == 3
        assert client.accept_frame(incoming.pop(), 1)
        assert client.observation(1)["status"] == "RESULT_HELD"
        assert store.save_native_mcu_result(result) == receipt
        assert mcu.McuWorkState_Saved(state, receipt["savedPayload"], 60) == 1
        assert client.poll(1000) == 4
        assert client.accept_frame(incoming.pop(), 1001)
        assert client.observation(1001)["status"] == "RESULT_RELEASED"
        assert client.observation(1001)["resultDigestSha256"] == observed["resultDigestSha256"]
        assert len(store.list_native_result_report_tasks()) == 1
        assert store._conn.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0] == 0
        assert not mcu.McuWorkState_CopyHeld(state, output, len(output))
        mcu.McuWorkState_Init(state, 0)  # actual MCU reset, original work gone
        assert client.poll(2000) == 5
        assert client.accept_frame(incoming.pop(), 2001)
        assert client.observation(2001)["status"] == "BOOT_MISMATCH"
        assert store.get_native_mcu_result(42, 3)["payload"] == result  # saved facts survive
    finally:
        store.close()
