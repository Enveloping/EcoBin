from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .atomic_json import AtomicJsonFile
from .model import normalize_error_code


_V1_FIELDS = {"schemaVersion", "resultCode"}
_V2_FIELDS = {
    "schemaVersion",
    "resultCode",
    "consecutiveFailureCount",
    "nextRetryAtMonotonicMs",
}


@dataclass(frozen=True, slots=True)
class CellularStatus:
    result_code: str
    consecutive_failure_count: int
    next_retry_at_monotonic_ms: int | None


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

    def publish(
        self,
        result_code: str,
        *,
        consecutive_failure_count: int = 0,
        next_retry_at_monotonic_ms: int | None = None,
    ) -> None:
        code = _validate_result_code(result_code)
        status = _validate_status_values(
            code,
            consecutive_failure_count,
            next_retry_at_monotonic_ms,
        )
        self._file.write_object(
            {
                "schemaVersion": 2,
                "resultCode": status.result_code,
                "consecutiveFailureCount": status.consecutive_failure_count,
                "nextRetryAtMonotonicMs": status.next_retry_at_monotonic_ms,
            }
        )

    def read(self) -> str | None:
        status = self.read_status()
        return status.result_code if status is not None else None

    def read_status(self) -> CellularStatus | None:
        value = self._file.read_object()
        if value is None:
            return None
        return parse_cellular_status(value)


def parse_cellular_status(value: object) -> CellularStatus:
    if not isinstance(value, dict):
        raise ValueError("cellular status fields are invalid")
    schema_version = value.get("schemaVersion")
    if type(schema_version) is not int:
        raise ValueError("cellular status fields are invalid")
    if schema_version == 1 and set(value) == _V1_FIELDS:
        return CellularStatus(
            result_code=_validate_result_code(value.get("resultCode")),
            consecutive_failure_count=0,
            next_retry_at_monotonic_ms=None,
        )
    if schema_version != 2 or set(value) != _V2_FIELDS:
        raise ValueError("cellular status fields are invalid")
    return _validate_status_values(
        _validate_result_code(value.get("resultCode")),
        value.get("consecutiveFailureCount"),
        value.get("nextRetryAtMonotonicMs"),
    )


def _validate_result_code(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("cellular result code is invalid")
    normalized = normalize_error_code(value)
    if normalized != value or normalized == "INTERNAL_ERROR":
        raise ValueError("cellular result code is invalid")
    return normalized


def _validate_status_values(
    result_code: str,
    consecutive_failure_count: object,
    next_retry_at_monotonic_ms: object,
) -> CellularStatus:
    if (
        type(consecutive_failure_count) is not int
        or not 0 <= consecutive_failure_count <= 1_000_000
        or not (
            next_retry_at_monotonic_ms is None
            or (
                type(next_retry_at_monotonic_ms) is int
                and 0 <= next_retry_at_monotonic_ms <= 10**15
            )
        )
        or (
            result_code == "NONE"
            and (
                consecutive_failure_count != 0
                or next_retry_at_monotonic_ms is not None
            )
        )
        or (
            next_retry_at_monotonic_ms is not None
            and consecutive_failure_count == 0
        )
    ):
        raise ValueError("cellular status values are invalid")
    return CellularStatus(
        result_code=result_code,
        consecutive_failure_count=consecutive_failure_count,
        next_retry_at_monotonic_ms=next_retry_at_monotonic_ms,
    )


__all__ = ["CellularStatus", "CellularStatusStore", "parse_cellular_status"]
