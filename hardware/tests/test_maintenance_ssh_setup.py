from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import maintenance_ssh_setup
from maintenance_ssh_setup import install_maintenance_ssh

from tests.test_device_credentials import valid_document


def test_installs_ca_principal_no_long_term_keys_and_no_forwarding(tmp_path: Path):
    credentials = tmp_path / "device-credentials.json"
    document = valid_document()
    credentials.write_text(json.dumps(document), encoding="utf-8")
    credentials.chmod(0o600)
    ca = tmp_path / "ssh" / "maintenance-ca.pub"
    principals = tmp_path / "ssh" / "principals" / "ecobin-maintenance"
    drop_in = tmp_path / "ssh" / "sshd_config.d" / "maintenance.conf"
    sudoers = tmp_path / "sudoers" / "ecobin-maintenance"

    install_maintenance_ssh(
        credentials_path=credentials,
        ca_path=ca,
        principals_path=principals,
        sshd_drop_in_path=drop_in,
        sudoers_path=sudoers,
        user_exists=lambda _name: True,
        validate_and_reload=False,
    )

    assert ca.read_text(encoding="ascii").strip() == document[
        "remoteSupport"
    ]["maintenanceCaPublicKey"]
    assert principals.read_text(encoding="ascii").strip() == document[
        "remoteSupport"
    ]["maintenancePrincipal"]
    config = drop_in.read_text(encoding="ascii")
    assert "TrustedUserCAKeys" in config
    assert "AuthorizedKeysFile none" in config
    assert "PubkeyAuthentication yes" in config
    assert "AuthenticationMethods publickey" in config
    assert "AllowTcpForwarding no" in config
    assert config.endswith("Match all\n")
    assert "NOPASSWD: ALL" in sudoers.read_text(encoding="ascii")
    if os.name != "nt":
        assert stat.S_IMODE(sudoers.stat().st_mode) == 0o440


def test_missing_user_is_created_with_subprocess_argv_only(tmp_path: Path):
    credentials = tmp_path / "device-credentials.json"
    credentials.write_text(json.dumps(valid_document()), encoding="utf-8")
    credentials.chmod(0o600)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))

    install_maintenance_ssh(
        credentials_path=credentials,
        ca_path=tmp_path / "ca",
        principals_path=tmp_path / "principals",
        sshd_drop_in_path=tmp_path / "sshd.conf",
        sudoers_path=tmp_path / "sudoers",
        command_runner=run,
        user_exists=lambda _name: False,
        validate_and_reload=False,
    )

    assert calls[0][0][0] == "/usr/sbin/useradd"
    assert "ecobin-maintenance" == calls[0][0][-1]
    assert "shell" not in calls[0][1]


def test_privileged_configuration_is_validated_before_sshd_reload(tmp_path: Path):
    credentials = tmp_path / "device-credentials.json"
    credentials.write_text(json.dumps(valid_document()), encoding="utf-8")
    credentials.chmod(0o600)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))

    sudoers = tmp_path / "sudoers"
    install_maintenance_ssh(
        credentials_path=credentials,
        ca_path=tmp_path / "ca",
        principals_path=tmp_path / "principals",
        sshd_drop_in_path=tmp_path / "sshd.conf",
        sudoers_path=sudoers,
        command_runner=run,
        user_exists=lambda _name: True,
    )

    assert [call[0][0] for call in calls] == [
        "/usr/sbin/visudo",
        "/usr/sbin/sshd",
        "/usr/bin/systemctl",
    ]
    assert calls[0][0] == ["/usr/sbin/visudo", "-cf", str(sudoers)]
    assert calls[2][0] == [
        "/usr/bin/systemctl",
        "reload-or-restart",
        "ssh.service",
    ]
    assert all("shell" not in kwargs for _argv, kwargs in calls)


def test_removes_write_bits_from_trusted_etc_ancestor(
    tmp_path: Path,
    monkeypatch,
):
    credentials = tmp_path / "device-credentials.json"
    credentials.write_text(json.dumps(valid_document()), encoding="utf-8")
    credentials.chmod(0o600)
    fake_etc = tmp_path / "etc"
    fake_etc.mkdir(mode=0o775)
    fake_etc.chmod(0o775)
    monkeypatch.setattr(
        maintenance_ssh_setup,
        "SYSTEM_ETC_DIRECTORY",
        fake_etc,
    )

    install_maintenance_ssh(
        credentials_path=credentials,
        ca_path=fake_etc / "ssh" / "maintenance-ca.pub",
        principals_path=(
            fake_etc / "ssh" / "principals" / "ecobin-maintenance"
        ),
        sshd_drop_in_path=(
            fake_etc / "ssh" / "sshd_config.d" / "maintenance.conf"
        ),
        sudoers_path=fake_etc / "sudoers.d" / "ecobin-maintenance",
        user_exists=lambda _name: True,
        validate_and_reload=False,
    )

    if os.name != "nt":
        assert stat.S_IMODE(fake_etc.stat().st_mode) == 0o755
