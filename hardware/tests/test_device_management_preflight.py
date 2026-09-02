from __future__ import annotations

from copy import deepcopy

import pytest

from device_management_preflight import HELPERS, probe_helpers


def _health(component: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "component": component,
        "status": "READY",
        "stage": 3,
        "mutationEnabled": False,
        "remoteTriggerEnabled": False,
    }


class RecordingClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.requests: list[tuple[str, dict[str, object]]] = []

    def request(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        self.requests.append((action, payload))
        return deepcopy(self.result)


def test_preflight_probes_both_fixed_helpers_with_strict_timeouts() -> None:
    clients: list[RecordingClient] = []
    constructor_calls: list[tuple[str, dict[str, object]]] = []

    def factory(socket_path: str, **kwargs: object) -> RecordingClient:
        constructor_calls.append((socket_path, kwargs))
        component = HELPERS[len(constructor_calls) - 1][2]
        client = RecordingClient(_health(component))
        clients.append(client)
        return client

    assert probe_helpers(factory) == (
        "BUSINESS_ACTIVATION_HELPER",
        "MCU_FLASH_HELPER",
    )
    assert [call[0] for call in constructor_calls] == [item[0] for item in HELPERS]
    assert [call[1]["protocol_name"] for call in constructor_calls] == [
        item[1] for item in HELPERS
    ]
    assert all(call[1]["connect_timeout_seconds"] == 1.0 for call in constructor_calls)
    assert all(call[1]["response_timeout_seconds"] == 5.0 for call in constructor_calls)
    assert all(client.requests == [("HEALTH", {})] for client in clients)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("component", "OTHER_HELPER"),
        ("status", "UNKNOWN"),
        ("stage", 4),
        ("mutationEnabled", True),
        ("remoteTriggerEnabled", True),
    ],
)
def test_preflight_rejects_any_unsafe_helper_posture(
    field: str,
    value: object,
) -> None:
    first = _health("BUSINESS_ACTIVATION_HELPER")
    first[field] = value

    def factory(_socket_path: str, **_kwargs: object) -> RecordingClient:
        return RecordingClient(first)

    with pytest.raises(RuntimeError, match="safe stage-three posture"):
        probe_helpers(factory)


def test_preflight_rejects_unexpected_health_fields() -> None:
    first = _health("BUSINESS_ACTIVATION_HELPER")
    first["unexpected"] = True

    def factory(_socket_path: str, **_kwargs: object) -> RecordingClient:
        return RecordingClient(first)

    with pytest.raises(RuntimeError, match="health document is malformed"):
        probe_helpers(factory)
