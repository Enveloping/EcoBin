from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Iterable, Mapping


class AtomicWriteError(RuntimeError):
    pass


OwnershipSetter = Callable[[Path], None]


def root_group_owner(group_id: int) -> OwnershipSetter:
    if isinstance(group_id, bool) or not isinstance(group_id, int) or group_id < 0:
        raise ValueError("group_id must be a non-negative integer")

    def set_owner(path: Path) -> None:
        get_euid = getattr(os, "geteuid", None)
        if get_euid is not None and get_euid() != 0:
            raise PermissionError("root privileges are required for the public projection")
        os.chown(path, 0, group_id)

    return set_owner


class AtomicJsonFile:
    """Bounded JSON storage with same-directory replace and directory fsync."""

    def __init__(
        self,
        path: Path,
        *,
        mode: int,
        directory_mode: int = 0o700,
        owner: OwnershipSetter | None = None,
        maximum_bytes: int = 64 * 1024,
        compatible_read_modes: Iterable[int] | None = None,
    ) -> None:
        if mode & ~0o777:
            raise ValueError("mode contains unsupported bits")
        if maximum_bytes < 1:
            raise ValueError("maximum_bytes must be positive")
        self.path = Path(path)
        self.mode = mode
        self.directory_mode = directory_mode
        self.owner = owner
        self.maximum_bytes = maximum_bytes
        self.compatible_read_modes = frozenset(
            compatible_read_modes if compatible_read_modes is not None else (mode,)
        )
        if not self.compatible_read_modes or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value & ~0o777
            for value in self.compatible_read_modes
        ):
            raise ValueError("compatible read mode contains unsupported bits")

    def read_object(self) -> dict[str, Any] | None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("JSON state path must be a regular file")
        if (
            os.name != "nt"
            and stat.S_IMODE(info.st_mode) not in self.compatible_read_modes
        ):
            raise ValueError("JSON state permissions are invalid")
        if info.st_size < 1 or info.st_size > self.maximum_bytes:
            raise ValueError("JSON state size is invalid")
        raw = self.path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("JSON state is invalid") from error
        if not isinstance(value, dict):
            raise ValueError("JSON state root must be an object")
        return value

    def write_object(self, value: Mapping[str, Any]) -> None:
        payload = (
            json.dumps(
                dict(value),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) > self.maximum_bytes:
            raise ValueError("JSON payload is too large")

        self._prepare_directory()
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                self.mode,
            )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, self.mode)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if self.owner is not None:
                self.owner(temporary)
            os.replace(temporary, self.path)
            os.chmod(self.path, self.mode)
            if self.owner is not None:
                self.owner(self.path)
            self._fsync_file()
            self._fsync_directory()
        except Exception as error:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            if isinstance(error, (ValueError, PermissionError)):
                raise
            raise AtomicWriteError("atomic JSON write failed") from error

    def _prepare_directory(self) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, mode=self.directory_mode, exist_ok=True)
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("JSON state parent must be a real directory")
        os.chmod(parent, self.directory_mode)
        try:
            target = self.path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode):
            raise ValueError("JSON state target must be a regular file")

    def _fsync_directory(self) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self.path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _fsync_file(self) -> None:
        if os.name == "nt":
            # The write handle was already flushed above.  Windows rejects
            # fsync on a read-only descriptor; Linux production performs the
            # additional post-chown inode sync here.
            return
        descriptor = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
