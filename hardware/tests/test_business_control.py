from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from business_control import (
    BUSINESS_COMPONENT,
    BUSINESS_PROTOCOL_NAME,
    BusinessControlController,
    build_business_control_service,
)


INSTANCE_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _controller() -> BusinessControlController:
    return BusinessControlController(
        "1.2.3",
        utc_now=lambda: datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc),
        instance_uid_factory=lambda: INSTANCE_UID,
    )


def test_stage_three_status_does_not_claim_permanent_cutover() -> None:
    controller = _controller()

    starting = controller.health({})
    controller.mark_ready()
    ready = controller.health({})

    assert starting == {
        "component": BUSINESS_COMPONENT,
        "status": "STARTING",
        "runtimeInstanceUid": str(INSTANCE_UID),
        "releaseVersion": "1.2.3",
        "startedAt": "2026-09-02T05:00:00.000Z",
        "localProtocolName": BUSINESS_PROTOCOL_NAME,
        "localProtocolMajor": 1,
        "localProtocolMinor": 0,
        "managementArchitectureGeneration": "LEGACY_DIRECT",
        "cloudConnectionOwner": "BUSINESS_RUNTIME",
        "jobPermitEnforced": False,
        "maintenanceHandoffEnabled": False,
    }
    assert ready == {**starting, "status": "READY"}


def test_stopping_runtime_cannot_be_marked_ready_again() -> None:
    controller = _controller()
    controller.mark_stopping()

    with pytest.raises(RuntimeError, match="cannot become ready"):
        controller.mark_ready()


@pytest.mark.parametrize("version", [None, "", "x\n1", "x" * 65])
def test_release_version_is_strict(version) -> None:
    with pytest.raises(ValueError, match="release version"):
        BusinessControlController(version)


def test_builder_registers_only_read_only_stage_three_actions(tmp_path) -> None:
    socket_parent = tmp_path / "business"
    socket_parent.mkdir()
    service = build_business_control_service(
        socket_parent / "control.sock",
        release_version="1.2.3",
        allowed_uids={0, 1234},
        socket_gid=5678,
        utc_now=lambda: datetime(2026, 9, 2, tzinfo=timezone.utc),
        instance_uid_factory=lambda: INSTANCE_UID,
    )

    assert set(service.server.actions) == {"HEALTH", "GET_STATUS"}
    assert service.server.allowed_uids == frozenset({0, 1234})
    assert service.server.socket_gid == 5678
    assert service.server.socket_mode == 0o660
