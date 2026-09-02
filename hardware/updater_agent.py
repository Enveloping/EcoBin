"""Stage-three permanent device-updater process.

This process deliberately installs only the durable and authenticated local
control boundary.  Update execution and the permanent job gate remain
disabled until their later migration stages are implemented and qualified.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable, Iterable
from typing import Any

from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    LocalControlAction,
    LocalControlActionError,
    LocalControlServer,
)
from updater_store import UpdaterStore


logger = logging.getLogger("device-updater")

DEFAULT_STATE_PATH = "/var/lib/ecobin/updater/updater.db"
DEFAULT_SOCKET_PATH = "/run/ecobin/updater/control.sock"
UPDATER_LOCAL_PROTOCOL_NAME = "ecobin.updater.control"

DISABLED_UPDATE_ACTIONS = frozenset(
    {
        "START_BUSINESS_UPDATE",
        "START_MCU_UPDATE",
        "CANCEL_UPDATE",
        "RENEW_DOWNLOAD_AUTHORIZATION",
    }
)


class UpdaterControlHandler:
    """Expose only truthful stage-three health and status operations."""

    def __init__(self, store: UpdaterStore) -> None:
        self.store = store

    def get_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **self.store.get_status(),
            "status": "READY",
            "localProtocolName": UPDATER_LOCAL_PROTOCOL_NAME,
            "localProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "localProtocolMinor": LOCAL_PROTOCOL_MINOR,
        }

    @staticmethod
    def reject_disabled_update(
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise LocalControlActionError(
            "FEATURE_DISABLED",
            "device updates are disabled in stage three",
        )


class UpdaterAgent:
    def __init__(
        self,
        store: UpdaterStore,
        server: LocalControlServer,
    ) -> None:
        self.store = store
        self.server = server
        self._stop_event = threading.Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            self.server.start()
        except Exception:
            self.server.stop()
            self.store.close()
            raise
        self._started = True
        logger.info("device updater stage-three control plane ready")

    def request_stop(self) -> None:
        self._stop_event.set()

    def wait(self) -> None:
        while not self._stop_event.is_set():
            if not self.server.wait_stopped(timeout_seconds=0.25):
                continue
            if self._stop_event.is_set():
                return
            failure = self.server.failure
            if failure is not None:
                raise RuntimeError(
                    "device updater control server failed"
                ) from failure
            raise RuntimeError(
                "device updater control server stopped unexpectedly"
            )

    def stop(self) -> None:
        self.request_stop()
        if self._started:
            self.server.stop()
            self._started = False
        self.store.close()
        logger.info("device updater stopped")


def build_agent(args: argparse.Namespace) -> UpdaterAgent:
    allowed_uids = resolve_allowed_uids(
        args.allowed_uid,
        getattr(args, "allowed_user", None),
    )
    socket_gid = resolve_socket_gid(
        getattr(args, "socket_group", None)
    )
    store = UpdaterStore(
        args.state,
        release_version=args.release_version,
    )
    store.initialize()
    handler = UpdaterControlHandler(store)
    actions = build_control_actions(handler, allowed_uids=allowed_uids)
    try:
        server = LocalControlServer(
            args.socket,
            protocol_name=UPDATER_LOCAL_PROTOCOL_NAME,
            actions=actions,
            allowed_uids=allowed_uids,
            socket_mode=0o660,
            socket_gid=socket_gid,
        )
    except Exception:
        store.close()
        raise
    return UpdaterAgent(store, server)


def build_control_actions(
    handler: UpdaterControlHandler,
    *,
    allowed_uids: Iterable[int],
) -> dict[str, LocalControlAction]:
    action_uids = frozenset(allowed_uids)
    return {
        "HEALTH": LocalControlAction(
            handler.get_status,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
        "GET_STATUS": LocalControlAction(
            handler.get_status,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
        **{
            action: LocalControlAction(
                handler.reject_disabled_update,
                payload_fields=frozenset(),
                allowed_uids=action_uids,
            )
            for action in DISABLED_UPDATE_ACTIONS
        },
    }


def resolve_allowed_uids(
    allowed_uids: Iterable[int] | None,
    allowed_users: Iterable[str] | None,
    *,
    user_lookup: Callable[[str], int] | None = None,
) -> list[int]:
    """Resolve CLI user names without weakening explicit UID policy."""

    resolved: set[int] = {0}
    for uid in allowed_uids or ():
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise ValueError("--allowed-uid must be non-negative")
        resolved.add(uid)
    lookup = user_lookup or _system_user_uid
    for username in allowed_users or ():
        if not isinstance(username, str) or not username:
            raise ValueError("--allowed-user must be non-empty")
        try:
            uid = lookup(username)
        except KeyError as error:
            raise ValueError(
                f"--allowed-user does not exist: {username}"
            ) from error
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise ValueError(
                f"--allowed-user has an invalid UID: {username}"
            )
        resolved.add(uid)
    # Root is always retained for the explicitly accepted stage-three local
    # diagnosis/recovery boundary; configured identities extend that set.
    return sorted(resolved)


def _system_user_uid(username: str) -> int:
    try:
        import pwd
    except ImportError as error:  # pragma: no cover - target OS is Linux
        raise RuntimeError("system user lookup is unavailable") from error
    return pwd.getpwnam(username).pw_uid


def resolve_socket_gid(
    group_name: str | None,
    *,
    group_lookup: Callable[[str], int] | None = None,
) -> int | None:
    if group_name is None:
        return None
    if not isinstance(group_name, str) or not group_name:
        raise ValueError("--socket-group must be non-empty")
    lookup = group_lookup or _system_group_gid
    try:
        gid = lookup(group_name)
    except KeyError as error:
        raise ValueError(
            f"--socket-group does not exist: {group_name}"
        ) from error
    if isinstance(gid, bool) or not isinstance(gid, int) or gid < 0:
        raise ValueError(
            f"--socket-group has an invalid GID: {group_name}"
        )
    return gid


def _system_group_gid(group_name: str) -> int:
    try:
        import grp
    except ImportError as error:  # pragma: no cover - target OS is Linux
        raise RuntimeError("system group lookup is unavailable") from error
    return grp.getgrnam(group_name).gr_gid


def notify_systemd(message: str) -> None:
    """Send one bounded lifecycle notification without libsystemd."""

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
    """Report readiness only after the updater control socket is listening."""

    notify_systemd("READY=1")


def notify_systemd_stopping() -> None:
    notify_systemd("STOPPING=1")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        default=os.getenv("ECOBIN_UPDATER_STATE_PATH", DEFAULT_STATE_PATH),
    )
    parser.add_argument(
        "--socket",
        default=os.getenv("ECOBIN_UPDATER_SOCKET", DEFAULT_SOCKET_PATH),
    )
    parser.add_argument(
        "--release-version",
        default=os.getenv("ECOBIN_UPDATER_RELEASE_VERSION"),
    )
    parser.add_argument(
        "--allowed-uid",
        action="append",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--allowed-user",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--socket-group",
        default=os.getenv("ECOBIN_UPDATER_SOCKET_GROUP"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-22s] %(levelname)-5s %(message)s",
        stream=sys.stdout,
    )
    args = build_parser().parse_args(argv)
    agent = build_agent(args)

    def request_stop(signum: int, _frame: Any) -> None:
        logger.info("signal %d, stopping device updater", signum)
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
