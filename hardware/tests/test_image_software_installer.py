from __future__ import annotations

import base64
import hashlib
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from factory_seal.validation import FactorySealPaths, collect_local_factory_facts
from first_boot.management_layer import (
    ImageManagedLayerPaths,
    image_managed_layer_complete,
)
from install.runtime_release import RUNTIME_APP_FILES as SIGNED_RUNTIME_APP_FILES
from install.runtime_payload_manifest import BUSINESS_APP_FILES
from system import image_software_installer as image_installer
from system.image_software_installer import (
    COMMUNICATION_AGENT_FILES,
    DEVICE_UPDATER_FILES,
    DEVICE_UPDATER_HELPER_FILES,
    DEVICE_UPDATER_HELPER_UNIT_FILES,
    FACTORY_APP_RUNTIME_FILES,
    LEGACY_RUNTIME_APP_FILES,
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
    assert "camera_selection.py" in RUNTIME_APP_FILES
    assert "camera_selection.py" in FACTORY_APP_RUNTIME_FILES
    assert "trusted_clock.py" in RUNTIME_APP_FILES
    assert "trusted_clock.py" in FACTORY_APP_RUNTIME_FILES
    assert "factory_progress.py" in RUNTIME_APP_FILES
    assert "factory_progress.py" in FACTORY_APP_RUNTIME_FILES
    assert "factory_progress.py" in image_installer.ENROLLMENT_FILES
    assert "device_runtime_projection.py" in image_installer.ENROLLMENT_FILES
    assert "business_identity.py" in image_installer.ENROLLMENT_FILES
    assert "communication_credentials.py" in image_installer.ENROLLMENT_FILES
    assert set(COMMUNICATION_AGENT_FILES) == {
        "cloud_transport.py",
        "communication_agent.py",
        "communication_credentials.py",
        "communication_router.py",
        "communication_store.py",
        "direct_onenet_transport.py",
        "local_control.py",
        "onenet_projection_model.json",
        "onenet_wire.py",
        "trusted_clock.py",
    }
    assert set(DEVICE_UPDATER_FILES) == {
        "job_safety.py",
        "uart2_protocol.py",
        "business_runtime_cutover.py",
        "business_runtime_cutover_state.py",
        "business_update_coordinator.py",
        "business_update_downloader.py",
        "business_update_reporter.py",
        "business_update_package.py",
        "business_update_store.py",
        "device_software_state_reporter.py",
        "device_management_preflight.py",
        "install/__init__.py",
        "install/business_release.py",
        "install/runtime_payload_manifest.py",
        "install/runtime_release.py",
        "local_control.py",
        "mcu_firmware_package.py",
        "mcu_update_coordinator.py",
        "mcu_update_package.py",
        "mcu_update_store.py",
        "onenet_projection_model.json",
        "onenet_wire.py",
        "trusted_clock.py",
        "updater_agent.py",
        "updater_control_cli.py",
        "updater_store.py",
    }
    assert set(DEVICE_UPDATER_HELPER_FILES) == {
        "__init__.py",
        "privileged_control.py",
        "business_activation_helper.py",
        "business_activation_candidate_helper.py",
        "business_activation_primitives.py",
        "business_release_activation_candidate_helper.py",
        "mcu_flash_helper.py",
        "mcu_flash_candidate_helper.py",
        "mcu_flash_primitives.py",
        "mcu_flash_recovery.py",
        "updater_mutation_authorizer.py",
    }
    assert set(DEVICE_UPDATER_HELPER_UNIT_FILES) == {
        "ecobin-business-activation-helper.socket",
        "ecobin-business-activation-helper@.service",
        "ecobin-mcu-flash-helper.socket",
        "ecobin-mcu-flash-helper@.service",
        "ecobin-business-activation-candidate-helper.socket",
        "ecobin-business-activation-candidate-helper@.service",
        "ecobin-business-release-activation-candidate-helper.socket",
        "ecobin-business-release-activation-candidate-helper@.service",
        "ecobin-mcu-flash-candidate-helper.socket",
        "ecobin-mcu-flash-candidate-helper@.service",
    }
    assert "business_control.py" in RUNTIME_APP_FILES
    assert "business_runtime_cutover_state.py" in FACTORY_APP_RUNTIME_FILES
    assert "business_runtime_cutover_state.py" in DEVICE_UPDATER_FILES
    assert "fixed_frame_mcu_maintenance.py" in RUNTIME_APP_FILES
    assert "job_safety.py" in RUNTIME_APP_FILES
    assert "native_recovery_close_isolation.py" in RUNTIME_APP_FILES
    assert "native_recovery_entry.py" in RUNTIME_APP_FILES
    assert "native_recovery_runtime.py" in RUNTIME_APP_FILES
    assert "native_device_entry_url.py" in RUNTIME_APP_FILES
    assert "native_device_entry_url.py" in BUSINESS_APP_FILES
    assert "local_control.py" in RUNTIME_APP_FILES
    assert "communication_agent.py" not in RUNTIME_APP_FILES
    assert "communication_store.py" not in RUNTIME_APP_FILES
    assert "updater_agent.py" not in RUNTIME_APP_FILES
    assert "updater_store.py" not in RUNTIME_APP_FILES
    assert "updater_control_cli.py" not in RUNTIME_APP_FILES
    assert isinstance(LEGACY_RUNTIME_APP_FILES, tuple)
    assert len(LEGACY_RUNTIME_APP_FILES) == 40
    assert "business_control.py" not in LEGACY_RUNTIME_APP_FILES
    assert "fixed_frame_mcu_maintenance.py" not in LEGACY_RUNTIME_APP_FILES
    assert "local_control.py" not in LEGACY_RUNTIME_APP_FILES
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


def test_device_updater_staged_inventory_starts_from_its_isolated_app(
    tmp_path: Path,
) -> None:
    """Lock the permanent updater's installed import closure, not the repo tree."""

    app = tmp_path / "updater" / "app"
    for relative in DEVICE_UPDATER_FILES:
        source = HARDWARE_ROOT / relative
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, str(app / "updater_agent.py"), "--help"],
        cwd=app,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--enable-remote-business-update" in completed.stdout


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
        # Windows real-time scanning can briefly hold freshly copied Python
        # modules.  The Linux image/CI path keeps the tighter bound.
        timeout=60 if os.name == "nt" else 30,
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
    communication_release_id: str = "communication-001",
    updater_release_id: str = "updater-001",
) -> tuple[Path, str, str]:
    payload = tmp_path / "payload"
    payload.mkdir()
    git_commit = "a" * 40
    _write_runtime(payload, git_commit, "runtime-001")
    for name in ("enrollment", "remote-support", "factory-test"):
        _make_venv(payload / f"components/{name}-venv")
    for component, files in (
        ("communication-agent", COMMUNICATION_AGENT_FILES),
        ("device-updater", DEVICE_UPDATER_FILES),
    ):
        app = payload / "components" / component / "app"
        app.mkdir(parents=True)
        for name in files:
            target = app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((HARDWARE_ROOT / name).read_bytes())
    _make_venv(payload / "components/communication-agent/.venv")
    _make_venv(payload / "components/device-updater/.venv")
    updater_root = payload / "components/device-updater"
    for relative, files, source_root in (
        (
            "helpers",
            DEVICE_UPDATER_HELPER_FILES,
            HARDWARE_ROOT / "device_management/helpers",
        ),
        (
            "systemd",
            DEVICE_UPDATER_HELPER_UNIT_FILES,
            HARDWARE_ROOT / "device_management/helpers/systemd",
        ),
    ):
        destination = updater_root / relative
        destination.mkdir()
        for name in files:
            (destination / name).write_bytes((source_root / name).read_bytes())
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
        ("business-release-keys", "business_2026.pem", 3),
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
            "--communication-agent-release-id",
            communication_release_id,
            "--device-updater-release-id",
            updater_release_id,
        ]
    )
    if not hil_approved or not expect_valid:
        assert result == 2
        return payload, git_commit, ""
    assert result == 0
    return payload, git_commit, _sha256(payload / "software-payload.lock.json")


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory modes are required")
def test_payload_lock_rejects_private_service_component_directory(
    tmp_path: Path,
) -> None:
    def make_runtime_app_private(payload: Path) -> None:
        os.chmod(payload / "components/hardware-runtime/app", 0o700)

    payload, _git_commit, digest = _make_payload(
        tmp_path,
        mutate_before_lock=make_runtime_app_private,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


def _convert_payload_to_legacy_v1(
    payload: Path,
    git_commit: str,
) -> str:
    lock_path = payload / "software-payload.lock.json"
    lock_path.unlink()
    shutil.rmtree(payload / "components/communication-agent")
    shutil.rmtree(payload / "components/device-updater")
    runtime = payload / "components/hardware-runtime"
    for name in (
        "business_control.py",
        "fixed_frame_mcu_maintenance.py",
        "local_control.py",
    ):
        (runtime / "app" / name).unlink()
    checksum_path = runtime / "SHA256SUMS"
    checksum_path.write_text(
        "".join(
            line
            for line in checksum_path.read_text(encoding="ascii").splitlines(keepends=True)
            if not line.rstrip().endswith(
                (
                    "app/business_control.py",
                    "app/fixed_frame_mcu_maintenance.py",
                    "app/local_control.py",
                )
            )
        ),
        encoding="ascii",
    )
    namespace = runpy.run_path(
        str(PAYLOAD_LOCK_GENERATOR), run_name="legacy_payload_lock_generator"
    )
    document = {
        "schemaVersion": 1,
        "lockState": "LOCKED",
        "payloadId": "payload-v1-001",
        "sourceGitCommit": git_commit,
        "components": {
            "hardwareRuntime": {
                "releaseId": "runtime-001",
                "root": "components/hardware-runtime",
            },
            "enrollment": {
                "releaseId": "enrollment-001",
                "venv": "components/enrollment-venv",
            },
            "remoteSupport": {
                "releaseId": "remote-001",
                "venv": "components/remote-support-venv",
            },
            "factoryTest": {
                "releaseId": "factory-001",
                "venv": "components/factory-test-venv",
            },
            "firstBoot": {"releaseId": "first-boot-001"},
        },
        "entries": namespace["_inventory"](payload),
    }
    lock_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(lock_path, 0o644)
    return _sha256(lock_path)


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
        "communicationAgent",
        "deviceUpdater",
    }
    assert lock["schemaVersion"] == 2
    assert all(component["releaseId"] for component in lock["components"].values())
    (payload / "config/enrollment.env").write_text("tampered\n", encoding="ascii")
    with pytest.raises(ImageSoftwareError, match="inventory"):
        load_and_validate_payload(
            payload,
            expected_sha256=digest,
            expected_git_commit=git_commit,
        )


def test_payload_requires_an_independent_communication_dependency_environment(
    tmp_path: Path,
) -> None:
    def remove_launcher(payload: Path) -> None:
        (payload / "components/communication-agent/.venv/bin/python").unlink()

    payload, _git_commit, digest = _make_payload(
        tmp_path,
        mutate_before_lock=remove_launcher,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


def test_legacy_v1_payload_remains_readable_but_cannot_build_a_new_image(
    tmp_path: Path,
) -> None:
    payload, git_commit, _digest = _make_payload(tmp_path)
    digest = _convert_payload_to_legacy_v1(payload, git_commit)

    lock = load_and_validate_payload(
        payload,
        expected_sha256=digest,
        expected_git_commit=git_commit,
    )

    assert lock["schemaVersion"] == 1
    assert set(lock["components"]) == set(image_installer.LEGACY_COMPONENT_NAMES)
    rootfs = tmp_path / "new-image-rootfs"
    rootfs.mkdir()
    with pytest.raises(ImageSoftwareError, match="requires schema-v2"):
        install_image_software(
            rootfs,
            REPOSITORY_ROOT,
            payload,
            payload_sha256=digest,
            release_id="new-image-001",
            version="1.0.0",
            git_commit=git_commit,
        )


def test_permanent_component_release_ids_fit_device_fact_contract(
    tmp_path: Path,
) -> None:
    payload, _git_commit, digest = _make_payload(
        tmp_path,
        communication_release_id="a" * 33,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


def test_validator_rejects_oversize_permanent_component_release_id(
    tmp_path: Path,
) -> None:
    payload, git_commit, _digest = _make_payload(tmp_path)
    lock_path = payload / "software-payload.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["components"]["deviceUpdater"]["releaseId"] = "u" * 33
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ImageSoftwareError, match="device fact limit"):
        load_and_validate_payload(
            payload,
            expected_sha256=_sha256(lock_path),
            expected_git_commit=git_commit,
        )


@pytest.mark.parametrize(
    "relative",
    [
        "components/communication-agent/app/communication_agent.py",
        "components/device-updater/helpers/business_activation_primitives.py",
        "components/device-updater/systemd/ecobin-mcu-flash-helper@.service",
    ],
)
def test_payload_lock_refuses_permanent_component_source_drift(
    tmp_path: Path,
    relative: str,
) -> None:
    def mutate(payload: Path) -> None:
        (payload / relative).write_text(
            "# substituted permanent agent\n",
            encoding="utf-8",
        )

    payload, _git_commit, digest = _make_payload(
        tmp_path,
        mutate_before_lock=mutate,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


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


@pytest.mark.parametrize(
    "mutation", ["private-key", "extra-file", "empty-key", "reused-key"]
)
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
        elif mutation == "empty-key":
            (payload / "trust/runtime-release-keys/factory_2026.pem").write_bytes(
                b""
            )
        else:
            (payload / "trust/business-release-keys/business_2026.pem").write_bytes(
                (payload / "trust/runtime-release-keys/factory_2026.pem").read_bytes()
            )

    payload, _git_commit, digest = _make_payload(
        tmp_path,
        mutate_before_lock=mutate,
        expect_valid=False,
    )

    assert digest == ""
    assert not (payload / "software-payload.lock.json").exists()


def _install_image_for_public_trust_audit(tmp_path: Path) -> Path:
    payload, git_commit, digest = _make_payload(tmp_path)
    rootfs = tmp_path / "rootfs"
    systemd = rootfs / "etc/systemd/system"
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
    (wants / "ecobin-mcu-safe-gpio.service").symlink_to(
        "../ecobin-mcu-safe-gpio.service"
    )

    previous_umask = os.umask(0o077)
    try:
        install_image_software(
            rootfs,
            REPOSITORY_ROOT,
            payload,
            payload_sha256=digest,
            release_id="public-trust-audit-001",
            version="1.0.0",
            git_commit=git_commit,
        )
    finally:
        os.umask(previous_umask)
    return rootfs


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() != 0,
    reason="root-owned trust metadata requires a privileged Linux test",
)
def test_audit_rejects_unsafe_public_runtime_trust_path_metadata(
    tmp_path: Path,
) -> None:
    rootfs = _install_image_for_public_trust_audit(tmp_path)
    trust_store = rootfs / "usr/share/ecobin/runtime-release-keys"
    public_key = trust_store / "factory_2026.pem"

    os.chmod(trust_store, 0o750)
    try:
        with pytest.raises(ImageSoftwareError, match="root-owned 0755"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        os.chmod(trust_store, 0o755)

    os.chown(trust_store, 1, 1)
    try:
        with pytest.raises(ImageSoftwareError, match="root-owned 0755"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        os.chown(trust_store, 0, 0)

    os.chmod(public_key, 0o600)
    try:
        with pytest.raises(ImageSoftwareError, match="permissions are invalid"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        os.chmod(public_key, 0o644)

    os.chown(public_key, 1, 1)
    try:
        with pytest.raises(ImageSoftwareError, match="not owned by root"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        os.chown(public_key, 0, 0)

    hardlink_source = tmp_path / "runtime-release-key-hardlink-source.pem"
    public_key.rename(hardlink_source)
    os.link(hardlink_source, public_key)
    try:
        with pytest.raises(ImageSoftwareError, match="single-link"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        public_key.unlink()
        hardlink_source.rename(public_key)

    linked_child_target = tmp_path / "linked-trust-child"
    linked_child_target.mkdir()
    linked_child = trust_store / "linked-child"
    linked_child.symlink_to(linked_child_target, target_is_directory=True)
    try:
        with pytest.raises(ImageSoftwareError, match="unsupported name|single-link"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        linked_child.unlink()

    trust_store_target = trust_store.with_name("runtime-release-keys-real")
    trust_store.rename(trust_store_target)
    trust_store.symlink_to(trust_store_target.name, target_is_directory=True)
    try:
        with pytest.raises(ImageSoftwareError, match="root-owned 0755"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        trust_store.unlink()
        trust_store_target.rename(trust_store)

    share = rootfs / "usr/share"
    share_target = rootfs / "usr/share-real"
    share.rename(share_target)
    share.symlink_to(share_target.name, target_is_directory=True)
    try:
        with pytest.raises(ImageSoftwareError, match="root-owned 0755"):
            audit_image_software(rootfs, REPOSITORY_ROOT)
    finally:
        share.unlink()
        share_target.rename(share)


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

    previous_umask = os.umask(0o077) if os.name == "posix" else None
    try:
        metadata = install_image_software(
            rootfs,
            REPOSITORY_ROOT,
            payload,
            payload_sha256=digest,
            release_id="image-001",
            version="1.0.0",
            git_commit=git_commit,
        )
    finally:
        if previous_umask is not None:
            os.umask(previous_umask)

    assert image_managed_layer_complete(
        ImageManagedLayerPaths(
            private_image_release=rootfs / "etc/ecobin/image-release.json",
            public_image_release=rootfs / "usr/share/ecobin/image-release.json",
            software_payload_lock=(
                rootfs / "usr/share/ecobin/software-payload.lock.json"
            ),
            release_environment=(
                rootfs / "usr/share/ecobin/device-management-release.env"
            ),
        ),
        expected_owner=None if os.name != "posix" else (0, 0),
    )

    if os.name == "posix":
        assert stat.S_IMODE(ecobin_root.stat().st_mode) == 0o755
        assert stat.S_IMODE(factory_test_root.stat().st_mode) == 0o755
        hardware_release = ecobin_root / "hardware/releases/runtime-001"
        communication_release = (
            ecobin_root / "communication/releases/communication-001"
        )
        updater_release = ecobin_root / "updater/releases/updater-001"
        for release in (
            hardware_release,
            communication_release,
            updater_release,
        ):
            assert all(
                stat.S_IMODE(path.stat().st_mode) == 0o755
                for path in (
                    release,
                    *(
                        item
                        for item in release.rglob("*")
                        if item.is_dir() and not item.is_symlink()
                    ),
                )
            )
        remote_support_root = ecobin_root / "remote-support"
        remote_release = remote_support_root / "releases/remote-001"
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o755
            for path in (
                remote_support_root,
                remote_support_root / "releases",
                remote_release,
                remote_release / "app",
            )
        )

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
    assert metadata["schemaVersion"] == 1
    assert metadata["softwarePayloadSchemaVersion"] == 2
    assert metadata["components"]["communicationAgent"]["releaseId"] == "communication-001"
    assert metadata["components"]["deviceUpdater"]["releaseId"] == "updater-001"
    assert os.readlink(rootfs / "opt/ecobin/communication/current") == (
        "releases/communication-001"
    )
    assert os.readlink(rootfs / "opt/ecobin/updater/current") == (
        "releases/updater-001"
    )
    assert (
        rootfs / "usr/share/ecobin/device-management-release.env"
    ).read_text(encoding="ascii") == (
        "ECOBIN_COMMUNICATION_AGENT_VERSION=communication-001\n"
        "ECOBIN_DEVICE_UPDATER_VERSION=updater-001\n"
    )
    assert (
        rootfs / "usr/share/ecobin/runtime-release-keys/factory_2026.pem"
    ).read_bytes() == (payload / "trust/runtime-release-keys/factory_2026.pem").read_bytes()
    assert (
        rootfs / "usr/share/ecobin/business-release-keys/business_2026.pem"
    ).read_bytes() == (
        payload / "trust/business-release-keys/business_2026.pem"
    ).read_bytes()
    private_release = rootfs / "etc/ecobin/image-release.json"
    public_release = rootfs / "usr/share/ecobin/image-release.json"
    assert public_release.read_bytes() == private_release.read_bytes()
    factory_report = json.loads(
        (
            HARDWARE_ROOT
            / "image-artifacts/evidence/"
            "hil-v8-simulated-acceptance-20260829-01/report.json"
        ).read_text(encoding="utf-8")
    )
    factory_report["imageReleaseId"] = metadata["releaseId"]
    factory_report_path = tmp_path / "factory-report.json"
    factory_report_path.write_text(
        json.dumps(factory_report),
        encoding="utf-8",
    )
    seal_facts = collect_local_factory_facts(
        FactorySealPaths(
            image_release=private_release,
            factory_report=factory_report_path,
            sealed=tmp_path / "sealed.json",
        )
    )
    assert seal_facts.image_release_id == "image-001"
    if os.name == "posix":
        assert stat.S_IMODE(public_release.stat().st_mode) == 0o644
    assert (
        systemd / "multi-user.target.wants/ecobin-first-boot.service"
    ).is_symlink()
    assert (systemd / "network-pre.target.requires/ecobin-first-boot.service").is_symlink()
    assert (systemd / "sysinit.target.wants/ecobin-factory-egress-lock.service").is_symlink()
    assert not os.path.lexists(
        systemd / "multi-user.target.wants/ecobin-hardware.service"
    )
    assert not os.path.lexists(
        systemd / "multi-user.target.wants/ecobin-communication.service"
    )
    assert not os.path.lexists(
        systemd / "multi-user.target.wants/ecobin-updater.service"
    )
    assert not os.path.lexists(
        systemd / "multi-user.target.wants/ecobin-business-permission-preflight.service"
    )
    assert not os.path.lexists(
        systemd / "multi-user.target.wants/ecobin-device-management-preflight.service"
    )
    assert (
        systemd / "ecobin-business-permission-preflight.service"
    ).read_bytes() == (
        HARDWARE_ROOT / "ecobin-business-permission-preflight.service"
    ).read_bytes()
    assert (
        systemd / "ecobin-device-management-preflight.service"
    ).read_bytes() == (
        HARDWARE_ROOT / "ecobin-device-management-preflight.service"
    ).read_bytes()
    assert (
        rootfs / "usr/lib/ecobin/business_runtime_preflight.py"
    ).read_bytes() == (
        HARDWARE_ROOT / "system/business_runtime_preflight.py"
    ).read_bytes()
    assert (
        rootfs / "usr/lib/ecobin/camera_selection.py"
    ).read_bytes() == (HARDWARE_ROOT / "camera_selection.py").read_bytes()
    assert (
        rootfs / "usr/lib/ecobin/device-management/local_control.py"
    ).read_bytes() == (HARDWARE_ROOT / "local_control.py").read_bytes()
    for name in DEVICE_UPDATER_HELPER_FILES:
        assert (
            rootfs / "usr/lib/ecobin/device-management/helpers" / name
        ).read_bytes() == (
            HARDWARE_ROOT / "device_management/helpers" / name
        ).read_bytes()
    for name in DEVICE_UPDATER_HELPER_UNIT_FILES:
        assert (systemd / name).read_bytes() == (
            HARDWARE_ROOT / "device_management/helpers/systemd" / name
        ).read_bytes()
    assert (
        rootfs / "usr/lib/sysusers.d/ecobin-device-runtime.conf"
    ).read_bytes() == (
        HARDWARE_ROOT
        / "device_management/config/sysusers.d/ecobin-device-runtime.conf"
    ).read_bytes()
    assert (
        rootfs / "usr/lib/tmpfiles.d/ecobin-device-runtime.conf"
    ).read_bytes() == (
        HARDWARE_ROOT
        / "device_management/config/tmpfiles.d/ecobin-device-runtime.conf"
    ).read_bytes()
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
    if os.name == "posix":
        private_runtime_directory = (
            rootfs / "opt/ecobin/hardware/releases/runtime-001/app"
        )
        os.chmod(private_runtime_directory, 0o700)
        try:
            with pytest.raises(ImageSoftwareError, match="root-owned 0755"):
                audit_image_software(rootfs, REPOSITORY_ROOT)
        finally:
            os.chmod(private_runtime_directory, 0o755)
    forbidden_helper_link = (
        systemd
        / "multi-user.target.wants/ecobin-business-activation-helper.socket"
    )
    forbidden_helper_link.symlink_to(
        "../ecobin-business-activation-helper.socket"
    )
    with pytest.raises(ImageSoftwareError, match="independently enabled"):
        audit_image_software(rootfs, REPOSITORY_ROOT)
    forbidden_helper_link.unlink()
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
    installed_agent = (
        rootfs
        / "opt/ecobin/communication/releases/communication-001/app/communication_agent.py"
    )
    controlled_agent = (HARDWARE_ROOT / "communication_agent.py").read_bytes()
    installed_agent.write_text("# tampered\n", encoding="ascii")
    with pytest.raises(ImageSoftwareError, match="controlled source|installed tree"):
        audit_image_software(rootfs, REPOSITORY_ROOT)
    installed_agent.write_bytes(controlled_agent)
    os.chmod(installed_agent, 0o644)
    (rootfs / "etc/ecobin/hardware.env").write_text("tampered\n", encoding="ascii")
    with pytest.raises(ImageSoftwareError, match="controlled source"):
        audit_image_software(rootfs, REPOSITORY_ROOT)
