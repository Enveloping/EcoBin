"""Applied native configuration -> real IRQ acquisition -> retained group result.

Only GPIO/time are simulated. No replacement sampler, reducer or config owner.
"""
import ctypes as c
import hashlib
import pytest
import uart2_protocol as uart
from mcu_configuration import NativeMcuConfiguration

from hardware.tests.test_mcu_ultrasonic import ultrasonic_library, ultrasonic, configured, Observation
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_device_facts import capture


class Result(c.Structure):
    _fields_ = [("config_version", c.c_uint64), ("started_ms", c.c_uint64),
        ("completed_ms", c.c_uint64), ("last_captured_ms", c.c_uint64),
        ("content", c.c_uint8 * 32), ("subset", c.c_uint8 * 32),
        ("sequence", c.c_uint32), ("distance", c.c_uint32),
        ("status", c.c_uint8), ("reason", c.c_uint8), ("port", c.c_uint8),
        ("sensor_kind", c.c_uint8), ("sensor_value", c.c_uint8), ("basis", c.c_uint8),
        ("distance_present", c.c_uint8), ("requested", c.c_uint8),
        ("completed", c.c_uint8), ("valid", c.c_uint8)]


def ready(lib, candidate=None):
    facts, runtime, candidate = configured(lib, candidate)
    values = inputs()
    assert lib.McuDeviceFacts_PublishConfiguration(facts, values["config_version"],
        bytes.fromhex(values["content_sha256"]), bytes.fromhex(candidate.mcu_payload_sha256), 0)
    run = (c.c_uint64 * 32)()
    lib.McuFullnessRun_Init(run)
    return run, facts, runtime, candidate


def echo(lib, distance):
    assert lib.TestUltrasonic_Trigger()
    lib.TestUltrasonic_Advance(15)
    lib.TestUltrasonic_Edge(1)
    lib.TestUltrasonic_Advance((distance * 58 + 9) // 10)
    lib.TestUltrasonic_Edge(0)


def result(lib, run):
    out = Result()
    assert lib.McuFullnessRun_Copy(run, c.byref(out))
    return out


def with_policy(**changes):
    values = inputs()
    original = NativeMcuConfiguration(**values)
    name, raw = original.encode_part(3, application_uid="11111111-1111-4111-8111-111111111111",
        mcu_command_uid="00000000-0000-4000-8000-000000000003", target_mcu_boot_id=42, command_sequence=3)
    part = uart.decode_payload(name, raw) | changes
    part["commandDigestSha256"] = uart.compute_command_digest(name, part)
    encoded = uart.encode_payload(name, part)
    offset = next(field["offset"] for field in uart.MESSAGE_SPECS[name]["fields"] if field["name"] == "portNo")
    size = len(encoded) - offset
    preimage = bytearray(original.digest_preimage)
    start = len(preimage) - len(values["ports"]) * size
    preimage[start:start + size] = encoded[offset:]
    values["ports"][0].update(changes)
    values["expected_sha256"] = hashlib.sha256(preimage).hexdigest()
    return NativeMcuConfiguration(**values)


def complete(lib, run, distances, timeout=40000):
    assert not lib.McuFullnessRun_Poll(run)
    for index, distance in enumerate(distances):
        if distance is None:
            assert lib.TestUltrasonic_Trigger()
            lib.TestUltrasonic_Advance(15 + timeout)
        else:
            echo(lib, distance)
        assert bool(lib.McuFullnessRun_Poll(run)) == (index == len(distances) - 1)
        if index < len(distances) - 1 and not lib.TestUltrasonic_Trigger():
            lib.TestUltrasonic_Advance(70000)
            assert not lib.McuFullnessRun_Poll(run)
    return result(lib, run)


@pytest.mark.parametrize("distances, minimum, median, value", [
    ([100, 99, 200], 3, 100, 1), ([99, 98, 200], 3, 99, 2),
    ([103, 100, 200, 99], 3, 102, 1),  # 101.5 -> 102 mm
    ([99, 98, 98, 99], 3, 99, 2),
    ([100, None, None], 1, 100, 1), ([None, 98, None, 100, None], 2, 99, 2),
    ([100, None, 99, None, 200], 3, 100, 1),
    ([9, 8, 7, 6, 5, 4, 3, 2, 1], 9, 5, 2),
])
def test_exact_applied_sample_count_minimum_and_threshold_control_the_real_group(ultrasonic, distances, minimum, median, value):
    lib = ultrasonic
    candidate = with_policy(fullnessSampleCount=len(distances), fullnessMinimumValidSampleCount=minimum,
        fullnessDistanceThresholdMm=100, fullnessSettleWaitMs=0, fullnessEchoTimeoutUs=100000)
    run, facts, (_, config, _, _), _ = ready(lib, candidate)
    assert lib.McuFullnessRun_Begin(run, facts, config)
    out = complete(lib, run, distances, 100000)
    assert (out.requested, out.completed, out.valid) == (len(distances), len(distances), sum(x is not None for x in distances))
    assert (out.basis, out.distance_present, out.distance, out.sensor_value) == (1, 1, median, value)


def test_second_group_has_new_identity_and_no_cached_success_or_distance(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, _, _), _ = ready(lib, with_policy(fullnessSettleWaitMs=0))
    assert lib.McuFullnessRun_Begin(run, facts, config) == 1
    first = complete(lib, run, [100] * 5)
    assert lib.McuFullnessRun_Interrupt(run)  # completed result cannot become interrupted
    assert bytes(result(lib, run)) == bytes(first)
    assert lib.McuFullnessRun_Retire(run, 1)
    assert not lib.McuFullnessRun_Retire(run, 1)
    assert lib.McuFullnessRun_Begin(run, facts, config) == 2
    lib.TestUltrasonic_Advance(70000)
    second = complete(lib, run, [None] * 5, inputs()["ports"][0]["fullnessEchoTimeoutUs"])
    assert second.sequence == 2 and second.started_ms >= first.completed_ms
    assert (second.valid, second.basis, second.distance_present, second.distance) == (0, 2, 0, 0)


def test_new_group_waits_new_capture_tick_after_previous_groups_long_timeout(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, _, _), _ = ready(lib, with_policy(fullnessSettleWaitMs=0, fullnessEchoTimeoutUs=100000))
    assert lib.McuFullnessRun_Begin(run, facts, config) == 1
    complete(lib, run, [None] * 5, 100000)
    assert not lib.McuEnvironmentMonitor_StartUltrasonic(facts, config)
    assert lib.McuFullnessRun_Retire(run, 1)
    assert lib.McuFullnessRun_Begin(run, facts, config) == 2
    assert not lib.McuFullnessRun_Poll(run)
    assert not lib.TestUltrasonic_Trigger()
    lib.TestUltrasonic_Advance(10000)
    assert not lib.McuFullnessRun_Poll(run)
    echo(lib, 20)
    assert lib.McuFullnessRun_Interrupt(run)
    assert (result(lib, run).completed, result(lib, run).valid) == (1, 1)


def test_delayed_foreground_never_invents_missing_attempts_or_refreshes_capture_time(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, work, _), _ = ready(lib, with_policy(fullnessSettleWaitMs=0))
    assert lib.McuFullnessRun_Begin(run, facts, config)
    lib.McuFullnessRun_Poll(run)
    echo(lib, 500)
    lib.TestUltrasonic_Advance(10000000)
    assert not lib.McuFullnessRun_Poll(run)
    assert not lib.McuFullnessRun_Copy(run, c.byref(Result()))
    assert lib.TestUltrasonic_Trigger()  # exactly one next real request, not a catch-up batch
    assert lib.McuFullnessRun_Interrupt(run)
    out = result(lib, run)
    assert (out.completed, out.valid) == (1, 1)
    assert out.last_captured_ms == 2 and out.completed_ms >= 10002
    assert capture(lib, facts, work)["fullnessCapturedUptimeMs"] == 2


@pytest.mark.parametrize("change", ["version", "content", "subset", "staging", "port", "disabled", "digital"])
def test_group_refuses_unapplied_or_unsupported_settings_without_claiming_sensor(ultrasonic, change):
    lib = ultrasonic
    candidate = with_policy(**({"enabled": False} if change == "disabled" else
        {"fullnessSensorKind": "DIGITAL_INFRARED"} if change == "digital" else {}))
    run, facts, (_, config, _, _), _ = ready(lib, candidate)
    values = inputs()
    lib.McuDeviceFacts_Init(facts, 2 if change == "port" else 1)
    assert lib.McuDeviceFacts_PublishConfiguration(facts, 9 if change == "version" else values["config_version"],
        bytes.fromhex("aa" * 32 if change == "content" else values["content_sha256"]),
        bytes.fromhex("bb" * 32 if change == "subset" else candidate.mcu_payload_sha256), int(change == "staging"))
    assert not lib.McuFullnessRun_Begin(run, facts, config)
    assert not lib.TestUltrasonic_Trigger()
    assert lib.UltrasonicReader_Begin(100) == 1


def test_two_group_owners_cannot_mix_and_retirement_does_not_steal_the_next_sensor_owner(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, _, _), _ = ready(lib, with_policy(fullnessSettleWaitMs=0))
    other = (c.c_uint64 * 32)()
    lib.McuFullnessRun_Init(other)
    assert lib.McuFullnessRun_Begin(run, facts, config)
    assert not lib.McuFullnessRun_Begin(other, facts, config)
    complete(lib, run, [100] * 5)
    assert lib.McuFullnessRun_Begin(other, facts, config)
    assert lib.McuFullnessRun_Retire(run, 1)
    assert not lib.UltrasonicReader_Begin(100)
    lib.TestUltrasonic_Advance(70000)
    assert complete(lib, other, [100] * 5).valid == 5


def test_real_group_waits_original_settle_then_retains_measured_median(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, work, _), candidate = ready(lib)
    policy = inputs()["ports"][0]
    assert policy["fullnessSampleCount"] == 5
    assert lib.McuFullnessRun_Begin(run, facts, config) == 1
    assert not lib.TestUltrasonic_Trigger()
    wait = policy["fullnessSettleWaitMs"]
    lib.TestUltrasonic_Advance(wait * 1000 - 1)
    assert not lib.McuFullnessRun_Poll(run)
    assert not lib.TestUltrasonic_Trigger()
    lib.TestUltrasonic_Advance(1)
    assert not lib.McuFullnessRun_Poll(run)
    for index, distance in enumerate([200, 1000, 300, 400, 250]):
        echo(lib, distance)
        if index == 4:
            lib.TestFacts_AdvanceOnEntry(2, 10)  # timer preempts raw-facts publication
        assert bool(lib.McuFullnessRun_Poll(run)) == (index == 4)
        assert capture(lib, facts, work)["fullnessDistanceMm"] == distance
        if index < 4:
            assert not lib.McuFullnessRun_Copy(run, c.byref(Result()))
            lib.TestUltrasonic_Advance(70000)
            assert not lib.McuFullnessRun_Poll(run)
    out = result(lib, run)
    assert (out.status, out.reason, out.requested, out.completed, out.valid) == (1, 0, 5, 5, 5)
    assert (out.basis, out.distance_present, out.distance) == (1, 1, 300)
    assert out.completed_ms >= out.last_captured_ms + 10
    assert out.sensor_value == (2 if 300 < policy["fullnessDistanceThresholdMm"] else 1)
    assert out.config_version == inputs()["config_version"]
    assert bytes(out.content).hex() == inputs()["content_sha256"]
    assert bytes(out.subset).hex() == candidate.mcu_payload_sha256
    original = bytes(out)
    lib.TestUltrasonic_Advance(100000)
    assert lib.McuFullnessRun_Poll(run)
    assert bytes(result(lib, run)) == original
    assert not lib.McuFullnessRun_Begin(run, facts, config)
    assert not lib.McuFullnessRun_Retire(run, 2)
    assert lib.McuFullnessRun_Retire(run, 1)
    assert not lib.McuFullnessRun_Copy(run, c.byref(Result()))
    assert lib.McuFullnessRun_Begin(run, facts, config) == 2


@pytest.mark.parametrize("distances, basis, valid", [
    ([None] * 5, 2, 0), ([200, None, None, None, None], 3, 1),
    ([200, None, 100, None, None], 3, 2),
])
def test_completed_group_fallback_retains_counts_without_fabricating_a_distance(ultrasonic, distances, basis, valid):
    lib = ultrasonic
    run, facts, (_, config, work, _), _ = ready(lib)
    policy = inputs()["ports"][0]
    assert lib.McuFullnessRun_Begin(run, facts, config)
    lib.TestUltrasonic_Advance(policy["fullnessSettleWaitMs"] * 1000)
    lib.McuFullnessRun_Poll(run)
    for index, distance in enumerate(distances):
        if distance is None:
            lib.TestUltrasonic_Advance(15 + policy["fullnessEchoTimeoutUs"])
        else:
            echo(lib, distance)
        assert bool(lib.McuFullnessRun_Poll(run)) == (index == 4)
        if index < 4:
            lib.TestUltrasonic_Advance(70000)
            lib.McuFullnessRun_Poll(run)
    out = result(lib, run)
    assert (out.status, out.requested, out.completed, out.valid) == (1, 5, 5, valid)
    assert (out.basis, out.sensor_value, out.distance_present, out.distance) == (basis, 1, 0, 0)
    assert capture(lib, facts, work)["fullnessReadStatus"] == "UNAVAILABLE"


@pytest.mark.parametrize("stage", ["settle", "trigger", "echo", "held"])
def test_changed_applied_facts_interrupt_without_a_fake_clear_or_lost_completed_observation(ultrasonic, stage):
    lib = ultrasonic
    run, facts, (_, config, work, _), candidate = ready(lib)
    values = inputs()
    assert lib.McuFullnessRun_Begin(run, facts, config)
    if stage != "settle":
        lib.TestUltrasonic_Advance(values["ports"][0]["fullnessSettleWaitMs"] * 1000)
        lib.McuFullnessRun_Poll(run)
        if stage == "echo":
            lib.TestUltrasonic_Advance(15)
            lib.TestUltrasonic_Edge(1)
        elif stage == "held":
            echo(lib, 275)
    assert lib.McuDeviceFacts_PublishConfiguration(facts, values["config_version"],
        bytes.fromhex(values["content_sha256"]), bytes.fromhex(candidate.mcu_payload_sha256), 1)
    assert lib.McuFullnessRun_Poll(run)
    out = result(lib, run)
    assert (out.status, out.reason, out.basis, out.sensor_value, out.distance_present) == (2, 2, 0, 0, 0)
    assert out.completed == out.valid == (1 if stage == "held" else 0)
    assert capture(lib, facts, work)["fullnessReadStatus"] == ("VALID" if stage == "held" else "NOT_OBSERVED")
    assert not lib.TestUltrasonic_Trigger()
    original = bytes(out)
    lib.TestUltrasonic_Advance(200000)
    lib.TestUltrasonic_Edge(0)
    lib.McuFullnessRun_Poll(run)
    assert bytes(result(lib, run)) == original
    assert lib.UltrasonicReader_Begin(40000)  # only sensor resource released, result still retained


def test_caller_interrupt_keeps_partial_count_and_original_facts_not_a_sensor_failure(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, work, _), _ = ready(lib)
    assert not lib.McuFullnessRun_Interrupt(run)
    assert lib.McuFullnessRun_Begin(run, facts, config)
    lib.TestUltrasonic_Advance(inputs()["ports"][0]["fullnessSettleWaitMs"] * 1000)
    lib.McuFullnessRun_Poll(run)
    echo(lib, 123)
    lib.McuFullnessRun_Poll(run)
    lib.TestUltrasonic_Advance(70000)
    lib.McuFullnessRun_Poll(run)
    before = capture(lib, facts, work)
    assert lib.McuFullnessRun_Interrupt(run)
    out = result(lib, run)
    assert (out.status, out.reason, out.completed, out.valid, out.basis, out.sensor_value) == (2, 1, 1, 1, 0, 0)
    assert capture(lib, facts, work) == before
    lib.TestUltrasonic_Advance(100000)
    assert lib.McuFullnessRun_Interrupt(run)
    assert bytes(result(lib, run)) == bytes(out)


@pytest.mark.parametrize("commit", [False, True])
def test_real_configuration_transaction_stops_the_original_group_even_when_facts_still_match_or_are_updated(ultrasonic, commit):
    from hardware.tests.test_mcu_configuration_runtime import receive, versioned_candidate
    lib = ultrasonic
    run, facts, runtime, original = ready(lib)
    assert lib.McuFullnessRun_Begin(run, facts, runtime[1])
    candidate = versioned_candidate(9)
    for part in range(1, candidate.part_count + 1 if commit else 2):
        assert receive(runtime, candidate, part, sequence=part + 5,
            application_uid="99999999-9999-4999-8999-999999999999").execute
    if commit:
        assert lib.McuDeviceFacts_PublishConfiguration(facts, 9, bytes.fromhex(inputs()["content_sha256"]),
            bytes.fromhex(candidate.mcu_payload_sha256), 0)
    assert lib.McuFullnessRun_Poll(run)
    out = result(lib, run)
    assert (out.status, out.reason, out.config_version) == (2, 2, 8)
    assert bytes(out.subset).hex() == original.mcu_payload_sha256
    assert not lib.TestUltrasonic_Trigger()


def test_sink_rejection_holds_the_exact_observation_and_stop_until_it_can_be_published(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, _, _), candidate = ready(lib, with_policy(fullnessSettleWaitMs=0))
    assert lib.McuFullnessRun_Begin(run, facts, config)
    lib.McuFullnessRun_Poll(run)
    echo(lib, 200)
    lib.McuDeviceFacts_Init(facts, 2)  # boundary unavailable for this board's port 1
    assert not lib.McuFullnessRun_Interrupt(run)
    assert not lib.McuFullnessRun_Copy(run, c.byref(Result()))
    assert not lib.McuFullnessRun_Retire(run, 1)
    observation = Observation()
    assert lib.UltrasonicReader_CopyOwned(run, c.byref(observation))
    assert observation.attempt == 1 and observation.status == 1
    assert not lib.UltrasonicReader_Begin(100)
    lib.McuDeviceFacts_Init(facts, 1)
    assert lib.McuDeviceFacts_PublishConfiguration(facts, 8, bytes.fromhex(inputs()["content_sha256"]),
        bytes.fromhex(candidate.mcu_payload_sha256), 0)
    lib.TestUltrasonic_Advance(70000)
    assert lib.McuFullnessRun_Poll(run)
    out = result(lib, run)
    assert (out.status, out.reason, out.completed, out.valid, out.basis) == (2, 1, 1, 1, 0)
    assert out.last_captured_ms == observation.captured
    assert lib.UltrasonicReader_Begin(100)


def test_completion_irq_between_copy_and_cancel_is_drained_without_becoming_clear_fallback(ultrasonic):
    lib = ultrasonic
    run, facts, (_, config, work, _), _ = ready(lib, with_policy(fullnessSettleWaitMs=0, fullnessEchoTimeoutUs=100000))
    assert lib.McuFullnessRun_Begin(run, facts, config)
    lib.McuFullnessRun_Poll(run)
    lib.TestUltrasonic_ExpireOnEntry(2, 100015)
    assert not lib.McuFullnessRun_Interrupt(run)
    assert not lib.McuFullnessRun_Copy(run, c.byref(Result()))
    assert lib.McuFullnessRun_Poll(run)
    out = result(lib, run)
    assert (out.status, out.reason, out.completed, out.valid, out.basis, out.sensor_value) == (2, 1, 1, 0, 0, 0)
    assert out.last_captured_ms == 100 and out.completed_ms >= 100
    assert capture(lib, facts, work)["fullnessReadStatus"] == "UNAVAILABLE"
