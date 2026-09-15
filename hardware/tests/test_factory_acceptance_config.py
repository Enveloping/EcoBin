from __future__ import annotations

from pathlib import Path
import re

import pytest

from camera_selection import (
    CURRENT_INSIDE_CAMERA,
    CURRENT_OUTSIDE_CAMERA,
    LEGACY_INSIDE_CAMERA,
)
from factory.acceptance_config import (
    AcceptanceConfiguration,
    AcceptanceConfigurationError,
    parse_environment_file,
)
from factory.acceptance_core import DEFAULT_ACTION_TIMEOUT_MS
from factory.acceptance_portal_client import EXECUTE_TIMEOUT_SECONDS
from factory.native_acceptance_mcu import (
    FACTORY_CLEAN_OPERATION_WINDOW_MS,
    FACTORY_CONFIG_VERSION,
    FACTORY_DEVICE_CONFIG,
    FACTORY_PORT_CONFIG,
)


def test_action_timeout_chain_covers_required_factory_workflows() -> None:
    weight_timeout = FACTORY_PORT_CONFIG["weightMeasurementTimeoutMs"]
    required_single_delivery_round_budget = (
        weight_timeout
        + FACTORY_DEVICE_CONFIG["deliveryAutoCloseMs"]
        + FACTORY_DEVICE_CONFIG["deliveryDoorTravelWaitMs"]
        + weight_timeout
        + FACTORY_DEVICE_CONFIG["continueDeliveryWaitMs"]
    )
    clean_budget = weight_timeout + FACTORY_CLEAN_OPERATION_WINDOW_MS

    assert (
        DEFAULT_ACTION_TIMEOUT_MS["DELIVERY"]
        >= required_single_delivery_round_budget + 10_000
    )
    assert DEFAULT_ACTION_TIMEOUT_MS["CLEAN"] >= clean_budget + 10_000
    assert (
        EXECUTE_TIMEOUT_SECONDS * 1_000
        >= max(DEFAULT_ACTION_TIMEOUT_MS.values()) + 30_000
    )

    app = (Path(__file__).parents[1] / "factory" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    browser_timeout = int(
        re.search(r"action:\s*(\d+)", app).group(1)  # type: ignore[union-attr]
    )
    assert browser_timeout >= EXECUTE_TIMEOUT_SECONDS * 1_000 + 10_000


def test_factory_delivery_uses_real_door_travel_and_preserves_operator_selection_time() -> None:
    assert FACTORY_CONFIG_VERSION == 4
    assert FACTORY_DEVICE_CONFIG["deliveryDoorTravelWaitMs"] == 3_000
    assert FACTORY_DEVICE_CONFIG["continueDeliveryWaitMs"] == 30_000

    app = (Path(__file__).parents[1] / "factory" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "请在设备屏幕选择结束投递" in app


def test_hotspot_explains_native_recovery_errors_without_generic_fallback() -> None:
    app = (Path(__file__).parents[1] / "factory" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    assert 'FINAL_RESULT_TIMEOUT: "控制板未在时限内返回硬件动作结果' in app
    assert 'MCU_FACTORY_WORK_STILL_RUNNING: "控制板仍在执行上次硬件动作' in app
    assert 'MCU_FULLNESS_FACT_STALE: "旧版本遗留的测距过期状态' in app


def test_default_configuration_is_the_native_uart_v2_production_wiring() -> None:
    config = AcceptanceConfiguration.from_mapping({})

    assert config.serial_port == "/dev/ttyS5"
    assert config.serial_baudrate == 115200
    assert config.protocol_mode == "uart-v2"
    assert config.native_uart_state_path == (
        "/var/lib/ecobin/factory-test/state.uart-v2.json"
    )
    assert config.native_uart_state_path != config.state_path
    assert (config.boot0_wpi, config.reset_wpi) == (2, 5)
    assert (config.boot0_active_level, config.reset_active_level) == (1, 1)
    assert config.outside_camera.startswith("/dev/v4l/by-id/")
    assert config.inside_camera.startswith("/dev/v4l/by-id/")
    assert config.outside_camera == CURRENT_OUTSIDE_CAMERA
    assert config.inside_camera == CURRENT_INSIDE_CAMERA
    assert not hasattr(config, "camera_warmup_frames")
    assert (config.weight_target_grams, config.weight_tolerance_grams) == (500, 10)
    assert (
        config.weight_stable_sample_count,
        config.weight_stable_max_spread_grams,
        config.weight_sample_interval_ms,
        config.weight_sample_timeout_ms,
    ) == (3, 2, 100, 3000)
    assert re.fullmatch(r"[0-9a-f]{64}", config.digest())


def test_configuration_uses_legacy_camera_only_when_current_model_is_missing(
    monkeypatch,
) -> None:
    present = {CURRENT_OUTSIDE_CAMERA, LEGACY_INSIDE_CAMERA}
    monkeypatch.setattr(
        "camera_selection.os.path.exists",
        lambda source: source in present,
    )

    config = AcceptanceConfiguration.from_mapping({})

    assert config.outside_camera == CURRENT_OUTSIDE_CAMERA
    assert config.inside_camera == LEGACY_INSIDE_CAMERA


def test_legacy_camera_warmup_frames_is_ignored() -> None:
    config = AcceptanceConfiguration.from_mapping(
        {"ECOBIN_CAMERA_WARMUP_FRAMES": "not-used"}
    )

    assert not hasattr(config, "camera_warmup_frames")


def test_digest_is_canonical_and_binds_hardware_but_not_storage_paths() -> None:
    first = AcceptanceConfiguration.from_mapping(
        {"ECOBIN_CAMERA_OUTSIDE": "/dev/v4l/by-id/usb-camera-a"}
    )
    same = AcceptanceConfiguration.from_mapping(
        {
            "ECOBIN_FACTORY_TEST_STATE_PATH": (
                "/var/lib/ecobin/factory-test/other-state.json"
            ),
            "ECOBIN_FACTORY_TEST_REPORT_PATH": (
                "/var/lib/ecobin/factory-test/other-report.json"
            ),
            "ECOBIN_FACTORY_TEST_PHOTO_DIR": (
                "/run/ecobin/factory-test/other-photos"
            ),
            "ECOBIN_CAMERA_OUTSIDE": "/dev/v4l/by-id/usb-camera-a",
        }
    )
    changed = AcceptanceConfiguration.from_mapping(
        {"ECOBIN_CAMERA_OUTSIDE": "/dev/v4l/by-id/usb-camera-b"}
    )

    assert first.digest() == same.digest()
    assert first.digest() != changed.digest()


@pytest.mark.parametrize(
    "override",
    (
        {"ECOBIN_MCU_SIMULATED": "true"},
        {"ECOBIN_SERIAL_PORT": "/dev/ttyUSB0"},
        {"ECOBIN_SERIAL_BAUDRATE": "9600"},
        {"ECOBIN_MCU_PROTOCOL": "fixed-frame"},
        {"ECOBIN_MCU_PROTOCOL": "legacy"},
        {"ECOBIN_MCU_BOOT0_WPI": "3"},
        {"ECOBIN_MCU_RESET_WPI": "4"},
        {"ECOBIN_MCU_BOOT0_ACTIVE_LEVEL": "0"},
        {"ECOBIN_CAMERA_OUTSIDE": "/dev/video0"},
        {"ECOBIN_CAMERA_INSIDE": "/dev/video1"},
        {"ECOBIN_FACTORY_TEST_WEIGHT_GRAMS": "499"},
        {"ECOBIN_FACTORY_TEST_WEIGHT_TOLERANCE_GRAMS": "11"},
        {"ECOBIN_FACTORY_WEIGHT_STABLE_SAMPLE_COUNT": "2"},
        {"ECOBIN_FACTORY_TEST_STATE_PATH": "/tmp/state.json"},
        {"ECOBIN_FACTORY_TEST_PHOTO_DIR": "/tmp/photos"},
    ),
)
def test_configuration_rejects_non_production_acceptance_parameters(
    override: dict[str, str],
) -> None:
    with pytest.raises(AcceptanceConfigurationError):
        AcceptanceConfiguration.from_mapping(override)


def test_environment_file_parser_has_no_shell_expansion_or_ambiguity() -> None:
    assert parse_environment_file(
        "# installed values\nECOBIN_SERIAL_PORT=/dev/ttyS5\n"
    ) == {"ECOBIN_SERIAL_PORT": "/dev/ttyS5"}

    for text in (
        "ECOBIN_SERIAL_PORT=/dev/ttyS5\nECOBIN_SERIAL_PORT=/dev/ttyS5\n",
        "BAD_NAME=value\n",
        "ECOBIN_SERIAL_PORT=$DEVICE\n",
        "ECOBIN_SERIAL_PORT='/dev/ttyS5'\n",
        "ECOBIN_SERIAL_PORT\n",
    ):
        with pytest.raises(AcceptanceConfigurationError):
            parse_environment_file(text)
