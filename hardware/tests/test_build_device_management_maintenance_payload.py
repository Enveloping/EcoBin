from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

import pytest

from system.device_management_maintenance_installer import (
    COMMUNICATION_AGENT_FILES,
    DEVICE_UPDATER_FILES,
    DEVICE_UPDATER_HELPER_FILES,
    DROP_IN_CONTENT,
    DROP_IN_RELATIVE,
    HELPER_UNIT_FILES,
    MAIN_UNIT_FILES,
    MANIFEST_NAME,
    PREFLIGHT_PAYLOAD,
    RELEASE_ENV_PAYLOAD,
    RUNTIME_START_FENCE_CONDITION,
    SYSUSERS_PAYLOAD,
    TMPFILES_PAYLOAD,
    _expected_fixed_payload_files,
    expected_payload_directories,
    load_and_validate_payload,
)
from tools.build_device_management_maintenance_payload import (
    PayloadBuildError,
    PayloadIdentity,
    build_device_management_maintenance_payload,
)


IDENTITY = PayloadIdentity(
    payload_id="stage3-maintenance-v13-to-v16",
    source_git_commit="a" * 40,
    expected_image_release_id="single-card-hil-20260831-13",
    expected_image_version="0.1.0-single-card.20260831.13",
    communication_release_id="communication-20260903-16",
    updater_release_id="updater-20260903-16",
)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_hardware_source(root: Path) -> None:
    for name in {*COMMUNICATION_AGENT_FILES, *DEVICE_UPDATER_FILES}:
        _write(root.joinpath(*PurePosixPath(name).parts), f"root:{name}\n".encode())
    for name in DEVICE_UPDATER_HELPER_FILES:
        _write(
            root.joinpath("device_management", "helpers", *PurePosixPath(name).parts),
            f"helper:{name}\n".encode(),
        )
    for name in MAIN_UNIT_FILES:
        _write(
            root / name,
            (
                "[Unit]\n"
                f"Description=Test fixture for {name}\n"
                f"{RUNTIME_START_FENCE_CONDITION}\n"
                "\n[Service]\n"
                "ExecStart=/bin/true\n"
            ).encode(),
        )
    for name in HELPER_UNIT_FILES:
        _write(
            root / "device_management/helpers/systemd" / name,
            (
                "[Unit]\n"
                f"Description=Test fixture for {name}\n"
                f"{RUNTIME_START_FENCE_CONDITION}\n"
                "\n[Service]\n"
                "ExecStart=/bin/true\n"
            ).encode(),
        )
    _write(
        root / "device_management/config/sysusers.d/ecobin-device-runtime.conf",
        b"# controlled sysusers\n",
    )
    _write(
        root / "device_management/config/tmpfiles.d/ecobin-device-runtime.conf",
        b"# controlled tmpfiles\n",
    )
    _write(root / "system/business_runtime_preflight.py", b"# permission probe\n")


def _public_key(seed: int) -> bytes:
    der = bytes.fromhex("302a300506032b6570032100") + bytes(
        (seed + index) % 256 for index in range(32)
    )
    body = base64.b64encode(der)
    return b"-----BEGIN PUBLIC KEY-----\n" + body + b"\n-----END PUBLIC KEY-----\n"


def _make_trust(root: Path) -> None:
    root.mkdir()
    _write(root / "current.pem", _public_key(1))
    _write(root / "previous.pem", _public_key(2))


def _fixture_roots(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "hardware-source"
    source.mkdir()
    _make_hardware_source(source)
    trust = tmp_path / "runtime-trust"
    _make_trust(trust)
    return source, trust


def test_builds_exact_canonical_payload_and_validates_it(tmp_path: Path) -> None:
    source, trust = _fixture_roots(tmp_path)
    output = tmp_path / "payload"

    result = build_device_management_maintenance_payload(
        output,
        trust,
        IDENTITY,
        hardware_source_root=source,
    )

    assert result.output == output.absolute()
    raw_manifest = (output / MANIFEST_NAME).read_bytes()
    assert result.manifest_sha256 == hashlib.sha256(raw_manifest).hexdigest()
    assert raw_manifest.endswith(b"\n")
    manifest_document = json.loads(raw_manifest)
    assert raw_manifest == (
        json.dumps(
            manifest_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    validated = load_and_validate_payload(output, result.manifest_sha256)
    assert validated == result.manifest
    assert validated.source_git_commit == IDENTITY.source_git_commit
    assert validated.expected_image_release_id == IDENTITY.expected_image_release_id
    assert validated.expected_image_version == IDENTITY.expected_image_version

    expected_files = _expected_fixed_payload_files() | {
        "trust/runtime-release-keys/current.pem",
        "trust/runtime-release-keys/previous.pem",
    }
    actual_files = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert actual_files == expected_files | {MANIFEST_NAME}
    actual_directories = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_dir()
    }
    assert actual_directories == expected_payload_directories(actual_files)
    assert (output / DROP_IN_RELATIVE).read_bytes() == DROP_IN_CONTENT
    assert (output / RELEASE_ENV_PAYLOAD).read_bytes() == (
        b"ECOBIN_COMMUNICATION_AGENT_VERSION=communication-20260903-16\n"
        b"ECOBIN_DEVICE_UPDATER_VERSION=updater-20260903-16\n"
    )
    assert (output / "communication/app/communication_agent.py").read_bytes() == (
        source / "communication_agent.py"
    ).read_bytes()
    assert (output / "updater/helpers/privileged_control.py").read_bytes() == (
        source / "device_management/helpers/privileged_control.py"
    ).read_bytes()
    assert (output / SYSUSERS_PAYLOAD).read_bytes() == (
        source / "device_management/config/sysusers.d/ecobin-device-runtime.conf"
    ).read_bytes()
    assert (output / TMPFILES_PAYLOAD).read_bytes() == (
        source / "device_management/config/tmpfiles.d/ecobin-device-runtime.conf"
    ).read_bytes()
    assert (output / PREFLIGHT_PAYLOAD).read_bytes() == (
        source / "system/business_runtime_preflight.py"
    ).read_bytes()

    if os.name == "posix":
        assert stat.S_IMODE(output.lstat().st_mode) == 0o755
        for path in output.rglob("*"):
            expected_mode = 0o755 if path.is_dir() else 0o644
            assert stat.S_IMODE(path.lstat().st_mode) == expected_mode


def test_build_is_reproducible_and_accepts_an_existing_empty_output(
    tmp_path: Path,
) -> None:
    source, trust = _fixture_roots(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    second.mkdir()

    first_result = build_device_management_maintenance_payload(
        first, trust, IDENTITY, hardware_source_root=source
    )
    second_result = build_device_management_maintenance_payload(
        second, trust, IDENTITY, hardware_source_root=source
    )

    assert first_result.manifest_sha256 == second_result.manifest_sha256
    assert (first / MANIFEST_NAME).read_bytes() == (second / MANIFEST_NAME).read_bytes()
    first_files = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files


def test_refuses_nonempty_or_nested_trust_output(tmp_path: Path) -> None:
    source, trust = _fixture_roots(tmp_path)
    nonempty = tmp_path / "nonempty"
    _write(nonempty / "keep.txt", b"owned by caller\n")

    with pytest.raises(PayloadBuildError, match="must not exist or must be an empty"):
        build_device_management_maintenance_payload(
            nonempty, trust, IDENTITY, hardware_source_root=source
        )
    assert (nonempty / "keep.txt").read_bytes() == b"owned by caller\n"

    with pytest.raises(PayloadBuildError, match="inside the runtime trust"):
        build_device_management_maintenance_payload(
            trust / "payload", trust, IDENTITY, hardware_source_root=source
        )


def test_refuses_unexpected_or_non_public_trust_material_without_publishing(
    tmp_path: Path,
) -> None:
    source, trust = _fixture_roots(tmp_path)
    _write(trust / "README.txt", b"unexpected\n")
    output = tmp_path / "payload"

    with pytest.raises(PayloadBuildError, match="unexpected entry"):
        build_device_management_maintenance_payload(
            output, trust, IDENTITY, hardware_source_root=source
        )
    assert not output.exists()

    (trust / "README.txt").unlink()
    (trust / "current.pem").write_bytes(
        b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"
    )
    with pytest.raises(PayloadBuildError, match="validator rejected"):
        build_device_management_maintenance_payload(
            output, trust, IDENTITY, hardware_source_root=source
        )
    assert not output.exists()


def test_refuses_invalid_identity_before_creating_output(tmp_path: Path) -> None:
    source, trust = _fixture_roots(tmp_path)
    output = tmp_path / "payload"
    bad = PayloadIdentity(
        payload_id=IDENTITY.payload_id,
        source_git_commit="A" * 40,
        expected_image_release_id=IDENTITY.expected_image_release_id,
        expected_image_version=IDENTITY.expected_image_version,
        communication_release_id=IDENTITY.communication_release_id,
        updater_release_id=IDENTITY.updater_release_id,
    )

    with pytest.raises(PayloadBuildError, match="40 lowercase hexadecimal"):
        build_device_management_maintenance_payload(
            output, trust, bad, hardware_source_root=source
        )
    assert not output.exists()


def test_refuses_symlinked_repository_source(tmp_path: Path) -> None:
    source, trust = _fixture_roots(tmp_path)
    source_file = source / "communication_agent.py"
    replacement = source / "replacement.py"
    replacement.write_bytes(source_file.read_bytes())
    source_file.unlink()
    try:
        source_file.symlink_to(replacement.name)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable in this test environment")
    output = tmp_path / "payload"

    with pytest.raises(PayloadBuildError, match="must be a regular file"):
        build_device_management_maintenance_payload(
            output, trust, IDENTITY, hardware_source_root=source
        )
    assert not output.exists()
