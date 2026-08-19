"""Stage the least-privilege credential file for the tunnel agent."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

try:
    import pwd
except ImportError:  # pragma: no cover - production target is Linux
    pwd = None

from device_credentials import (
    DEFAULT_CREDENTIALS_PATH,
    DEFAULT_REMOTE_SUPPORT_CREDENTIALS_PATH,
    DeviceCredentials,
    load_device_credentials,
    load_remote_support_credentials,
    validate_remote_support_credentials,
)
from secure_files import atomic_write_json


REMOTE_SUPPORT_USER = "ecobin-remote"


def build_remote_support_credentials_document(
    bundle: DeviceCredentials,
) -> dict:
    remote = bundle.remote_support
    return {
        "schemaVersion": 1,
        "hardwareSn": bundle.hardware_sn,
        "remoteSupport": {
            "tunnelHost": remote.server_host,
            "tunnelSshPort": remote.server_port,
            "tunnelUser": remote.server_user,
            "tunnelServerHostPublicKey": (
                remote.server_host_public_key
            ),
            "tunnelIdentityPrivateKey": remote.identity_private_key,
            "jumpUser": remote.jump_user,
            "maintenancePrincipal": remote.maintenance_principal,
            "maintenanceCaPublicKey": remote.maintenance_ca_public_key,
        },
    }


def install_remote_support_credentials(
    *,
    source_path: str | os.PathLike[str] = DEFAULT_CREDENTIALS_PATH,
    target_path: str | os.PathLike[str] = (
        DEFAULT_REMOTE_SUPPORT_CREDENTIALS_PATH
    ),
    service_user: str = REMOTE_SUPPORT_USER,
    command_runner: Callable[..., subprocess.CompletedProcess] = (
        subprocess.run
    ),
    user_lookup: Callable[[str], tuple[int, int] | None] | None = None,
) -> None:
    """Create the service account and atomically install its only secret."""

    bundle = load_device_credentials(source_path, required=True)
    if bundle is None:  # required=True already raises
        raise ValueError("device credentials are required")
    if user_lookup is None:
        user_lookup = _lookup_user
    identity = user_lookup(service_user)
    if identity is None:
        command_runner(
            [
                "/usr/sbin/useradd",
                "--system",
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                "--user-group",
                service_user,
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        identity = user_lookup(service_user)
        if identity is None:
            raise RuntimeError("remote support service user was not created")

    target = Path(target_path)
    document = build_remote_support_credentials_document(bundle)
    atomic_write_json(
        target,
        document,
        validator=validate_remote_support_credentials,
    )
    # Keep the projection root-only at rest.  systemd LoadCredential copies
    # it into the service's private credential directory at exec time, so the
    # static service account never needs traversal access to /etc/ecobin.
    os.chmod(target, 0o600)
    loaded = load_remote_support_credentials(target, required=True)
    if loaded != bundle.remote_support:
        raise RuntimeError(
            "remote support credential projection did not verify"
        )


def _lookup_user(name: str) -> tuple[int, int] | None:
    if pwd is None:
        return None
    try:
        record = pwd.getpwnam(name)
    except KeyError:
        return None
    return record.pw_uid, record.pw_gid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=DEFAULT_CREDENTIALS_PATH,
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_REMOTE_SUPPORT_CREDENTIALS_PATH,
    )
    parser.add_argument("--service-user", default=REMOTE_SUPPORT_USER)
    args = parser.parse_args(argv)
    install_remote_support_credentials(
        source_path=args.source,
        target_path=args.target,
        service_user=args.service_user,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
