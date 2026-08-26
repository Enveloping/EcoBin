#!/usr/bin/env python3
"""Build a deterministic Debian 12 ARM64 EcoBin runtime archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import mmap
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

try:
    from .runtime_release import (
        ARTIFACT_KIND,
        EDGE_SCHEMA_VERSION,
        ED25519_SIGNATURE_BYTES,
        MAX_PRIVATE_KEY_BYTES,
        PYTHON_SERIES,
        RELEASE_FORMAT_VERSION,
        RUNTIME_APP_FILES,
        ReleaseValidationError,
        _read_security_file,
        runtime_allowlist_sha256,
        sha256_file,
        validate_release_id,
        validate_release_tree,
        write_sha256sums,
    )
except ImportError:  # pragma: no cover - direct execution in the ARM64 builder
    from runtime_release import (  # type: ignore[no-redef]
        ARTIFACT_KIND,
        EDGE_SCHEMA_VERSION,
        ED25519_SIGNATURE_BYTES,
        MAX_PRIVATE_KEY_BYTES,
        PYTHON_SERIES,
        RELEASE_FORMAT_VERSION,
        RUNTIME_APP_FILES,
        ReleaseValidationError,
        _read_security_file,
        runtime_allowlist_sha256,
        sha256_file,
        validate_release_id,
        validate_release_tree,
        write_sha256sums,
    )


WHEEL_PATTERN = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)-.+\.whl$"
)
RUNTIME_RELEASE_BUILD_FILES = (
    "install/build_runtime_release.py",
    "install/runtime_payload_manifest.py",
    "install/runtime_release.py",
)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _require_arm64_python311_builder() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("runtime release builder requires Linux")
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        raise RuntimeError(
            "runtime release builder requires a native/emulated ARM64 builder"
        )
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("runtime release builder requires Python 3.11")
    if shutil.which("uv") is None:
        raise RuntimeError("runtime release builder requires locked uv")


def _git_metadata(source_root: Path) -> tuple[str, int]:
    source_root = source_root.resolve(strict=True)
    expected_repository = source_root.parent
    git_prefix = [
        "git",
        "-c",
        f"safe.directory={expected_repository}",
        "-C",
        str(expected_repository),
    ]
    repository = Path(
        _run(
            [*git_prefix, "rev-parse", "--show-toplevel"]
        ).stdout.strip()
    ).resolve(strict=True)
    if repository != expected_repository:
        raise RuntimeError("hardware source root must be directly inside its repository")
    scoped = [
        str((source_root / name).relative_to(repository))
        for name in RUNTIME_APP_FILES
    ]
    scoped.extend(
        str((source_root / name).relative_to(repository))
        for name in (
            *RUNTIME_RELEASE_BUILD_FILES,
            "pyproject.toml",
            "uv.lock",
        )
    )
    dirty = _run(
        [*git_prefix, "status", "--porcelain", "--", *scoped]
    ).stdout.strip()
    if dirty:
        raise RuntimeError(
            "runtime sources or dependency locks are not committed; refusing build"
        )
    commit = _run(
        [*git_prefix, "rev-parse", "HEAD"]
    ).stdout.strip()
    epoch_text = _run(
        [*git_prefix, "show", "-s", "--format=%ct", commit]
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not epoch_text.isdigit():
        raise RuntimeError("cannot resolve deterministic Git release metadata")
    return commit, int(epoch_text)


def _copy_runtime_sources(source_root: Path, app: Path) -> None:
    app.mkdir(mode=0o755, parents=True)
    for name in RUNTIME_APP_FILES:
        source = source_root / name
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"runtime allowlist source is missing: {name}")
        destination = app / name
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o644)


def _export_runtime_requirements(source_root: Path, destination: Path) -> None:
    _run(
        [
            "uv",
            "export",
            "--project",
            str(source_root),
            "--frozen",
            "--only-group",
            "runtime",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--no-header",
            "--output-file",
            str(destination),
        ]
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("uv exported an empty runtime dependency set")


def _build_wheelhouse(requirements: Path, wheelhouse: Path) -> None:
    wheelhouse.mkdir(mode=0o755)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--require-hashes",
            "--wheel-dir",
            str(wheelhouse),
            "--requirement",
            str(requirements),
        ]
    )
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels or any(path.is_symlink() for path in wheels):
        raise RuntimeError("ARM64 wheelhouse was not built completely")
    if any(path.suffix != ".whl" for path in wheelhouse.iterdir()):
        raise RuntimeError("wheelhouse contains a non-wheel artifact")


def _write_offline_requirements(wheelhouse: Path, destination: Path) -> None:
    distributions: dict[str, tuple[str, str]] = {}
    for wheel in sorted(wheelhouse.glob("*.whl")):
        match = WHEEL_PATTERN.fullmatch(wheel.name)
        if match is None:
            raise RuntimeError(f"cannot parse wheel identity: {wheel.name}")
        name = match.group("name").replace("_", "-").lower()
        version = match.group("version")
        if name in distributions:
            raise RuntimeError(f"wheelhouse contains duplicate distribution: {name}")
        distributions[name] = (version, sha256_file(wheel))
    content = "".join(
        f"{name}=={version} --hash=sha256:{digest}\n"
        for name, (version, digest) in sorted(distributions.items())
    )
    destination.write_text(content, encoding="utf-8", newline="\n")


def _verify_offline_environment(release_root: Path, work_root: Path) -> None:
    verification = work_root / "verification-venv"
    _run([sys.executable, "-m", "venv", str(verification)])
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
    environment = dict(os.environ)
    environment["ECOBIN_CONFIG_MODE"] = "production"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
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
                "('cryptography','cv2','paho.mqtt.client','serial',"
                "'qcloud_cos','main','mqtt_client','command_processor',"
                "'factory_seal.admission','onenet_wire',"
                "'fixed_frame_mcu_adapter','simulated_camera',"
                "'system.mcu_safe_gpio','device_credentials')]"
            ),
            str(release_root / "app"),
        ],
        cwd=work_root,
        environment=environment,
    )


def _write_manifest(
    release_root: Path,
    *,
    release_id: str,
    git_commit: str,
    source_date_epoch: int,
) -> None:
    manifest = (
        f"ECOBIN_RELEASE_FORMAT_VERSION={RELEASE_FORMAT_VERSION}\n"
        f"ECOBIN_ARTIFACT_KIND={ARTIFACT_KIND}\n"
        f"ECOBIN_RELEASE_ID={release_id}\n"
        f"ECOBIN_GIT_COMMIT={git_commit}\n"
        f"ECOBIN_PYTHON_SERIES={PYTHON_SERIES}\n"
        f"ECOBIN_EDGE_SCHEMA_VERSION={EDGE_SCHEMA_VERSION}\n"
        f"ECOBIN_SOURCE_DATE_EPOCH={source_date_epoch}\n"
        "ECOBIN_RUNTIME_ALLOWLIST_SHA256="
        f"{runtime_allowlist_sha256()}\n"
    )
    (release_root / "manifest.env").write_text(
        manifest,
        encoding="utf-8",
        newline="\n",
    )
    (release_root / "release.env").write_text(
        f"ECOBIN_EDGE_VERSION={release_id}\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_deterministic_archive(
    release_root: Path,
    archive: Path,
    *,
    release_id: str,
    source_date_epoch: int,
) -> None:
    top_level = f"ecobin-hardware-{release_id}"
    with archive.open("xb") as raw_stream:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_stream,
            compresslevel=9,
            mtime=source_date_epoch,
        ) as gzip_stream:
            with tarfile.open(fileobj=gzip_stream, mode="w") as tar_stream:
                top = tarfile.TarInfo(top_level)
                top.type = tarfile.DIRTYPE
                top.mode = 0o755
                top.uid = top.gid = 0
                top.uname = top.gname = "root"
                top.mtime = source_date_epoch
                tar_stream.addfile(top)
                paths = sorted(
                    release_root.rglob("*"),
                    key=lambda path: path.relative_to(release_root).as_posix(),
                )
                for path in paths:
                    if path.is_symlink() or not (path.is_dir() or path.is_file()):
                        raise RuntimeError("release tree contains a link or special file")
                    relative = path.relative_to(release_root).as_posix()
                    info = tar_stream.gettarinfo(
                        str(path),
                        arcname=f"{top_level}/{relative}",
                    )
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mtime = source_date_epoch
                    info.mode = 0o755 if path.is_dir() else 0o644
                    if path.is_file():
                        with path.open("rb") as source:
                            tar_stream.addfile(info, source)
                    else:
                        tar_stream.addfile(info)


def _load_signing_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        key_pem = _read_security_file(
            path,
            maximum_bytes=MAX_PRIVATE_KEY_BYTES,
            require_private_permissions=True,
        )
    except ReleaseValidationError:
        raise RuntimeError("signing private key is not securely readable") from None
    if not key_pem.startswith(b"-----BEGIN PRIVATE KEY-----"):
        raise RuntimeError("signing private key must be PKCS8 Ed25519 PEM")
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except (TypeError, ValueError):
        raise RuntimeError("signing private key must be PKCS8 Ed25519 PEM") from None
    finally:
        del key_pem
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("signing private key must be Ed25519")
    return key


def _sign_archive(
    archive: Path,
    private_key: Ed25519PrivateKey,
) -> bytes:
    try:
        with archive.open("rb") as stream:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                signature = private_key.sign(data)
    except OSError:
        raise RuntimeError("runtime archive could not be signed") from None
    if len(signature) != ED25519_SIGNATURE_BYTES:
        raise RuntimeError("Ed25519 signer returned an invalid signature")
    return signature


def _write_new_fsynced_file(path: Path, content: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_file(path: Path) -> None:
    access = os.O_RDONLY if os.name == "posix" else os.O_RDWR
    descriptor = os.open(path, access | getattr(os, "O_BINARY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_release(
    *,
    source_root: Path,
    output_directory: Path,
    release_id: str,
    signing_private_key: Path,
) -> tuple[Path, Path, Path]:
    _require_arm64_python311_builder()
    validate_release_id(release_id)
    private_key = _load_signing_private_key(signing_private_key)
    source_root = source_root.resolve(strict=True)
    git_commit, source_date_epoch = _git_metadata(source_root)
    output_directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    archive = output_directory / f"ecobin-hardware-{release_id}.tar.gz"
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    signature = archive.with_suffix(archive.suffix + ".sig")
    if archive.exists() or checksum.exists() or signature.exists():
        raise RuntimeError("release output already exists")

    with tempfile.TemporaryDirectory(prefix="ecobin-runtime-build-") as temp_text:
        work_root = Path(temp_text)
        release_root = work_root / f"ecobin-hardware-{release_id}"
        release_root.mkdir(mode=0o755)
        _copy_runtime_sources(source_root, release_root / "app")
        _export_runtime_requirements(
            source_root,
            release_root / "requirements-runtime.txt",
        )
        _build_wheelhouse(
            release_root / "requirements-runtime.txt",
            release_root / "wheelhouse",
        )
        _write_offline_requirements(
            release_root / "wheelhouse",
            release_root / "requirements-offline.txt",
        )
        _write_manifest(
            release_root,
            release_id=release_id,
            git_commit=git_commit,
            source_date_epoch=source_date_epoch,
        )
        write_sha256sums(release_root)
        validate_release_tree(release_root, expected_release_id=release_id)
        _verify_offline_environment(release_root, work_root)

        suffix = f".tmp-{os.getpid()}"
        temporary_archive = output_directory / f".{archive.name}{suffix}"
        temporary_checksum = output_directory / f".{checksum.name}{suffix}"
        temporary_signature = output_directory / f".{signature.name}{suffix}"
        try:
            _write_deterministic_archive(
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
            _write_new_fsynced_file(
                temporary_signature,
                signature_bytes,
                0o644,
            )
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
    parser.add_argument("--release-id", required=True)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument(
        "--signing-private-key",
        type=Path,
        required=True,
        help="external PKCS8 PEM Ed25519 private key (never copied)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    archive, checksum, signature = build_release(
        source_root=args.source_root,
        output_directory=args.output_directory,
        release_id=args.release_id,
        signing_private_key=args.signing_private_key,
    )
    print(archive)
    print(checksum)
    print(signature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
