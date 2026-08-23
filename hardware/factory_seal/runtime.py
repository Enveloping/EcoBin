from __future__ import annotations

from .validation import FactorySealPaths, collect_local_factory_facts


class RuntimeFactorySealAuthorizer:
    """Bridge validated local factory facts into one EdgeStore transaction."""

    def __init__(self, store, paths: FactorySealPaths = FactorySealPaths()) -> None:
        self._store = store
        self._paths = paths

    def authorize(self, command: dict) -> dict:
        payload = command.get("payload")
        expected_hardware_sn = (
            payload.get("hardwareSn") if isinstance(payload, dict) else None
        )
        facts = collect_local_factory_facts(
            self._paths,
            expected_hardware_sn=expected_hardware_sn,
        )
        return self._store.accept_factory_seal_authorization(
            command,
            facts.as_store_dict(),
        )


__all__ = ["RuntimeFactorySealAuthorizer"]
