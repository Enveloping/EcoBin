#!/usr/bin/env python3
"""Root-only operator control for a latched native UART communication fault."""

from __future__ import annotations

import argparse
import json
import os
import sys

from business_control import BUSINESS_PROTOCOL_NAME, DEFAULT_BUSINESS_CONTROL_SOCKET
from local_control import (
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--socket",
        default=os.getenv(
            "ECOBIN_BUSINESS_CONTROL_SOCKET",
            DEFAULT_BUSINESS_CONTROL_SOCKET,
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "status",
        help="read the exact active native communication fault and readiness",
    )
    recover = commands.add_parser(
        "recover",
        help="clear one exact fault after the communication cause is fixed",
    )
    recover.add_argument("--fault-uid", required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument(
        "--confirm-cause-fixed",
        action="store_true",
        help="confirm that an operator has fixed and checked the cause",
    )
    return parser


def _effective_uid() -> int | None:
    getter = getattr(os, "geteuid", None)
    return getter() if getter is not None else None


def _request(args: argparse.Namespace) -> tuple[str, dict]:
    if args.command == "status":
        return "GET_NATIVE_FAULT_STATUS", {}
    if not args.confirm_cause_fixed:
        raise ValueError("--confirm-cause-fixed is required")
    return "RECOVER_NATIVE_COMMUNICATION_FAULT", {
        "expectedFaultUid": args.fault_uid,
        "reason": args.reason,
        "causeFixedConfirmed": True,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if _effective_uid() != 0:
        print(
            json.dumps(
                {
                    "errorCode": "ROOT_REQUIRED",
                    "message": "native fault control requires UID 0",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        action, payload = _request(args)
    except ValueError as error:
        print(
            json.dumps(
                {"errorCode": "CONFIRMATION_REQUIRED", "message": str(error)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    client = LocalControlClient(
        args.socket,
        protocol_name=BUSINESS_PROTOCOL_NAME,
    )
    try:
        result = client.request(action, payload)
    except LocalControlRemoteError as error:
        print(
            json.dumps(
                {"errorCode": error.code, "message": error.message},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except LocalControlUnavailable:
        print(
            json.dumps(
                {
                    "errorCode": "BUSINESS_RUNTIME_UNAVAILABLE",
                    "message": "the local business control service is unavailable",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
