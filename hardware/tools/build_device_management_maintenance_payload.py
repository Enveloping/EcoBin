#!/usr/bin/env python3
"""Build an exact Stage-3 maintenance payload on a trusted workstation.

The resulting directory is intentionally transport-only.  The live-device
installer authenticates its canonical manifest by a SHA-256 value delivered
out of band before it changes the device.  This builder copies only the
permanent communication/updater allowlist and public runtime-release trust
keys; it never reads device credentials or signing private keys.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


HARDWARE_SOURCE_ROOT = Path(__file__).resolve().parent.parent
if str(HARDWARE_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(HARDWARE_SOURCE_ROOT))

from system.device_management_maintenance_installer import (  # noqa: E402
    COMMUNICATION_AGENT_FILES,
    DEVICE_UPDATER_FILES,
    DEVICE_UPDATER_HELPER_FILES,
    DROP_IN_CONTENT,
    DROP_IN_RELATIVE,
    GIT_COMMIT,
    HELPER_UNIT_FILES,
    MAIN_UNIT_FILES,
    MANIFEST_NAME,
    MAX_PAYLOAD_FILE_BYTES,
    MAX_TRUST_KEYS,
    PREFLIGHT_PAYLOAD,
    RELEASE_ENV_PAYLOAD,
    RUNTIME_KEY_NAME,
    SAFE_ID,
    SYSUSERS_PAYLOAD,
    TMPFILES_PAYLOAD,
    MaintenanceInstallError,
    MaintenanceManifest,
    _expected_fixed_payload_files,
    expected_payload_directories,
    load_and_validate_payload,
    write_payload_manifest,
)


class PayloadBuildError(RuntimeError):
    """A workstation input or output violates the payload build boundary."""


@dataclass(frozen=True)
class PayloadIdentity:
    payload_id: str
    source_git_commit: str
    expected_image_release_id: str
    expected_image_version: str
    communication_release_id: str
    updater_release_id: str


@dataclass(frozen=True)
class PayloadBuildResult:
    output: Path
    manifest_sha256: str
    manifest: MaintenanceManifest


@dataclass(frozen=True)
class _OutputReservation:
    path: Path
    existed: bool
    device: int | None
    inode: int | None


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_ordinary_directory(path: Path, description: str) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise PayloadBuildError(f"{description} is unavailable: {path}") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise PayloadBuildError(f"{description} must be an ordinary directory: {path}")
    return details


def _assert_directory_chain(path: Path, description: str) -> None:
    """Reject symlinked or non-directory components without resolving them."""

    absolute = _absolute_without_resolving(path)
    for component in reversed(absolute.parents):
        _assert_ordinary_directory(component, description)
    _assert_ordinary_directory(absolute, description)


def _reserve_output(output: Path) -> _OutputReservation:
    absolute = _absolute_without_resolving(output)
    if absolute.name in ("", ".", ".."):
        raise PayloadBuildError("output path is unsafe")
    _assert_directory_chain(absolute.parent, "output parent")
    if not _lexists(absolute):
        return _OutputReservation(absolute, False, None, None)

    details = _assert_ordinary_directory(absolute, "output")
    try:
        entries = list(os.scandir(absolute))
    except OSError as exc:
        raise PayloadBuildError(f"output cannot be enumerated: {absolute}") from exc
    if entries:
        raise PayloadBuildError("output must not exist or must be an empty directory")
    return _OutputReservation(
        absolute,
        True,
        getattr(details, "st_dev", None),
        getattr(details, "st_ino", None),
    )


def _validate_identity(identity: PayloadIdentity) -> None:
    for description, value in (
        ("payload ID", identity.payload_id),
        ("expected image release ID", identity.expected_image_release_id),
        ("expected image version", identity.expected_image_version),
        ("communication release ID", identity.communication_release_id),
        ("updater release ID", identity.updater_release_id),
    ):
        if not SAFE_ID.fullmatch(value):
            raise PayloadBuildError(f"{description} is invalid")
    if not GIT_COMMIT.fullmatch(identity.source_git_commit):
        raise PayloadBuildError(
            "source Git commit must be 40 lowercase hexadecimal digits"
        )


def _read_regular_file(
    source_root: Path, relative: str, *, description: str
) -> bytes:
    """Read a regular single-link file without accepting a symlink component."""

    root = _absolute_without_resolving(source_root)
    _assert_ordinary_directory(root, f"{description} root")
    current = root
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise PayloadBuildError(f"{description} path is unsafe: {relative}")
    for part in parts[:-1]:
        current = current / part
        _assert_ordinary_directory(current, f"{description} parent")
    source = current / parts[-1]
    try:
        before = source.lstat()
    except OSError as exc:
        raise PayloadBuildError(f"{description} is unavailable: {relative}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PayloadBuildError(f"{description} must be a regular file: {relative}")
    if os.name == "posix" and before.st_nlink != 1:
        raise PayloadBuildError(f"{description} must not be hard-linked: {relative}")
    if before.st_size > MAX_PAYLOAD_FILE_BYTES:
        raise PayloadBuildError(f"{description} is too large: {relative}")

    try:
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if os.name == "posix" and (
                opened.st_dev != before.st_dev or opened.st_ino != before.st_ino
            ):
                raise PayloadBuildError(
                    f"{description} changed while opening: {relative}"
                )
            content = stream.read(MAX_PAYLOAD_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise PayloadBuildError(f"{description} cannot be read: {relative}") from exc
    if len(content) > MAX_PAYLOAD_FILE_BYTES:
        raise PayloadBuildError(f"{description} is too large: {relative}")
    if (
        after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or (os.name == "posix" and after.st_nlink != 1)
    ):
        raise PayloadBuildError(f"{description} changed while reading: {relative}")
    return content


def _fixed_sources(hardware_root: Path) -> Mapping[str, bytes]:
    sources: dict[str, bytes] = {}

    def add(payload_path: str, source_relative: str) -> None:
        if payload_path in sources:
            raise PayloadBuildError(f"duplicate internal payload path: {payload_path}")
        sources[payload_path] = _read_regular_file(
            hardware_root,
            source_relative,
            description="repository source",
        )

    for name in COMMUNICATION_AGENT_FILES:
        add(f"communication/app/{name}", name)
    for name in DEVICE_UPDATER_FILES:
        add(f"updater/app/{name}", name)
    for name in DEVICE_UPDATER_HELPER_FILES:
        add(f"updater/helpers/{name}", f"device_management/helpers/{name}")
    for name in MAIN_UNIT_FILES:
        add(f"systemd/{name}", name)
    for name in HELPER_UNIT_FILES:
        add(
            f"systemd/{name}",
            f"device_management/helpers/systemd/{name}",
        )
    add(
        SYSUSERS_PAYLOAD,
        "device_management/config/sysusers.d/ecobin-device-runtime.conf",
    )
    add(
        TMPFILES_PAYLOAD,
        "device_management/config/tmpfiles.d/ecobin-device-runtime.conf",
    )
    add(PREFLIGHT_PAYLOAD, "system/business_runtime_preflight.py")
    sources[DROP_IN_RELATIVE] = DROP_IN_CONTENT
    return sources


def _trust_sources(runtime_trust_dir: Path) -> Mapping[str, bytes]:
    trust_root = _absolute_without_resolving(runtime_trust_dir)
    _assert_directory_chain(trust_root, "runtime trust directory")
    try:
        entries = sorted(os.scandir(trust_root), key=lambda item: item.name)
    except OSError as exc:
        raise PayloadBuildError("runtime trust directory cannot be enumerated") from exc
    if not 1 <= len(entries) <= MAX_TRUST_KEYS:
        raise PayloadBuildError(
            f"runtime trust directory must contain 1 to {MAX_TRUST_KEYS} public keys"
        )

    result: dict[str, bytes] = {}
    for entry in entries:
        if not RUNTIME_KEY_NAME.fullmatch(entry.name):
            raise PayloadBuildError(
                f"runtime trust directory contains an unexpected entry: {entry.name}"
            )
        relative = entry.name
        content = _read_regular_file(
            trust_root,
            relative,
            description="runtime public trust key",
        )
        result[f"trust/runtime-release-keys/{entry.name}"] = content
    return result


def _write_payload_file(root: Path, relative: str, content: bytes) -> None:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise PayloadBuildError(f"internal payload path is unsafe: {relative}")
    destination = root.joinpath(*parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(content)
            stream.flush()
            if os.name == "posix":
                os.fsync(stream.fileno())
    except OSError as exc:
        raise PayloadBuildError(f"cannot write payload file: {relative}") from exc
    os.chmod(destination, 0o644)


def _normalize_and_verify_modes(root: Path) -> None:
    directories, files = _walk_without_links(root)
    for directory in directories:
        os.chmod(directory, 0o755)
    for file in files:
        os.chmod(file, 0o644)
    if os.name == "posix":
        for directory in directories:
            if stat.S_IMODE(directory.lstat().st_mode) != 0o755:
                raise PayloadBuildError(
                    f"payload directory mode is not 0755: {directory}"
                )
        for file in files:
            if stat.S_IMODE(file.lstat().st_mode) != 0o644:
                raise PayloadBuildError(f"payload file mode is not 0644: {file}")


def _walk_without_links(root: Path) -> tuple[list[Path], list[Path]]:
    root_details = _assert_ordinary_directory(root, "payload staging root")
    del root_details
    directories = [root]
    files: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise PayloadBuildError(
                f"payload directory cannot be enumerated: {directory}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise PayloadBuildError(
                    f"payload entry cannot be inspected: {path}"
                ) from exc
            if stat.S_ISLNK(details.st_mode):
                raise PayloadBuildError(f"payload contains a symbolic link: {path}")
            if stat.S_ISDIR(details.st_mode):
                directories.append(path)
                stack.append(path)
            elif stat.S_ISREG(details.st_mode):
                if os.name == "posix" and details.st_nlink != 1:
                    raise PayloadBuildError(
                        f"payload contains a hard-linked file: {path}"
                    )
                files.append(path)
            else:
                raise PayloadBuildError(f"payload contains a special file: {path}")
    return sorted(directories), sorted(files)


def _remove_private_staging(staging: Path, expected_parent: Path) -> None:
    """Remove only the private sibling directory created by this process."""

    if (
        staging.parent != expected_parent
        or not staging.name.startswith(".ecobin-stage3-payload-")
        or not _lexists(staging)
    ):
        return
    shutil.rmtree(staging)


def _publish(staging: Path, reservation: _OutputReservation) -> None:
    output = reservation.path
    if reservation.existed:
        details = _assert_ordinary_directory(output, "reserved output")
        try:
            with os.scandir(output) as entries:
                output_is_empty = next(entries, None) is None
        except OSError as exc:
            raise PayloadBuildError("reserved output cannot be enumerated") from exc
        if (
            getattr(details, "st_dev", None) != reservation.device
            or getattr(details, "st_ino", None) != reservation.inode
            or not output_is_empty
        ):
            raise PayloadBuildError("reserved output changed during the build")
        output.rmdir()
    elif _lexists(output):
        raise PayloadBuildError("output appeared during the build")

    try:
        os.rename(staging, output)
    except OSError as exc:
        if reservation.existed and not _lexists(output):
            output.mkdir(mode=0o755)
        raise PayloadBuildError("cannot publish the completed payload") from exc


def build_device_management_maintenance_payload(
    output: Path,
    runtime_trust_dir: Path,
    identity: PayloadIdentity,
    *,
    hardware_source_root: Path = HARDWARE_SOURCE_ROOT,
) -> PayloadBuildResult:
    """Create, authenticate, validate, and atomically publish one payload."""

    _validate_identity(identity)
    reservation = _reserve_output(output)
    hardware_root = _absolute_without_resolving(hardware_source_root)
    _assert_directory_chain(hardware_root, "hardware source root")
    trust_root = _absolute_without_resolving(runtime_trust_dir)
    try:
        trust_root.relative_to(reservation.path)
    except ValueError:
        pass
    else:
        raise PayloadBuildError("runtime trust directory must not be inside output")
    try:
        reservation.path.relative_to(trust_root)
    except ValueError:
        pass
    else:
        raise PayloadBuildError("output must not be inside the runtime trust directory")

    files = dict(_fixed_sources(hardware_root))
    files[RELEASE_ENV_PAYLOAD] = (
        f"ECOBIN_COMMUNICATION_AGENT_VERSION={identity.communication_release_id}\n"
        f"ECOBIN_DEVICE_UPDATER_VERSION={identity.updater_release_id}\n"
    ).encode("ascii")
    files.update(_trust_sources(trust_root))
    fixed_actual = {
        relative
        for relative in files
        if not relative.startswith("trust/runtime-release-keys/")
    }
    if fixed_actual != _expected_fixed_payload_files():
        raise PayloadBuildError(
            "builder fixed allowlist differs from installer allowlist"
        )

    staging = Path(
        tempfile.mkdtemp(
            prefix=".ecobin-stage3-payload-",
            dir=reservation.path.parent,
        )
    )
    published = False
    try:
        for relative in sorted(files):
            _write_payload_file(staging, relative, files[relative])
        _normalize_and_verify_modes(staging)
        try:
            digest = write_payload_manifest(
                staging,
                payload_id=identity.payload_id,
                source_git_commit=identity.source_git_commit,
                expected_image_release_id=identity.expected_image_release_id,
                expected_image_version=identity.expected_image_version,
                communication_release_id=identity.communication_release_id,
                updater_release_id=identity.updater_release_id,
            )
            _normalize_and_verify_modes(staging)
            manifest = load_and_validate_payload(staging, digest)
        except MaintenanceInstallError as exc:
            raise PayloadBuildError(
                f"payload validator rejected the build: {exc}"
            ) from exc

        expected_directories = expected_payload_directories(
            {*manifest.files, MANIFEST_NAME}
        )
        actual_directories = {
            path.relative_to(staging).as_posix()
            for path in _walk_without_links(staging)[0]
            if path != staging
        }
        if actual_directories != expected_directories:
            raise PayloadBuildError("payload directory inventory is not exact")

        _publish(staging, reservation)
        published = True
        _normalize_and_verify_modes(reservation.path)
        try:
            final_manifest = load_and_validate_payload(reservation.path, digest)
        except MaintenanceInstallError as exc:
            raise PayloadBuildError(
                f"published payload failed final validation: {exc}"
            ) from exc
        return PayloadBuildResult(reservation.path, digest, final_manifest)
    finally:
        if not published:
            _remove_private_staging(staging, reservation.path.parent)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-trust-dir", required=True, type=Path)
    parser.add_argument("--payload-id", required=True)
    parser.add_argument("--communication-release-id", required=True)
    parser.add_argument("--updater-release-id", required=True)
    parser.add_argument("--source-git-commit", required=True)
    parser.add_argument("--expected-image-release-id", required=True)
    parser.add_argument("--expected-image-version", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        result = build_device_management_maintenance_payload(
            arguments.output,
            arguments.runtime_trust_dir,
            PayloadIdentity(
                payload_id=arguments.payload_id,
                source_git_commit=arguments.source_git_commit,
                expected_image_release_id=arguments.expected_image_release_id,
                expected_image_version=arguments.expected_image_version,
                communication_release_id=arguments.communication_release_id,
                updater_release_id=arguments.updater_release_id,
            ),
        )
        print(f"manifest-sha256={result.manifest_sha256}")
        return 0
    except PayloadBuildError as exc:
        print(f"device-management-payload-build=FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
