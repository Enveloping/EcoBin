"""Permanent communication-agent skeleton for the stage-three device image.

This stage deliberately does not own OneNet, route remote updates, read OneNet
credentials, or open any network socket.  It proves only the permanent process,
private database, authenticated local control channel, and service lifecycle.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Iterable
from typing import Any

from communication_store import CommunicationStore, MAX_RELEASE_VERSION_LENGTH
from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    LocalControlAction,
    LocalControlServer,
)


logger = logging.getLogger("communication-agent")

COMMUNICATION_PROTOCOL_NAME = "ecobin.communication.control"
DEFAULT_STATE_PATH = "/var/lib/ecobin/communication/communication.db"
DEFAULT_SOCKET_PATH = "/run/ecobin/communication/control.sock"


class CommunicationController:
    """Read-only stage-three RPC surface."""

    def __init__(self, store: CommunicationStore, release_version: str) -> None:
        self.store = store
        self.release_version = release_version
        self._current_start: dict[str, Any] | None = None

    def record_started(self, start_fact: dict[str, Any]) -> None:
        self._current_start = dict(start_fact)

    def _base_status(self) -> dict[str, Any]:
        if self._current_start is None:
            raise RuntimeError("communication process start fact is unavailable")
        return {
            "component": "COMMUNICATION_AGENT",
            "status": "READY",
            "runtimeInstanceUid": self._current_start["startUid"],
            "releaseVersion": self.release_version,
            "startedAt": self._current_start["startedAt"],
            "localProtocolName": COMMUNICATION_PROTOCOL_NAME,
            "localProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "localProtocolMinor": LOCAL_PROTOCOL_MINOR,
            "onenetOwnership": "DISABLED",
            "remoteUpdateRouting": "DISABLED",
        }

    def health(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._base_status()

    def get_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._base_status(),
            **self.store.get_status(),
        }


class CommunicationAgent:
    def __init__(
        self,
        store: CommunicationStore,
        controller: CommunicationController,
        server: LocalControlServer,
        release_version: str,
    ) -> None:
        self.store = store
        self.controller = controller
        self.server = server
        self.release_version = release_version
        self._stop_event = threading.Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        start_fact = self.store.record_process_start(self.release_version)
        self.controller.record_started(start_fact)
        self.server.start()
        self._started = True
        logger.info("communication agent ready with OneNet ownership disabled")

    def request_stop(self) -> None:
        self._stop_event.set()

    def wait(self, *, check_interval_seconds: float = 0.2) -> None:
        if check_interval_seconds <= 0:
            raise ValueError("communication agent check interval must be positive")
        while not self._stop_event.wait(check_interval_seconds):
            if not self.server.is_running:
                failure = self.server.failure
                if failure is not None:
                    raise RuntimeError(
                        "communication local control server stopped unexpectedly"
                    ) from failure
                raise RuntimeError(
                    "communication local control server stopped unexpectedly"
                )

    def stop(self) -> None:
        self.request_stop()
        if self._started:
            self.server.stop()
        self.store.close()
        self._started = False
        logger.info("communication agent stopped")


def notify_systemd(message: str) -> None:
    """Send one sd_notify datagram without linking libsystemd."""

    if message not in {"READY=1", "STOPPING=1"}:
        raise ValueError("unsupported systemd notification")
    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):  # Linux abstract Unix-domain socket
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.sendto(message.encode("ascii"), address)


def notify_systemd_ready() -> None:
    notify_systemd("READY=1")


def notify_systemd_stopping() -> None:
    notify_systemd("STOPPING=1")


def resolve_allowed_uids(
    allowed_uids: Iterable[int] | None,
    allowed_users: Iterable[str] | None,
) -> frozenset[int]:
    """Resolve the configured names once; a missing account is fatal."""

    result = {0}
    for uid in allowed_uids or ():
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise ValueError("--allowed-uid must be a non-negative integer")
        result.add(uid)
    names = tuple(allowed_users or ())
    if names:
        try:
            import pwd
        except ImportError as error:  # pragma: no cover - Linux production path
            raise RuntimeError("user name lookup is unavailable") from error
        for name in names:
            if not isinstance(name, str) or not name:
                raise ValueError("--allowed-user must be a non-empty account name")
            try:
                account = pwd.getpwnam(name)
            except KeyError as error:
                raise ValueError(f"allowed local control user does not exist: {name}") from error
            result.add(account.pw_uid)
    return frozenset(result)


def resolve_socket_gid(group_name: str | None) -> int | None:
    if group_name is None:
        return None
    if not group_name:
        raise ValueError("--socket-group must be a non-empty group name")
    try:
        import grp
    except ImportError as error:  # pragma: no cover - Linux production path
        raise RuntimeError("group name lookup is unavailable") from error
    try:
        group = grp.getgrnam(group_name)
    except KeyError as error:
        raise ValueError(f"local control socket group does not exist: {group_name}") from error
    return group.gr_gid


def build_agent(args: argparse.Namespace) -> CommunicationAgent:
    if args.release_version is None:
        raise ValueError("--release-version is required")
    if (
        not isinstance(args.release_version, str)
        or not 1 <= len(args.release_version) <= MAX_RELEASE_VERSION_LENGTH
        or args.release_version != args.release_version.strip()
        or not args.release_version.isprintable()
    ):
        raise ValueError("--release-version is invalid")
    allowed_uids = resolve_allowed_uids(args.allowed_uid, args.allowed_user)
    action_uids = frozenset(allowed_uids)
    socket_gid = resolve_socket_gid(args.socket_group)
    store = CommunicationStore(args.state)
    store.initialize()
    try:
        controller = CommunicationController(store, args.release_version)
        server = LocalControlServer(
            args.socket,
            protocol_name=COMMUNICATION_PROTOCOL_NAME,
            actions={
                "HEALTH": LocalControlAction(
                    controller.health,
                    payload_fields=frozenset(),
                    allowed_uids=action_uids,
                ),
                "GET_STATUS": LocalControlAction(
                    controller.get_status,
                    payload_fields=frozenset(),
                    allowed_uids=action_uids,
                ),
            },
            allowed_uids=allowed_uids,
            socket_mode=0o660,
            socket_gid=socket_gid,
        )
    except Exception:
        store.close()
        raise
    return CommunicationAgent(
        store,
        controller,
        server,
        args.release_version,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        default=os.getenv("ECOBIN_COMMUNICATION_STATE_PATH", DEFAULT_STATE_PATH),
    )
    parser.add_argument(
        "--socket",
        default=os.getenv("ECOBIN_COMMUNICATION_SOCKET", DEFAULT_SOCKET_PATH),
    )
    parser.add_argument(
        "--release-version",
        default=os.getenv("ECOBIN_COMMUNICATION_RELEASE_VERSION"),
    )
    parser.add_argument("--allowed-uid", action="append", type=int, default=None)
    parser.add_argument("--allowed-user", action="append", default=None)
    parser.add_argument("--socket-group", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-22s] %(levelname)-5s %(message)s",
        stream=sys.stdout,
    )
    args = build_parser().parse_args(argv)
    agent = build_agent(args)

    def request_stop(signum, _frame) -> None:
        logger.info("signal %d, stopping communication agent", signum)
        agent.request_stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        agent.start()
        notify_systemd_ready()
        agent.wait()
    finally:
        try:
            notify_systemd_stopping()
        finally:
            agent.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
