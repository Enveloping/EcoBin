from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from first_boot.management_layer import (
    ImageManagedLayerPaths,
    image_managed_layer_complete,
)


def _write_valid_image_layer(tmp_path: Path) -> ImageManagedLayerPaths:
    private_release = tmp_path / "etc/ecobin/image-release.json"
    public_release = tmp_path / "usr/share/ecobin/image-release.json"
    payload_lock = tmp_path / "usr/share/ecobin/software-payload.lock.json"
    release_environment = (
        tmp_path / "usr/share/ecobin/device-management-release.env"
    )
    private_release.parent.mkdir(parents=True)
    public_release.parent.mkdir(parents=True)

    component_release_ids = {
        "hardwareRuntime": "hardware-001",
        "enrollment": "enrollment-001",
        "remoteSupport": "remote-001",
        "factoryTest": "factory-001",
        "firstBoot": "first-boot-001",
        "communicationAgent": "communication-001",
        "deviceUpdater": "updater-001",
    }
    lock = {
        "schemaVersion": 2,
        "lockState": "LOCKED",
        "payloadId": "payload-001",
        "sourceGitCommit": "a" * 40,
        "components": {
            name: {"releaseId": release_id}
            for name, release_id in component_release_ids.items()
        },
        "entries": [],
    }
    lock_bytes = (
        json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    payload_lock.write_bytes(lock_bytes)
    release = {
        "schemaVersion": 1,
        "releaseId": "image-001",
        "version": "0.1.0",
        "gitCommit": "a" * 40,
        "softwarePayloadId": "payload-001",
        "softwarePayloadLockSha256": hashlib.sha256(lock_bytes).hexdigest(),
        "softwarePayloadSchemaVersion": 2,
        "components": {
            name: {"releaseId": release_id}
            for name, release_id in component_release_ids.items()
        },
        "contracts": {},
    }
    release_bytes = (
        json.dumps(release, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    private_release.write_bytes(release_bytes)
    public_release.write_bytes(release_bytes)
    release_environment.write_bytes(
        b"ECOBIN_COMMUNICATION_AGENT_VERSION=communication-001\n"
        b"ECOBIN_DEVICE_UPDATER_VERSION=updater-001\n"
    )
    for path in (
        private_release,
        public_release,
        payload_lock,
        release_environment,
    ):
        path.chmod(0o644)
    return ImageManagedLayerPaths(
        private_image_release=private_release,
        public_image_release=public_release,
        software_payload_lock=payload_lock,
        release_environment=release_environment,
    )


def test_image_managed_layer_accepts_bound_schema_v2_inventory(
    tmp_path: Path,
) -> None:
    paths = _write_valid_image_layer(tmp_path)

    assert image_managed_layer_complete(paths, expected_owner=None)


@pytest.mark.parametrize(
    "corrupt",
    ("private-release", "payload-lock", "release-environment"),
)
def test_image_managed_layer_rejects_inconsistent_inventory(
    tmp_path: Path,
    corrupt: str,
) -> None:
    paths = _write_valid_image_layer(tmp_path)
    target = {
        "private-release": paths.private_image_release,
        "payload-lock": paths.software_payload_lock,
        "release-environment": paths.release_environment,
    }[corrupt]
    target.write_bytes(target.read_bytes() + b"corrupt\n")

    assert not image_managed_layer_complete(paths, expected_owner=None)


def test_image_managed_layer_rejects_legacy_payload_schema(tmp_path: Path) -> None:
    paths = _write_valid_image_layer(tmp_path)
    release = json.loads(paths.private_image_release.read_text(encoding="utf-8"))
    release["softwarePayloadSchemaVersion"] = 1
    release_bytes = (
        json.dumps(release, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    paths.private_image_release.write_bytes(release_bytes)
    paths.public_image_release.write_bytes(release_bytes)

    assert not image_managed_layer_complete(paths, expected_owner=None)
