#!/usr/bin/env python3
"""Inject K1 and the shared factory AP passphrase without logging either."""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import stat
from pathlib import Path
from typing import Callable, Sequence

from release_trust import ReleaseTrustError, enforce_secret_memory_policy


MAX_SECRET_FILE_BYTES = 4096
K1_DESTINATION = Path("etc/ecobin/enrollment.key")
AP_KEY_DESTINATION = Path("etc/ecobin/setup-ap.key")


class SecretInjectionError(RuntimeError):
    """A secret source or mounted destination failed a non-secret check."""


def inject_factory_secrets(
    rootfs: Path,
    *,
    enrollment_key_source: Path,
    setup_ap_key_source: Path,
    chown: Callable[[Path, int, int], None] | None = None,
) -> None:
    root = _validated_root(rootfs)
    enrollment_payload = _read_secret_source(
        enrollment_key_source,
        validator=_validate_enrollment_key,
        label="enrollment key",
    )
    setup_payload = _read_secret_source(
        setup_ap_key_source,
        validator=_validate_setup_passphrase,
        label="factory AP key",
    )
    destinations = (
        (_destination(root, K1_DESTINATION), enrollment_payload),
        (_destination(root, AP_KEY_DESTINATION), setup_payload),
    )
    if any(os.path.lexists(path) for path, _ in destinations):
        raise SecretInjectionError("sealed destination already contains a secret")

    owner = chown or _chown_root
    written: list[Path] = []
    try:
        for destination, payload in destinations:
            _atomic_write_secret(destination, payload, owner)
            written.append(destination)
    except BaseException:
        for destination in written:
            try:
                destination.unlink()
                _fsync_directory(destination.parent)
            except OSError:
                pass
        raise


def _validated_root(rootfs: Path) -> Path:
    raw = Path(rootfs)
    if not raw.is_absolute():
        raise SecretInjectionError("rootfs path must be absolute")
    try:
        root = raw.resolve(strict=True)
    except OSError as error:
        raise SecretInjectionError("rootfs path is missing") from error
    if root == Path(root.anchor).resolve(strict=True):
        raise SecretInjectionError("refusing to inject into the host root")
    marker = root / "etc/os-release"
    if marker.is_symlink() or not marker.is_file():
        raise SecretInjectionError("rootfs identity is missing or unsafe")
    _inside(root, marker, strict=True)
    return root


def _read_secret_source(
    source: Path,
    *,
    validator: Callable[[bytes], None],
    label: str,
) -> bytes:
    path = Path(source)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SecretInjectionError(f"{label} source is unavailable") from error
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (os.name == "posix" and (details.st_uid != 0 or details.st_gid != 0))
            or (os.name == "posix" and details.st_mode & 0o077)
            or details.st_size <= 0
            or details.st_size > MAX_SECRET_FILE_BYTES
        ):
            raise SecretInjectionError(f"{label} source permissions are unsafe")
        chunks: list[bytes] = []
        remaining = MAX_SECRET_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_SECRET_FILE_BYTES:
        raise SecretInjectionError(f"{label} source is too large")
    validator(payload)
    return payload


def _single_ascii_line(payload: bytes, label: str) -> str:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise SecretInjectionError(f"{label} must be ASCII") from error
    lines = text.splitlines()
    if len(lines) != 1 or text not in {lines[0], f"{lines[0]}\n", f"{lines[0]}\r\n"}:
        raise SecretInjectionError(f"{label} must contain exactly one line")
    if not lines[0]:
        raise SecretInjectionError(f"{label} must not be empty")
    return lines[0]


def _validate_enrollment_key(payload: bytes) -> None:
    encoded = _single_ascii_line(payload, "enrollment key")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SecretInjectionError("enrollment key is not valid base64") from error
    if not 32 <= len(decoded) <= 128:
        raise SecretInjectionError("enrollment key length is outside policy")


def _validate_setup_passphrase(payload: bytes) -> None:
    passphrase = _single_ascii_line(payload, "factory AP key")
    if not 8 <= len(passphrase) <= 63:
        raise SecretInjectionError("factory AP key length is outside WPA2 policy")
    if passphrase != passphrase.strip() or any(
        ord(character) < 0x20 or ord(character) > 0x7E
        for character in passphrase
    ):
        raise SecretInjectionError("factory AP key contains forbidden characters")


def _destination(root: Path, relative: Path) -> Path:
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        if os.path.lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise SecretInjectionError("secret destination parent is unsafe")
            _inside(root, current, strict=True)
        else:
            current.mkdir(mode=0o755)
            _fsync_directory(current.parent)
    destination = current / relative.name
    _inside(root, destination, strict=False)
    return destination


def _inside(root: Path, path: Path, *, strict: bool) -> Path:
    try:
        resolved = path.resolve(strict=strict)
    except OSError as error:
        raise SecretInjectionError("secret image path cannot be resolved") from error
    if not resolved.is_relative_to(root):
        raise SecretInjectionError("secret image path escapes the mounted rootfs")
    return resolved


def _atomic_write_secret(
    destination: Path,
    payload: bytes,
    chown: Callable[[Path, int, int], None],
) -> None:
    temporary = destination.with_name(f".{destination.name}.seal-tmp")
    if os.path.lexists(temporary):
        raise SecretInjectionError("stale secret injection temporary exists")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        chown(temporary, 0, 0)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _chown_root(path: Path, uid: int, gid: int) -> None:
    if os.name != "posix":
        raise SecretInjectionError("secret ownership can only be set on POSIX")
    os.chown(path, uid, gid, follow_symlinks=False)


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inject protected factory inputs into a mounted image",
    )
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--enrollment-key-file", type=Path, required=True)
    parser.add_argument("--setup-ap-key-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        enforce_secret_memory_policy("factory secret injection")
        inject_factory_secrets(
            args.rootfs,
            enrollment_key_source=args.enrollment_key_file,
            setup_ap_key_source=args.setup_ap_key_file,
        )
    except (ReleaseTrustError, SecretInjectionError) as error:
        print(f"factory secret injection failed: {error}", file=os.sys.stderr)
        return 2
    print("factory secret injection complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
