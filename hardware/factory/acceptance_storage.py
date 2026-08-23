"""Durable, private storage and process locks for offline factory acceptance.

This module intentionally knows nothing about EdgeStore, MQTT, COS, enrollment,
orders, bags, or production photo storage.  The factory executor owns separate
JSON files and lock files.  Every committed JSON value is written through a
0600 temporary file, fsynced, atomically replaced, and followed by a directory
fsync on Linux.
"""

from __future__ import annotations

import copy
import errno
import json
import os
import stat
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional


class AcceptanceStorageError(RuntimeError):
    """A durable acceptance file cannot be trusted."""


class AcceptanceLockBusy(RuntimeError):
    """Another acceptance/runtime owner already holds a required lock."""


FaultHook = Optional[Callable[[str], None]]


def _call_fault(hook: FaultHook, point: str) -> None:
    if hook is not None:
        hook(point)


def _reject_symlink(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise AcceptanceStorageError("acceptance path must not be a symlink")


def _ensure_private_directory(
    path: Path,
    *,
    chmod_existing: bool = True,
) -> None:
    """Create a private directory without following an existing symlink."""

    if not path.is_absolute():
        raise AcceptanceStorageError("acceptance directory must be absolute")
    _reject_symlink(path)
    existed = path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink(path)
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise AcceptanceStorageError("acceptance parent is not a directory")
    if chmod_existing or not existed:
        try:
            os.chmod(path, 0o700)
        except OSError as error:
            raise AcceptanceStorageError(
                "acceptance directory permissions cannot be secured"
            ) from error


def fsync_directory(path: Path) -> None:
    """Durably commit a directory entry on Linux.

    Windows does not expose a generally usable directory fsync through
    ``os.open``.  Tests still exercise the same atomic-replace path there;
    production Debian must complete the real directory fsync.
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if os.name == "nt" and error.errno in {
            errno.EACCES,
            errno.EINVAL,
            errno.EPERM,
        }:
            return
        raise AcceptanceStorageError(
            "acceptance directory cannot be opened for fsync"
        ) from error
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if os.name == "nt" and error.errno in {
                errno.EACCES,
                errno.EBADF,
                errno.EINVAL,
                errno.EPERM,
            }:
                return
            raise AcceptanceStorageError(
                "acceptance directory fsync failed"
            ) from error
    finally:
        os.close(descriptor)


class AtomicJsonFile:
    """One canonical JSON document committed with power-loss-safe ordering."""

    def __init__(
        self,
        path: Path | str,
        *,
        fault_hook: FaultHook = None,
        fault_prefix: str = "json",
    ) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise AcceptanceStorageError("acceptance JSON path must be absolute")
        if self.path.name in {"", ".", ".."}:
            raise AcceptanceStorageError("acceptance JSON filename is invalid")
        self._fault_hook = fault_hook
        self._fault_prefix = fault_prefix
        self._mutex = threading.RLock()

    def exists(self) -> bool:
        _reject_symlink(self.path)
        return self.path.exists()

    def read(self) -> Optional[dict]:
        with self._mutex:
            _reject_symlink(self.path)
            if not self.path.exists():
                return None
            flags = os.O_RDONLY
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(self.path, flags)
            except OSError as error:
                raise AcceptanceStorageError(
                    "acceptance JSON cannot be opened"
                ) from error
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise AcceptanceStorageError(
                        "acceptance JSON is not a regular file"
                    )
                if os.name != "nt" and metadata.st_mode & 0o077:
                    raise AcceptanceStorageError(
                        "acceptance JSON permissions are not 0600"
                    )
                with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                    descriptor = -1
                    value = json.load(handle)
            except (UnicodeError, json.JSONDecodeError) as error:
                raise AcceptanceStorageError(
                    "acceptance JSON is malformed"
                ) from error
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if not isinstance(value, dict):
                raise AcceptanceStorageError(
                    "acceptance JSON root must be an object"
                )
            return copy.deepcopy(value)

    def write(self, value: dict) -> None:
        if not isinstance(value, dict):
            raise TypeError("acceptance JSON value must be an object")
        encoded = (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        with self._mutex:
            _ensure_private_directory(self.path.parent)
            _reject_symlink(self.path)
            temporary = self.path.parent / (
                f".{self.path.name}.{os.getpid()}."
                f"{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )
            _reject_symlink(temporary)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = -1
            try:
                descriptor = os.open(temporary, flags, 0o600)
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                else:
                    os.chmod(temporary, 0o600)
                offset = 0
                while offset < len(encoded):
                    written = os.write(descriptor, encoded[offset:])
                    if written <= 0:
                        raise AcceptanceStorageError(
                            "acceptance JSON write made no progress"
                        )
                    offset += written
                os.fsync(descriptor)
                _call_fault(
                    self._fault_hook,
                    f"{self._fault_prefix}.after_file_fsync",
                )
                os.close(descriptor)
                descriptor = -1
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
                _call_fault(
                    self._fault_hook,
                    f"{self._fault_prefix}.after_replace",
                )
                fsync_directory(self.path.parent)
                _call_fault(
                    self._fault_hook,
                    f"{self._fault_prefix}.after_directory_fsync",
                )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


class ExclusiveFileLock:
    """A non-blocking 0600 advisory lock usable on Debian and test Windows."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise AcceptanceStorageError("acceptance lock path must be absolute")
        self._descriptor: Optional[int] = None

    @property
    def acquired(self) -> bool:
        return self._descriptor is not None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        # A caller may intentionally place the lock under the shared
        # ``/run/lock`` directory.  Secure a newly-created leaf directory but
        # never chmod an existing system-wide lock directory.
        _ensure_private_directory(
            self.path.parent,
            chmod_existing=False,
        )
        _reject_symlink(self.path)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                os.chmod(self.path, 0o600)
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise AcceptanceLockBusy(
                        "acceptance lock is already held"
                    ) from error
            else:
                import fcntl

                try:
                    fcntl.flock(
                        descriptor,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN}:
                        raise AcceptanceLockBusy(
                            "acceptance lock is already held"
                        ) from error
                    raise
        except Exception:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "ExclusiveFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.release()


class AcceptanceLease:
    """Hold both the executor singleton lock and the UART ownership lock."""

    def __init__(
        self,
        instance_lock_path: Path | str,
        uart_lock_path: Path | str,
    ) -> None:
        instance = Path(instance_lock_path)
        uart = Path(uart_lock_path)
        if instance == uart:
            raise AcceptanceStorageError(
                "instance and UART lock paths must be distinct"
            )
        self._instance = ExclusiveFileLock(instance)
        self._uart = ExclusiveFileLock(uart)

    @property
    def acquired(self) -> bool:
        return self._instance.acquired and self._uart.acquired

    def acquire(self) -> None:
        self._instance.acquire()
        try:
            self._uart.acquire()
        except Exception:
            self._instance.release()
            raise

    def release(self) -> None:
        self._uart.release()
        self._instance.release()

    def __enter__(self) -> "AcceptanceLease":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.release()
