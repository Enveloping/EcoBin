"""Regression for the live v37 ledger-only cutover, with update ingress closed."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from system import business_runtime_preflight as preflight

from system.business_runtime_preflight import (
    BusinessRuntimePreflightError,
    build_parser,
    verify_proxy_candidate_health,
)


def facts():
    communication = {
        "component": "COMMUNICATION_AGENT", "status": "READY",
        "onenetOwnership": "ENABLED", "businessEventIngress": "ENABLED",
        "cloudConnectionState": "CONNECTED", "remoteUpdateRouting": "DISABLED",
    }
    updater = {
        "component": "DEVICE_UPDATER", "status": "READY", "schemaVersion": 3,
        "jobGateControlExtensionVersion": 1, "candidateActivationState": "ACTIVE",
        "stage4CandidateEnabled": True, "jobGateMode": "ENFORCED",
        "jobPermitRpcEnabled": True, "jobGateState": "OPEN", "maintenanceState": "IDLE",
        "businessUpdateEnabled": False, "mcuUpdateEnabled": False,
        "mcuUpdateCandidateEnabled": False, "businessUpdateCandidateEnabled": True,
        "privilegedHelperMutationEnabled": True,
        "businessUpdateCandidate": {
            "schemaVersion": 1, "businessUpdateCandidateEnabled": True,
            "remoteTriggerEnabled": False, "activeUpdate": None,
        },
    }
    return communication, updater


def test_ledger_only_healthy_posture_is_explicitly_supported():
    communication, updater = facts()
    verify_proxy_candidate_health(communication, updater, ledger_only=True)
    with pytest.raises(BusinessRuntimePreflightError):
        verify_proxy_candidate_health(communication, updater)
    args = build_parser().parse_args([
        "--posture", "ledger-only", "--outside-camera", "/dev/outside",
        "--inside-camera", "/dev/inside",
    ])
    assert args.posture == "ledger-only"


@pytest.mark.parametrize("side,key,value", [
    ("communication", "remoteUpdateRouting", "BUSINESS_RUNTIME_ONLY"),
    ("communication", "onenetOwnership", "DISABLED"),
    ("communication", "businessEventIngress", "DISABLED"),
    ("updater", "candidateActivationState", "REQUIRED"),
    ("updater", "stage4CandidateEnabled", False),
    ("updater", "jobPermitRpcEnabled", False),
    ("updater", "schemaVersion", 2),
    ("updater", "mcuUpdateCandidateEnabled", True),
    ("updater", "mcuUpdateCandidate", {}),
    ("business", "remoteTriggerEnabled", True),
    ("business", "remoteTriggerEnabled", None),
    ("business", "activeUpdate", {"state": "QUEUED"}),
])
def test_ledger_only_rejects_unsafe_or_expanded_postures(side, key, value):
    communication, updater = deepcopy(facts())
    target = {"communication": communication, "updater": updater,
              "business": updater["businessUpdateCandidate"]}[side]
    target[key] = value
    with pytest.raises(BusinessRuntimePreflightError):
        verify_proxy_candidate_health(communication, updater, ledger_only=True)


def test_ledger_only_cli_keeps_identity_seal_and_device_checks(monkeypatch):
    communication, updater = facts()
    called = []
    for name in [
        "verify_runtime_identity", "verify_legacy_runtime_stopped", "probe_device",
        "probe_private_directory", "probe_socket_directory", "probe_device_capabilities",
        "probe_business_identity", "probe_factory_seal",
    ]:
        monkeypatch.setattr(
            preflight, name,
            lambda *args, _name=name, **kwargs: called.append(_name),
        )
    monkeypatch.setattr(preflight, "resolve_camera_roles", lambda **kwargs: SimpleNamespace(
        outside_source="/dev/outside", inside_source="/dev/inside",
    ))
    monkeypatch.setattr(preflight, "request_health", lambda path, protocol:
        communication if protocol == preflight.COMMUNICATION_PROTOCOL else updater)
    assert preflight.main([
        "--posture", "ledger-only", "--outside-camera", "/dev/outside",
        "--inside-camera", "/dev/inside",
    ]) == 0
    assert called.count("probe_device") == 3
    assert called.count("verify_legacy_runtime_stopped") == 2
    assert "probe_business_identity" in called
    assert "probe_factory_seal" in called
