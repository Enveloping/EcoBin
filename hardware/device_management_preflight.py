"""Boot-scoped health probe for the permanent privileged helpers.

The probe runs as ``ecobin-updater``.  A successful oneshot service therefore
proves that both socket-activated helper programs can be loaded, authenticate
the real updater UID, and answer inside their production systemd sandboxes.
Stage three deliberately keeps every mutating helper action disabled.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from local_control import LocalControlClient


HELPERS = (
    (
        "/run/ecobin/privileged/business-activation.sock",
        "ecobin.business-activation-helper.control",
        "BUSINESS_ACTIVATION_HELPER",
    ),
    (
        "/run/ecobin/privileged/mcu-flash.sock",
        "ecobin.mcu-flash-helper.control",
        "MCU_FLASH_HELPER",
    ),
)


def probe_helpers(
    client_factory: Callable[..., LocalControlClient] = LocalControlClient,
) -> tuple[str, ...]:
    """Probe both helpers and return their validated component identities."""

    confirmed: list[str] = []
    for socket_path, protocol_name, component in HELPERS:
        client = client_factory(
            socket_path,
            protocol_name=protocol_name,
            connect_timeout_seconds=1.0,
            response_timeout_seconds=5.0,
        )
        result = client.request("HEALTH", {})
        _require_stage_three_health(result, component)
        confirmed.append(component)
    return tuple(confirmed)


def _require_stage_three_health(result: Any, component: str) -> None:
    expected_fields = {
        "schemaVersion",
        "component",
        "status",
        "stage",
        "mutationEnabled",
        "remoteTriggerEnabled",
    }
    if not isinstance(result, dict) or set(result) != expected_fields:
        raise RuntimeError("privileged helper health document is malformed")
    if (
        result["schemaVersion"] != 1
        or result["component"] != component
        or result["status"] != "READY"
        or result["stage"] != 3
        or result["mutationEnabled"] is not False
        or result["remoteTriggerEnabled"] is not False
    ):
        raise RuntimeError("privileged helper is not in the safe stage-three posture")


def main() -> int:
    try:
        confirmed = probe_helpers()
    except Exception as error:  # noqa: BLE001 - systemd needs a stable failed exit
        print(
            f"ecobin-device-management-preflight=FAIL reason={type(error).__name__}",
            file=sys.stderr,
        )
        return 1
    print(
        "ecobin-device-management-preflight=PASS helpers=" + ",".join(confirmed)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
