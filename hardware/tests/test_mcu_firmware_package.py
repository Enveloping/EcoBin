from __future__ import annotations

import json
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcu_firmware_package import (
    FirmwarePackageError,
    create_package,
    generate_identity,
    verify_package,
)


def _private_key(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return key


def _package(tmp_path: Path):
    key_path = tmp_path / "signing.pem"
    key = _private_key(key_path)
    header = tmp_path / "firmware_identity.h"
    identity = tmp_path / "identity.json"
    release_uid = uuid.uuid4()
    generated = generate_identity(
        version="1.2.3-rc.1",
        version_code=10203,
        header_path=header,
        metadata_path=identity,
        release_uid=release_uid,
    )
    image = tmp_path / "firmware.bin"
    image.write_bytes(b"\x00\x20\x00\x08" + bytes(range(256)) * 16)
    output = tmp_path / "firmware.efw"
    verified = create_package(
        image_path=image,
        identity_metadata_path=identity,
        private_key_path=key_path,
        key_id="RELEASE_2026_01",
        hardware_compatibility="ECOBIN_MAINBOARD_V1.1",
        build_commit="0123456789abcdef",
        built_at="2026-08-19T10:00:00Z",
        output_path=output,
    )
    return key, output, generated, verified


def test_signed_package_round_trip_and_generated_identity(tmp_path: Path):
    key, package, generated, created = _package(tmp_path)

    verified = verify_package(
        package,
        {"RELEASE_2026_01": key.public_key()},
        expected_hardware_compatibility="ECOBIN_MAINBOARD_V1.1",
    )

    assert verified.manifest == created.manifest
    assert verified.manifest["releaseUid"] == generated["releaseUid"]
    assert verified.manifest["firmwareIdentityHex"] in (
        tmp_path / "firmware_identity.h"
    ).read_text(encoding="ascii").replace("0x", "").replace(",", "").replace(" ", "").lower()
    assert verified.image.startswith(b"\x00\x20\x00\x08")


def test_mcu_readme_identity_command_selects_hardware_uv_project(
    tmp_path: Path,
):
    repository_root = Path(__file__).resolve().parents[2]
    readme = (
        repository_root / "hardware_mcu" / "README.md"
    ).read_text(encoding="utf-8")
    assert "uv run --project hardware --python 3.11" in readme

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to verify the documented build command")
    header = tmp_path / "firmware_identity.h"
    metadata = tmp_path / "firmware_identity.json"
    result = subprocess.run(
        [
            uv,
            "run",
            "--project",
            "hardware",
            "--python",
            "3.11",
            "python",
            "hardware/mcu_firmware_package.py",
            "identity",
            "--version",
            "1.0.0",
            "--version-code",
            "10000",
            "--header",
            str(header),
            "--metadata",
            str(metadata),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert header.is_file()
    assert json.loads(metadata.read_text(encoding="utf-8"))[
        "firmwareVersionCode"
    ] == 10000


def test_rejects_wrong_board_and_unknown_key(tmp_path: Path):
    key, package, _, _ = _package(tmp_path)
    with pytest.raises(FirmwarePackageError, match="another board"):
        verify_package(
            package,
            {"RELEASE_2026_01": key.public_key()},
            expected_hardware_compatibility="OTHER_BOARD",
        )
    with pytest.raises(FirmwarePackageError, match="not trusted"):
        verify_package(package, {})


def test_rejects_image_tampering_even_with_original_signature(tmp_path: Path):
    key, package, _, _ = _package(tmp_path)
    tampered = tmp_path / "tampered.efw"
    with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(
        tampered, "w", compression=zipfile.ZIP_STORED
    ) as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "firmware.bin":
                data = data[:-1] + bytes((data[-1] ^ 0xFF,))
            target.writestr(name, data)

    with pytest.raises(FirmwarePackageError, match="SHA-256"):
        verify_package(tampered, {"RELEASE_2026_01": key.public_key()})


def test_rejects_noncanonical_manifest(tmp_path: Path):
    key, package, _, _ = _package(tmp_path)
    rewritten = tmp_path / "rewritten.efw"
    with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(
        rewritten, "w", compression=zipfile.ZIP_STORED
    ) as target:
        manifest = json.loads(source.read("manifest.json"))
        target.writestr("firmware.bin", source.read("firmware.bin"))
        target.writestr("manifest.json", json.dumps(manifest, indent=2))
        target.writestr("manifest.sig", source.read("manifest.sig"))

    with pytest.raises(FirmwarePackageError, match="not canonical"):
        verify_package(rewritten, {"RELEASE_2026_01": key.public_key()})
