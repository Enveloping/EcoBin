from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .atomic_json import AtomicJsonFile
from .model import FirstBootStage, normalize_error_code


@dataclass(frozen=True)
class FirstBootState:
    schema_version: int
    stage: FirstBootStage
    generation: int
    last_error_code: str
    updated_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class FirstBootStateStore:
    def __init__(
        self,
        path: Path = Path("/var/lib/ecobin/first-boot/state.json"),
        *,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        # The business runtime receives read-only access to the separately
        # protected sealed.json marker through the first-boot directory.  Keep
        # group execute (traverse) on the shared parent whenever this frequently
        # written private state file is replaced; state.json itself remains
        # root-only 0600 and the directory is still not listable by the group.
        self._file = AtomicJsonFile(path, mode=0o600, directory_mode=0o710)
        self._clock = clock

    def load(self) -> FirstBootState | None:
        value = self._file.read_object()
        if value is None:
            return None
        return self._decode(value)

    def save(
        self,
        stage: FirstBootStage,
        *,
        previous: FirstBootState | None,
        last_error_code: str,
    ) -> FirstBootState:
        state = FirstBootState(
            schema_version=1,
            stage=stage,
            generation=(previous.generation + 1 if previous else 1),
            last_error_code=normalize_error_code(last_error_code),
            updated_at=self._clock(),
        )
        self._file.write_object(
            {
                "schemaVersion": state.schema_version,
                "stage": state.stage.value,
                "generation": state.generation,
                "lastErrorCode": state.last_error_code,
                "updatedAt": state.updated_at,
            }
        )
        return state

    @staticmethod
    def _decode(value: dict[str, Any]) -> FirstBootState:
        if set(value) != {
            "schemaVersion",
            "stage",
            "generation",
            "lastErrorCode",
            "updatedAt",
        }:
            raise ValueError("first-boot state fields are invalid")
        if value["schemaVersion"] != 1:
            raise ValueError("unsupported first-boot state schema")
        generation = value["generation"]
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("first-boot state generation is invalid")
        updated_at = value["updatedAt"]
        if not isinstance(updated_at, str) or not 1 <= len(updated_at) <= 40:
            raise ValueError("first-boot state time is invalid")
        error = value["lastErrorCode"]
        if not isinstance(error, str) or normalize_error_code(error) != error:
            raise ValueError("first-boot state error code is invalid")
        try:
            stage = FirstBootStage(value["stage"])
        except (TypeError, ValueError) as exception:
            raise ValueError("first-boot state stage is invalid") from exception
        return FirstBootState(1, stage, generation, error, updated_at)
