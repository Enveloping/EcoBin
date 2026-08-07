from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import config
import edge_boot
import main
from fixed_frame_mcu_adapter import FixedFrameMcuAdapter


HARDWARE_DIR = Path(__file__).resolve().parents[1]


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


def test_explicit_simulated_camera_sources_pass_configuration_validation(
    monkeypatch,
):
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


def test_boot_sequence_no_longer_accepts_a_test_mode_argument():
    parameters = inspect.signature(edge_boot.boot_sequence).parameters

    assert "test_mode" not in parameters
