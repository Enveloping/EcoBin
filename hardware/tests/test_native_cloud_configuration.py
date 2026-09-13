"""Explicit cloud profile -> lossless OneNet -> exact v2 bytes accepted by real C."""
import copy
import ctypes as c
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contracts/tools"))
from contractlib import ContractError, JsonSchemaSubsetValidator
from hardware.tests.test_mcu_config_collection import collection, configuration_parts, offer, WeightPolicy
from mcu_configuration import (NativeMcuConfiguration, NATIVE_CONFIGURATION_PROFILE,
                               NATIVE_DEVICE_CONSTANTS, NATIVE_PORT_CONSTANTS)
from onenet_wire import (canonical_payload_sha256, decode_service_command,
                         validate_configuration_payload, _validate_command_envelope)
from uart_link import compute_mcu_payload_sha256
import uart2_protocol as uart

SCHEMA = ROOT / "contracts/onenet/commands/commands.schema.json"


def example(native=True):
    name = "apply-native-configuration" if native else "apply-configuration"
    return json.loads((ROOT / f"contracts/examples/onenet/{name}.command.json").read_text("utf-8"))


def wire(native=True):
    name = "apply-native-configuration" if native else "apply-configuration"
    return json.loads((ROOT / f"contracts/examples/onenet-wire/{name}.service-wire.json").read_text("utf-8"))[
        "callServiceApiBodyTemplate"]["params"]


def validate(command):
    # These immutable July examples are not fresh remote commands. Test shape,
    # target and digest only, never authorize them for current transmission.
    _validate_command_envelope(command, trusted_environment=None,
                              trusted_business_release_download_base_url=None, expiry_reference_time=None)


@pytest.mark.parametrize("native", [False, True])
def test_configuration_examples_round_trip_and_keep_original_payload(native):
    original = example(native)
    decoded = decode_service_command("applyConfiguration", wire(native))
    assert decoded == original
    JsonSchemaSubsetValidator().validate(decoded, SCHEMA)
    validate(decoded)
    assert set(decoded["payload"]["config"]) == {"version", "contentSha256", "mcuPayloadSha256"}
    assert all(len(port) == 19 for port in decoded["payload"]["ports"])
    if not native:
        assert "mcuConfigurationProfile" not in decoded["payload"]
        assert decoded["payloadSha256"] == "dd3f8c52cb3b1188b56991123288ceb54c87ba4fa2e977e454fb7cc13f7321d1"
        assert decoded["payload"]["deviceConfig"]["weightMeasurementTimeoutMs"] == 6000
        assert decoded["payload"]["ports"][0]["weightRequiredSampleCount"] == 10
        assert compute_mcu_payload_sha256(decoded["payload"]) == "c5430dca4c2ff314ea4da686e09d5a4838413a4e29165d65b6a5748d317a77de"
        with pytest.raises(ValueError, match="explicit UART_V2_SIMPLIFIED"):
            NativeMcuConfiguration.from_cloud_payload(decoded["payload"])


def test_cloud_profile_produces_original_digest_vector_and_real_c_configuration(collection):
    candidate = NativeMcuConfiguration.from_cloud_payload(example()["payload"])
    _, vector = configuration_parts()
    assert candidate.digest_preimage == bytes.fromhex(vector["preimageHex"])
    assert candidate.mcu_payload_sha256 == vector["sha256"]
    for index in range(1, candidate.part_count + 1):
        part = candidate.encode_part(index, application_uid=example()["payload"]["applicationUid"],
            mcu_command_uid=f"00000000-0000-4000-8000-{index:012d}", target_mcu_boot_id=42,
            command_sequence=index)
        assert offer(collection, part) == (2 if index == candidate.part_count else 1)
    lib, state = collection
    policy = WeightPolicy()
    assert lib.McuConfigCollection_ReadWeightPolicy(state, 2, c.byref(policy))
    assert policy.poll_interval_ms == 250 and policy.response_timeout_ms == 200
    assert policy.measurement.timeout == 5000


def test_actual_java_canonicalizer_fixture_round_trips_through_onenet_pi_and_real_c(collection):
    from generate_contracts import _encode_function_parameters

    # Produced by the real backend canonicalizer and independently frozen by
    # DeviceConfigurationCanonicalizerTest, not a hand-authored Pi payload.
    fixture = json.loads((ROOT / "ecobin-module-device/src/test/resources/native-configuration-java.json").read_text("utf-8"))
    command = example()
    command["payload"] = fixture["payload"]
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    content_sha = hashlib.sha256(fixture["canonicalContent"].encode("utf-8")).hexdigest()
    assert content_sha == command["payload"]["config"]["contentSha256"]
    assert content_sha == canonical_payload_sha256(json.loads(fixture["canonicalContent"]))
    JsonSchemaSubsetValidator().validate(command, SCHEMA)
    model = json.loads((ROOT / "contracts/onenet/generated/onenet-thing-model.candidate.json").read_text("utf-8"))
    mapping = json.loads((ROOT / "contracts/onenet/generated/onenet-wire-mapping.json").read_text("utf-8"))
    source = json.loads((ROOT / "contracts/onenet/thing-model.mapping.yaml").read_text("utf-8"))
    service = next(item for item in model["services"] if item["identifier"] == "applyConfiguration")
    params = _encode_function_parameters(service["input"],
        mapping["functions"]["applyConfiguration"]["inputMappings"], command, source.get("enumDisplay", {}))
    decoded = decode_service_command("applyConfiguration", params)
    assert decoded == command
    validate(decoded)
    candidate = NativeMcuConfiguration.from_cloud_payload(decoded["payload"])
    assert candidate.mcu_payload_sha256 == "417bd3f8e4e29f4c867dcca4fe951658f6a12b5e23e8b422ebb1d96036d1de5c"
    for index in range(1, candidate.part_count + 1):
        part = candidate.encode_part(index, application_uid=command["payload"]["applicationUid"],
            mcu_command_uid=f"00000000-0000-4000-8000-{index:012d}", target_mcu_boot_id=42,
            command_sequence=index)
        assert offer(collection, part) == (2 if index == candidate.part_count else 1)
    lib, state = collection
    copied = c.create_string_buffer(512)
    length = lib.McuConfigCollection_CopyComplete(state, copied, 512)
    assert copied.raw[:length] == candidate.digest_preimage
    assert hashlib.sha256(copied.raw[:length]).hexdigest() == command["payload"]["config"]["mcuPayloadSha256"]


def test_profile_constants_match_source_contract_and_uart_registry():
    mapping = json.loads((ROOT / "contracts/onenet/thing-model.mapping.yaml").read_text("utf-8"))
    profile = mapping["mcuConfigurationProfiles"][NATIVE_CONFIGURATION_PROFILE]
    assert dict(NATIVE_DEVICE_CONSTANTS) == profile["deviceConstants"]
    assert dict(NATIVE_PORT_CONSTANTS) == profile["portConstants"]
    for name, constants in (("CONFIG_DEVICE_BLOCK", NATIVE_DEVICE_CONSTANTS),
                            ("CONFIG_PORT_BLOCK", NATIVE_PORT_CONSTANTS)):
        fields = {field["name"]: field for field in uart.MESSAGE_SPECS[name]["fields"]}
        assert {key: fields[key]["const"] for key in constants} == dict(constants)
    assert bytes.fromhex(uart.REGISTRY["digestProfiles"]["mcuPayloadSha256"]["domainHex"]) == b"ECOBIN:UART:MCU-CONFIG:v2\0"
    with pytest.raises(TypeError):
        NATIVE_DEVICE_CONSTANTS["weightPollIntervalMs"] = 999


def test_edge_fields_are_checked_but_never_projected_or_mutated():
    payload = example()["payload"]
    original = copy.deepcopy(payload)
    first = NativeMcuConfiguration.from_cloud_payload(payload)
    assert payload == original
    payload["deviceConfig"]["edgeHeartbeatIntervalMs"] = 5000
    payload["deviceConfig"].pop("edgeHeartbeatMissThreshold")
    payload["ports"][0]["displayName"] = "新投口名称"
    assert NativeMcuConfiguration.from_cloud_payload(payload).digest_preimage == first.digest_preimage
    payload["ports"][0]["unitPriceTenThousandths"] += 1
    with pytest.raises(ValueError, match="mcuPayloadSha256 mismatch"):
        NativeMcuConfiguration.from_cloud_payload(payload)


BAD_SHAPES = [
    ("profile_null", lambda p: p.update(mcuConfigurationProfile=None)),
    ("profile_legacy_label", lambda p: p.update(mcuConfigurationProfile="UART_V1")),
    ("profile_unknown", lambda p: p.update(mcuConfigurationProfile="UART_V2_FUTURE")),
    ("extra_payload", lambda p: p.update(extra=True)),
    ("extra_identity", lambda p: p["config"].update(mcuConfigurationProfile=NATIVE_CONFIGURATION_PROFILE)),
    ("zero_version", lambda p: p["config"].update(version=0)),
    ("bool_version", lambda p: p["config"].update(version=True)),
    ("uppercase_hash", lambda p: p["config"].update(contentSha256="AB" * 32)),
    ("missing_smoke", lambda p: p["deviceConfig"].pop("smokeMonitoringEnabled")),
    ("bool_heartbeat", lambda p: p["deviceConfig"].update(edgeHeartbeatIntervalMs=True)),
    ("heartbeat_outside_range", lambda p: p["deviceConfig"].update(edgeHeartbeatMissThreshold=0)),
    ("bad_bool", lambda p: p["deviceConfig"].update(smokeMonitoringEnabled=1)),
    ("short_door_wait", lambda p: p["deviceConfig"].update(deliveryDoorTravelWaitMs=1)),
    ("legacy_device_timeout", lambda p: p["deviceConfig"].update(weightMeasurementTimeoutMs=6000)),
    ("explicit_poll_override", lambda p: p["deviceConfig"].update(weightPollIntervalMs=250)),
    ("explicit_response_override", lambda p: p["deviceConfig"].update(weightResponseTimeoutMs=200)),
    ("explicit_age_override", lambda p: p["ports"][0].update(weightMaximumSampleAgeMs=750)),
    ("explicit_median_override", lambda p: p["ports"][0].update(weightMinimumMedianSampleCount=5)),
    ("empty_ports", lambda p: p.update(ports=[])),
    ("missing_port_field", lambda p: p["ports"][0].pop("calibrationVersion")),
    ("missing_display", lambda p: p["ports"][0].pop("displayName")),
    ("empty_display", lambda p: p["ports"][0].update(displayName="")),
    ("bad_enabled", lambda p: p["ports"][0].update(enabled=1)),
    ("bad_fullness_mode", lambda p: p["ports"][0].update(fullnessMode="UNKNOWN")),
    ("bad_sensor_kind", lambda p: p["ports"][0].update(fullnessSensorKind="UNKNOWN")),
    ("legacy_window", lambda p: p["ports"][0].update(weightStableWindowMs=6000)),
    ("legacy_fluctuation", lambda p: p["ports"][0].update(weightMaximumFluctuationGrams=20)),
    ("legacy_samples", lambda p: p["ports"][0].update(weightRequiredSampleCount=10)),
    ("legacy_port_timeout", lambda p: p["ports"][0].update(weightMeasurementTimeoutMs=6000)),
]


@pytest.mark.parametrize("_label,mutate", BAD_SHAPES, ids=[row[0] for row in BAD_SHAPES])
def test_native_malformed_shapes_rejected_by_schema_and_pi(_label, mutate):
    command = example()
    mutate(command["payload"])
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    with pytest.raises(ContractError):
        JsonSchemaSubsetValidator().validate(command, SCHEMA)
    with pytest.raises(ValueError):
        validate(command)
    with pytest.raises(ValueError):
        NativeMcuConfiguration.from_cloud_payload(command["payload"])


@pytest.mark.parametrize("native", [False, True])
def test_business_configuration_cross_field_guards_remain_strict(native):
    for mutate in (lambda p: p["ports"].reverse(),
                   lambda p: p["ports"][0].update(weightMinimumGrams=100000),
                   lambda p: p["ports"][0].update(fullnessMinimumValidSampleCount=6)):
        payload = example(native)["payload"]
        mutate(payload)
        with pytest.raises(ValueError):
            validate_configuration_payload(payload)


def test_no_profile_does_not_upgrade_or_infer_native_digest():
    command = example()
    command["payload"].pop("mcuConfigurationProfile")
    assert validate_configuration_payload(command["payload"]) is None
    with pytest.raises(ValueError, match="explicit UART_V2_SIMPLIFIED"):
        NativeMcuConfiguration.from_cloud_payload(command["payload"])
    command = example()
    command["payload"]["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(command["payload"])
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    with pytest.raises(ValueError, match="v1 fallback is forbidden"):
        validate(command)


def test_payload_hash_and_application_target_remain_envelope_requirements():
    command = example()
    command["payload"]["ports"][0]["displayName"] = "篡改"
    with pytest.raises(ValueError, match="payloadSha256 mismatch"):
        validate(command)
    command = example()
    command["target"]["uid"] = "10000000-0000-4000-8000-000000000099"
    with pytest.raises(ValueError, match="target"):
        validate(command)


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_profile_presence_flag_is_strict_boolean(value):
    params = wire()
    params["mcuConfigurationProfilePresent"] = value
    with pytest.raises(ValueError):
        decode_service_command("applyConfiguration", params)


def test_old_wire_without_profile_and_unknown_profile_are_not_upgraded():
    params = wire(False)
    params.pop("mcuConfigurationProfilePresent")
    params.pop("mcuConfigurationProfile")
    assert decode_service_command("applyConfiguration", params) == example(False)
    params = wire()
    params["mcuConfigurationProfile"] = 2
    command = decode_service_command("applyConfiguration", params)
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    with pytest.raises(ValueError, match="unsupported mcuConfigurationProfile"):
        validate(command)


@pytest.mark.parametrize("value", [True, None, [], {}])
def test_native_wire_profile_rejects_non_enum_values(value):
    params = wire()
    params["mcuConfigurationProfile"] = value
    with pytest.raises(ValueError, match="enum value"):
        decode_service_command("applyConfiguration", params)


def test_one_net_port_struct_remains_within_twenty_fields():
    model = json.loads((ROOT / "contracts/onenet/generated/onenet-thing-model.candidate.json").read_text("utf-8"))
    service = next(item for item in model["services"] if item["identifier"] == "applyConfiguration")
    port = next(item for item in service["input"] if item["identifier"] == "ports")
    assert len(port["dataType"]["specs"]["specs"]) == 19
    assert len(service["input"]) <= 20
