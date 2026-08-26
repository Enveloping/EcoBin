from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import config
import edge_boot
import main
from fixed_frame_mcu_adapter import FixedFrameMcuAdapter


HARDWARE_DIR = Path(__file__).resolve().parents[1]


def _set_production_hardware_boundary(monkeypatch):
    monkeypatch.setattr(config, "CONFIG_MODE", "production")
    monkeypatch.setattr(
        config,
        "DEVICE_CAPABILITIES_PATH",
        "/var/lib/ecobin/device-capabilities.json",
    )
    monkeypatch.setattr(
        config,
        "DEVICE_CAPABILITIES",
        {
            "schemaVersion": 1,
            "mcuRemoteUpdateCapable": True,
            "factoryReportSha256": "a" * 64,
        },
    )
    monkeypatch.setattr(config, "DEVICE_CAPABILITIES_ERROR", None)
    monkeypatch.setattr(config, "MCU_PROTOCOL_MODE", "fixed-frame")
    monkeypatch.setattr(config, "MCU_SIMULATED", False)
    monkeypatch.setattr(config, "SERIAL_PORT", "/dev/ttyS5")
    monkeypatch.setattr(config, "SERIAL_BAUDRATE", 115200)
    monkeypatch.setattr(config, "UART_PORT_COUNT", 1)
    monkeypatch.setattr(config, "MCU_UPDATE_ENABLED", True)
    monkeypatch.setattr(config, "MCU_BOOT0_WPI", 2)
    monkeypatch.setattr(config, "MCU_RESET_WPI", 5)
    monkeypatch.setattr(config, "MCU_BOOT0_ACTIVE_LEVEL", 1)
    monkeypatch.setattr(config, "MCU_RESET_ACTIVE_LEVEL", 1)
    monkeypatch.setattr(config, "UART_HIL_REQUIRED_CAPABILITIES", None)
    monkeypatch.setattr(config, "COS_BUCKET_NAME", "demo-1250000000")
    monkeypatch.setattr(config, "COS_REGION", "ap-shanghai")
    monkeypatch.setattr(
        config,
        "COS_BASE_URL",
        "https://demo-1250000000.cos.ap-shanghai.myqcloud.com",
    )
    monkeypatch.setattr(
        config,
        "TRUSTED_COS_ENVIRONMENT",
        {
            "bucket": "demo-1250000000",
            "region": "ap-shanghai",
            "baseUrl": (
                "https://demo-1250000000.cos.ap-shanghai.myqcloud.com"
            ),
        },
    )


def test_runtime_configuration_has_no_global_test_mode_switch():
    config_source = (HARDWARE_DIR / "config.py").read_text(
        encoding="utf-8"
    )
    env_example = (HARDWARE_DIR / ".env.example").read_text(
        encoding="utf-8"
    )
    main_source = (HARDWARE_DIR / "main.py").read_text(
        encoding="utf-8"
    )

    assert "ECOBIN_TEST_MODE" not in config_source
    assert "ECOBIN_TEST_MODE" not in env_example
    assert not hasattr(config, "TEST_MODE")
    assert "from test_mode import" not in main_source
    assert "simulate_camera=" not in main_source


def test_fixed_frame_link_selection_always_uses_configured_serial_boundary(
    monkeypatch,
):
    monkeypatch.setattr(main, "MCU_PROTOCOL_MODE", "fixed-frame")

    link = main._make_uart_link(
        "/tmp/ecobin-fixed-frame-mcu",
        boot_id=77,
        baudrate=115200,
        port_count=1,
        hil_required_capabilities=None,
    )

    assert isinstance(link, FixedFrameMcuAdapter)
    assert link.port == "/tmp/ecobin-fixed-frame-mcu"
    assert link.is_simulated is False


def test_fixed_frame_simulator_is_explicitly_marked_for_acceptance(
    monkeypatch,
):
    monkeypatch.setattr(main, "MCU_PROTOCOL_MODE", "fixed-frame")

    link = main._make_uart_link(
        "/tmp/ecobin-fixed-frame-mcu",
        boot_id=77,
        baudrate=115200,
        port_count=1,
        hil_required_capabilities=None,
        mcu_simulated=True,
    )

    assert isinstance(link, FixedFrameMcuAdapter)
    assert link.is_simulated is True


def test_obsolete_environment_value_cannot_relax_camera_path_validation(
    monkeypatch,
):
    monkeypatch.setenv("ECOBIN_TEST_MODE", "true")
    monkeypatch.setattr(config, "CAMERA_OUTSIDE_SOURCE", "/dev/video10")
    monkeypatch.setattr(config, "CAMERA_INSIDE_SOURCE", "/dev/video11")

    with pytest.raises(ValueError, match="stable /dev/v4l/by-id"):
        config.validate()


def test_device_entry_url_refresh_interval_must_be_positive(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_ENTRY_URL_REFRESH_SECONDS", 0)

    with pytest.raises(ValueError, match="entry URL refresh interval"):
        config.validate()


def test_explicit_simulated_camera_sources_pass_configuration_validation(
    monkeypatch,
):
    monkeypatch.setattr(config, "CONFIG_MODE", "development")
    monkeypatch.setattr(
        config,
        "CAMERA_OUTSIDE_SOURCE",
        "simulated://outside",
    )
    monkeypatch.setattr(
        config,
        "CAMERA_INSIDE_SOURCE",
        "simulated://inside",
    )
    monkeypatch.setattr(config, "COS_BUCKET_NAME", "demo-1250000000")
    monkeypatch.setattr(config, "COS_REGION", "ap-shanghai")
    monkeypatch.setattr(
        config,
        "COS_BASE_URL",
        (
            "https://demo-1250000000.cos.ap-shanghai."
            "myqcloud.com"
        ),
    )
    monkeypatch.setattr(
        config,
        "TRUSTED_COS_ENVIRONMENT",
        {
            "bucket": "demo-1250000000",
            "region": "ap-shanghai",
            "baseUrl": (
                "https://demo-1250000000.cos.ap-shanghai."
                "myqcloud.com"
            ),
        },
    )

    config.validate()


def test_production_defaults_match_the_fixed_open_drain_mainboard():
    assert config.MCU_BOOT0_WPI == 2
    assert config.MCU_RESET_WPI == 5
    assert config.MCU_BOOT0_ACTIVE_LEVEL == 1
    assert config.MCU_RESET_ACTIVE_LEVEL == 1
    assert config.GPIO_PATH == "/usr/bin/gpio"
    assert config.STM32FLASH_PATH == "/usr/bin/stm32flash"
    assert config.MCU_HARDWARE_COMPATIBILITY == "ECOBIN_MAINBOARD_V1.1"


def test_production_accepts_device_without_remote_update_lines(monkeypatch):
    _set_production_hardware_boundary(monkeypatch)
    monkeypatch.setattr(config, "_require_path_under", lambda *args: None)
    monkeypatch.setattr(config, "MCU_UPDATE_ENABLED", False)
    monkeypatch.setattr(
        config,
        "DEVICE_CAPABILITIES",
        {
            "schemaVersion": 1,
            "mcuRemoteUpdateCapable": False,
            "factoryReportSha256": "b" * 64,
        },
    )
    monkeypatch.setattr(config, "MCU_BOOT0_WPI", None)
    monkeypatch.setattr(config, "MCU_RESET_WPI", None)
    monkeypatch.setattr(config, "GPIO_PATH", "")
    monkeypatch.setattr(config, "STM32FLASH_PATH", "")

    config.validate()


def test_production_fails_closed_without_device_capability_fact(monkeypatch):
    _set_production_hardware_boundary(monkeypatch)
    monkeypatch.setattr(config, "DEVICE_CAPABILITIES", None)
    monkeypatch.setattr(
        config,
        "DEVICE_CAPABILITIES_ERROR",
        "device capability file does not exist",
    )

    with pytest.raises(ValueError, match="capability file does not exist"):
        config.validate()


def test_production_rejects_a_runtime_reset_polarity_override(monkeypatch):
    _set_production_hardware_boundary(monkeypatch)
    monkeypatch.setattr(config, "MCU_RESET_ACTIVE_LEVEL", 0)

    with pytest.raises(ValueError, match="2N7002 reset gate"):
        config.validate()


def test_production_rejects_a_different_uart_or_gpio_mapping(monkeypatch):
    _set_production_hardware_boundary(monkeypatch)
    monkeypatch.setattr(config, "SERIAL_PORT", "/dev/ttyS4")

    with pytest.raises(ValueError, match="fixed Orange Pi UART5"):
        config.validate()


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("GPIO_PATH", "/tmp/gpio"),
        ("STM32FLASH_PATH", "/tmp/stm32flash"),
        ("MCU_HARDWARE_COMPATIBILITY", "STM32F103C8T6"),
    ],
)
def test_production_rejects_unlocked_tool_or_mainboard_identity(
    monkeypatch,
    attribute,
    value,
):
    _set_production_hardware_boundary(monkeypatch)
    monkeypatch.setattr(config, attribute, value)

    with pytest.raises(ValueError, match="locked WiringOP"):
        config.validate()


def test_production_rejects_hil_overrides_and_simulated_cameras(monkeypatch):
    _set_production_hardware_boundary(monkeypatch)
    monkeypatch.setattr(config, "UART_HIL_REQUIRED_CAPABILITIES", 0x300)

    with pytest.raises(ValueError, match="forbids a UART HIL"):
        config.validate()

    monkeypatch.setattr(config, "UART_HIL_REQUIRED_CAPABILITIES", None)
    monkeypatch.setattr(config, "CAMERA_OUTSIDE_SOURCE", "simulated://outside")

    with pytest.raises(ValueError, match="forbids simulated camera"):
        config.validate()


def test_boot_sequence_no_longer_accepts_a_test_mode_argument():
    parameters = inspect.signature(edge_boot.boot_sequence).parameters

    assert "test_mode" not in parameters
