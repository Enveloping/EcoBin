#!/usr/bin/env python3
"""Exercise the stage-three permanent agents without installing them."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parent.parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import communication_agent  # noqa: E402
import updater_agent  # noqa: E402
from local_control import LocalControlClient, LocalControlRemoteError  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _require_private_regular_file(path: Path) -> None:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"expected a regular file: {path}")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError(f"expected mode 0600: {path}")
    if info.st_uid != os.getuid():
        raise RuntimeError(f"expected current-user ownership: {path}")


def _exercise_communication(work_root: Path) -> dict[str, Any]:
    state_path = work_root / "communication.db"
    socket_path = work_root / "communication.sock"
    runtime_uids: list[str] = []
    final_status: dict[str, Any] = {}

    for expected_start_count in (1, 2):
        args = argparse.Namespace(
            state=str(state_path),
            socket=str(socket_path),
            release_version="communication-shadow-v15",
            allowed_uid=[os.getuid()],
            allowed_user=None,
            socket_group=None,
        )
        agent = communication_agent.build_agent(args)
        client = LocalControlClient(
            socket_path,
            protocol_name=communication_agent.COMMUNICATION_PROTOCOL_NAME,
        )
        try:
            agent.start()
            health = client.request("HEALTH", {})
            status = client.request("GET_STATUS", {})
            _require(
                health["component"] == "COMMUNICATION_AGENT",
                "communication component identity differs",
            )
            _require(health["status"] == "READY", "communication is not ready")
            _require(
                health["onenetOwnership"] == "DISABLED",
                "communication unexpectedly owns OneNet",
            )
            _require(
                health["remoteUpdateRouting"] == "DISABLED",
                "communication unexpectedly routes updates",
            )
            _require(status["schemaVersion"] == 2, "communication schema differs")
            _require(
                status["processStartCount"] == expected_start_count,
                "communication lifecycle count differs",
            )
            _require(
                status["runtimeInstanceUid"] == health["runtimeInstanceUid"],
                "communication health and status identities differ",
            )
            runtime_uids.append(status["runtimeInstanceUid"])
            final_status = status
        finally:
            agent.stop()

    _require(
        len(set(runtime_uids)) == 2,
        "communication lifecycle did not create a new runtime identity",
    )
    _require_private_regular_file(state_path)
    return {
        "schemaVersion": final_status["schemaVersion"],
        "processStartCount": final_status["processStartCount"],
        "inProcessLifecycleRuns": 2,
        "secondLifecycleCreatedNewRuntimeIdentity": True,
        "onenetOwnership": final_status["onenetOwnership"],
        "remoteUpdateRouting": final_status["remoteUpdateRouting"],
        "databaseMode": "0600",
        "databaseIntegrity": _database_integrity(state_path),
    }


def _exercise_updater(work_root: Path) -> dict[str, Any]:
    state_path = work_root / "updater.db"
    socket_path = work_root / "updater.sock"
    runtime_uids: list[str] = []
    final_status: dict[str, Any] = {}
    rejected_actions: dict[str, str] = {}

    for attempt in (1, 2):
        args = argparse.Namespace(
            state=str(state_path),
            socket=str(socket_path),
            release_version="updater-shadow-v15",
            allowed_uid=[os.getuid()],
            allowed_user=None,
            socket_group=None,
            enable_stage4_candidate=False,
            business_uid=None,
            business_user=None,
        )
        agent = updater_agent.build_agent(args)
        client = LocalControlClient(
            socket_path,
            protocol_name=updater_agent.UPDATER_LOCAL_PROTOCOL_NAME,
        )
        try:
            agent.start()
            health = client.request("HEALTH", {})
            status = client.request("GET_STATUS", {})
            _require(health == status, "updater health and status differ")
            _require(
                status["component"] == "DEVICE_UPDATER",
                "updater component identity differs",
            )
            _require(status["status"] == "READY", "updater is not ready")
            _require(status["schemaVersion"] == 3, "updater schema differs")
            _require(
                status["jobGateControlExtensionVersion"] == 1,
                "updater job-gate extension differs",
            )
            _require(
                status["stage4CandidateEnabled"] is False,
                "stage-four candidate unexpectedly enabled",
            )
            _require(
                status["candidateActivationState"] == "REQUIRED",
                "disabled candidate retained a prior activation",
            )
            _require(
                status["updatesEnabled"] is False,
                "updates unexpectedly enabled",
            )
            _require(
                status["businessUpdateEnabled"] is False,
                "business update unexpectedly enabled",
            )
            _require(
                status["mcuUpdateEnabled"] is False,
                "MCU update unexpectedly enabled",
            )
            _require(
                status["privilegedHelperMutationEnabled"] is False,
                "privileged helper mutation unexpectedly enabled",
            )
            _require(
                status["jobGateMode"] == "DISABLED",
                "job gate mode is not disabled",
            )
            _require(
                status["jobGateState"] == "LOCKED",
                "job gate is not locked",
            )
            _require(
                status["maintenanceState"] == "LOCKED",
                "maintenance state is not locked",
            )
            runtime_uids.append(status["runtimeInstanceUid"])
            final_status = status

            if attempt == 1:
                for action in (
                    "START_BUSINESS_UPDATE",
                    "START_MCU_UPDATE",
                    "CANCEL_UPDATE",
                    "RENEW_DOWNLOAD_AUTHORIZATION",
                ):
                    try:
                        client.request(action, {})
                    except LocalControlRemoteError as error:
                        _require(
                            error.code == "FEATURE_DISABLED",
                            f"{action} returned an unexpected error",
                        )
                        rejected_actions[action] = error.code
                    else:
                        raise AssertionError(f"unsafe action unexpectedly succeeded: {action}")
        finally:
            agent.stop()

    _require(
        len(set(runtime_uids)) == 2,
        "updater lifecycle did not create a new runtime identity",
    )
    _require_private_regular_file(state_path)
    return {
        "schemaVersion": final_status["schemaVersion"],
        "inProcessLifecycleRuns": 2,
        "secondLifecycleCreatedNewRuntimeIdentity": True,
        "stage4CandidateEnabled": final_status["stage4CandidateEnabled"],
        "candidateActivationState": final_status[
            "candidateActivationState"
        ],
        "updatesEnabled": final_status["updatesEnabled"],
        "jobGateMode": final_status["jobGateMode"],
        "jobGateState": final_status["jobGateState"],
        "privilegedHelperMutationEnabled": final_status[
            "privilegedHelperMutationEnabled"
        ],
        "rejectedActions": rejected_actions,
        "databaseMode": "0600",
        "databaseIntegrity": _database_integrity(state_path),
    }


def _database_integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    finally:
        connection.close()
    _require(rows == [("ok",)], f"database integrity check failed: {path}")
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", required=True)
    args = parser.parse_args()
    work_root = Path(args.work_root).resolve()

    if os.name != "posix" or not hasattr(os, "getuid"):
        raise RuntimeError("this probe requires Linux Unix-domain sockets")
    if sys.flags.optimize != 0:
        raise RuntimeError("shadow probe refuses optimized Python mode")
    if os.getuid() == 0:
        raise RuntimeError("shadow probe must run as an unprivileged user")
    home = Path.home().resolve()
    if work_root == home or home not in work_root.parents:
        raise RuntimeError("work root must be a dedicated directory below the user home")
    if work_root.exists():
        raise RuntimeError("work root already exists; refusing to reuse prior evidence")
    work_root.mkdir(mode=0o700, parents=True)

    result = {
        "result": "PASS",
        "uid": os.getuid(),
        "python": sys.version.split()[0],
        "workRoot": str(work_root),
        "communication": _exercise_communication(work_root),
        "updater": _exercise_updater(work_root),
    }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
