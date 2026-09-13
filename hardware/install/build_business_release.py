#!/usr/bin/env python3
"""Build one deterministic, signed Linux ARM64 business-runtime package."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    from .build_runtime_release import (
        _build_wheelhouse,
        _fsync_directory,
        _fsync_file,
        _load_signing_private_key,
        _require_arm64_python311_builder,
        _run,
        _sign_archive,
        _write_new_fsynced_file,
        _write_offline_requirements,
    )
    from .business_release import (
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
        validate_business_release_id,
        validate_business_release_tree,
        validate_release_sequence,
        validate_version_name,
        write_sha256sums,
    )
    from .runtime_payload_manifest import EDGE_SCHEMA_VERSION, verify_source_schema_version
    from .runtime_release import sha256_file
except ImportError:  # pragma: no cover - direct execution in the builder
    from build_runtime_release import (  # type: ignore[no-redef]
        _build_wheelhouse,
        _fsync_directory,
        _fsync_file,
        _load_signing_private_key,
        _require_arm64_python311_builder,
        _run,
        _sign_archive,
        _write_new_fsynced_file,
        _write_offline_requirements,
    )
    from business_release import (  # type: ignore[no-redef]
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
        validate_business_release_id,
        validate_business_release_tree,
        validate_release_sequence,
        validate_version_name,
        write_sha256sums,
    )
    from runtime_payload_manifest import EDGE_SCHEMA_VERSION, verify_source_schema_version  # type: ignore[no-redef]
    from runtime_release import sha256_file  # type: ignore[no-redef]


BUSINESS_RELEASE_BUILD_FILES = (
    "install/build_business_release.py",
    "install/build_runtime_release.py",
    "install/business_release.py",
    "install/runtime_payload_manifest.py",
    "install/runtime_release.py",
)


def _git_metadata(source_root: Path) -> tuple[str, int]:
    source_root = source_root.resolve(strict=True)
    repository = source_root.parent
    prefix = [
        "git",
        "-c",
        f"safe.directory={repository}",
        "-C",
        str(repository),
    ]
    actual = Path(
        _run([*prefix, "rev-parse", "--show-toplevel"]).stdout.strip()
    ).resolve(strict=True)
    if actual != repository:
        raise RuntimeError("hardware source root must be directly inside its repository")
    scoped = [
        str((source_root / name).relative_to(repository))
        for name in (
            *BUSINESS_APP_FILES,
            *BUSINESS_RELEASE_BUILD_FILES,
            "pyproject.toml",
            "uv.lock",
        )
    ]
    if _run([*prefix, "status", "--porcelain", "--", *scoped]).stdout.strip():
        raise RuntimeError("business sources or dependency locks are not committed")
    commit = _run([*prefix, "rev-parse", "HEAD"]).stdout.strip()
    epoch = _run([*prefix, "show", "-s", "--format=%ct", commit]).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None or not epoch.isdigit():
        raise RuntimeError("cannot resolve deterministic business release metadata")
    return commit, int(epoch)


def _copy_business_sources(source_root: Path, app: Path) -> None:
    app.mkdir(mode=0o755, parents=True)
    for name in BUSINESS_APP_FILES:
        source = source_root / name
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"business allowlist source is missing: {name}")
        destination = app / name
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o644)


def _export_business_requirements(source_root: Path, destination: Path) -> None:
    _run(
        [
            "uv",
            "export",
            "--project",
            str(source_root),
            "--frozen",
            "--only-group",
            "business",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--no-header",
            "--output-file",
            str(destination),
        ]
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("uv exported an empty business dependency set")


def _write_metadata(
    release_root: Path,
    *,
    release_id: str,
    version_name: str,
    release_sequence: int,
    git_commit: str,
    source_date_epoch: int,
) -> None:
    (release_root / "manifest.env").write_text(
        "".join(
            (
                "ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION="
                f"{BUSINESS_RELEASE_FORMAT_VERSION}\n",
                f"ECOBIN_ARTIFACT_KIND={BUSINESS_ARTIFACT_KIND}\n",
                f"ECOBIN_RELEASE_ID={release_id}\n",
                f"ECOBIN_VERSION_NAME={version_name}\n",
                f"ECOBIN_RELEASE_SEQUENCE={release_sequence}\n",
                f"ECOBIN_GIT_COMMIT={git_commit}\n",
                f"ECOBIN_PYTHON_SERIES={PYTHON_SERIES}\n",
                f"ECOBIN_TARGET_PLATFORM={TARGET_PLATFORM}\n",
                f"ECOBIN_EDGE_SCHEMA_VERSION={EDGE_SCHEMA_VERSION}\n",
                f"ECOBIN_SOURCE_DATE_EPOCH={source_date_epoch}\n",
                "ECOBIN_BUSINESS_ALLOWLIST_SHA256="
                f"{business_allowlist_sha256()}\n",
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
        newline="\n",
    )
    (release_root / "release.env").write_text(
        "".join(
            (
                f"ECOBIN_EDGE_VERSION={version_name}\n",
                f"ECOBIN_BUSINESS_RELEASE_ID={release_id}\n",
                f"ECOBIN_BUSINESS_RELEASE_SEQUENCE={release_sequence}\n",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )


def _compile_business_sources(release_root: Path, work_root: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONPYCACHEPREFIX"] = str(work_root / "pycache")
    sources = [
        str(release_root / "app" / name)
        for name in BUSINESS_APP_FILES
        if name.endswith(".py")
    ]
    _run([sys.executable, "-m", "py_compile", *sources], environment=environment)


def _verify_business_environment(release_root: Path, work_root: Path) -> None:
    verification = work_root / "verification-venv"
    _run([sys.executable, "-m", "venv", "--copies", str(verification)])
    python = verification / "bin" / "python"
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--require-hashes",
            "--find-links",
            str(release_root / "wheelhouse"),
            "--requirement",
            str(release_root / "requirements-offline.txt"),
        ]
    )
    _run([str(python), "-m", "pip", "check"])

    identity = work_root / "device-identity.json"
    identity.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "assetUid": "00000000-0000-4000-8000-000000000001",
                "deviceName": "business-release-build-check",
                "modelCode": "EC-M0",
                "expectedPortCount": 1,
                "deviceEntryUrl": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(identity, 0o600)
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
    _run(
        [
            str(python),
            "-I",
            "-c",
            (
                "import importlib,sys; "
                "sys.dont_write_bytecode=True; "
                "sys.path.insert(0,sys.argv[1]); "
                "[importlib.import_module(name) for name in "
                "('cv2','serial','qcloud_cos','main','config',"
                "'local_proxy_cloud_transport','business_message_handler',"
                "'business_outbox_relay','command_processor',"
                "'fixed_frame_mcu_adapter')]"
            ),
            str(release_root / "app"),
        ],
        cwd=work_root,
        environment=environment,
    )


def _write_archive(
    release_root: Path,
    archive: Path,
    *,
    release_id: str,
    source_date_epoch: int,
) -> None:
    top_level = f"{BUSINESS_ARCHIVE_PREFIX}{release_id}"
    with archive.open("xb") as raw:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            compresslevel=9,
            mtime=source_date_epoch,
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as output:
                top = tarfile.TarInfo(top_level)
                top.type = tarfile.DIRTYPE
                top.mode = 0o755
                top.uid = top.gid = 0
                top.uname = top.gname = "root"
                top.mtime = source_date_epoch
                output.addfile(top)
                for path in sorted(
                    release_root.rglob("*"),
                    key=lambda item: item.relative_to(release_root).as_posix(),
                ):
                    if path.is_symlink() or not (path.is_dir() or path.is_file()):
                        raise RuntimeError("business release contains a link or special file")
                    relative = path.relative_to(release_root).as_posix()
                    info = output.gettarinfo(
                        str(path), arcname=f"{top_level}/{relative}"
                    )
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mtime = source_date_epoch
                    info.mode = 0o755 if path.is_dir() else 0o644
                    if path.is_file():
                        with path.open("rb") as source:
                            output.addfile(info, source)
                    else:
                        output.addfile(info)


def build_release(
    *,
    source_root: Path,
    output_directory: Path,
    release_id: str,
    version_name: str,
    release_sequence: int,
    signing_private_key: Path,
) -> tuple[Path, Path, Path]:
    verify_source_schema_version(source_root)
    _require_arm64_python311_builder()
    validate_business_release_id(release_id)
    validate_version_name(version_name)
    release_sequence = validate_release_sequence(release_sequence)
    private_key = _load_signing_private_key(signing_private_key)
    source_root = source_root.resolve(strict=True)
    git_commit, source_date_epoch = _git_metadata(source_root)
    output_directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    archive = output_directory / f"{BUSINESS_ARCHIVE_PREFIX}{release_id}.tar.gz"
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    signature = archive.with_suffix(archive.suffix + ".sig")
    if any(path.exists() for path in (archive, checksum, signature)):
        raise RuntimeError("business release output already exists")

    with tempfile.TemporaryDirectory(prefix="ecobin-business-build-") as temp_text:
        work_root = Path(temp_text)
        release_root = work_root / f"{BUSINESS_ARCHIVE_PREFIX}{release_id}"
        release_root.mkdir(mode=0o755)
        _copy_business_sources(source_root, release_root / "app")
        (release_root / "migrations").mkdir(mode=0o755)
        _export_business_requirements(
            source_root, release_root / "requirements-runtime.txt"
        )
        _build_wheelhouse(
            release_root / "requirements-runtime.txt",
            release_root / "wheelhouse",
        )
        _write_offline_requirements(
            release_root / "wheelhouse",
            release_root / "requirements-offline.txt",
        )
        _write_metadata(
            release_root,
            release_id=release_id,
            version_name=version_name,
            release_sequence=release_sequence,
            git_commit=git_commit,
            source_date_epoch=source_date_epoch,
        )
        write_sha256sums(release_root)
        validate_business_release_tree(
            release_root,
            expected_release_id=release_id,
            expected_version_name=version_name,
            expected_release_sequence=release_sequence,
        )
        _compile_business_sources(release_root, work_root)
        _verify_business_environment(release_root, work_root)

        suffix = f".tmp-{os.getpid()}"
        temporary_archive = output_directory / f".{archive.name}{suffix}"
        temporary_checksum = output_directory / f".{checksum.name}{suffix}"
        temporary_signature = output_directory / f".{signature.name}{suffix}"
        try:
            _write_archive(
                release_root,
                temporary_archive,
                release_id=release_id,
                source_date_epoch=source_date_epoch,
            )
            _fsync_file(temporary_archive)
            digest = sha256_file(temporary_archive)
            signature_bytes = _sign_archive(temporary_archive, private_key)
            _write_new_fsynced_file(
                temporary_checksum,
                f"{digest}  {archive.name}\n".encode("ascii"),
                0o644,
            )
            _write_new_fsynced_file(temporary_signature, signature_bytes, 0o644)
            os.replace(temporary_archive, archive)
            os.replace(temporary_checksum, checksum)
            os.replace(temporary_signature, signature)
            _fsync_directory(output_directory)
        finally:
            for temporary in (
                temporary_archive,
                temporary_checksum,
                temporary_signature,
            ):
                if temporary.exists():
                    temporary.unlink()
    return archive, checksum, signature


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--release-sequence", type=int, required=True)
    parser.add_argument("--signing-private-key", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    archive, checksum, signature = build_release(
        source_root=args.source_root,
        output_directory=args.output_directory,
        release_id=args.release_id,
        version_name=args.version_name,
        release_sequence=args.release_sequence,
        signing_private_key=args.signing_private_key,
    )
    print(archive)
    print(checksum)
    print(signature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
