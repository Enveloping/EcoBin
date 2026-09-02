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
def test_health_and_status_report_only_truthful_stage3_capabilities(
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
            "schemaVersion": 1,
            "runtimeInstanceUid": status["runtimeInstanceUid"],
            "releaseVersion": "updater-v1",
            "startedAt": status["startedAt"],
            "managementStateSequence": 1,
            "updatesEnabled": False,
            "jobGateMode": "NOT_ENFORCED_STAGE3",
            "maintenanceState": "IDLE",
            "businessUpdateEnabled": False,
            "mcuUpdateEnabled": False,
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
        }
        assert all(
            specification.payload_fields == frozenset()
            for specification in actions.values()
        )
        assert all(
            specification.allowed_uids == frozenset({0})
            for specification in actions.values()
        )
        for action in updater_agent.DISABLED_UPDATE_ACTIONS:
            with pytest.raises(LocalControlActionError) as raised:
                actions[action].handler({})
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
