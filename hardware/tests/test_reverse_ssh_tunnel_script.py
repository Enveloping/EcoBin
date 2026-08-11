from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "reverse_ssh_tunnel.sh"
)
BASH = shutil.which("bash")
SSH_KEYGEN = shutil.which("ssh-keygen")

pytestmark = pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="the reverse SSH tunnel is a Linux target script",
)


def run_script(
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_environment = os.environ.copy()
    if environment:
        merged_environment.update(environment)
    return subprocess.run(
        [BASH, str(SCRIPT), *arguments],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=merged_environment,
        timeout=15,
    )


def create_ssh_material(tmp_path: Path, server_port: int = 2222) -> dict[str, str]:
    if SSH_KEYGEN is None:
        pytest.skip("ssh-keygen is unavailable")

    identity = tmp_path / "tunnel_key"
    host_identity = tmp_path / "host_key"
    known_hosts = tmp_path / "known_hosts"
    subprocess.run(
        [SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(identity)],
        check=True,
    )
    subprocess.run(
        [SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(host_identity)],
        check=True,
    )
    identity.chmod(stat.S_IRUSR | stat.S_IWUSR)
    host_public_key = host_identity.with_suffix(".pub").read_text(encoding="utf-8").split()
    known_hosts.write_text(
        f"[debug.example]:{server_port} {host_public_key[0]} {host_public_key[1]}\n",
        encoding="utf-8",
    )

    return {
        "ECOBIN_REVERSE_SSH_SERVER_HOST": "debug.example",
        "ECOBIN_REVERSE_SSH_SERVER_PORT": str(server_port),
        "ECOBIN_REVERSE_SSH_REMOTE_PORT": "22999",
        "ECOBIN_REVERSE_SSH_IDENTITY_FILE": str(identity),
        "ECOBIN_REVERSE_SSH_KNOWN_HOSTS_FILE": str(known_hosts),
        "ECOBIN_REVERSE_SSH_LOCK_FILE": str(tmp_path / "tunnel.lock"),
    }


def test_help_explains_server_reverse_and_target_ports():
    result = run_script("--help")

    assert result.returncode == 0, result.stderr
    assert "SERVER_PORT" in result.stdout
    assert "REMOTE_PORT" in result.stdout
    assert "127.0.0.1:22" in result.stdout


def test_dry_run_builds_restricted_noninteractive_reverse_forward(tmp_path: Path):
    environment = {
        "ECOBIN_REVERSE_SSH_SERVER_HOST": "debug.example",
        "ECOBIN_REVERSE_SSH_SERVER_PORT": "2222",
        "ECOBIN_REVERSE_SSH_REMOTE_PORT": "22999",
        "ECOBIN_REVERSE_SSH_IDENTITY_FILE": str(tmp_path / "unused-key"),
        "ECOBIN_REVERSE_SSH_KNOWN_HOSTS_FILE": str(tmp_path / "unused-known-hosts"),
        "ECOBIN_REVERSE_SSH_LOCK_FILE": str(tmp_path / "unused-lock"),
    }

    result = run_script("--dry-run", environment=environment)

    assert result.returncode == 0, result.stderr
    assert "-R 127.0.0.1:22999:127.0.0.1:22" in result.stdout
    assert "StrictHostKeyChecking=yes" in result.stdout
    assert "BatchMode=yes" in result.stdout
    assert "PasswordAuthentication=no" in result.stdout
    assert "debug.example" in result.stdout
    assert "2222" in result.stdout


def test_invalid_remote_port_is_rejected(tmp_path: Path):
    result = run_script(
        "--dry-run",
        environment={
            "ECOBIN_REVERSE_SSH_REMOTE_PORT": "22",
            "ECOBIN_REVERSE_SSH_IDENTITY_FILE": str(tmp_path / "unused-key"),
            "ECOBIN_REVERSE_SSH_KNOWN_HOSTS_FILE": str(tmp_path / "unused-known-hosts"),
            "ECOBIN_REVERSE_SSH_LOCK_FILE": str(tmp_path / "unused-lock"),
        },
    )

    assert result.returncode != 0
    assert "REMOTE_PORT" in result.stderr


def test_check_accepts_valid_pinned_host_configuration(tmp_path: Path):
    environment = create_ssh_material(tmp_path)

    result = run_script("--check", environment=environment)

    assert result.returncode == 0, result.stderr
    assert "本地配置有效" in result.stderr


def test_check_rejects_group_readable_private_key(tmp_path: Path):
    environment = create_ssh_material(tmp_path)
    identity = Path(environment["ECOBIN_REVERSE_SSH_IDENTITY_FILE"])
    identity.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

    result = run_script("--check", environment=environment)

    assert result.returncode != 0
    assert "不能向组或其他用户开放" in result.stderr


def test_once_passes_expected_forward_to_ssh_process(tmp_path: Path):
    environment = create_ssh_material(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    ssh_log = tmp_path / "ssh-arguments"
    fake_ssh.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == '-G' ]]; then exit 0; fi\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_SSH_LOG\"\n"
        "exit 42\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{os.environ['PATH']}"
    environment["FAKE_SSH_LOG"] = str(ssh_log)

    result = run_script("--once", environment=environment)

    assert result.returncode == 42, result.stderr
    arguments = ssh_log.read_text(encoding="utf-8").splitlines()
    assert "127.0.0.1:22999:127.0.0.1:22" in arguments
    assert "debug.example" in arguments[-1]
    assert "ecobin-tunnel@debug.example" == arguments[-1]
