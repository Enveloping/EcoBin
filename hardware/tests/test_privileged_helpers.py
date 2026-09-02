from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from device_management.helpers import business_activation_helper, mcu_flash_helper
from device_management.helpers.privileged_control import (
    HelperAction,
    OneShotPrivilegedHelper,
    resolve_updater_uid,
)
from local_control import LOCAL_PROTOCOL_MAJOR, LOCAL_PROTOCOL_MINOR

HARDWARE_ROOT = Path(__file__).resolve().parents[1]
UNIT_ROOT = HARDWARE_ROOT / "device_management" / "helpers" / "systemd"
UPDATER_UID = 1732


class FakeConnection:
    def __init__(
        self,
        document: dict[str, Any] | None = None,
        *,
        peer_uid: int = UPDATER_UID,
        encoded: bytes | None = None,
    ):
        if encoded is None:
            assert document is not None
            encoded = (
                json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
        self._incoming = encoded
        self.peer_uid = peer_uid
        self.sent = bytearray()
        self.recv_calls = 0

    def recv(self, size: int) -> bytes:
        self.recv_calls += 1
        result = self._incoming[:size]
        self._incoming = self._incoming[size:]
        return result

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)


def request(
    protocol_name: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocolName": protocol_name,
        "protocolMajor": LOCAL_PROTOCOL_MAJOR,
        "protocolMinor": LOCAL_PROTOCOL_MINOR,
        "requestId": str(uuid.uuid4()),
        "action": action,
        "payload": {} if payload is None else payload,
    }


def invoke(
    policy: Any,
    document: dict[str, Any],
    *,
    peer_uid: int = UPDATER_UID,
    actions: dict[str, HelperAction] | None = None,
    mutation_guard: Any = nullcontext,
) -> tuple[dict[str, Any], FakeConnection]:
    connection = FakeConnection(document, peer_uid=peer_uid)
    if actions is None:
        actions = {
            name: HelperAction(lambda _payload: {"called": True}, frozenset())
            for name in policy.primitive_actions
        }
    helper = OneShotPrivilegedHelper(
        policy,
        updater_uid=UPDATER_UID,
        actions=actions,
        peer_uid_reader=lambda candidate: candidate.peer_uid,  # type: ignore[attr-defined]
        mutation_guard=mutation_guard,
    )
    helper.handle(connection)  # type: ignore[arg-type]
    return json.loads(connection.sent.decode("utf-8")), connection


@pytest.mark.parametrize(
    "policy,component",
    [
        (business_activation_helper.POLICY, "BUSINESS_ACTIVATION_HELPER"),
        (mcu_flash_helper.POLICY, "MCU_FLASH_HELPER"),
    ],
)
def test_health_uses_strict_local_control_envelope(policy: Any, component: str) -> None:
    document = request(policy.protocol_name, "HEALTH")

    response, connection = invoke(policy, document)

    assert connection.recv_calls == 1
    assert response == {
        "protocolName": policy.protocol_name,
        "protocolMajor": LOCAL_PROTOCOL_MAJOR,
        "protocolMinor": LOCAL_PROTOCOL_MINOR,
        "requestId": document["requestId"],
        "ok": True,
        "result": {
            "schemaVersion": 1,
            "component": component,
            "status": "READY",
            "stage": 3,
            "mutationEnabled": False,
            "remoteTriggerEnabled": False,
        },
    }


def test_capabilities_only_publish_fixed_business_configuration() -> None:
    response, _connection = invoke(
        business_activation_helper.POLICY,
        request(business_activation_helper.PROTOCOL_NAME, "GET_CAPABILITIES"),
    )

    result = response["result"]
    assert result["mutationEnabled"] is False
    assert result["remoteTriggerEnabled"] is False
    assert result["fixedConfiguration"] == {
        "businessService": "ecobin-hardware.service",
        "businessReleaseRoot": "/opt/ecobin/business/releases",
        "businessCurrentLink": "/opt/ecobin/business/current",
        "businessDatabase": "/var/lib/ecobin/business/edge.db",
        "stagingRoot": "/var/lib/ecobin/updater/staging",
        "snapshotRoot": "/var/lib/ecobin/privileged/business-snapshots",
    }
    encoded = json.dumps(result)
    assert "command" not in encoded.lower()
    assert "credential" not in encoded.lower()


def test_capabilities_publish_only_fixed_mcu_device_pins_and_binaries() -> None:
    response, _connection = invoke(
        mcu_flash_helper.POLICY,
        request(mcu_flash_helper.PROTOCOL_NAME, "GET_CAPABILITIES"),
    )

    assert response["result"]["fixedConfiguration"] == {
        "serialDevice": "/dev/ttyS5",
        "boot0WiringPiPin": 2,
        "resetGateWiringPiPin": 5,
        "safeLevel": 0,
        "gpioBinary": "/usr/bin/gpio",
        "flashBinary": "/usr/bin/stm32flash",
        "firmwareRoot": "/var/lib/ecobin/updater/mcu-firmware",
    }


@pytest.mark.parametrize(
    "policy",
    [business_activation_helper.POLICY, mcu_flash_helper.POLICY],
)
def test_stage3_rejects_every_primitive_before_lock_or_handler(policy: Any) -> None:
    handler_calls: list[str] = []
    guard_calls: list[str] = []

    def mutation_guard() -> Any:
        guard_calls.append("entered")
        return nullcontext()

    actions = {
        name: HelperAction(
            lambda _payload, action_name=name: handler_calls.append(action_name) or {},
            frozenset(),
        )
        for name in policy.primitive_actions
    }

    for action_name in policy.primitive_actions:
        response, _connection = invoke(
            policy,
            request(
                policy.protocol_name,
                action_name,
                {"callerControlled": "/tmp/must-not-be-inspected"},
            ),
            actions=actions,
            mutation_guard=mutation_guard,
        )

        assert response["ok"] is False
        assert response["errorCode"] == "FEATURE_DISABLED"

    assert guard_calls == []
    assert handler_calls == []


def test_non_updater_peer_is_rejected_before_request_is_read() -> None:
    response, connection = invoke(
        business_activation_helper.POLICY,
        request(business_activation_helper.PROTOCOL_NAME, "HEALTH"),
        peer_uid=0,
    )

    assert connection.recv_calls == 0
    assert response["ok"] is False
    assert response["requestId"] is None
    assert response["errorCode"] == "PEER_NOT_AUTHORIZED"


def test_malformed_json_is_a_stable_invalid_request_not_an_internal_error() -> None:
    connection = FakeConnection(encoded=b'{"not":"closed"\n')
    actions = {
        name: HelperAction(lambda _payload: {}, frozenset())
        for name in business_activation_helper.POLICY.primitive_actions
    }
    helper = OneShotPrivilegedHelper(
        business_activation_helper.POLICY,
        updater_uid=UPDATER_UID,
        actions=actions,
        peer_uid_reader=lambda candidate: candidate.peer_uid,  # type: ignore[attr-defined]
        mutation_guard=nullcontext,
    )

    helper.handle(connection)  # type: ignore[arg-type]

    response = json.loads(connection.sent.decode("utf-8"))
    assert response["ok"] is False
    assert response["requestId"] is None
    assert response["errorCode"] == "REQUEST_INVALID"
    assert response["message"] == "local helper request is invalid"


def test_updater_uid_is_resolved_by_fixed_account_name_and_missing_account_fails() -> None:
    seen: list[str] = []

    def found(name: str) -> SimpleNamespace:
        seen.append(name)
        return SimpleNamespace(pw_uid=UPDATER_UID)

    assert resolve_updater_uid(found) == UPDATER_UID
    assert seen == ["ecobin-updater"]

    def missing(_name: str) -> Any:
        raise KeyError

    with pytest.raises(RuntimeError, match="required ecobin-updater account"):
        resolve_updater_uid(missing)


@pytest.mark.parametrize(
    "socket_name,service_name,listen_path",
    [
        (
            "ecobin-business-activation-helper.socket",
            "ecobin-business-activation-helper@.service",
            "/run/ecobin/privileged/business-activation.sock",
        ),
        (
            "ecobin-mcu-flash-helper.socket",
            "ecobin-mcu-flash-helper@.service",
            "/run/ecobin/privileged/mcu-flash.sock",
        ),
    ],
)
def test_socket_activation_is_one_process_per_connection(
    socket_name: str,
    service_name: str,
    listen_path: str,
) -> None:
    socket_unit = (UNIT_ROOT / socket_name).read_text(encoding="utf-8")
    service_unit = (UNIT_ROOT / service_name).read_text(encoding="utf-8")

    assert f"ListenStream={listen_path}" in socket_unit
    assert "Accept=yes" in socket_unit
    assert "Service=" not in socket_unit
    assert service_name == socket_name.removesuffix(".socket") + "@.service"
    assert "SocketUser=root" in socket_unit
    assert "SocketGroup=ecobin-privileged-helper-ipc" in socket_unit
    assert "SocketGroup=ecobin-updater-ipc" not in socket_unit
    assert "SocketMode=0660" in socket_unit
    assert "DirectoryMode=0750" in socket_unit
    assert "MaxConnections=1" in socket_unit
    assert "StandardInput=socket" in service_unit
    assert "StandardOutput=socket" in service_unit
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in service_unit
    assert "Environment=PYTHONUNBUFFERED=1" in service_unit
    assert "Restart=" not in service_unit
    assert "[Install]" not in service_unit
    assert "[Install]" not in socket_unit
    assert "WantedBy=sockets.target" not in socket_unit


@pytest.mark.parametrize(
    "service_name",
    [
        "ecobin-business-activation-helper@.service",
        "ecobin-mcu-flash-helper@.service",
    ],
)
def test_root_helper_units_are_hardened_and_offline(service_name: str) -> None:
    unit = (UNIT_ROOT / service_name).read_text(encoding="utf-8")

    for directive in (
        "User=root",
        "Group=root",
        "NoNewPrivileges=yes",
        "PrivateNetwork=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "ProtectControlGroups=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelTunables=yes",
        "RestrictAddressFamilies=AF_UNIX",
        "IPAddressDeny=any",
        "RestrictNamespaces=yes",
        "RestrictSUIDSGID=yes",
        "MemoryDenyWriteExecute=yes",
        "SystemCallFilter=@system-service",
    ):
        assert directive in unit
    assert "ReadWritePaths=/run/ecobin/privileged/mutation.lock" in unit


def test_business_helper_has_no_device_access_and_mcu_helper_has_only_fixed_devices() -> None:
    business = (
        UNIT_ROOT / "ecobin-business-activation-helper@.service"
    ).read_text(encoding="utf-8")
    mcu = (UNIT_ROOT / "ecobin-mcu-flash-helper@.service").read_text(
        encoding="utf-8"
    )

    assert "PrivateDevices=yes" in business
    assert "DevicePolicy=closed" in business
    assert "DeviceAllow=" not in business
    assert "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER" in business
    assert (
        "ReadWritePaths=/var/lib/ecobin/privileged/business-snapshots"
        in business
    )
    assert "/var/lib/ecobin/updater/snapshots" not in business
    assert "DevicePolicy=closed" in mcu
    assert "DeviceAllow=/dev/ttyS5 rw" in mcu
    assert "DeviceAllow=/dev/mem rw" in mcu
    assert "DeviceAllow=/dev/gpiomem rw" in mcu
    assert "CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SYS_RAWIO" in mcu
    assert (
        "InaccessiblePaths=-/var/lib/ecobin/privileged/business-snapshots"
        in mcu
    )


@pytest.mark.parametrize(
    "source_name",
    [
        "privileged_control.py",
        "business_activation_helper.py",
        "mcu_flash_helper.py",
    ],
)
def test_helper_sources_have_no_command_execution_or_network_client(
    source_name: str,
) -> None:
    source = (
        HARDWARE_ROOT / "device_management" / "helpers" / source_name
    ).read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "os.system" not in source
    assert "shell=True" not in source
    assert "AF_INET" not in source
    assert "create_connection" not in source
