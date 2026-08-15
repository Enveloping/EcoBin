"""Idempotently install the device-side OpenSSH maintenance CA boundary."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Callable

try:
    import pwd
except ImportError:  # pragma: no cover - production target is Linux
    pwd = None

from device_credentials import load_device_credentials
from secure_files import atomic_write_bytes


MAINTENANCE_USER = "ecobin-maintenance"


def install_maintenance_ssh(
    *,
    credentials_path: str | os.PathLike[str],
    ca_path: str | os.PathLike[str] = "/etc/ssh/ecobin_maintenance_ca.pub",
    principals_path: str | os.PathLike[str] = (
        "/etc/ssh/auth_principals/ecobin-maintenance"
    ),
    sshd_drop_in_path: str | os.PathLike[str] = (
        "/etc/ssh/sshd_config.d/60-ecobin-maintenance.conf"
    ),
    sudoers_path: str | os.PathLike[str] = (
        "/etc/sudoers.d/ecobin-maintenance"
    ),
    command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    user_exists: Callable[[str], bool] | None = None,
    validate_and_reload: bool = True,
) -> None:
    bundle = load_device_credentials(credentials_path, required=True)
    if bundle is None:  # for type checkers; required=True already raises
        raise ValueError("device credentials are required")
    if user_exists is None:
        user_exists = _user_exists
    if not user_exists(MAINTENANCE_USER):
        command_runner(
            [
                "/usr/sbin/useradd",
                "--create-home",
                "--shell",
                "/bin/bash",
                "--user-group",
                MAINTENANCE_USER,
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )

    ca_target = Path(ca_path)
    principals_target = Path(principals_path)
    drop_in_target = Path(sshd_drop_in_path)
    sudoers_target = Path(sudoers_path)
    for directory, mode in (
        (ca_target.parent, 0o755),
        (principals_target.parent, 0o755),
        (drop_in_target.parent, 0o755),
        (sudoers_target.parent, 0o755),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, mode)

    remote = bundle.remote_support
    atomic_write_bytes(
        ca_target,
        (remote.maintenance_ca_public_key + "\n").encode("ascii"),
        mode=0o644,
    )
    atomic_write_bytes(
        principals_target,
        (remote.maintenance_principal + "\n").encode("ascii"),
        mode=0o644,
    )
    sshd_config = (
        f"TrustedUserCAKeys {ca_target}\n"
        f"Match User {MAINTENANCE_USER}\n"
        f"    AuthorizedPrincipalsFile {principals_target}\n"
        "    AuthorizedKeysFile none\n"
        "    PubkeyAuthentication yes\n"
        "    AuthenticationMethods publickey\n"
        "    PasswordAuthentication no\n"
        "    KbdInteractiveAuthentication no\n"
        "    AllowAgentForwarding no\n"
        "    AllowTcpForwarding no\n"
        "    X11Forwarding no\n"
        "    PermitTunnel no\n"
        "    PermitTTY yes\n"
        "Match all\n"
    )
    atomic_write_bytes(
        drop_in_target,
        sshd_config.encode("ascii"),
        mode=0o644,
    )
    atomic_write_bytes(
        sudoers_target,
        (
            f"{MAINTENANCE_USER} ALL=(root) NOPASSWD: ALL\n"
        ).encode("ascii"),
        mode=0o440,
    )
    if validate_and_reload:
        command_runner(
            ["/usr/sbin/visudo", "-cf", str(sudoers_target)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        command_runner(
            ["/usr/sbin/sshd", "-t"],
            check=True,
            capture_output=True,
            timeout=15,
        )
        command_runner(
            ["/usr/bin/systemctl", "reload-or-restart", "ssh.service"],
            check=True,
            capture_output=True,
            timeout=15,
        )


def _user_exists(name: str) -> bool:
    if pwd is None:
        return False
    try:
        pwd.getpwnam(name)
    except KeyError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--credentials",
        default="/etc/ecobin/device-credentials.json",
    )
    args = parser.parse_args(argv)
    install_maintenance_ssh(credentials_path=args.credentials)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
