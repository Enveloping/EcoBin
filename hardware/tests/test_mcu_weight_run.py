"""Real C configured measurement owner; serial ownership remains an ingress precondition."""
import ctypes as c
import subprocess
from pathlib import Path

import pytest

from hardware.tests.test_mcu_config_collection import WeightPolicy, collection, configuration_parts, offer
from hardware.tests.test_mcu_device_facts import ScaleObservation, WeightResult, scale_frame
from hardware.tests.test_mcu_result_builder import Measurement, builder, work
from hardware.tests.test_mcu_process_measurement import Meta, producer
from hardware.tests.test_mcu_process_event_slot import slot_lib
from hardware.tests.test_mcu_work_state_c import query_payload
from edge_store import EdgeStore
import uart2_protocol as uart

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    target = tmp_path_factory.mktemp("weight-run") / "run.dll"
    signatures = {
        "McuWeightRun_Init": (None, [c.c_void_p]),
        "McuWeightRun_StartIdleAttempt": (c.c_uint32, [c.c_void_p, c.c_void_p, c.c_uint64]),
        "McuWeightRun_FinishIdleAttempt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint64, c.c_uint64, c.c_void_p, c.c_size_t]),
        "McuWeightRun_CancelIdleAttempt": (None, [c.c_void_p, c.c_uint64]),
        "McuWeightRun_Begin": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint32, c.c_uint64]),
        "McuWeightRun_StartOwnedAttempt": (c.c_uint32, [c.c_void_p, c.c_uint64]),
        "McuWeightRun_FinishOwnedAttempt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint32,
            c.c_uint64, c.c_uint64, c.c_void_p, c.c_size_t]),
        "McuWeightRun_Poll": (c.c_uint8, [c.c_void_p, c.c_uint64]),
        "McuWeightRun_Copy": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p]),
        "McuWeightRun_CopyObservation": (c.c_uint8, [c.c_void_p, c.POINTER(ScaleObservation)]),
        "McuWeightRun_Retire": (c.c_uint8, [c.c_void_p, c.c_uint32]),
        "McuWeightRun_Interrupt": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint64]),
    }
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-I", str(user), *[str(user / (name + ".c")) for name in
            ("mcu_weight_run", "weight_measurement", "scale_reader")],
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(target)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = c.CDLL(str(target))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    return lib


@pytest.fixture
def policy(collection):
    lib, state = collection
    parts, _ = configuration_parts()
    for part in parts:
        assert offer(collection, part) in (1, 2)
    result = WeightPolicy()
    assert lib.McuConfigCollection_ReadWeightPolicy(state, 1, c.byref(result))
    return result


def state(runner, policy, *, sequence=1, start=100):
    memory = (c.c_uint64 * 80)()  # Includes independent idle-read policy; never copy old measurements.
    runner.McuWeightRun_Init(memory)
    assert runner.McuWeightRun_Begin(memory, c.byref(policy), sequence, start)
    return memory


def copy(runner, memory):
    result, policy = WeightResult(), WeightPolicy()
    assert runner.McuWeightRun_Copy(memory, c.byref(result), c.byref(policy))
    return result, policy


def sample(runner, memory, now, grams, *, measurement=1, delay=20):
    attempt = runner.McuWeightRun_StartOwnedAttempt(memory, now)
    assert attempt
    frame = scale_frame(grams)
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, measurement, attempt,
        now + delay, now + delay, frame, len(frame))
    return attempt


def observation(runner, memory):
    result = ScaleObservation()
    assert runner.McuWeightRun_CopyObservation(memory, c.byref(result))
    return result


def test_observation_retains_original_calibration_until_a_new_owned_read(runner, policy):
    memory = state(runner, policy)
    untouched = ScaleObservation(999, 99, 88, 777, 6, 5)
    before = bytes(untouched)
    assert not runner.McuWeightRun_CopyObservation(memory, c.byref(untouched))
    assert bytes(untouched) == before
    for index in range(5):
        sample(runner, memory, 100 + index * 250, 500)
    retained = observation(runner, memory)
    assert (retained.captured, retained.attempt, retained.calibration, retained.grams,
        retained.port, retained.status) == (1120, 5, policy.calibration_version, 500, policy.port_no, 0)
    assert runner.McuWeightRun_Retire(memory, 1)
    policy.calibration_version += 1
    assert runner.McuWeightRun_Begin(memory, c.byref(policy), 2, 2000)
    assert runner.McuWeightRun_StartOwnedAttempt(memory, 2000) == 6
    assert bytes(observation(runner, memory)) == bytes(retained)
    frame = scale_frame(0)
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, 2, 6, 2020, 2100, frame, len(frame))
    actual = observation(runner, memory)
    assert (actual.status, actual.grams, actual.captured, actual.calibration) == (0, 0, 2020, policy.calibration_version)


def test_new_idle_read_updates_health_but_preserves_old_failed_measurement(runner, policy):
    memory = state(runner, policy)
    runner.McuWeightRun_Poll(memory, 5100)
    old_result, old_policy = copy(runner, memory)
    assert old_result.status == 3 and runner.McuWeightRun_Retire(memory, 1)
    policy.calibration_version += 1
    attempt = runner.McuWeightRun_StartIdleAttempt(memory, c.byref(policy), 5500)
    assert attempt == 1
    frame = scale_frame(-100)
    assert not runner.McuWeightRun_FinishOwnedAttempt(memory, 1, attempt, 5520, 5600, frame, len(frame))
    assert runner.McuWeightRun_FinishIdleAttempt(memory, attempt, 5520, 5600, frame, len(frame))
    current = observation(runner, memory)
    assert (current.status, current.grams, current.calibration) == (0, -100, policy.calibration_version)
    retained, retained_policy = copy(runner, memory)
    assert bytes(retained) == bytes(old_result) and bytes(retained_policy) == bytes(old_policy)
    assert runner.McuWeightRun_Begin(memory, c.byref(policy), 2, 6000)
    assert runner.McuWeightRun_StartOwnedAttempt(memory, 6000) == attempt + 1


def test_idle_timeout_and_cancel_never_become_business_measurements(runner, policy):
    memory = (c.c_uint64 * 80)()
    runner.McuWeightRun_Init(memory)
    assert runner.McuWeightRun_StartIdleAttempt(memory, c.byref(policy), 100) == 1
    assert not runner.McuWeightRun_Begin(memory, c.byref(policy), 1, 150)
    runner.McuWeightRun_Poll(memory, 300)
    previous = observation(runner, memory)
    assert (previous.status, previous.captured, previous.attempt) == (2, 300, 1)
    assert not runner.McuWeightRun_Copy(memory, c.byref(WeightResult()), c.byref(WeightPolicy()))
    assert runner.McuWeightRun_StartIdleAttempt(memory, c.byref(policy), 400) == 2
    runner.McuWeightRun_CancelIdleAttempt(memory, 410)
    frame = scale_frame(1000)
    assert not runner.McuWeightRun_FinishIdleAttempt(memory, 2, 420, 420, frame, len(frame))
    assert bytes(observation(runner, memory)) == bytes(previous)
    assert runner.McuWeightRun_Begin(memory, c.byref(policy), 1, 500)
    assert not runner.McuWeightRun_StartIdleAttempt(memory, c.byref(policy), 750)


@pytest.mark.parametrize("start,poll,timeout", [(4900, 5100, True), (4901, 5300, False), (5050, 5300, False)])
def test_phase_deadline_does_not_invent_a_timeout_after_cancelled_request(runner, policy, start, poll, timeout):
    memory = state(runner, policy)  # phase ends at 5100
    sample(runner, memory, 100, 500)
    previous = bytes(observation(runner, memory))
    assert runner.McuWeightRun_StartOwnedAttempt(memory, start) == 2
    assert runner.McuWeightRun_Poll(memory, poll)
    result = observation(runner, memory)
    if timeout:
        assert (result.status, result.attempt, result.captured, result.grams) == (2, 2, 5100, 0)
    else:
        assert bytes(result) == previous
    assert copy(runner, memory)[0].status == 3


def test_interruption_is_not_a_scale_timeout_and_rejected_frame_does_not_replace_observation(runner, policy):
    memory = state(runner, policy)
    sample(runner, memory, 100, 500)
    retained = bytes(observation(runner, memory))
    assert runner.McuWeightRun_StartOwnedAttempt(memory, 350) == 2
    assert runner.McuWeightRun_Interrupt(memory, 1, 400)
    raw = scale_frame(999)
    assert not runner.McuWeightRun_FinishOwnedAttempt(memory, 1, 2, 410, 410, raw, len(raw))
    assert runner.McuWeightRun_Poll(memory, 5100)
    assert bytes(observation(runner, memory)) == retained
    assert copy(runner, memory)[0].status == 6


@pytest.mark.parametrize("extra,accepted", [(0, True), (1, False)])
def test_complete_capture_at_response_deadline_precedes_poll_across_32bit_wrap(runner, policy, extra, accepted):
    began = (1 << 32) - 100
    memory = state(runner, policy, start=began)
    assert runner.McuWeightRun_StartOwnedAttempt(memory, began) == 1
    deadline = began + policy.response_timeout_ms
    raw = scale_frame(-10)
    assert bool(runner.McuWeightRun_FinishOwnedAttempt(memory, 1, 1, deadline + extra,
        began + 1000, raw, len(raw))) is accepted
    assert runner.McuWeightRun_Poll(memory, began + 1000)
    read = observation(runner, memory)
    assert read.captured == deadline and read.attempt == 1
    assert (read.status, read.grams) == ((0, -10) if accepted else (2, 0))


def test_verified_config_and_real_frames_produce_one_stable_mean(runner, policy):
    memory = state(runner, policy)
    for index, grams in enumerate((450, 500, 550, 510, 490)):
        sample(runner, memory, 100 + index * 250, grams)
    result, used = copy(runner, memory)
    assert (result.status, result.available, result.grams, result.count, result.span) == (1, 1, 500, 5, 100)
    assert result.elapsed == 1020 and result.sequence == 1
    assert used.config_version == policy.config_version and used.calibration_version == policy.calibration_version
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, 2000)


def test_interruption_retains_partial_samples_and_rejects_old_reply_without_restarting(runner, policy):
    memory = state(runner, policy)
    sample(runner, memory, 100, 450)
    pending = runner.McuWeightRun_StartOwnedAttempt(memory, 350)
    assert pending
    assert runner.McuWeightRun_Interrupt(memory, 1, 400)
    result, used = copy(runner, memory)
    assert (result.status, result.available, result.grams, result.count, result.elapsed) == (6, 0, 0, 1, 300)
    assert used.config_version == policy.config_version
    raw = scale_frame(999)
    assert not runner.McuWeightRun_FinishOwnedAttempt(memory, 1, pending, 410, 410, raw, len(raw))
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, 1000)
    assert runner.McuWeightRun_Poll(memory, 5100)
    assert bytes(copy(runner, memory)[0]) == bytes(result)
    assert runner.McuWeightRun_Retire(memory, 1)
    assert runner.McuWeightRun_Begin(memory, c.byref(policy), 2, 5200)
    assert runner.McuWeightRun_StartOwnedAttempt(memory, 5200) == pending + 1


@pytest.mark.parametrize("mode,expected", [("stable", 1), ("median_due", 2), ("unavailable_due", 3)])
def test_interrupt_never_replaces_an_existing_or_already_due_result(runner, policy, mode, expected):
    memory = state(runner, policy)
    if mode == "stable":
        for index in range(5):
            sample(runner, memory, 100 + index * 250, 500)
        before = bytes(copy(runner, memory)[0])
    elif mode == "median_due":
        for index, grams in enumerate((0, 400, 0, 400, 0)):
            sample(runner, memory, 3900 + index * 250, grams)
    assert runner.McuWeightRun_Interrupt(memory, 1, 5100)
    result = copy(runner, memory)[0]
    assert result.status == expected
    if mode == "stable":
        assert bytes(result) == before
    else:
        assert result.elapsed == 5000
    assert runner.McuWeightRun_Interrupt(memory, 1, 6100)
    assert bytes(copy(runner, memory)[0]) == bytes(result)


def test_wrong_or_retired_interruption_never_changes_measurement_or_counter(runner, policy):
    memory = state(runner, policy)
    sample(runner, memory, 100, 400)
    before = bytes(memory)
    assert not runner.McuWeightRun_Interrupt(memory, 2, 400)
    assert not runner.McuWeightRun_Interrupt(memory, 1, 119)
    assert bytes(memory) == before
    assert runner.McuWeightRun_Interrupt(memory, 1, 400)
    assert runner.McuWeightRun_Retire(memory, 1)
    before = bytes(memory)
    assert not runner.McuWeightRun_Interrupt(memory, 1, 500)
    assert bytes(memory) == before


def test_single_request_250ms_start_schedule_timeout_and_no_catchup_burst(runner, policy):
    memory = state(runner, policy)
    first = sample(runner, memory, 100, 500)
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, 349)
    second = runner.McuWeightRun_StartOwnedAttempt(memory, 350)
    assert second == first + 1
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, 400)
    assert runner.McuWeightRun_Poll(memory, 549)
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, 549)
    assert runner.McuWeightRun_Poll(memory, 550)  # 200ms request deadline
    # The real ingress must resolve old-response ambiguity BEFORE another start.
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, 599)
    third = runner.McuWeightRun_StartOwnedAttempt(memory, 1700)  # foreground delayed
    assert third == second + 1
    raw = scale_frame(999)
    assert not runner.McuWeightRun_FinishOwnedAttempt(memory, 1, second, 1710, 1710, raw, len(raw))
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, 1, third, 1710, 1710, raw, len(raw))
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, 1710)  # no missed-period burst
    assert copy(runner, memory)[0].count == 2


def test_capture_window_and_exact_local_identity_precede_decode(runner, policy):
    memory = state(runner, policy)
    attempt = runner.McuWeightRun_StartOwnedAttempt(memory, 100)
    raw = scale_frame(500)
    for measurement, number, captured, now in (
        (2, attempt, 120, 120), (1, attempt + 1, 120, 120),
        (1, attempt, 99, 120), (1, attempt, 121, 120),
        (1, attempt, 301, 301),  # complete response exceeded the 200ms request deadline
    ):
        assert not runner.McuWeightRun_FinishOwnedAttempt(memory, measurement, number, captured, now, raw, len(raw))
        assert copy(runner, memory)[0].count == 0
    # Captured exactly at the deadline; delayed foreground is not a new sample time.
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, 1, attempt, 300, 400, raw, len(raw))
    assert not runner.McuWeightRun_FinishOwnedAttempt(memory, 1, attempt, 300, 400, raw, len(raw))
    assert copy(runner, memory)[0].count == 1


def test_original_policy_and_result_retained_until_exact_owner_retirement(runner, policy):
    memory = state(runner, policy)
    original = bytes(policy)
    policy.config_version += 1
    policy.calibration_version += 1
    policy.measurement.maximum = 400  # new config must not alter the running 500g measurement
    assert not runner.McuWeightRun_Retire(memory, 1)
    assert not runner.McuWeightRun_Begin(memory, c.byref(policy), 2, 100)
    for index in range(5):
        sample(runner, memory, 100 + index * 250, 500)
    result, used = copy(runner, memory)
    assert bytes(used) == original and result.grams == 500
    assert not runner.McuWeightRun_Begin(memory, c.byref(policy), 2, 2000)
    assert not runner.McuWeightRun_Retire(memory, 2)
    assert runner.McuWeightRun_Retire(memory, 1)  # owner has copied/frozen the original record
    assert not runner.McuWeightRun_Begin(memory, c.byref(policy), 1, 2000)
    assert runner.McuWeightRun_Begin(memory, c.byref(policy), 2, 2000)
    assert copy(runner, memory)[1].config_version == policy.config_version
    new_attempt = runner.McuWeightRun_StartOwnedAttempt(memory, 2000)
    assert new_attempt == 6  # no reset at a business phase boundary
    raw = scale_frame(500)
    assert not runner.McuWeightRun_FinishOwnedAttempt(memory, 1, 5, 2020, 2020, raw, len(raw))
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, 2, new_attempt, 2020, 2020, raw, len(raw))
    assert copy(runner, memory)[0].count == 0  # actual new range applied, no previous phase value


@pytest.mark.parametrize("field,value", [
    ("enabled", 0), ("port_no", 0), ("port_no", 7), ("config_version", 0),
    ("config_version", 9007199254740992), ("poll_interval_ms", 0), ("response_timeout_ms", 0),
    ("measurement.timeout", 0), ("measurement.minimum", 999999),
])
def test_invalid_or_disabled_policy_cannot_erase_retained_measurement(runner, policy, field, value):
    memory = state(runner, policy)
    assert runner.McuWeightRun_Poll(memory, 5100)
    assert runner.McuWeightRun_Retire(memory, 1)
    before = tuple(bytes(item) for item in copy(runner, memory))
    target = policy
    if "." in field:
        parent, field = field.split(".")
        target = getattr(target, parent)
    setattr(target, field, value)
    assert not runner.McuWeightRun_Begin(memory, c.byref(policy), 2, 5200)
    assert tuple(bytes(item) for item in copy(runner, memory)) == before


@pytest.mark.parametrize("start,processing_delay", [(100, 0), (0xFFFFFFF0, 0), (100, 0x100000000)])
def test_five_second_median_survives_32bit_wrap_and_delayed_foreground(runner, policy, start, processing_delay):
    memory = state(runner, policy, start=start)
    for index in range(19):
        sample(runner, memory, start + index * 250, -1000 if index % 2 else 1000)
    attempt = runner.McuWeightRun_StartOwnedAttempt(memory, start + 4750)
    raw = scale_frame(-1000)
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, 1, attempt,
        start + 4770, start + 4770 + processing_delay, raw, len(raw))
    assert runner.McuWeightRun_Poll(memory, start + 5000 + processing_delay)
    result, _ = copy(runner, memory)
    assert (result.status, result.available, result.grams, result.count, result.span, result.elapsed) == (2, 1, 0, 20, 2000, 5000)
    assert not runner.McuWeightRun_StartOwnedAttempt(memory, start + 5000 + processing_delay)


@pytest.mark.parametrize("bad", ["crc", "address", "length", "range", "null"])
def test_bad_owned_response_is_consumed_but_later_valid_reads_can_recover(runner, policy, bad):
    memory = state(runner, policy)
    attempt = runner.McuWeightRun_StartOwnedAttempt(memory, 100)
    raw = scale_frame(500)
    if bad == "crc": raw = raw[:-1] + bytes([raw[-1] ^ 1])
    elif bad == "address": raw = b"\x02" + raw[1:]
    elif bad == "length": raw = raw + raw
    elif bad == "range": raw = scale_frame(policy.measurement.maximum + 1)
    elif bad == "null": raw = None
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, 1, attempt, 120, 120, raw, len(raw) if raw else 0)
    assert copy(runner, memory)[0].count == 0
    # Replaying corrected bytes under the consumed local attempt cannot launder it.
    raw = scale_frame(0)
    assert not runner.McuWeightRun_FinishOwnedAttempt(memory, 1, attempt, 120, 120, raw, len(raw))
    for index in range(5):
        sample(runner, memory, 350 + index * 250, 0)
    result, _ = copy(runner, memory)
    assert (result.status, result.available, result.grams, result.count) == (1, 1, 0, 5)


@pytest.mark.parametrize("readings", [0, 1, 4])
def test_missing_or_insufficient_reads_never_become_a_successful_zero(runner, policy, readings):
    memory = state(runner, policy)
    for index in range(readings):
        sample(runner, memory, 100 + index * 250, -1000 if index % 2 else 1000)
    assert runner.McuWeightRun_Poll(memory, 5100)
    result, _ = copy(runner, memory)
    assert (result.status, result.available, result.count, result.elapsed) == (3, 0, readings, 5000)


def test_phase_deadline_excludes_late_frame_even_if_its_request_is_within_200ms(runner, policy):
    memory = state(runner, policy)
    attempt = runner.McuWeightRun_StartOwnedAttempt(memory, 5000)
    raw = scale_frame(500)
    assert runner.McuWeightRun_FinishOwnedAttempt(memory, 1, attempt, 5101, 5101, raw, len(raw))
    assert runner.McuWeightRun_Poll(memory, 5101)
    result, _ = copy(runner, memory)
    assert (result.status, result.available, result.count) == (3, 0, 0)


@pytest.mark.parametrize("median", [False, True])
@pytest.mark.parametrize("name,phase,clean,step", [
    ("WORK_PREOPEN_WEIGHT_READY", 17, False, 1),
    ("WORK_POSTCLOSE_WEIGHT_READY", 24, False, 3),
    ("WORK_PREUNLOCK_WEIGHT_READY", 33, True, 0),
    ("CLEAN_FINAL_WEIGHT_READY", 37, True, 7),
])
def test_configured_raw_samples_reach_exact_c_slot_and_sqlite_receipt(
        runner, policy, builder, producer, slot_lib, tmp_path, median, name, phase, clean, step):
    memory = state(runner, policy, sequence=12)
    for index in range(20 if median else 5):
        grams = (-1000 if index % 2 else 1000) if median else 500
        sample(runner, memory, 100 + index * 250, grams, measurement=12)
    assert runner.McuWeightRun_Poll(memory, 5100)
    result, used = copy(runner, memory)
    measurement = Measurement()
    assert builder.McuResultMeasurement_FromAvailable(c.byref(measurement), c.byref(result),
        (12).to_bytes(16, "big"), 42, 17, used.calibration_version)
    changes = {"workType": "CLEAN_OPERATION"} if clean else {}
    retained = work(builder, **changes)
    assert builder.McuWorkState_SetPhase(retained, phase)
    before = bytes(retained)
    meta, scratch = Meta(used.config_version, 5100, step), c.create_string_buffer(242)
    length = producer(retained, c.byref(measurement), c.byref(meta), uart.MESSAGE_TYPE[name], scratch, 242)
    assert length
    raw = scratch.raw[:length]
    decoded = uart.decode_payload(name, raw)
    assert decoded["measurementKind"] == ("TIMEOUT_MEDIAN" if median else "STABLE_MEAN")
    assert decoded["configVersion"] == used.config_version
    assert decoded["measurementElapsedMs"] == (5000 if median else 1020)
    assert decoded["reportedWeightGrams"] == (0 if median else 500)
    assert decoded["mcuEventSequence"] == 17  # not the local measurement or attempt counter
    original = uart.decode_payload("QUERY_WORK", query_payload(**changes))
    original.update(eventMessageType=name, stepSequence=step, configVersion=used.config_version)
    scope = uart.encode_payload("QUERY_PROCESS_EVENT", original)[8:]
    slot = (c.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert slot_lib.McuProcessEventSlot_Freeze(slot, scope, len(scope), uart.MESSAGE_TYPE[name], raw, length)
    assert runner.McuWeightRun_Retire(memory, 12)  # exact original is now retained in the C slot
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(scope, name, raw)
        assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
        assert store.get_native_process_receipt(scope)["payload"] == raw
        assert store.list_native_result_report_tasks() == [] and store.get_work_slot() is None
        assert bytes(retained) == before  # no automatic business completion/financial side effects
    finally:
        store.close()
