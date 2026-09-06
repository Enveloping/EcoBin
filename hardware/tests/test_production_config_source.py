from __future__ import annotations

import os
import json
import subprocess
import sys
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


def test_device_capability_fact_is_strict_and_boolean(tmp_path):
    path = tmp_path / "device-capabilities.json"
    path.write_text(
        '{"schemaVersion":1,"mcuRemoteUpdateCapable":false,'
        '"factoryReportSha256":"' + "a" * 64 + '"}',
        encoding="utf-8",
    )

    assert config._load_device_capabilities(str(path)) == {
        "schemaVersion": 1,
        "mcuRemoteUpdateCapable": False,
        "factoryReportSha256": "a" * 64,
    }


@pytest.mark.parametrize(
    "document",
    [
        {},
        {
            "schemaVersion": True,
            "mcuRemoteUpdateCapable": False,
            "factoryReportSha256": "a" * 64,
        },
        {
            "schemaVersion": 1,
            "mcuRemoteUpdateCapable": "false",
            "factoryReportSha256": "a" * 64,
        },
        {
            "schemaVersion": 1,
            "mcuRemoteUpdateCapable": True,
            "factoryReportSha256": "a" * 64,
            "unexpected": True,
        },
    ],
)
def test_device_capability_fact_rejects_ambiguous_content(tmp_path, document):
    import json

    path = tmp_path / "device-capabilities.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="capability|Capable"):
        config._load_device_capabilities(str(path))


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
        "ECOBIN_COS_REGION=ap-beijing",
        "ECOBIN_COS_BUCKET_NAME=ecobin-photo-1436310712",
        (
            "ECOBIN_COS_BASE_URL=https://ecobin-photo-1436310712.cos."
            "ap-beijing.myqcloud.com"
        ),
        (
            "ECOBIN_DEVICE_CAPABILITIES_PATH="
            "/var/lib/ecobin/device-capabilities.json"
        ),
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
        "ECOBIN_MCU_UPDATE_ENABLED=",
    ):
        assert forbidden not in content
    assert not any("simulated://" in assignment for assignment in assignments)


def test_repository_dotenv_template_is_explicitly_development_only():
    content = (HARDWARE / ".env.example").read_text(encoding="utf-8")

    assert "ECOBIN_CONFIG_MODE=development" in content
    assert "/etc/ecobin/hardware.env" in content
    assert "代码目录出现 .env 会拒绝启动" in content
    assert "ECOBIN_COS_REGION=ap-beijing" in content
    assert "ECOBIN_COS_BUCKET_NAME=ecobin-photo-1436310712" in content
    assert (
        "ECOBIN_COS_BASE_URL=https://ecobin-photo-1436310712.cos."
        "ap-beijing.myqcloud.com"
    ) in content


def test_local_proxy_configuration_uses_only_the_business_identity(
    tmp_path: Path,
) -> None:
    identity = tmp_path / "device-identity.json"
    identity.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "assetUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "deviceName": "ECM0-PROXY-TEST",
                "modelCode": "EC-M0",
                "expectedPortCount": 1,
                "deviceEntryUrl": "https://www.jinshoubao.com/d/test",
            }
        ),
        encoding="utf-8",
    )
    identity.chmod(0o600)
    dotenv = tmp_path / "development.env"
    dotenv.write_text("ECOBIN_TEST_MARKER=1\n", encoding="utf-8")
    environment = os.environ.copy()
    for name in (
        "ECOBIN_PRODUCT_ID",
        "ECOBIN_DEVICE_NAME",
        "ECOBIN_DEVICE_KEY",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "ECOBIN_CONFIG_MODE": "development",
            "ECOBIN_DOTENV_PATH": str(dotenv),
            "ECOBIN_CLOUD_TRANSPORT_MODE": "local-proxy",
            "ECOBIN_BUSINESS_IDENTITY_PATH": str(identity),
            "ECOBIN_MCU_UPDATE_ENABLED": "true",
            "PYTHONPATH": str(HARDWARE),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,config; print(json.dumps({"
                "'device':config.DEVICE_NAME,"
                "'product':config.PRODUCT_ID,"
                "'key':config.DEVICE_KEY,"
                "'credentials':config.DEVICE_CREDENTIALS is None,"
                "'capable':config.MCU_REMOTE_UPDATE_CAPABLE,"
                "'legacyUpdater':config.MCU_UPDATE_ENABLED}))"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "device": "ECM0-PROXY-TEST",
        "product": "",
        "key": "",
        "credentials": True,
        "capable": True,
        "legacyUpdater": False,
    }
