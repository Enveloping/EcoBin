from __future__ import annotations

import json
from pathlib import Path
import socket
import uuid

from .errors import FactorySealPortalError
from .runtime_health import validate_runtime_services


DEFAULT_SOCKET = Path("/run/ecobin/factory-portal/seal-control.sock")
_STATUS_FIELDS = {
    "authorized",
    "confirmAllowed",
    "statusCode",
    "acceptanceGeneration",
    "authorizationBindingSha256",
    "runtimeServices",
}


def get_factory_seal_authorization_status(
    socket_path: Path | str = DEFAULT_SOCKET,
) -> dict[str, object]:
    return _request({"operation": "GET_STATUS"}, socket_path)


def confirm_factory_seal(
    operator_confirmation_uid: str,
    socket_path: Path | str = DEFAULT_SOCKET,
) -> dict[str, object]:
    try:
        parsed = uuid.UUID(str(operator_confirmation_uid))
    except (ValueError, TypeError, AttributeError):
        raise FactorySealPortalError("FACTORY_SEAL_CONFIRMATION_INVALID") from None
    if parsed.version != 4 or str(parsed) != operator_confirmation_uid:
        raise FactorySealPortalError("FACTORY_SEAL_CONFIRMATION_INVALID")
    return _request(
        {
            "operation": "CONFIRM",
            "operatorConfirmationUid": operator_confirmation_uid,
        },
        socket_path,
    )


def acknowledge_factory_seal_presented(
    operator_confirmation_uid: str,
    socket_path: Path | str = DEFAULT_SOCKET,
) -> dict[str, object]:
    try:
        parsed = uuid.UUID(str(operator_confirmation_uid))
    except (ValueError, TypeError, AttributeError):
        raise FactorySealPortalError(
            "FACTORY_SEAL_PRESENTATION_ACK_INVALID"
        ) from None
    if parsed.version != 4 or str(parsed) != operator_confirmation_uid:
        raise FactorySealPortalError(
            "FACTORY_SEAL_PRESENTATION_ACK_INVALID"
        )
    return _request(
        {
            "operation": "ACK_PRESENTED",
            "operatorConfirmationUid": operator_confirmation_uid,
        },
        socket_path,
    )


def _request(
    document: dict[str, object],
    socket_path: Path | str,
) -> dict[str, object]:
    encoded = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    if not hasattr(socket, "AF_UNIX"):
        raise FactorySealPortalError("FACTORY_SEAL_NOT_AVAILABLE")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(socket_path))
            client.sendall(encoded)
            response = bytearray()
            while len(response) <= 8192:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
                if b"\n" in chunk:
                    break
    except (OSError, TimeoutError):
        raise FactorySealPortalError("FACTORY_SEAL_NOT_AVAILABLE") from None
    if len(response) > 8192 or not response.endswith(b"\n"):
        raise FactorySealPortalError("FACTORY_SEAL_RESPONSE_INVALID")
    try:
        envelope = json.loads(bytes(response).decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FactorySealPortalError("FACTORY_SEAL_RESPONSE_INVALID") from None
    if not isinstance(envelope, dict) or set(envelope) != {"ok", "data", "errorCode"}:
        raise FactorySealPortalError("FACTORY_SEAL_RESPONSE_INVALID")
    if envelope["ok"] is not True:
        code = envelope.get("errorCode")
        raise FactorySealPortalError(
            code if isinstance(code, str) else "FACTORY_SEAL_RESPONSE_INVALID"
        )
    data = envelope.get("data")
    if not isinstance(data, dict) or set(data) != _STATUS_FIELDS:
        raise FactorySealPortalError("FACTORY_SEAL_RESPONSE_INVALID")
    if not isinstance(data["authorized"], bool) or not isinstance(
        data["confirmAllowed"], bool
    ):
        raise FactorySealPortalError("FACTORY_SEAL_RESPONSE_INVALID")
    try:
        data["runtimeServices"] = validate_runtime_services(
            data["runtimeServices"]
        )
    except ValueError:
        raise FactorySealPortalError("FACTORY_SEAL_RESPONSE_INVALID") from None
    return data


__all__ = [
    "FactorySealPortalError",
    "acknowledge_factory_seal_presented",
    "confirm_factory_seal",
    "get_factory_seal_authorization_status",
]
