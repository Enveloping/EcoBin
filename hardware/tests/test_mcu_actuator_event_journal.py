"""Actual C bounded evidence reservation; no mechanical output or wire ACK."""
import ctypes
import subprocess
from pathlib import Path

import pytest
import uart2_protocol as uart
from contracts.tests.test_uart_v2_actuator_events import actuator_values

ROOT = Path(__file__).resolve().parents[2]


class Reservation(ctypes.Structure):
    _fields_ = [("boot_id", ctypes.c_uint64), ("number", ctypes.c_uint32)]


@pytest.fixture(scope="module")
def journal_lib(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    target = tmp_path_factory.mktemp("actuator-journal") / "journal.dll"
    signatures = {
        "Init": ([ctypes.c_void_p, ctypes.c_uint64], None),
        "Reserve": ([ctypes.c_void_p, ctypes.c_uint8, ctypes.POINTER(Reservation)], ctypes.c_uint8),
        "Freeze": ([ctypes.c_void_p, ctypes.POINTER(Reservation), ctypes.c_uint8, ctypes.c_uint8,
                    ctypes.c_void_p, ctypes.c_size_t], ctypes.c_uint8),
        "CopyNextHeld": ([ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8),
                          ctypes.c_void_p, ctypes.c_size_t], ctypes.c_size_t),
        "ConfirmSaved": ([ctypes.c_void_p, ctypes.c_uint8, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_uint8),
        "Cancel": ([ctypes.c_void_p, ctypes.POINTER(Reservation)], ctypes.c_uint8),
        "MemberSaved": ([ctypes.c_void_p, ctypes.POINTER(Reservation), ctypes.c_uint8, ctypes.c_uint8], ctypes.c_uint8),
    }
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1",
        str(ROOT / "hardware_mcu/USER/mcu_actuator_event_journal.c"),
        str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        *["-Wl,/EXPORT:McuActuatorEventJournal_" + name for name in signatures], "-o", str(target)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = ctypes.CDLL(str(target))
    for name, (arguments, result) in signatures.items():
        function = getattr(lib, "McuActuatorEventJournal_" + name)
        function.argtypes, function.restype = arguments, result
    return lib


def journal(lib, boot=42):
    instance = (ctypes.c_uint64 * 96)()
    lib.McuActuatorEventJournal_Init(instance, boot)
    return instance


def reserve(lib, instance, count=2):
    token = Reservation()
    assert lib.McuActuatorEventJournal_Reserve(instance, count, ctypes.byref(token)) == 1
    return token


def payload(name="CLEAN_LOCK_POWER_CHANGED", **changes):
    return uart.encode_payload(name, actuator_values(name, **changes))


def freeze(lib, instance, token, member, body, name="CLEAN_LOCK_POWER_CHANGED"):
    return lib.McuActuatorEventJournal_Freeze(instance, ctypes.byref(token), member,
        uart.MESSAGE_TYPE[name], body, len(body))


def next_held(lib, instance, after=0, capacity=242):
    output, name = ctypes.create_string_buffer(242), ctypes.c_uint8()
    size = lib.McuActuatorEventJournal_CopyNextHeld(instance, after, ctypes.byref(name), output, capacity)
    return (name.value, output.raw[:size]) if size else None


def test_lock_off_has_reserved_space_while_lock_on_is_still_unconfirmed(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance)
    on = payload()
    off = payload(mcuEventSequence=8, uptimeMs=2000, lockPowerState="DEENERGIZED")
    assert freeze(lib, instance, token, 0, on) == 1
    assert freeze(lib, instance, token, 1, off) == 1
    assert next_held(lib, instance) == (uart.MESSAGE_TYPE["CLEAN_LOCK_POWER_CHANGED"], on)
    assert next_held(lib, instance, 7) == (uart.MESSAGE_TYPE["CLEAN_LOCK_POWER_CHANGED"], off)
    assert next_held(lib, instance, 8) is None


def confirm(lib, instance, body, name="CLEAN_LOCK_POWER_CHANGED"):
    return lib.McuActuatorEventJournal_ConfirmSaved(instance, uart.MESSAGE_TYPE[name], body, len(body))


def test_saved_member_proof_rejects_held_wrong_identity_and_reclaimed_evidence(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance, 1)
    name, body = "CLEAN_LOCK_POWER_CHANGED", payload()

    def saved(reservation=token, member=0, message=name):
        return lib.McuActuatorEventJournal_MemberSaved(instance, ctypes.byref(reservation), member, uart.MESSAGE_TYPE[message])

    assert not saved()
    assert freeze(lib, instance, token, 0, body)
    assert not saved()
    assert confirm(lib, instance, body) == 1
    assert saved()
    assert not saved(Reservation(43, token.number)) and not saved(Reservation(42, 0))
    assert not saved(member=1) and not saved(message="DELIVERY_CYCLE_ABORTED")
    reserve(lib, instance, 8)
    assert not saved()


def test_exact_saved_body_releases_only_that_edge_and_duplicate_freeze_cannot_resurrect_it(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance)
    on = payload()
    off = payload(mcuEventSequence=8, uptimeMs=2000, lockPowerState="DEENERGIZED")
    assert freeze(lib, instance, token, 0, on)
    assert freeze(lib, instance, token, 1, off)
    assert confirm(lib, instance, payload(solenoidHealth="UNKNOWN")) == 4
    assert next_held(lib, instance)[1] == on
    assert confirm(lib, instance, on) == 1
    assert confirm(lib, instance, on) == 2
    assert freeze(lib, instance, token, 0, on) == 1
    assert next_held(lib, instance)[1] == off
    assert confirm(lib, instance, off) == 1
    assert next_held(lib, instance) is None


def test_only_an_entirely_unstarted_reservation_can_be_cancelled(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance, 8)
    assert lib.McuActuatorEventJournal_Cancel(instance, ctypes.byref(token)) == 1
    replacement = reserve(lib, instance, 8)
    assert replacement.number != token.number
    assert freeze(lib, instance, token, 0, payload()) == 0
    assert freeze(lib, instance, replacement, 0, payload()) == 1
    assert confirm(lib, instance, payload()) == 1
    # Saving ON must not permit cancelling the reserved OFF evidence.
    assert lib.McuActuatorEventJournal_Cancel(instance, ctypes.byref(replacement)) == 0
    assert freeze(lib, instance, replacement, 1,
        payload(mcuEventSequence=8, lockPowerState="DEENERGIZED")) == 1


def test_full_queue_cannot_steal_off_reservation_or_partially_reserve_more_space(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    lock = reserve(lib, instance, 2)
    untouched = Reservation(123, 456)
    assert lib.McuActuatorEventJournal_Reserve(instance, 7, ctypes.byref(untouched)) == 0
    assert (untouched.boot_id, untouched.number) == (123, 456)
    other = reserve(lib, instance, 6)
    assert freeze(lib, instance, lock, 0, payload(mcuEventSequence=1))
    for index in range(6):
        assert freeze(lib, instance, other, index, payload(mcuEventSequence=index + 2))
    assert lib.McuActuatorEventJournal_Reserve(instance, 1, ctypes.byref(untouched)) == 0
    off = payload(mcuEventSequence=8, lockPowerState="DEENERGIZED")
    assert freeze(lib, instance, lock, 1, off)
    assert [uart.decode_payload("CLEAN_LOCK_POWER_CHANGED", next_held(lib, instance, i)[1])["mcuEventSequence"]
            for i in range(8)] == list(range(1, 9))
    assert confirm(lib, instance, payload(mcuEventSequence=1)) == 1
    assert lib.McuActuatorEventJournal_Reserve(instance, 2, ctypes.byref(untouched)) == 0
    single = reserve(lib, instance, 1)
    assert confirm(lib, instance, payload(mcuEventSequence=1)) == 3  # released cache reclaimed
    assert freeze(lib, instance, lock, 0, payload(mcuEventSequence=9)) == 0
    assert freeze(lib, instance, single, 0, payload(mcuEventSequence=9))
    assert next_held(lib, instance, 7)[1] == off


@pytest.mark.parametrize("name", uart.REGISTRY["sessionPolicy"]["actuatorEventMessages"])
def test_all_three_output_types_keep_exact_bytes_and_do_not_deduplicate_real_later_actions(journal_lib, name):
    lib, instance = journal_lib, journal(journal_lib)
    first, second = payload(name), payload(name, mcuEventSequence=8, uptimeMs=2000)
    token = reserve(lib, instance)
    source = ctypes.create_string_buffer(first)
    assert freeze(lib, instance, token, 0, source, name) == 0  # includes trailing NUL, wrong length
    assert lib.McuActuatorEventJournal_Freeze(instance, ctypes.byref(token), 0,
        uart.MESSAGE_TYPE[name], source, len(first)) == 1
    source[0] = b"\xff"
    assert next_held(lib, instance) == (uart.MESSAGE_TYPE[name], first)
    assert freeze(lib, instance, token, 1, second, name) == 1
    assert confirm(lib, instance, second, name) == 1  # receipt order need not follow event order
    assert next_held(lib, instance) == (uart.MESSAGE_TYPE[name], first)
    assert confirm(lib, instance, first, name) == 1
    assert next_held(lib, instance) is None


@pytest.mark.parametrize("changes", [dict(mcuBootId=43), dict(mcuEventSequence=6),
    dict(mcuCommandUid="99999999-9999-4999-8999-999999999999"),
    dict(operationUid="99999999-9999-4999-8999-999999999999"), dict(portNo=2),
    dict(lockPowerState="DEENERGIZED"), dict(uptimeMs=1001)])
def test_valid_but_wrong_saved_evidence_cannot_release_an_edge(journal_lib, changes):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance)
    original = payload()
    assert freeze(lib, instance, token, 0, original)
    status = confirm(lib, instance, payload(**changes))
    assert status == (5 if "mcuBootId" in changes else 3 if "mcuEventSequence" in changes else 4)
    assert next_held(lib, instance)[1] == original


@pytest.mark.parametrize("name", uart.REGISTRY["sessionPolicy"]["actuatorEventMessages"])
@pytest.mark.parametrize("damage", ["short", "long", "zero_command", "zero_boot", "oversized", "unknown_message"])
def test_invalid_content_does_not_consume_the_reserved_slot_or_advance_identity(journal_lib, name, damage):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance)
    raw, message = bytearray(payload(name, mcuEventSequence=99)), uart.MESSAGE_TYPE[name]
    if damage == "short":
        raw = raw[:-1]
    elif damage == "long":
        raw += b"\0"
    elif damage == "zero_command":
        field = next(f for f in uart.MESSAGE_SPECS[name]["fields"] if f["name"] == "mcuCommandUid")
        raw[field["offset"]:field["offset"] + 16] = bytes(16)
    elif damage == "zero_boot":
        raw[:8] = bytes(8)
    elif damage == "oversized":
        raw += bytes(65536)
    else:
        message = 254
    raw = bytes(raw)
    assert lib.McuActuatorEventJournal_Freeze(instance, ctypes.byref(token), 0, message, raw, len(raw)) == 0
    assert lib.McuActuatorEventJournal_ConfirmSaved(instance, message, raw, len(raw)) == 0
    assert next_held(lib, instance) is None
    assert freeze(lib, instance, token, 0, payload(name), name) == 1


@pytest.mark.parametrize("boot", [0, 9007199254740992, 18446744073709551615])
def test_unbound_or_invalid_boot_never_reserves_or_accepts_an_event(journal_lib, boot):
    lib, instance = journal_lib, journal(journal_lib, boot)
    token = Reservation(42, 1)
    assert lib.McuActuatorEventJournal_Reserve(instance, 1, ctypes.byref(token)) == 0
    assert freeze(lib, instance, token, 0, payload()) == 0
    assert next_held(lib, instance) is None


def test_mcu_reset_retires_tokens_and_receipts_even_when_reservation_counter_restarts(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    old = reserve(lib, instance)
    assert freeze(lib, instance, old, 0, payload())
    lib.McuActuatorEventJournal_Init(instance, 43)
    fresh = reserve(lib, instance)
    assert fresh.number == old.number and fresh.boot_id != old.boot_id
    assert next_held(lib, instance) is None
    assert freeze(lib, instance, old, 0, payload(mcuBootId=43)) == 0
    assert lib.McuActuatorEventJournal_Cancel(instance, ctypes.byref(old)) == 0
    assert confirm(lib, instance, payload()) == 5
    assert freeze(lib, instance, fresh, 0, payload(mcuBootId=43))


def test_same_event_sequence_cannot_be_reused_across_reservations_or_message_types(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    first, second = reserve(lib, instance), reserve(lib, instance)
    original = payload()
    assert freeze(lib, instance, first, 0, original)
    other = payload("DELIVERY_DOOR_COMMAND_RESULT")
    assert freeze(lib, instance, second, 0, other, "DELIVERY_DOOR_COMMAND_RESULT") == 0
    assert confirm(lib, instance, other, "DELIVERY_DOOR_COMMAND_RESULT") == 4
    assert confirm(lib, instance, original) == 1
    assert freeze(lib, instance, second, 0, original) == 0
    assert freeze(lib, instance, second, 0, payload(mcuEventSequence=6)) == 0
    assert freeze(lib, instance, second, 0, payload(mcuEventSequence=8)) == 1


def test_sequence_maximum_remains_readable_and_confirmable_but_never_wraps(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance)
    last = payload(mcuEventSequence=0xFFFFFFFF)
    assert freeze(lib, instance, token, 0, last)
    assert next_held(lib, instance, 0xFFFFFFFE)[1] == last
    assert next_held(lib, instance, 0xFFFFFFFF) is None
    assert confirm(lib, instance, last) == 1
    assert freeze(lib, instance, token, 1, payload()) == 0
    assert lib.McuActuatorEventJournal_Reserve(instance, 1, ctypes.byref(Reservation())) == 0


def test_reclaimed_on_slot_does_not_make_pending_off_cancellable(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance)
    assert freeze(lib, instance, token, 0, payload())
    assert confirm(lib, instance, payload()) == 1
    reserve(lib, instance, 7)  # reuse ON plus all six never-reserved slots
    assert lib.McuActuatorEventJournal_Cancel(instance, ctypes.byref(token)) == 0
    assert freeze(lib, instance, token, 1, payload(mcuEventSequence=8, lockPowerState="DEENERGIZED")) == 1


@pytest.mark.parametrize("count", [0, 9, 255])
def test_bad_reservation_size_does_not_change_remaining_capacity(journal_lib, count):
    lib, instance = journal_lib, journal(journal_lib)
    assert lib.McuActuatorEventJournal_Reserve(instance, count, ctypes.byref(Reservation())) == 0
    reserve(lib, instance, 8)


def test_read_buffer_shortage_and_forged_member_do_not_change_retained_records(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance)
    original = payload()
    assert freeze(lib, instance, token, 2, original) == 0
    assert freeze(lib, instance, Reservation(42, 999), 0, original) == 0
    assert freeze(lib, instance, token, 0, original)
    assert next_held(lib, instance, capacity=len(original) - 1) is None
    assert next_held(lib, instance)[1] == original


def test_many_reuse_cycles_never_reintroduce_released_edges_or_lose_reserved_capacity(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    for cycle in range(100):
        token = reserve(lib, instance, 8)
        events = [payload(mcuEventSequence=cycle * 8 + i + 1, uptimeMs=cycle * 1000 + i) for i in range(8)]
        for index, event in enumerate(events):
            assert freeze(lib, instance, token, index, event)
        for index in (3, 7, 2, 6, 1, 5, 0, 4):
            assert confirm(lib, instance, events[index]) == 1
            assert freeze(lib, instance, token, index, events[index]) == 1
        assert next_held(lib, instance) is None


def test_unused_capacity_is_preferred_so_lost_confirmation_can_still_be_repeated(journal_lib):
    lib, instance = journal_lib, journal(journal_lib)
    token = reserve(lib, instance, 1)
    original = payload()
    assert freeze(lib, instance, token, 0, original)
    assert confirm(lib, instance, original) == 1
    reserve(lib, instance, 1)
    assert confirm(lib, instance, original) == 2
