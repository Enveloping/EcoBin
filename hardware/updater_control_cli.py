#!/usr/bin/env python3
"""Root-only local control for stage-four job-gate activation and locking."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from local_control import (
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
)


DEFAULT_SOCKET_PATH = "/run/ecobin/updater/control.sock"
UPDATER_LOCAL_PROTOCOL_NAME = "ecobin.updater.control"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--socket",
        default=os.getenv("ECOBIN_UPDATER_SOCKET", DEFAULT_SOCKET_PATH),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="read the persisted job-gate status")
    for name, help_text in (
        ("activate", "activate the current candidate cycle with evidence"),
        ("lock", "immediately apply the durable safety lock"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--operation-uid", required=True)
        command.add_argument("--evidence-digest", required=True)
        command.add_argument(
            "--expected-management-sequence",
            required=True,
            type=int,
        )
    return parser


def _effective_uid() -> int | None:
    getter = getattr(os, "geteuid", None)
    return getter() if getter is not None else None


def _request_for(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.command == "status":
        return "GET_STAGE4_RECONCILIATION_STATUS", {}
    payload = {
        "operationUid": args.operation_uid,
        "evidenceDigest": args.evidence_digest,
        "expectedManagementStateSequence": (
            args.expected_management_sequence
        ),
    }
    if args.command == "activate":
        return "ACTIVATE_STAGE4_JOB_GATE", payload
    if args.command == "lock":
        return "LOCK_STAGE4_JOB_GATE", payload
    raise AssertionError("unsupported updater control command")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if _effective_uid() != 0:
        print(
            json.dumps(
                {
                    "errorCode": "ROOT_REQUIRED",
                    "message": "this updater control command requires UID 0",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    action, payload = _request_for(args)
    client = LocalControlClient(
        args.socket,
        protocol_name=UPDATER_LOCAL_PROTOCOL_NAME,
    )
    try:
        response = client.request(action, payload)
    except LocalControlRemoteError as error:
        print(
            json.dumps(
                {
                    "errorCode": error.code,
                    "message": error.message,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except LocalControlUnavailable:
        print(
            json.dumps(
                {
                    "errorCode": "UPDATER_UNAVAILABLE",
                    "message": "the local updater control service is unavailable",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
