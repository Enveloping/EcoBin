from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Sequence

import pytest

from system.device_management_maintenance_installer import (
    ACCOUNT_NAMES,
    ACCOUNT_GROUPS,
    ACTIVE_MARKER,
    DROP_IN_CONTENT,
    DROP_IN_RELATIVE,
    GROUP_NAMES,
    IPC_GROUP_MEMBERS,
    LEGACY_GATE_DROP_IN_CONTENT,
    LEGACY_GATE_DROP_IN_PATH,
    LEGACY_GATE_DROP_IN_SHA256,
    HELPER_UNIT_FILES,
    LEGACY_SERVICE,
    MAIN_UNIT_FILES,
    MANIFEST_NAME,
    MCU_RECOVERY_MARKER,
    MCU_SAFE_GPIO_SERVICE,
    PENDING_MARKER,
    PREFLIGHT_PAYLOAD,
    RELEASE_ENV_PAYLOAD,
    START_UNITS,
    SYSUSERS_PAYLOAD,
    TMPFILES_DIRECTORY_MODES,
    TMPFILES_PAYLOAD,
    TMPFILES_REGULAR_PATHS,
    CommandResult,
    MaintenanceInstallError,
    MaintenanceInstaller,
    MaintenanceProcessInterrupted,
    build_payload_manifest,
    load_and_validate_payload,
    target_files,
    write_payload_manifest,
)
from install.runtime_payload_manifest import (
    COMMUNICATION_AGENT_FILES,
    DEVICE_UPDATER_FILES,
    DEVICE_UPDATER_HELPER_FILES,
)


IMAGE_RELEASE_ID = "image-v13"
IMAGE_VERSION = "0.1.0-single-card.20260831.13"
COMMUNICATION_RELEASE = "communication-20260903-16"
UPDATER_RELEASE = "updater-20260903-16"
PAYLOAD_ID = "stage3-maintenance-20260903-16"
GIT_COMMIT = "a" * 40


def _write(path: Path, value: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    os.chmod(path, mode)


def _public_key() -> bytes:
    der = bytes.fromhex("302a300506032b6570032100") + bytes(range(32))
    body = base64.b64encode(der).decode("ascii")
    return (
        "-----BEGIN PUBLIC KEY-----\n"
        f"{body}\n"
        "-----END PUBLIC KEY-----\n"
    ).encode("ascii")


def _make_payload(tmp_path: Path) -> tuple[Path, str]:
    payload = tmp_path / "payload"
    for name in COMMUNICATION_AGENT_FILES:
        _write(payload / "communication/app" / name, f"# communication {name}\n".encode())
    for name in DEVICE_UPDATER_FILES:
        _write(payload / "updater/app" / name, f"# updater {name}\n".encode())
    for name in DEVICE_UPDATER_HELPER_FILES:
        _write(payload / "updater/helpers" / name, f"# helper {name}\n".encode())
    for name in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
        _write(payload / "systemd" / name, f"[Unit]\nDescription={name}\n".encode())
    _write(payload / DROP_IN_RELATIVE, DROP_IN_CONTENT)
    _write(payload / SYSUSERS_PAYLOAD, b"# controlled sysusers\n")
    _write(payload / TMPFILES_PAYLOAD, b"# controlled tmpfiles\n")
    _write(payload / PREFLIGHT_PAYLOAD, b"# controlled permission preflight\n")
    _write(
        payload / RELEASE_ENV_PAYLOAD,
        (
            f"ECOBIN_COMMUNICATION_AGENT_VERSION={COMMUNICATION_RELEASE}\n"
            f"ECOBIN_DEVICE_UPDATER_VERSION={UPDATER_RELEASE}\n"
        ).encode(),
    )
    _write(payload / "trust/runtime-release-keys/factory_2026.pem", _public_key())
    digest = write_payload_manifest(
        payload,
        payload_id=PAYLOAD_ID,
        source_git_commit=GIT_COMMIT,
        expected_image_release_id=IMAGE_RELEASE_ID,
        expected_image_version=IMAGE_VERSION,
        communication_release_id=COMMUNICATION_RELEASE,
        updater_release_id=UPDATER_RELEASE,
    )
    return payload, digest


def _rewrite_manifest(payload: Path, update) -> str:
    path = payload / MANIFEST_NAME
    document = json.loads(path.read_text(encoding="utf-8"))
    update(document)
    raw = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    path.write_bytes(raw)
    os.chmod(path, 0o644)
    import hashlib

    return hashlib.sha256(raw).hexdigest()


class FakeSystem:
    def __init__(self, rootfs: Path) -> None:
        self.rootfs = rootfs
        self.identities_created = False
        self.fail_start: str | None = None
        self.fail_stop: str | None = None
        self.sticky_active: str | None = None
        self.spawn_helper_on_socket_stop: str | None = None
        self.legacy_drop_in_after_reload: str | None = None
        self.fail_reload = False
        self.calls: list[tuple[str, ...]] = []
        self.owners: dict[str, tuple[int, int]] = {}
        self.modes: dict[str, int] = {}
        self.states: dict[str, dict[str, str]] = {
            LEGACY_SERVICE: self._state(
                "loaded",
                "active",
                "/etc/systemd/system/ecobin-hardware.service",
                "running",
            ),
            MCU_SAFE_GPIO_SERVICE: self._state(
                "loaded",
                "active",
                "/etc/systemd/system/ecobin-mcu-safe-gpio.service",
                "exited",
            ),
            "ecobin-runtime.target": self._state(
                "loaded",
                "active",
                "/etc/systemd/system/ecobin-runtime.target",
                "active",
            ),
        }
        self.states[LEGACY_SERVICE]["DropInPaths"] = LEGACY_GATE_DROP_IN_PATH

    @staticmethod
    def _state(
        load: str, active: str, fragment: str = "", sub: str = "dead"
    ) -> dict[str, str]:
        return {
            "LoadState": load,
            "ActiveState": active,
            "SubState": sub,
            "UnitFileState": "static" if load == "loaded" else "",
            "FragmentPath": fragment,
            "DropInPaths": "",
        }

    def _show(self, unit: str) -> CommandResult:
        canonical = unit.replace("@maintenance-audit.service", "@.service")
        state = self.states.get(canonical, self._state("not-found", "inactive"))
        output = [f"Id={unit}"] + [f"{key}={value}" for key, value in state.items()]
        return CommandResult(0, "\n".join(output) + "\n", "")

    def metadata_identity(
        self, absolute: str, _details: os.stat_result
    ) -> tuple[int, int]:
        return self.owners.get(absolute, (0, 0))

    def metadata_mode(self, absolute: str, details: os.stat_result) -> int:
        if absolute in self.modes:
            return self.modes[absolute]
        if os.name == "posix":
            return stat.S_IMODE(details.st_mode)
        if stat.S_ISDIR(details.st_mode):
            return 0o755
        if stat.S_ISREG(details.st_mode):
            return 0o644
        return stat.S_IMODE(details.st_mode)

    def _make_tmpfiles(self) -> None:
        uid = {
            "ecobin-communication": 995,
            "ecobin-business": 994,
            "ecobin-updater": 993,
        }
        primary_gid = dict(uid)
        ipc_gid = {
            "ecobin-communication-ipc": 990,
            "ecobin-business-ipc": 989,
            "ecobin-updater-ipc": 988,
            "ecobin-privileged-helper-ipc": 987,
        }
        owners = {
            "/var/lib/ecobin/communication": (
                uid["ecobin-communication"],
                primary_gid["ecobin-communication"],
            ),
            "/var/lib/ecobin/business": (
                uid["ecobin-business"],
                primary_gid["ecobin-business"],
            ),
            "/var/lib/ecobin/business/photos": (
                uid["ecobin-business"],
                primary_gid["ecobin-business"],
            ),
            "/var/lib/ecobin/updater": (
                uid["ecobin-updater"],
                ipc_gid["ecobin-updater-ipc"],
            ),
            "/var/lib/ecobin/updater/staging": (
                uid["ecobin-updater"],
                primary_gid["ecobin-updater"],
            ),
            "/var/lib/ecobin/updater/mcu-firmware": (
                uid["ecobin-updater"],
                primary_gid["ecobin-updater"],
            ),
            "/var/lib/ecobin/privileged": (0, 0),
            "/var/lib/ecobin/privileged/business-snapshots": (0, 0),
            "/opt/ecobin/business": (0, primary_gid["ecobin-business"]),
            "/opt/ecobin/business/releases": (
                0,
                primary_gid["ecobin-business"],
            ),
            "/run/ecobin/communication": (
                uid["ecobin-communication"],
                ipc_gid["ecobin-communication-ipc"],
            ),
            "/run/ecobin/business": (
                uid["ecobin-business"],
                ipc_gid["ecobin-business-ipc"],
            ),
            "/run/ecobin/updater": (
                uid["ecobin-updater"],
                ipc_gid["ecobin-updater-ipc"],
            ),
            "/run/ecobin/privileged": (
                0,
                ipc_gid["ecobin-privileged-helper-ipc"],
            ),
        }
        for absolute, mode in TMPFILES_DIRECTORY_MODES.items():
            path = self.rootfs / absolute.lstrip("/")
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, mode)
            self.owners[absolute] = owners[absolute]
            self.modes[absolute] = mode
        for absolute in TMPFILES_REGULAR_PATHS:
            path = self.rootfs / absolute.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            os.chmod(path, 0o600)
            self.owners[absolute] = (0, 0)
            self.modes[absolute] = 0o600

    def _apply_systemd_directory_ownership(
        self, absolute: str, expected_owner: tuple[int, int]
    ) -> None:
        """Model systemd's conditional recursive ownership correction."""
        if self.owners.get(absolute) == expected_owner:
            return
        prefix = absolute + "/"
        for candidate in tuple(self.owners):
            if candidate == absolute or candidate.startswith(prefix):
                self.owners[candidate] = expected_owner

    def __call__(self, arguments: Sequence[str]) -> CommandResult:
        args = tuple(arguments)
        self.calls.append(args)
        if args[:2] == ("systemctl", "show"):
            return self._show(args[2])
        if args[:2] == ("systemctl", "list-units"):
            instances = [
                unit
                for unit, state in sorted(self.states.items())
                if "@" in unit
                and "@.service" not in unit
                and unit.endswith(".service")
                and (
                    unit.startswith("ecobin-business-activation-helper@")
                    or unit.startswith("ecobin-mcu-flash-helper@")
                )
                and state["LoadState"] == "loaded"
            ]
            return CommandResult(
                0,
                "".join(
                    f"{unit} loaded {self.states[unit]['ActiveState']} "
                    f"{self.states[unit]['SubState']} helper\n"
                    for unit in instances
                ),
            )
        if args[:2] == ("systemctl", "daemon-reload"):
            if self.fail_reload:
                return CommandResult(1, "", "injected daemon-reload failure")
            for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
                unit_path = self.rootfs / "etc/systemd/system" / unit
                if unit_path.exists():
                    self.states.setdefault(
                        unit,
                        self._state("loaded", "inactive", f"/etc/systemd/system/{unit}"),
                    )
                elif unit in self.states:
                    self.states[unit] = self._state("not-found", "inactive")
            for unit in tuple(self.states):
                if "@" not in unit or "@.service" in unit:
                    continue
                for template in HELPER_UNIT_FILES:
                    prefix = template.replace("@.service", "@")
                    if unit.startswith(prefix) and unit.endswith(".service"):
                        template_path = self.rootfs / "etc/systemd/system" / template
                        if not template_path.exists():
                            self.states[unit] = self._state("not-found", "inactive")
            runtime_drop_in = (
                self.rootfs
                / "etc/systemd/system/ecobin-runtime.target.d/"
                "50-device-management-maintenance.conf"
            )
            self.states["ecobin-runtime.target"]["DropInPaths"] = (
                "/etc/systemd/system/ecobin-runtime.target.d/"
                "50-device-management-maintenance.conf"
                if runtime_drop_in.is_file()
                else ""
            )
            if self.legacy_drop_in_after_reload is not None:
                self.states[LEGACY_SERVICE]["DropInPaths"] = (
                    self.legacy_drop_in_after_reload
                )
            return CommandResult(0)
        if args[:2] == ("systemctl", "start"):
            unit = args[2]
            if unit == self.fail_start:
                return CommandResult(1, "", "injected start failure")
            state = self.states.setdefault(
                unit,
                self._state("loaded", "inactive", f"/etc/systemd/system/{unit}"),
            )
            state.update(LoadState="loaded", ActiveState="active", SubState="running")
            if unit == "ecobin-communication.service":
                self._apply_systemd_directory_ownership(
                    "/var/lib/ecobin/communication", (995, 990)
                )
                _write(
                    self.rootfs / "var/lib/ecobin/communication/communication.db",
                    b"state",
                    0o600,
                )
            if unit == "ecobin-updater.service":
                self._apply_systemd_directory_ownership(
                    "/var/lib/ecobin/updater", (993, 988)
                )
                _write(
                    self.rootfs / "var/lib/ecobin/updater/updater.db",
                    b"state",
                    0o600,
                )
            return CommandResult(0)
        if args[:2] == ("systemctl", "stop"):
            unit = args[2]
            if unit == self.fail_stop:
                return CommandResult(1, "", "injected stop failure")
            state = self.states.setdefault(unit, self._state("loaded", "inactive"))
            if unit != self.sticky_active:
                state.update(ActiveState="inactive", SubState="dead")
            if unit.endswith(".socket") and self.spawn_helper_on_socket_stop:
                instance = self.spawn_helper_on_socket_stop
                template = instance.split("@", 1)[0] + "@.service"
                self.states[instance] = self._state(
                    "loaded",
                    "active",
                    f"/etc/systemd/system/{template}",
                    "running",
                )
                self.spawn_helper_on_socket_stop = None
            return CommandResult(0)
        if args[0] == "systemd-sysusers":
            self.identities_created = True
            return CommandResult(0)
        if args[:2] == ("systemd-tmpfiles", "--create"):
            self._make_tmpfiles()
            return CommandResult(0)
        if args[0] == "getent":
            database, name = args[1], args[2]
            if self.identities_created and name.isdigit():
                numeric = int(name)
                if database == "passwd" and numeric in (993, 994, 995):
                    account_name = ACCOUNT_NAMES[995 - numeric]
                    return CommandResult(
                        0,
                        f"{account_name}:x:{numeric}:{numeric}::{('/var/lib/ecobin/' + account_name.removeprefix('ecobin-'))}:/usr/sbin/nologin\n",
                    )
                if database == "group" and numeric in (993, 994, 995):
                    group_name = ACCOUNT_NAMES[995 - numeric]
                    return CommandResult(0, f"{group_name}:x:{numeric}:\n")
            exists = name in ("dialout", "video") or (
                self.identities_created
                and name in (*ACCOUNT_NAMES, *GROUP_NAMES)
            )
            if not exists:
                return CommandResult(2)
            if database == "passwd":
                uid = 995 - ACCOUNT_NAMES.index(name)
                return CommandResult(
                    0,
                    f"{name}:x:{uid}:{uid}::{('/var/lib/ecobin/' + name.removeprefix('ecobin-'))}:/usr/sbin/nologin\n",
                )
            if name in ACCOUNT_NAMES:
                gid = 995 - ACCOUNT_NAMES.index(name)
                members = ""
            elif name in GROUP_NAMES:
                gid = 990 - GROUP_NAMES.index(name)
                members = ",".join(sorted(IPC_GROUP_MEMBERS[name]))
            else:
                gid = 20 if name == "dialout" else 44
                members = "ecobin-business" if self.identities_created else ""
            return CommandResult(0, f"{name}:x:{gid}:{members}\n")
        if args[:2] == ("id", "-nG"):
            name = args[2]
            if not self.identities_created or name not in ACCOUNT_NAMES:
                return CommandResult(1)
            return CommandResult(0, " ".join(sorted(ACCOUNT_GROUPS[name])) + "\n")
        return CommandResult(1, "", f"unexpected command: {args}")


def _make_rootfs(tmp_path: Path) -> tuple[Path, FakeSystem, Path]:
    rootfs = tmp_path / "rootfs"
    for relative in (
        "etc/systemd/system",
        "usr/lib/sysusers.d",
        "usr/lib/tmpfiles.d",
        "usr/share/ecobin",
        "var/lib",
        "run/lock",
        "run/ecobin",
        "opt/ecobin/hardware/releases/runtime-v13/app/pkg",
        "opt/ecobin/hardware/releases/runtime-v13/.venv/bin",
    ):
        (rootfs / relative).mkdir(parents=True, exist_ok=True)
    identity = {
        "schemaVersion": 1,
        "releaseId": IMAGE_RELEASE_ID,
        "version": IMAGE_VERSION,
    }
    _write(
        rootfs / "usr/share/ecobin/image-release.json",
        (json.dumps(identity) + "\n").encode(),
    )
    _write(
        rootfs / "etc/ecobin/image-release.json",
        (json.dumps(identity) + "\n").encode(),
    )
    _write(rootfs / "etc/ecobin/hardware.env", b"ECOBIN_UART_PORT=/dev/ttyS5\n", 0o640)
    _write(rootfs / "etc/ecobin/device-credentials.json", b"{}\n", 0o600)
    _write(
        rootfs / "etc/systemd/system/ecobin-hardware.service",
        b"[Service]\nExecStart=/bin/true\n",
    )
    _write(
        rootfs / LEGACY_GATE_DROP_IN_PATH.lstrip("/"),
        LEGACY_GATE_DROP_IN_CONTENT,
        0o644,
    )
    _write(
        rootfs / "etc/systemd/system/ecobin-mcu-safe-gpio.service",
        b"[Service]\nType=oneshot\nExecStart=/bin/true\n",
    )
    _write(rootfs / "etc/systemd/system/ecobin-runtime.target", b"[Unit]\n")
    runtime = rootfs / "opt/ecobin/hardware/releases/runtime-v13"
    _write(runtime / "app/program.py", b"# legacy runtime\n")
    _write(runtime / "app/pkg/module.py", b"# package\n")
    _write(runtime / ".venv/bin/python", b"#!/bin/sh\n", 0o755)
    _write(runtime / ".venv/.ecobin-install-complete", b"complete\n", 0o600)
    current = rootfs / "opt/ecobin/hardware/current"
    current.symlink_to("releases/runtime-v13")
    for relative in (
        "opt/ecobin",
        "opt/ecobin/hardware",
        "opt/ecobin/hardware/releases",
        "opt/ecobin/hardware/releases/runtime-v13",
        "opt/ecobin/hardware/releases/runtime-v13/app",
        "opt/ecobin/hardware/releases/runtime-v13/app/pkg",
        "opt/ecobin/hardware/releases/runtime-v13/.venv",
        "opt/ecobin/hardware/releases/runtime-v13/.venv/bin",
    ):
        os.chmod(rootfs / relative, 0o700)
    fake = FakeSystem(rootfs)
    return rootfs, fake, runtime


def _installer(
    rootfs: Path,
    fake: FakeSystem,
    *,
    before_publish_hook=None,
    after_publish_hook=None,
) -> MaintenanceInstaller:
    return MaintenanceInstaller(
        rootfs=rootfs,
        runner=fake,
        enforce_root_ownership=False,
        check_host_tools=False,
        metadata_identity=fake.metadata_identity,
        metadata_mode=fake.metadata_mode,
        before_publish_hook=before_publish_hook,
        after_publish_hook=after_publish_hook,
    )


def _confirmations() -> dict[str, str]:
    return {
        "expected_image_release_id": IMAGE_RELEASE_ID,
        "expected_image_version": IMAGE_VERSION,
        "expected_legacy_service": LEGACY_SERVICE,
    }


def test_embedded_v13_gate_baseline_matches_the_image_source_file() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "first_boot/systemd/ecobin-hardware.service.d/20-first-boot-gate.conf"
    )

    assert source.read_bytes() == LEGACY_GATE_DROP_IN_CONTENT
    assert hashlib.sha256(LEGACY_GATE_DROP_IN_CONTENT).hexdigest() == (
        LEGACY_GATE_DROP_IN_SHA256
    )


def _maintenance_record(rootfs: Path) -> dict:
    marker_path = rootfs / ACTIVE_MARKER.lstrip("/")
    if not marker_path.exists():
        marker_path = rootfs / PENDING_MARKER.lstrip("/")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    record_path = (
        rootfs
        / "var/lib/ecobin/device-management-maintenance/backups"
        / marker["operationId"]
        / "record.json"
    )
    return json.loads(record_path.read_text(encoding="utf-8"))


def test_payload_manifest_is_an_exact_authenticated_allowlist(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)

    manifest = load_and_validate_payload(payload, digest)

    assert manifest.payload_id == PAYLOAD_ID
    assert manifest.communication_release_id == COMMUNICATION_RELEASE
    assert manifest.updater_release_id == UPDATER_RELEASE
    assert len(manifest.files) > 20


def test_payload_rejects_wrong_manifest_digest_and_unlisted_file(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)

    with pytest.raises(MaintenanceInstallError, match="manifest SHA-256 differs"):
        load_and_validate_payload(payload, "0" * 64)

    _write(payload / "unexpected.txt", b"not allowlisted\n")
    with pytest.raises(MaintenanceInstallError, match="inventory differs"):
        load_and_validate_payload(payload, digest)


def test_payload_rejects_symlink_and_hardlink(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    source = payload / "communication/app/communication_agent.py"
    source.unlink()
    source.symlink_to("communication_store.py")
    with pytest.raises(MaintenanceInstallError, match="symbolic link"):
        load_and_validate_payload(payload, digest)

    if os.name != "posix":
        return
    payload, digest = _make_payload(tmp_path / "second")
    source = payload / "communication/app/communication_agent.py"
    alias = payload / "communication/app/communication_store.py"
    alias.unlink()
    try:
        os.link(source, alias)
    except OSError:
        pytest.skip("hard links are unavailable on this test filesystem")
    with pytest.raises(MaintenanceInstallError, match="hard-linked"):
        load_and_validate_payload(payload, digest)


def test_payload_rejects_missing_fixed_file_even_with_resigned_manifest(
    tmp_path: Path,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    removed = "systemd/ecobin-updater.service"
    (payload / removed).unlink()
    digest = _rewrite_manifest(
        payload,
        lambda document: document.__setitem__(
            "files", [item for item in document["files"] if item["path"] != removed]
        ),
    )

    with pytest.raises(MaintenanceInstallError, match="fixed allowlist"):
        load_and_validate_payload(payload, digest)


def test_preflight_confirms_identity_legacy_service_and_safe_gpio(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)

    result = _installer(rootfs, fake).preflight(payload, digest, **_confirmations())

    assert result["status"] == "READY"
    if os.name == "posix":
        assert result["hardwareDirectoriesNeedingModeChange"] >= 6
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700


@pytest.mark.parametrize("failure", ["identity", "legacy", "safe-gpio", "recovery-marker"])
def test_preflight_fails_closed_on_wrong_device_or_mcu_state(
    tmp_path: Path, failure: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    confirmations = _confirmations()
    if failure == "identity":
        confirmations["expected_image_version"] = "wrong-version"
    elif failure == "legacy":
        fake.states[LEGACY_SERVICE]["ActiveState"] = "inactive"
    elif failure == "safe-gpio":
        fake.states[MCU_SAFE_GPIO_SERVICE]["ActiveState"] = "inactive"
    else:
        _write(rootfs / MCU_RECOVERY_MARKER.lstrip("/"), b"required\n", 0o600)

    with pytest.raises(MaintenanceInstallError):
        _installer(rootfs, fake).preflight(payload, digest, **confirmations)


def test_preflight_refuses_unknown_target_without_overwriting_it(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    unknown = rootfs / "etc/systemd/system/ecobin-updater.service"
    _write(unknown, b"user-owned unit\n")

    with pytest.raises(MaintenanceInstallError, match="unknown target"):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())
    assert unknown.read_bytes() == b"user-owned unit\n"


def test_install_defaults_to_dry_run_and_changes_nothing(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)

    result = _installer(rootfs, fake).install(
        payload, digest, **_confirmations()
    )

    assert result["status"] == "READY"
    assert result["dryRun"] is True
    if os.name == "posix":
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert not any(call[0] == "systemd-sysusers" for call in fake.calls)


def test_install_audit_and_idempotent_reentry(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)

    installed = installer.install(payload, digest, apply=True, **_confirmations())

    assert installed["status"] == "PASS"
    assert installed["legacyServiceActive"] is True
    if os.name == "posix":
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o755
    assert os.readlink(rootfs / "opt/ecobin/hardware/current") == "releases/runtime-v13"
    assert os.readlink(rootfs / "opt/ecobin/communication/current") == (
        f"releases/{COMMUNICATION_RELEASE}"
    )
    assert os.readlink(rootfs / "opt/ecobin/updater/current") == (
        f"releases/{UPDATER_RELEASE}"
    )
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert all(fake.states[unit]["ActiveState"] == "active" for unit in START_UNITS)

    second = installer.install(payload, digest, apply=True, **_confirmations())
    assert second["status"] == "PASS"
    assert len(list((rootfs / "var/lib/ecobin/device-management-maintenance/backups").iterdir())) == 1


def test_audit_detects_changed_installed_file_and_recovery_marker(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    target = rootfs / (
        f"opt/ecobin/communication/releases/{COMMUNICATION_RELEASE}/app/communication_agent.py"
    )
    target.write_text("changed\n", encoding="utf-8")

    with pytest.raises(MaintenanceInstallError, match="installed file changed"):
        installer.audit(**_confirmations())

    # Restore the exact allowlisted file, including the originally recorded
    # inode cannot be recreated; use a fresh fixture for the independent gate.
    second = tmp_path / "marker-case"
    payload2, digest2 = _make_payload(second)
    rootfs2, fake2, _runtime2 = _make_rootfs(second)
    installer2 = _installer(rootfs2, fake2)
    installer2.install(payload2, digest2, apply=True, **_confirmations())
    _write(rootfs2 / MCU_RECOVERY_MARKER.lstrip("/"), b"required\n", 0o600)
    with pytest.raises(MaintenanceInstallError, match="recovery marker"):
        installer2.audit(**_confirmations())


def test_rollback_is_dry_run_then_removes_only_exact_artifacts_and_restores_modes(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())

    preview = installer.rollback(**_confirmations())
    assert preview["status"] == "ROLLBACK_READY"
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).exists()

    result = installer.rollback(apply=True, **_confirmations())

    assert result["status"] == "ROLLED_BACK"
    if os.name == "posix":
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert not (rootfs / "etc/systemd/system/ecobin-updater.service").exists()
    assert not (rootfs / "opt/ecobin/communication/current").exists()
    # State created by the service is deliberately retained, never recursively
    # deleted, while its service is stopped and unit removed.
    assert (rootfs / "var/lib/ecobin/communication/communication.db").read_bytes() == b"state"
    assert "/var/lib/ecobin/communication" in result["retainedPaths"]
    assert fake.states[LEGACY_SERVICE]["ActiveState"] == "active"
    with pytest.raises(MaintenanceInstallError, match="unexplained permanent-layer state"):
        installer.preflight(payload, digest, **_confirmations())


def test_install_audit_and_rollback_preserve_exact_v13_legacy_gate_drop_in(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    gate = rootfs / LEGACY_GATE_DROP_IN_PATH.lstrip("/")
    gate_directory = gate.parent
    gate_before = gate.lstat()
    directory_before = gate_directory.lstat()

    installer.install(payload, digest, apply=True, **_confirmations())
    record = _maintenance_record(rootfs)

    assert LEGACY_GATE_DROP_IN_PATH in record["protectedBefore"]
    assert str(PurePosixPath(LEGACY_GATE_DROP_IN_PATH).parent) in record[
        "protectedBefore"
    ]
    assert LEGACY_GATE_DROP_IN_PATH not in {
        item["path"] for item in record["artifactIntents"]
    }
    assert installer.audit(**_confirmations())["status"] == "PASS"

    result = installer.rollback(apply=True, **_confirmations())
    gate_after = gate.lstat()
    directory_after = gate_directory.lstat()

    assert result["status"] == "ROLLED_BACK"
    assert gate.read_bytes() == LEGACY_GATE_DROP_IN_CONTENT
    assert (gate_after.st_dev, gate_after.st_ino, gate_after.st_nlink) == (
        gate_before.st_dev,
        gate_before.st_ino,
        gate_before.st_nlink,
    )
    assert (directory_after.st_dev, directory_after.st_ino) == (
        directory_before.st_dev,
        directory_before.st_ino,
    )
    assert fake.states[LEGACY_SERVICE]["DropInPaths"] == LEGACY_GATE_DROP_IN_PATH


def test_changed_legacy_gate_blocks_audit_and_rollback_before_code_deletion(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    gate = rootfs / LEGACY_GATE_DROP_IN_PATH.lstrip("/")
    installed_code = rootfs / "etc/systemd/system/ecobin-updater.service"
    gate.write_bytes(b"changed after installation\n")

    with pytest.raises(MaintenanceInstallError):
        installer.audit(**_confirmations())
    with pytest.raises(MaintenanceInstallError):
        installer.rollback(apply=True, **_confirmations())

    assert installed_code.is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()


@pytest.mark.parametrize("mutation", ("different-target", "same-target-new-inode"))
def test_changed_hardware_current_blocks_rollback_before_permanent_code_deletion(
    tmp_path: Path, mutation: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    current = rootfs / "opt/ecobin/hardware/current"
    original_target = os.readlink(current)
    original_inode = current.lstat().st_ino
    if mutation == "different-target":
        current.unlink()
        current.symlink_to("releases/unexpected-runtime")
    else:
        replacement = current.with_name("current-maintenance-test-replacement")
        replacement.symlink_to(original_target)
        replacement_inode = replacement.lstat().st_ino
        assert replacement_inode != original_inode
        current.unlink()
        replacement.rename(current)
        assert os.readlink(current) == original_target
        assert current.lstat().st_ino == replacement_inode
    installed_code = rootfs / "etc/systemd/system/ecobin-updater.service"
    stop_calls_before = sum(
        call[:2] == ("systemctl", "stop") for call in fake.calls
    )

    with pytest.raises(MaintenanceInstallError, match="hardware current"):
        installer.rollback(apply=True, **_confirmations())

    assert installed_code.is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert sum(call[:2] == ("systemctl", "stop") for call in fake.calls) == (
        stop_calls_before
    )


def test_rollback_end_rechecks_legacy_gate_systemd_report_before_marker_clear(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    gate = rootfs / LEGACY_GATE_DROP_IN_PATH.lstrip("/")
    gate_inode = gate.lstat().st_ino
    fake.legacy_drop_in_after_reload = (
        f"{LEGACY_GATE_DROP_IN_PATH} "
        "/run/systemd/system/ecobin-hardware.service.d/unexpected.conf"
    )

    with pytest.raises(MaintenanceInstallError, match="report changed"):
        installer.rollback(apply=True, **_confirmations())

    assert gate.read_bytes() == LEGACY_GATE_DROP_IN_CONTENT
    assert gate.lstat().st_ino == gate_inode
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "ROLLBACK_BLOCKED"


def test_rollback_refuses_to_delete_a_locally_changed_file(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    changed = rootfs / "etc/systemd/system/ecobin-updater.service"
    changed.write_text("local administrator change\n", encoding="utf-8")

    with pytest.raises(MaintenanceInstallError, match="rollback refuses changed"):
        installer.rollback(apply=True, **_confirmations())

    assert changed.read_text(encoding="utf-8") == "local administrator change\n"
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).exists()


def test_install_failure_automatically_rolls_back_code_and_restores_hardware_mode(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)
    fake.fail_start = "ecobin-updater.service"

    with pytest.raises(MaintenanceInstallError, match="command failed"):
        _installer(rootfs, fake).install(
            payload, digest, apply=True, **_confirmations()
        )

    if os.name == "posix":
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert not (rootfs / "etc/systemd/system/ecobin-updater.service").exists()
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert fake.states[LEGACY_SERVICE]["ActiveState"] == "active"


def test_target_map_cannot_touch_identity_secrets_hardware_link_or_business_db(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    manifest = load_and_validate_payload(payload, digest)
    destinations = {item.destination for item in target_files(manifest)}

    assert not any(path.startswith("/etc/ecobin/") for path in destinations)
    assert "/opt/ecobin/hardware/current" not in destinations
    assert not any(path.startswith("/var/lib/ecobin/hardware/") for path in destinations)


def test_build_manifest_refuses_old_manifest_and_unsafe_inventory(tmp_path: Path) -> None:
    payload, _digest = _make_payload(tmp_path)
    with pytest.raises(MaintenanceInstallError, match="remove the old"):
        build_payload_manifest(
            payload,
            payload_id=PAYLOAD_ID,
            source_git_commit=GIT_COMMIT,
            expected_image_release_id=IMAGE_RELEASE_ID,
            expected_image_version=IMAGE_VERSION,
            communication_release_id=COMMUNICATION_RELEASE,
            updater_release_id=UPDATER_RELEASE,
        )


@pytest.mark.parametrize(
    "absolute",
    (
        "/var/lib/ecobin/communication",
        "/var/lib/ecobin/updater",
        "/var/lib/ecobin/business",
        "/var/lib/ecobin/privileged",
        "/run/ecobin/privileged",
    ),
)
def test_clean_v13_preflight_rejects_unexplained_permanent_state(
    tmp_path: Path, absolute: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    (rootfs / absolute.lstrip("/")).mkdir(parents=True, exist_ok=True)

    with pytest.raises(MaintenanceInstallError, match="unexplained permanent-layer state"):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())


@pytest.mark.parametrize(
    ("unit", "fragment"),
    (
        ("ecobin-updater.service", "/usr/lib/systemd/system/ecobin-updater.service"),
        (
            "ecobin-mcu-flash-helper@.service",
            "/usr/lib/systemd/system/ecobin-mcu-flash-helper@.service",
        ),
    ),
)
def test_preflight_requires_every_new_unit_to_be_unknown_and_inactive(
    tmp_path: Path, unit: str, fragment: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    fake.states[unit] = fake._state("loaded", "inactive", fragment)

    with pytest.raises(MaintenanceInstallError, match="already known to systemd"):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())
    if "@.service" in unit:
        assert any(
            call[:3]
            == (
                "systemctl",
                "show",
                unit.replace("@.service", "@maintenance-audit.service"),
            )
            for call in fake.calls
        )


def test_preflight_rejects_systemd_reported_drop_in_without_a_main_unit(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    fake.states["ecobin-updater.service"] = fake._state("not-found", "inactive")
    fake.states["ecobin-updater.service"]["DropInPaths"] = (
        "/usr/lib/systemd/system/ecobin-updater.service.d/vendor.conf"
    )

    with pytest.raises(MaintenanceInstallError, match="already known to systemd"):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())


@pytest.mark.parametrize(
    "reported",
    (
        "",
        "/etc/systemd/system/ecobin-hardware.service.d/wrong.conf",
        (
            f"{LEGACY_GATE_DROP_IN_PATH} "
            "/usr/lib/systemd/system/ecobin-hardware.service.d/vendor.conf"
        ),
    ),
)
def test_preflight_requires_exact_legacy_gate_systemd_report(
    tmp_path: Path, reported: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    fake.states[LEGACY_SERVICE]["DropInPaths"] = reported

    with pytest.raises(MaintenanceInstallError, match="v13 baseline"):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())


@pytest.mark.parametrize(
    "damage",
    (
        "missing",
        "content",
        "owner",
        "group",
        "mode",
        "symlink",
        "hardlink",
        "sibling",
        "directory-owner",
        "directory-mode",
    ),
)
def test_preflight_rejects_changed_legacy_gate_file_or_metadata(
    tmp_path: Path, damage: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    absolute = LEGACY_GATE_DROP_IN_PATH
    gate = rootfs / absolute.lstrip("/")
    directory_absolute = str(PurePosixPath(absolute).parent)
    directory = gate.parent

    if damage == "missing":
        gate.unlink()
    elif damage == "content":
        gate.write_bytes(b"[Unit]\nRequires=unexpected.service\n")
    elif damage == "owner":
        fake.owners[absolute] = (1000, 0)
    elif damage == "group":
        fake.owners[absolute] = (0, 1000)
    elif damage == "mode":
        fake.modes[absolute] = 0o600
    elif damage == "symlink":
        reference = rootfs / "gate-reference.conf"
        _write(reference, LEGACY_GATE_DROP_IN_CONTENT)
        gate.unlink()
        try:
            gate.symlink_to(reference)
        except OSError:
            pytest.skip("symbolic links are unavailable")
    elif damage == "hardlink":
        try:
            os.link(gate, rootfs / "gate-hardlink.conf")
        except OSError:
            pytest.skip("hard links are unavailable")
    elif damage == "sibling":
        _write(directory / "99-uncontrolled.conf", b"[Service]\nUser=nobody\n")
    elif damage == "directory-owner":
        fake.owners[directory_absolute] = (1000, 0)
    else:
        fake.modes[directory_absolute] = 0o700

    with pytest.raises(MaintenanceInstallError):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())


@pytest.mark.parametrize(
    "base",
    (
        "etc/systemd/system",
        "run/systemd/system",
        "usr/lib/systemd/system",
    ),
)
def test_preflight_rejects_instance_specific_helper_drop_in_tree(
    tmp_path: Path, base: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    _write(
        rootfs
        / base
        / "ecobin-mcu-flash-helper@unexpected.service.d/override.conf",
        b"[Service]\nUser=nobody\n",
    )

    with pytest.raises(MaintenanceInstallError, match="uncontrolled systemd drop-in"):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())


@pytest.mark.parametrize("interrupted_kind", ("regular", "symlink"))
def test_fresh_installer_rolls_back_publication_interrupted_before_done_journal(
    tmp_path: Path, interrupted_kind: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    interrupted_path: str | None = None

    def interrupt_after_publish(intent) -> None:
        nonlocal interrupted_path
        if intent["kind"] == interrupted_kind:
            interrupted_path = intent["path"]
            raise MaintenanceProcessInterrupted("simulated power loss")

    with pytest.raises(MaintenanceProcessInterrupted):
        _installer(
            rootfs,
            fake,
            after_publish_hook=interrupt_after_publish,
        ).install(payload, digest, apply=True, **_confirmations())

    assert interrupted_path is not None
    assert (rootfs / interrupted_path.lstrip("/")).exists() or (
        rootfs / interrupted_path.lstrip("/")
    ).is_symlink()
    assert (rootfs / PENDING_MARKER.lstrip("/")).is_file()
    record = _maintenance_record(rootfs)
    interrupted_intent = next(
        item for item in record["artifactIntents"] if item["path"] == interrupted_path
    )
    assert interrupted_intent["state"] == "PREPARED"
    assert "inode" not in interrupted_intent

    recovered = _installer(rootfs, fake).rollback(
        apply=True, **_confirmations()
    )

    assert recovered["status"] == "ROLLED_BACK"
    assert not os.path.lexists(rootfs / interrupted_path.lstrip("/"))
    assert not (rootfs / PENDING_MARKER.lstrip("/")).exists()


def test_fresh_installer_removes_incoming_file_left_before_atomic_publication(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)

    def interrupt_before_publish(intent) -> None:
        raise MaintenanceProcessInterrupted("simulated power loss before rename")

    with pytest.raises(MaintenanceProcessInterrupted):
        _installer(
            rootfs,
            fake,
            before_publish_hook=interrupt_before_publish,
        ).install(payload, digest, apply=True, **_confirmations())

    record = _maintenance_record(rootfs)
    intent = record["artifactIntents"][0]
    incoming = rootfs / intent["temporaryPath"].lstrip("/")
    destination = rootfs / intent["path"].lstrip("/")
    assert intent["state"] == "PREPARED"
    assert incoming.is_file()
    assert not destination.exists()

    recovered = _installer(rootfs, fake).rollback(
        apply=True, **_confirmations()
    )

    assert recovered["status"] == "ROLLED_BACK"
    assert not incoming.exists()
    assert not (rootfs / PENDING_MARKER.lstrip("/")).exists()


@pytest.mark.parametrize("stop_failure", ("command", "sticky"))
def test_rollback_stop_gate_keeps_code_and_marker_on_failure(
    tmp_path: Path, stop_failure: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    unit = "ecobin-updater.service"
    if stop_failure == "command":
        fake.fail_stop = unit
    else:
        fake.sticky_active = unit
    installed_file = rootfs / "etc/systemd/system/ecobin-updater.service"

    with pytest.raises(MaintenanceInstallError):
        installer.rollback(apply=True, **_confirmations())

    assert installed_file.is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "ROLLBACK_BLOCKED"


def test_rollback_reload_failure_never_clears_marker(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.fail_reload = True

    with pytest.raises(MaintenanceInstallError, match="cannot reload systemd"):
        installer.rollback(apply=True, **_confirmations())

    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "ROLLBACK_BLOCKED"


def test_rollback_stops_live_privileged_helper_instance_before_deleting_code(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    instance = "ecobin-mcu-flash-helper@connection-7.service"
    fake.states[instance] = fake._state(
        "loaded",
        "active",
        "/etc/systemd/system/ecobin-mcu-flash-helper@.service",
        "running",
    )
    fake.fail_stop = instance

    with pytest.raises(MaintenanceInstallError, match="cannot stop"):
        installer.rollback(apply=True, **_confirmations())

    assert (rootfs / "usr/lib/ecobin/device-management/local_control.py").is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()


def test_rollback_closes_socket_before_enumerating_new_helper_instances(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    instance = "ecobin-mcu-flash-helper@late-connection.service"
    fake.spawn_helper_on_socket_stop = instance

    result = installer.rollback(apply=True, **_confirmations())

    assert result["status"] == "ROLLED_BACK"
    assert ("systemctl", "stop", instance) in fake.calls
    assert fake.states[instance]["ActiveState"] == "inactive"
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()


@pytest.mark.parametrize(
    "failure",
    (
        "state-owner",
        "state-group",
        "nested-state-group",
        "privileged-group",
        "snapshot-mode",
        "lock-mode",
        "trust-owner",
    ),
)
def test_audit_rejects_incorrect_runtime_or_code_permissions(
    tmp_path: Path, failure: str
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())

    if failure == "state-owner":
        fake.owners["/var/lib/ecobin/updater"] = (0, 988)
    elif failure == "state-group":
        fake.owners["/var/lib/ecobin/updater"] = (993, 993)
    elif failure == "nested-state-group":
        fake.owners["/var/lib/ecobin/updater/staging"] = (993, 988)
    elif failure == "privileged-group":
        fake.owners["/run/ecobin/privileged"] = (0, 0)
    elif failure == "snapshot-mode":
        fake.modes["/var/lib/ecobin/privileged/business-snapshots"] = 0o750
    elif failure == "lock-mode":
        fake.modes["/run/ecobin/privileged/mutation.lock"] = 0o640
    else:
        fake.owners["/usr/share/ecobin/runtime-release-keys/factory_2026.pem"] = (
            1000,
            1000,
        )

    with pytest.raises(MaintenanceInstallError):
        installer.audit(**_confirmations())


def test_audit_rejects_unit_fragment_outside_controlled_etc_path(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.states["ecobin-updater.service"]["FragmentPath"] = (
        "/usr/lib/systemd/system/ecobin-updater.service"
    )

    with pytest.raises(MaintenanceInstallError, match="controlled file"):
        installer.audit(**_confirmations())


def test_audit_rejects_uncontrolled_systemd_drop_in(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.states["ecobin-updater.service"]["DropInPaths"] = (
        "/run/systemd/system/ecobin-updater.service.d/override.conf"
    )

    with pytest.raises(MaintenanceInstallError, match="controlled file"):
        installer.audit(**_confirmations())


def test_audit_rejects_missing_or_extra_legacy_gate_systemd_report(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.states[LEGACY_SERVICE]["DropInPaths"] = ""

    with pytest.raises(MaintenanceInstallError):
        installer.audit(**_confirmations())
