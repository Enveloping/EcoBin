"""Pi native configuration -> actual C complete-set verification and policy."""
import ctypes as c
from dataclasses import FrozenInstanceError
import hashlib

import pytest

import uart2_protocol as uart
from hardware.tests.test_mcu_config_collection import collection, configuration_parts, offer, WeightPolicy


def inputs():
    _, vector = configuration_parts()
    values = vector["components"]
    return {"config_version": values["configVersion"], "content_sha256": values["contentSha256"],
        "device": values["device"], "ports": values["ports"], "expected_sha256": vector["sha256"]}


def test_native_pi_configuration_is_exactly_the_set_verified_by_real_c(collection):
    from mcu_configuration import NativeMcuConfiguration

    _, vector = configuration_parts()
    values = vector["components"]
    candidate = NativeMcuConfiguration(config_version=values["configVersion"],
        content_sha256=values["contentSha256"], device=values["device"], ports=values["ports"],
        expected_sha256=vector["sha256"])
    assert candidate.digest_preimage == bytes.fromhex(vector["preimageHex"])
    assert candidate.mcu_payload_sha256 == vector["sha256"]
    assert candidate.part_count == 5
    for index in range(1, candidate.part_count + 1):
        name, payload = candidate.encode_part(index,
            application_uid="11111111-1111-4111-8111-111111111111",
            mcu_command_uid=f"00000000-0000-4000-8000-{index:012d}", target_mcu_boot_id=42,
            command_sequence=index)
        decoded = uart.decode_payload(name, payload)
        assert decoded["commandSequence"] == index and decoded["partIndex"] == index
        assert offer(collection, (name, payload)) == (2 if index == candidate.part_count else 1)
    lib, state = collection
    policy = WeightPolicy()
    assert lib.McuConfigCollection_ReadWeightPolicy(state, 2, c.byref(policy))
    assert policy.measurement.timeout == 5000 and policy.poll_interval_ms == 250


def test_input_mutation_and_duplicate_encoding_cannot_change_frozen_candidate():
    from mcu_configuration import NativeMcuConfiguration

    values = inputs()
    candidate = NativeMcuConfiguration(**values)
    identity = {"application_uid": "11111111-1111-4111-8111-111111111111",
        "mcu_command_uid": "22222222-2222-4222-8222-222222222222", "target_mcu_boot_id": 42, "command_sequence": 3}
    first = candidate.encode_part(3, **identity)
    preimage = candidate.digest_preimage
    values["device"]["negativeWeightThresholdGrams"] = 999
    values["ports"][0]["weightMinimumGrams"] = -999
    values["ports"].clear()
    assert candidate.encode_part(3, **identity) == first and candidate.digest_preimage == preimage
    with pytest.raises(FrozenInstanceError):
        candidate.mcu_payload_sha256 = "00" * 32
    name, payload = candidate.encode_part(3, **(identity | {"command_sequence": 4}))
    original = uart.decode_payload(*first)
    updated = uart.decode_payload(name, payload)
    assert updated["commandDigestSha256"] != original["commandDigestSha256"]
    assert updated["mcuPayloadSha256"] == original["mcuPayloadSha256"]


def test_missing_or_legacy_fields_never_receive_implicit_native_defaults():
    from mcu_configuration import NativeMcuConfiguration

    for field in ("weightPollIntervalMs", "weightResponseTimeoutMs"):
        values = inputs()
        del values["device"][field]
        with pytest.raises(ValueError):
            NativeMcuConfiguration(**values)
    for field in ("weightMaximumSampleAgeMs", "weightMinimumMedianSampleCount"):
        values = inputs()
        del values["ports"][0][field]
        with pytest.raises(ValueError):
            NativeMcuConfiguration(**values)
    values = inputs()
    values["device"]["edgeHeartbeatIntervalMs"] = 5000  # Not the explicit MCU subset.
    with pytest.raises(ValueError):
        NativeMcuConfiguration(**values)


@pytest.mark.parametrize("change", [
    {"expected_sha256": "00" * 32}, {"expected_sha256": "cc" * 32}, {"content_sha256": "00" * 32},
    {"config_version": True}, {"config_version": 0}, {"config_version": 2**53},
])
def test_bad_configuration_metadata_or_digest_is_rejected(change):
    from mcu_configuration import NativeMcuConfiguration

    with pytest.raises((ValueError, uart.ProtocolError)):
        NativeMcuConfiguration(**(inputs() | change))


def test_old_digest_domain_and_invalid_port_sets_are_not_accepted():
    from mcu_configuration import NativeMcuConfiguration

    values = inputs()
    candidate = NativeMcuConfiguration(**values)
    old_domain_preimage = candidate.digest_preimage.replace(b"MCU-CONFIG:v2\0", b"MCU-CONFIG:v1\0", 1)
    with pytest.raises(ValueError, match="v1 fallback"):
        NativeMcuConfiguration(**(values | {"expected_sha256": hashlib.sha256(old_domain_preimage).hexdigest()}))
    for ports in ([], list(reversed(values["ports"])), [values["ports"][0]] * 2, values["ports"] * 4):
        with pytest.raises(ValueError):
            NativeMcuConfiguration(**(values | {"ports": ports}))


def test_real_part_identity_is_mandatory_valid_and_not_allocated_by_encoder():
    from mcu_configuration import NativeMcuConfiguration

    candidate = NativeMcuConfiguration(**inputs())
    identity = {"application_uid": "11111111-1111-4111-8111-111111111111",
        "mcu_command_uid": "22222222-2222-4222-8222-222222222222", "target_mcu_boot_id": 42, "command_sequence": 3}
    for change in ({"application_uid": "00000000-0000-0000-0000-000000000000"},
                   {"mcu_command_uid": "00000000-0000-0000-0000-000000000000"},
                   {"target_mcu_boot_id": 0}, {"command_sequence": 0}):
        with pytest.raises((ValueError, uart.ProtocolError)):
            candidate.encode_part(1, **(identity | change))
    for index in (0, 6, True, "1"):
        with pytest.raises(ValueError):
            candidate.encode_part(index, **identity)
    with pytest.raises(TypeError):
        candidate.encode_part(1)


def test_original_parts_reconstruct_the_same_immutable_configuration():
    from mcu_configuration import NativeMcuConfiguration

    parts, vector = configuration_parts()
    candidate = NativeMcuConfiguration.from_parts(parts)
    assert candidate.digest_preimage == bytes.fromhex(vector["preimageHex"])
    for index, (name, raw) in enumerate(parts, 1):
        original = uart.decode_payload(name, raw)
        assert candidate.encode_part(index, application_uid=original["applicationUid"],
            mcu_command_uid=original["mcuCommandUid"], target_mcu_boot_id=original["targetMcuBootId"],
            command_sequence=original["commandSequence"]) == (name, raw)


@pytest.mark.parametrize("bad_part", [(), ("CONFIG_PORT_BLOCK",), ("UNREGISTERED", b""),
    ("CONFIG_PORT_BLOCK", bytearray(200)), (None, b""), None])
def test_malformed_original_part_is_rejected_as_validation_error(bad_part):
    from mcu_configuration import NativeMcuConfiguration

    parts, _ = configuration_parts()
    parts[2] = bad_part
    with pytest.raises(ValueError):
        NativeMcuConfiguration.from_parts(parts)


@pytest.mark.parametrize("change", ["applicationUid", "targetMcuBootId", "configVersion", "partIndex", "mcuCommandUid", "commandSequence", "fullnessDistanceThresholdMm"])
def test_individually_valid_parts_cannot_be_mixed_or_relabelled_as_the_original_complete_set(change):
    from mcu_configuration import NativeMcuConfiguration

    parts, _ = configuration_parts()
    name, raw = parts[2]
    value = uart.decode_payload(name, raw)
    if change == "applicationUid":
        value[change] = "99999999-9999-4999-8999-999999999999"
    elif change in {"mcuCommandUid", "commandSequence"}:
        value[change] = uart.decode_payload(*parts[1])[change]
    else:
        value[change] += 1
        if change == "partIndex":
            value["portNo"] += 1  # Valid single block, wrong original position.
    value["commandDigestSha256"] = uart.compute_command_digest(name, value)
    parts[2] = name, uart.encode_payload(name, value)
    with pytest.raises(ValueError):
        NativeMcuConfiguration.from_parts(parts)
