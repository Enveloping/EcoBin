"""Real smoke ADC/debounce -> candidate device facts -> Python wire observer."""
import ctypes as c
from pathlib import Path
import subprocess

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from mcu_work_query import McuDeviceFactsQuery
from tests.test_mcu_device_facts import ROOT, sources, capture


@pytest.fixture
def mcu(tmp_path):
    user = ROOT / "hardware_mcu/USER"
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    signatures = {
        "TestFacts_InitHardware": (None, []),
        "TestFacts_Writes": (c.c_uint32, []),
        "TestEnvironment_Reset": (None, []),
        "TestEnvironment_Adc": (None, [c.c_uint16, c.c_uint8]),
        "TestEnvironment_Reads": (c.c_uint32, []),
        "RuntimeClock_Advance": (None, [c.c_uint32]),
        "McuDeviceFacts_Init": (None, [c.c_void_p, c.c_uint8]),
        "McuWorkState_Init": (None, [c.c_void_p, c.c_uint64]),
        "McuDeviceFacts_Capture": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p, c.c_size_t]),
        "McuEnvironmentMonitor_PollSmoke": (c.c_uint8, [c.c_void_p]),
    }
    path = tmp_path / "environment.dll"
    modules = ("mcu_environment_monitor", "smoke_monitor", "mcu_device_facts", "mcu_work_state",
               "mcu_result_slot", "actuator_runtime", "door_control", "clean_lock",
               "runtime_clock", "scale_reader", "weight_measurement")
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared",
        "-include", str(ROOT / "hardware_mcu/tests/fake_adc.h"), "-I", str(user),
        *[str(user / (name + ".c")) for name in modules],
        str(ROOT / "hardware_mcu/tests/device_facts_host.c"),
        str(ROOT / "hardware_mcu/tests/environment_facts_host.c"),
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(path)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    library = c.CDLL(str(path))
    for name, (restype, argtypes) in signatures.items():
        fn = getattr(library, name)
        fn.restype, fn.argtypes = restype, argtypes
    library.TestFacts_InitHardware()
    library.TestEnvironment_Reset()
    return library


def sample(mcu, facts, count):
    for _ in range(count):
        mcu.RuntimeClock_Advance(50)
        assert mcu.McuEnvironmentMonitor_PollSmoke(facts)


def test_real_smoke_monitor_preheat_alarm_recovery_and_adc_failure(mcu):
    facts, work = sources(mcu)
    writes = mcu.TestFacts_Writes()
    mcu.RuntimeClock_Advance(59950)
    assert not mcu.McuEnvironmentMonitor_PollSmoke(facts)
    assert mcu.TestEnvironment_Reads() == 0
    assert capture(mcu, facts, work)["smokeObservationState"] == "NOT_OBSERVED"
    mcu.TestEnvironment_Adc(2000, 1)
    sample(mcu, facts, 2)
    assert capture(mcu, facts, work)["smokeObservationState"] == "UNAVAILABLE"
    sample(mcu, facts, 1)
    assert capture(mcu, facts, work)["smokeObservationState"] == "ALARM"
    mcu.TestEnvironment_Adc(0, 1)
    sample(mcu, facts, 19)
    assert capture(mcu, facts, work)["smokeObservationState"] == "ALARM"
    sample(mcu, facts, 1)
    assert capture(mcu, facts, work)["smokeObservationState"] == "NORMAL"
    mcu.TestEnvironment_Adc(0, 0)
    sample(mcu, facts, 10)
    assert capture(mcu, facts, work)["smokeObservationState"] == "UNAVAILABLE"
    assert mcu.TestFacts_Writes() == writes


def test_queries_do_not_sample_or_refresh_old_normal_and_pi_timeout_is_unknown(mcu, tmp_path):
    facts, work = sources(mcu)
    mcu.RuntimeClock_Advance(59950)
    sample(mcu, facts, 20)
    at = capture(mcu, facts, work)["smokeObservedUptimeMs"]
    reads = mcu.TestEnvironment_Reads()
    assert not mcu.McuEnvironmentMonitor_PollSmoke(facts)
    incoming = []
    def write(frame):
        request = uart.decode_frame(frame, sender_role="EDGE")["payload"]
        size = uart.MESSAGE_SPECS["DEVICE_FACTS_REPLY"]["maximumPayloadLength"]
        output = c.create_string_buffer(size)
        assert mcu.McuDeviceFacts_Capture(facts, work, request, len(request), output, size) == size
        incoming.append(uart.encode_frame("DEVICE_FACTS_REPLY", 1, output.raw))
        return len(frame)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        query = McuDeviceFactsQuery(store, write, target_mcu_boot_id=42, port_no=1, interval_ms=1000)
        query.poll(0)
        assert query.accept_frame(incoming.pop(), 1)
        assert query.observation(1)["smokeObservationState"] == "NORMAL"
        assert query.observation(1000) is None
        mcu.RuntimeClock_Advance(5000)
        query.poll(1000)
        assert query.accept_frame(incoming.pop(), 1001)
        assert query.observation(1001)["smokeObservedUptimeMs"] == at
        assert query.observation(1001)["capturedUptimeMs"] == at + 5000
        assert mcu.TestEnvironment_Reads() == reads
        assert store.get_work_slot() is None
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
