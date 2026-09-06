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
_V3_FIELDS = {*_V2_FIELDS, "checks"}
CELLULAR_CHECK_IDS = (
    "MODEM_INTERFACE",
    "MODEM_CONTROL",
    "SIM_READY",
    "NETWORK_REGISTERED",
    "PACKET_ATTACHED",
    "IP_ADDRESS",
    "DEFAULT_ROUTE",
    "DNS_RESOLUTION",
    "BACKEND_HTTPS",
)
_CHECK_STATES = frozenset({"PASSED", "WAITING", "FAILED", "UNKNOWN"})

_CHECK_FAILURES = {
    "CELLULAR_DEVICE_NOT_FOUND": ("MODEM_INTERFACE", "WAITING"),
    "CELLULAR_RNDIS_UNAVAILABLE": ("MODEM_INTERFACE", "WAITING"),
    "CELLULAR_RNDIS_AMBIGUOUS": ("MODEM_INTERFACE", "FAILED"),
    "CELLULAR_USB_PARENT_UNVERIFIED": ("MODEM_INTERFACE", "FAILED"),
    "CELLULAR_USB_DRIVER_MISMATCH": ("MODEM_INTERFACE", "FAILED"),
    "CELLULAR_INTERFACE_INVALID": ("MODEM_INTERFACE", "FAILED"),
    "CELLULAR_MODEM_CONTROL_UNAVAILABLE": ("MODEM_CONTROL", "WAITING"),
    "CELLULAR_MODEM_CONTROL_AMBIGUOUS": ("MODEM_CONTROL", "FAILED"),
    "CELLULAR_MODEM_STATUS_UNAVAILABLE": ("MODEM_CONTROL", "FAILED"),
    "CELLULAR_SIM_ABSENT": ("SIM_READY", "FAILED"),
    "CELLULAR_SIM_LOCKED": ("SIM_READY", "FAILED"),
    "CELLULAR_NETWORK_REGISTRATION_PENDING": ("NETWORK_REGISTERED", "WAITING"),
    "CELLULAR_NETWORK_REGISTRATION_DENIED": ("NETWORK_REGISTERED", "FAILED"),
    "CELLULAR_PACKET_SERVICE_PENDING": ("PACKET_ATTACHED", "WAITING"),
    "CELLULAR_PROFILE_INSTALL_FAILED": ("IP_ADDRESS", "FAILED"),
    "CELLULAR_ACTIVATION_FAILED": ("IP_ADDRESS", "WAITING"),
    "CELLULAR_DHCP_UNAVAILABLE": ("IP_ADDRESS", "WAITING"),
    "CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE": ("DEFAULT_ROUTE", "FAILED"),
    "CELLULAR_DNS_UNAVAILABLE": ("DNS_RESOLUTION", "WAITING"),
    "CELLULAR_HTTPS_UNAVAILABLE": ("BACKEND_HTTPS", "WAITING"),
}
_TIME_RESULTS = frozenset(
    {
        "TIME_NOT_TRUSTED",
        "TIME_SYNC_PENDING",
        "TIME_TRUST_QUERY_FAILED",
        "CHRONY_ONLINE_FAILED",
        "CHRONY_ACTIVITY_FAILED",
        "CHRONY_SOURCES_UNAVAILABLE",
        "CHRONY_REFRESH_FAILED",
        "CHRONY_BURST_FAILED",
        "CHRONY_WAITSYNC_FAILED",
        "TIME_SYNC_INTERNAL_ERROR",
    }
)


@dataclass(frozen=True, slots=True)
class CellularStatus:
    result_code: str
    consecutive_failure_count: int
    next_retry_at_monotonic_ms: int | None
    checks: dict[str, str] | None = None


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
            maximum_bytes=2048,
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
                "schemaVersion": 3,
                "resultCode": status.result_code,
                "consecutiveFailureCount": status.consecutive_failure_count,
                "nextRetryAtMonotonicMs": status.next_retry_at_monotonic_ms,
                "checks": status.checks,
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
            checks=cellular_check_states(value.get("resultCode")),
        )
    if schema_version == 2 and set(value) == _V2_FIELDS:
        result_code = _validate_result_code(value.get("resultCode"))
        return _validate_status_values(
            result_code,
            value.get("consecutiveFailureCount"),
            value.get("nextRetryAtMonotonicMs"),
            cellular_check_states(result_code),
        )
    if schema_version != 3 or set(value) != _V3_FIELDS:
        raise ValueError("cellular status fields are invalid")
    return _validate_status_values(
        _validate_result_code(value.get("resultCode")),
        value.get("consecutiveFailureCount"),
        value.get("nextRetryAtMonotonicMs"),
        value.get("checks"),
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
    checks: object | None = None,
) -> CellularStatus:
    normalized_checks = _validate_checks(
        cellular_check_states(result_code) if checks is None else checks
    )
    if normalized_checks != cellular_check_states(result_code):
        raise ValueError("cellular check values are inconsistent")
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
        checks=normalized_checks,
    )


def cellular_check_states(result_code: object) -> dict[str, str]:
    if not isinstance(result_code, str):
        return {check_id: "UNKNOWN" for check_id in CELLULAR_CHECK_IDS}
    if result_code == "NONE":
        return {check_id: "PASSED" for check_id in CELLULAR_CHECK_IDS}
    if result_code in _TIME_RESULTS:
        current = "BACKEND_HTTPS"
        current_state = "WAITING"
    else:
        failure = _CHECK_FAILURES.get(result_code)
        if failure is None:
            return {check_id: "UNKNOWN" for check_id in CELLULAR_CHECK_IDS}
        current, current_state = failure
    current_index = CELLULAR_CHECK_IDS.index(current)
    return {
        check_id: (
            "PASSED"
            if index < current_index
            else current_state
            if index == current_index
            else "UNKNOWN"
        )
        for index, check_id in enumerate(CELLULAR_CHECK_IDS)
    }


def _validate_checks(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(CELLULAR_CHECK_IDS):
        raise ValueError("cellular check fields are invalid")
    if any(state not in _CHECK_STATES for state in value.values()):
        raise ValueError("cellular check values are invalid")
    return {check_id: value[check_id] for check_id in CELLULAR_CHECK_IDS}


__all__ = [
    "CELLULAR_CHECK_IDS",
    "CellularStatus",
    "CellularStatusStore",
    "cellular_check_states",
    "parse_cellular_status",
]
