from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from communication_credentials import load_communication_credentials


def _write(path: Path, **overrides) -> None:
    document = {
        "schemaVersion": 1,
        "productId": "product-1",
        "deviceName": "SN-0001",
        "deviceKey": base64.b64encode(b"secret-device-key").decode("ascii"),
        "mqttHost": "studio-mqtt.heclouds.com",
        "mqttPort": 1883,
    }
    document.update(overrides)
    path.write_text(json.dumps(document), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)


def test_loads_minimal_private_communication_credential(tmp_path):
    path = tmp_path / "onenet-credentials.json"
    _write(path)

    credential = load_communication_credentials(path)

    assert credential.product_id == "product-1"
    assert credential.device_name == "SN-0001"
    assert credential.mqtt_port == 1883


@pytest.mark.parametrize(
    "overrides",
    [
        {"schemaVersion": 2},
        {"extra": True},
        {"deviceKey": "not base64!"},
        {"mqttHost": "https://broker.example"},
        {"mqttPort": 0},
    ],
)
def test_rejects_malformed_credential_fields(tmp_path, overrides):
    path = tmp_path / "onenet-credentials.json"
    _write(path, **overrides)
    with pytest.raises(ValueError):
        load_communication_credentials(path)


def test_rejects_symbolic_link(tmp_path):
    target = tmp_path / "target.json"
    _write(target)
    link = tmp_path / "onenet-credentials.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(ValueError, match="regular file"):
        load_communication_credentials(link)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode boundary")
def test_rejects_broad_permissions(tmp_path):
    path = tmp_path / "onenet-credentials.json"
    _write(path)
    path.chmod(0o640)
    with pytest.raises(ValueError, match="mode 0600"):
        load_communication_credentials(path)
