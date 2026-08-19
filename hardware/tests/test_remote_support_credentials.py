from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from device_credentials import load_remote_support_credentials
from remote_support_credentials import install_remote_support_credentials
from tests.test_device_credentials import valid_document


def test_installer_projects_only_tunnel_credentials_for_service_user(
    tmp_path: Path,
):
    source = tmp_path / "device-credentials.json"
    target = tmp_path / "remote-support-credentials.json"
    source.write_text(json.dumps(valid_document()), encoding="utf-8")
    source.chmod(0o600)
    created = False
    calls = []

    def lookup(_name: str):
        return (1234, 1234) if created else None

    def run(argv, **kwargs):
        nonlocal created
        calls.append((argv, kwargs))
        created = True
        return subprocess.CompletedProcess(argv, 0)

    install_remote_support_credentials(
        source_path=source,
        target_path=target,
        command_runner=run,
        user_lookup=lookup,
    )

    document = json.loads(target.read_text(encoding="utf-8"))
    assert set(document) == {"schemaVersion", "hardwareSn", "remoteSupport"}
    assert "oneNet" not in document
    assert "deviceKey" not in target.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) & 0o077 == 0
    assert load_remote_support_credentials(target, required=True) is not None
    assert calls[0][0][-1] == "ecobin-remote"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission boundary")
def test_tunnel_credential_loader_rejects_group_readable_file(
    tmp_path: Path,
):
    source = tmp_path / "device-credentials.json"
    target = tmp_path / "remote-support-credentials.json"
    source.write_text(json.dumps(valid_document()), encoding="utf-8")
    source.chmod(0o600)
    install_remote_support_credentials(
        source_path=source,
        target_path=target,
        user_lookup=lambda _name: (os.getuid(), os.getgid()),
    )
    target.chmod(0o440)

    with pytest.raises(ValueError, match="group/world"):
        load_remote_support_credentials(target, required=True)
