"""C verifies a complete immutable configuration set, not an applied config."""
import ctypes as c
import hashlib
import itertools
import json
import subprocess
import uuid
from pathlib import Path

import pytest
import uart2_protocol as uart
from hardware.tests.test_mcu_device_facts import WeightConfig, WeightResult

ROOT = Path(__file__).resolve().parents[2]


class WeightPolicy(c.Structure):
    _fields_ = [("config_version", c.c_uint64), ("calibration_version", c.c_uint32),
        ("poll_interval_ms", c.c_uint32), ("response_timeout_ms", c.c_uint32),
        ("measurement", WeightConfig), ("port_no", c.c_uint8), ("enabled", c.c_uint8)]


def configuration_parts():
    vectors = json.loads((ROOT / "contracts/examples/uart/digest-vectors.json").read_text("utf-8"))["vectors"]
    vector = next(item for item in vectors if item["profile"] == "mcuPayloadSha256")
    values = vector["components"]
    count = len(values["ports"]) + 3
    common = {"applicationUid": "11111111-1111-4111-8111-111111111111", "configVersion": values["configVersion"],
        "contentSha256": values["contentSha256"], "mcuPayloadSha256": vector["sha256"], "partCount": count}
    bodies = [("CONFIG_BEGIN", {"partIndex": 1, "expectedPortCount": len(values["ports"])}),
        ("CONFIG_DEVICE_BLOCK", {"partIndex": 2} | values["device"])]
    bodies += [("CONFIG_PORT_BLOCK", {"partIndex": port["portNo"] + 2} | port) for port in values["ports"]]
    bodies += [("CONFIG_COMMIT", {"partIndex": count})]
    parts = []
    for sequence, (name, fields) in enumerate(bodies, 1):
        fields |= common | {"mcuCommandUid": str(uuid.UUID(int=sequence)), "targetMcuBootId": 42,
            "commandSequence": sequence, "commandDigestSha256": "00" * 32}
        fields["commandDigestSha256"] = uart.compute_command_digest(name, fields)
        parts.append((name, uart.encode_payload(name, fields)))
    return parts, vector


@pytest.fixture
def collection(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    output, user = tmp_path / "collection.dll", ROOT / "hardware_mcu/USER"
    signatures = {
        "McuConfigCollection_Init": (None, [c.c_void_p, c.c_uint64, c.c_uint8]),
        "McuConfigCollection_Offer": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_void_p, c.c_size_t]),
        "McuConfigCollection_CopyComplete": (c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t]),
        "McuConfigCollection_ReadWeightPolicy": (c.c_uint8, [c.c_void_p, c.c_uint8, c.c_void_p]),
        "WeightMeasurement_Begin": (c.c_uint8, [c.c_void_p, c.c_void_p, c.c_uint32, c.c_uint32, c.c_uint32]),
        "WeightMeasurement_Observe": (c.c_uint8, [c.c_void_p, c.c_uint32, c.c_uint32, c.c_uint32, c.c_int32, c.c_uint32]),
        "WeightMeasurement_Poll": (WeightResult, [c.c_void_p, c.c_uint32]),
    }
    run = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-I", str(user),
        str(user / "mcu_config_collection.c"), str(user / "weight_measurement.c"),
        *["-Wl,/EXPORT:" + name for name in signatures], "-o", str(output)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    lib = c.CDLL(str(output))
    for name, (restype, argtypes) in signatures.items():
        getattr(lib, name).restype, getattr(lib, name).argtypes = restype, argtypes
    state = (c.c_uint64 * 64)()  # static budget <= 512, no allocator
    lib.McuConfigCollection_Init(state, 42, 2)
    return lib, state


def offer(collection, part):
    lib, state = collection
    name, payload = part
    return lib.McuConfigCollection_Offer(state, uart.MESSAGE_TYPE[name], payload, len(payload))


def test_complete_configuration_requires_all_parts_and_matches_existing_digest_vector(collection):
    lib, state = collection
    parts, vector = configuration_parts()
    copied = c.create_string_buffer(512)
    for part in parts[:-1]:
        assert offer(collection, part) == 1  # staged, NOT APPLIED
        assert lib.McuConfigCollection_CopyComplete(state, copied, 512) == 0
    assert offer(collection, parts[-1]) == 2  # complete candidate, still NOT APPLIED
    length = lib.McuConfigCollection_CopyComplete(state, copied, 512)
    # Public export is exact digest preimage, not padded C memory or metadata.
    assert copied.raw[:length] == bytes.fromhex(vector["preimageHex"])
    assert offer(collection, parts[-1]) == 3  # exact semantic duplicate


def test_verified_configuration_drives_real_measurement_without_separate_test_settings(collection):
    lib, state = collection
    parts, vector = configuration_parts()
    policy = WeightPolicy()
    assert not lib.McuConfigCollection_ReadWeightPolicy(state, 1, c.byref(policy))
    for part in parts:
        assert offer(collection, part) in (1, 2)
    assert lib.McuConfigCollection_ReadWeightPolicy(state, 1, c.byref(policy))
    assert policy.config_version == vector["components"]["configVersion"]
    assert policy.calibration_version == 4 and policy.port_no == 1 and policy.enabled == 1
    assert (policy.poll_interval_ms, policy.response_timeout_ms) == (250, 200)
    assert (policy.measurement.timeout, policy.measurement.window,
        policy.measurement.age, policy.measurement.span,
        policy.measurement.stable_samples, policy.measurement.median_samples) == (5000, 1500, 750, 100, 5, 5)
    assert (policy.measurement.minimum, policy.measurement.maximum) == (-5000, 100000)
    core = (c.c_uint64 * 48)()
    assert lib.WeightMeasurement_Begin(core, c.byref(policy.measurement), 1, 0, 0)
    for sample in range(1, 6):
        captured_ms = (sample - 1) * policy.poll_interval_ms
        assert lib.WeightMeasurement_Observe(core, 1, sample, captured_ms, 0, captured_ms)
    result = lib.WeightMeasurement_Poll(core, 1000)
    assert (result.status, result.available, result.grams, result.count) == (1, 1, 0, 5)


def test_projected_policy_uses_five_second_median_and_rejects_missing_or_unsupported_policy(collection):
    lib, state = collection
    parts, _ = configuration_parts()
    policy = WeightPolicy()
    c.memset(c.byref(policy), 0xA5, c.sizeof(policy))
    untouched = bytes(policy)
    for part in parts[:-1]:
        assert offer(collection, part) == 1
        assert not lib.McuConfigCollection_ReadWeightPolicy(state, 1, c.byref(policy))
        assert bytes(policy) == untouched
    assert offer(collection, parts[-1]) == 2
    for port in (0, 3, 6, 255):
        assert not lib.McuConfigCollection_ReadWeightPolicy(state, port, c.byref(policy))
        assert bytes(policy) == untouched
    assert lib.McuConfigCollection_ReadWeightPolicy(state, 2, c.byref(policy))
    assert policy.port_no == 2
    core = (c.c_uint64 * 48)()
    assert lib.WeightMeasurement_Begin(core, c.byref(policy.measurement), 1, 0, 0)
    for sample in range(1, 21):
        captured_ms = (sample - 1) * policy.poll_interval_ms
        assert lib.WeightMeasurement_Observe(core, 1, sample, captured_ms, -1000 if sample % 2 else 1000, captured_ms)
    assert lib.WeightMeasurement_Poll(core, policy.measurement.timeout - 1).status == 0
    result = lib.WeightMeasurement_Poll(core, policy.measurement.timeout)
    assert (result.status, result.available, result.grams, result.count, result.elapsed) == (2, 1, 0, 20, 5000)


def changed(part, **changes):
    name, payload = part
    values = uart.decode_payload(name, payload) | changes
    values["commandDigestSha256"] = uart.compute_command_digest(name, values)
    return name, uart.encode_payload(name, values)


@pytest.mark.parametrize("missing", [1, 2, 3])
def test_missing_segment_never_exports_candidate_and_later_segment_can_complete(collection, missing):
    lib, state = collection
    parts, _ = configuration_parts()
    for index, part in enumerate(parts[:-1]):
        if index != missing:
            assert offer(collection, part) == 1
    before = bytes(state)
    assert offer(collection, parts[-1]) == 6
    assert bytes(state) == before
    assert lib.McuConfigCollection_CopyComplete(state, c.create_string_buffer(512), 512) == 0
    assert offer(collection, parts[missing]) == 1
    assert offer(collection, parts[-1]) == 2


def test_collection_orders_port_bytes_not_arrival_order_and_exact_duplicates_do_not_overwrite(collection):
    lib, state = collection
    parts, vector = configuration_parts()
    for order in itertools.permutations(parts[1:-1]):
        lib.McuConfigCollection_Init(state, 42, 2)
        assert offer(collection, parts[0]) == 1
        for part in order:
            assert offer(collection, part) == 1
            before = bytes(state)
            duplicate = changed(part, commandSequence=99, mcuCommandUid="99999999-9999-4999-8999-999999999999")
            assert offer(collection, duplicate) == 3
            assert bytes(state) == before
        assert offer(collection, parts[-1]) == 2
        output = c.create_string_buffer(512)
        size = lib.McuConfigCollection_CopyComplete(state, output, 512)
        assert output.raw[:size] == bytes.fromhex(vector["preimageHex"])


@pytest.mark.parametrize("change", [
    {"applicationUid": "99999999-9999-4999-8999-999999999999"}, {"configVersion": 9},
    {"contentSha256": "bb" * 32}, {"mcuPayloadSha256": "cc" * 32}, {"partCount": 6},
])
def test_mixed_transaction_identity_keeps_original_candidate_unchanged(collection, change):
    lib, state = collection
    parts, _ = configuration_parts()
    assert offer(collection, parts[0]) == 1
    before = bytes(state)
    assert offer(collection, changed(parts[1], **change)) == 5
    assert bytes(state) == before
    for part in parts[1:-1]:
        assert offer(collection, part) == 1
    assert offer(collection, parts[-1]) == 2


def test_changed_existing_part_and_new_begin_do_not_replace_complete_candidate(collection):
    lib, state = collection
    parts, _ = configuration_parts()
    for part in parts[:-1]:
        assert offer(collection, part) == 1
    assert offer(collection, parts[-1]) == 2
    before = bytes(state)
    assert offer(collection, changed(parts[1], negativeWeightThresholdGrams=501)) == 5
    assert offer(collection, changed(parts[0], configVersion=9)) == 5
    assert bytes(state) == before
    assert offer(collection, parts[0]) == 3


def test_each_valid_command_does_not_substitute_for_correct_whole_set_digest(collection):
    lib, state = collection
    parts, _ = configuration_parts()
    parts[1] = changed(parts[1], negativeWeightThresholdGrams=501)
    for part in parts[:-1]:
        assert offer(collection, part) == 1
    before = bytes(state)
    assert offer(collection, parts[-1]) == 7
    assert bytes(state) == before
    assert lib.McuConfigCollection_CopyComplete(state, c.create_string_buffer(512), 512) == 0


def test_bad_payload_wrong_boot_unbound_and_unsupported_port_count_do_not_stage(collection):
    lib, state = collection
    parts, _ = configuration_parts()
    before = bytes(state)
    name, payload = parts[0]
    assert offer(collection, (name, payload[:-1])) == 0
    assert offer(collection, (name, payload[:16] + bytes([payload[16] ^ 1]) + payload[17:])) == 0
    assert offer(collection, changed(parts[0], targetMcuBootId=43)) == 4
    assert offer(collection, parts[-1]) == 6
    assert bytes(state) == before
    lib.McuConfigCollection_Init(state, 0, 2)
    assert offer(collection, parts[0]) == 4
    lib.McuConfigCollection_Init(state, 42, 1)
    assert offer(collection, parts[0]) == 8


def test_maximum_six_port_collection_has_bounded_storage_and_no_short_export(collection):
    lib, original_state = collection
    state = (c.c_uint64 * 66)()
    state[64], state[65] = 0x1234567890ABCDEF, 0xFEDCBA0987654321
    lib.McuConfigCollection_Init(state, 42, 6)
    parts, vector = configuration_parts()
    parts = [changed(parts[0], expectedPortCount=6, partCount=9), changed(parts[1], partCount=9)] + [
        changed(parts[2], portNo=port, partIndex=port + 2, partCount=9) for port in range(1, 7)
    ] + [changed(parts[-1], partIndex=9, partCount=9)]
    def body(name, field, payload):
        offset = next(item["offset"] for item in uart.MESSAGE_SPECS[name]["fields"] if item["name"] == field)
        return payload[offset:]
    components = vector["components"]
    preimage = bytes.fromhex(uart.REGISTRY["digestProfiles"]["mcuPayloadSha256"]["domainHex"])
    preimage += components["configVersion"].to_bytes(8, "big") + bytes.fromhex(components["contentSha256"]) + b"\x06"
    preimage += body("CONFIG_DEVICE_BLOCK", "continueDeliveryWaitMs", parts[1][1])
    preimage += b"".join(body("CONFIG_PORT_BLOCK", "portNo", part[1]) for part in parts[2:-1])
    digest = hashlib.sha256(preimage).hexdigest()
    parts = [changed(part, mcuPayloadSha256=digest) for part in parts]
    for part in parts[:-1]:
        assert offer((lib, state), part) == 1
    assert offer((lib, state), parts[-1]) == 2
    assert (state[64], state[65]) == (0x1234567890ABCDEF, 0xFEDCBA0987654321)
    output = c.create_string_buffer(b"x" * 512, 512)
    assert lib.McuConfigCollection_CopyComplete(state, output, len(preimage) - 1) == 0
    assert output.raw == b"x" * 512
    assert lib.McuConfigCollection_CopyComplete(state, output, 512) == len(preimage)
    assert output.raw[:len(preimage)] == preimage


def test_config_collection_target_compile(tmp_path):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required")
    output = tmp_path / "collection.o"
    run = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
        "-I", str(ROOT / "hardware_mcu/USER"), "-c", str(ROOT / "hardware_mcu/USER/mcu_config_collection.c"),
        "-o", str(output)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not run.stdout.strip() and not run.stderr.strip()
    sizes = subprocess.run([str(compiler.with_name("fromelf.exe")), "--text", "-z", str(output)],
        capture_output=True, text=True, timeout=30)
    assert sizes.returncode == 0, sizes.stdout + sizes.stderr
    print(sizes.stdout)
