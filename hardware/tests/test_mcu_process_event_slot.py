"""Real C retained process custody: no UART/GPIO or business phase mutation."""
import ctypes
import subprocess
from pathlib import Path
import pytest
from contracts.tests.test_uart_v2_process_handoff import process_scope
from contracts.tests.test_uart_v2_process_measurement import process_values
from hardware import uart2_protocol as uart

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def slot_lib(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    target = tmp_path_factory.mktemp("process-slot") / "slot.dll"
    names = ["Init", "Freeze", "CopyHeld", "Query", "Saved"]
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1",
        str(ROOT / "hardware_mcu/USER/mcu_process_event_slot.c"),
        str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        *["-Wl,/EXPORT:McuProcessEventSlot_" + name for name in names], "-o", str(target)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = ctypes.CDLL(str(target))
    lib.McuProcessEventSlot_Init.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    lib.McuProcessEventSlot_Freeze.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint8, ctypes.c_void_p, ctypes.c_size_t]
    lib.McuProcessEventSlot_Freeze.restype = ctypes.c_uint8
    lib.McuProcessEventSlot_CopyHeld.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.McuProcessEventSlot_CopyHeld.restype = ctypes.c_size_t
    lib.McuProcessEventSlot_Query.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    lib.McuProcessEventSlot_Query.restype = ctypes.c_size_t
    lib.McuProcessEventSlot_Saved.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.McuProcessEventSlot_Saved.restype = ctypes.c_uint8
    return lib


def query(slot_lib, slot, **changes):
    values = dict(queryId=23, **process_scope())
    values.update(changes)
    payload = uart.encode_payload("QUERY_PROCESS_EVENT", values)
    out = ctypes.create_string_buffer(242)
    size = slot_lib.McuProcessEventSlot_Query(slot, payload, len(payload), out, len(out))
    return uart.decode_payload("PROCESS_EVENT_QUERY_REPLY", out.raw[:size])


def freeze(slot_lib, slot, values=None, scope=None, name="WORK_PREOPEN_WEIGHT_READY"):
    payload = uart.encode_payload(name, values or process_values())
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", dict(queryId=23, **(scope or process_scope())))[8:]
    return slot_lib.McuProcessEventSlot_Freeze(slot, raw_scope, len(raw_scope), uart.MESSAGE_SPECS[name]["id"], payload, len(payload))


def saved(values=None, name="WORK_PREOPEN_WEIGHT_READY"):
    values = values or process_values()
    payload = uart.encode_payload(name, values)
    return uart.encode_payload("PROCESS_EVENT_SAVED", dict(mcuBootId=values["mcuBootId"],
        mcuEventSequence=values["mcuEventSequence"], eventMessageType=name,
        eventDigestSha256=uart.compute_process_event_digest(name, payload)))


def test_retained_original_bytes_survive_queries_until_exact_saved_confirmation(slot_lib):
    slot = (ctypes.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert freeze(slot_lib, slot) == 1
    held = query(slot_lib, slot)
    assert held["status"] == "HELD"
    out = ctypes.create_string_buffer(242)
    size = slot_lib.McuProcessEventSlot_CopyHeld(slot, out, len(out))
    original = uart.encode_payload("WORK_PREOPEN_WEIGHT_READY", process_values())
    assert out.raw[:size] == original
    assert held["eventDigestSha256"] == uart.compute_process_event_digest("WORK_PREOPEN_WEIGHT_READY", original)
    receipt = saved()
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert query(slot_lib, slot)["status"] == "RELEASED"
    assert slot_lib.McuProcessEventSlot_CopyHeld(slot, out, len(out)) == 0
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 2


@pytest.mark.parametrize("field,value", [("sessionUid", "55555555-5555-4555-8555-555555555555"),
    ("mcuCommandUid", "55555555-5555-4555-8555-555555555555"), ("portNo", 2), ("roundIndex", 2), ("configVersion", 8)])
def test_a_valid_measurement_cannot_be_attached_to_a_different_original_scope(slot_lib, field, value):
    slot = (ctypes.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    values = dict(process_values(), **{field: value})
    assert freeze(slot_lib, slot, values) == 0
    assert query(slot_lib, slot)["status"] == "NOT_FOUND"


def test_released_phase_cannot_be_remeasured_under_a_new_event_number(slot_lib):
    slot = (ctypes.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert freeze(slot_lib, slot) == 1
    receipt = saved()
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert freeze(slot_lib, slot, dict(process_values(), mcuEventSequence=4)) == 0


def test_old_receipt_cannot_release_the_next_phase_or_a_new_mcu_boot(slot_lib):
    slot = (ctypes.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert freeze(slot_lib, slot) == 1
    assert freeze(slot_lib, slot) == 1
    first = saved()
    wrong = first[:-1] + bytes([first[-1] ^ 1])
    assert slot_lib.McuProcessEventSlot_Saved(slot, wrong, len(wrong)) == 4
    second = dict(process_values("WORK_POSTCLOSE_WEIGHT_READY"), mcuEventSequence=4)
    scope = dict(process_scope(), eventMessageType="WORK_POSTCLOSE_WEIGHT_READY")
    assert freeze(slot_lib, slot, second, scope, "WORK_POSTCLOSE_WEIGHT_READY") == 0
    assert slot_lib.McuProcessEventSlot_Saved(slot, first, len(first)) == 1
    assert freeze(slot_lib, slot, second, scope, "WORK_POSTCLOSE_WEIGHT_READY") == 1
    assert slot_lib.McuProcessEventSlot_Saved(slot, first, len(first)) == 3
    assert query(slot_lib, slot, eventMessageType="WORK_POSTCLOSE_WEIGHT_READY")["status"] == "HELD"
    assert query(slot_lib, slot)["status"] == "NOT_FOUND"
    slot_lib.McuProcessEventSlot_Init(slot, 43)
    assert query(slot_lib, slot)["status"] == "BOOT_MISMATCH"
    assert slot_lib.McuProcessEventSlot_Saved(slot, first, len(first)) == 5


def test_same_business_step_with_conflicting_command_identity_is_not_reported_as_absent(slot_lib):
    slot = (ctypes.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert freeze(slot_lib, slot) == 1
    reply = query(slot_lib, slot, commandDigestSha256="55" * 32)
    assert reply["status"] == "IDENTITY_CONFLICT"
    assert reply["mcuEventSequence"] == 0 and reply["eventDigestSha256"] == "00" * 32
