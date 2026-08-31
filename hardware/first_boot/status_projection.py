from __future__ import annotations

from pathlib import Path
from typing import Callable

from .atomic_json import AtomicJsonFile, OwnershipSetter, root_group_owner
from .model import FactoryTestStatus, FirstBootFacts, FirstBootStage, normalize_error_code


_EXACT_FIELDS = {
    "stage",
    "lastErrorCode",
    "timeTrusted",
    "factoryTestStatus",
}
_AP_EXACT_FIELDS = {"schemaVersion", "allowed", "statusCode"}


class PortalStatusProjector:
    def __init__(
        self,
        path: Path = Path("/run/ecobin/factory-portal/status.json"),
        *,
        group_id: int | None = None,
        owner: OwnershipSetter | None = None,
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

    def publish(
        self,
        stage: FirstBootStage,
        facts: FirstBootFacts,
        *,
        error_code: str,
    ) -> None:
        public = {
            "stage": stage.value,
            "lastErrorCode": normalize_error_code(error_code),
            "timeTrusted": bool(facts.time_trusted),
            "factoryTestStatus": facts.factory_test_status.value,
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
    return {
        "stage": stage.value,
        "lastErrorCode": last_error,
        "timeTrusted": value["timeTrusted"],
        "factoryTestStatus": factory_status.value,
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
