"""Fail-closed primitives for EcoBin hardware runtime releases."""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import re
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

if __package__:
    from .runtime_payload_manifest import EDGE_SCHEMA_VERSION, RUNTIME_APP_FILES
else:  # pragma: no cover - direct execution/import from the install directory
    from runtime_payload_manifest import EDGE_SCHEMA_VERSION, RUNTIME_APP_FILES


RELEASE_FORMAT_VERSION = "1"
ARTIFACT_KIND = "hardware-runtime"
PYTHON_SERIES = "3.11"
RELEASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIGNING_KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_ARCHIVE_MEMBERS = 20_000
MAX_ARCHIVE_BYTES = 1_500_000_000
MAX_LEGACY_CRYPTOGRAPHY_MESSAGE_BYTES = 512 * 1024 * 1024
MAX_PUBLIC_KEY_BYTES = 16_384
MAX_PRIVATE_KEY_BYTES = 16_384
ED25519_SIGNATURE_BYTES = 64
INSTALL_MARKER_VERSION = 1
INSTALL_COMPLETE_MARKER = ".ecobin-install-complete"
ACTIVATION_JOURNAL_VERSION = 1
ACTIVATION_JOURNAL_NAME = ".ecobin-pending-activation.json"
INSTALL_LOCK_NAME = ".ecobin-runtime-install.lock"
EXPECTED_VENV_SYMLINKS = frozenset(
    {
        "bin/python",
        "bin/python3",
        "bin/python3.11",
        "lib64",
    }
)
PYTHON_VENV_SYMLINKS = frozenset(
    {"bin/python", "bin/python3", "bin/python3.11"}
)

FORBIDDEN_RUNTIME_NAMES = frozenset(
    {
        "device_enrollment.py",
        "enrollment_bootstrap.py",
        "maintenance_ssh_setup.py",
        "remote_support_agent.py",
        "remote_support.py",
        "remote_support_credentials.py",
        "remote_support_store.py",
        "secure_files.py",
    }
)

PACKAGE_TOP_LEVEL = frozenset(
    {
        "app",
        "wheelhouse",
        "manifest.env",
        "release.env",
        "requirements-runtime.txt",
        "requirements-offline.txt",
        "SHA256SUMS",
    }
)

MANIFEST_KEYS = frozenset(
    {
        "ECOBIN_RELEASE_FORMAT_VERSION",
        "ECOBIN_ARTIFACT_KIND",
        "ECOBIN_RELEASE_ID",
        "ECOBIN_GIT_COMMIT",
        "ECOBIN_PYTHON_SERIES",
        "ECOBIN_EDGE_SCHEMA_VERSION",
        "ECOBIN_SOURCE_DATE_EPOCH",
        "ECOBIN_RUNTIME_ALLOWLIST_SHA256",
    }
)


class ReleaseValidationError(ValueError):
    """The release is malformed, incomplete, or does not match its digest."""


def validate_release_id(value: str) -> str:
    if not RELEASE_ID_PATTERN.fullmatch(value):
        raise ReleaseValidationError("invalid release ID")
    return value


def validate_signing_key_id(value: str) -> str:
    if not SIGNING_KEY_ID_PATTERN.fullmatch(value):
        raise ReleaseValidationError("invalid signing key ID")
    return value


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _open_secure_regular_file(
    path: str | os.PathLike[str],
    *,
    maximum_bytes: int,
    exact_bytes: int | None = None,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    reject_wide_permissions: bool = False,
    require_private_permissions: bool = False,
) -> Iterator[BinaryIO]:
    candidate = Path(path)
    try:
        before = candidate.lstat()
    except OSError:
        raise ReleaseValidationError("cannot open required security file") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseValidationError("security file must be a regular no-follow file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        raise ReleaseValidationError("cannot open required security file") from None
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or not os.path.samestat(before, details)
        ):
            raise ReleaseValidationError(
                "security file must be a regular single-link file"
            )
        if details.st_size <= 0 or details.st_size > maximum_bytes:
            raise ReleaseValidationError("security file size is invalid")
        if exact_bytes is not None and details.st_size != exact_bytes:
            raise ReleaseValidationError("security file size is invalid")
        if expected_uid is not None and details.st_uid != expected_uid:
            raise ReleaseValidationError("security file owner is invalid")
        if expected_gid is not None and details.st_gid != expected_gid:
            raise ReleaseValidationError("security file group is invalid")
        if os.name == "posix":
            permissions = stat.S_IMODE(details.st_mode)
            if require_private_permissions and permissions & 0o077:
                raise ReleaseValidationError("security file permissions are unsafe")
            if reject_wide_permissions and permissions & 0o022:
                raise ReleaseValidationError("security file permissions are unsafe")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            yield stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_security_file(
    path: str | os.PathLike[str],
    *,
    maximum_bytes: int,
    exact_bytes: int | None = None,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    reject_wide_permissions: bool = False,
    require_private_permissions: bool = False,
) -> bytes:
    with _open_secure_regular_file(
        path,
        maximum_bytes=maximum_bytes,
        exact_bytes=exact_bytes,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        reject_wide_permissions=reject_wide_permissions,
        require_private_permissions=require_private_permissions,
    ) as stream:
        return stream.read(maximum_bytes + 1)


def _validate_trusted_public_key_directory(
    directory: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    try:
        details = directory.lstat()
    except OSError:
        raise ReleaseValidationError(
            "trusted public key directory is unavailable"
        ) from None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise ReleaseValidationError(
            "trusted public key directory must be a no-follow directory"
        )
    if details.st_uid != expected_uid or details.st_gid != expected_gid:
        raise ReleaseValidationError("trusted public key directory owner is invalid")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o022:
        raise ReleaseValidationError(
            "trusted public key directory permissions are unsafe"
        )


@contextmanager
def verified_archive_stream(
    archive_path: str | os.PathLike[str],
    *,
    expected_sha256: str,
    signature_path: str | os.PathLike[str],
    signing_key_id: str,
    trusted_public_keys_directory: str | os.PathLike[str],
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> Iterator[BinaryIO]:
    """Yield a private snapshot cryptographically bound to the verified input."""

    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise ReleaseValidationError("expected archive SHA-256 is invalid")
    with _open_secure_regular_file(
        archive_path,
        maximum_bytes=MAX_ARCHIVE_BYTES,
    ) as archive:
        if _sha256_stream(archive) != expected_sha256:
            raise ReleaseValidationError("archive SHA-256 mismatch")

        # No signature or trust-store path is touched until the digest supplied
        # by the trusted release metadata has matched.
        key_id = validate_signing_key_id(signing_key_id)
        signature = _read_security_file(
            signature_path,
            maximum_bytes=ED25519_SIGNATURE_BYTES,
            exact_bytes=ED25519_SIGNATURE_BYTES,
        )
        trust_directory = Path(trusted_public_keys_directory)
        _validate_trusted_public_key_directory(
            trust_directory,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        public_key_pem = _read_security_file(
            trust_directory / f"{key_id}.pem",
            maximum_bytes=MAX_PUBLIC_KEY_BYTES,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            reject_wide_permissions=True,
        )
        if not public_key_pem.startswith(b"-----BEGIN PUBLIC KEY-----"):
            raise ReleaseValidationError("trusted signing key is not Ed25519 PEM")
        try:
            public_key = serialization.load_pem_public_key(public_key_pem)
        except (TypeError, ValueError):
            raise ReleaseValidationError(
                "trusted signing key is not Ed25519 PEM"
            ) from None
        if not isinstance(public_key, Ed25519PublicKey):
            raise ReleaseValidationError("trusted signing key is not Ed25519")

        archive.seek(0)
        try:
            with mmap.mmap(
                archive.fileno(),
                0,
                access=mmap.ACCESS_READ,
            ) as archive_bytes:
                try:
                    public_key.verify(signature, archive_bytes)
                except TypeError:
                    if len(archive_bytes) > MAX_LEGACY_CRYPTOGRAPHY_MESSAGE_BYTES:
                        raise ReleaseValidationError(
                            "archive is too large for the installed legacy "
                            "cryptography backend"
                        ) from None
                    try:
                        legacy_message = archive_bytes[:]
                    except (MemoryError, OSError, ValueError):
                        raise ReleaseValidationError(
                            "archive signature cannot be verified"
                        ) from None
                    try:
                        public_key.verify(signature, legacy_message)
                    except TypeError:
                        raise ReleaseValidationError(
                            "archive signature cannot be verified"
                        ) from None
        except InvalidSignature:
            raise ReleaseValidationError("archive signature is invalid") from None
        except (OSError, ValueError):
            raise ReleaseValidationError(
                "archive signature cannot be verified"
            ) from None
        # Copy only after the source has passed both checks.  Rehashing the
        # private temporary snapshot detects an in-place source mutation during
        # copying; extraction never reads the externally mutable pathname.
        archive.seek(0)
        with tempfile.TemporaryFile(mode="w+b") as snapshot:
            shutil.copyfileobj(archive, snapshot, length=1024 * 1024)
            snapshot.flush()
            os.fsync(snapshot.fileno())
            if _sha256_stream(snapshot) != expected_sha256:
                raise ReleaseValidationError(
                    "archive changed while creating its verified snapshot"
                )
            snapshot.seek(0)
            yield snapshot


def runtime_allowlist_sha256() -> str:
    payload = "".join(f"{name}\n" for name in RUNTIME_APP_FILES).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ReleaseValidationError("release path is empty or non-POSIX")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseValidationError(f"unsafe release path: {value}")
    if path.as_posix() != value:
        raise ReleaseValidationError(f"non-canonical release path: {value}")
    return path


def _regular_package_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for current_text, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_text)
        relative_current = current.relative_to(root)
        if relative_current.parts and relative_current.parts[0] == ".venv":
            directory_names[:] = []
            continue

        kept_directories: list[str] = []
        for name in sorted(directory_names):
            path = current / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ReleaseValidationError(
                    f"release contains a linked or special directory: {path}"
                )
            if relative_current == Path() and name == ".venv":
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            path = current / name
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise ReleaseValidationError(
                    f"release contains a link or special file: {path}"
                )
            relative = path.relative_to(root).as_posix()
            _safe_relative_path(relative)
            files[relative] = path
    return files


def write_sha256sums(root: str | os.PathLike[str]) -> Path:
    release_root = Path(root)
    checksum_path = release_root / "SHA256SUMS"
    files = _regular_package_files(release_root)
    files.pop("SHA256SUMS", None)
    content = "".join(
        f"{sha256_file(path)}  {relative}\n"
        for relative, path in sorted(files.items())
    )
    checksum_path.write_text(content, encoding="utf-8", newline="\n")
    return checksum_path


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseValidationError(f"cannot read release metadata: {path}") from error
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ReleaseValidationError(f"invalid metadata line in {path.name}")
        key, value = line.split("=", 1)
        if not key or key in values or not value or any(
            character in value for character in "\x00\r\n"
        ):
            raise ReleaseValidationError(f"invalid metadata value in {path.name}")
        values[key] = value
    return values


def _parse_sha256sums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseValidationError("cannot read SHA256SUMS") from error
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ReleaseValidationError("invalid SHA256SUMS line")
        digest, relative = match.groups()
        _safe_relative_path(relative)
        if relative in checksums or relative == "SHA256SUMS":
            raise ReleaseValidationError("duplicate or recursive SHA256SUMS entry")
        checksums[relative] = digest
    if not checksums:
        raise ReleaseValidationError("SHA256SUMS is empty")
    return checksums


def validate_release_tree(
    root: str | os.PathLike[str],
    *,
    expected_release_id: str | None = None,
) -> dict[str, str]:
    release_root = Path(root)
    if not release_root.is_dir() or release_root.is_symlink():
        raise ReleaseValidationError("release root must be a regular directory")

    files = _regular_package_files(release_root)
    top_level = {PurePosixPath(name).parts[0] for name in files}
    top_level.update(
        entry.name
        for entry in release_root.iterdir()
        if entry.is_dir() and entry.name != ".venv"
    )
    if top_level != PACKAGE_TOP_LEVEL:
        raise ReleaseValidationError("release top-level members do not match format")

    checksums = _parse_sha256sums(release_root / "SHA256SUMS")
    actual_files = set(files) - {"SHA256SUMS"}
    if set(checksums) != actual_files:
        raise ReleaseValidationError(
            "SHA256SUMS must cover every package file exactly once"
        )
    for relative, expected_digest in checksums.items():
        if sha256_file(files[relative]) != expected_digest:
            raise ReleaseValidationError(f"checksum mismatch: {relative}")

    manifest = _parse_env_file(release_root / "manifest.env")
    if set(manifest) != MANIFEST_KEYS:
        raise ReleaseValidationError("manifest keys do not match release format")
    if manifest["ECOBIN_RELEASE_FORMAT_VERSION"] != RELEASE_FORMAT_VERSION:
        raise ReleaseValidationError("unsupported release format")
    if manifest["ECOBIN_ARTIFACT_KIND"] != ARTIFACT_KIND:
        raise ReleaseValidationError("unexpected artifact kind")
    release_id = validate_release_id(manifest["ECOBIN_RELEASE_ID"])
    if expected_release_id is not None and release_id != expected_release_id:
        raise ReleaseValidationError("release ID differs from expected value")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["ECOBIN_GIT_COMMIT"]):
        raise ReleaseValidationError("invalid release Git commit")
    if manifest["ECOBIN_PYTHON_SERIES"] != PYTHON_SERIES:
        raise ReleaseValidationError("runtime must target Python 3.11")
    if not re.fullmatch(r"[1-9][0-9]{0,8}", manifest["ECOBIN_EDGE_SCHEMA_VERSION"]):
        raise ReleaseValidationError("runtime has an invalid edge schema version")
    if not manifest["ECOBIN_SOURCE_DATE_EPOCH"].isdigit():
        raise ReleaseValidationError("invalid source date epoch")
    if (
        manifest["ECOBIN_RUNTIME_ALLOWLIST_SHA256"]
        != runtime_allowlist_sha256()
    ):
        raise ReleaseValidationError("runtime source allowlist differs")

    release_environment = _parse_env_file(release_root / "release.env")
    if release_environment != {"ECOBIN_EDGE_VERSION": release_id}:
        raise ReleaseValidationError("release.env must contain only the release ID")

    app_directory = release_root / "app"
    app_files = {
        relative.removeprefix("app/")
        for relative in files
        if relative.startswith("app/")
    }
    if app_files != set(RUNTIME_APP_FILES):
        raise ReleaseValidationError("runtime app does not match its exact allowlist")
    expected_app_directories = {
        parent.as_posix()
        for name in RUNTIME_APP_FILES
        for parent in PurePosixPath(name).parents
        if parent != PurePosixPath(".")
    }
    actual_app_directories = {
        path.relative_to(app_directory).as_posix()
        for path in app_directory.rglob("*")
        if path.is_dir()
    }
    if actual_app_directories != expected_app_directories:
        raise ReleaseValidationError("runtime app contains an unexpected directory")
    if FORBIDDEN_RUNTIME_NAMES.intersection(
        PurePosixPath(name).name for name in app_files
    ):
        raise ReleaseValidationError("runtime app contains enrollment/agent code")

    wheelhouse = release_root / "wheelhouse"
    wheels = list(wheelhouse.iterdir())
    if not wheels or any(
        not wheel.is_file() or wheel.is_symlink() or wheel.suffix != ".whl"
        for wheel in wheels
    ):
        raise ReleaseValidationError("wheelhouse must contain only wheel files")
    for requirements_name in (
        "requirements-runtime.txt",
        "requirements-offline.txt",
    ):
        requirements = release_root / requirements_name
        if not requirements.is_file() or requirements.stat().st_size == 0:
            raise ReleaseValidationError(f"{requirements_name} is missing or empty")
    return manifest


def _archive_member_path(name: str) -> PurePosixPath:
    normalized = name[:-1] if name.endswith("/") else name
    return _safe_relative_path(normalized)


def safe_extract_archive(
    archive_path: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    expected_sha256: str,
) -> str:
    with _open_secure_regular_file(
        archive_path,
        maximum_bytes=MAX_ARCHIVE_BYTES,
    ) as archive:
        return safe_extract_archive_stream(
            archive,
            destination,
            expected_sha256=expected_sha256,
        )


def safe_extract_archive_stream(
    archive: BinaryIO,
    destination: str | os.PathLike[str],
    *,
    expected_sha256: str,
) -> str:
    target = Path(destination)
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise ReleaseValidationError("expected archive SHA-256 is invalid")
    if _sha256_stream(archive) != expected_sha256:
        raise ReleaseValidationError("archive SHA-256 mismatch")
    if target.exists():
        if target.is_symlink() or not target.is_dir() or any(target.iterdir()):
            raise ReleaseValidationError(
                "archive destination must be an empty regular directory"
            )
    else:
        target.mkdir(parents=True, exist_ok=False)

    archive.seek(0)
    with tarfile.open(fileobj=archive, mode="r:*") as stream:
        members = stream.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise ReleaseValidationError("archive member count is invalid")
        total_size = sum(member.size for member in members if member.isfile())
        if total_size > MAX_ARCHIVE_BYTES:
            raise ReleaseValidationError("archive expands beyond the size limit")

        names: set[str] = set()
        top_levels: set[str] = set()
        parsed_members: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        for member in members:
            path = _archive_member_path(member.name)
            canonical = path.as_posix()
            if canonical in names:
                raise ReleaseValidationError("archive contains a duplicate member")
            names.add(canonical)
            top_levels.add(path.parts[0])
            if not member.isdir() and not member.isfile():
                raise ReleaseValidationError(
                    "archive contains a link or special file"
                )
            parsed_members.append((member, path))

        if len(top_levels) != 1:
            raise ReleaseValidationError("archive must have one top-level directory")
        top_level = next(iter(top_levels))
        prefix = "ecobin-hardware-"
        if not top_level.startswith(prefix):
            raise ReleaseValidationError("archive top-level directory is invalid")
        archive_release_id = validate_release_id(top_level[len(prefix) :])

        for member, path in sorted(
            parsed_members,
            key=lambda item: (len(item[1].parts), item[1].as_posix()),
        ):
            relative_parts = path.parts[1:]
            if not relative_parts:
                if not member.isdir():
                    raise ReleaseValidationError(
                        "archive top-level member must be a directory"
                    )
                continue
            output = target.joinpath(*relative_parts)
            if member.isdir():
                output.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            source = stream.extractfile(member)
            if source is None:
                raise ReleaseValidationError("cannot read archive member")
            descriptor = os.open(
                output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with source, os.fdopen(descriptor, "wb") as destination_stream:
                shutil.copyfileobj(source, destination_stream, length=1024 * 1024)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            os.chmod(output, 0o644)
    return archive_release_id


def _release_from_current(current: Path, releases: Path) -> str | None:
    if not os.path.lexists(current):
        return None
    if not current.is_symlink():
        raise ReleaseValidationError("current must be a symbolic link")
    resolved_releases = releases.resolve(strict=True)
    resolved = current.resolve(strict=True)
    if resolved.parent != resolved_releases or not resolved.is_dir():
        raise ReleaseValidationError("current points outside the release store")
    return resolved.name


def installed_current_release(
    current_link: str | os.PathLike[str],
    releases_directory: str | os.PathLike[str],
) -> str | None:
    return _release_from_current(Path(current_link), Path(releases_directory))


def _atomic_set_current(current: Path, releases: Path, release_id: str) -> None:
    validate_release_id(release_id)
    target = releases / release_id
    if not target.is_dir() or target.is_symlink():
        raise ReleaseValidationError("activation target is not an installed release")
    current.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = current.with_name(f".{current.name}.next-{os.getpid()}")
    try:
        if os.path.lexists(temporary):
            temporary.unlink()
        relative_target = os.path.relpath(target, current.parent)
        os.symlink(relative_target, temporary, target_is_directory=True)
        os.replace(temporary, current)
        _fsync_directory(current.parent)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def activate_with_rollback(
    *,
    releases_directory: str | os.PathLike[str],
    current_link: str | os.PathLike[str],
    release_id: str,
    health_check: Callable[[str], None],
    rollback_health_check: Callable[[str | None], None] | None = None,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> str | None:
    releases = Path(releases_directory)
    current = Path(current_link)
    previous = _release_from_current(current, releases)
    _require_schema_compatible(releases, previous, release_id)
    _write_activation_journal(
        current,
        previous=previous,
        target=release_id,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    try:
        _atomic_set_current(current, releases, release_id)
        health_check(release_id)
    except Exception as activation_error:
        try:
            _restore_previous_current(current, releases, previous)
            if rollback_health_check is None:
                raise RuntimeError("rollback health verification is required")
            rollback_health_check(previous)
        except Exception as rollback_error:
            # Keep the journal.  A later boot recovery must not mistake the
            # unverified target for a safe current release.
            raise RuntimeError(
                "new release failed and previous release did not recover"
            ) from rollback_error
        _clear_activation_journal(
            current,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        raise RuntimeError(
            f"release {release_id} failed health verification; current rolled back"
        ) from activation_error
    _clear_activation_journal(
        current,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    return previous


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_safe_owner_and_mode(
    mode: int,
    uid: int,
    gid: int,
    *,
    expected_uid: int | None,
    expected_gid: int | None,
    private: bool = False,
) -> None:
    forbidden_permissions = 0o077 if private else (stat.S_IWGRP | stat.S_IWOTH)
    if stat.S_IMODE(mode) & forbidden_permissions:
        raise ReleaseValidationError("installed runtime has unsafe permissions")
    if expected_uid is not None and uid != expected_uid:
        raise ReleaseValidationError("installed runtime is not owned by root")
    if expected_gid is not None and gid != expected_gid:
        raise ReleaseValidationError("installed runtime is not grouped by root")


def _harden_regular_venv_entry(
    path: Path,
    details: os.stat_result,
    *,
    expected_uid: int | None,
    expected_gid: int | None,
) -> None:
    is_directory = stat.S_ISDIR(details.st_mode)
    is_regular = stat.S_ISREG(details.st_mode)
    if not is_directory and not is_regular:
        raise ReleaseValidationError(
            "installed venv contains a special filesystem entry"
        )
    if is_regular and details.st_nlink != 1:
        raise ReleaseValidationError("installed venv contains a hard-linked file")
    if expected_uid is not None and details.st_uid != expected_uid:
        raise ReleaseValidationError("installed runtime is not owned by root")
    if expected_gid is not None and details.st_gid != expected_gid:
        raise ReleaseValidationError("installed runtime is not grouped by root")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if is_directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseValidationError(
            "cannot securely open installed venv entry"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not os.path.samestat(details, opened)
            or stat.S_ISDIR(opened.st_mode) != is_directory
            or stat.S_ISREG(opened.st_mode) != is_regular
            or (is_regular and opened.st_nlink != 1)
        ):
            raise ReleaseValidationError(
                "installed venv changed while hardening permissions"
            )
        if expected_uid is not None and opened.st_uid != expected_uid:
            raise ReleaseValidationError("installed runtime is not owned by root")
        if expected_gid is not None and opened.st_gid != expected_gid:
            raise ReleaseValidationError("installed runtime is not grouped by root")
        safe_mode = stat.S_IMODE(opened.st_mode) & ~(
            stat.S_IWGRP | stat.S_IWOTH
        )
        os.fchmod(descriptor, safe_mode)
        hardened = os.fstat(descriptor)
        if not os.path.samestat(opened, hardened):
            raise ReleaseValidationError(
                "installed venv changed while hardening permissions"
            )
        _require_safe_owner_and_mode(
            hardened.st_mode,
            hardened.st_uid,
            hardened.st_gid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    except OSError as error:
        raise ReleaseValidationError(
            "cannot harden installed venv permissions"
        ) from error
    finally:
        os.close(descriptor)


def harden_installed_venv_permissions(
    release: str | os.PathLike[str],
    *,
    expected_uid: int | None = 0,
    expected_gid: int | None = 0,
) -> None:
    """Remove wide write bits without following or mutating venv links."""

    venv = Path(release) / ".venv"
    try:
        root_details = venv.lstat()
    except OSError as error:
        raise ReleaseValidationError("installed release has no regular venv") from error
    if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
        raise ReleaseValidationError("installed release has no regular venv")
    _harden_regular_venv_entry(
        venv,
        root_details,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )

    pending = [venv]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ReleaseValidationError("cannot inspect installed venv") from error
        for entry in entries:
            path = Path(entry.path)
            details = path.lstat()
            mode = details.st_mode
            if stat.S_ISLNK(mode):
                if details.st_nlink != 1:
                    raise ReleaseValidationError(
                        "installed venv contains a hard-linked symbolic link"
                    )
                if expected_uid is not None and details.st_uid != expected_uid:
                    raise ReleaseValidationError(
                        "installed venv link is not owned by root"
                    )
                if expected_gid is not None and details.st_gid != expected_gid:
                    raise ReleaseValidationError(
                        "installed venv link is not grouped by root"
                    )
                continue
            _harden_regular_venv_entry(
                path,
                details,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            if stat.S_ISDIR(mode):
                pending.append(path)


def _validate_private_parent(
    parent: Path,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    if parent.is_symlink() or not parent.is_dir():
        raise ReleaseValidationError("runtime state parent is not a directory")
    details = parent.stat()
    _require_safe_owner_and_mode(
        details.st_mode,
        details.st_uid,
        details.st_gid,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )


def audit_installed_venv(
    release: str | os.PathLike[str],
    *,
    trusted_python_targets: Iterable[str | os.PathLike[str]],
    expected_uid: int | None = 0,
    expected_gid: int | None = 0,
) -> None:
    """Recursively audit an installed venv without following its links.

    A Debian venv may contain only the Python launcher links created by
    ``venv`` and the conventional internal ``lib64 -> lib`` link.  Launcher
    links that leave the venv must resolve to one of the caller-provided,
    already trusted Python 3.11 executables.
    """

    release_root = Path(release)
    venv = release_root / ".venv"
    if venv.is_symlink() or not venv.is_dir():
        raise ReleaseValidationError("installed release has no regular venv")
    venv_resolved = venv.resolve(strict=True)

    trusted: set[Path] = set()
    for candidate_text in trusted_python_targets:
        candidate = Path(candidate_text).resolve(strict=True)
        details = candidate.stat()
        if not stat.S_ISREG(details.st_mode):
            raise ReleaseValidationError("trusted Python target is not regular")
        _require_safe_owner_and_mode(
            details.st_mode,
            details.st_uid,
            details.st_gid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        trusted.add(candidate)
    if not trusted:
        raise ReleaseValidationError("no trusted Python 3.11 target was supplied")

    root_details = venv.lstat()
    _require_safe_owner_and_mode(
        root_details.st_mode,
        root_details.st_uid,
        root_details.st_gid,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )

    pending = [venv]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ReleaseValidationError("cannot inspect installed venv") from error
        for entry in entries:
            path = Path(entry.path)
            details = path.lstat()
            relative = path.relative_to(venv).as_posix()
            mode = details.st_mode
            if stat.S_ISLNK(mode):
                if details.st_nlink != 1:
                    raise ReleaseValidationError(
                        "installed venv contains a hard-linked symbolic link"
                    )
                if relative not in EXPECTED_VENV_SYMLINKS:
                    raise ReleaseValidationError(
                        "installed venv contains an unexpected symbolic link"
                    )
                if expected_uid is not None and details.st_uid != expected_uid:
                    raise ReleaseValidationError(
                        "installed venv link is not owned by root"
                    )
                if expected_gid is not None and details.st_gid != expected_gid:
                    raise ReleaseValidationError(
                        "installed venv link is not grouped by root"
                    )
                try:
                    resolved = path.resolve(strict=True)
                except OSError as error:
                    raise ReleaseValidationError(
                        "installed venv contains a broken symbolic link"
                    ) from error
                if not resolved.is_relative_to(venv_resolved):
                    if relative not in PYTHON_VENV_SYMLINKS or resolved not in trusted:
                        raise ReleaseValidationError(
                            "installed venv link escapes its trusted Python boundary"
                        )
                continue

            _require_safe_owner_and_mode(
                mode,
                details.st_uid,
                details.st_gid,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                if details.st_nlink != 1:
                    raise ReleaseValidationError(
                        "installed venv contains a hard-linked file"
                    )
            else:
                raise ReleaseValidationError(
                    "installed venv contains a special filesystem entry"
                )


def fsync_release_tree(root: str | os.PathLike[str]) -> None:
    """Flush every regular file and directory before publishing a release."""

    release = Path(root)
    if release.is_symlink() or not release.is_dir():
        raise ReleaseValidationError("release root must be a regular directory")
    directories: list[Path] = []
    for current_text, directory_names, file_names in os.walk(
        release,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_text)
        directories.append(current)
        for name in directory_names:
            child = current / name
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            if not stat.S_ISDIR(mode):
                raise ReleaseValidationError("release contains a special directory")
        for name in file_names:
            child = current / name
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ReleaseValidationError("release contains a special file")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(child, flags)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _install_marker_content(release: Path, release_id: str) -> bytes:
    validate_release_id(release_id)
    digest = sha256_file(release / "SHA256SUMS")
    return (
        f"ECOBIN_INSTALL_MARKER_VERSION={INSTALL_MARKER_VERSION}\n"
        f"ECOBIN_RELEASE_ID={release_id}\n"
        f"ECOBIN_PACKAGE_INDEX_SHA256={digest}\n"
    ).encode("ascii")


def _atomic_write_private_file(
    path: Path,
    content: bytes,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    _validate_private_parent(
        path.parent,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_text)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            temporary.unlink()


def write_install_complete_marker(
    release: str | os.PathLike[str],
    release_id: str,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> Path:
    release_root = Path(release)
    marker = release_root / ".venv" / INSTALL_COMPLETE_MARKER
    _atomic_write_private_file(
        marker,
        _install_marker_content(release_root, release_id),
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    return marker


def validate_install_complete_marker(
    release: str | os.PathLike[str],
    release_id: str,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    release_root = Path(release)
    marker = release_root / ".venv" / INSTALL_COMPLETE_MARKER
    try:
        details = marker.lstat()
    except FileNotFoundError as error:
        raise ReleaseValidationError("installed release is incomplete") from error
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise ReleaseValidationError("install marker is not a regular private file")
    _require_safe_owner_and_mode(
        details.st_mode,
        details.st_uid,
        details.st_gid,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        private=True,
    )
    expected = _install_marker_content(release_root, release_id)
    try:
        actual = marker.read_bytes()
    except OSError as error:
        raise ReleaseValidationError("cannot read install marker") from error
    if actual != expected:
        raise ReleaseValidationError("installed release has an invalid marker")


def activation_journal_path(current_link: str | os.PathLike[str]) -> Path:
    return Path(current_link).parent / ACTIVATION_JOURNAL_NAME


def _write_activation_journal(
    current: Path,
    *,
    previous: str | None,
    target: str,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    if os.path.lexists(activation_journal_path(current)):
        raise ReleaseValidationError(
            "pending activation must be recovered before another switch"
        )
    if previous is not None:
        validate_release_id(previous)
    validate_release_id(target)
    content = json.dumps(
        {
            "previous": previous,
            "target": target,
            "version": ACTIVATION_JOURNAL_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii") + b"\n"
    _atomic_write_private_file(
        activation_journal_path(current),
        content,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )


def _read_activation_journal(
    current: Path,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> tuple[str | None, str] | None:
    path = activation_journal_path(current)
    if not os.path.lexists(path):
        return None
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise ReleaseValidationError("pending activation journal is not regular")
    _require_safe_owner_and_mode(
        details.st_mode,
        details.st_uid,
        details.st_gid,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        private=True,
    )
    if details.st_size > 512:
        raise ReleaseValidationError("pending activation journal is oversized")

    def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseValidationError(
                    "pending activation journal has duplicate fields"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="ascii"),
            object_pairs_hook=exact_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseValidationError("pending activation journal is invalid") from error
    if not isinstance(value, dict) or set(value) != {"previous", "target", "version"}:
        raise ReleaseValidationError("pending activation journal has invalid fields")
    if (
        type(value["version"]) is not int
        or value["version"] != ACTIVATION_JOURNAL_VERSION
    ):
        raise ReleaseValidationError("pending activation journal version is invalid")
    previous = value["previous"]
    target = value["target"]
    if previous is not None and not isinstance(previous, str):
        raise ReleaseValidationError("pending activation previous release is invalid")
    if not isinstance(target, str):
        raise ReleaseValidationError("pending activation target release is invalid")
    if previous is not None:
        validate_release_id(previous)
    validate_release_id(target)
    return previous, target


def _clear_activation_journal(
    current: Path,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    path = activation_journal_path(current)
    if os.path.lexists(path):
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ReleaseValidationError(
                "refusing to remove an unsafe activation journal"
            )
        _require_safe_owner_and_mode(
            details.st_mode,
            details.st_uid,
            details.st_gid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            private=True,
        )
        path.unlink()
        _fsync_directory(path.parent)


def _release_schema_version(releases: Path, release_id: str) -> str:
    manifest = validate_release_tree(
        releases / validate_release_id(release_id),
        expected_release_id=release_id,
    )
    return manifest["ECOBIN_EDGE_SCHEMA_VERSION"]


def _require_schema_compatible(
    releases: Path,
    previous: str | None,
    target: str,
) -> None:
    target_schema = _release_schema_version(releases, target)
    if previous is None:
        return
    previous_schema = _release_schema_version(releases, previous)
    if previous_schema != target_schema:
        raise ReleaseValidationError(
            "automatic activation across edge schema versions is forbidden"
        )


def _restore_previous_current(
    current: Path,
    releases: Path,
    previous: str | None,
) -> None:
    if previous is not None:
        _release_schema_version(releases, previous)
        _atomic_set_current(current, releases, previous)
        return
    if os.path.lexists(current):
        _release_from_current(current, releases)
        current.unlink()
        _fsync_directory(current.parent)


def recover_pending_activation(
    *,
    releases_directory: str | os.PathLike[str],
    current_link: str | os.PathLike[str],
    health_check: Callable[[str | None], None],
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> str | None:
    """Recover only the previous release recorded before an interrupted switch."""

    releases = Path(releases_directory)
    current = Path(current_link)
    pending = _read_activation_journal(
        current,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if pending is None:
        return _release_from_current(current, releases)
    previous, _target = pending
    _restore_previous_current(current, releases, previous)
    health_check(previous)
    _clear_activation_journal(
        current,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    return previous


@contextmanager
def nonblocking_install_lock(
    current_link: str | os.PathLike[str],
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> Iterator[None]:
    """Serialize staging, activation and recovery across all installer CLIs."""

    if os.name != "posix":
        raise RuntimeError("runtime installation locking requires POSIX")
    import fcntl

    current = Path(current_link)
    current.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    _validate_private_parent(
        current.parent,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    lock_path = current.parent / INSTALL_LOCK_NAME
    common_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    common_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            lock_path,
            common_flags | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        descriptor = os.open(lock_path, common_flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ReleaseValidationError("runtime install lock is not regular")
        _require_safe_owner_and_mode(
            details.st_mode,
            details.st_uid,
            details.st_gid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            private=True,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another runtime installation is active") from None
        yield
    finally:
        os.close(descriptor)


def create_incoming_directory(releases: Path, release_id: str) -> Path:
    releases.mkdir(mode=0o755, parents=True, exist_ok=True)
    resolved = releases.resolve(strict=True)
    name = tempfile.mkdtemp(prefix=f".incoming-{release_id}-", dir=resolved)
    return Path(name)


def remove_incoming_directory(path: Path, releases: Path) -> None:
    resolved_releases = releases.resolve(strict=True)
    resolved_path = path.resolve(strict=False)
    if (
        resolved_path.parent != resolved_releases
        or not resolved_path.name.startswith(".incoming-")
    ):
        raise ReleaseValidationError("refusing to remove an unexpected directory")
    if path.exists():
        shutil.rmtree(path)


def sorted_tree_paths(root: Path) -> Iterable[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.name != "SHA256SUMS"),
        key=lambda path: path.relative_to(root).as_posix(),
    )
