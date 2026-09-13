from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from business_update_package import BusinessReleasePackageStager
from install import build_business_release
from install.business_release import (
    BUSINESS_APP_FILES,
    BUSINESS_ARCHIVE_PREFIX,
    BUSINESS_ARTIFACT_KIND,
    BUSINESS_RELEASE_FORMAT_VERSION,
    BACKEND_COMMAND_CONTRACT_VERSION,
    COMMUNICATION_BUSINESS_PROTOCOL_MAJOR,
    COMMUNICATION_BUSINESS_PROTOCOL_MINOR,
    DEVICE_EVENT_CONTRACT_VERSION,
    PYTHON_SERIES,
    PROVIDED_BUSINESS_CAPABILITY_BITMAP_HEX,
    REQUIRED_FIXED_FRAME_REVISION,
    REQUIRED_MCU_CAPABILITY_BITMAP_HEX,
    TARGET_PLATFORM,
    UART_PROTOCOL_FAMILY,
    UART_PROTOCOL_MAJOR,
    UART_PROTOCOL_MINOR,
    UPDATER_BUSINESS_PROTOCOL_MAJOR,
    UPDATER_BUSINESS_PROTOCOL_MINOR,
    business_allowlist_sha256,
    validate_business_release_tree,
    write_sha256sums,
)
from install.runtime_payload_manifest import EDGE_SCHEMA_VERSION
from install.runtime_release import sha256_file


RELEASE_ID = "11111111-1111-4111-8111-111111111111"
UPDATE_UID = "22222222-2222-4222-8222-222222222222"


def _make_release(root: Path) -> Path:
    root.mkdir()
    app = root / "app"
    wheelhouse = root / "wheelhouse"
    migrations = root / "migrations"
    app.mkdir()
    wheelhouse.mkdir()
    migrations.mkdir()
    for name in BUSINESS_APP_FILES:
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\n", encoding="utf-8")
    wheel = wheelhouse / "demo-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (root / "requirements-runtime.txt").write_text(
        "demo==1.0\n", encoding="utf-8"
    )
    (root / "requirements-offline.txt").write_text(
        f"demo==1.0 --hash=sha256:{wheel_sha256}\n", encoding="utf-8"
    )
    (root / "manifest.env").write_text(
        "".join(
            (
                f"ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION={BUSINESS_RELEASE_FORMAT_VERSION}\n",
                f"ECOBIN_ARTIFACT_KIND={BUSINESS_ARTIFACT_KIND}\n",
                f"ECOBIN_RELEASE_ID={RELEASE_ID}\n",
                "ECOBIN_VERSION_NAME=1.1.0\n",
                "ECOBIN_RELEASE_SEQUENCE=2\n",
                f"ECOBIN_GIT_COMMIT={'a' * 40}\n",
                f"ECOBIN_PYTHON_SERIES={PYTHON_SERIES}\n",
                f"ECOBIN_TARGET_PLATFORM={TARGET_PLATFORM}\n",
                f"ECOBIN_EDGE_SCHEMA_VERSION={EDGE_SCHEMA_VERSION}\n",
                "ECOBIN_SOURCE_DATE_EPOCH=1\n",
                f"ECOBIN_BUSINESS_ALLOWLIST_SHA256={business_allowlist_sha256()}\n",
                "ECOBIN_BACKEND_COMMAND_CONTRACT_VERSION="
                f"{BACKEND_COMMAND_CONTRACT_VERSION}\n",
                "ECOBIN_DEVICE_EVENT_CONTRACT_VERSION="
                f"{DEVICE_EVENT_CONTRACT_VERSION}\n",
                "ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MAJOR="
                f"{COMMUNICATION_BUSINESS_PROTOCOL_MAJOR}\n",
                "ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MINOR="
                f"{COMMUNICATION_BUSINESS_PROTOCOL_MINOR}\n",
                "ECOBIN_UPDATER_BUSINESS_PROTOCOL_MAJOR="
                f"{UPDATER_BUSINESS_PROTOCOL_MAJOR}\n",
                "ECOBIN_UPDATER_BUSINESS_PROTOCOL_MINOR="
                f"{UPDATER_BUSINESS_PROTOCOL_MINOR}\n",
                f"ECOBIN_UART_PROTOCOL_FAMILY={UART_PROTOCOL_FAMILY}\n",
                f"ECOBIN_UART_PROTOCOL_MAJOR={UART_PROTOCOL_MAJOR}\n",
                f"ECOBIN_UART_PROTOCOL_MINOR={UART_PROTOCOL_MINOR}\n",
                "ECOBIN_REQUIRED_FIXED_FRAME_REVISION="
                f"{REQUIRED_FIXED_FRAME_REVISION}\n",
                "ECOBIN_REQUIRED_MCU_CAPABILITY_BITMAP_HEX="
                f"{REQUIRED_MCU_CAPABILITY_BITMAP_HEX}\n",
                "ECOBIN_PROVIDED_BUSINESS_CAPABILITY_BITMAP_HEX="
                f"{PROVIDED_BUSINESS_CAPABILITY_BITMAP_HEX}\n",
            )
        ),
        encoding="utf-8",
    )
    (root / "release.env").write_text(
        "ECOBIN_EDGE_VERSION=1.1.0\n"
        f"ECOBIN_BUSINESS_RELEASE_ID={RELEASE_ID}\n"
        "ECOBIN_BUSINESS_RELEASE_SEQUENCE=2\n",
        encoding="utf-8",
    )
    write_sha256sums(root)
    return root


def _archive_and_sign(tmp_path: Path, release: Path) -> tuple[Path, Path, Path]:
    archive = tmp_path / "package.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(release, arcname=f"{BUSINESS_ARCHIVE_PREFIX}{RELEASE_ID}")
    private_key = Ed25519PrivateKey.generate()
    signature = tmp_path / "package.sig"
    signature.write_bytes(private_key.sign(archive.read_bytes()))
    trust = tmp_path / "trust"
    trust.mkdir()
    public_key = trust / "business_2026.pem"
    public_key.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    if os.name == "posix":
        archive.chmod(0o600)
        signature.chmod(0o600)
        trust.chmod(0o755)
        public_key.chmod(0o644)
    return archive, signature, trust


def test_business_allowlist_excludes_permanent_and_legacy_owners() -> None:
    assert len(BUSINESS_APP_FILES) == len(set(BUSINESS_APP_FILES))
    assert "local_proxy_cloud_transport.py" in BUSINESS_APP_FILES
    assert "business_update_coordinator.py" not in BUSINESS_APP_FILES
    assert "direct_onenet_transport.py" not in BUSINESS_APP_FILES
    assert "mqtt_client.py" not in BUSINESS_APP_FILES
    assert "device_credentials.py" not in BUSINESS_APP_FILES
    assert "device_acceptance.py" not in BUSINESS_APP_FILES
    assert "factory_progress.py" not in BUSINESS_APP_FILES
    assert "factory_seal/runtime.py" not in BUSINESS_APP_FILES
    assert "mcu_firmware_updater.py" not in BUSINESS_APP_FILES
    assert "system/mcu_safe_gpio.py" not in BUSINESS_APP_FILES
    assert "system/orangepi_boot_config.py" not in BUSINESS_APP_FILES
    assert "factory_seal/admission.py" in BUSINESS_APP_FILES
    assert "factory_seal/validation.py" in BUSINESS_APP_FILES
    assert "native_recovery_close_isolation.py" in BUSINESS_APP_FILES
    assert "native_recovery_entry.py" in BUSINESS_APP_FILES
    assert "native_recovery_runtime.py" in BUSINESS_APP_FILES


def test_proxy_business_sources_import_without_factory_only_modules(
    tmp_path: Path,
) -> None:
    source = Path(__file__).resolve().parents[1]
    app = tmp_path / "app"
    build_business_release._copy_business_sources(source, app)
    identity = tmp_path / "device-identity.json"
    identity.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "assetUid": "00000000-0000-4000-8000-000000000001",
                "deviceName": "business-package-import-check",
                "modelCode": "EC-M0",
                "expectedPortCount": 1,
                "deviceEntryUrl": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    if os.name == "posix":
        identity.chmod(0o600)
    environment = dict(os.environ)
    environment.update(
        {
            "ECOBIN_CONFIG_MODE": "production",
            "ECOBIN_CLOUD_TRANSPORT_MODE": "local-proxy",
            "ECOBIN_BUSINESS_IDENTITY_PATH": str(identity),
            "ECOBIN_MCU_UPDATE_ENABLED": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    environment.pop("ECOBIN_DOTENV_PATH", None)

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; sys.path.insert(0, sys.argv[1]); import main; "
                "forbidden={'device_acceptance','factory_progress',"
                "'factory_seal.runtime','direct_onenet_transport',"
                "'device_credentials','mcu_firmware_updater'}; "
                "assert forbidden.isdisjoint(sys.modules)"
            ),
            str(app),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_business_dependency_export_uses_dedicated_lock_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "requirements.txt"
    calls: list[list[str]] = []

    def fake_run(arguments, **_kwargs):
        calls.append(list(arguments))
        destination.write_text("pyserial==3.5\n", encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(build_business_release, "_run", fake_run)
    build_business_release._export_business_requirements(
        tmp_path / "hardware", destination
    )

    assert len(calls) == 1
    assert calls[0][calls[0].index("--only-group") + 1] == "business"


def test_business_builder_signs_only_the_final_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hardware"
    source.mkdir()
    (source / "edge_store.py").write_text(f"CURRENT_SCHEMA_VERSION = {EDGE_SCHEMA_VERSION}\n", encoding="utf-8")
    output = tmp_path / "output"
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "business-private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    if os.name == "posix":
        private_path.chmod(0o600)

    monkeypatch.setattr(
        build_business_release,
        "_require_arm64_python311_builder",
        lambda: None,
    )
    monkeypatch.setattr(
        build_business_release,
        "_git_metadata",
        lambda _source: ("a" * 40, 1),
    )
    monkeypatch.setattr(
        build_business_release,
        "_copy_business_sources",
        lambda _source, app: app.mkdir(parents=True),
    )
    monkeypatch.setattr(
        build_business_release,
        "_export_business_requirements",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_business_release,
        "_build_wheelhouse",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_business_release,
        "_write_offline_requirements",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_business_release,
        "validate_business_release_tree",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        build_business_release,
        "_compile_business_sources",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_business_release,
        "_verify_business_environment",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_business_release,
        "_write_archive",
        lambda _root, archive, **_kwargs: archive.write_bytes(
            b"deterministic business archive"
        ),
    )

    archive, checksum, signature = build_business_release.build_release(
        source_root=source,
        output_directory=output,
        release_id=RELEASE_ID,
        version_name="1.1.0",
        release_sequence=2,
        signing_private_key=private_path,
    )

    assert archive.name == f"ecobin-business-{RELEASE_ID}.tar.gz"
    assert checksum.read_text(encoding="ascii") == (
        f"{sha256_file(archive)}  {archive.name}\n"
    )
    private_key.public_key().verify(signature.read_bytes(), archive.read_bytes())
    assert private_path.name.encode() not in archive.read_bytes()


def test_release_tree_requires_exact_identity_and_checksums(tmp_path: Path) -> None:
    release = _make_release(tmp_path / "release")

    manifest = validate_business_release_tree(
        release,
        expected_release_id=RELEASE_ID,
        expected_version_name="1.1.0",
        expected_release_sequence=2,
    )

    assert manifest["ECOBIN_RELEASE_ID"] == RELEASE_ID
    assert manifest["ECOBIN_BACKEND_COMMAND_CONTRACT_VERSION"] == "2"
    assert manifest["ECOBIN_REQUIRED_FIXED_FRAME_REVISION"] == "2"
    (release / "app/main.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(Exception, match="checksum mismatch"):
        validate_business_release_tree(release)


def test_stager_verifies_signature_and_is_idempotent(tmp_path: Path) -> None:
    release = _make_release(tmp_path / "release")
    archive, signature, trust = _archive_and_sign(tmp_path, release)
    incoming = tmp_path / "incoming"
    staging = tmp_path / "staging"
    update_incoming = incoming / UPDATE_UID
    update_incoming.mkdir(parents=True)
    staging.mkdir()
    if os.name == "posix":
        incoming.chmod(0o700)
        update_incoming.chmod(0o700)
        staging.chmod(0o700)
    archive.replace(update_incoming / "package.tar.gz")
    signature.replace(update_incoming / "package.sig")

    def make_environment(root: Path) -> None:
        python = root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("python-3.11\n", encoding="utf-8")

    identity = os.getuid() if os.name == "posix" else 0
    stager = BusinessReleasePackageStager(
        incoming,
        staging,
        trust,
        free_space_reserve_bytes=0,
        environment_builder=make_environment,
        trusted_key_uid=identity,
        trusted_key_gid=identity,
    )
    request = {
        "update_uid": UPDATE_UID,
        "release_id": RELEASE_ID,
        "version_name": "1.1.0",
        "release_sequence": 2,
        "expected_package_sha256": sha256_file(update_incoming / "package.tar.gz"),
        "expected_package_size": (update_incoming / "package.tar.gz").stat().st_size,
        "signing_key_id": "business_2026",
        "business_database_size": 4096,
    }

    first = stager.stage(**request)
    second = stager.stage(**request)

    assert first.staged_path == second.staged_path
    assert (first.staged_path / ".venv/bin/python").is_file()
    assert (
        first.staged_path / ".venv/.ecobin-business-environment.json"
    ).is_file()
