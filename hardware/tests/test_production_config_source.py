from __future__ import annotations

import os
from pathlib import Path

import pytest

import config


HARDWARE = Path(__file__).resolve().parents[1]


def test_production_refuses_a_code_directory_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ECOBIN_DEVICE_KEY=forbidden\n", encoding="utf-8")
    monkeypatch.setenv("ECOBIN_CONFIG_MODE", "production")
    monkeypatch.delenv("ECOBIN_DOTENV_PATH", raising=False)

    with pytest.raises(RuntimeError, match="refuses a code-directory .env"):
        config._configure_environment_source(tmp_path)


def test_production_does_not_load_an_explicit_external_dotenv(
    tmp_path,
    monkeypatch,
):
    dotenv = tmp_path / "external.env"
    dotenv.write_text("ECOBIN_P2_SHOULD_NOT_LOAD=yes\n", encoding="utf-8")
    code = tmp_path / "code"
    code.mkdir()
    monkeypatch.setenv("ECOBIN_CONFIG_MODE", "production")
    monkeypatch.setenv("ECOBIN_DOTENV_PATH", str(dotenv))
    monkeypatch.delenv("ECOBIN_P2_SHOULD_NOT_LOAD", raising=False)

    assert config._configure_environment_source(code) == "production"
    assert "ECOBIN_P2_SHOULD_NOT_LOAD" not in os.environ


def test_development_explicitly_loads_without_overriding_process_values(
    tmp_path,
    monkeypatch,
):
    dotenv = tmp_path / "development.env"
    dotenv.write_text(
        "ECOBIN_P2_EXISTING=from-file\nECOBIN_P2_LOADED=loaded\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ECOBIN_CONFIG_MODE", "development")
    monkeypatch.setenv("ECOBIN_DOTENV_PATH", str(dotenv))
    monkeypatch.setenv("ECOBIN_P2_EXISTING", "from-process")
    monkeypatch.delenv("ECOBIN_P2_LOADED", raising=False)
    try:
        assert config._configure_environment_source(tmp_path) == "development"
        assert os.environ["ECOBIN_P2_EXISTING"] == "from-process"
        assert os.environ["ECOBIN_P2_LOADED"] == "loaded"
    finally:
        os.environ.pop("ECOBIN_P2_LOADED", None)


def test_development_requires_the_explicit_file_to_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOBIN_CONFIG_MODE", "development")
    monkeypatch.setenv("ECOBIN_DOTENV_PATH", str(tmp_path / "missing.env"))

    with pytest.raises(FileNotFoundError, match="does not exist"):
        config._configure_environment_source(tmp_path)


def test_unknown_configuration_mode_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOBIN_CONFIG_MODE", "staging")

    with pytest.raises(ValueError, match="production or development"):
        config._configure_environment_source(tmp_path)


def test_production_environment_template_uses_fhs_and_no_device_secrets():
    content = (HARDWARE / "install" / "hardware.env.example").read_text(
        encoding="utf-8"
    )
    assignments = [
        line
        for line in content.splitlines()
        if line and not line.startswith("#")
    ]

    for assignment in (
        "ECOBIN_DATA_DIR=/var/lib/ecobin/hardware",
        "ECOBIN_EDGE_STORE_PATH=/var/lib/ecobin/hardware/edge.db",
        "ECOBIN_EDGE_BOOT_ID_PATH=/var/lib/ecobin/hardware/edge-boot-id",
        "ECOBIN_DEVICE_CONFIG_PATH=/var/lib/ecobin/hardware/device-config.json",
        "ECOBIN_DEVICE_CREDENTIALS_PATH=/etc/ecobin/device-credentials.json",
        "ECOBIN_MCU_BOOT0_WPI=2",
        "ECOBIN_MCU_RESET_WPI=5",
        "ECOBIN_MCU_BOOT0_ACTIVE_LEVEL=1",
        "ECOBIN_MCU_RESET_ACTIVE_LEVEL=1",
    ):
        assert assignment in content
    for forbidden in (
        "ECOBIN_PRODUCT_ID=",
        "ECOBIN_DEVICE_NAME=",
        "ECOBIN_DEVICE_KEY=",
        "ECOBIN_UART_HIL_REQUIRED_CAPABILITIES=",
        "ECOBIN_EDGE_VERSION=0.1.0",
    ):
        assert forbidden not in content
    assert not any("simulated://" in assignment for assignment in assignments)


def test_repository_dotenv_template_is_explicitly_development_only():
    content = (HARDWARE / ".env.example").read_text(encoding="utf-8")

    assert "ECOBIN_CONFIG_MODE=development" in content
    assert "/etc/ecobin/hardware.env" in content
    assert "代码目录出现 .env 会拒绝启动" in content
