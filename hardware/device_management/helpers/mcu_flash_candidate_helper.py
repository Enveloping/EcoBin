"""Stage-four MCU flash primitives authorized by the durable updater ledger."""

from __future__ import annotations

from .mcu_flash_primitives import McuFlashPrimitives
from .privileged_control import HelperAction, HelperPolicy, serve_systemd_connection
from .updater_mutation_authorizer import build_authorizer


PROTOCOL_NAME = "ecobin.mcu-flash-helper.control"
COMPONENT = "MCU_FLASH_CANDIDATE_HELPER"
POLICY = HelperPolicy(
    protocol_name=PROTOCOL_NAME,
    component=COMPONENT,
    fixed_configuration={
        "serialDevice": "/dev/ttyS5",
        "boot0WiringPiPin": 2,
        "resetGateWiringPiPin": 5,
        "safeLevel": 0,
        "gpioBinary": "/usr/bin/gpio",
        "flashBinary": "/usr/bin/stm32flash",
        "firmwareRoot": "/var/lib/ecobin/updater/mcu-firmware",
        "businessServicesRequiredInactive": [
            "ecobin-hardware.service",
            "ecobin-business.service",
            "ecobin-business-updatable-candidate.service",
        ],
    },
    primitive_actions=("FLASH_MCU_FIRMWARE", "RECOVER_MCU_APPLICATION"),
    stage=4,
    mutation_enabled=True,
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


def _authorizer_factory(_updater_uid: int):
    return build_authorizer(COMPONENT)


def main() -> int:
    serve_systemd_connection(POLICY, build_actions, _authorizer_factory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
