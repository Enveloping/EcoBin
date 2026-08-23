from __future__ import annotations

from .errors import FactorySealError
from .validation import FactorySealPaths, inspect_sealed_authorization


FACTORY_NOT_SEALED = "FACTORY_NOT_SEALED"

# These commands are needed to finish enrollment, cloud acceptance, reliable
# event convergence, operator sealing, or diagnostics.  None starts a delivery,
# cleaning operation, actuator/sensor work, or an MCU firmware write.
PRESEAL_CONTROL_COMMAND_TYPES = frozenset(
    {
        "APPLY_CONFIGURATION",
        "CONFIRM_EDGE_EVENT",
        "PROVIDE_PHOTO_UPLOAD_GRANT",
        "REQUEST_DEVICE_ACCEPTANCE",
        "AUTHORIZE_FACTORY_SEAL",
        "SYNC_DEVICE_ENTRY_URL",
        "OPEN_REMOTE_SUPPORT_TUNNEL",
        "CLOSE_REMOTE_SUPPORT_TUNNEL",
    }
)


class FactorySealProductionGate:
    """Fail-closed admission for commands that can affect production work.

    A marker file by itself is not authority.  The existing strict validator
    binds it to the exact root-owned EdgeStore authorization row.  The shared
    inspector additionally proves the v16 cleanup timestamp and the exact
    reliable completion event in ``event_outbox``.  Therefore a legacy v15
    row that only says ``SEALED`` remains closed until reconciliation has
    atomically completed and recorded that fact.
    """

    def __init__(
        self,
        paths: FactorySealPaths = FactorySealPaths(),
    ) -> None:
        self._paths = paths

    @staticmethod
    def requires_completed_seal(command_type: object) -> bool:
        return command_type not in PRESEAL_CONTROL_COMMAND_TYPES

    def production_ready(self) -> bool:
        fact = inspect_sealed_authorization(self._paths)
        return bool(
            fact.exists
            and fact.valid
            and fact.authorization_state == "SEALED"
        )

    def require_command_allowed(self, command_type: object) -> None:
        if not self.requires_completed_seal(command_type):
            return
        if not self.production_ready():
            raise FactorySealError(FACTORY_NOT_SEALED)


__all__ = [
    "FACTORY_NOT_SEALED",
    "PRESEAL_CONTROL_COMMAND_TYPES",
    "FactorySealProductionGate",
]
