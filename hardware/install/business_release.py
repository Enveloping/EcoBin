"""Fail-closed format and extraction rules for replaceable business releases.

The permanent communication agent and updater are intentionally absent from
this format.  An archive is trusted only after its externally supplied digest
and Ed25519 signature match, and every extracted file is then checked against
the package's own exact checksum inventory.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from typing import BinaryIO

try:
    from .runtime_payload_manifest import BUSINESS_APP_FILES, EDGE_SCHEMA_VERSION
    from .runtime_release import (
        MAX_ARCHIVE_BYTES,
        MAX_ARCHIVE_MEMBERS,
        ReleaseValidationError,
        _parse_env_file,
        _parse_sha256sums,
        _regular_package_files,
        _safe_relative_path,
        _sha256_stream,
        sha256_file,
        verified_archive_stream,
        write_sha256sums,
    )
except ImportError:  # pragma: no cover - direct execution by the release builder
    from runtime_payload_manifest import (  # type: ignore[no-redef]
        BUSINESS_APP_FILES,
        EDGE_SCHEMA_VERSION,
    )
    from runtime_release import (  # type: ignore[no-redef]
        MAX_ARCHIVE_BYTES,
        MAX_ARCHIVE_MEMBERS,
        ReleaseValidationError,
        _parse_env_file,
        _parse_sha256sums,
        _regular_package_files,
        _safe_relative_path,
        _sha256_stream,
        sha256_file,
        verified_archive_stream,
        write_sha256sums,
    )


BUSINESS_RELEASE_FORMAT_VERSION = "1"
BUSINESS_ARTIFACT_KIND = "orangepi-business-runtime"
PYTHON_SERIES = "3.11"
TARGET_PLATFORM = "linux-arm64"
BUSINESS_ARCHIVE_PREFIX = "ecobin-business-"
BUSINESS_PACKAGE_TOP_LEVEL = frozenset(
    {
        "app",
        "wheelhouse",
        "migrations",
        "manifest.env",
        "release.env",
        "requirements-runtime.txt",
        "requirements-offline.txt",
        "SHA256SUMS",
    }
)
BUSINESS_MANIFEST_KEYS = frozenset(
    {
        "ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION",
        "ECOBIN_ARTIFACT_KIND",
        "ECOBIN_RELEASE_ID",
        "ECOBIN_VERSION_NAME",
        "ECOBIN_RELEASE_SEQUENCE",
        "ECOBIN_GIT_COMMIT",
        "ECOBIN_PYTHON_SERIES",
        "ECOBIN_TARGET_PLATFORM",
        "ECOBIN_EDGE_SCHEMA_VERSION",
        "ECOBIN_SOURCE_DATE_EPOCH",
        "ECOBIN_BUSINESS_ALLOWLIST_SHA256",
    }
)
BUSINESS_RELEASE_ENV_KEYS = frozenset(
    {
        "ECOBIN_EDGE_VERSION",
        "ECOBIN_BUSINESS_RELEASE_ID",
        "ECOBIN_BUSINESS_RELEASE_SEQUENCE",
    }
)
FORBIDDEN_BUSINESS_BASENAMES = frozenset(
    {
        "communication_agent.py",
        "communication_credentials.py",
        "communication_router.py",
        "communication_store.py",
        "updater_agent.py",
        "updater_store.py",
        "business_update_coordinator.py",
        "business_update_store.py",
        "mcu_update_coordinator.py",
        "mcu_update_store.py",
        "remote_support_agent.py",
        "enrollment_bootstrap.py",
        "maintenance_ssh_setup.py",
        "device_credentials.py",
        "direct_onenet_transport.py",
        "mqtt_client.py",
        "mcu_firmware_package.py",
        "mcu_firmware_updater.py",
        "mcu_safe_gpio.py",
    }
)
VERSION_NAME_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


def validate_business_release_id(value: str) -> str:
    if not isinstance(value, str):
        raise ReleaseValidationError("business release ID must be a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ReleaseValidationError("business release ID must be a UUIDv4") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ReleaseValidationError("business release ID must be a lowercase UUIDv4")
    return value


def validate_version_name(value: str) -> str:
    if not isinstance(value, str) or VERSION_NAME_PATTERN.fullmatch(value) is None:
        raise ReleaseValidationError("business version name must be semantic version text")
    if len(value) > 64:
        raise ReleaseValidationError("business version name is too long")
    return value


def validate_release_sequence(value: object) -> int:
    if isinstance(value, bool):
        raise ReleaseValidationError("business release sequence is invalid")
    try:
        sequence = int(value)
    except (TypeError, ValueError):
        raise ReleaseValidationError("business release sequence is invalid") from None
    if str(sequence) != str(value) or not 1 <= sequence <= 2_147_483_647:
        raise ReleaseValidationError("business release sequence is invalid")
    return sequence


def business_allowlist_sha256() -> str:
    payload = "".join(f"{name}\n" for name in BUSINESS_APP_FILES).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_business_release_tree(
    root: str | os.PathLike[str],
    *,
    expected_release_id: str | None = None,
    expected_version_name: str | None = None,
    expected_release_sequence: int | None = None,
) -> dict[str, str]:
    release_root = Path(root)
    if not release_root.is_dir() or release_root.is_symlink():
        raise ReleaseValidationError("business release root must be a regular directory")

    files = _regular_package_files(release_root)
    top_level = {PurePosixPath(name).parts[0] for name in files}
    top_level.update(
        entry.name
        for entry in release_root.iterdir()
        if entry.is_dir() and entry.name != ".venv"
    )
    if top_level != BUSINESS_PACKAGE_TOP_LEVEL:
        raise ReleaseValidationError(
            "business release top-level members do not match the format"
        )

    checksums = _parse_sha256sums(release_root / "SHA256SUMS")
    actual_files = set(files) - {"SHA256SUMS"}
    if set(checksums) != actual_files:
        raise ReleaseValidationError(
            "SHA256SUMS must cover every business package file exactly once"
        )
    for relative, expected_digest in checksums.items():
        if sha256_file(files[relative]) != expected_digest:
            raise ReleaseValidationError(f"business package checksum mismatch: {relative}")

    manifest = _parse_env_file(release_root / "manifest.env")
    if set(manifest) != BUSINESS_MANIFEST_KEYS:
        raise ReleaseValidationError("business manifest keys do not match the format")
    if (
        manifest["ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION"]
        != BUSINESS_RELEASE_FORMAT_VERSION
    ):
        raise ReleaseValidationError("unsupported business release format")
    if manifest["ECOBIN_ARTIFACT_KIND"] != BUSINESS_ARTIFACT_KIND:
        raise ReleaseValidationError("unexpected business artifact kind")
    release_id = validate_business_release_id(manifest["ECOBIN_RELEASE_ID"])
    version_name = validate_version_name(manifest["ECOBIN_VERSION_NAME"])
    sequence = validate_release_sequence(manifest["ECOBIN_RELEASE_SEQUENCE"])
    if expected_release_id is not None and release_id != expected_release_id:
        raise ReleaseValidationError("business release ID differs from expected value")
    if expected_version_name is not None and version_name != expected_version_name:
        raise ReleaseValidationError("business version name differs from expected value")
    if expected_release_sequence is not None and sequence != expected_release_sequence:
        raise ReleaseValidationError("business release sequence differs from expected value")
    if re.fullmatch(r"[0-9a-f]{40}", manifest["ECOBIN_GIT_COMMIT"]) is None:
        raise ReleaseValidationError("invalid business release Git commit")
    if manifest["ECOBIN_PYTHON_SERIES"] != PYTHON_SERIES:
        raise ReleaseValidationError("business release must target Python 3.11")
    if manifest["ECOBIN_TARGET_PLATFORM"] != TARGET_PLATFORM:
        raise ReleaseValidationError("business release must target Linux ARM64")
    if manifest["ECOBIN_EDGE_SCHEMA_VERSION"] != EDGE_SCHEMA_VERSION:
        raise ReleaseValidationError(
            "first-version business updates require the current EdgeStore schema"
        )
    if not manifest["ECOBIN_SOURCE_DATE_EPOCH"].isdigit():
        raise ReleaseValidationError("invalid business release source timestamp")
    if (
        manifest["ECOBIN_BUSINESS_ALLOWLIST_SHA256"]
        != business_allowlist_sha256()
    ):
        raise ReleaseValidationError("business source allowlist differs")

    release_environment = _parse_env_file(release_root / "release.env")
    if set(release_environment) != BUSINESS_RELEASE_ENV_KEYS or release_environment != {
        "ECOBIN_EDGE_VERSION": version_name,
        "ECOBIN_BUSINESS_RELEASE_ID": release_id,
        "ECOBIN_BUSINESS_RELEASE_SEQUENCE": str(sequence),
    }:
        raise ReleaseValidationError("business release.env identity is invalid")

    app_directory = release_root / "app"
    app_files = {
        relative.removeprefix("app/")
        for relative in files
        if relative.startswith("app/")
    }
    if app_files != set(BUSINESS_APP_FILES):
        raise ReleaseValidationError("business app does not match its exact allowlist")
    expected_directories = {
        parent.as_posix()
        for name in BUSINESS_APP_FILES
        for parent in PurePosixPath(name).parents
        if parent != PurePosixPath(".")
    }
    actual_directories = {
        path.relative_to(app_directory).as_posix()
        for path in app_directory.rglob("*")
        if path.is_dir()
    }
    if actual_directories != expected_directories:
        raise ReleaseValidationError("business app contains an unexpected directory")
    if FORBIDDEN_BUSINESS_BASENAMES.intersection(
        PurePosixPath(name).name for name in app_files
    ):
        raise ReleaseValidationError("business app contains permanent or legacy code")

    wheelhouse = release_root / "wheelhouse"
    wheels = list(wheelhouse.iterdir())
    if not wheels or any(
        not wheel.is_file() or wheel.is_symlink() or wheel.suffix != ".whl"
        for wheel in wheels
    ):
        raise ReleaseValidationError("business wheelhouse must contain only wheels")
    for requirements_name in (
        "requirements-runtime.txt",
        "requirements-offline.txt",
    ):
        requirements = release_root / requirements_name
        if not requirements.is_file() or requirements.stat().st_size == 0:
            raise ReleaseValidationError(f"{requirements_name} is missing or empty")

    migrations = release_root / "migrations"
    if any(migrations.iterdir()):
        raise ReleaseValidationError(
            "first-version business updates do not permit schema-changing migrations"
        )
    return manifest


def extract_verified_business_archive(
    archive_path: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    expected_sha256: str,
    signature_path: str | os.PathLike[str],
    signing_key_id: str,
    trusted_public_keys_directory: str | os.PathLike[str],
    expected_release_id: str,
    expected_version_name: str,
    expected_release_sequence: int,
    expected_trust_uid: int = 0,
    expected_trust_gid: int = 0,
) -> dict[str, str]:
    with verified_archive_stream(
        archive_path,
        expected_sha256=expected_sha256,
        signature_path=signature_path,
        signing_key_id=signing_key_id,
        trusted_public_keys_directory=trusted_public_keys_directory,
        expected_uid=expected_trust_uid,
        expected_gid=expected_trust_gid,
    ) as archive:
        archive_release_id = safe_extract_business_archive_stream(
            archive,
            destination,
            expected_sha256=expected_sha256,
        )
    if archive_release_id != expected_release_id:
        raise ReleaseValidationError("business archive directory identity differs")
    return validate_business_release_tree(
        destination,
        expected_release_id=expected_release_id,
        expected_version_name=expected_version_name,
        expected_release_sequence=expected_release_sequence,
    )


def safe_extract_business_archive_stream(
    archive: BinaryIO,
    destination: str | os.PathLike[str],
    *,
    expected_sha256: str,
) -> str:
    target = Path(destination)
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ReleaseValidationError("expected business archive SHA-256 is invalid")
    if _sha256_stream(archive) != expected_sha256:
        raise ReleaseValidationError("business archive SHA-256 mismatch")
    if target.exists():
        if target.is_symlink() or not target.is_dir() or any(target.iterdir()):
            raise ReleaseValidationError(
                "business archive destination must be an empty regular directory"
            )
    else:
        target.mkdir(parents=True, exist_ok=False)

    def ensure_directory(directory: Path) -> None:
        try:
            relative = directory.relative_to(target)
        except ValueError as error:
            raise ReleaseValidationError(
                "business archive directory escapes extraction root"
            ) from error
        current = target
        for part in relative.parts:
            current /= part
            current.mkdir(mode=0o700, exist_ok=True)
            details = current.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise ReleaseValidationError(
                    "business archive directory conflicts with a non-directory"
                )
            os.chmod(current, 0o700)

    archive.seek(0)
    with tarfile.open(fileobj=archive, mode="r:*") as stream:
        members = stream.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise ReleaseValidationError("business archive member count is invalid")
        total_size = sum(member.size for member in members if member.isfile())
        if total_size > MAX_ARCHIVE_BYTES:
            raise ReleaseValidationError("business archive expands beyond the limit")
        names: set[str] = set()
        top_levels: set[str] = set()
        parsed: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        for member in members:
            normalized = member.name[:-1] if member.name.endswith("/") else member.name
            path = _safe_relative_path(normalized)
            canonical = path.as_posix()
            if canonical in names:
                raise ReleaseValidationError("business archive has a duplicate member")
            names.add(canonical)
            top_levels.add(path.parts[0])
            if not member.isdir() and not member.isfile():
                raise ReleaseValidationError("business archive contains a link or special file")
            parsed.append((member, path))
        if len(top_levels) != 1:
            raise ReleaseValidationError("business archive must have one top-level directory")
        top_level = next(iter(top_levels))
        if not top_level.startswith(BUSINESS_ARCHIVE_PREFIX):
            raise ReleaseValidationError("business archive top-level directory is invalid")
        release_id = validate_business_release_id(
            top_level[len(BUSINESS_ARCHIVE_PREFIX) :]
        )

        for member, path in sorted(
            parsed,
            key=lambda item: (len(item[1].parts), item[1].as_posix()),
        ):
            relative_parts = path.parts[1:]
            if not relative_parts:
                if not member.isdir():
                    raise ReleaseValidationError(
                        "business archive top-level member must be a directory"
                    )
                continue
            output = target.joinpath(*relative_parts)
            if member.isdir():
                ensure_directory(output)
                continue
            ensure_directory(output.parent)
            source = stream.extractfile(member)
            if source is None:
                raise ReleaseValidationError("cannot read business archive member")
            descriptor = os.open(
                output,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            with source, os.fdopen(descriptor, "wb") as destination_stream:
                shutil.copyfileobj(source, destination_stream, length=1024 * 1024)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            os.chmod(output, 0o600)
    return release_id


__all__ = [
    "BUSINESS_APP_FILES",
    "BUSINESS_ARTIFACT_KIND",
    "BUSINESS_RELEASE_FORMAT_VERSION",
    "business_allowlist_sha256",
    "extract_verified_business_archive",
    "safe_extract_business_archive_stream",
    "validate_business_release_id",
    "validate_business_release_tree",
    "validate_release_sequence",
    "validate_version_name",
    "write_sha256sums",
]
