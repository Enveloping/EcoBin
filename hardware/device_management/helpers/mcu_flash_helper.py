"""Socket-activated fixed MCU flash primitive for the local updater."""

from __future__ import annotations

from .mcu_flash_primitives import McuFlashPrimitives
from .privileged_control import (
    HelperAction,
    HelperPolicy,
    serve_systemd_connection,
)

PROTOCOL_NAME = "ecobin.mcu-flash-helper.control"
POLICY = HelperPolicy(
    protocol_name=PROTOCOL_NAME,
    component="MCU_FLASH_HELPER",
    fixed_configuration={
        "serialDevice": "/dev/ttyS5",
        "boot0WiringPiPin": 2,
        "resetGateWiringPiPin": 5,
        "safeLevel": 0,
        "gpioBinary": "/usr/bin/gpio",
        "flashBinary": "/usr/bin/stm32flash",
        "firmwareRoot": "/var/lib/ecobin/updater/mcu-firmware",
    },
    primitive_actions=("FLASH_MCU_FIRMWARE", "RECOVER_MCU_APPLICATION"),
)


def build_actions(updater_uid: int) -> dict[str, HelperAction]:
    primitives = McuFlashPrimitives(updater_uid=updater_uid)
    return {
        "FLASH_MCU_FIRMWARE": HelperAction(
            primitives.flash,
            frozenset({"updateUid", "actionUid", "source"}),
        ),
        "RECOVER_MCU_APPLICATION": HelperAction(
            primitives.recover_application,
            frozenset({"updateUid", "actionUid"}),
        ),
    }


def main() -> int:
    serve_systemd_connection(POLICY, build_actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
