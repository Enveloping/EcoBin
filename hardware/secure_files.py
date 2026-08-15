"""Small crash-safe file primitives used for device secrets."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Callable


def atomic_write_bytes(
    path: str | os.PathLike[str],
    content: bytes,
    *,
    mode: int = 0o600,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(
    path: str | os.PathLike[str],
    document: dict[str, Any],
    *,
    validator: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    content = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    atomic_write_bytes(path, content, mode=0o600)
    persisted = json.loads(Path(path).read_text(encoding="utf-8"))
    if persisted != document:
        raise RuntimeError("atomically persisted JSON did not verify")
    if validator is not None:
        validator(persisted)
    return persisted


def unlink_and_fsync(path: str | os.PathLike[str]) -> bool:
    target = Path(path)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    _fsync_directory(target.parent)
    return True


def _fsync_directory(directory: Path) -> None:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError:
        # Windows cannot open a directory descriptor; os.replace remains the
        # strongest available primitive there.  The production target is Linux.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)
