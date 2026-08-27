from __future__ import annotations

import base64
import hashlib
import os
import runpy
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from install.runtime_release import RUNTIME_APP_FILES as SIGNED_RUNTIME_APP_FILES
from system import image_software_installer as image_installer
from system.image_software_installer import (
    FACTORY_APP_RUNTIME_FILES,
    RUNTIME_APP_FILES,
    ImageSoftwareError,
    audit_image_software,
    install_image_software,
    load_and_validate_payload,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HARDWARE_ROOT = REPOSITORY_ROOT / "hardware"
PAYLOAD_LOCK_GENERATOR = (
    REPOSITORY_ROOT
    / "tools/orangepi-image/lib/generate_software_payload_lock.py"
)
IMAGE_SOFTWARE_INSTALLER = HARDWARE_ROOT / "system/image_software_installer.py"


def test_image_and_signed_release_share_one_runtime_source_manifest() -> None:
    assert RUNTIME_APP_FILES is SIGNED_RUNTIME_APP_FILES
    assert "factory_seal/admission.py" in RUNTIME_APP_FILES
    assert "camera_capture.py" in RUNTIME_APP_FILES
    assert "camera_capture.py" in FACTORY_APP_RUNTIME_FILES
    assert "trusted_clock.py" in RUNTIME_APP_FILES
    assert "trusted_clock.py" in FACTORY_APP_RUNTIME_FILES
    assert set(FACTORY_APP_RUNTIME_FILES) < set(RUNTIME_APP_FILES)


def test_image_installer_direct_file_help_loads_shared_manifest(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [sys.executable, "-I", str(IMAGE_SOFTWARE_INSTALLER), "--help"],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "validate-payload" in completed.stdout


def _assert_isolated_app_imports(app: Path, *module_names: str) -> None:
    program = r"""
import importlib
import os
from pathlib import Path
import sys

app = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(app))
os.chdir(app.parent)
for name in sys.argv[2:]:
    importlib.import_module(name)
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", program, str(app), *module_names],
        cwd=app.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_factory_app_staging_has_an_isolated_exact_import_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "posix":
        monkeypatch.setattr(image_installer.os, "chown", lambda *_args: None)
    app = tmp_path / "staged-factory-app"

    image_installer._stage_factory_app(HARDWARE_ROOT, app)

    _assert_isolated_app_imports(
        app,
        "first_boot.orchestrator",
        "factory.acceptance_service",
        "device_credentials",
    )
    def assert_same_without_root_owner(
        installed: Path,
        source: Path,
        mode: int | None = None,
    ) -> None:
        details = installed.lstat()
        assert stat.S_ISREG(details.st_mode)
        assert not installed.is_symlink()
        assert installed.read_bytes() == source.read_bytes()
        if os.name == "posix" and mode is not None:
            assert stat.S_IMODE(details.st_mode) == mode

    with monkeypatch.context() as unprivileged_audit:
        unprivileged_audit.setattr(
            image_installer,
            "_assert_same_file",
            assert_same_without_root_owner,
        )
        image_installer._audit_factory_app(app, HARDWARE_ROOT)
        (app / "unexpected.py").write_text(
            "# not allowlisted\n",
            encoding="utf-8",
        )
        with pytest.raises(ImageSoftwareError, match="allowlist is not exact"):
            image_installer._audit_factory_app(app, HARDWARE_ROOT)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_venv(path: Path) -> None:
    (path / "bin").mkdir(parents=True)
    launcher = path / "bin/python"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    os.chmod(launcher, 0o755)


def _ed25519_public_key_pem(fill: int) -> bytes:
    der = bytes.fromhex("302a300506032b6570032100") + bytes([fill]) * 32
    encoded = base64.b64encode(der).decode("ascii")
    return (
        "-----BEGIN PUBLIC KEY-----\n"
        f"{encoded}\n"
        "-----END PUBLIC KEY-----\n"
    ).encode("ascii")


def _ed25519_private_key_pem(fill: int) -> bytes:
    der = bytes.fromhex("302e020100300506032b657004220420") + bytes([fill]) * 32
    encoded = base64.b64encode(der).decode("ascii")
    return (
        "-----BEGIN PRIVATE KEY-----\n"
        f"{encoded}\n"
        "-----END PRIVATE KEY-----\n"
    ).encode("ascii")


def _write_runtime(payload: Path, git_commit: str, release_id: str) -> None:
    runtime = payload / "components/hardware-runtime"
    app = runtime / "app"
    app.mkdir(parents=True)
    for name in RUNTIME_APP_FILES:
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((HARDWARE_ROOT / name).read_bytes())
    _make_venv(runtime / ".venv")
    (runtime / ".venv/.ecobin-install-complete").write_text(
        "ECOBIN_INSTALL_MARKER_VERSION=1\n",
        encoding="ascii",
    )
    (runtime / "manifest.env").write_text(
        f"ECOBIN_RELEASE_ID={release_id}\n"
        f"ECOBIN_GIT_COMMIT={git_commit}\n"
        "ECOBIN_PYTHON_SERIES=3.11\n",
        encoding="ascii",
    )
    (runtime / "release.env").write_text(
        f"ECOBIN_EDGE_VERSION={release_id}\n",
        encoding="ascii",
    )
    checksums = []
    for path in sorted(runtime.rglob("*")):
        if (
            path.is_file()
            and not path.is_symlink()
            and ".venv" not in path.parts
            and path.name != "SHA256SUMS"
        ):
            checksums.append(
                f"{_sha256(path)}  {path.relative_to(runtime).as_posix()}\n"
            )
    (runtime / "SHA256SUMS").write_text("".join(checksums), encoding="ascii")


def _make_payload(
    tmp_path: Path,
    *,
    hil_approved: bool = True,
    mutate_before_lock: Callable[[Path], None] | None = None,
    expect_valid: bool = True,
) -> tuple[Path, str, str]:
    payload = tmp_path / "payload"
    payload.mkdir()
    git_commit = "a" * 40
    _write_runtime(payload, git_commit, "runtime-001")
    for name in ("enrollment", "remote-support", "factory-test"):
        _make_venv(payload / f"components/{name}-venv")
    (payload / "config").mkdir()
    (payload / "config/enrollment.env").write_text(
        "ECOBIN_ENROLLMENT_BACKEND_URL=https://api.jinshoubao.com\n"
        "ECOBIN_ENROLLMENT_KEY_ID=K1\n"
        "ECOBIN_ENROLLMENT_MODE=SELF_ENROLLMENT\n",
        encoding="utf-8",
    )
    (payload / "config/cellular.env").write_text(
        "ECOBIN_CELLULAR_SCHEMA_VERSION=2\n"
        f"ECOBIN_CELLULAR_HIL_APPROVED={'true' if hil_approved else 'false'}\n"
        "ECOBIN_CELLULAR_CONNECTION_ID=ecobin-air780e-rndis\n"
        "ECOBIN_CELLULAR_USB_DRIVER=rndis_host\n"
        "ECOBIN_CELLULAR_USB_PROFILE=RNDIS\n"
        "ECOBIN_CELLULAR_AUTO_APN=true\n"
        "ECOBIN_CELLULAR_PROBE_IPV4=1.1.1.1\n"
        "ECOBIN_CELLULAR_HTTPS_PROBE_URL=https://api.jinshoubao.com/health\n",
        encoding="utf-8",
    )
    if os.name == "posix":
        # The production payload builder locks these files only after applying
        # their controlled transport mode.  Keep the fixture faithful so the
        # installed 0600 mode can be compared with the signed payload lock.
        os.chmod(payload / "config/enrollment.env", 0o600)
        os.chmod(payload / "config/cellular.env", 0o600)
    for name, key_name, fill in (
        ("mcu-release-keys", "RELEASE_2026_01.pem", 1),
        ("runtime-release-keys", "factory_2026.pem", 2),
    ):
        directory = payload / "trust" / name
        directory.mkdir(parents=True)
        key = directory / key_name
        key.write_bytes(_ed25519_public_key_pem(fill))
        os.chmod(key, 0o644)
    evidence = payload / "evidence"
    evidence.mkdir()
    (evidence / "runtime-release-verification.json").write_text(
        "{\n"
        '  "archiveSha256": "' + "1" * 64 + '",\n'
        '  "releaseId": "runtime-001",\n'
        '  "schemaVersion": 1,\n'
        '  "signatureSha256": "' + "2" * 64 + '",\n'
        '  "signingKeyId": "factory_2026",\n'
        '  "sourceGitCommit": "' + git_commit + '"\n'
        "}\n",
        encoding="utf-8",
    )
    if mutate_before_lock is not None:
        mutate_before_lock(payload)
    namespace = runpy.run_path(
        str(PAYLOAD_LOCK_GENERATOR), run_name="payload_lock_generator"
    )
    result = namespace["main"](
        [
            "--payload-dir",
            str(payload),
            "--payload-id",
            "payload-001",
            "--git-commit",
            git_commit,
            "--hardware-runtime-release-id",
            "runtime-001",
            "--enrollment-release-id",
            "enrollment-001",
            "--remote-support-release-id",
            "remote-001",
            "--factory-test-release-id",
            "factory-001",
            "--first-boot-release-id",
            "first-boot-001",
        ]
    )
    if not hil_approved or not expect_valid:
        assert result == 2
        return payload, git_commit, ""
    assert result == 0
    return payload, git_commit, _sha256(payload / "software-payload.lock.json")


def test_payload_lock_is_bound_to_every_file_and_real_component_identity(tmp_path: Path):
    payload, git_commit, digest = _make_payload(tmp_path)

    lock = load_and_validate_payload(
        payload,
        expected_sha256=digest,
        expected_git_commit=git_commit,
    )

    assert set(lock["components"]) == {
        "hardwareRuntime",
        "enrollment",
        "remoteSupport",
        "factoryTest",
        "firstBoot",
    }
    assert all(component["releaseId"] for component in lock["components"].values())
    (payload / "config/enrollment.env").write_text("tampered\n", encoding="ascii")
    with pytest.raises(ImageSoftwareError, match="inventory"):
        load_and_validate_payload(
            payload,
            expected_sha256=digest,
            expected_git_commit=git_commit,
        )


def test_payload_lock_generator_refuses_unapproved_cellular_facts(tmp_path: Path):
    payload, _git_commit, digest = _make_payload(tmp_path, hil_approved=False)

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


@pytest.mark.parametrize(
    "replacement",
    [
        (
            "ECOBIN_ENROLLMENT_BACKEND_URL=https://api.jinshoubao.com\n"
            "ECOBIN_ENROLLMENT_KEY_ID=K1\n"
            "ECOBIN_ENROLLMENT_MODE=LEGACY_ADOPTION\n"
        ),
        (
            "ECOBIN_ENROLLMENT_BACKEND_URL=https://api.jinshoubao.com\n"
            "ECOBIN_ENROLLMENT_KEY_ID=K1\n"
            "ECOBIN_ENROLLMENT_MODE=SELF_ENROLLMENT\n"
            "ECOBIN_ENROLLMENT_PASSWORD=must-not-enter-an-image\n"
        ),
        (
            "ECOBIN_ENROLLMENT_BACKEND_URL=https://api.jinshoubao.com\n"
            "ECOBIN_ENROLLMENT_KEY_ID=K1\n"
            "ECOBIN_ENROLLMENT_MODE=SELF_ENROLLMENT\n"
            "ECOBIN_ENROLLMENT_K1=must-not-enter-an-image\n"
        ),
    ],
)
def test_payload_lock_refuses_legacy_or_secret_enrollment_configuration(
    tmp_path: Path, replacement: str
) -> None:
    def mutate(payload: Path) -> None:
        (payload / "config/enrollment.env").write_text(
            replacement,
            encoding="utf-8",
        )

    payload, _git_commit, digest = _make_payload(
        tmp_path,
        mutate_before_lock=mutate,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


@pytest.mark.parametrize(
    "bad_line",
    [
        "ECOBIN_CELLULAR_USB_VID=19d1",
        "ECOBIN_CELLULAR_PROBE_IPV4=127.0.0.1",
        "ECOBIN_CELLULAR_HTTPS_PROBE_URL=https://api.jinshoubao.com/health?token=x",
    ],
)
def test_payload_lock_refuses_extra_or_malformed_cellular_facts(
    tmp_path: Path, bad_line: str
) -> None:
    def mutate(payload: Path) -> None:
        path = payload / "config/cellular.env"
        lines = path.read_text(encoding="utf-8").splitlines()
        key = bad_line.split("=", 1)[0]
        if any(line.startswith(f"{key}=") for line in lines):
            lines = [bad_line if line.startswith(f"{key}=") else line for line in lines]
        else:
            lines.append(bad_line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload, _git_commit, digest = _make_payload(
        tmp_path,
        mutate_before_lock=mutate,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


@pytest.mark.parametrize("mutation", ["private-key", "extra-file", "empty-key"])
def test_payload_lock_refuses_unsafe_trust_store_entries(
    tmp_path: Path, mutation: str
) -> None:
    def mutate(payload: Path) -> None:
        if mutation == "private-key":
            (payload / "trust/mcu-release-keys/RELEASE_2026_01.pem").write_bytes(
                _ed25519_private_key_pem(3)
            )
        elif mutation == "extra-file":
            (payload / "trust/runtime-release-keys/README.txt").write_text(
                "not a trust key\n",
                encoding="ascii",
            )
        else:
            (payload / "trust/runtime-release-keys/factory_2026.pem").write_bytes(
                b""
            )

    payload, _git_commit, digest = _make_payload(
        tmp_path,
        mutate_before_lock=mutate,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


def test_installer_enables_only_early_safety_units_and_audit_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "posix" and os.geteuid() != 0:
        pytest.skip("root-owned target-program fixtures require a privileged Linux test")
    payload, git_commit, digest = _make_payload(tmp_path)
    rootfs = tmp_path / "rootfs"
    systemd = rootfs / "etc/systemd/system"
    ecobin_root = rootfs / "opt/ecobin"
    ecobin_root.mkdir(parents=True)
    os.chmod(ecobin_root, 0o700)
    factory_test_root = ecobin_root / "factory-test"
    factory_test_root.mkdir()
    os.chmod(factory_test_root, 0o700)
    (rootfs / "usr/sbin").mkdir(parents=True)
    systemd.mkdir(parents=True)
    for name in ("hostapd", "dnsmasq"):
        executable = rootfs / "usr/sbin" / name
        executable.write_text("#!/bin/sh\n", encoding="ascii")
        os.chmod(executable, 0o755)
    (systemd / "ecobin-mcu-safe-gpio.service").write_bytes(
        (HARDWARE_ROOT / "ecobin-mcu-safe-gpio.service").read_bytes()
    )
    wants = systemd / "multi-user.target.wants"
    wants.mkdir()
    try:
        (wants / "ecobin-mcu-safe-gpio.service").symlink_to(
            "../ecobin-mcu-safe-gpio.service"
        )
    except OSError as exc:
        pytest.skip(f"host does not permit symbolic-link fixtures: {exc}")

    metadata = install_image_software(
        rootfs,
        REPOSITORY_ROOT,
        payload,
        payload_sha256=digest,
        release_id="image-001",
        version="1.0.0",
        git_commit=git_commit,
    )

    if os.name == "posix":
        assert stat.S_IMODE(ecobin_root.stat().st_mode) == 0o755
        assert stat.S_IMODE(factory_test_root.stat().st_mode) == 0o755

    factory_app = (
        rootfs / "opt/ecobin/factory-test/releases/factory-001/app"
    )
    if os.name == "posix":
        assert stat.S_IMODE(factory_app.parent.stat().st_mode) == 0o755
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o755
            for path in (
                factory_app,
                *(item for item in factory_app.rglob("*") if item.is_dir()),
            )
        )
    _assert_isolated_app_imports(
        factory_app,
        "first_boot.orchestrator",
        "factory.acceptance_service",
        "device_credentials",
    )

    assert metadata["components"]["firstBoot"]["releaseId"] == "first-boot-001"
    assert (systemd / "network-pre.target.requires/ecobin-first-boot.service").is_symlink()
    assert (systemd / "sysinit.target.wants/ecobin-factory-egress-lock.service").is_symlink()
    assert not os.path.lexists(
        systemd / "multi-user.target.wants/ecobin-hardware.service"
    )
    assert os.readlink(systemd / "nftables.service") == "/dev/null"
    audit_image_software(
        rootfs,
        REPOSITORY_ROOT,
        payload_root=payload,
        expected_payload_sha256=digest,
        expected_release_id="image-001",
        expected_version="1.0.0",
        expected_git_commit=git_commit,
    )
    direct_audit = subprocess.run(
        [
            sys.executable,
            "-I",
            str(IMAGE_SOFTWARE_INSTALLER),
            "audit",
            "--rootfs",
            str(rootfs),
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--payload",
            str(payload),
            "--payload-sha256",
            digest,
            "--release-id",
            "image-001",
            "--version",
            "1.0.0",
            "--git-commit",
            git_commit,
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    assert direct_audit.returncode == 0, direct_audit.stderr
    assert "image-software-audit=PASS" in direct_audit.stdout
    with monkeypatch.context() as contract_patch:
        contract_patch.setattr(
            image_installer,
            "_factory_contract_facts",
            lambda _repository: {
                "oneNetCommandEnvelopeSchemaVersion": 2,
                "factorySealAuthorizationSchemaVersion": 1,
                "factorySealDatabaseMigration": "V57",
            },
        )
        with pytest.raises(ImageSoftwareError, match="contract facts differ"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    (rootfs / "etc/ecobin/hardware.env").write_text("tampered\n", encoding="ascii")
    with pytest.raises(ImageSoftwareError, match="controlled source"):
        audit_image_software(rootfs, REPOSITORY_ROOT)
