from __future__ import annotations

import os
from pathlib import Path

import pytest

from system.business_runtime_preflight import (
    BusinessRuntimePreflightError,
    probe_private_directory,
    verify_legacy_runtime_stopped,
    verify_stage_three_health,
)


HARDWARE = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership is required")
def test_private_directory_probe_writes_and_cleans_up(tmp_path: Path) -> None:
    probe_private_directory(str(tmp_path), "test state directory")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership is required")
def test_private_directory_probe_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"host does not permit symlink fixtures: {error}")

    with pytest.raises(BusinessRuntimePreflightError, match="real directory"):
        probe_private_directory(str(link), "test state directory")


def test_health_gate_requires_truthful_disabled_stage_three_posture() -> None:
    communication = {
        "component": "COMMUNICATION_AGENT",
        "status": "READY",
        "onenetOwnership": "DISABLED",
        "remoteUpdateRouting": "DISABLED",
    }
    updater = {
        "component": "DEVICE_UPDATER",
        "status": "READY",
        "schemaVersion": 2,
        "stage4CandidateEnabled": False,
        "updatesEnabled": False,
        "jobGateMode": "DISABLED",
        "jobGateState": "LOCKED",
        "jobPermitRpcEnabled": False,
        "maintenanceState": "LOCKED",
        "maintenanceOwnerUid": None,
        "maintenanceType": None,
        "maintenanceFenceToken": None,
        "reconciliationRequired": False,
        "blockReasonCode": "STAGE4_CANDIDATE_DISABLED",
        "activeJobPermitCount": 0,
        "unreconciledPhysicalActionCount": 0,
        "businessUpdateEnabled": False,
        "mcuUpdateEnabled": False,
        "privilegedHelperMutationEnabled": False,
    }

    verify_stage_three_health(communication, updater)

    with pytest.raises(BusinessRuntimePreflightError, match="communication"):
        verify_stage_three_health(
            {**communication, "onenetOwnership": "COMMUNICATION_AGENT"},
            updater,
        )
    with pytest.raises(BusinessRuntimePreflightError, match="updater"):
        verify_stage_three_health(
            communication,
            {**updater, "updatesEnabled": True},
        )


def test_preflight_refuses_to_probe_while_business_runtime_is_active() -> None:
    class Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def runner(_argv, **kwargs):
        assert kwargs["timeout"] == 5
        assert kwargs["check"] is False
        return Result(0)

    with pytest.raises(BusinessRuntimePreflightError, match="must be stopped"):
        verify_legacy_runtime_stopped(runner=runner)

    verify_legacy_runtime_stopped(
        runner=lambda _argv, **_kwargs: Result(3)
    )

    with pytest.raises(BusinessRuntimePreflightError, match="cannot be confirmed"):
        verify_legacy_runtime_stopped(
            runner=lambda _argv, **_kwargs: Result(4)
        )


def test_systemd_preflight_uses_real_non_root_resource_boundary() -> None:
    unit = (HARDWARE / "ecobin-business-permission-preflight.service").read_text(
        encoding="utf-8"
    )

    assert "User=ecobin-business" in unit
    assert "Group=ecobin-business" in unit
    assert "SupplementaryGroups=dialout video" in unit
    assert "WorkingDirectory=/opt/ecobin/hardware/current/app" in unit
    assert (
        "ExecStart=/opt/ecobin/hardware/current/.venv/bin/python "
        "/usr/lib/ecobin/business_runtime_preflight.py"
    ) in unit
    assert "DeviceAllow=/dev/ttyS5 rw" in unit
    assert "DeviceAllow=char-video4linux rw" in unit
    assert "Requires=ecobin-communication.service ecobin-updater.service" in unit
    assert "PrivateNetwork=yes" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "ReadWritePaths=/var/lib/ecobin/business /run/ecobin/business" in unit
    assert "WantedBy=" not in unit
    assert "User=root" not in unit


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership bits are required")
def test_private_directory_probe_rejects_group_writable_directory(
    tmp_path: Path,
) -> None:
    os.chmod(tmp_path, 0o770)
    with pytest.raises(BusinessRuntimePreflightError, match="another account"):
        probe_private_directory(str(tmp_path), "test state directory")
