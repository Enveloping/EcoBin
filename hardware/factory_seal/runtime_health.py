from __future__ import annotations

import subprocess
from typing import Any


RUNTIME_SERVICE_DEFINITIONS = (
    ("COMMUNICATION_AGENT", "ecobin-communication.service"),
    ("DEVICE_UPDATER", "ecobin-updater.service"),
    ("BUSINESS_ACTIVATION_CONTROL", "ecobin-business-activation-helper.socket"),
    ("MCU_UPDATE_CONTROL", "ecobin-mcu-flash-helper.socket"),
    ("DEVICE_MANAGEMENT_PREFLIGHT", "ecobin-device-management-preflight.service"),
    ("BUSINESS_RUNTIME", "ecobin-hardware.service"),
    ("CELLULAR_UPLINK", "ecobin-cellular-uplink.service"),
    ("REMOTE_SUPPORT", "ecobin-remote-support.service"),
)
RUNTIME_SERVICE_STATES = frozenset(
    {"ACTIVE", "STARTING", "INACTIVE", "FAILED", "UNKNOWN"}
)


def inspect_runtime_services() -> list[dict[str, str]]:
    """Inspect every seal dependency in one bounded systemd request."""

    units = tuple(unit for _service_id, unit in RUNTIME_SERVICE_DEFINITIONS)
    try:
        result = subprocess.run(
            ("/usr/bin/systemctl", "is-active", *units),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            text=True,
            encoding="ascii",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return unknown_runtime_services()
    observed = result.stdout.splitlines()
    if len(observed) != len(RUNTIME_SERVICE_DEFINITIONS):
        return unknown_runtime_services()
    activating_units = tuple(
        unit
        for (_service_id, unit), state in zip(
            RUNTIME_SERVICE_DEFINITIONS,
            observed,
            strict=True,
        )
        if state.strip().lower() == "activating"
    )
    failed_restarts = _failed_auto_restart_units(activating_units)
    return [
        {
            "id": service_id,
            "state": (
                "FAILED"
                if unit in failed_restarts
                else _public_state(state)
            ),
        }
        for (service_id, unit), state in zip(
            RUNTIME_SERVICE_DEFINITIONS,
            observed,
            strict=True,
        )
    ]


def _failed_auto_restart_units(units: tuple[str, ...]) -> frozenset[str]:
    if not units:
        return frozenset()
    try:
        result = subprocess.run(
            (
                "/usr/bin/systemctl",
                "show",
                *units,
                "--property=Id",
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
                "--no-pager",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            text=True,
            encoding="ascii",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if result.returncode != 0:
        return frozenset()
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in (*result.stdout.splitlines(), ""):
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, separator, value = line.partition("=")
        if not separator or key in current:
            return frozenset()
        current[key] = value
    expected_fields = {"Id", "ActiveState", "SubState", "Result"}
    if any(set(record) != expected_fields for record in records):
        return frozenset()
    allowed = set(units)
    return frozenset(
        record["Id"]
        for record in records
        if record["Id"] in allowed
        and record["ActiveState"] == "activating"
        and record["SubState"] == "auto-restart"
    )


def active_runtime_services() -> list[dict[str, str]]:
    return _uniform_runtime_services("ACTIVE")


def inactive_runtime_services() -> list[dict[str, str]]:
    return _uniform_runtime_services("INACTIVE")


def unknown_runtime_services() -> list[dict[str, str]]:
    return _uniform_runtime_services("UNKNOWN")


def runtime_services_healthy(value: object) -> bool:
    try:
        services = validate_runtime_services(value)
    except ValueError:
        return False
    return all(service["state"] == "ACTIVE" for service in services)


def validate_runtime_services(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(RUNTIME_SERVICE_DEFINITIONS):
        raise ValueError("runtime service status list is invalid")
    validated: list[dict[str, str]] = []
    for (expected_id, _unit), item in zip(
        RUNTIME_SERVICE_DEFINITIONS,
        value,
        strict=True,
    ):
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "state"}
            or item.get("id") != expected_id
            or item.get("state") not in RUNTIME_SERVICE_STATES
        ):
            raise ValueError("runtime service status is invalid")
        validated.append({"id": expected_id, "state": str(item["state"])})
    return validated


def _uniform_runtime_services(state: str) -> list[dict[str, str]]:
    return [
        {"id": service_id, "state": state}
        for service_id, _unit in RUNTIME_SERVICE_DEFINITIONS
    ]


def _public_state(value: Any) -> str:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if normalized in {"active", "reloading"}:
        return "ACTIVE"
    if normalized == "activating":
        return "STARTING"
    if normalized == "failed":
        return "FAILED"
    if normalized in {"inactive", "deactivating"}:
        return "INACTIVE"
    return "UNKNOWN"


__all__ = [
    "RUNTIME_SERVICE_DEFINITIONS",
    "RUNTIME_SERVICE_STATES",
    "active_runtime_services",
    "inactive_runtime_services",
    "inspect_runtime_services",
    "runtime_services_healthy",
    "unknown_runtime_services",
    "validate_runtime_services",
]
