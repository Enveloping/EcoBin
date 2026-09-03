"""Standalone supervisor for EcoBin's short-lived reverse SSH tunnel."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Iterable

from device_credentials import (
    DEFAULT_REMOTE_SUPPORT_CREDENTIALS_PATH,
    load_remote_support_credentials,
)
from remote_support import RemoteSupportManager
from remote_support_control import (
    DEFAULT_SOCKET_PATH,
    RemoteSupportControlServer,
)
from remote_support_store import RemoteSupportStore


logger = logging.getLogger("remote-support-agent")

DEFAULT_STATE_PATH = "/var/lib/ecobin/remote-support/state.db"
DEFAULT_RUNTIME_DIRECTORY = "/run/ecobin/remote-support"


def notify_systemd_ready() -> None:
    """Report readiness only after the private control socket is listening."""

    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):  # Linux abstract Unix-domain socket
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.sendto(b"READY=1", address)


class RemoteSupportAgent:
    def __init__(
        self,
        store: RemoteSupportStore,
        manager: RemoteSupportManager,
        server: RemoteSupportControlServer,
    ) -> None:
        self.store = store
        self.manager = manager
        self.server = server
        self._stop_event = threading.Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.manager.start()
        try:
            self.server.start()
        except Exception:
            self.manager.stop()
            raise
        self._started = True
        logger.info("remote support agent ready")

    def request_stop(self) -> None:
        self._stop_event.set()

    def wait(self) -> None:
        self._stop_event.wait()

    def stop(self) -> None:
        if not self._started:
            self.store.close()
            return
        self.server.stop()
        self.manager.stop()
        self.store.close()
        self._started = False
        logger.info("remote support agent stopped")


def build_agent(args: argparse.Namespace) -> RemoteSupportAgent:
    credentials = load_remote_support_credentials(
        args.credentials,
        required=True,
    )
    if credentials is None:  # required=True already raises
        raise ValueError("remote support credentials are required")
    store = RemoteSupportStore(args.state)
    store.initialize()
    if args.legacy_edge_store:
        disposition = store.import_legacy_edge_store(
            args.legacy_edge_store
        )
        logger.info(
            "legacy remote support session migration: %s",
            disposition,
        )
    manager = RemoteSupportManager(
        store,
        credentials,
        runtime_dir=args.runtime_directory,
    )
    server = RemoteSupportControlServer(
        args.socket,
        controller=manager,
        store=store,
        allowed_uids=resolve_allowed_uids(
            args.allowed_uid,
            getattr(args, "allowed_user", None),
        ),
        socket_mode=0o660 if getattr(args, "socket_group", None) else 0o600,
        socket_gid=resolve_socket_gid(getattr(args, "socket_group", None)),
    )
    return RemoteSupportAgent(store, manager, server)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--credentials",
        default=os.getenv(
            "ECOBIN_REMOTE_SUPPORT_CREDENTIALS_PATH",
            DEFAULT_REMOTE_SUPPORT_CREDENTIALS_PATH,
        ),
    )
    parser.add_argument(
        "--state",
        default=os.getenv(
            "ECOBIN_REMOTE_SUPPORT_STATE_PATH",
            DEFAULT_STATE_PATH,
        ),
    )
    parser.add_argument(
        "--socket",
        default=os.getenv(
            "ECOBIN_REMOTE_SUPPORT_SOCKET",
            DEFAULT_SOCKET_PATH,
        ),
    )
    parser.add_argument(
        "--runtime-directory",
        default=os.getenv(
            "ECOBIN_REMOTE_SUPPORT_RUNTIME_DIR",
            DEFAULT_RUNTIME_DIRECTORY,
        ),
    )
    parser.add_argument(
        "--legacy-edge-store",
        default=os.getenv("ECOBIN_LEGACY_EDGE_STORE_PATH", ""),
    )
    parser.add_argument(
        "--allowed-uid",
        action="append",
        type=int,
        default=None,
    )
    parser.add_argument("--allowed-user", action="append", default=None)
    parser.add_argument("--socket-group", default=None)
    parser.add_argument("--migrate-only", action="store_true")
    return parser


def resolve_allowed_uids(
    allowed_uids: Iterable[int] | None,
    allowed_users: Iterable[str] | None,
) -> frozenset[int]:
    result = set(allowed_uids or ())
    names = tuple(allowed_users or ())
    if names:
        try:
            import pwd
        except ImportError as error:  # pragma: no cover - Linux target
            raise RuntimeError("user name lookup is unavailable") from error
        for name in names:
            if not isinstance(name, str) or not name:
                raise ValueError("--allowed-user must be non-empty")
            try:
                result.add(pwd.getpwnam(name).pw_uid)
            except KeyError as error:
                raise ValueError(
                    f"allowed remote support user does not exist: {name}"
                ) from error
    if any(
        isinstance(uid, bool) or not isinstance(uid, int) or uid < 0
        for uid in result
    ):
        raise ValueError("--allowed-uid must be non-negative")
    return frozenset(result)


def resolve_socket_gid(group_name: str | None) -> int | None:
    if group_name is None:
        return None
    if not isinstance(group_name, str) or not group_name:
        raise ValueError("--socket-group must be non-empty")
    try:
        import grp
    except ImportError as error:  # pragma: no cover - Linux target
        raise RuntimeError("group name lookup is unavailable") from error
    try:
        gid = grp.getgrnam(group_name).gr_gid
    except KeyError as error:
        raise ValueError(
            f"remote support socket group does not exist: {group_name}"
        ) from error
    if isinstance(gid, bool) or not isinstance(gid, int) or gid < 0:
        raise ValueError("remote support socket group GID is invalid")
    return gid


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-22s] %(levelname)-5s %(message)s",
        stream=sys.stdout,
    )
    args = build_parser().parse_args(argv)
    if args.allowed_uid is None:
        args.allowed_uid = [0]
    if any(uid < 0 for uid in args.allowed_uid):
        raise ValueError("--allowed-uid must be non-negative")
    agent = build_agent(args)
    if args.migrate_only:
        if not args.legacy_edge_store:
            agent.store.close()
            raise ValueError(
                "--migrate-only requires --legacy-edge-store"
            )
        agent.store.close()
        logger.info("remote support state migration completed")
        return 0

    def request_stop(signum, _frame) -> None:
        logger.info("signal %d, stopping remote support agent", signum)
        agent.request_stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        agent.start()
        notify_systemd_ready()
        agent.wait()
    finally:
        agent.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
