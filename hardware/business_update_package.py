"""Local-only staging of signed replaceable business-runtime packages."""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from install.business_release import (
    BUSINESS_RELEASE_FORMAT_VERSION,
    extract_verified_business_archive,
    validate_business_release_tree,
)
from install.runtime_release import ReleaseValidationError, sha256_file


DEFAULT_INCOMING_ROOT = Path("/var/lib/ecobin/updater/business-packages")
DEFAULT_STAGING_ROOT = Path("/var/lib/ecobin/updater/staging")
DEFAULT_SIGNING_KEYS_ROOT = Path("/usr/share/ecobin/business-release-keys")
DEFAULT_FREE_SPACE_RESERVE_BYTES = 256 * 1024 * 1024
MAX_BUSINESS_PACKAGE_BYTES = 1_500_000_000
ENVIRONMENT_MARKER = ".venv/.ecobin-business-environment.json"


class BusinessPackageStageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StagedBusinessRelease:
    update_uid: str
    release_id: str
    version_name: str
    release_sequence: int
    package_sha256: str
    package_size: int
    signing_key_id: str
    manifest: Mapping[str, str]
    staged_path: Path


class BusinessReleasePackageStager:
    """Verify one fixed local package pair and prepare its offline venv."""

    def __init__(
        self,
        incoming_root: str | os.PathLike[str] = DEFAULT_INCOMING_ROOT,
        staging_root: str | os.PathLike[str] = DEFAULT_STAGING_ROOT,
        signing_keys_root: str | os.PathLike[str] = DEFAULT_SIGNING_KEYS_ROOT,
        *,
        free_space_reserve_bytes: int = DEFAULT_FREE_SPACE_RESERVE_BYTES,
        environment_builder: Callable[[Path], None] | None = None,
        trusted_key_uid: int = 0,
        trusted_key_gid: int = 0,
    ) -> None:
        self.incoming_root = Path(incoming_root)
        self.staging_root = Path(staging_root)
        self.signing_keys_root = Path(signing_keys_root)
        if (
            isinstance(free_space_reserve_bytes, bool)
            or not isinstance(free_space_reserve_bytes, int)
            or free_space_reserve_bytes < 0
        ):
            raise ValueError("business update free-space reserve is invalid")
        for value, label in (
            (trusted_key_uid, "trusted key UID"),
            (trusted_key_gid, "trusted key GID"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} is invalid")
        self.free_space_reserve_bytes = free_space_reserve_bytes
        self._environment_builder = environment_builder or _prepare_offline_environment
        self.trusted_key_uid = trusted_key_uid
        self.trusted_key_gid = trusted_key_gid

    def stage(
        self,
        *,
        update_uid: str,
        release_id: str,
        version_name: str,
        release_sequence: int,
        expected_package_sha256: str,
        expected_package_size: int,
        signing_key_id: str,
        business_database_size: int,
    ) -> StagedBusinessRelease:
        _require_uuid4(update_uid, "updateUid")
        _require_uuid4(release_id, "releaseId")
        if (
            isinstance(expected_package_size, bool)
            or not isinstance(expected_package_size, int)
            or not 1 <= expected_package_size <= MAX_BUSINESS_PACKAGE_BYTES
        ):
            raise BusinessPackageStageError(
                "BUSINESS_PACKAGE_SIZE_INVALID",
                "business package size is outside the supported range",
            )
        if (
            isinstance(business_database_size, bool)
            or not isinstance(business_database_size, int)
            or business_database_size < 0
        ):
            raise BusinessPackageStageError(
                "BUSINESS_DATABASE_SIZE_INVALID",
                "business database size could not be confirmed",
            )
        _require_private_directory(self.incoming_root, "business package root")
        _require_private_directory(self.staging_root, "business staging root")
        incoming = self.incoming_root / update_uid
        _require_private_directory(incoming, "business package update directory")
        archive = incoming / "package.tar.gz"
        signature = incoming / "package.sig"
        archive_details = _require_regular_file(archive, "business package")
        _require_regular_file(signature, "business package signature")
        if archive_details.st_size != expected_package_size:
            raise BusinessPackageStageError(
                "BUSINESS_PACKAGE_SIZE_MISMATCH",
                "business package size differs from the update request",
            )
        if sha256_file(archive) != expected_package_sha256:
            raise BusinessPackageStageError(
                "BUSINESS_PACKAGE_SHA256_MISMATCH",
                "business package digest differs from the update request",
            )
        self._require_capacity(expected_package_size, business_database_size)

        update_staging = self.staging_root / update_uid
        _create_or_require_private_directory(update_staging)
        final = update_staging / release_id
        if final.exists() or final.is_symlink():
            try:
                manifest = validate_business_release_tree(
                    final,
                    expected_release_id=release_id,
                    expected_version_name=version_name,
                    expected_release_sequence=release_sequence,
                )
                _verify_environment_marker(final, expected_package_sha256)
            except (ReleaseValidationError, OSError, ValueError) as error:
                raise BusinessPackageStageError(
                    "BUSINESS_STAGING_CONFLICT",
                    "existing business staging content is not the requested package",
                ) from error
            return self._result(
                update_uid,
                release_id,
                version_name,
                release_sequence,
                expected_package_sha256,
                expected_package_size,
                signing_key_id,
                manifest,
                final,
            )

        temporary = update_staging / f".prepare-{release_id}-{os.getpid()}"
        if temporary.exists() or temporary.is_symlink():
            _remove_private_tree(temporary, update_staging)
        try:
            manifest = extract_verified_business_archive(
                archive,
                temporary,
                expected_sha256=expected_package_sha256,
                signature_path=signature,
                signing_key_id=signing_key_id,
                trusted_public_keys_directory=self.signing_keys_root,
                expected_release_id=release_id,
                expected_version_name=version_name,
                expected_release_sequence=release_sequence,
                expected_trust_uid=self.trusted_key_uid,
                expected_trust_gid=self.trusted_key_gid,
            )
            self._environment_builder(temporary)
            _reject_links_and_special_files(temporary)
            _write_environment_marker(temporary, expected_package_sha256)
            os.rename(temporary, final)
            _fsync_directory(update_staging)
        except OSError as error:
            if temporary.exists() or temporary.is_symlink():
                _remove_private_tree(temporary, update_staging)
            code = (
                "DEVICE_STORAGE_INSUFFICIENT"
                if error.errno == errno.ENOSPC
                else "BUSINESS_PACKAGE_PREPARATION_FAILED"
            )
            raise BusinessPackageStageError(
                code,
                "business package environment could not be prepared",
            ) from error
        except (ReleaseValidationError, subprocess.SubprocessError, ValueError) as error:
            if temporary.exists() or temporary.is_symlink():
                _remove_private_tree(temporary, update_staging)
            raise BusinessPackageStageError(
                "BUSINESS_PACKAGE_INVALID",
                "business package validation or offline environment preparation failed",
            ) from error
        return self._result(
            update_uid,
            release_id,
            version_name,
            release_sequence,
            expected_package_sha256,
            expected_package_size,
            signing_key_id,
            manifest,
            final,
        )

    def cleanup(self, update_uid: str) -> None:
        _require_uuid4(update_uid, "updateUid")
        target = self.staging_root / update_uid
        if target.exists() or target.is_symlink():
            _remove_private_tree(target, self.staging_root)

    def _require_capacity(self, package_size: int, database_size: int) -> None:
        required = package_size * 2 + database_size + self.free_space_reserve_bytes
        try:
            free = shutil.disk_usage(self.staging_root).free
        except OSError as error:
            raise BusinessPackageStageError(
                "DEVICE_STORAGE_UNKNOWN",
                "available business update storage could not be confirmed",
            ) from error
        if free < required:
            raise BusinessPackageStageError(
                "DEVICE_STORAGE_INSUFFICIENT",
                "device storage cannot safely hold the candidate and database snapshot",
            )

    @staticmethod
    def _result(
        update_uid: str,
        release_id: str,
        version_name: str,
        release_sequence: int,
        package_sha256: str,
        package_size: int,
        signing_key_id: str,
        manifest: Mapping[str, str],
        final: Path,
    ) -> StagedBusinessRelease:
        return StagedBusinessRelease(
            update_uid=update_uid,
            release_id=release_id,
            version_name=version_name,
            release_sequence=release_sequence,
            package_sha256=package_sha256,
            package_size=package_size,
            signing_key_id=signing_key_id,
            manifest=dict(manifest),
            staged_path=final,
        )


def _prepare_offline_environment(release: Path) -> None:
    environment = release / ".venv"
    subprocess.run(
        ["/usr/bin/python3", "-m", "venv", "--copies", str(environment)],
        check=True,
        timeout=300,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # CPython commonly creates ``lib64 -> lib`` even when ``venv --copies``
    # is requested.  The installed release deliberately permits no links, so
    # remove only that known redundant alias and reject every other link in
    # the final tree.
    lib64 = environment / "lib64"
    if lib64.is_symlink():
        if os.readlink(lib64) != "lib":
            raise ValueError("business environment has an unexpected lib64 alias")
        lib64.unlink()
        _fsync_directory(environment)
    python = environment / "bin" / "python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--require-hashes",
            "--find-links",
            str(release / "wheelhouse"),
            "--requirement",
            str(release / "requirements-offline.txt"),
        ],
        check=True,
        timeout=1200,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [str(python), "-m", "pip", "check"],
        check=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _write_environment_marker(release: Path, package_sha256: str) -> None:
    marker = release / ENVIRONMENT_MARKER
    payload = (
        json.dumps(
            {
                "schemaVersion": 1,
                "packageFormatVersion": BUSINESS_RELEASE_FORMAT_VERSION,
                "packageSha256": package_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        marker,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("business environment marker write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_environment_marker(release: Path, package_sha256: str) -> None:
    marker = _require_regular_file(
        release / ENVIRONMENT_MARKER,
        "business environment marker",
    )
    if marker.st_size > 4096:
        raise ValueError("business environment marker is too large")
    document = json.loads((release / ENVIRONMENT_MARKER).read_text(encoding="utf-8"))
    if document != {
        "schemaVersion": 1,
        "packageFormatVersion": BUSINESS_RELEASE_FORMAT_VERSION,
        "packageSha256": package_sha256,
    }:
        raise ValueError("business environment marker differs")
    python = release / ".venv/bin/python"
    _require_regular_file(python, "business environment Python")


def _reject_links_and_special_files(root: Path) -> None:
    for current_text, directories, files in os.walk(root, followlinks=False):
        current = Path(current_text)
        for name in (*directories, *files):
            details = (current / name).lstat()
            if not (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode)):
                raise BusinessPackageStageError(
                    "BUSINESS_ENVIRONMENT_UNSAFE",
                    "prepared business environment contains a link or special file",
                )
            if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
                raise BusinessPackageStageError(
                    "BUSINESS_ENVIRONMENT_UNSAFE",
                    "prepared business environment contains a hard-linked file",
                )


def _require_private_directory(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise BusinessPackageStageError(
            "BUSINESS_PACKAGE_NOT_READY", f"{label} does not exist"
        ) from error
    if path.is_symlink() or not stat.S_ISDIR(details.st_mode):
        raise BusinessPackageStageError(
            "BUSINESS_PACKAGE_PATH_UNSAFE", f"{label} is not a real directory"
        )
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
        raise BusinessPackageStageError(
            "BUSINESS_PACKAGE_PATH_UNSAFE", f"{label} permissions are too broad"
        )
    return details


def _create_or_require_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        _require_private_directory(path, "business staging update directory")
    os.chmod(path, 0o700)


def _require_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise BusinessPackageStageError(
            "BUSINESS_PACKAGE_NOT_READY", f"{label} does not exist"
        ) from error
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise BusinessPackageStageError(
            "BUSINESS_PACKAGE_PATH_UNSAFE", f"{label} is not a single regular file"
        )
    return details


def _remove_private_tree(path: Path, parent: Path) -> None:
    try:
        if path.parent.resolve(strict=True) != parent.resolve(strict=True):
            raise BusinessPackageStageError(
                "BUSINESS_PACKAGE_PATH_UNSAFE",
                "business staging cleanup target escaped its fixed parent",
            )
    except OSError as error:
        raise BusinessPackageStageError(
            "BUSINESS_PACKAGE_PATH_UNSAFE",
            "business staging cleanup parent cannot be confirmed",
        ) from error
    if path.is_symlink() or not path.is_dir():
        raise BusinessPackageStageError(
            "BUSINESS_PACKAGE_PATH_UNSAFE",
            "business staging cleanup target is not a real directory",
        )
    _reject_links_and_special_files(path)
    shutil.rmtree(path)
    _fsync_directory(parent)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise BusinessPackageStageError("REQUEST_INVALID", f"{field} must be UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise BusinessPackageStageError(
            "REQUEST_INVALID", f"{field} must be UUIDv4"
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise BusinessPackageStageError("REQUEST_INVALID", f"{field} must be UUIDv4")
    return value


__all__ = [
    "BusinessPackageStageError",
    "BusinessReleasePackageStager",
    "StagedBusinessRelease",
]
