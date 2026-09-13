"""The native candidate explicitly carries the agreed measurement policy."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import ContractError, decode_uart_frame, load_uart_registry, uart_message_specs
from generate_contracts import build_uart_digest_vectors
from contracts.tests.test_uart_v2_command_guards import codec, c_guard
from contracts.tests.test_uart_v2_configuration_guards import changed_frame


def test_native_configuration_digest_carries_every_effective_weight_parameter():
    registry = load_uart_registry()
    vector = next(item for item in build_uart_digest_vectors(registry, uart_message_specs(registry))
        if item["profile"] == "mcuPayloadSha256")
    assert registry["digestProfiles"]["mcuPayloadSha256"]["layoutVersion"] == 2
    assert bytes.fromhex(vector["preimageHex"]).startswith(b"ECOBIN:UART:MCU-CONFIG:v2\0")
    device = vector["components"]["device"]
    assert device["weightMeasurementTimeoutMs"] == 5000
    assert device["weightPollIntervalMs"] == 250
    assert device["weightResponseTimeoutMs"] == 200
    for port in vector["components"]["ports"]:
        assert port["weightMeasurementTimeoutMs"] == 5000
        assert port["weightRequiredSampleCount"] == 5
        assert port["weightMaximumFluctuationGrams"] == 100
        assert port["weightStableWindowMs"] == 1500
        assert port["weightMaximumSampleAgeMs"] == 750
        assert port["weightMinimumMedianSampleCount"] == 5


@pytest.mark.parametrize("language", ["reference", "python", "c"])
def test_old_or_conflicting_measurement_settings_are_rejected_even_with_correct_digest(codec, c_guard, language):
    registry = load_uart_registry()
    for name, field, value in (
        ("CONFIG_DEVICE_BLOCK", "weightMeasurementTimeoutMs", 6000),
        ("CONFIG_DEVICE_BLOCK", "weightMeasurementTimeoutMs", 4000),
        ("CONFIG_DEVICE_BLOCK", "weightPollIntervalMs", 600),
        ("CONFIG_DEVICE_BLOCK", "weightResponseTimeoutMs", 250),
        ("CONFIG_PORT_BLOCK", "weightMeasurementTimeoutMs", 6000),
        ("CONFIG_PORT_BLOCK", "weightRequiredSampleCount", 10),
        ("CONFIG_PORT_BLOCK", "weightMaximumFluctuationGrams", 200),
        ("CONFIG_PORT_BLOCK", "weightStableWindowMs", 5000),
        ("CONFIG_PORT_BLOCK", "weightMaximumSampleAgeMs", 5000),
        ("CONFIG_PORT_BLOCK", "weightMinimumMedianSampleCount", 1),
        ("SAMPLE_FULLNESS", "measurementTimeoutMs", 6000),
        ("MEASURE_BASELINE", "measurementTimeoutMs", 6000),
    ):
        frame = changed_frame(registry, name, {field: value})
        if language == "c":
            assert c_guard(frame, len(frame)) != 0, (name, field)
        elif language == "python":
            with pytest.raises(codec.ProtocolError):
                codec.decode_frame(frame, sender_role="EDGE")
        else:
            with pytest.raises(ContractError):
                decode_uart_frame(registry, frame, sender_role="EDGE")
