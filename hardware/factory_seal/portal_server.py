from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import threading

from .controller import FactorySealController
from .errors import FactorySealError


class FactorySealPortalServer:
    """Bounded root-owned Unix service; no secret is projected to the portal."""

    def __init__(
        self,
        controller: FactorySealController,
        socket_path: Path | str = (
            "/run/ecobin/factory-portal/seal-control.sock"
        ),
        *,
        group_id: int,
        connection_timeout_seconds: float = 2.0,
    ) -> None:
        if (
            isinstance(connection_timeout_seconds, bool)
            or not isinstance(connection_timeout_seconds, (int, float))
            or not 0.05 <= connection_timeout_seconds <= 10.0
        ):
            raise ValueError(
                "connection_timeout_seconds must be between 0.05 and 10"
            )
        self._controller = controller
        self._path = Path(socket_path)
        self._group_id = group_id
        self._connection_timeout_seconds = float(
            connection_timeout_seconds
        )
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        _prepare_secure_socket_parent(
            self._path.parent,
            group_id=self._group_id,
        )
        _remove_stale_socket(self._path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._path))
        except Exception:
            listener.close()
            raise
        os.chmod(self._path, 0o660)
        if hasattr(os, "chown"):
            os.chown(self._path, 0, self._group_id)
        listener.listen(4)
        listener.settimeout(0.5)
        self._socket = listener
        self._thread = threading.Thread(
            target=self._serve,
            name="factory-seal-portal",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stopping.set()
        listener = self._socket
        if listener is not None:
            listener.close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

    def _serve(self) -> None:
        listener = self._socket
        if listener is None:
            return
        while not self._stopping.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                try:
                    connection.settimeout(
                        self._connection_timeout_seconds
                    )
                    confirmed = self._handle(connection)
                except OSError:
                    confirmed = False
            # The HTTP process has received the response before its target is
            # stopped.  A crash here is recovered by the first-boot loop.
            if confirmed:
                try:
                    self._controller.reconcile_cleanup()
                except Exception:
                    pass

    def _handle(self, connection: socket.socket) -> bool:
        try:
            request = bytearray()
            while len(request) <= 4096:
                chunk = connection.recv(2048)
                if not chunk:
                    break
                request.extend(chunk)
                if b"\n" in chunk:
                    break
            if len(request) > 4096 or not request.endswith(b"\n"):
                raise FactorySealError("FACTORY_SEAL_REQUEST_INVALID")
            value = json.loads(bytes(request).decode("ascii"))
            if not isinstance(value, dict):
                raise FactorySealError("FACTORY_SEAL_REQUEST_INVALID")
            operation = value.get("operation")
            if operation == "GET_STATUS" and set(value) == {"operation"}:
                data = self._controller.status()
                confirmed = False
            elif operation == "CONFIRM" and set(value) == {
                "operation",
                "operatorConfirmationUid",
            }:
                data = self._controller.confirm(
                    value["operatorConfirmationUid"]
                )
                confirmed = True
            else:
                raise FactorySealError("FACTORY_SEAL_REQUEST_INVALID")
            response = {"ok": True, "data": data, "errorCode": None}
        except TimeoutError:
            response = {
                "ok": False,
                "data": None,
                "errorCode": "FACTORY_SEAL_REQUEST_TIMEOUT",
            }
            confirmed = False
        except OSError:
            response = {
                "ok": False,
                "data": None,
                "errorCode": "FACTORY_SEAL_IO_ERROR",
            }
            confirmed = False
        except (UnicodeDecodeError, json.JSONDecodeError, FactorySealError) as error:
            code = getattr(error, "code", "FACTORY_SEAL_REQUEST_INVALID")
            response = {"ok": False, "data": None, "errorCode": code}
            confirmed = False
        except Exception:
            response = {
                "ok": False,
                "data": None,
                "errorCode": "FACTORY_SEAL_INTERNAL_ERROR",
            }
            confirmed = False
        encoded = (
            json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        try:
            connection.sendall(encoded)
        except (OSError, TimeoutError):
            return False
        return confirmed


def _prepare_secure_socket_parent(parent: Path, *, group_id: int) -> None:
    """Create each component without following a replaceable path.

    Production runs as root.  A non-root owner is accepted only when the
    process itself is non-root, which keeps filesystem-backed unit tests
    useful without weakening the deployed service.
    """

    if not parent.is_absolute() or any(
        component in {".", ".."} for component in parent.parts
    ):
        raise RuntimeError("factory seal socket parent is invalid")
    if os.name != "posix":
        parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        _verify_directory_chain_without_symlinks(parent)
        return

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(parent.anchor, flags)
    try:
        _require_secure_directory(os.fstat(descriptor))
        for component in parent.parts[1:]:
            try:
                os.mkdir(component, 0o750, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(
                component,
                flags,
                dir_fd=descriptor,
            )
            try:
                _require_secure_directory(os.fstat(next_descriptor))
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        effective_uid = os.geteuid()
        os.fchmod(descriptor, 0o750)
        os.fchown(
            descriptor,
            0 if effective_uid == 0 else effective_uid,
            group_id if effective_uid == 0 else os.getegid(),
        )
        _require_secure_directory(os.fstat(descriptor))
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            "factory seal socket parent is insecure"
        ) from error
    finally:
        os.close(descriptor)


def _verify_directory_chain_without_symlinks(parent: Path) -> None:
    current = Path(parent.anchor)
    for component in parent.parts[1:]:
        current /= component
        details = current.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise RuntimeError("factory seal socket parent is insecure")


def _require_secure_directory(details: os.stat_result) -> None:
    if not stat.S_ISDIR(details.st_mode):
        raise RuntimeError("factory seal socket parent is not a directory")
    effective_uid = os.geteuid()
    if details.st_uid not in {0, effective_uid}:
        raise RuntimeError("factory seal socket parent owner is invalid")
    writable = details.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    sticky_root = bool(details.st_mode & stat.S_ISVTX) and details.st_uid == 0
    if writable and not sticky_root:
        raise RuntimeError("factory seal socket parent is replaceable")


def _remove_stale_socket(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISSOCK(details.st_mode):
        raise RuntimeError("factory seal socket path is not a stale socket")
    if os.name == "posix" and details.st_uid not in {0, os.geteuid()}:
        raise RuntimeError("factory seal stale socket owner is invalid")
    path.unlink()


__all__ = ["FactorySealPortalServer"]
