from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from device_credentials import (
    effective_onenet_credentials,
    load_device_credentials,
    validate_device_credentials,
)


def _openssh_private() -> str:
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode("ascii")


def _openssh_public() -> str:
    return ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode("ascii")


def valid_document() -> dict:
    return {
        "schemaVersion": 1,
        "assetUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "hardwareSn": "ECM0-0123456789ABCDEFGHJKMNPQ",
        "modelCode": "EC-M0",
        "expectedPortCount": 1,
        "sshHostPublicKey": _openssh_public(),
        "oneNet": {
            "productId": "product-1",
            "deviceName": "ECM0-0123456789ABCDEFGHJKMNPQ",
            "deviceId": "onenet-device-id",
            "deviceKey": "device-secret",
            "mqttHost": "studio-mqtt.heclouds.com",
            "mqttPort": 1883,
        },
        "deviceEntryUrl": "https://www.jinshoubao.com/d/asset",
        "remoteSupport": {
            "tunnelHost": "support.example.com",
            "tunnelSshPort": 22,
            "tunnelUser": "ecobin-tunnel",
            "tunnelServerHostPublicKey": _openssh_public(),
            "tunnelIdentityPrivateKey": _openssh_private(),
            "jumpUser": "ecobin-jump",
            "maintenancePrincipal": (
                "ecobin-device-ECM0-0123456789ABCDEFGHJKMNPQ"
            ),
            "maintenanceCaPublicKey": _openssh_public(),
        },
    }


def test_valid_bundle_resolves_onenet_and_pinned_host(tmp_path: Path):
    path = tmp_path / "device-credentials.json"
    path.write_text(json.dumps(valid_document()), encoding="utf-8")
    path.chmod(0o600)

    bundle = load_device_credentials(path, required=True)

    assert bundle.hardware_sn == bundle.one_net.device_name
    assert bundle.remote_support.known_hosts_name() == "support.example.com"
    assert bundle.remote_support.known_hosts_line().startswith(
        "support.example.com ssh-ed25519 "
    )


def test_nonstandard_server_port_uses_openssh_bracket_host_name():
    document = valid_document()
    document["remoteSupport"]["tunnelSshPort"] = 2222

    bundle = validate_device_credentials(document)

    assert bundle.remote_support.known_hosts_name() == "[support.example.com]:2222"


def test_legacy_environment_is_all_or_nothing_and_has_migration_precedence():
    bundle = validate_device_credentials(valid_document())

    resolved = effective_onenet_credentials(
        bundle,
        {
            "ECOBIN_PRODUCT_ID": "legacy-product",
            "ECOBIN_DEVICE_NAME": "legacy-device",
            "ECOBIN_DEVICE_KEY": "legacy-key",
        },
    )

    assert resolved.device_name == "legacy-device"
    with pytest.raises(ValueError, match="incomplete"):
        effective_onenet_credentials(
            bundle,
            {"ECOBIN_PRODUCT_ID": "only-one"},
        )


def test_bundle_rejects_mixed_device_identity_and_principal():
    document = valid_document()
    document["oneNet"]["deviceName"] = "another-device"
    with pytest.raises(ValueError, match="hardwareSn"):
        validate_device_credentials(document)

    document = valid_document()
    document["remoteSupport"]["maintenancePrincipal"] = "another-device"
    with pytest.raises(ValueError, match="maintenancePrincipal"):
        validate_device_credentials(document)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission boundary")
def test_bundle_rejects_group_readable_secret_file(tmp_path: Path):
    path = tmp_path / "device-credentials.json"
    path.write_text(json.dumps(valid_document()), encoding="utf-8")
    path.chmod(0o640)

    with pytest.raises(ValueError, match="group/world"):
        load_device_credentials(path, required=True)
