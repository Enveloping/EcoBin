"""Actual C session fencing and atomic foreground configuration activation."""
import ctypes as c
import hashlib
import subprocess
from pathlib import Path

import pytest
import uart2_protocol as uart
from mcu_configuration import NativeMcuConfiguration
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_config_collection import WeightPolicy
from hardware.tests.test_native_command_session import CSession, CBindReply, CDecision, CCommand
from hardware.tests.test_mcu_work_state_c import query_payload
from hardware.tests.test_native_result_handoff import result_payload

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user, output = ROOT / "hardware_mcu/USER", tmp_path / "configuration.dll"
    signatures = {
        "McuConfiguration_Init": (None, [c.c_void_p, c.c_uint64, c.c_uint8]),
        "McuConfiguration_Receive": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint16,
            c.c_uint8, c.c_void_p, c.c_size_t, c.c_void_p]),
        "McuConfiguration_CopyActive": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuConfiguration_ReadWeightPolicy": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_void_p]),
        "McuConfiguration_IsStaging": (c.c_uint8, [c.c_void_p]),
        "McuSession_Init": (None, [c.c_void_p]),
        "McuSession_Probe": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_void_p]),
        "McuSession_Bind": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_uint64, c.c_void_p]),
        "McuWorkState_Init": (None, [c.c_void_p, c.c_uint64]),
        "McuSession_ReceiveCommand": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint16, c.c_void_p]),
        "McuWorkState_BeginAccepted": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint8]),
        "McuWorkState_Complete": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_Saved": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuWorkState_CopyHeld": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t]),
    }
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-I", str(user),
        "-DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1", str(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c"),
        *[str(user / (name + ".c")) for name in ("mcu_configuration", "mcu_config_collection", "mcu_session", "mcu_work_state", "mcu_result_slot")],
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(output)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = c.CDLL(str(output))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    state, work, session = (c.c_uint64 * 192)(), (c.c_uint64 * 38)(), CSession()
    lib.McuSession_Init(c.byref(session))
    assert lib.McuSession_Probe(c.byref(session), 1, c.byref(c.c_uint64()))
    assert lib.McuSession_Bind(c.byref(session), 1, 42, c.byref(CBindReply()))
    lib.McuWorkState_Init(work, 42)
    lib.McuConfiguration_Init(state, 42, 2)
    return lib, state, work, session


def receive(runtime, candidate, index, *, sequence=None, owner_error=0,
            application_uid="11111111-1111-4111-8111-111111111111", boot=42):
    lib, state, work, session = runtime
    sequence = index if sequence is None else sequence
    name, payload = candidate.encode_part(index, application_uid=application_uid,
        mcu_command_uid=f"00000000-0000-4000-8000-{sequence:012d}", target_mcu_boot_id=boot, command_sequence=sequence)
    decision = CDecision()
    assert lib.McuConfiguration_Receive(state, c.byref(session), work, owner_error,
        uart.MESSAGE_TYPE[name], payload, len(payload), c.byref(decision))
    return decision


def active(runtime):
    lib, state, _, _ = runtime
    output = c.create_string_buffer(512)
    length = lib.McuConfiguration_CopyActive(state, output, 512)
    return output.raw[:length]


def test_new_configuration_only_becomes_active_after_session_accepts_complete_commit(runtime):
    lib, state, _, session = runtime
    candidate = NativeMcuConfiguration(**inputs())
    assert active(runtime) == b"" and not lib.McuConfiguration_IsStaging(state)
    for index in range(1, candidate.part_count):
        decision = receive(runtime, candidate, index)
        assert (decision.outcome, decision.execute, session.highest) == (1, 1, index)
        assert active(runtime) == b"" and lib.McuConfiguration_IsStaging(state)
    decision = receive(runtime, candidate, candidate.part_count)
    assert (decision.outcome, decision.execute) == (1, 1)
    assert active(runtime) == candidate.digest_preimage and not lib.McuConfiguration_IsStaging(state)
    policy = WeightPolicy()
    assert lib.McuConfiguration_ReadWeightPolicy(state, 1, c.byref(policy))
    assert policy.config_version == 8 and policy.measurement.timeout == 5000


def versioned_candidate(version, content_sha256=None):
    values = inputs()
    original = NativeMcuConfiguration(**values)
    preimage = bytearray(original.digest_preimage)
    offset = len(bytes.fromhex(uart.REGISTRY["digestProfiles"]["mcuPayloadSha256"]["domainHex"]))
    preimage[offset:offset + 8] = version.to_bytes(8, "big")
    if content_sha256 is not None:
        preimage[offset + 8:offset + 40] = bytes.fromhex(content_sha256)
        values["content_sha256"] = content_sha256
    return NativeMcuConfiguration(**(values | {"config_version": version,
        "expected_sha256": hashlib.sha256(preimage).hexdigest()}))


def test_newer_application_keeps_old_active_bank_until_its_complete_commit(runtime):
    lib, state, _, _ = runtime
    old, new = versioned_candidate(8), versioned_candidate(9)
    for index in range(1, 6):
        assert receive(runtime, old, index).execute
    for index in range(1, 5):
        decision = receive(runtime, new, index, sequence=index + 5,
            application_uid="99999999-9999-4999-8999-999999999999")
        assert (decision.outcome, decision.execute) == (1, 1)
        assert active(runtime) == old.digest_preimage and lib.McuConfiguration_IsStaging(state)
    assert receive(runtime, new, 5, sequence=10,
        application_uid="99999999-9999-4999-8999-999999999999").execute
    assert active(runtime) == new.digest_preimage and not lib.McuConfiguration_IsStaging(state)


def test_duplicate_old_and_previously_rejected_commands_never_activate_again(runtime):
    lib, state, _, session = runtime
    candidate = versioned_candidate(8)
    assert receive(runtime, candidate, 1).execute
    rejected = receive(runtime, candidate, 5, sequence=2)  # missing device/ports
    assert (rejected.outcome, rejected.error, rejected.execute) == (2, 8, 0)
    assert active(runtime) == b"" and lib.McuConfiguration_IsStaging(state)
    repeat = receive(runtime, candidate, 5, sequence=2)
    assert (repeat.outcome, repeat.error, repeat.execute) == (2, 8, 0)
    for index in (2, 3, 4):
        assert receive(runtime, candidate, index, sequence=index + 1).execute
    assert receive(runtime, candidate, 5, sequence=6).execute
    before, high = bytes(state), session.highest
    repeated = receive(runtime, candidate, 5, sequence=6, owner_error=7)
    assert (repeated.outcome, repeated.execute) == (1, 0)  # cached acceptance, not a new application
    old = receive(runtime, candidate, 1)
    assert (old.outcome, old.execute) == (5, 0)
    assert bytes(state) == before and session.highest == high


def test_running_work_and_held_result_block_configuration_without_clearing_either(runtime):
    lib, state, work, session = runtime
    candidate = versioned_candidate(8)
    for index in range(1, 6):
        assert receive(runtime, candidate, index).execute
    original = query_payload(commandSequence=6)
    values = uart.decode_payload("QUERY_WORK", original)
    command = CCommand(42, 6)
    command.uid[:] = bytes.fromhex(values["mcuCommandUid"].replace("-", ""))
    command.digest[:] = bytes.fromhex(values["commandDigestSha256"])
    accepted = CDecision()
    assert lib.McuSession_ReceiveCommand(c.byref(session), c.byref(command), 0, c.byref(accepted)) and accepted.execute
    # Seeding the existing accepted-work API; business wire execution is not connected.
    assert lib.McuWorkState_BeginAccepted(work, original[8:], len(original) - 8, 16)
    before_work = bytes(work)
    decision = receive(runtime, candidate, 1, sequence=7)
    assert (decision.outcome, decision.error, decision.execute) == (2, 7, 0)
    assert bytes(work) == before_work and active(runtime) == candidate.digest_preimage
    result = result_payload(originCommandSequence=6)
    assert lib.McuWorkState_Complete(work, result, len(result))
    held = c.create_string_buffer(199)
    decision = receive(runtime, candidate, 1, sequence=8)
    assert (decision.outcome, decision.error, decision.execute) == (2, 7, 0)
    assert lib.McuWorkState_CopyHeld(work, held, 199) == 199 and held.raw == result
    assert lib.McuWorkState_Saved(work, result[:60], 60) == 1
    # The old rejection remains cached; only a newly authorized command can proceed.
    assert receive(runtime, candidate, 1, sequence=8).outcome == 2
    assert receive(runtime, candidate, 1, sequence=9).execute
    assert active(runtime) == candidate.digest_preimage


def test_other_owner_preconditions_remain_required_even_when_no_delivery_or_clean_work(runtime):
    lib, state, _, session = runtime
    candidate = versioned_candidate(8)
    decision = receive(runtime, candidate, 1, owner_error=11)
    assert (decision.outcome, decision.error, decision.execute) == (2, 11, 0)
    assert not lib.McuConfiguration_IsStaging(state) and active(runtime) == b""
    assert receive(runtime, candidate, 1).outcome == 2 and session.highest == 1
    assert receive(runtime, candidate, 1, sequence=2).execute


def test_version_rollback_conflicting_digest_and_repurposed_application_cannot_replace_active_config(runtime):
    old = versioned_candidate(8)
    for index in range(1, 6):
        assert receive(runtime, old, index).execute
    other_app = "99999999-9999-4999-8999-999999999999"
    for sequence, candidate, application in (
        (6, versioned_candidate(7), other_app),
        (7, versioned_candidate(8, "cd" * 32), other_app),
        (8, versioned_candidate(9), "11111111-1111-4111-8111-111111111111"),
    ):
        decision = receive(runtime, candidate, 1, sequence=sequence, application_uid=application)
        assert (decision.outcome, decision.error, decision.execute) == (2, 8, 0)
        assert active(runtime) == old.digest_preimage
    # A different application with identical version/settings is an explicit
    # new transaction, not a silent overwrite or version conflict.
    for index in range(1, 6):
        assert receive(runtime, old, index, sequence=index + 8, application_uid=other_app).execute
        assert active(runtime) == old.digest_preimage


def test_new_begin_may_supersede_only_staging_and_late_old_parts_do_not_pollute_it(runtime):
    old, abandoned, new = versioned_candidate(8), versioned_candidate(9), versioned_candidate(10)
    for index in range(1, 6):
        assert receive(runtime, old, index).execute
    app_b, app_c = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    assert receive(runtime, abandoned, 1, sequence=6, application_uid=app_b).execute
    assert receive(runtime, new, 1, sequence=7, application_uid=app_c).execute
    late = receive(runtime, abandoned, 2, sequence=8, application_uid=app_b)
    assert (late.outcome, late.error, late.execute) == (2, 8, 0)
    assert active(runtime) == old.digest_preimage
    for index in range(2, 6):
        assert receive(runtime, new, index, sequence=index + 7, application_uid=app_c).execute
    assert active(runtime) == new.digest_preimage


def test_wrong_boot_bad_digest_and_identity_conflict_do_not_consume_or_apply(runtime):
    lib, state, work, session = runtime
    candidate = versioned_candidate(8)
    assert receive(runtime, candidate, 1, boot=43).outcome == 3
    assert session.highest == 0 and not lib.McuConfiguration_IsStaging(state)
    name, payload = candidate.encode_part(1, application_uid="11111111-1111-4111-8111-111111111111",
        mcu_command_uid="22222222-2222-4222-8222-222222222222", target_mcu_boot_id=42, command_sequence=1)
    invalid = payload[:16] + bytes([payload[16] ^ 1]) + payload[17:]
    decision = CDecision()
    assert not lib.McuConfiguration_Receive(state, c.byref(session), work, 0, uart.MESSAGE_TYPE[name],
        invalid, len(invalid), c.byref(decision))
    assert session.highest == 0 and not lib.McuConfiguration_IsStaging(state)
    assert receive(runtime, candidate, 1).execute
    before = bytes(state)
    assert lib.McuConfiguration_Receive(state, c.byref(session), work, 0, uart.MESSAGE_TYPE[name],
        payload, len(payload), c.byref(decision))  # same sequence, different command UID
    assert (decision.outcome, decision.execute) == (4, 0)
    assert bytes(state) == before and session.highest == 1


def test_pi_probe_retains_active_bank_but_actual_mcu_reset_loses_ram_without_reapplying_old_config(runtime):
    lib, state, work, session = runtime
    candidate = versioned_candidate(8)
    for index in range(1, 6):
        assert receive(runtime, candidate, index).execute
    boot = c.c_uint64()
    assert lib.McuSession_Probe(c.byref(session), 2, c.byref(boot)) and boot.value == 42
    assert active(runtime) == candidate.digest_preimage
    lib.McuSession_Init(c.byref(session))
    lib.McuWorkState_Init(work, 0)
    lib.McuConfiguration_Init(state, 0, 2)
    assert active(runtime) == b"" and not lib.McuConfiguration_IsStaging(state)
    assert receive(runtime, candidate, 1, sequence=6).outcome == 3
    assert session.highest == 0 and active(runtime) == b""


def test_whole_digest_failure_is_cached_and_preserves_previous_active_configuration(runtime):
    lib, state, work, session = runtime
    old, new = versioned_candidate(8), versioned_candidate(9)
    for index in range(1, 6):
        assert receive(runtime, old, index).execute
    app = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    for index in range(1, 5):
        if index != 2:
            assert receive(runtime, new, index, sequence=index + 5, application_uid=app).execute
            continue
        name, payload = new.encode_part(index, application_uid=app,
            mcu_command_uid="22222222-2222-4222-8222-222222222222", target_mcu_boot_id=42, command_sequence=7)
        values = uart.decode_payload(name, payload) | {"negativeWeightThresholdGrams": 501}
        values["commandDigestSha256"] = uart.compute_command_digest(name, values)
        payload = uart.encode_payload(name, values)  # command SHA correct, complete-set SHA will differ
        decision = CDecision()
        assert lib.McuConfiguration_Receive(state, c.byref(session), work, 0, uart.MESSAGE_TYPE[name],
            payload, len(payload), c.byref(decision)) and decision.execute
    rejected = receive(runtime, new, 5, sequence=10, application_uid=app)
    assert (rejected.outcome, rejected.error, rejected.execute) == (2, 5, 0)
    assert active(runtime) == old.digest_preimage and lib.McuConfiguration_IsStaging(state)
    assert receive(runtime, new, 5, sequence=10, application_uid=app).outcome == 2
    for index in range(1, 6):
        assert receive(runtime, new, index, sequence=index + 10,
            application_uid="cccccccc-cccc-4ccc-8ccc-cccccccccccc").execute
    assert active(runtime) == new.digest_preimage


def test_configuration_runtime_target_compile(tmp_path):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required")
    output = tmp_path / "configuration.o"
    run = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
        "-I", str(ROOT / "hardware_mcu/USER"), "-c", str(ROOT / "hardware_mcu/USER/mcu_configuration.c"),
        "-o", str(output)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not run.stdout.strip() and not run.stderr.strip()
    sizes = subprocess.run([str(compiler.with_name("fromelf.exe")), "--text", "-z", str(output)],
        capture_output=True, text=True, timeout=30)
    assert sizes.returncode == 0, sizes.stdout + sizes.stderr
    print(sizes.stdout)
