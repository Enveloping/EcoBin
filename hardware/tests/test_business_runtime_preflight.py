from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from system.business_runtime_preflight import (
    BusinessRuntimePreflightError,
    probe_business_identity,
    probe_device_capabilities,
    probe_factory_seal,
    probe_private_directory,
    verify_legacy_runtime_stopped,
    verify_proxy_candidate_health,
    verify_stage_three_health,
)
from updater_agent import UpdaterControlHandler
from updater_store import UpdaterStore


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
        "schemaVersion": 3,
        "jobGateControlExtensionVersion": 1,
        "candidateActivationState": "REQUIRED",
        "stage4CandidateEnabled": False,
        "updatesEnabled": False,
        "jobGateMode": "DISABLED",
        "jobGateState": "LOCKED",
        "jobPermitRpcEnabled": False,
        "maintenanceState": "LOCKED",
        "maintenanceOwnerUid": None,
        "maintenanceType": None,
        "maintenancePhase": None,
        "maintenanceFenceToken": None,
        "reconciliationRequired": False,
        "blockReasonCode": "STAGE4_CANDIDATE_DISABLED",
        "activeJobPermitCount": 0,
        "unreconciledPhysicalActionCount": 0,
        "businessUpdateEnabled": False,
        "mcuUpdateEnabled": False,
        "mcuUpdateCandidateEnabled": False,
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

    for unknown_schema_version in (2, 4, None):
        with pytest.raises(BusinessRuntimePreflightError, match="updater"):
            verify_stage_three_health(
                communication,
                {**updater, "schemaVersion": unknown_schema_version},
            )
    for unknown_extension_version in (0, 2, None):
        with pytest.raises(BusinessRuntimePreflightError, match="updater"):
            verify_stage_three_health(
                communication,
                {
                    **updater,
                    "jobGateControlExtensionVersion": (
                        unknown_extension_version
                    ),
                },
            )
    for unsafe_activation_state in ("ACTIVE", "UNKNOWN", None):
        with pytest.raises(BusinessRuntimePreflightError, match="updater"):
            verify_stage_three_health(
                communication,
                {
                    **updater,
                    "candidateActivationState": unsafe_activation_state,
                },
            )


def test_health_gate_accepts_real_schema_v3_updater_extension(
    tmp_path: Path,
) -> None:
    communication = {
        "component": "COMMUNICATION_AGENT",
        "status": "READY",
        "onenetOwnership": "DISABLED",
        "remoteUpdateRouting": "DISABLED",
    }
    store = UpdaterStore(
        tmp_path / "updater.db",
        release_version="preflight-regression",
    )
    store.initialize()
    try:
        status = UpdaterControlHandler(store).get_status({})

        assert status["schemaVersion"] == 3
        assert status["jobGateControlExtensionVersion"] == 1
        assert status["candidateActivationState"] == "REQUIRED"
        assert status["stage4CandidateEnabled"] is False
        assert status["jobGateState"] == "LOCKED"
        verify_stage_three_health(communication, status)
    finally:
        store.close()


def test_proxy_health_gate_requires_permanent_owners_and_active_job_gate() -> None:
    communication = {
        "component": "COMMUNICATION_AGENT",
        "status": "READY",
        "onenetOwnership": "ENABLED",
        "businessEventIngress": "ENABLED",
        "cloudConnectionState": "DISCONNECTED",
        "remoteUpdateRouting": "DISABLED",
    }
    updater = {
        "component": "DEVICE_UPDATER",
        "status": "READY",
        "schemaVersion": 3,
        "jobGateControlExtensionVersion": 1,
        "candidateActivationState": "ACTIVE",
        "stage4CandidateEnabled": True,
        "jobGateMode": "ENFORCED",
        "jobPermitRpcEnabled": True,
        "jobGateState": "LOCKED",
        "maintenanceState": "LOCKED",
        "businessUpdateEnabled": False,
        "mcuUpdateEnabled": False,
        "mcuUpdateCandidateEnabled": True,
        "privilegedHelperMutationEnabled": True,
        "mcuUpdateCandidate": {
            "schemaVersion": 1,
            "candidateEnabled": True,
            "remoteTriggerEnabled": False,
            "activeUpdate": None,
            "unresolvedPrivilegedActionCount": 0,
        },
    }

    verify_proxy_candidate_health(communication, updater)

    with pytest.raises(BusinessRuntimePreflightError, match="communication"):
        verify_proxy_candidate_health(
            {**communication, "onenetOwnership": "DISABLED"},
            updater,
        )
    with pytest.raises(BusinessRuntimePreflightError, match="updater"):
        verify_proxy_candidate_health(
            communication,
            {**updater, "candidateActivationState": "REQUIRED"},
        )


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership is required")
def test_proxy_preflight_reads_strict_business_identity(tmp_path: Path) -> None:
    identity = tmp_path / "device-identity.json"
    identity.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "assetUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "deviceName": "ECM0-TEST",
                "modelCode": "EC-M0",
                "expectedPortCount": 1,
                "deviceEntryUrl": None,
            }
        ),
        encoding="utf-8",
    )
    identity.chmod(0o600)

    probe_business_identity(str(identity))

    identity.chmod(0o640)
    with pytest.raises(BusinessRuntimePreflightError, match="unsafe"):
        probe_business_identity(str(identity))


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership is required")
def test_preflight_reads_root_published_device_capabilities(
    tmp_path: Path,
) -> None:
    capabilities = tmp_path / "device-capabilities.json"
    capabilities.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "mcuRemoteUpdateCapable": True,
                "factoryReportSha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    capabilities.chmod(0o640)

    probe_device_capabilities(
        str(capabilities),
        expected_owner_uid=os.geteuid(),
        expected_group_gid=os.getegid(),
    )

    capabilities.chmod(0o600)
    with pytest.raises(BusinessRuntimePreflightError, match="ownership or file shape"):
        probe_device_capabilities(
            str(capabilities),
            expected_owner_uid=os.geteuid(),
            expected_group_gid=os.getegid(),
        )


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership is required")
def test_preflight_rejects_invalid_device_capability_content(
    tmp_path: Path,
) -> None:
    capabilities = tmp_path / "device-capabilities.json"
    capabilities.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "mcuRemoteUpdateCapable": "yes",
                "factoryReportSha256": "not-a-digest",
            }
        ),
        encoding="utf-8",
    )
    capabilities.chmod(0o640)

    with pytest.raises(BusinessRuntimePreflightError, match="content is invalid"):
        probe_device_capabilities(
            str(capabilities),
            expected_owner_uid=os.geteuid(),
            expected_group_gid=os.getegid(),
        )


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership is required")
def test_proxy_preflight_reads_root_published_factory_seal(
    tmp_path: Path,
) -> None:
    sealed = tmp_path / "sealed.json"
    sealed.write_text(
        json.dumps({"schemaVersion": 2, "status": "SEALED"}),
        encoding="utf-8",
    )
    sealed.chmod(0o640)

    probe_factory_seal(
        str(sealed),
        expected_owner_uid=os.geteuid(),
        expected_group_gid=os.getegid(),
    )

    sealed.chmod(0o600)
    with pytest.raises(BusinessRuntimePreflightError, match="ownership or file shape"):
        probe_factory_seal(
            str(sealed),
            expected_owner_uid=os.geteuid(),
            expected_group_gid=os.getegid(),
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
    assert "SupplementaryGroups=dialout video ecobin-factory-web" in unit
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
