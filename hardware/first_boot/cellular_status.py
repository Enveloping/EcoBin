from __future__ import annotations

from pathlib import Path

from .atomic_json import AtomicJsonFile
from .model import normalize_error_code


_EXACT_FIELDS = {"schemaVersion", "resultCode"}


class CellularStatusStore:
    """Root-only, boot-scoped projection of the current uplink result."""

    def __init__(
        self,
        path: Path = Path("/run/ecobin/cellular-uplink/status.json"),
    ) -> None:
        self._file = AtomicJsonFile(
            path,
            mode=0o600,
            directory_mode=0o700,
            maximum_bytes=512,
        )

    def publish(self, result_code: str) -> None:
        code = _validate_result_code(result_code)
        self._file.write_object(
            {
                "schemaVersion": 1,
                "resultCode": code,
            }
        )

    def read(self) -> str | None:
        value = self._file.read_object()
        if value is None:
            return None
        schema_version = value.get("schemaVersion")
        if (
            set(value) != _EXACT_FIELDS
            or type(schema_version) is not int
            or schema_version != 1
        ):
            raise ValueError("cellular status fields are invalid")
        return _validate_result_code(value.get("resultCode"))


def _validate_result_code(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("cellular result code is invalid")
    normalized = normalize_error_code(value)
    if normalized != value or normalized == "INTERNAL_ERROR":
        raise ValueError("cellular result code is invalid")
    return normalized
