"""Real nonblocking ultrasonic producer -> native facts; GPIO/timer boundary only."""
import ctypes as c
from pathlib import Path
import subprocess
import hashlib

import pytest
import uart2_protocol as uart
from hardware.tests.test_mcu_device_facts import ROOT, sources, capture
from hardware.tests.test_mcu_configuration_runtime import receive
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_native_command_session import CSession, CBindReply
from mcu_configuration import NativeMcuConfiguration


class Observation(c.Structure):
    _fields_ = [("captured", c.c_uint64), ("attempt", c.c_uint32), ("pulse", c.c_uint32), ("status", c.c_uint8)]


@pytest.fixture(scope="module")
def ultrasonic_library(tmp_path_factory):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    modules = ("ultrasonic_reader", "mcu_environment_ultrasonic", "mcu_device_facts", "mcu_work_state",
        "mcu_result_slot", "actuator_runtime", "door_control", "clean_lock", "runtime_clock", "scale_reader",
        "mcu_configuration", "mcu_config_collection", "mcu_session", "mcu_fullness_run", "mcu_process_measurement")
    signatures = {
        "TestFacts_InitHardware": (None, []), "TestFacts_Writes": (c.c_uint32, []),
        "TestFacts_AdvanceOnEntry": (None, [c.c_uint32, c.c_uint32]),
        "TestUltrasonic_Init": (None, []), "TestUltrasonic_Advance": (None, [c.c_uint32]),
        "TestUltrasonic_Edge": (None, [c.c_uint8]), "TestUltrasonic_Trigger": (c.c_uint8, []),
        "TestUltrasonic_ExpireOnEntry": (None, [c.c_uint32, c.c_uint32]),
        "UltrasonicReader_Begin": (c.c_uint32, [c.c_uint32]),
        "UltrasonicReader_Copy": (c.c_uint8, [c.POINTER(Observation)]),
        "UltrasonicReader_Retire": (c.c_uint8, [c.c_uint32]),
        "UltrasonicReader_Claim": (c.c_uint8, [c.c_void_p]),
        "UltrasonicReader_Release": (c.c_uint8, [c.c_void_p]),
        "UltrasonicReader_BeginOwned": (c.c_uint32, [c.c_void_p, c.c_uint32]),
        "UltrasonicReader_CopyOwned": (c.c_uint8, [c.c_void_p, c.POINTER(Observation)]),
        "UltrasonicReader_RetireOwned": (c.c_uint8, [c.c_void_p, c.c_uint32]),
        "UltrasonicReader_CancelOwned": (c.c_uint8, [c.c_void_p]),
        "McuFullnessRun_Init": (None, [c.c_void_p]),
        "McuFullnessRun_Begin": (c.c_uint32, [c.c_void_p, c.c_void_p, c.c_void_p]),
        "McuFullnessRun_Poll": (c.c_uint8, [c.c_void_p]),
        "McuFullnessRun_Interrupt": (c.c_uint8, [c.c_void_p]),
        "McuFullnessRun_Copy": (c.c_uint8, [c.c_void_p, c.c_void_p]),
        "McuFullnessRun_Retire": (c.c_uint8, [c.c_void_p, c.c_uint32]),
        "McuConfiguration_ReadFullnessPolicy": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_void_p]),
        "McuProcessMeasurement_BuildWorkEventWithFullness": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_void_p,
            c.c_void_p, c.c_uint8, c.c_void_p, c.c_size_t]),
        "McuDeviceFacts_Init": (None, [c.c_void_p, c.c_uint8]),
        "McuWorkState_Init": (None, [c.c_void_p, c.c_uint64]),
        "McuDeviceFacts_Capture": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p, c.c_size_t]),
        "McuEnvironmentMonitor_PollUltrasonic": (c.c_uint8, [c.c_void_p]),
        "McuEnvironmentMonitor_PublishUltrasonic": (c.c_uint8, [c.c_void_p, c.POINTER(Observation)]),
        "McuEnvironmentMonitor_StartUltrasonic": (c.c_uint32, [c.c_void_p, c.c_void_p]),
        "McuConfiguration_Init": (None, [c.c_void_p, c.c_uint64, c.c_uint8]),
        "McuConfiguration_Receive": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint16,
            c.c_uint8, c.c_void_p, c.c_size_t, c.c_void_p]),
        "McuSession_Init": (None, [c.c_void_p]), "McuSession_Probe": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_void_p]),
        "McuSession_Bind": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_uint64, c.c_void_p]),
        "McuDeviceFacts_PublishConfiguration": (c.c_uint8, [c.c_void_p, c.c_uint64, c.c_void_p, c.c_void_p, c.c_uint8]),
    }
    out = tmp_path_factory.mktemp("ultrasonic") / "ultrasonic.dll"
    compiled = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-I", str(user), *[str(user / (module + ".c")) for module in modules],
        str(ROOT / "hardware_mcu/tests/ultrasonic_host.c"), str(ROOT / "hardware_mcu/tests/device_facts_host.c"),
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(out)],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    lib = c.CDLL(str(out))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    return lib


@pytest.fixture
def ultrasonic(ultrasonic_library):
    lib = ultrasonic_library
    lib.TestFacts_InitHardware()
    lib.TestUltrasonic_Init()
    return lib


def test_actual_echo_pulse_is_published_in_mm_without_query_trigger_or_refresh(ultrasonic):
    lib = ultrasonic
    facts, work = sources(lib)
    writes = lib.TestFacts_Writes()
    assert lib.UltrasonicReader_Begin(40000) == 1
    assert lib.TestUltrasonic_Trigger() == 1  # Begin returns immediately during the pulse
    assert capture(lib, facts, work)["fullnessReadStatus"] == "NOT_OBSERVED"
    lib.TestUltrasonic_Advance(15)
    assert lib.TestUltrasonic_Trigger() == 0
    lib.TestUltrasonic_Advance(185)
    lib.TestUltrasonic_Edge(1)
    lib.TestUltrasonic_Advance(5800)
    lib.TestUltrasonic_Edge(0)
    lib.TestUltrasonic_Advance(100000)
    assert lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    value = capture(lib, facts, work)
    assert value["fullnessReadStatus"] == "VALID" and value["fullnessObservationKind"] == "ULTRASONIC"
    assert value["fullnessDistanceMm"] == 1000
    assert value["fullnessCapturedUptimeMs"] == 6  # echo completed then, not at foreground time 106
    assert not value["fullnessInfraredBlocked"]
    assert not lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    assert capture(lib, facts, work) == value
    assert lib.TestFacts_Writes() == writes


def configured(ultrasonic, candidate=None):
    lib = ultrasonic
    facts, work = sources(lib)
    config, session = (c.c_uint64 * 192)(), CSession()
    lib.McuSession_Init(c.byref(session))
    assert lib.McuSession_Probe(c.byref(session), 1, c.byref(c.c_uint64()))
    assert lib.McuSession_Bind(c.byref(session), 1, 42, c.byref(CBindReply()))
    lib.McuConfiguration_Init(config, 42, 2)
    candidate = candidate or NativeMcuConfiguration(**inputs())
    runtime = lib, config, work, session
    for index in range(1, candidate.part_count + 1):
        assert receive(runtime, candidate, index).execute
    return facts, runtime, candidate


def test_exclusive_run_prevents_diagnostic_consumer_from_stealing_its_observation(ultrasonic):
    lib = ultrasonic
    owner, other = c.c_uint32(), c.c_uint32()
    facts, work = sources(lib)
    assert lib.UltrasonicReader_Claim(c.byref(owner))
    assert not lib.UltrasonicReader_Claim(c.byref(other))
    assert not lib.UltrasonicReader_Begin(40000)
    assert not lib.UltrasonicReader_BeginOwned(c.byref(other), 40000)
    assert lib.UltrasonicReader_BeginOwned(c.byref(owner), 40000) == 1
    assert not lib.UltrasonicReader_Release(c.byref(owner))
    lib.TestUltrasonic_Advance(40015)
    assert not lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    assert capture(lib, facts, work)["fullnessReadStatus"] == "NOT_OBSERVED"
    assert not lib.UltrasonicReader_Retire(1)
    observation = Observation()
    assert not lib.UltrasonicReader_CopyOwned(c.byref(other), c.byref(observation))
    assert lib.UltrasonicReader_CopyOwned(c.byref(owner), c.byref(observation))
    assert observation.attempt == 1 and observation.status == 2
    assert not lib.UltrasonicReader_RetireOwned(c.byref(other), 1)
    assert lib.UltrasonicReader_RetireOwned(c.byref(owner), 1)
    assert not lib.UltrasonicReader_Release(c.byref(other))
    assert lib.UltrasonicReader_Release(c.byref(owner))
    lib.TestUltrasonic_Advance(30000)
    assert lib.UltrasonicReader_Begin(40000) == 2


def test_reservation_cancel_cannot_discard_a_completion_or_release_another_owner(ultrasonic):
    lib = ultrasonic
    owner, other = c.c_uint32(), c.c_uint32()
    assert not lib.UltrasonicReader_Claim(None)
    assert not lib.UltrasonicReader_Release(None)
    assert not lib.UltrasonicReader_CancelOwned(None)
    assert lib.UltrasonicReader_Begin(100) == 1
    assert not lib.UltrasonicReader_Claim(c.byref(owner))
    lib.TestUltrasonic_Advance(115)
    assert not lib.UltrasonicReader_Claim(c.byref(owner))
    assert lib.UltrasonicReader_Retire(1)
    assert lib.UltrasonicReader_Claim(c.byref(owner))
    assert not lib.UltrasonicReader_Claim(c.byref(owner))
    assert not lib.UltrasonicReader_CancelOwned(c.byref(other))
    lib.TestUltrasonic_Advance(70000)
    assert lib.UltrasonicReader_BeginOwned(c.byref(owner), 100) == 2
    lib.TestUltrasonic_Advance(115)
    assert not lib.UltrasonicReader_CancelOwned(c.byref(owner))
    assert not lib.UltrasonicReader_Release(c.byref(owner))
    assert lib.UltrasonicReader_RetireOwned(c.byref(owner), 2)
    assert lib.UltrasonicReader_CancelOwned(c.byref(owner))
    assert lib.UltrasonicReader_Release(c.byref(owner))


@pytest.mark.parametrize("attempt, pulse, status", [(0, 580, 1), (1, 0, 1), (1, 100001, 1),
    (1, 0xFFFFFFFF, 1), (1, 580, 2), (1, 0, 0)])
def test_raw_publication_refuses_impossible_source_observations(ultrasonic, attempt, pulse, status):
    lib = ultrasonic
    facts, work = sources(lib)
    observation = Observation(0, attempt, pulse, status)
    assert not lib.McuEnvironmentMonitor_PublishUltrasonic(facts, c.byref(observation))
    assert capture(lib, facts, work)["fullnessReadStatus"] == "NOT_OBSERVED"


def test_acquisition_uses_actual_verified_and_applied_configuration_without_faking_apply(ultrasonic):
    lib = ultrasonic
    facts, (_, config, work, _), candidate = configured(lib)
    assert not lib.McuEnvironmentMonitor_StartUltrasonic(facts, config)
    assert lib.TestUltrasonic_Trigger() == 0
    values = inputs()
    assert lib.McuDeviceFacts_PublishConfiguration(facts, values["config_version"],
        bytes.fromhex(values["content_sha256"]), bytes.fromhex(candidate.mcu_payload_sha256), 0)
    assert lib.McuEnvironmentMonitor_StartUltrasonic(facts, config) == 1
    assert not lib.McuEnvironmentMonitor_StartUltrasonic(facts, config)
    timeout = values["ports"][0]["fullnessEchoTimeoutUs"]
    lib.TestUltrasonic_Advance(15 + timeout - 1)
    assert not lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    lib.TestUltrasonic_Advance(1)
    assert lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    assert capture(lib, facts, work)["fullnessReadStatus"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", ["version", "content", "subset", "staging", "port"])
def test_acquisition_refuses_configuration_mismatch_without_a_pulse(ultrasonic, change):
    lib = ultrasonic
    facts, (_, config, _, _), candidate = configured(lib)
    values = inputs()
    if change == "port":
        lib.McuDeviceFacts_Init(facts, 2)
    assert lib.McuDeviceFacts_PublishConfiguration(facts, 9 if change == "version" else values["config_version"],
        bytes.fromhex("aa" * 32 if change == "content" else values["content_sha256"]),
        bytes.fromhex("bb" * 32 if change == "subset" else candidate.mcu_payload_sha256), int(change == "staging"))
    assert not lib.McuEnvironmentMonitor_StartUltrasonic(facts, config)
    assert lib.TestUltrasonic_Trigger() == 0


def test_in_progress_configuration_blocks_sampling_even_if_old_applied_facts_match(ultrasonic):
    from hardware.tests.test_mcu_configuration_runtime import versioned_candidate
    lib = ultrasonic
    facts, runtime, candidate = configured(lib)
    values = inputs()
    assert lib.McuDeviceFacts_PublishConfiguration(facts, values["config_version"],
        bytes.fromhex(values["content_sha256"]), bytes.fromhex(candidate.mcu_payload_sha256), 0)
    assert receive(runtime, versioned_candidate(9), 1, sequence=6,
        application_uid="99999999-9999-4999-8999-999999999999").execute
    assert not lib.McuEnvironmentMonitor_StartUltrasonic(facts, runtime[1])
    assert lib.TestUltrasonic_Trigger() == 0


@pytest.mark.parametrize("change", [{"enabled": False}, {"fullnessSensorKind": "DIGITAL_INFRARED"}])
def test_disabled_or_other_sensor_configuration_never_drives_ultrasonic_pin(ultrasonic, change):
    values = inputs()
    original = NativeMcuConfiguration(**values)
    name, raw = original.encode_part(3, application_uid="11111111-1111-4111-8111-111111111111",
        mcu_command_uid="00000000-0000-4000-8000-000000000003", target_mcu_boot_id=42, command_sequence=3)
    part = uart.decode_payload(name, raw) | change
    part["commandDigestSha256"] = uart.compute_command_digest(name, part)
    updated = uart.encode_payload(name, part)
    offset = next(field["offset"] for field in uart.MESSAGE_SPECS[name]["fields"] if field["name"] == "portNo")
    semantic_size = len(updated) - offset
    preimage = bytearray(original.digest_preimage)
    port_start = len(preimage) - len(values["ports"]) * semantic_size
    preimage[port_start:port_start + semantic_size] = updated[offset:]
    values["ports"][0].update(change)
    values["expected_sha256"] = hashlib.sha256(preimage).hexdigest()
    candidate = NativeMcuConfiguration(**values)
    lib = ultrasonic
    facts, runtime, _ = configured(lib, candidate)
    assert lib.McuDeviceFacts_PublishConfiguration(facts, values["config_version"],
        bytes.fromhex(values["content_sha256"]), bytes.fromhex(candidate.mcu_payload_sha256), 0)
    assert not lib.McuEnvironmentMonitor_StartUltrasonic(facts, runtime[1])
    assert lib.TestUltrasonic_Trigger() == 0


@pytest.mark.parametrize("timeout", [100, 40000, 100000])
@pytest.mark.parametrize("rises", [False, True])
def test_missing_echo_edges_finish_once_at_configured_deadline(ultrasonic, timeout, rises):
    lib = ultrasonic
    facts, work = sources(lib)
    assert lib.UltrasonicReader_Begin(timeout) == 1
    lib.TestUltrasonic_Advance(15)
    if rises:
        lib.TestUltrasonic_Edge(1)
    lib.TestUltrasonic_Advance(timeout - 1)
    assert not lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    lib.TestUltrasonic_Advance(1)
    assert lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    actual = capture(lib, facts, work)
    assert actual["fullnessReadStatus"] == "UNAVAILABLE" and actual["fullnessDistanceMm"] == 0
    assert actual["fullnessCapturedUptimeMs"] == (15 + timeout) // 1000
    lib.TestUltrasonic_Edge(0)
    lib.TestUltrasonic_Advance(200000)
    assert not lib.McuEnvironmentMonitor_PollUltrasonic(facts)


@pytest.mark.parametrize("timeout", [0, 99, 100001, 4294967295])
def test_invalid_timeout_never_emits_trigger(ultrasonic, timeout):
    assert not ultrasonic.UltrasonicReader_Begin(timeout)
    assert ultrasonic.TestUltrasonic_Trigger() == 0
    assert not ultrasonic.UltrasonicReader_Copy(c.byref(Observation()))


def test_unconsumed_or_wrong_port_result_cannot_be_overwritten_or_retriggered(ultrasonic):
    lib = ultrasonic
    lib.TestUltrasonic_Edge(1)  # already high: unavailable, no transmitted trigger
    assert lib.UltrasonicReader_Begin(40000) == 1
    assert lib.TestUltrasonic_Trigger() == 0
    retained = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(retained)) and retained.status == 2
    lib.TestUltrasonic_Advance(100000)
    assert not lib.UltrasonicReader_Begin(40000)
    assert not lib.UltrasonicReader_Retire(2)
    facts, _ = sources(lib)
    lib.McuDeviceFacts_Init(facts, 2)
    assert not lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    copy = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(copy)) and bytes(copy) == bytes(retained)
    lib.McuDeviceFacts_Init(facts, 1)
    assert lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    assert not lib.UltrasonicReader_Copy(c.byref(copy))
    lib.TestUltrasonic_Edge(0)
    assert lib.UltrasonicReader_Begin(40000) == 2


def test_gpio_activity_without_a_request_does_not_create_a_measurement(ultrasonic):
    lib = ultrasonic
    for value in (0, 1, 1, 0):
        lib.TestUltrasonic_Edge(value)
    lib.TestUltrasonic_Advance(100000)
    assert not lib.UltrasonicReader_Copy(c.byref(Observation()))
    assert lib.TestUltrasonic_Trigger() == 0


@pytest.mark.parametrize("edges", [(0,), (1, 1)])
def test_inconsistent_echo_edge_order_is_unavailable_not_a_fabricated_pulse(ultrasonic, edges):
    lib = ultrasonic
    assert lib.UltrasonicReader_Begin(40000)
    lib.TestUltrasonic_Advance(200)
    for edge in edges:
        lib.TestUltrasonic_Edge(edge)
    actual = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(actual))
    assert actual.status == 2 and actual.pulse == 0


def test_cooldown_and_failed_new_read_preserve_no_old_distance(ultrasonic):
    lib = ultrasonic
    facts, work = sources(lib)
    assert lib.UltrasonicReader_Begin(40000) == 1
    lib.TestUltrasonic_Advance(200)
    lib.TestUltrasonic_Edge(1)
    lib.TestUltrasonic_Advance(5800)
    lib.TestUltrasonic_Edge(0)
    assert lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    assert capture(lib, facts, work)["fullnessDistanceMm"] == 1000
    lib.TestUltrasonic_Advance(63999)
    assert not lib.UltrasonicReader_Begin(40000)
    lib.TestUltrasonic_Advance(1)
    assert lib.UltrasonicReader_Begin(40000) == 2
    lib.TestUltrasonic_Advance(40015)
    assert lib.McuEnvironmentMonitor_PollUltrasonic(facts)
    after = capture(lib, facts, work)
    assert after["fullnessReadStatus"] == "UNAVAILABLE" and after["fullnessDistanceMm"] == 0
    assert after["fullnessCapturedUptimeMs"] == 110


def test_microsecond_clock_wrap_does_not_wrap_capture_time_or_pulse_width(ultrasonic):
    lib = ultrasonic
    lib.TestUltrasonic_Advance(4294967200)
    assert lib.UltrasonicReader_Begin(40000) == 1
    lib.TestUltrasonic_Advance(200)
    lib.TestUltrasonic_Edge(1)
    lib.TestUltrasonic_Advance(5800)
    lib.TestUltrasonic_Edge(0)
    actual = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(actual))
    assert (actual.status, actual.pulse, actual.captured) == (1, 5800, 4294973)


@pytest.fixture
def adapter(tmp_path):
    compiler = Path("C:/Program Files/LLVM/bin/clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    target = tmp_path / "adapter.dll"
    signatures = {
        "UltrasonicStm32_Init": (c.c_uint8, []), "UltrasonicReader_Begin": (c.c_uint32, [c.c_uint32]),
        "UltrasonicReader_Copy": (c.c_uint8, [c.POINTER(Observation)]),
        "UltrasonicReader_Retire": (c.c_uint8, [c.c_uint32]),
        "TestAdapter_Prepare": (None, []), "TestAdapter_Advance": (None, [c.c_uint32]),
        "TestAdapter_Edge": (None, [c.c_uint8]), "TestAdapter_Trigger": (c.c_uint8, []),
        "TestAdapter_CheckInit": (c.c_uint32, []),
        "TestAdapter_DeferTimer": (None, [c.c_uint8]),
    }
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-include", str(ROOT / "hardware_mcu/tests/fake_ultrasonic_stm32.h"),
        "-I", str(user), "-I", str(ROOT / "hardware_mcu/CMSIS"), "-I", str(ROOT / "hardware_mcu/FWlib/inc"),
        *[str(user / (name + ".c")) for name in ("ultrasonic_reader", "ultrasonic_stm32", "runtime_clock")],
        str(ROOT / "hardware_mcu/tests/ultrasonic_stm32_host.c"),
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(target)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = c.CDLL(str(target))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    lib.TestAdapter_Prepare()
    assert lib.UltrasonicStm32_Init()
    assert lib.TestAdapter_CheckInit()
    assert lib.TestAdapter_Trigger() == 0
    return lib


def test_real_stm32_adapter_handles_echo_across_16bit_timer_rollover(adapter):
    lib = adapter
    lib.TestAdapter_Advance(65000)
    assert lib.UltrasonicReader_Begin(100000) == 1
    assert lib.TestAdapter_Trigger() == 1
    lib.TestAdapter_Advance(15)
    assert lib.TestAdapter_Trigger() == 0
    lib.TestAdapter_Advance(185)
    lib.TestAdapter_Edge(1)
    lib.TestAdapter_Advance(5800)
    lib.TestAdapter_Edge(0)
    actual = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(actual))
    assert (actual.attempt, actual.status, actual.pulse, actual.captured) == (1, 1, 5800, 71)
    assert not lib.UltrasonicStm32_Init()  # reconnect cannot reset driver/results
    lib.TestAdapter_Advance(200000)
    later = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(later)) and bytes(later) == bytes(actual)


@pytest.mark.parametrize("start", [0, 65520, 65535])
def test_real_stm32_adapter_ignores_early_low16_match_for_100ms_deadline(adapter, start):
    lib = adapter
    lib.TestAdapter_Advance(start)
    assert lib.UltrasonicReader_Begin(100000)
    lib.TestAdapter_Advance(15 + 99999)
    actual = Observation()
    assert not lib.UltrasonicReader_Copy(c.byref(actual))
    lib.TestAdapter_Advance(1)
    assert lib.UltrasonicReader_Copy(c.byref(actual))
    assert (actual.status, actual.pulse, actual.captured) == (2, 0, (start + 100015) // 1000)


def test_echo_irq_counts_pending_timer_wrap_without_consuming_it_twice(adapter):
    lib = adapter
    lib.TestAdapter_Advance(65500)
    assert lib.UltrasonicReader_Begin(40000)
    lib.TestAdapter_Advance(20)
    lib.TestAdapter_Edge(1)
    lib.TestAdapter_DeferTimer(1)
    lib.TestAdapter_Advance(5800)
    lib.TestAdapter_Edge(0)
    actual = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(actual))
    assert (actual.status, actual.pulse, actual.captured) == (1, 5800, 71)
    lib.TestAdapter_DeferTimer(0)
    assert lib.UltrasonicReader_Retire(actual.attempt)
    lib.TestAdapter_Advance(64180)
    assert lib.UltrasonicReader_Begin(40000) == 2
    lib.TestAdapter_Advance(200)
    lib.TestAdapter_Edge(1)
    lib.TestAdapter_Advance(5800)
    lib.TestAdapter_Edge(0)
    assert lib.UltrasonicReader_Copy(c.byref(actual))
    assert (actual.status, actual.pulse, actual.captured) == (1, 5800, 141)


def test_late_echo_is_unavailable_even_before_delayed_deadline_irq_runs(adapter):
    lib = adapter
    assert lib.UltrasonicReader_Begin(40000)
    lib.TestAdapter_Advance(200)
    lib.TestAdapter_Edge(1)
    lib.TestAdapter_DeferTimer(1)
    lib.TestAdapter_Advance(40000)
    lib.TestAdapter_Edge(0)
    actual = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(actual))
    assert actual.status == 2 and actual.pulse == 0
    lib.TestAdapter_DeferTimer(0)
    repeated = Observation()
    assert lib.UltrasonicReader_Copy(c.byref(repeated)) and bytes(repeated) == bytes(actual)
