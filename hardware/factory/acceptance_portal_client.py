"""Low-privilege client for the root-owned P7 AF_UNIX executor."""

from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
import socket
import threading
from typing import Any


DEFAULT_CONTROL_SOCKET = Path("/run/ecobin/factory-test/control.sock")
MAXIMUM_IPC_RESPONSE_BYTES = 64 * 1024


class AcceptancePortalClientError(RuntimeError):
    def __init__(self, code: str, status: HTTPStatus) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class AcceptancePortalClient:
    def __init__(self, path: Path = DEFAULT_CONTROL_SOCKET) -> None:
        # ``Path('/run/...')`` is deliberately a POSIX path.  Windows treats
        # it as drive-relative during unit tests, even though the deployed
        # Debian path is absolute.  Preserve the production check while
        # allowing the same literal to be constructed on the test host.
        if not (path.is_absolute() or path.as_posix().startswith("/")):
            raise ValueError("acceptance socket path must be absolute")
        self._path = path
        self._mutation_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return self._request({"operation": "STATUS"}, timeout_seconds=2.0)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._mutation_lock.acquire(blocking=False):
            raise AcceptancePortalClientError(
                "ACCEPTANCE_REQUEST_BUSY", HTTPStatus.CONFLICT
            )
        try:
            return self._request(request, timeout_seconds=75.0)
        finally:
            self._mutation_lock.release()

    def _request(
        self,
        request: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        encoded = (
            json.dumps(
                request,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > 4096:
            raise AcceptancePortalClientError(
                "ACCEPTANCE_REQUEST_TOO_LARGE", HTTPStatus.BAD_REQUEST
            )
        if not hasattr(socket, "AF_UNIX"):
            raise AcceptancePortalClientError(
                "ACCEPTANCE_EXECUTOR_UNAVAILABLE",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        response = bytearray()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout_seconds)
                connection.connect(str(self._path))
                connection.sendall(encoded)
                connection.shutdown(socket.SHUT_WR)
                while len(response) <= MAXIMUM_IPC_RESPONSE_BYTES:
                    block = connection.recv(8192)
                    if not block:
                        break
                    response.extend(block)
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError) as error:
            raise AcceptancePortalClientError(
                "ACCEPTANCE_EXECUTOR_UNAVAILABLE",
                HTTPStatus.SERVICE_UNAVAILABLE,
            ) from error
        if len(response) > MAXIMUM_IPC_RESPONSE_BYTES:
            raise AcceptancePortalClientError(
                "ACCEPTANCE_RESPONSE_TOO_LARGE",
                HTTPStatus.BAD_GATEWAY,
            )
        try:
            envelope = json.loads(bytes(response).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AcceptancePortalClientError(
                "ACCEPTANCE_RESPONSE_INVALID", HTTPStatus.BAD_GATEWAY
            ) from error
        if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
            raise AcceptancePortalClientError(
                "ACCEPTANCE_RESPONSE_INVALID", HTTPStatus.BAD_GATEWAY
            )
        if envelope["ok"] is False:
            code = envelope.get("error")
            status_value = envelope.get("httpStatus")
            try:
                status = HTTPStatus(status_value)
            except (TypeError, ValueError):
                status = HTTPStatus.BAD_GATEWAY
            if not isinstance(code, str):
                code = "ACCEPTANCE_RESPONSE_INVALID"
            raise AcceptancePortalClientError(code, status)
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise AcceptancePortalClientError(
                "ACCEPTANCE_RESPONSE_INVALID", HTTPStatus.BAD_GATEWAY
            )
        return data
