from __future__ import annotations

import copy
from http import HTTPStatus
import json
import os
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from factory.acceptance_config import AcceptanceConfiguration
from factory.acceptance_service import (
    AcceptanceCommandController,
    AcceptanceCommandError,
    AcceptanceUnixServer,
)
import factory.acceptance_service as acceptance_service


requires_unix_socket = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"),
    reason="Unix-domain sockets are unavailable on this test platform",
)


class FakeExecutor:
    def __init__(self) -> None:
        self.state: dict[str, object] = {
            "schemaVersion": 1,
            "status": "NOT_RUN",
            "phase": "NOT_RUN",
            "revision": 0,
            "checks": {},
            "secret": "must-not-be-projected",
        }
        self.calls: list[tuple[str, object]] = []

    def snapshot(self) -> dict[str, object]:
        return copy.deepcopy(self.state)

    def _bump(self, phase: str) -> None:
        self.state["phase"] = phase
        self.state["revision"] = int(self.state["revision"]) + 1

    def begin_run(self, **values: object) -> dict[str, object]:
        self.calls.append(("begin_run", values))
        self.state.update(
            {
                "status": "RUNNING",
                "imageReleaseId": values["image_release_id"],
                "hardwareConfigDigest": values["hardware_config_digest"],
            }
        )
        self._bump("RUNNING")
        return self.snapshot()

    def run_action(
        self, action: str, *, operator_area_safe_confirmed: bool
    ) -> dict[str, object]:
        self.calls.append(
            (
                "run_action",
                (action, operator_area_safe_confirmed),
            )
        )
        checks = self.state.setdefault("checks", {})
        assert isinstance(checks, dict)
        checks[action.lower()] = {
            "status": "PASSED",
            "resultCode": f"{action}_SAFE_VERIFIED",
        }
        self._bump(f"{action}_PASSED")
        return self.snapshot()

    def __getattr__(self, name: str):
        def action(*args: object, **kwargs: object) -> dict[str, object]:
            self.calls.append((name, (args, kwargs)))
            self._bump(name.upper())
            return self.snapshot()

        return action


def _controller(
    executor: FakeExecutor | None = None,
    *,
    projections: list[dict[str, object]] | None = None,
) -> tuple[AcceptanceCommandController, FakeExecutor]:
    selected = executor or FakeExecutor()
    controller = AcceptanceCommandController(
        selected,  # type: ignore[arg-type]
        AcceptanceConfiguration.from_mapping({}),
        image_release_id="ecobin-zero3-1.0.0",
        boot_id="11111111-2222-3333-4444-555555555555",
        wall_time_trusted=False,
        projection_writer=(projections.append if projections is not None else None),
    )
    return controller, selected


def _request(
    operation: str,
    revision: int,
    parameters: dict[str, object],
) -> dict[str, object]:
    return {
        "operation": operation,
        "expectedRevision": revision,
        "parameters": parameters,
    }


def test_start_requires_explicit_confirmation_and_binds_release_and_config() -> None:
    controller, executor = _controller()

    with pytest.raises(AcceptanceCommandError) as missing:
        controller.execute(_request("START", 0, {"confirmOfflineAcceptance": False}))
    result = controller.execute(
        _request("START", 0, {"confirmOfflineAcceptance": True})
    )

    assert missing.value.code == "OFFLINE_ACCEPTANCE_CONFIRMATION_REQUIRED"
    assert missing.value.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert result["status"] == "RUNNING"
    begin_values = executor.calls[-1][1]
    assert isinstance(begin_values, dict)
    assert begin_values["image_release_id"] == "ecobin-zero3-1.0.0"
    assert begin_values["hardware_config_digest"] == (
        AcceptanceConfiguration.from_mapping({}).digest()
    )


def test_clean_rejects_early_door_confirmation_and_only_sends_safe_action_once() -> None:
    controller, executor = _controller()
    executor.state.update({"status": "RUNNING", "revision": 7})

    with pytest.raises(AcceptanceCommandError) as early:
        controller.execute(
            _request(
                "RUN_CLEAN",
                7,
                {
                    "operatorAreaSafeConfirmed": True,
                    "cleanDoorClosedConfirmed": True,
                },
            )
        )
    result = controller.execute(
        _request("RUN_CLEAN", 7, {"operatorAreaSafeConfirmed": True})
    )

    assert early.value.code == "ACTION_PARAMETERS_INVALID"
    assert executor.calls == [("run_action", ("CLEAN", True))]
    assert result["checks"]["clean"]["status"] == "PASSED"


def test_stale_revision_for_an_already_achieved_action_is_idempotent() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "revision": 9,
            "checks": {
                "delivery": {
                    "status": "PASSED",
                    "resultCode": "DELIVERY_SAFE_VERIFIED",
                }
            },
        }
    )

    result = controller.execute(
        _request("RUN_DELIVERY", 8, {"operatorAreaSafeConfirmed": True})
    )

    assert result["idempotent"] is True
    assert executor.calls == []


def test_stale_revision_for_unachieved_action_conflicts_without_hardware_io() -> None:
    controller, executor = _controller()
    executor.state.update({"status": "RUNNING", "revision": 2})

    with pytest.raises(AcceptanceCommandError) as conflict:
        controller.execute(_request("CHECK_MCU", 1, {}))

    assert conflict.value.code == "ACCEPTANCE_REVISION_CONFLICT"
    assert conflict.value.status == HTTPStatus.CONFLICT
    assert executor.calls == []


def test_concurrent_physical_mutation_returns_busy_instead_of_queueing() -> None:
    controller, executor = _controller()
    assert controller._action_lock.acquire(blocking=False)  # intentional race boundary
    try:
        with pytest.raises(AcceptanceCommandError) as busy:
            controller.execute(
                _request("START", 0, {"confirmOfflineAcceptance": True})
            )
    finally:
        controller._action_lock.release()

    assert busy.value.code == "ACCEPTANCE_EXECUTOR_BUSY"
    assert busy.value.status == HTTPStatus.CONFLICT
    assert executor.calls == []


def test_public_projection_is_allowlisted_and_never_copies_executor_secrets() -> None:
    written: list[dict[str, object]] = []
    controller, executor = _controller(projections=written)
    executor.state.update(
        {
            "status": "RECOVERY_REQUIRED",
            "phase": "ACTION_RECOVERY_REQUIRED",
            "revision": 4,
            "mcuIdentity": {
                "fixedFrameRevision": 2,
                "firmwareVersion": "v1.2.3",
                "firmwareVersionCode": 0x010203,
                "firmwareIdentityHex": "0123456789abcdef",
                "deviceKey": "must-not-pass",
            },
            "recovery": {
                "context": "CLEAN",
                "resultCode": "CLEAN_RESULT_UNCERTAIN",
                "hardwareVerified": False,
                "privateDiagnostic": "must-not-pass",
            },
        }
    )

    projection = controller.projection(idempotent=True)

    assert projection["idempotent"] is True
    assert written[-1]["idempotent"] is False
    encoded = str(written[-1])
    assert "must-not-be-projected" not in encoded
    assert "privateDiagnostic" not in encoded
    # MCU identity is non-secret acceptance evidence, but arbitrary nested keys
    # must not cross the root-to-web privilege boundary.
    assert "deviceKey" not in encoded


def _unix_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AcceptanceUnixServer:
    controller, _executor = _controller()
    monkeypatch.setattr(
        acceptance_service.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=os.getgid()),
    )
    monkeypatch.setattr(acceptance_service.os, "chown", lambda *_args: None)
    return AcceptanceUnixServer(
        tmp_path / "acceptance.sock",
        controller,
        portal_group="ecobin-factory-web",
    )


@requires_unix_socket
def test_slow_unix_client_receives_stable_timeout_and_cannot_hold_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        acceptance_service, "CLIENT_SOCKET_TIMEOUT_SECONDS", 0.05
    )
    server = _unix_server(tmp_path, monkeypatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(1)
        client.connect(str(tmp_path / "acceptance.sock"))

        response = json.loads(client.makefile("rb").readline().decode("ascii"))

        assert response == {
            "error": "IPC_REQUEST_TIMEOUT",
            "httpStatus": HTTPStatus.REQUEST_TIMEOUT.value,
            "ok": False,
        }
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


@requires_unix_socket
def test_unix_executor_rejects_connections_above_fixed_concurrency_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        acceptance_service, "CLIENT_SOCKET_TIMEOUT_SECONDS", 1.0
    )
    server = _unix_server(tmp_path, monkeypatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    clients: list[socket.socket] = []
    overflow: socket.socket | None = None
    try:
        for _ in range(acceptance_service.MAXIMUM_CONCURRENT_CLIENTS):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1)
            client.connect(str(tmp_path / "acceptance.sock"))
            clients.append(client)
        deadline = time.monotonic() + 0.5
        while server._client_slots._value != 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert server._client_slots._value == 0

        overflow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        overflow.settimeout(1)
        overflow.connect(str(tmp_path / "acceptance.sock"))
        response = json.loads(overflow.makefile("rb").readline().decode("ascii"))

        assert response == {
            "error": "IPC_CONCURRENCY_LIMIT",
            "httpStatus": HTTPStatus.SERVICE_UNAVAILABLE.value,
            "ok": False,
        }
    finally:
        if overflow is not None:
            overflow.close()
        for client in clients:
            client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
