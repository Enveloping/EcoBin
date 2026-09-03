from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Sequence

import pytest

from system.device_management_maintenance_installer import (
    ACCOUNT_NAMES,
    ACCOUNT_GROUPS,
    ACTIVE_MARKER,
    BACKUP_PARENT,
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
    MAX_MANIFEST_BYTES,
    MCU_RECOVERY_MARKER,
    MCU_SAFE_GPIO_FACT_PATH,
    MCU_SAFE_GPIO_SERVICE,
    MCU_SAFE_GPIO_UNIT_PATH,
    PENDING_MARKER,
    PREFLIGHT_PAYLOAD,
    RELEASE_ENV_PAYLOAD,
    RUNTIME_START_FENCE,
    RUNTIME_START_FENCE_CONDITION,
    START_UNITS,
    SYSUSERS_PAYLOAD,
    TMPFILES_DIRECTORY_MODES,
    TMPFILES_PAYLOAD,
    TMPFILES_REGULAR_PATHS,
    UPDATER_STATE_DATABASE_PATH,
    CommandResult,
    MaintenanceInstallError,
    MaintenanceInstaller,
    MaintenanceProcessInterrupted,
    RollbackDeferredForBusyState,
    _validate_runtime_fence_unit_bytes,
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
from updater_store import UpdaterStore


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


def _public_key(seed: int) -> bytes:
    der = bytes.fromhex("302a300506032b6570032100") + bytes(
        (seed + index) % 256 for index in range(32)
    )
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
        _write(
            payload / "systemd" / name,
            (
                f"[Unit]\nDescription={name}\n"
                f"{RUNTIME_START_FENCE_CONDITION}\n\n"
                "[Service]\nType=oneshot\n"
            ).encode(),
        )
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
    _write(payload / "trust/runtime-release-keys/factory_2026.pem", _public_key(1))
    _write(payload / "trust/business-release-keys/business_2026.pem", _public_key(101))
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


def _rewrite_payload_file_and_manifest(
    payload: Path,
    relative: str,
    raw_file: bytes,
) -> str:
    path = payload / relative
    path.write_bytes(raw_file)
    os.chmod(path, 0o644)

    def update(document: dict) -> None:
        item = next(entry for entry in document["files"] if entry["path"] == relative)
        item["sha256"] = hashlib.sha256(raw_file).hexdigest()
        item["size"] = len(raw_file)

    return _rewrite_manifest(payload, update)


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
        self.after_start = None
        self.after_first_stop = None
        self.after_daemon_reload = None
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
        if absolute.startswith(
            "/var/lib/ecobin/device-management-maintenance/"
        ):
            if stat.S_ISDIR(details.st_mode):
                return 0o700
            if stat.S_ISREG(details.st_mode):
                return 0o600
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
                vendor_unit_path = self.rootfs / "usr/lib/systemd/system" / unit
                if unit_path.exists():
                    self.states.setdefault(
                        unit,
                        self._state("loaded", "inactive", f"/etc/systemd/system/{unit}"),
                    )
                    self.states[unit].update(
                        LoadState="loaded",
                        FragmentPath=f"/etc/systemd/system/{unit}",
                    )
                elif vendor_unit_path.exists():
                    self.states[unit] = self._state(
                        "loaded",
                        "inactive",
                        f"/usr/lib/systemd/system/{unit}",
                    )
                elif unit in self.states:
                    self.states[unit] = self._state("not-found", "inactive")
                rollback_fence = (
                    self.rootfs
                    / "etc/systemd/system"
                    / f"{unit}.d"
                    / "99-ecobin-rollback-start-fence.conf"
                )
                self.states.setdefault(
                    unit, self._state("not-found", "inactive")
                )["DropInPaths"] = (
                    f"/etc/systemd/system/{unit}.d/"
                    "99-ecobin-rollback-start-fence.conf"
                    if rollback_fence.is_file()
                    else ""
                )
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
            if self.after_daemon_reload is not None:
                callback = self.after_daemon_reload
                self.after_daemon_reload = None
                callback()
            return CommandResult(0)
        if args[:2] == ("systemctl", "start"):
            unit = args[2]
            if unit == self.fail_start:
                return CommandResult(1, "", "injected start failure")
            state = self.states.setdefault(
                unit,
                self._state("loaded", "inactive", f"/etc/systemd/system/{unit}"),
            )
            if (
                (
                    unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES)
                    or any(
                        unit.startswith(prefix)
                        for prefix in (
                            "ecobin-business-activation-helper@",
                            "ecobin-mcu-flash-helper@",
                        )
                    )
                )
                and (self.rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
            ):
                state.update(ActiveState="inactive", SubState="dead")
                return CommandResult(0)
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
                updater_database = (
                    self.rootfs / "var/lib/ecobin/updater/updater.db"
                )
                updater_store = UpdaterStore(
                    updater_database,
                    release_version=UPDATER_RELEASE,
                )
                updater_store.initialize()
                updater_store.close()
                self.owners["/var/lib/ecobin/updater/updater.db"] = (993, 988)
                self.modes["/var/lib/ecobin/updater/updater.db"] = 0o600
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = f"/var/lib/ecobin/updater/updater.db{suffix}"
                    if (self.rootfs / sidecar.lstrip("/")).exists():
                        self.owners[sidecar] = (993, 988)
                        self.modes[sidecar] = 0o640
            if self.after_start is not None:
                self.after_start(unit)
            return CommandResult(0)
        if args[:2] == ("systemctl", "stop"):
            unit = args[2]
            if unit == self.fail_stop:
                return CommandResult(1, "", "injected stop failure")
            state = self.states.setdefault(unit, self._state("loaded", "inactive"))
            if unit != self.sticky_active:
                state.update(ActiveState="inactive", SubState="dead")
            if self.after_first_stop is not None:
                callback = self.after_first_stop
                self.after_first_stop = None
                callback(unit)
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
        if args[:2] == ("systemctl", "reset-failed"):
            unit = args[2]
            state = self.states.setdefault(unit, self._state("loaded", "inactive"))
            if state["ActiveState"] == "failed":
                state.update(ActiveState="inactive", SubState="dead")
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
        (
            Path(__file__).resolve().parents[1]
            / "ecobin-mcu-safe-gpio.service"
        ).read_bytes(),
    )
    boot_id = "12345678-1234-1234-1234-123456789abc"
    _write(rootfs / "proc/sys/kernel/random/boot_id", f"{boot_id}\n".encode())
    _write(
        rootfs / "run/ecobin/mcu-safe-gpio/boot-safe.json",
        (
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "SAFE_APPLICATION",
                    "bootId": boot_id,
                    "boot0": {"wpi": 2, "level": 0},
                    "resetGate": {"wpi": 5, "level": 0},
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode(),
        0o600,
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
    fake.modes["/run/ecobin/mcu-safe-gpio"] = 0o700
    fake.modes["/run/ecobin/mcu-safe-gpio/boot-safe.json"] = 0o600
    fake.modes["/var/lib/ecobin/device-management-maintenance"] = 0o700
    fake.modes[
        "/var/lib/ecobin/device-management-maintenance/backups"
    ] = 0o700
    return rootfs, fake, runtime


def _installer(
    rootfs: Path,
    fake: FakeSystem,
    *,
    runner=None,
    before_publish_hook=None,
    after_publish_hook=None,
) -> MaintenanceInstaller:
    return MaintenanceInstaller(
        rootfs=rootfs,
        runner=fake if runner is None else runner,
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


def _filesystem_snapshot(root: Path) -> dict[str, tuple[int, int, bytes | str | None]]:
    snapshot: dict[str, tuple[int, int, bytes | str | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        details = path.lstat()
        content: bytes | str | None = None
        if stat.S_ISREG(details.st_mode):
            content = path.read_bytes()
        elif stat.S_ISLNK(details.st_mode):
            content = os.readlink(path)
        snapshot[path.relative_to(root).as_posix()] = (
            stat.S_IFMT(details.st_mode),
            stat.S_IMODE(details.st_mode),
            content,
        )
    return snapshot


def _create_orphan_operation_directory(
    rootfs: Path, operation_id: str
) -> Path:
    maintenance_root = rootfs / "var/lib/ecobin/device-management-maintenance"
    backup_parent = rootfs / BACKUP_PARENT.lstrip("/")
    operation = backup_parent / operation_id
    operation.mkdir(mode=0o700, parents=True)
    for directory in (maintenance_root, backup_parent, operation):
        os.chmod(directory, 0o700)
    return operation


def _leave_first_artifact_unpublished(
    rootfs: Path,
    fake: FakeSystem,
    payload: Path,
    digest: str,
) -> tuple[str, Path, Path]:
    def interrupt_before_publish(_intent) -> None:
        raise MaintenanceProcessInterrupted(
            "simulated power loss before the first artifact rename"
        )

    with pytest.raises(MaintenanceProcessInterrupted):
        _installer(
            rootfs,
            fake,
            before_publish_hook=interrupt_before_publish,
        ).install(payload, digest, apply=True, **_confirmations())

    pending_path = rootfs / PENDING_MARKER.lstrip("/")
    marker = json.loads(pending_path.read_text(encoding="utf-8"))
    record_path = (
        rootfs
        / BACKUP_PARENT.lstrip("/")
        / marker["operationId"]
        / "record.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    artifact_incoming = rootfs / record["artifactIntents"][0][
        "temporaryPath"
    ].lstrip("/")
    assert artifact_incoming.is_file()
    return marker["operationId"], record_path, artifact_incoming


def _updater_database(rootfs: Path) -> Path:
    return rootfs / UPDATER_STATE_DATABASE_PATH.lstrip("/")


def _record_updater_sidecar_metadata(rootfs: Path, fake: FakeSystem) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        absolute = f"{UPDATER_STATE_DATABASE_PATH}{suffix}"
        if (rootfs / absolute.lstrip("/")).exists():
            fake.owners[absolute] = (993, 988)
            fake.modes[absolute] = 0o640


def _activate_stage4_candidate(rootfs: Path, fake: FakeSystem) -> UpdaterStore:
    store = UpdaterStore(
        _updater_database(rootfs),
        release_version=UPDATER_RELEASE,
        enable_stage4_candidate=True,
    )
    store.initialize()
    state = store.get_status()
    store.activate_stage4_job_gate(
        {
            "operationUid": str(uuid.uuid4()),
            "evidenceDigest": "a" * 64,
            "expectedManagementStateSequence": state[
                "managementStateSequence"
            ],
        }
    )
    _record_updater_sidecar_metadata(rootfs, fake)
    return store


def _new_mutating_systemctl_calls(
    fake: FakeSystem, offset: int
) -> list[tuple[str, ...]]:
    return [
        call
        for call in fake.calls[offset:]
        if call[:2] in {
            ("systemctl", "start"),
            ("systemctl", "stop"),
            ("systemctl", "daemon-reload"),
        }
    ]


def _insert_completed_permit(connection: sqlite3.Connection) -> str:
    permit_uid = str(uuid.uuid4())
    now = "2026-09-03T00:00:00.000000Z"
    connection.execute(
        """INSERT INTO job_permit (
               permit_uid, work_uid, command_uid, work_type,
               request_digest_sha256, state, grant_gate_sequence,
               completion_uid, completion_outcome,
               completion_digest_sha256, created_at, completed_at, updated_at
           ) VALUES (?, ?, ?, 'DELIVERY', ?, 'COMPLETED', 1,
                     ?, 'SUCCEEDED', ?, ?, ?, ?)""",
        (
            permit_uid,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            "b" * 64,
            str(uuid.uuid4()),
            "c" * 64,
            now,
            now,
            now,
        ),
    )
    return permit_uid


def _mutate_updater_rollback_state(
    rootfs: Path, fake: FakeSystem, mutation: str
) -> None:
    database = _updater_database(rootfs)
    if mutation == "missing-database":
        database.unlink()
        return
    if mutation == "corrupt-database":
        database.write_bytes(b"not a sqlite database")
        return
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if mutation == "schema-version":
            connection.execute("UPDATE schema_version SET version=4")
        elif mutation == "unknown-table":
            connection.execute("CREATE TABLE future_update_fact (value TEXT)")
        elif mutation == "base-ddl-drift":
            connection.execute(
                "ALTER TABLE job_permit ADD COLUMN future_fact TEXT"
            )
        elif mutation == "extension-ddl-drift":
            connection.execute(
                """ALTER TABLE job_gate_control_extension
                   ADD COLUMN future_state TEXT"""
            )
        elif mutation == "missing-extension-table":
            connection.execute("DROP TABLE operator_job_gate_lock")
        elif mutation == "candidate-enabled":
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       stage4_candidate_enabled=1,
                       job_gate_mode='ENFORCED',
                       job_gate_state='LOCKED', maintenance_state='LOCKED',
                       reconciliation_required=1,
                       block_reason_code='STAGE4_ACTIVATION_REQUIRED'
                   WHERE singleton_id=1"""
            )
        elif mutation == "completed-permit":
            _insert_completed_permit(connection)
        elif mutation == "confirmed-action":
            permit_uid = _insert_completed_permit(connection)
            now = "2026-09-03T00:00:00.000000Z"
            connection.execute(
                """INSERT INTO physical_action_ledger (
                       action_uid, permit_uid, work_uid, command_uid,
                       action_key, action_kind, action_digest_sha256,
                       state, dispatch_mode, dispatch_attempt_token_sha256,
                       receipt_uid, confirmed_outcome, confirmation_basis,
                       evidence_digest_sha256, created_at, confirmed_at,
                       updated_at
                   ) SELECT ?, permit_uid, work_uid, command_uid,
                            'delivery-door', 'OPEN_DELIVERY_DOOR', ?,
                            'CONFIRMED', 'PREPARED_ONLY', ?, ?,
                            'NOT_EXECUTED', 'PREPARED_NOT_ARMED', ?, ?, ?, ?
                     FROM job_permit WHERE permit_uid=?""",
                (
                    str(uuid.uuid4()),
                    "d" * 64,
                    "e" * 64,
                    str(uuid.uuid4()),
                    "f" * 64,
                    now,
                    now,
                    now,
                    permit_uid,
                ),
            )
        elif mutation == "maintenance-lock":
            now = "2026-09-03T00:00:00.000000Z"
            connection.execute(
                """INSERT INTO maintenance_lock (
                       singleton_id, owner_update_uid, maintenance_type,
                       phase, fence_token, acquired_at, updated_at
                   ) VALUES (1, ?, 'BUSINESS', 'LOCKED', 1, ?, ?)""",
                (str(uuid.uuid4()), now, now),
            )
        else:  # pragma: no cover - test helper misuse.
            raise AssertionError(f"unknown mutation: {mutation}")
    _record_updater_sidecar_metadata(rootfs, fake)


def test_payload_manifest_is_an_exact_authenticated_allowlist(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)

    manifest = load_and_validate_payload(payload, digest)

    assert manifest.payload_id == PAYLOAD_ID
    assert manifest.communication_release_id == COMMUNICATION_RELEASE
    assert manifest.updater_release_id == UPDATER_RELEASE
    assert len(manifest.files) > 20


def test_payload_rejects_runtime_key_reused_for_business_releases(
    tmp_path: Path,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    runtime_key = payload / "trust/runtime-release-keys/factory_2026.pem"
    relative = "trust/business-release-keys/business_2026.pem"
    digest = _rewrite_payload_file_and_manifest(
        payload,
        relative,
        runtime_key.read_bytes(),
    )

    with pytest.raises(MaintenanceInstallError, match="independent public keys"):
        load_and_validate_payload(payload, digest)


@pytest.mark.parametrize(
    "payload_key",
    (
        "trust/runtime-release-keys/factory_2026.pem",
        "trust/business-release-keys/business_2026.pem",
    ),
)
def test_preflight_rejects_payload_key_reused_from_installed_mcu_trust(
    tmp_path: Path,
    payload_key: str,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    _write(
        rootfs / "usr/share/ecobin/mcu-release-keys/RELEASE_2026_01.pem",
        (payload / payload_key).read_bytes(),
    )

    with pytest.raises(MaintenanceInstallError, match="independent public keys"):
        _installer(rootfs, fake).preflight(payload, digest, **_confirmations())


def test_payload_allows_nonconflicting_conditions_in_the_unit_section(
    tmp_path: Path,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-business-permission-preflight.service"
    raw = (payload / relative).read_bytes().replace(
        f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
        (
            f"{RUNTIME_START_FENCE_CONDITION}\n"
            "ConditionPathExists=/etc/ecobin/hardware.env\n"
            "ConditionPathIsDirectory=/var/lib/ecobin\n"
        ).encode(),
        1,
    )
    digest = _rewrite_payload_file_and_manifest(payload, relative, raw)

    manifest = load_and_validate_payload(payload, digest)

    assert relative in manifest.files


@pytest.mark.parametrize("placement", ("before", "after"))
def test_payload_rejects_cross_type_condition_reset_around_runtime_fence(
    tmp_path: Path,
    placement: str,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    raw = (payload / relative).read_bytes()
    reset = b"ConditionPathIsDirectory=\n"
    if placement == "before":
        raw = raw.replace(b"[Unit]\n", b"[Unit]\n" + reset, 1)
    else:
        raw = raw.replace(
            f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
            f"{RUNTIME_START_FENCE_CONDITION}\n".encode() + reset,
            1,
        )
    digest = _rewrite_payload_file_and_manifest(payload, relative, raw)

    with pytest.raises(MaintenanceInstallError, match="resets runtime conditions"):
        load_and_validate_payload(payload, digest)


def test_payload_rejects_cross_type_condition_without_assignment(
    tmp_path: Path,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    raw = (payload / relative).read_bytes().replace(
        f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
        (
            f"{RUNTIME_START_FENCE_CONDITION}\n"
            "ConditionPathIsDirectory\n"
        ).encode(),
        1,
    )
    digest = _rewrite_payload_file_and_manifest(payload, relative, raw)

    with pytest.raises(MaintenanceInstallError, match="malformed runtime conditions"):
        load_and_validate_payload(payload, digest)


def test_payload_accepts_systemd_crlf_unit_lines(tmp_path: Path) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    raw = (payload / relative).read_bytes().replace(b"\n", b"\r\n")
    digest = _rewrite_payload_file_and_manifest(payload, relative, raw)

    manifest = load_and_validate_payload(payload, digest)

    assert relative in manifest.files


@pytest.mark.parametrize(
    "joined_separator",
    ("\r", "\v", "\f", "\u0085", "\u2028", "\u2029"),
    ids=(
        "cr",
        "vertical-tab",
        "form-feed",
        "nel",
        "line-separator",
        "paragraph-separator",
    ),
)
def test_payload_rejects_non_lf_text_joined_to_runtime_fence_tail(
    tmp_path: Path,
    joined_separator: str,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    raw = (payload / relative).read_bytes().replace(
        f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
        (
            f"{RUNTIME_START_FENCE_CONDITION}{joined_separator}"
            "X-EcoBin-Joined=true\n"
        ).encode("utf-8"),
        1,
    )
    digest = _rewrite_payload_file_and_manifest(payload, relative, raw)

    with pytest.raises(
        MaintenanceInstallError,
        match="bare carriage return|non-ASCII or unsafe control bytes",
    ):
        load_and_validate_payload(payload, digest)


@pytest.mark.parametrize("placement", ("prefix", "trailing-edge"))
def test_payload_rejects_nbsp_around_runtime_fence_line(
    tmp_path: Path,
    placement: str,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    replacement = (
        "\u00a0" + RUNTIME_START_FENCE_CONDITION
        if placement == "prefix"
        else RUNTIME_START_FENCE_CONDITION + "\u00a0"
    )
    raw = (payload / relative).read_bytes().replace(
        f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
        f"{replacement}\n".encode("utf-8"),
        1,
    )
    digest = _rewrite_payload_file_and_manifest(payload, relative, raw)

    with pytest.raises(
        MaintenanceInstallError,
        match="non-ASCII or unsafe control bytes",
    ):
        load_and_validate_payload(payload, digest)


@pytest.mark.parametrize(
    "hidden_section",
    (
        b"[Service]\rX-EcoBin-Hidden=true\n",
        b"[Service]\x00X-EcoBin-Hidden=true\n",
    ),
    ids=("bare-cr", "nul"),
)
def test_payload_rejects_hidden_service_section_before_runtime_fence(
    tmp_path: Path,
    hidden_section: bytes,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    raw = (payload / relative).read_bytes().replace(
        f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
        hidden_section + f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
        1,
    )
    digest = _rewrite_payload_file_and_manifest(payload, relative, raw)

    with pytest.raises(
        MaintenanceInstallError,
        match="NUL byte|bare carriage return",
    ):
        load_and_validate_payload(payload, digest)


@pytest.mark.parametrize("injected", (b"\x00", b"\r"), ids=("nul", "bare-cr"))
def test_runtime_fence_validator_rejects_unsafe_byte_at_every_boundary(
    injected: bytes,
) -> None:
    raw = (
        b"[Unit]\n"
        + f"{RUNTIME_START_FENCE_CONDITION}\n".encode()
        + b"[Service]\nType=oneshot\n"
    )
    for offset in range(len(raw) + 1):
        candidate = raw[:offset] + injected + raw[offset:]
        if injected == b"\r" and offset < len(raw) and raw[offset] == 0x0A:
            _validate_runtime_fence_unit_bytes(candidate, "every-boundary.service")
            continue
        with pytest.raises(
            MaintenanceInstallError,
            match="NUL byte|bare carriage return",
        ):
            _validate_runtime_fence_unit_bytes(candidate, "every-boundary.service")


@pytest.mark.parametrize(
    "unsafe",
    (b"\x01", b"\x7f", "\u00e9".encode("utf-8")),
    ids=("c0-control", "delete", "non-ascii"),
)
def test_runtime_fence_validator_rejects_other_unsafe_bytes(
    unsafe: bytes,
) -> None:
    raw = (
        b"[Unit]\n"
        + f"{RUNTIME_START_FENCE_CONDITION}\n".encode()
        + b"[Service]\nType=oneshot\n"
    )

    with pytest.raises(
        MaintenanceInstallError,
        match="non-ASCII or unsafe control bytes",
    ):
        _validate_runtime_fence_unit_bytes(raw + unsafe, "unsafe-byte.service")


@pytest.mark.parametrize(
    "mutate",
    (
        lambda raw: raw.replace(
            f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
            b"",
        ),
        lambda raw: raw.replace(
            b"\n[Service]",
            b"\nConditionPathExists=\n\n[Service]",
        ),
        lambda raw: raw
        + b"\n[Unit]\nConditionPathExists=\n",
        lambda raw: raw.replace(
            f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
            (
                f"{RUNTIME_START_FENCE_CONDITION}\n"
                f"{RUNTIME_START_FENCE_CONDITION}\n"
            ).encode(),
            1,
        ),
        lambda raw: raw.replace(
            f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
            (
                f"{RUNTIME_START_FENCE_CONDITION}\n"
                f"ConditionPathExists={RUNTIME_START_FENCE}\n"
            ).encode(),
            1,
        ),
        lambda raw: raw
        + b"\nConditionPathExists=/etc/ecobin/outside-unit\n",
        lambda raw: raw.replace(
            f"{RUNTIME_START_FENCE_CONDITION}\n".encode(),
            b"",
            1,
        )
        + f"\n{RUNTIME_START_FENCE_CONDITION}\n".encode(),
    ),
)
def test_payload_rejects_unsafe_runtime_start_fence_conditions(
    tmp_path: Path,
    mutate,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    raw = (payload / relative).read_bytes()
    digest = _rewrite_payload_file_and_manifest(payload, relative, mutate(raw))

    with pytest.raises(MaintenanceInstallError, match="runtime|Unit section"):
        load_and_validate_payload(payload, digest)


@pytest.mark.parametrize("trailing_backslash_count", (1, 2))
def test_payload_rejects_physical_line_continuation_before_unit_section(
    tmp_path: Path,
    trailing_backslash_count: int,
) -> None:
    payload, _digest = _make_payload(tmp_path)
    relative = "systemd/ecobin-updater.service"
    raw = (payload / relative).read_bytes()
    prefix = (
        b"[Install]\nAlias=unsafe"
        + (b"\\" * trailing_backslash_count)
        + b"\n"
    )
    digest = _rewrite_payload_file_and_manifest(payload, relative, prefix + raw)

    with pytest.raises(MaintenanceInstallError, match="unsafe line continuation"):
        load_and_validate_payload(payload, digest)


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
    before = _filesystem_snapshot(rootfs)

    result = _installer(rootfs, fake).preflight(payload, digest, **_confirmations())

    assert result["status"] == "READY"
    assert _filesystem_snapshot(rootfs) == before
    assert not any(
        call[0] in {"systemd-sysusers", "systemd-tmpfiles"}
        or call[:2]
        in {
            ("systemctl", "start"),
            ("systemctl", "stop"),
            ("systemctl", "daemon-reload"),
        }
        for call in fake.calls
    )
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


@pytest.mark.parametrize(
    ("failure", "message"),
    (
        ("fragment", "active image-owned unit"),
        ("unit-hash", "differs from the image baseline"),
        ("stale-current-boot-fact", "invalid or stale"),
    ),
)
def test_preflight_rejects_untrusted_mcu_unit_or_stale_current_boot_fact(
    tmp_path: Path,
    failure: str,
    message: str,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)

    if failure == "fragment":
        fake.states[MCU_SAFE_GPIO_SERVICE]["FragmentPath"] = (
            "/run/systemd/system/ecobin-mcu-safe-gpio.service"
        )
    elif failure == "unit-hash":
        _write(
            rootfs / MCU_SAFE_GPIO_UNIT_PATH.lstrip("/"),
            b"[Unit]\nDescription=locally replaced safety gate\n",
            0o644,
        )
    else:
        fact_path = rootfs / MCU_SAFE_GPIO_FACT_PATH.lstrip("/")
        fact = json.loads(fact_path.read_text(encoding="utf-8"))
        fact["bootId"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        _write(
            fact_path,
            (json.dumps(fact, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            0o600,
        )

    with pytest.raises(MaintenanceInstallError, match=message):
        _installer(rootfs, fake).preflight(
            payload,
            digest,
            **_confirmations(),
        )

    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert not (rootfs / PENDING_MARKER.lstrip("/")).exists()
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    assert not any(
        call[0] in {"systemd-sysusers", "systemd-tmpfiles"}
        or call[:2] in {("systemctl", "start"), ("systemctl", "stop")}
        for call in fake.calls
    )


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
    before = _filesystem_snapshot(rootfs)

    result = _installer(rootfs, fake).install(
        payload, digest, **_confirmations()
    )

    assert result["status"] == "READY"
    assert result["dryRun"] is True
    if os.name == "posix":
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert _filesystem_snapshot(rootfs) == before
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert not any(
        call[0] in {"systemd-sysusers", "systemd-tmpfiles"}
        or call[:2]
        in {
            ("systemctl", "start"),
            ("systemctl", "stop"),
            ("systemctl", "daemon-reload"),
        }
        for call in fake.calls
    )


def test_fresh_install_replays_prepared_sysusers_after_first_group_side_effect(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    first_group_created = False
    interrupt_next_sysusers = True
    runner_calls: list[tuple[str, ...]] = []

    def interrupted_runner(arguments: Sequence[str]) -> CommandResult:
        nonlocal first_group_created, interrupt_next_sysusers
        args = tuple(arguments)
        runner_calls.append(args)
        if (
            first_group_created
            and not fake.identities_created
            and args[:2] == ("getent", "group")
            and args[2] in {"ecobin-communication", "995"}
        ):
            return CommandResult(0, "ecobin-communication:x:995:\n")
        if args[0] == "systemd-sysusers" and interrupt_next_sysusers:
            first_group_created = True
            interrupt_next_sysusers = False
            raise MaintenanceProcessInterrupted(
                "simulated power loss after the first declared group"
            )
        return fake(args)

    with pytest.raises(MaintenanceProcessInterrupted):
        _installer(rootfs, fake, runner=interrupted_runner).install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    interrupted_record = _maintenance_record(rootfs)
    assert interrupted_record["setupCommandIntents"] == [
        {
            "name": "systemd-sysusers",
            "arguments": [
                "systemd-sysusers",
                "/usr/lib/sysusers.d/ecobin-device-runtime.conf",
            ],
            "state": "PREPARED",
            "preparedAt": interrupted_record["setupCommandIntents"][0][
                "preparedAt"
            ],
        }
    ]
    assert first_group_created is True
    assert fake.identities_created is False

    resumed = _installer(rootfs, fake, runner=interrupted_runner).install(
        payload,
        digest,
        apply=True,
        **_confirmations(),
    )

    assert resumed["status"] == "PASS"
    assert sum(call[0] == "systemd-sysusers" for call in runner_calls) == 2
    assert fake.identities_created is True
    assert [
        (intent["name"], intent["state"])
        for intent in _maintenance_record(rootfs)["setupCommandIntents"]
    ] == [
        ("systemd-sysusers", "DONE"),
        ("systemd-tmpfiles", "DONE"),
    ]


def test_prepared_sysusers_rejects_conflicting_partial_group_before_replay(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    partial_group_created = False
    expose_conflict = False
    interrupt_next_sysusers = True
    runner_calls: list[tuple[str, ...]] = []

    def interrupted_runner(arguments: Sequence[str]) -> CommandResult:
        nonlocal partial_group_created, interrupt_next_sysusers
        args = tuple(arguments)
        runner_calls.append(args)
        if (
            partial_group_created
            and not fake.identities_created
            and args[:2] == ("getent", "group")
            and args[2] in {"ecobin-communication", "995"}
        ):
            members = "unexpected-account" if expose_conflict else ""
            return CommandResult(
                0,
                f"ecobin-communication:x:995:{members}\n",
            )
        if args[0] == "systemd-sysusers" and interrupt_next_sysusers:
            partial_group_created = True
            interrupt_next_sysusers = False
            raise MaintenanceProcessInterrupted(
                "simulated power loss after the first declared group"
            )
        return fake(args)

    with pytest.raises(MaintenanceProcessInterrupted):
        _installer(rootfs, fake, runner=interrupted_runner).install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    expose_conflict = True
    before = _filesystem_snapshot(rootfs)
    with pytest.raises(
        MaintenanceInstallError,
        match="partial installed group conflicts with the declaration",
    ):
        _installer(rootfs, fake, runner=interrupted_runner).install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    after = _filesystem_snapshot(rootfs)
    maintenance_prefix = "var/lib/ecobin/device-management-maintenance/"
    assert {
        path: value
        for path, value in after.items()
        if not path.startswith(maintenance_prefix)
    } == {
        path: value
        for path, value in before.items()
        if not path.startswith(maintenance_prefix)
    }
    assert sum(call[0] == "systemd-sysusers" for call in runner_calls) == 1
    assert fake.identities_created is False
    assert partial_group_created is True
    assert not any(call[0] == "systemd-tmpfiles" for call in runner_calls)


def test_fresh_install_replays_prepared_tmpfiles_after_one_directory_side_effect(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    partial_absolute = "/var/lib/ecobin/communication"
    partial_directory = rootfs / partial_absolute.lstrip("/")
    interrupt_next_tmpfiles = True
    runner_calls: list[tuple[str, ...]] = []

    def interrupted_runner(arguments: Sequence[str]) -> CommandResult:
        nonlocal interrupt_next_tmpfiles
        args = tuple(arguments)
        runner_calls.append(args)
        if args[:2] == ("systemd-tmpfiles", "--create") and interrupt_next_tmpfiles:
            partial_directory.mkdir(parents=True, exist_ok=True)
            os.chmod(partial_directory, 0o700)
            fake.owners[partial_absolute] = (995, 995)
            fake.modes[partial_absolute] = 0o700
            interrupt_next_tmpfiles = False
            raise MaintenanceProcessInterrupted(
                "simulated power loss after the first declared directory"
            )
        return fake(args)

    with pytest.raises(MaintenanceProcessInterrupted):
        _installer(rootfs, fake, runner=interrupted_runner).install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    interrupted_record = _maintenance_record(rootfs)
    assert [
        (intent["name"], intent["state"])
        for intent in interrupted_record["setupCommandIntents"]
    ] == [
        ("systemd-sysusers", "DONE"),
        ("systemd-tmpfiles", "PREPARED"),
    ]
    assert partial_directory.is_dir()
    assert not (rootfs / "var/lib/ecobin/business").exists()

    resumed = _installer(rootfs, fake, runner=interrupted_runner).install(
        payload,
        digest,
        apply=True,
        **_confirmations(),
    )

    assert resumed["status"] == "PASS"
    assert sum(call[0] == "systemd-tmpfiles" for call in runner_calls) == 2
    assert all(
        (rootfs / absolute.lstrip("/")).is_dir()
        for absolute in TMPFILES_DIRECTORY_MODES
    )
    assert [
        (intent["name"], intent["state"])
        for intent in _maintenance_record(rootfs)["setupCommandIntents"]
    ] == [
        ("systemd-sysusers", "DONE"),
        ("systemd-tmpfiles", "DONE"),
    ]


def test_prepared_tmpfiles_rejects_unknown_child_before_replay(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    partial_absolute = "/var/lib/ecobin/communication"
    partial_directory = rootfs / partial_absolute.lstrip("/")
    interrupt_next_tmpfiles = True
    runner_calls: list[tuple[str, ...]] = []

    def interrupted_runner(arguments: Sequence[str]) -> CommandResult:
        nonlocal interrupt_next_tmpfiles
        args = tuple(arguments)
        runner_calls.append(args)
        if args[:2] == ("systemd-tmpfiles", "--create") and interrupt_next_tmpfiles:
            partial_directory.mkdir(parents=True, exist_ok=True)
            os.chmod(partial_directory, 0o700)
            fake.owners[partial_absolute] = (995, 995)
            fake.modes[partial_absolute] = 0o700
            interrupt_next_tmpfiles = False
            raise MaintenanceProcessInterrupted(
                "simulated power loss after the first declared directory"
            )
        return fake(args)

    with pytest.raises(MaintenanceProcessInterrupted):
        _installer(rootfs, fake, runner=interrupted_runner).install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    unknown = partial_directory / "unmanaged-state"
    _write(unknown, b"must not be adopted\n", 0o600)
    before = _filesystem_snapshot(rootfs)
    with pytest.raises(
        MaintenanceInstallError,
        match="partial tmpfiles directory has unknown content",
    ):
        _installer(rootfs, fake, runner=interrupted_runner).install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    after = _filesystem_snapshot(rootfs)
    maintenance_prefix = "var/lib/ecobin/device-management-maintenance/"
    assert {
        path: value
        for path, value in after.items()
        if not path.startswith(maintenance_prefix)
    } == {
        path: value
        for path, value in before.items()
        if not path.startswith(maintenance_prefix)
    }
    assert unknown.read_bytes() == b"must not be adopted\n"
    assert sum(call[0] == "systemd-tmpfiles" for call in runner_calls) == 1
    assert not (rootfs / "var/lib/ecobin/business").exists()


def test_preflight_rejects_unknown_maintenance_state_without_backup_tree_read_only(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    maintenance_root = rootfs / "var/lib/ecobin/device-management-maintenance"
    maintenance_root.mkdir(mode=0o700, parents=True)
    os.chmod(maintenance_root, 0o700)
    unknown = maintenance_root / "unknown-state"
    _write(unknown, b"unattributed\n", 0o600)
    fake.modes[
        "/var/lib/ecobin/device-management-maintenance/unknown-state"
    ] = 0o600
    before = _filesystem_snapshot(rootfs)

    with pytest.raises(MaintenanceInstallError, match="unknown state"):
        _installer(rootfs, fake).preflight(
            payload,
            digest,
            **_confirmations(),
        )

    assert _filesystem_snapshot(rootfs) == before
    assert not (maintenance_root / "backups").exists()


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


@pytest.mark.parametrize("directory_kind", ("root", "backups", "operation"))
def test_audit_and_rollback_preview_reject_unsafe_maintenance_ancestry(
    tmp_path: Path,
    directory_kind: str,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    marker = json.loads(
        (rootfs / ACTIVE_MARKER.lstrip("/")).read_text(encoding="utf-8")
    )
    paths = {
        "root": "/var/lib/ecobin/device-management-maintenance",
        "backups": "/var/lib/ecobin/device-management-maintenance/backups",
        "operation": (
            "/var/lib/ecobin/device-management-maintenance/backups/"
            f"{marker['operationId']}"
        ),
    }
    absolute = paths[directory_kind]
    os.chmod(rootfs / absolute.lstrip("/"), 0o777)
    fake.modes[absolute] = 0o777

    with pytest.raises(MaintenanceInstallError, match="writable|mode"):
        _installer(rootfs, fake).audit(**_confirmations())
    with pytest.raises(MaintenanceInstallError, match="writable|mode"):
        _installer(rootfs, fake).rollback(**_confirmations())


def test_install_fence_blocks_coordinator_start_before_intents_are_durable(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    observations: list[tuple[bool, list, str, bool]] = []

    def race_after_daemon_reload() -> None:
        record = _maintenance_record(rootfs)
        fake(("systemctl", "start", "ecobin-updater.service"))
        observations.append(
            (
                (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file(),
                list(record["unitStartIntents"]),
                fake.states["ecobin-updater.service"]["ActiveState"],
                _updater_database(rootfs).exists(),
            )
        )

    fake.after_daemon_reload = race_after_daemon_reload
    result = _installer(rootfs, fake).install(
        payload,
        digest,
        apply=True,
        **_confirmations(),
    )

    assert result["status"] == "PASS"
    assert observations == [(True, [], "inactive", False)]
    assert [
        item["unit"] for item in _maintenance_record(rootfs)["unitStartIntents"]
    ] == list(START_UNITS)
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()


def test_fresh_install_resumes_after_fence_release_before_first_unit_start(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    interrupted_calls: list[tuple[str, ...]] = []

    def interrupt_before_first_start(arguments: Sequence[str]) -> CommandResult:
        call = tuple(arguments)
        interrupted_calls.append(call)
        if call[:2] == ("systemctl", "start"):
            raise MaintenanceProcessInterrupted(
                "simulated power loss before the first unit start"
            )
        return fake(call)

    interrupted_installer = MaintenanceInstaller(
        rootfs=rootfs,
        runner=interrupt_before_first_start,
        enforce_root_ownership=False,
        check_host_tools=False,
        metadata_identity=fake.metadata_identity,
        metadata_mode=fake.metadata_mode,
    )
    with pytest.raises(MaintenanceProcessInterrupted):
        interrupted_installer.install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    interrupted_record = _maintenance_record(rootfs)
    assert interrupted_record["status"] == "INSTALLING"
    assert interrupted_record["activation"]["state"] == "STARTING"
    assert interrupted_record["runtimeStartFence"]["state"] == "RELEASED"
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    assert all(fake.states[unit]["ActiveState"] == "inactive" for unit in START_UNITS)
    assert interrupted_calls[-1][:2] == ("systemctl", "start")
    assert not any(call[:2] == ("systemctl", "start") for call in fake.calls)

    observations: list[tuple[str, str | bool, bool | None]] = []

    def observe_reload() -> None:
        observations.append(
            (
                "daemon-reload",
                (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file(),
                None,
            )
        )

    def observe_start(unit: str) -> None:
        if not any(item[0] == "first-start" for item in observations):
            observations.append(
                (
                    "first-start",
                    unit,
                    (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file(),
                )
            )

    fake.calls.clear()
    fake.after_daemon_reload = observe_reload
    fake.after_start = observe_start
    result = _installer(rootfs, fake).install(
        payload,
        digest,
        apply=True,
        **_confirmations(),
    )

    assert result["status"] == "PASS"
    assert observations[0] == ("daemon-reload", True, None)
    assert observations[1] == ("first-start", START_UNITS[0], False)
    reload_index = fake.calls.index(("systemctl", "daemon-reload"))
    first_start_index = next(
        index
        for index, call in enumerate(fake.calls)
        if call[:2] == ("systemctl", "start")
    )
    assert reload_index < first_start_index
    installed_record = _maintenance_record(rootfs)
    assert installed_record["activation"]["state"] == "VERIFIED"
    assert installed_record["runtimeStartFence"]["state"] == "RELEASED"
    assert all(fake.states[unit]["ActiveState"] == "active" for unit in START_UNITS)
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()


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


def test_audit_rejects_a_recreated_runtime_start_fence(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    record = _maintenance_record(rootfs)
    fence_document = {
        "schemaVersion": 1,
        "operationId": record["operationId"],
        "payloadId": record["payloadId"],
        "manifestSha256": record["manifestSha256"],
    }
    _write(
        rootfs / RUNTIME_START_FENCE.lstrip("/"),
        (
            json.dumps(
                fence_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode(),
        0o600,
    )

    with pytest.raises(MaintenanceInstallError, match="not safely released"):
        installer.audit(**_confirmations())

    assert all(fake.states[unit]["ActiveState"] == "active" for unit in START_UNITS)


def test_rollback_is_dry_run_then_removes_only_exact_artifacts_and_restores_modes(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())

    preview = installer.rollback(**_confirmations())
    assert preview["status"] == "ROLLBACK_REQUIRES_FENCED_INSPECTION"
    assert preview["logicalEligibilityChecked"] is False
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


def test_fresh_rollback_does_not_replay_setup_after_config_removal_started(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    sysusers_config = rootfs / "usr/lib/sysusers.d/ecobin-device-runtime.conf"
    tmpfiles_config = rootfs / "usr/lib/tmpfiles.d/ecobin-device-runtime.conf"
    original_remove = installer._remove_artifact_intent
    interrupted = False

    def interrupt_after_configs_removed(item, *, operation_id: str) -> None:
        nonlocal interrupted
        original_remove(item, operation_id=operation_id)
        if (
            not interrupted
            and not sysusers_config.exists()
            and not tmpfiles_config.exists()
        ):
            interrupted = True
            raise MaintenanceProcessInterrupted(
                "simulated power loss after setup configurations were removed"
            )

    installer._remove_artifact_intent = interrupt_after_configs_removed
    with pytest.raises(MaintenanceProcessInterrupted):
        installer.rollback(apply=True, **_confirmations())

    interrupted_record = _maintenance_record(rootfs)
    operation_record = (
        rootfs
        / BACKUP_PARENT.lstrip("/")
        / interrupted_record["operationId"]
        / "record.json"
    )
    assert interrupted is True
    assert interrupted_record["status"] == "ROLLBACK_REMOVING"
    assert isinstance(interrupted_record["rollbackRemovalStartedAt"], str)
    assert all(
        intent["state"] == "DONE"
        for intent in interrupted_record["setupCommandIntents"]
    )
    assert not sysusers_config.exists()
    assert not tmpfiles_config.exists()

    fake.calls.clear()
    recovered = _installer(rootfs, fake).rollback(
        apply=True,
        **_confirmations(),
    )

    assert recovered["status"] == "ROLLED_BACK"
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert json.loads(operation_record.read_text(encoding="utf-8"))["status"] == (
        "ROLLED_BACK"
    )
    assert not any(
        call[0] in {"systemd-sysusers", "systemd-tmpfiles"}
        for call in fake.calls
    )


def test_fresh_rollback_only_finalizes_terminal_record_and_residual_locator(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    active_marker = rootfs / ACTIVE_MARKER.lstrip("/")
    marker_raw = active_marker.read_bytes()
    marker = json.loads(marker_raw)
    record_path = (
        rootfs
        / "var/lib/ecobin/device-management-maintenance/backups"
        / marker["operationId"]
        / "record.json"
    )

    assert installer.rollback(apply=True, **_confirmations())["status"] == "ROLLED_BACK"
    assert json.loads(record_path.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"

    # Model power loss after the terminal record was fsynced but before the
    # locator and an earlier interrupted singleton write were removed.
    _write(active_marker, marker_raw, 0o600)
    active_incoming = active_marker.with_name(".active.json.incoming")
    _write(active_incoming, marker_raw, 0o600)
    fake.modes[ACTIVE_MARKER] = 0o600
    fake.modes[
        "/var/lib/ecobin/device-management-maintenance/.active.json.incoming"
    ] = 0o600
    fake.calls.clear()

    result = _installer(rootfs, fake).rollback(
        apply=True,
        **_confirmations(),
    )

    assert result["status"] == "ROLLED_BACK"
    assert not active_marker.exists()
    assert not active_incoming.exists()
    assert json.loads(record_path.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"
    assert not any(
        call[0] in {"systemd-sysusers", "systemd-tmpfiles"}
        or call[:2] in {("systemctl", "start"), ("systemctl", "stop")}
        for call in fake.calls
    )


@pytest.mark.parametrize("incoming_kind", ("foreign", "oversized"))
def test_terminal_rollback_preview_rejects_unsafe_singleton_incoming(
    tmp_path: Path,
    incoming_kind: str,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    active_marker = rootfs / ACTIVE_MARKER.lstrip("/")
    marker_raw = active_marker.read_bytes()
    installer.rollback(apply=True, **_confirmations())

    _write(active_marker, marker_raw, 0o600)
    incoming = active_marker.with_name(".active.json.incoming")
    incoming_raw = (
        b"{}"
        if incoming_kind == "foreign"
        else b"{" + (b"x" * MAX_MANIFEST_BYTES)
    )
    _write(incoming, incoming_raw, 0o600)
    before = _filesystem_snapshot(rootfs)

    with pytest.raises(MaintenanceInstallError, match="cannot be attributed"):
        _installer(rootfs, fake).rollback(**_confirmations())

    assert _filesystem_snapshot(rootfs) == before


def test_terminal_rollback_keeps_locator_when_legacy_service_is_inactive(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    active_marker = rootfs / ACTIVE_MARKER.lstrip("/")
    marker_raw = active_marker.read_bytes()
    installer.rollback(apply=True, **_confirmations())

    _write(active_marker, marker_raw, 0o600)
    fake.states[LEGACY_SERVICE].update(ActiveState="inactive", SubState="dead")

    with pytest.raises(MaintenanceInstallError, match="legacy service is not active"):
        _installer(rootfs, fake).rollback(
            apply=True,
            **_confirmations(),
        )

    assert active_marker.read_bytes() == marker_raw
    assert fake.states[LEGACY_SERVICE]["ActiveState"] == "inactive"


@pytest.mark.parametrize("inactive_unit", START_UNITS)
def test_rollback_defers_before_mutation_when_a_permanent_unit_is_inactive(
    tmp_path: Path,
    inactive_unit: str,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.states[inactive_unit].update(ActiveState="inactive", SubState="dead")
    active_marker = rootfs / ACTIVE_MARKER.lstrip("/")
    marker_raw = active_marker.read_bytes()
    marker = json.loads(marker_raw)
    record_path = (
        rootfs
        / "var/lib/ecobin/device-management-maintenance/backups"
        / marker["operationId"]
        / "record.json"
    )
    fake.calls.clear()

    with pytest.raises(
        RollbackDeferredForBusyState,
        match="every permanent service to be healthy before quiesce",
    ):
        _installer(rootfs, fake).rollback(
            apply=True,
            **_confirmations(),
        )

    assert not any(
        call[:2] in {("systemctl", "start"), ("systemctl", "stop")}
        for call in fake.calls
    )
    assert fake.states[inactive_unit]["ActiveState"] == "inactive"
    assert all(
        fake.states[unit]["ActiveState"] == "active"
        for unit in START_UNITS
        if unit != inactive_unit
    )
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    assert active_marker.read_bytes() == marker_raw
    deferred_record = json.loads(record_path.read_text(encoding="utf-8"))
    assert deferred_record["status"] == "INSTALLED"
    assert deferred_record["activation"]["state"] == "VERIFIED"
    assert deferred_record["runtimeStartFence"]["state"] == "RELEASED"
    assert deferred_record["lastRollbackDeferral"]["servicesStopped"] is False
    assert inactive_unit in deferred_record["lastRollbackDeferral"]["reason"]


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-database",
        "corrupt-database",
        "schema-version",
        "unknown-table",
        "base-ddl-drift",
        "extension-ddl-drift",
        "missing-extension-table",
        "candidate-enabled",
        "completed-permit",
        "confirmed-action",
        "maintenance-lock",
    ),
)
def test_rollback_rejects_non_pristine_updater_state_without_deleting_code(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    _mutate_updater_rollback_state(rootfs, fake, mutation)
    calls_before = len(fake.calls)
    installed_code = rootfs / "etc/systemd/system/ecobin-updater.service"

    with pytest.raises(MaintenanceInstallError, match="updater rollback"):
        installer.rollback(apply=True, **_confirmations())

    assert installed_code.is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert fake.states[LEGACY_SERVICE]["ActiveState"] == "active"
    changed_calls = _new_mutating_systemctl_calls(fake, calls_before)
    if mutation == "missing-database":
        assert changed_calls == []
        assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    elif mutation in {
        "candidate-enabled",
        "completed-permit",
        "confirmed-action",
        "maintenance-lock",
    }:
        assert any(call[:2] == ("systemctl", "stop") for call in changed_calls)
        assert any(call[:2] == ("systemctl", "start") for call in changed_calls)
        assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
        assert _maintenance_record(rootfs)["status"] == "INSTALLED"
        assert all(fake.states[unit]["ActiveState"] == "active" for unit in START_UNITS)
    else:
        assert any(call[:2] == ("systemctl", "stop") for call in changed_calls)
        assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
        assert _maintenance_record(rootfs)["status"] == "ROLLBACK_BLOCKED"


def test_rollback_rejects_historical_activation_after_candidate_is_disabled_again(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    activated = _activate_stage4_candidate(rootfs, fake)
    activated.close()
    disabled = UpdaterStore(
        _updater_database(rootfs),
        release_version=UPDATER_RELEASE,
        enable_stage4_candidate=False,
    )
    disabled.initialize()
    disabled.close()
    _record_updater_sidecar_metadata(rootfs, fake)
    with sqlite3.connect(_updater_database(rootfs)) as connection:
        posture = connection.execute(
            """SELECT stage4_candidate_enabled, job_gate_mode,
                      job_gate_state, reconciliation_required,
                      block_reason_code
               FROM updater_management_state"""
        ).fetchone()
        activation = connection.execute(
            """SELECT candidate_activation_state
               FROM job_gate_control_extension"""
        ).fetchone()[0]
        operation_count = connection.execute(
            "SELECT COUNT(*) FROM job_gate_control_operation"
        ).fetchone()[0]
    assert posture == (
        0,
        "DISABLED",
        "LOCKED",
        0,
        "STAGE4_CANDIDATE_DISABLED",
    )
    assert activation == "REQUIRED"
    assert operation_count == 1
    calls_before = len(fake.calls)

    with pytest.raises(MaintenanceInstallError, match="not permitted"):
        installer.rollback(apply=True, **_confirmations())

    changed_calls = _new_mutating_systemctl_calls(fake, calls_before)
    assert any(call[:2] == ("systemctl", "stop") for call in changed_calls)
    assert any(call[:2] == ("systemctl", "start") for call in changed_calls)
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    assert _maintenance_record(rootfs)["status"] == "INSTALLED"


def test_rollback_dry_run_reads_updater_state_without_mutating_it(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    database = _updater_database(rootfs)
    marker = rootfs / ACTIVE_MARKER.lstrip("/")
    record = _maintenance_record(rootfs)
    record_path = (
        rootfs
        / "var/lib/ecobin/device-management-maintenance/backups"
        / record["operationId"]
        / "record.json"
    )
    before = {
        "database": hashlib.sha256(database.read_bytes()).hexdigest(),
        "marker": marker.read_bytes(),
        "record": record_path.read_bytes(),
    }
    calls_before = len(fake.calls)

    result = installer.rollback(**_confirmations())

    assert result["status"] == "ROLLBACK_REQUIRES_FENCED_INSPECTION"
    assert result["updaterRollbackState"] == "DATABASE_METADATA_ONLY"
    assert result["logicalEligibilityChecked"] is False
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before["database"]
    assert marker.read_bytes() == before["marker"]
    assert record_path.read_bytes() == before["record"]
    assert _new_mutating_systemctl_calls(fake, calls_before) == []
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def test_second_rollback_check_catches_activation_committed_during_quiesce(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    activated: list[UpdaterStore] = []

    def activate_after_first_stop(_unit: str) -> None:
        activated.append(_activate_stage4_candidate(rootfs, fake))

    fake.after_first_stop = activate_after_first_stop
    calls_before = len(fake.calls)
    try:
        with pytest.raises(MaintenanceInstallError, match="updater rollback"):
            installer.rollback(apply=True, **_confirmations())
    finally:
        for store in activated:
            store.close()

    changed_calls = fake.calls[calls_before:]
    assert any(call[:2] == ("systemctl", "stop") for call in changed_calls)
    assert ("systemctl", "start", LEGACY_SERVICE) not in changed_calls
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / "opt/ecobin/updater/current").is_symlink()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "INSTALLED"
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    assert all(fake.states[unit]["ActiveState"] == "active" for unit in START_UNITS)


def test_online_rollback_check_observes_uncheckpointed_wal_activation(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    activated = _activate_stage4_candidate(rootfs, fake)
    database = _updater_database(rootfs)
    wal = Path(f"{database}-wal")
    assert wal.is_file() and wal.stat().st_size > 0
    calls_before = len(fake.calls)
    try:
        with pytest.raises(MaintenanceInstallError, match="updater rollback"):
            installer.rollback(apply=True, **_confirmations())
    finally:
        activated.close()

    changed_calls = _new_mutating_systemctl_calls(fake, calls_before)
    assert any(call[:2] == ("systemctl", "stop") for call in changed_calls)
    assert any(call[:2] == ("systemctl", "start") for call in changed_calls)
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    assert _maintenance_record(rootfs)["status"] == "INSTALLED"


def test_rollback_rejects_wal_without_shm_without_creating_a_sidecar(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    database = _updater_database(rootfs)
    wal = Path(f"{database}-wal")
    shm = Path(f"{database}-shm")
    wal.write_bytes(b"incomplete crash residue")
    fake.owners[f"{UPDATER_STATE_DATABASE_PATH}-wal"] = (993, 988)
    fake.modes[f"{UPDATER_STATE_DATABASE_PATH}-wal"] = 0o640
    calls_before = len(fake.calls)

    with pytest.raises(MaintenanceInstallError, match="sidecars are incomplete"):
        installer.rollback(apply=True, **_confirmations())

    assert wal.read_bytes() == b"incomplete crash residue"
    assert not shm.exists()
    assert any(
        call[:2] == ("systemctl", "stop")
        for call in _new_mutating_systemctl_calls(fake, calls_before)
    )
    assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()


def test_checkpoint_size_growth_does_not_look_like_database_replacement(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    database = _updater_database(rootfs)
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    now = "2026-09-03T00:00:00.000000Z"
    writer.executemany(
        """INSERT INTO updater_runtime_instance (
               instance_uid, component, release_version, started_at
           ) VALUES (?, 'DEVICE_UPDATER', ?, ?)""",
        (
            (str(uuid.uuid4()), UPDATER_RELEASE, now)
            for _ in range(800)
        ),
    )
    writer.commit()
    _record_updater_sidecar_metadata(rootfs, fake)
    main_size_before = database.stat().st_size
    main_size_after: list[int] = []

    def checkpoint_after_first_stop(_unit: str) -> None:
        writer.close()
        main_size_after.append(database.stat().st_size)

    fake.after_first_stop = checkpoint_after_first_stop
    result = installer.rollback(apply=True, **_confirmations())

    assert result["status"] == "ROLLED_BACK"
    assert main_size_after and main_size_after[0] > main_size_before
    assert database.is_file()
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()


def test_mcu_recovery_marker_blocks_rollback_before_any_stop(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    _write(rootfs / MCU_RECOVERY_MARKER.lstrip("/"), b"required\n", 0o600)
    calls_before = len(fake.calls)

    with pytest.raises(MaintenanceInstallError, match="MCU application recovery"):
        installer.rollback(apply=True, **_confirmations())

    assert _new_mutating_systemctl_calls(fake, calls_before) == []
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()


def test_mcu_recovery_marker_created_during_stop_blocks_artifact_deletion(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())

    def create_recovery_marker(_unit: str) -> None:
        _write(
            rootfs / MCU_RECOVERY_MARKER.lstrip("/"),
            b"required\n",
            0o600,
        )

    fake.after_first_stop = create_recovery_marker
    calls_before = len(fake.calls)
    with pytest.raises(MaintenanceInstallError, match="MCU application recovery"):
        installer.rollback(apply=True, **_confirmations())

    changed_calls = fake.calls[calls_before:]
    assert any(call[:2] == ("systemctl", "stop") for call in changed_calls)
    assert ("systemctl", "start", LEGACY_SERVICE) not in changed_calls
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / "opt/ecobin/updater/current").is_symlink()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()


def test_mcu_recovery_marker_is_rechecked_before_legacy_restore(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())

    def create_recovery_marker() -> None:
        _write(
            rootfs / MCU_RECOVERY_MARKER.lstrip("/"),
            b"required\n",
            0o600,
        )

    fake.after_daemon_reload = create_recovery_marker
    calls_before = len(fake.calls)
    with pytest.raises(MaintenanceInstallError, match="MCU application recovery"):
        installer.rollback(apply=True, **_confirmations())

    changed_calls = fake.calls[calls_before:]
    assert ("systemctl", "start", LEGACY_SERVICE) not in changed_calls
    assert (rootfs / MCU_RECOVERY_MARKER.lstrip("/")).is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "ROLLBACK_BLOCKED"


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


def test_changed_legacy_service_fragment_blocks_rollback_before_any_stop(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fragment = rootfs / "etc/systemd/system/ecobin-hardware.service"
    fragment.write_bytes(b"[Service]\nExecStart=/bin/false\n")
    calls_before = len(fake.calls)

    with pytest.raises(MaintenanceInstallError, match="protected device path"):
        installer.rollback(apply=True, **_confirmations())

    assert _new_mutating_systemctl_calls(fake, calls_before) == []
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert ("systemctl", "start", LEGACY_SERVICE) not in fake.calls[calls_before:]


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

    calls_before = len(fake.calls)
    with pytest.raises(MaintenanceInstallError, match="definition changed"):
        installer.rollback(apply=True, **_confirmations())

    assert gate.read_bytes() == LEGACY_GATE_DROP_IN_CONTENT
    assert gate.lstat().st_ino == gate_inode
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "ROLLBACK_BLOCKED"
    assert ("systemctl", "start", LEGACY_SERVICE) not in fake.calls[calls_before:]


def test_rollback_refuses_to_delete_a_locally_changed_file(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    changed = rootfs / "etc/systemd/system/ecobin-updater.service"
    changed.write_text("local administrator change\n", encoding="utf-8")

    calls_before = len(fake.calls)
    with pytest.raises(MaintenanceInstallError, match="installed file changed"):
        installer.rollback(apply=True, **_confirmations())

    assert changed.read_text(encoding="utf-8") == "local administrator change\n"
    assert not any(
        call[:2] == ("systemctl", "stop") for call in fake.calls[calls_before:]
    )
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).exists()


def test_hidden_vendor_fallback_blocks_rollback_before_code_deletion(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    _write(
        rootfs / "usr/lib/systemd/system/ecobin-updater.service",
        b"[Unit]\nDescription=uncontrolled fallback\n\n[Service]\nExecStart=/bin/false\n",
    )
    calls_before = len(fake.calls)

    with pytest.raises(MaintenanceInstallError, match="fallback definition"):
        installer.rollback(apply=True, **_confirmations())

    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    # Setup-effect recovery now establishes the persistent fence before any
    # privileged declarative command could be replayed.  A later fallback
    # mismatch therefore fails closed with the fence retained.
    assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert not any(
        call[:2] == ("systemctl", "stop") for call in fake.calls[calls_before:]
    )


def test_failed_updater_start_with_no_database_requires_forward_resume(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)
    fake.fail_start = "ecobin-updater.service"

    with pytest.raises(MaintenanceInstallError, match="must be resumed"):
        _installer(rootfs, fake).install(
            payload, digest, apply=True, **_confirmations()
        )

    if os.name == "posix":
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o755
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert (rootfs / PENDING_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "INSTALL_RESUME_REQUIRED"
    assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
    assert fake.states[LEGACY_SERVICE]["ActiveState"] == "active"
    fake.fail_start = None
    result = _installer(rootfs, fake).install(
        payload, digest, apply=True, **_confirmations()
    )
    assert result["status"] == "PASS"
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()


def test_install_failure_after_pristine_updater_start_resumes_forward(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, runtime = _make_rootfs(tmp_path)
    fake.fail_start = "ecobin-device-management-preflight.service"

    with pytest.raises(MaintenanceInstallError, match="must be resumed"):
        _installer(rootfs, fake).install(
            payload, digest, apply=True, **_confirmations()
        )

    assert _updater_database(rootfs).is_file()
    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    assert (rootfs / PENDING_MARKER.lstrip("/")).is_file()
    assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
    assert fake.states[LEGACY_SERVICE]["ActiveState"] == "active"
    fake.fail_start = None
    result = _installer(rootfs, fake).install(
        payload, digest, apply=True, **_confirmations()
    )
    assert result["status"] == "PASS"


def test_used_updater_state_after_start_is_retained_for_forward_resume(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    fake.fail_start = "ecobin-device-management-preflight.service"

    def activate_after_start(unit: str) -> None:
        if unit != "ecobin-updater.service":
            return
        activated = _activate_stage4_candidate(rootfs, fake)
        activated.close()
        _record_updater_sidecar_metadata(rootfs, fake)

    fake.after_start = activate_after_start

    with pytest.raises(
        MaintenanceInstallError,
        match="must be resumed",
    ):
        _installer(rootfs, fake).install(
            payload, digest, apply=True, **_confirmations()
        )

    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / "opt/ecobin/updater/current").is_symlink()
    assert (rootfs / PENDING_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "INSTALL_RESUME_REQUIRED"
    assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
    assert all(
        fake.states[unit]["ActiveState"] == "active"
        for unit in START_UNITS
        if unit != "ecobin-device-management-preflight.service"
    )
    assert (
        fake.states["ecobin-device-management-preflight.service"]["ActiveState"]
        == "inactive"
    )
    assert ("systemctl", "start", LEGACY_SERVICE) not in fake.calls
    fake.fail_start = None
    fake.after_start = None
    result = _installer(rootfs, fake).install(
        payload, digest, apply=True, **_confirmations()
    )
    assert result["status"] == "PASS"


def test_database_loss_after_updater_start_is_repaired_by_forward_resume(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    fake.fail_start = "ecobin-device-management-preflight.service"

    def delete_state_after_updater_start(unit: str) -> None:
        if unit != "ecobin-updater.service":
            return
        activated = _activate_stage4_candidate(rootfs, fake)
        activated.close()
        database = _updater_database(rootfs)
        for path in (
            database,
            Path(f"{database}-wal"),
            Path(f"{database}-shm"),
            Path(f"{database}-journal"),
        ):
            if path.exists():
                path.unlink()

    fake.after_start = delete_state_after_updater_start

    with pytest.raises(MaintenanceInstallError, match="must be resumed"):
        _installer(rootfs, fake).install(
            payload,
            digest,
            apply=True,
            **_confirmations(),
        )

    assert (rootfs / "etc/systemd/system/ecobin-updater.service").is_file()
    assert (rootfs / "opt/ecobin/updater/current").is_symlink()
    assert (rootfs / PENDING_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "INSTALL_RESUME_REQUIRED"
    assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()
    assert ("systemctl", "start", LEGACY_SERVICE) not in fake.calls
    fake.fail_start = None
    fake.after_start = None
    result = _installer(rootfs, fake).install(
        payload, digest, apply=True, **_confirmations()
    )
    assert result["status"] == "PASS"
    assert _updater_database(rootfs).is_file()


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


@pytest.mark.parametrize("unsafe_kind", ("mode", "size"))
def test_preflight_rejects_unsafe_unlocated_final_record_read_only(
    tmp_path: Path,
    unsafe_kind: str,
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

    pending_path = rootfs / PENDING_MARKER.lstrip("/")
    marker = json.loads(pending_path.read_text(encoding="utf-8"))
    record_absolute = (
        "/var/lib/ecobin/device-management-maintenance/backups/"
        f"{marker['operationId']}/record.json"
    )
    record_path = rootfs / record_absolute.lstrip("/")
    pending_path.unlink()
    if unsafe_kind == "mode":
        os.chmod(record_path, 0o666)
        fake.modes[record_absolute] = 0o666
    else:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["padding"] = "x" * MAX_MANIFEST_BYTES
        _write(
            record_path,
            (
                json.dumps(record, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode(),
            0o600,
        )
        assert record_path.stat().st_size > MAX_MANIFEST_BYTES
    before = _filesystem_snapshot(rootfs)

    with pytest.raises(MaintenanceInstallError, match="mode or size"):
        _installer(rootfs, fake).preflight(
            payload,
            digest,
            **_confirmations(),
        )

    assert _filesystem_snapshot(rootfs) == before


def test_empty_unlocated_operation_is_read_only_then_recovered(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    operation = _create_orphan_operation_directory(
        rootfs, "op-empty-operation"
    )
    before = _filesystem_snapshot(rootfs)
    installer = _installer(rootfs, fake)

    preview = installer.install(payload, digest, **_confirmations())

    assert preview["status"] == "UNLOCATED_INSTALLATION_RECOVERY_REQUIRED"
    assert preview["operationId"] == "op-empty-operation"
    assert preview["orphanKind"] == "EMPTY_OPERATION"
    assert preview["samePayload"] is False
    assert preview["dryRun"] is True
    assert _filesystem_snapshot(rootfs) == before

    result = installer.install(
        payload, digest, apply=True, **_confirmations()
    )

    assert result["status"] == "PASS"
    assert not operation.exists()


def test_truncated_record_only_orphan_is_read_only_then_recovered(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    operation = _create_orphan_operation_directory(
        rootfs, "op-record-incoming-only"
    )
    _write(
        operation / ".record.json.incoming",
        b'{"schemaVersion":1,"operationId":"',
        0o600,
    )
    before = _filesystem_snapshot(rootfs)
    installer = _installer(rootfs, fake)

    preview = installer.install(payload, digest, **_confirmations())

    assert preview["status"] == "UNLOCATED_INSTALLATION_RECOVERY_REQUIRED"
    assert preview["operationId"] == "op-record-incoming-only"
    assert preview["orphanKind"] == "EMPTY_RECORD_INCOMING"
    assert preview["samePayload"] is False
    assert preview["dryRun"] is True
    assert _filesystem_snapshot(rootfs) == before

    result = installer.install(
        payload, digest, apply=True, **_confirmations()
    )

    assert result["status"] == "PASS"
    assert not operation.exists()


def test_unlocated_record_and_pending_incoming_roll_back_then_install(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    operation_id, record_path, artifact_incoming = (
        _leave_first_artifact_unpublished(
            rootfs, fake, payload, digest
        )
    )
    pending = rootfs / PENDING_MARKER.lstrip("/")
    pending_incoming = pending.with_name(f".{pending.name}.incoming")
    os.replace(pending, pending_incoming)
    assert not pending.exists()
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()
    before = _filesystem_snapshot(rootfs)
    installer = _installer(rootfs, fake)

    preview = installer.install(payload, digest, **_confirmations())

    assert preview["status"] == "UNLOCATED_INSTALLATION_RECOVERY_REQUIRED"
    assert preview["operationId"] == operation_id
    assert preview["orphanKind"] == "RECORD"
    assert preview["samePayload"] is True
    assert preview["dryRun"] is True
    assert _filesystem_snapshot(rootfs) == before

    result = installer.install(
        payload, digest, apply=True, **_confirmations()
    )

    assert result["status"] == "PASS"
    assert json.loads(record_path.read_text(encoding="utf-8"))["status"] == (
        "ROLLED_BACK"
    )
    assert not pending_incoming.exists()
    assert not artifact_incoming.exists()
    active = json.loads(
        (rootfs / ACTIVE_MARKER.lstrip("/")).read_text(encoding="utf-8")
    )
    assert active["operationId"] != operation_id


def test_unlocated_final_record_discards_torn_record_incoming_and_recovers(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    operation_id, record_path, artifact_incoming = (
        _leave_first_artifact_unpublished(
            rootfs, fake, payload, digest
        )
    )
    (rootfs / PENDING_MARKER.lstrip("/")).unlink()
    record_incoming = record_path.with_name(".record.json.incoming")
    _write(record_incoming, b'{"schemaVersion":1,"operation', 0o600)

    result = _installer(rootfs, fake).install(
        payload, digest, apply=True, **_confirmations()
    )

    assert result["status"] == "PASS"
    assert json.loads(record_path.read_text(encoding="utf-8"))["status"] == (
        "ROLLED_BACK"
    )
    assert not record_incoming.exists()
    assert not artifact_incoming.exists()
    active = json.loads(
        (rootfs / ACTIVE_MARKER.lstrip("/")).read_text(encoding="utf-8")
    )
    assert active["operationId"] != operation_id


def test_multiple_unlocated_operations_fail_closed_without_writes(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    _create_orphan_operation_directory(rootfs, "op-orphan-one")
    _create_orphan_operation_directory(rootfs, "op-orphan-two")
    before = _filesystem_snapshot(rootfs)

    with pytest.raises(
        MaintenanceInstallError,
        match="multiple unlocated maintenance operations",
    ):
        _installer(rootfs, fake).install(
            payload, digest, apply=True, **_confirmations()
        )

    assert _filesystem_snapshot(rootfs) == before


def test_unlocated_operation_for_different_payload_fails_without_writes(
    tmp_path: Path,
) -> None:
    old_payload, old_digest = _make_payload(tmp_path / "old")
    new_payload, _new_digest = _make_payload(tmp_path / "new")
    new_digest = _rewrite_manifest(
        new_payload,
        lambda document: document.__setitem__(
            "payloadId", "stage3-maintenance-20260903-17"
        ),
    )
    rootfs, fake, _runtime = _make_rootfs(tmp_path / "device")
    operation_id, _record_path, _artifact_incoming = (
        _leave_first_artifact_unpublished(
            rootfs, fake, old_payload, old_digest
        )
    )
    (rootfs / PENDING_MARKER.lstrip("/")).unlink()
    installer = _installer(rootfs, fake)

    preview = installer.install(
        new_payload, new_digest, **_confirmations()
    )
    assert preview["status"] == "UNLOCATED_INSTALLATION_RECOVERY_REQUIRED"
    assert preview["operationId"] == operation_id
    assert preview["samePayload"] is False
    before = _filesystem_snapshot(rootfs)

    with pytest.raises(
        MaintenanceInstallError,
        match="belongs to another authenticated payload",
    ):
        installer.install(
            new_payload,
            new_digest,
            apply=True,
            **_confirmations(),
        )

    assert _filesystem_snapshot(rootfs) == before


@pytest.mark.parametrize("stop_failure", ("command", "sticky"))
def test_rollback_stop_gate_restores_healthy_installation_on_failure(
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
    assert _maintenance_record(rootfs)["status"] == "INSTALLED"
    assert not (rootfs / RUNTIME_START_FENCE.lstrip("/")).exists()
    assert all(fake.states[unit]["ActiveState"] == "active" for unit in START_UNITS)


def test_rollback_reload_failure_never_clears_marker(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.fail_reload = True

    with pytest.raises(MaintenanceInstallError, match="persistent runtime start fence"):
        installer.rollback(apply=True, **_confirmations())

    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert _maintenance_record(rootfs)["status"] == "ROLLBACK_BLOCKED"


def test_fresh_rollback_retries_a_durable_blocked_state(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.fail_reload = True

    with pytest.raises(MaintenanceInstallError, match="persistent runtime start fence"):
        installer.rollback(apply=True, **_confirmations())

    blocked_record = _maintenance_record(rootfs)
    assert blocked_record["status"] == "ROLLBACK_BLOCKED"
    assert isinstance(blocked_record["installationAuditPassedAt"], str)
    assert (rootfs / RUNTIME_START_FENCE.lstrip("/")).is_file()

    fake.fail_reload = False
    fake.calls.clear()
    recovered = _installer(rootfs, fake).rollback(
        apply=True,
        **_confirmations(),
    )

    assert recovered["status"] == "ROLLED_BACK"
    assert not (rootfs / ACTIVE_MARKER.lstrip("/")).exists()


def test_rollback_never_stops_live_privileged_helper_instance(
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

    with pytest.raises(MaintenanceInstallError, match="still running"):
        installer.rollback(apply=True, **_confirmations())

    assert (rootfs / "usr/lib/ecobin/device-management/local_control.py").is_file()
    assert (rootfs / ACTIVE_MARKER.lstrip("/")).is_file()
    assert ("systemctl", "stop", instance) not in fake.calls
    assert fake.states[instance]["ActiveState"] == "active"


def test_rollback_closes_socket_then_waits_for_new_helper_instance(
    tmp_path: Path,
) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    instance = "ecobin-mcu-flash-helper@late-connection.service"
    fake.spawn_helper_on_socket_stop = instance

    with pytest.raises(MaintenanceInstallError, match="still running"):
        installer.rollback(apply=True, **_confirmations())

    assert ("systemctl", "stop", instance) not in fake.calls
    assert fake.states[instance]["ActiveState"] == "active"
    fake.spawn_helper_on_socket_stop = None
    fake.states.pop(instance)
    result = installer.rollback(apply=True, **_confirmations())
    assert result["status"] == "ROLLED_BACK"
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

    with pytest.raises(MaintenanceInstallError, match="exact fenced unit"):
        installer.audit(**_confirmations())


def test_audit_rejects_uncontrolled_systemd_drop_in(tmp_path: Path) -> None:
    payload, digest = _make_payload(tmp_path)
    rootfs, fake, _runtime = _make_rootfs(tmp_path)
    installer = _installer(rootfs, fake)
    installer.install(payload, digest, apply=True, **_confirmations())
    fake.states["ecobin-updater.service"]["DropInPaths"] = (
        "/run/systemd/system/ecobin-updater.service.d/override.conf"
    )

    with pytest.raises(MaintenanceInstallError, match="exact fenced unit"):
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
