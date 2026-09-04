from __future__ import annotations

import json
import os
import stat
import base64
from pathlib import Path

import pytest

from business_identity import validate_business_identity_document
from communication_credentials import validate_communication_credentials_document
from device_runtime_projection import install_device_runtime_projections
from tests.test_device_credentials import valid_document


def _valid_source_document() -> dict:
    document = valid_document()
    document["oneNet"]["deviceKey"] = base64.b64encode(
        b"device-secret"
    ).decode("ascii")
    return document


def _service_identity() -> tuple[int, int]:
    if os.name != "posix":
        return 1001, 1001
    uid = os.getuid()
    gid = os.getgid()
    if uid == 0:
        return 1201, 1201
    return uid, gid


def _prepare_private_directory(path: Path, owner: tuple[int, int]) -> None:
    path.mkdir()
    path.chmod(0o700)
    if os.name == "posix" and os.getuid() == 0:
        os.chown(path, owner[0], owner[1])


def test_enrollment_projects_exact_owner_specific_documents(tmp_path: Path) -> None:
    source = tmp_path / "device-credentials.json"
    source.write_text(json.dumps(_valid_source_document()), encoding="utf-8")
    source.chmod(0o600)
    communication_owner = _service_identity()
    business_owner = _service_identity()
    communication_dir = tmp_path / "communication"
    business_dir = tmp_path / "business"
    _prepare_private_directory(communication_dir, communication_owner)
    _prepare_private_directory(business_dir, business_owner)

    install_device_runtime_projections(
        source_path=source,
        communication_target=communication_dir / "onenet-credentials.json",
        business_target=business_dir / "device-identity.json",
        user_lookup=lambda name: {
            "ecobin-communication": communication_owner,
            "ecobin-business": business_owner,
        }[name],
    )

    communication_raw = (communication_dir / "onenet-credentials.json").read_text(
        encoding="utf-8"
    )
    business_raw = (business_dir / "device-identity.json").read_text(
        encoding="utf-8"
    )
    communication = json.loads(communication_raw)
    business = json.loads(business_raw)
    validate_communication_credentials_document(communication)
    validate_business_identity_document(business)
    assert set(communication) == {
        "schemaVersion",
        "productId",
        "deviceName",
        "deviceKey",
        "mqttHost",
        "mqttPort",
    }
    assert set(business) == {
        "schemaVersion",
        "assetUid",
        "deviceName",
        "modelCode",
        "expectedPortCount",
        "deviceEntryUrl",
    }
    assert "deviceKey" not in business_raw
    assert "remoteSupport" not in communication_raw + business_raw
    if os.name == "posix":
        for path, owner in (
            (communication_dir / "onenet-credentials.json", communication_owner),
            (business_dir / "device-identity.json", business_owner),
        ):
            details = path.stat()
            assert (details.st_uid, details.st_gid) == owner
            assert stat.S_IMODE(details.st_mode) == 0o600


def test_projection_refuses_missing_permanent_identity(tmp_path: Path) -> None:
    source = tmp_path / "device-credentials.json"
    source.write_text(json.dumps(_valid_source_document()), encoding="utf-8")
    source.chmod(0o600)

    with pytest.raises(RuntimeError, match="identity is unavailable"):
        install_device_runtime_projections(
            source_path=source,
            communication_target=tmp_path / "communication/onenet.json",
            business_target=tmp_path / "business/identity.json",
            user_lookup=lambda _name: None,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory mode boundary")
def test_projection_refuses_an_overpermissive_service_directory(
    tmp_path: Path,
) -> None:
    if os.getuid() == 0:
        pytest.skip("root ownership fixture is covered by the successful test")
    source = tmp_path / "device-credentials.json"
    source.write_text(json.dumps(_valid_source_document()), encoding="utf-8")
    source.chmod(0o600)
    communication = tmp_path / "communication"
    business = tmp_path / "business"
    communication.mkdir(mode=0o755)
    business.mkdir(mode=0o700)
    owner = (os.getuid(), os.getgid())

    with pytest.raises(RuntimeError, match="ownership or mode"):
        install_device_runtime_projections(
            source_path=source,
            communication_target=communication / "onenet.json",
            business_target=business / "identity.json",
            user_lookup=lambda _name: owner,
        )


@pytest.mark.skipif(
    os.name != "posix" or os.getuid() != 0,
    reason="requires root to model systemd's secondary IPC group",
)
def test_projection_accepts_private_state_owned_by_service_uid_and_ipc_gid(
    tmp_path: Path,
) -> None:
    source = tmp_path / "device-credentials.json"
    source.write_text(json.dumps(_valid_source_document()), encoding="utf-8")
    source.chmod(0o600)
    owner = (1201, 1201)
    ipc_gid = 1202
    communication = tmp_path / "communication"
    business = tmp_path / "business"
    for directory in (communication, business):
        directory.mkdir(mode=0o700)
        os.chown(directory, owner[0], ipc_gid)
    communication_target = communication / "onenet-credentials.json"
    business_target = business / "device-identity.json"
    for target in (communication_target, business_target):
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o600)
        os.chown(target, owner[0], ipc_gid)

    install_device_runtime_projections(
        source_path=source,
        communication_target=communication_target,
        business_target=business_target,
        user_lookup=lambda _name: owner,
    )

    assert communication_target.stat().st_uid == owner[0]
    assert business_target.stat().st_uid == owner[0]
