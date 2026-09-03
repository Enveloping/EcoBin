from __future__ import annotations

import argparse
import os
import socket
import sqlite3
from pathlib import Path

import pytest

import updater_agent
from local_control import (
    LocalControlActionError,
    LocalControlClient,
    LocalControlRemoteError,
)
from updater_store import UpdaterStore


requires_unix_socket = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"),
    reason="Unix domain sockets are unavailable",
)


def _uid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012x}"


def _activate_candidate(store: UpdaterStore) -> None:
    status = store.get_status()
    store.activate_stage4_job_gate(
        {
            "operationUid": _uid(900),
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )


def _build_agent(tmp_path: Path) -> tuple[
    updater_agent.UpdaterAgent,
    LocalControlClient,
]:
    socket_path = tmp_path / "updater.sock"
    args = argparse.Namespace(
        state=str(tmp_path / "updater.db"),
        socket=str(socket_path),
        release_version="updater-v1",
        allowed_uid={os.getuid()} if hasattr(os, "getuid") else {0},
        allowed_user=None,
        socket_group=None,
    )
    agent = updater_agent.build_agent(args)
    client = LocalControlClient(
        socket_path,
        protocol_name=updater_agent.UPDATER_LOCAL_PROTOCOL_NAME,
    )
    return agent, client


@requires_unix_socket
def test_health_and_status_report_truthful_default_locked_capabilities(
    tmp_path: Path,
) -> None:
    agent, client = _build_agent(tmp_path)
    try:
        agent.start()
        health = client.request("HEALTH", {})
        status = client.request("GET_STATUS", {})

        assert health == status
        assert status == {
            "component": "DEVICE_UPDATER",
            "status": "READY",
            "schemaVersion": 3,
            "jobGateControlExtensionVersion": 1,
            "candidateActivationState": "REQUIRED",
            "runtimeInstanceUid": status["runtimeInstanceUid"],
            "releaseVersion": "updater-v1",
            "startedAt": status["startedAt"],
            "managementStateSequence": 1,
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
            "localProtocolName": "ecobin.updater.control",
            "localProtocolMajor": 1,
            "localProtocolMinor": 0,
        }
    finally:
        agent.stop()


@requires_unix_socket
def test_disabled_updates_return_stable_error_without_durable_side_effect(
    tmp_path: Path,
) -> None:
    agent, client = _build_agent(tmp_path)
    path = tmp_path / "updater.db"
    try:
        agent.start()
        with sqlite3.connect(path) as connection:
            before = (
                connection.execute(
                    "SELECT COUNT(*) FROM updater_runtime_instance"
                ).fetchone()[0],
                connection.execute(
                    """SELECT management_state_sequence, updates_enabled,
                              job_gate_mode, maintenance_state,
                              business_update_enabled, mcu_update_enabled,
                              created_at, updated_at
                       FROM updater_management_state"""
                ).fetchall(),
            )

        for action in sorted(updater_agent.DISABLED_UPDATE_ACTIONS):
            with pytest.raises(LocalControlRemoteError) as raised:
                client.request(action, {})
            assert raised.value.code == "FEATURE_DISABLED"

        with sqlite3.connect(path) as connection:
            after = (
                connection.execute(
                    "SELECT COUNT(*) FROM updater_runtime_instance"
                ).fetchone()[0],
                connection.execute(
                    """SELECT management_state_sequence, updates_enabled,
                              job_gate_mode, maintenance_state,
                              business_update_enabled, mcu_update_enabled,
                              created_at, updated_at
                       FROM updater_management_state"""
                ).fetchall(),
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert after == before
        assert not any("update_job" in table for table in tables)
    finally:
        agent.stop()


def test_disabled_action_handlers_have_no_state_side_effect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = UpdaterStore(path, release_version="updater-v1")
    store.initialize()
    try:
        actions = updater_agent.build_control_actions(
            updater_agent.UpdaterControlHandler(store),
            allowed_uids={0},
        )
        before = store.get_status()

        assert set(actions) == {
            "HEALTH",
            "GET_STATUS",
            *updater_agent.DISABLED_UPDATE_ACTIONS,
            *updater_agent.ROOT_JOB_GATE_ACTION_FIELDS,
        }
        assert all(
            specification.payload_fields == frozenset()
            for action, specification in actions.items()
            if action not in {
                "ACTIVATE_STAGE4_JOB_GATE",
                "LOCK_STAGE4_JOB_GATE",
            }
        )
        for action, fields in (
            updater_agent.ROOT_JOB_GATE_ACTION_FIELDS.items()
        ):
            assert actions[action].payload_fields == fields
            assert actions[action].allowed_uids == frozenset({0})
        for action in updater_agent.DISABLED_UPDATE_ACTIONS:
            with pytest.raises(LocalControlActionError) as raised:
                actions[action].handler({})
            assert raised.value.code == "FEATURE_DISABLED"
        assert actions["GET_STAGE4_RECONCILIATION_STATUS"].handler({})[
            "stage4CandidateEnabled"
        ] is False
        gate_payload = {
            "operationUid": _uid(80),
            "evidenceDigest": "8" * 64,
            "expectedManagementStateSequence": before[
                "managementStateSequence"
            ],
        }
        for action in (
            "ACTIVATE_STAGE4_JOB_GATE",
            "LOCK_STAGE4_JOB_GATE",
        ):
            with pytest.raises(LocalControlActionError) as raised:
                actions[action].handler(gate_payload)
            assert raised.value.code == "FEATURE_DISABLED"

        assert store.get_status() == before
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM updater_runtime_instance"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM updater_management_state"
            ).fetchone()[0] == 1
    finally:
        store.close()


def test_candidate_actions_use_exact_payloads_and_business_only_uid(
    tmp_path: Path,
) -> None:
    store = UpdaterStore(
        tmp_path / "updater.db",
        release_version="updater-v2",
        enable_stage4_candidate=True,
    )
    store.initialize()
    try:
        actions = updater_agent.build_control_actions(
            updater_agent.UpdaterControlHandler(store),
            allowed_uids={0, 3101, 3102},
            business_uids={3102},
            enable_stage4_candidate=True,
        )

        assert set(updater_agent.JOB_ACTION_FIELDS).issubset(actions)
        assert "AUTHORIZE_PHYSICAL_ACTION" not in actions
        assert "TRANSITION_JOB_GATE" not in actions
        assert {
            "PREPARE_PHYSICAL_ACTION",
            "ARM_PHYSICAL_ACTION",
            "CANCEL_PREPARED_PHYSICAL_ACTION",
            "ABORT_PHYSICAL_ACTION_DISPATCH",
            "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT",
            "GET_PHYSICAL_ACTION",
            "CONFIRM_PHYSICAL_ACTION",
        }.issubset(actions)
        assert actions["PREPARE_PHYSICAL_ACTION"].payload_fields == frozenset(
            {
                "actionUid",
                "permitUid",
                "workUid",
                "commandUid",
                "actionKey",
                "actionKind",
                "actionDigestSha256",
                "dispatchAttemptToken",
            }
        )
        assert actions[
            "CANCEL_PREPARED_PHYSICAL_ACTION"
        ].payload_fields == frozenset(
            {
                "actionUid",
                "receiptUid",
                "dispatchAttemptToken",
                "evidenceDigestSha256",
            }
        )
        for action, expected_fields in updater_agent.JOB_ACTION_FIELDS.items():
            assert actions[action].payload_fields == expected_fields
            assert actions[action].allowed_uids == frozenset({3102})
        for action, expected_fields in (
            updater_agent.ROOT_JOB_GATE_ACTION_FIELDS.items()
        ):
            assert actions[action].payload_fields == expected_fields
            assert actions[action].allowed_uids == frozenset({0})
        assert actions["HEALTH"].allowed_uids == frozenset(
            {0, 3101, 3102}
        )
        assert actions["GET_STATUS"].allowed_uids == frozenset(
            {0, 3101, 3102}
        )
        for action in updater_agent.DISABLED_UPDATE_ACTIONS:
            with pytest.raises(LocalControlActionError) as raised:
                actions[action].handler({})
            assert raised.value.code == "FEATURE_DISABLED"
        assert store.get_status()["jobGateState"] == "LOCKED"
        assert (
            store.get_status()["blockReasonCode"]
            == "STAGE4_ACTIVATION_REQUIRED"
        )
        assert store.get_status()["updatesEnabled"] is False
    finally:
        store.close()


def test_agent_rejects_edge_rollback_token_takeover_and_cancellation(
    tmp_path: Path,
) -> None:
    store = UpdaterStore(
        tmp_path / "updater.db",
        release_version="updater-v3",
        enable_stage4_candidate=True,
    )
    store.initialize()
    try:
        _activate_candidate(store)
        store.request_job_permit(
            {
                "permitUid": _uid(1),
                "workUid": _uid(2),
                "commandUid": _uid(3),
                "workType": "DELIVERY",
                "requestDigestSha256": "a" * 64,
            }
        )
        store.begin_job(
            {
                "permitUid": _uid(1),
                "beginUid": _uid(4),
                "permitDigestSha256": "a" * 64,
            }
        )
        actions = updater_agent.build_control_actions(
            updater_agent.UpdaterControlHandler(store),
            allowed_uids={0, 3102},
            business_uids={3102},
            enable_stage4_candidate=True,
        )
        original = {
            "actionUid": _uid(5),
            "permitUid": _uid(1),
            "workUid": _uid(2),
            "commandUid": _uid(3),
            "actionKey": "delivery.door.unlock.1",
            "actionKind": "DELIVERY_DOOR_UNLOCK",
            "actionDigestSha256": "c" * 64,
            "dispatchAttemptToken": "L" * 43,
        }
        created = actions["PREPARE_PHYSICAL_ACTION"].handler(original)
        takeover = actions["PREPARE_PHYSICAL_ACTION"].handler(
            {**original, "dispatchAttemptToken": "M" * 43}
        )
        assert created["disposition"] == "ACCEPTED"
        assert takeover["disposition"] == "DENIED"
        assert takeover["state"] == "PREPARED"
        assert takeover["mayExecute"] is False

        with pytest.raises(LocalControlActionError) as cancelled:
            actions["CANCEL_PREPARED_PHYSICAL_ACTION"].handler(
                {
                    "actionUid": _uid(5),
                    "receiptUid": _uid(6),
                    "dispatchAttemptToken": "M" * 43,
                    "evidenceDigestSha256": "d" * 64,
                }
            )
        assert cancelled.value.code == "PHYSICAL_ACTION_CANCEL_DENIED"
        retained = actions["GET_PHYSICAL_ACTION"].handler(
            {"actionUid": _uid(5)}
        )
        assert retained["state"] == "PREPARED"
        assert retained["confirmedOutcome"] is None
        assert "L" * 43 not in repr(retained)
        assert "M" * 43 not in repr(retained)
    finally:
        store.close()


def test_candidate_requires_non_root_business_identity() -> None:
    assert updater_agent.resolve_role_uids(
        [3102],
        None,
        role="business",
    ) == [3102]
    assert updater_agent.resolve_role_uids(
        None,
        "ecobin-business",
        role="business",
        user_lookup=lambda _name: 3102,
    ) == [3102]
    with pytest.raises(ValueError, match="positive and non-root"):
        updater_agent.resolve_role_uids(
            [0],
            None,
            role="business",
        )
    with pytest.raises(ValueError, match="non-empty business UID"):
        class ReadOnlyHandler:
            @staticmethod
            def get_status(_payload):
                return {}

            @staticmethod
            def reject_disabled_update(_payload):
                return {}

            @staticmethod
            def get_stage4_reconciliation_status(_payload):
                return {}

            @staticmethod
            def activate_stage4_job_gate(_payload):
                return {}

            @staticmethod
            def lock_stage4_job_gate(_payload):
                return {}

        updater_agent.build_control_actions(
            ReadOnlyHandler(),  # type: ignore[arg-type]
            allowed_uids={0, 3101},
            enable_stage4_candidate=True,
        )


@requires_unix_socket
def test_updater_rejects_bad_payload_and_unknown_action(
    tmp_path: Path,
) -> None:
    agent, client = _build_agent(tmp_path)
    try:
        agent.start()
        with pytest.raises(LocalControlRemoteError) as bad_health:
            client.request("HEALTH", {"unexpected": True})
        assert bad_health.value.code == "REQUEST_INVALID"

        with pytest.raises(LocalControlRemoteError) as bad_disabled:
            client.request(
                "START_BUSINESS_UPDATE",
                {"updateUid": "not-accepted-in-stage-three"},
            )
        assert bad_disabled.value.code == "REQUEST_INVALID"

        with pytest.raises(LocalControlRemoteError) as unknown:
            client.request("RUN_ARBITRARY_COMMAND", {})
        assert unknown.value.code == "REQUEST_INVALID"
    finally:
        agent.stop()


def test_cli_resolves_explicit_user_and_uid_allowlist() -> None:
    looked_up: list[str] = []

    def lookup(username: str) -> int:
        looked_up.append(username)
        return {
            "ecobin-communication": 3101,
            "ecobin-business": 3102,
        }[username]

    assert updater_agent.resolve_allowed_uids(
        [0, 3101],
        ["ecobin-communication", "ecobin-business"],
        user_lookup=lookup,
    ) == [0, 3101, 3102]
    assert looked_up == ["ecobin-communication", "ecobin-business"]
    assert updater_agent.resolve_allowed_uids(
        None,
        None,
        user_lookup=lookup,
    ) == [0]


def test_cli_fails_when_an_allowed_user_or_socket_group_is_missing() -> None:
    def missing(_name: str) -> int:
        raise KeyError

    with pytest.raises(ValueError, match="allowed-user does not exist"):
        updater_agent.resolve_allowed_uids(
            None,
            ["missing-user"],
            user_lookup=missing,
        )
    with pytest.raises(ValueError, match="socket-group does not exist"):
        updater_agent.resolve_socket_gid(
            "missing-group",
            group_lookup=missing,
        )
    assert updater_agent.resolve_socket_gid(
        "ecobin-updater-ipc",
        group_lookup=lambda _name: 3201,
    ) == 3201


def test_systemd_ready_uses_abstract_notify_socket(monkeypatch) -> None:
    sent: list[tuple[bytes, str]] = []

    class Notifier:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendto(self, message: bytes, address: str) -> None:
            sent.append((message, address))

    monkeypatch.setenv("NOTIFY_SOCKET", "@ecobin-updater-ready")
    monkeypatch.setattr(
        updater_agent.socket,
        "AF_UNIX",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        updater_agent.socket,
        "socket",
        lambda *_args: Notifier(),
    )

    updater_agent.notify_systemd_ready()

    assert sent == [(b"READY=1", "\0ecobin-updater-ready")]


def test_agent_wait_surfaces_background_control_server_failure() -> None:
    class Store:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FailedServer:
        def __init__(self) -> None:
            self.failure = OSError("listener failed")
            self.stopped = False

        def start(self) -> None:
            return None

        def wait_stopped(self, timeout_seconds: float) -> bool:
            assert timeout_seconds == 0.25
            return True

        def stop(self) -> None:
            self.stopped = True

    store = Store()
    server = FailedServer()
    agent = updater_agent.UpdaterAgent(store, server)
    agent.start()

    with pytest.raises(
        RuntimeError,
        match="control server failed",
    ) as raised:
        agent.wait()
    assert isinstance(raised.value.__cause__, OSError)

    agent.stop()
    assert store.closed is True
    assert server.stopped is True


def test_cli_defaults_match_permanent_updater_paths(monkeypatch) -> None:
    monkeypatch.delenv("ECOBIN_UPDATER_STATE_PATH", raising=False)
    monkeypatch.delenv("ECOBIN_UPDATER_SOCKET", raising=False)
    monkeypatch.delenv("ECOBIN_UPDATER_RELEASE_VERSION", raising=False)
    monkeypatch.delenv("ECOBIN_UPDATER_SOCKET_GROUP", raising=False)

    args = updater_agent.build_parser().parse_args([])

    assert args.state == "/var/lib/ecobin/updater/updater.db"
    assert args.socket == "/run/ecobin/updater/control.sock"
    assert args.release_version is None
    assert args.allowed_uid is None
    assert args.allowed_user is None
    assert args.socket_group is None
    assert args.enable_stage4_candidate is False
    assert args.business_uid is None
    assert args.business_user is None
