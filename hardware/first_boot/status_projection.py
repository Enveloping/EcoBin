from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Callable

from .atomic_json import AtomicJsonFile, OwnershipSetter, root_group_owner
from .cellular_status import (
    CELLULAR_CHECK_IDS,
    CellularStatus,
    CellularStatusStore,
    cellular_check_states,
)
from .model import FactoryTestStatus, FirstBootFacts, FirstBootStage, normalize_error_code


_EXACT_FIELDS = {
    "stage",
    "lastErrorCode",
    "timeTrusted",
    "factoryTestStatus",
    "cellular",
}
_CELLULAR_PUBLIC_FIELDS = {
    "resultCode",
    "consecutiveFailureCount",
    "retryScheduled",
    "retryInSeconds",
    "checks",
}
_AP_EXACT_FIELDS = {"schemaVersion", "allowed", "statusCode"}


class PortalStatusProjector:
    def __init__(
        self,
        path: Path = Path("/run/ecobin/factory-portal/status.json"),
        *,
        group_id: int | None = None,
        owner: OwnershipSetter | None = None,
        cellular_status_reader: Callable[[], CellularStatus | None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if owner is None:
            if group_id is None:
                raise ValueError("group_id is required for the root-owned projection")
            owner = root_group_owner(group_id)
        self._file = AtomicJsonFile(
            path,
            mode=0o640,
            directory_mode=0o750,
            owner=owner,
            maximum_bytes=4096,
        )
        self._cellular_status_reader = (
            cellular_status_reader or CellularStatusStore().read_status
        )
        self._monotonic = monotonic or time.monotonic

    def publish(
        self,
        stage: FirstBootStage,
        facts: FirstBootFacts,
        *,
        error_code: str,
    ) -> None:
        try:
            cellular_status = self._cellular_status_reader()
        except Exception:
            cellular_status = None
        public = {
            "stage": stage.value,
            "lastErrorCode": normalize_error_code(error_code),
            "timeTrusted": bool(facts.time_trusted),
            "factoryTestStatus": facts.factory_test_status.value,
            "cellular": _public_cellular_status(
                cellular_status,
                now_monotonic=self._monotonic(),
            ),
        }
        if set(public) != _EXACT_FIELDS:
            raise AssertionError("portal status projection fields drifted")
        self._file.write_object(public)


class AccessPointAuthorizationProjector:
    """Root-to-web one-bit gate; never exposes seal or EdgeStore contents."""

    def __init__(
        self,
        path: Path = Path("/run/ecobin/factory-network/ap-allowed.json"),
        *,
        group_id: int | None = None,
        owner: OwnershipSetter | None = None,
    ) -> None:
        if owner is None:
            if group_id is None:
                raise ValueError("group_id is required for the AP projection")
            owner = root_group_owner(group_id)
        self._file = AtomicJsonFile(
            path,
            mode=0o640,
            directory_mode=0o755,
            owner=owner,
            maximum_bytes=512,
        )

    def publish(
        self,
        facts: FirstBootFacts,
        *,
        allow_sealed_response: bool = False,
    ) -> None:
        response_pending = bool(
            allow_sealed_response
            and facts.sealed_exists
        )
        allowed = not facts.sealed_exists or response_pending
        status = (
            "SEALED_RESPONSE_PENDING"
            if response_pending
            else "UNSEALED"
            if not facts.sealed_exists
            else "SEALED"
            if facts.sealed_valid
            else "SEALED_FACT_INVALID"
        )
        self._file.write_object(
            {"schemaVersion": 2, "allowed": allowed, "statusCode": status}
        )


def validate_public_projection(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _EXACT_FIELDS:
        raise ValueError("portal projection fields are invalid")
    try:
        stage = FirstBootStage(value["stage"])
        factory_status = FactoryTestStatus(value["factoryTestStatus"])
    except (TypeError, ValueError) as error:
        raise ValueError("portal projection enum is invalid") from error
    last_error = value["lastErrorCode"]
    if not isinstance(last_error, str) or normalize_error_code(last_error) != last_error:
        raise ValueError("portal projection error code is invalid")
    if not isinstance(value["timeTrusted"], bool):
        raise ValueError("portal projection timeTrusted is invalid")
    cellular = validate_public_cellular_status(value.get("cellular"))
    return {
        "stage": stage.value,
        "lastErrorCode": last_error,
        "timeTrusted": value["timeTrusted"],
        "factoryTestStatus": factory_status.value,
        "cellular": cellular,
    }


def _public_cellular_status(
    status: CellularStatus | None,
    *,
    now_monotonic: float,
) -> dict[str, object]:
    if status is None:
        return {
            "resultCode": "STATUS_UNAVAILABLE",
            "consecutiveFailureCount": 0,
            "retryScheduled": False,
            "retryInSeconds": None,
            "checks": cellular_check_states("STATUS_UNAVAILABLE"),
        }
    retry_scheduled = status.next_retry_at_monotonic_ms is not None
    retry_in_seconds = (
        max(
            0,
            math.ceil(
                status.next_retry_at_monotonic_ms / 1000 - now_monotonic
            ),
        )
        if status.next_retry_at_monotonic_ms is not None
        else None
    )
    return {
        "resultCode": status.result_code,
        "consecutiveFailureCount": status.consecutive_failure_count,
        "retryScheduled": retry_scheduled,
        "retryInSeconds": retry_in_seconds,
        "checks": (
            dict(status.checks)
            if status.checks is not None
            else cellular_check_states(status.result_code)
        ),
    }


def validate_public_cellular_status(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _CELLULAR_PUBLIC_FIELDS:
        raise ValueError("portal cellular projection fields are invalid")
    result_code = value.get("resultCode")
    failure_count = value.get("consecutiveFailureCount")
    retry_scheduled = value.get("retryScheduled")
    retry_in_seconds = value.get("retryInSeconds")
    checks = value.get("checks")
    if (
        not isinstance(result_code, str)
        or normalize_error_code(result_code) != result_code
        or type(failure_count) is not int
        or not 0 <= failure_count <= 1_000_000
        or type(retry_scheduled) is not bool
        or not (
            retry_in_seconds is None
            or (
                type(retry_in_seconds) is int
                and 0 <= retry_in_seconds <= 300
            )
        )
        or retry_scheduled != (retry_in_seconds is not None)
        or (retry_scheduled and failure_count == 0)
        or (
            result_code == "NONE"
            and (failure_count != 0 or retry_scheduled)
        )
        or not isinstance(checks, dict)
        or set(checks) != set(CELLULAR_CHECK_IDS)
        or any(
            checks.get(check_id) not in {"PASSED", "WAITING", "FAILED", "UNKNOWN"}
            for check_id in CELLULAR_CHECK_IDS
        )
        or checks != cellular_check_states(result_code)
    ):
        raise ValueError("portal cellular projection values are invalid")
    return {
        "resultCode": result_code,
        "consecutiveFailureCount": failure_count,
        "retryScheduled": retry_scheduled,
        "retryInSeconds": retry_in_seconds,
        "checks": {
            check_id: checks[check_id] for check_id in CELLULAR_CHECK_IDS
        },
    }


def validate_ap_authorization_projection(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _AP_EXACT_FIELDS:
        raise ValueError("AP authorization projection fields are invalid")
    if value.get("schemaVersion") != 2 or type(value.get("allowed")) is not bool:
        raise ValueError("AP authorization projection values are invalid")
    status = value.get("statusCode")
    if status not in {
        "UNSEALED",
        "SEALED_RESPONSE_PENDING",
        "SEALED",
        "SEALED_FACT_INVALID",
    }:
        raise ValueError("AP authorization projection status is invalid")
    if value["allowed"] and status not in {
        "UNSEALED",
        "SEALED_RESPONSE_PENDING",
    }:
        raise ValueError("AP authorization projection is inconsistent")
    if not value["allowed"] and status in {
        "UNSEALED",
        "SEALED_RESPONSE_PENDING",
    }:
        raise ValueError("AP authorization projection is inconsistent")
    return dict(value)
