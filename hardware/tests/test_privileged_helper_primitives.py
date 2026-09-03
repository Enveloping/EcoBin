from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from device_management.helpers import privileged_control
from device_management.helpers.business_activation_primitives import (
    BUSINESS_SERVICE,
    SYSTEMCTL,
    SYSTEMCTL_STATUS_TIMEOUT_SECONDS,
    BusinessActivationPrimitives,
)
from device_management.helpers.mcu_flash_primitives import (
    BOOT0_WPI,
    GPIO_BINARY,
    RESET_GATE_WPI,
    SERIAL_DEVICE,
    STM32FLASH_BINARY,
    McuFlashPrimitives,
)
from device_management.helpers.mcu_flash_recovery import McuApplicationRecovery
from local_control import LocalControlActionError

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="root helper file-descriptor and ownership rules are Linux-only",
)


class FakeServiceRunner:
    def __init__(
        self,
        state: str = "inactive",
        *,
        stop_state: str = "inactive",
    ) -> None:
        self.state = state
        self.stop_state = stop_state
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> SimpleNamespace:
        self.calls.append((argv, kwargs))
        if argv == [
            SYSTEMCTL,
            "show",
            "--property=ActiveState",
            "--value",
            "--",
            BUSINESS_SERVICE,
        ]:
            return SimpleNamespace(returncode=0, stdout=f"{self.state}\n")
        if argv == [SYSTEMCTL, "stop", "--", BUSINESS_SERVICE]:
            self.state = self.stop_state
            return SimpleNamespace(returncode=0, stdout="")
        if argv == [SYSTEMCTL, "start", "--", BUSINESS_SERVICE]:
            self.state = "active"
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected command: {argv!r}")


def _new_database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE fact(value TEXT NOT NULL)")
        connection.execute("INSERT INTO fact(value) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _read_database_value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("SELECT value FROM fact").fetchone()[0])
    finally:
        connection.close()


def _business_primitives(
    tmp_path: Path,
    *,
    runner: FakeServiceRunner | None = None,
) -> tuple[BusinessActivationPrimitives, FakeServiceRunner, Path, Path, Path]:
    runtime = tmp_path / "business-runtime"
    (runtime / "releases").mkdir(parents=True)
    state = tmp_path / "business-state"
    state.mkdir()
    database = state / "edge.db"
    _new_database(database, "before")
    updater = tmp_path / "updater"
    (updater / "staging").mkdir(parents=True)
    snapshot_root = tmp_path / "privileged" / "business-snapshots"
    snapshot_root.mkdir(parents=True, mode=0o700)
    runner = runner or FakeServiceRunner()
    identity_uid = os.getuid()
    identity_gid = os.getgid()
    primitives = BusinessActivationPrimitives(
        business_root=runtime,
        database_path=database,
        updater_root=updater,
        snapshot_root=snapshot_root,
        business_uid=identity_uid,
        business_gid=identity_gid,
        updater_uid=identity_uid,
        updater_gid=identity_gid,
        privileged_uid=identity_uid,
        privileged_gid=identity_gid,
        command_runner=runner,
    )
    return primitives, runner, runtime, updater, database


def _stage_release(
    updater_root: Path,
    update_uid: str,
    release_uid: str,
    *,
    content: bytes = b"print('business runtime')\n",
) -> Path:
    release = updater_root / "staging" / update_uid / release_uid
    (release / "app").mkdir(parents=True)
    entrypoint = release / "app" / "main.py"
    entrypoint.write_bytes(content)
    entrypoint.chmod(0o750)
    return release


def test_service_control_uses_only_the_fixed_systemd_unit(tmp_path: Path) -> None:
    primitives, runner, _runtime, _updater, _database = _business_primitives(
        tmp_path
    )
    update_uid = str(uuid.uuid4())

    assert primitives.start({"updateUid": update_uid})["businessRuntimeState"] == "ACTIVE"
    assert primitives.stop({"updateUid": update_uid})["businessRuntimeState"] == "INACTIVE"

    command_argv = [call[0] for call in runner.calls]
    assert [SYSTEMCTL, "start", "--", BUSINESS_SERVICE] in command_argv
    assert [SYSTEMCTL, "stop", "--", BUSINESS_SERVICE] in command_argv
    assert all(call[1].get("shell") is None for call in runner.calls)


def test_service_control_and_status_use_separate_bounded_timeouts(
    tmp_path: Path,
) -> None:
    primitives, runner, _runtime, _updater, _database = _business_primitives(
        tmp_path
    )
    primitives.service_control_timeout_seconds = 190
    update_uid = str(uuid.uuid4())

    primitives.start({"updateUid": update_uid})

    start_call = next(
        call for call in runner.calls if call[0] == [SYSTEMCTL, "start", "--", BUSINESS_SERVICE]
    )
    status_call = next(
        call for call in runner.calls if call[0][1] == "show"
    )
    assert start_call[1]["timeout"] == 190
    assert status_call[1]["timeout"] == SYSTEMCTL_STATUS_TIMEOUT_SECONDS


def test_invalid_update_identity_is_rejected_before_systemctl(tmp_path: Path) -> None:
    primitives, runner, _runtime, _updater, _database = _business_primitives(
        tmp_path
    )

    with pytest.raises(LocalControlActionError) as raised:
        primitives.stop({"updateUid": "../../caller-path"})

    assert raised.value.code == "REQUEST_INVALID"
    assert runner.calls == []


def test_failed_service_state_is_never_accepted_as_safely_inactive(
    tmp_path: Path,
) -> None:
    failed_runner = FakeServiceRunner(state="failed", stop_state="failed")
    primitives, _runner, _runtime, _updater, _database = _business_primitives(
        tmp_path,
        runner=failed_runner,
    )
    update_uid = str(uuid.uuid4())

    with pytest.raises(LocalControlActionError) as stop_error:
        primitives.stop({"updateUid": update_uid})
    assert stop_error.value.code == "SERVICE_CONTROL_FAILED"

    with pytest.raises(LocalControlActionError) as snapshot_error:
        primitives.snapshot_database({"updateUid": update_uid})
    assert snapshot_error.value.code == "BUSINESS_RUNTIME_ACTIVE"


def test_sqlite_snapshot_is_consistent_and_restore_replaces_only_business_db(
    tmp_path: Path,
) -> None:
    primitives, _runner, _runtime, updater, database = _business_primitives(
        tmp_path
    )
    update_uid = str(uuid.uuid4())

    created = primitives.snapshot_database({"updateUid": update_uid})
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE fact SET value = 'after'")
        connection.commit()
    finally:
        connection.close()
    restored = primitives.restore_database({"updateUid": update_uid})

    snapshot = tmp_path / "privileged" / "business-snapshots" / update_uid / "edge.db"
    assert created["disposition"] == "CREATED"
    assert restored["disposition"] == "RESTORED"
    assert snapshot.is_file()
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    assert _read_database_value(database) == "before"
    assert primitives.snapshot_database({"updateUid": update_uid})[
        "disposition"
    ] == "ALREADY_CREATED"


def test_release_install_copies_plain_tree_without_executing_package_code(
    tmp_path: Path,
) -> None:
    primitives, runner, runtime, updater, _database = _business_primitives(tmp_path)
    update_uid = str(uuid.uuid4())
    release_uid = str(uuid.uuid4())
    source = _stage_release(
        updater,
        update_uid,
        release_uid,
        content=b"raise AssertionError('package code must never run as root')\n",
    )

    result = primitives.install_release(
        {"updateUid": update_uid, "releaseUid": release_uid}
    )

    destination = runtime / "releases" / release_uid
    assert result["disposition"] == "INSTALLED"
    assert result["fileCount"] == 1
    assert (destination / "app" / "main.py").read_bytes() == (
        source / "app" / "main.py"
    ).read_bytes()
    marker = destination / ".ecobin-release.json"
    assert marker.is_file()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o640
    assert all(call[0][0] == SYSTEMCTL for call in runner.calls)
    assert primitives.install_release(
        {"updateUid": update_uid, "releaseUid": release_uid}
    )["disposition"] == "ALREADY_INSTALLED"


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo"])
def test_release_install_rejects_links_and_special_files(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    primitives, _runner, runtime, updater, _database = _business_primitives(
        tmp_path
    )
    update_uid = str(uuid.uuid4())
    release_uid = str(uuid.uuid4())
    source = _stage_release(updater, update_uid, release_uid)
    unsafe = source / "unsafe"
    if unsafe_kind == "symlink":
        unsafe.symlink_to("/etc/passwd")
    elif unsafe_kind == "hardlink":
        os.link(source / "app" / "main.py", unsafe)
    else:
        os.mkfifo(unsafe)

    with pytest.raises(LocalControlActionError) as raised:
        primitives.install_release(
            {"updateUid": update_uid, "releaseUid": release_uid}
        )

    assert raised.value.code == "RELEASE_TREE_INVALID"
    assert not (runtime / "releases" / release_uid).exists()


def test_activation_and_rollback_only_switch_valid_installed_uuid_releases(
    tmp_path: Path,
) -> None:
    primitives, _runner, runtime, updater, _database = _business_primitives(
        tmp_path
    )
    first_update = str(uuid.uuid4())
    first_release = str(uuid.uuid4())
    second_update = str(uuid.uuid4())
    second_release = str(uuid.uuid4())
    _stage_release(updater, first_update, first_release, content=b"first")
    _stage_release(updater, second_update, second_release, content=b"second")
    primitives.install_release(
        {"updateUid": first_update, "releaseUid": first_release}
    )
    primitives.install_release(
        {"updateUid": second_update, "releaseUid": second_release}
    )

    primitives.activate_release(
        {"updateUid": first_update, "releaseUid": first_release}
    )
    activated = primitives.activate_release(
        {"updateUid": second_update, "releaseUid": second_release}
    )
    rolled_back = primitives.rollback_release(
        {"updateUid": second_update, "releaseUid": first_release}
    )

    assert activated["previousReleaseUid"] == first_release
    assert rolled_back["replacedReleaseUid"] == second_release
    assert os.readlink(runtime / "current") == f"releases/{first_release}"
    assert os.readlink(runtime / "previous") == f"releases/{second_release}"


def test_cleanup_refuses_symlink_and_never_removes_its_target(tmp_path: Path) -> None:
    primitives, _runner, _runtime, updater, _database = _business_primitives(
        tmp_path
    )
    update_uid = str(uuid.uuid4())
    update_directory = updater / "staging" / update_uid
    update_directory.mkdir()
    protected = tmp_path / "protected"
    protected.write_text("keep", encoding="utf-8")
    (update_directory / "unsafe").symlink_to(protected)

    with pytest.raises(LocalControlActionError) as raised:
        primitives.cleanup_staging({"updateUid": update_uid})

    assert raised.value.code == "RELEASE_TREE_INVALID"
    assert protected.read_text(encoding="utf-8") == "keep"


class FakeMcuRunner:
    def __init__(
        self,
        *,
        business_state: str = "inactive",
        flash_ok: bool = True,
        boot0_safe_readback_ok: bool = True,
    ):
        self.business_state = business_state
        self.flash_ok = flash_ok
        self.boot0_safe_readback_ok = boot0_safe_readback_ok
        self.levels = {BOOT0_WPI: 0, RESET_GATE_WPI: 0}
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.flashed_payload: bytes | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> SimpleNamespace:
        self.calls.append((argv, kwargs))
        if argv[0] == SYSTEMCTL:
            return SimpleNamespace(returncode=0, stdout=f"{self.business_state}\n")
        if argv[0] == GPIO_BINARY:
            if argv[1] == "write":
                self.levels[int(argv[2])] = int(argv[3])
                return SimpleNamespace(returncode=0, stdout="")
            if argv[1] == "read":
                pin = int(argv[2])
                level = self.levels[pin]
                if (
                    pin == BOOT0_WPI
                    and level == 0
                    and not self.boot0_safe_readback_ok
                ):
                    level = 1
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"{level}\n",
                )
            if argv[1] == "mode":
                return SimpleNamespace(returncode=0, stdout="")
        if argv[0] == STM32FLASH_BINARY:
            firmware_path = argv[argv.index("-w") + 1]
            with open(firmware_path, "rb") as firmware:
                self.flashed_payload = firmware.read()
            return SimpleNamespace(
                returncode=0 if self.flash_ok else 1,
                stdout="verified" if self.flash_ok else "failed",
            )
        raise AssertionError(f"unexpected command: {argv!r}")


def _mcu_primitives(
    tmp_path: Path,
    *,
    runner: FakeMcuRunner | None = None,
    source: str = "TARGET",
) -> tuple[McuFlashPrimitives, FakeMcuRunner, str, Path]:
    update_uid = str(uuid.uuid4())
    firmware_root = tmp_path / "mcu-firmware"
    update_directory = firmware_root / update_uid
    update_directory.mkdir(parents=True)
    image = update_directory / (
        "target.bin" if source == "TARGET" else "rollback.bin"
    )
    image.write_bytes(b"fixed-firmware-image")
    image.chmod(0o600)
    runner = runner or FakeMcuRunner()
    primitives = McuFlashPrimitives(
        firmware_root=firmware_root,
        updater_uid=os.getuid(),
        recovery_marker_path=tmp_path / "mcu-application-recovery-required",
        command_runner=runner,
        sleeper=lambda _seconds: None,
    )
    return primitives, runner, update_uid, image


def _mcu_flash_payload(update_uid: str, source: str = "TARGET") -> dict[str, str]:
    return {
        "updateUid": update_uid,
        "actionUid": str(uuid.uuid4()),
        "source": source,
    }


@pytest.mark.parametrize("source", ["TARGET", "ROLLBACK"])
def test_mcu_flash_uses_fixed_image_gpio_arguments_and_serial(
    tmp_path: Path,
    source: str,
) -> None:
    primitives, runner, update_uid, image = _mcu_primitives(
        tmp_path,
        source=source,
    )

    payload = _mcu_flash_payload(update_uid, source)
    result = primitives.flash(payload)

    flash_call = next(call for call in runner.calls if call[0][0] == STM32FLASH_BINARY)
    argv, kwargs = flash_call
    assert argv[:8] == [
        STM32FLASH_BINARY,
        "-b",
        "115200",
        "-m",
        "8e1",
        "-f",
        "-S",
        f"0x08000000:{image.stat().st_size}",
    ]
    assert argv[-1] == SERIAL_DEVICE
    assert argv[argv.index("-w") + 1].startswith("/proc/self/fd/")
    assert kwargs["pass_fds"]
    assert kwargs.get("shell") is None
    assert runner.flashed_payload == image.read_bytes()
    assert runner.levels == {BOOT0_WPI: 0, RESET_GATE_WPI: 0}
    assert result["actionUid"] == payload["actionUid"]
    assert result["disposition"] == "FIRMWARE_WRITE_VERIFIED"
    assert result["safeApplicationSelected"] is True


def test_mcu_flash_failure_still_restores_and_verifies_safe_application_pins(
    tmp_path: Path,
) -> None:
    runner = FakeMcuRunner(flash_ok=False)
    primitives, runner, update_uid, _image = _mcu_primitives(
        tmp_path,
        runner=runner,
    )

    with pytest.raises(LocalControlActionError) as raised:
        primitives.flash(_mcu_flash_payload(update_uid))

    assert raised.value.code == "STM32FLASH_FAILED"
    assert runner.levels == {BOOT0_WPI: 0, RESET_GATE_WPI: 0}
    gpio_argv = [call[0] for call in runner.calls if call[0][0] == GPIO_BINARY]
    assert [GPIO_BINARY, "read", str(BOOT0_WPI)] in gpio_argv
    assert [GPIO_BINARY, "read", str(RESET_GATE_WPI)] in gpio_argv


def test_mcu_flash_refuses_active_business_before_gpio_or_serial(tmp_path: Path) -> None:
    runner = FakeMcuRunner(business_state="active")
    primitives, runner, update_uid, _image = _mcu_primitives(
        tmp_path,
        runner=runner,
    )

    with pytest.raises(LocalControlActionError) as raised:
        primitives.flash(_mcu_flash_payload(update_uid))

    assert raised.value.code == "BUSINESS_RUNTIME_ACTIVE"
    assert len(runner.calls) == 1
    assert runner.calls[0][0][0] == SYSTEMCTL


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink"])
def test_mcu_flash_rejects_linked_firmware_before_gpio(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    primitives, runner, update_uid, image = _mcu_primitives(tmp_path)
    real_image = image.with_name("real.bin")
    image.rename(real_image)
    if unsafe_kind == "symlink":
        image.symlink_to(real_image)
    else:
        os.link(real_image, image)

    with pytest.raises(LocalControlActionError) as raised:
        primitives.flash(_mcu_flash_payload(update_uid))

    assert raised.value.code == "FIRMWARE_IMAGE_INVALID"
    assert all(call[0][0] != GPIO_BINARY for call in runner.calls)


def test_mcu_source_enum_cannot_be_used_as_a_path(tmp_path: Path) -> None:
    primitives, runner, update_uid, _image = _mcu_primitives(tmp_path)

    with pytest.raises(LocalControlActionError) as raised:
        primitives.flash(_mcu_flash_payload(update_uid, "../../etc/passwd"))

    assert raised.value.code == "REQUEST_INVALID"
    assert runner.calls == []


def test_command_timeouts_are_bounded_and_shell_is_never_enabled(tmp_path: Path) -> None:
    primitives, runner, update_uid, _image = _mcu_primitives(tmp_path)

    primitives.flash(_mcu_flash_payload(update_uid))

    for _argv, kwargs in runner.calls:
        assert kwargs["timeout"] <= 120
        assert kwargs["check"] is False
        assert kwargs.get("shell") is None
        assert kwargs["stdin"] is subprocess.DEVNULL


@pytest.mark.parametrize(
    "action_uid",
    [None, str(uuid.uuid1()), "123E4567-E89B-42D3-A456-426614174000"],
)
def test_mcu_flash_requires_canonical_uuid4_action_uid_before_hardware(
    tmp_path: Path,
    action_uid: str | None,
) -> None:
    primitives, runner, update_uid, _image = _mcu_primitives(tmp_path)
    payload: dict[str, Any] = {
        "updateUid": update_uid,
        "source": "TARGET",
    }
    if action_uid is not None:
        payload["actionUid"] = action_uid

    with pytest.raises(LocalControlActionError) as raised:
        primitives.flash(payload)

    assert raised.value.code == "REQUEST_INVALID"
    assert runner.calls == []


def test_fixed_mcu_recovery_selects_application_boot_and_echoes_action_uid(
    tmp_path: Path,
) -> None:
    primitives, runner, update_uid, _image = _mcu_primitives(tmp_path)
    action_uid = str(uuid.uuid4())

    result = primitives.recover_application(
        {"updateUid": update_uid, "actionUid": action_uid}
    )

    gpio_argv = [call[0] for call in runner.calls if call[0][0] == GPIO_BINARY]
    assert gpio_argv == [
        [GPIO_BINARY, "mode", str(BOOT0_WPI), "out"],
        [GPIO_BINARY, "write", str(BOOT0_WPI), "0"],
        [GPIO_BINARY, "read", str(BOOT0_WPI)],
        [GPIO_BINARY, "mode", str(RESET_GATE_WPI), "out"],
        [GPIO_BINARY, "write", str(RESET_GATE_WPI), "1"],
        [GPIO_BINARY, "write", str(RESET_GATE_WPI), "0"],
        [GPIO_BINARY, "read", str(BOOT0_WPI)],
        [GPIO_BINARY, "read", str(RESET_GATE_WPI)],
    ]
    assert all(call[0][0] != STM32FLASH_BINARY for call in runner.calls)
    assert result == {
        "updateUid": update_uid,
        "actionUid": action_uid,
        "disposition": "APPLICATION_BOOT_PATH_SELECTED",
        "safeApplicationSelected": True,
        "gpioReadback": {"boot0Level": 0, "resetGateLevel": 0},
    }
    assert not (tmp_path / "mcu-application-recovery-required").exists()


def test_mcu_recovery_never_asserts_reset_until_safe_boot0_is_proven(
    tmp_path: Path,
) -> None:
    runner = FakeMcuRunner(boot0_safe_readback_ok=False)
    primitives, runner, update_uid, _image = _mcu_primitives(
        tmp_path,
        runner=runner,
    )

    with pytest.raises(LocalControlActionError) as raised:
        primitives.recover_application(
            {"updateUid": update_uid, "actionUid": str(uuid.uuid4())}
        )

    assert raised.value.code == "MCU_SAFE_RECOVERY_FAILED"
    gpio_argv = [call[0] for call in runner.calls if call[0][0] == GPIO_BINARY]
    assert [GPIO_BINARY, "write", str(RESET_GATE_WPI), "1"] not in gpio_argv
    assert (tmp_path / "mcu-application-recovery-required").exists()


def test_systemd_recovery_guard_is_inert_when_no_mutation_was_started(
    tmp_path: Path,
) -> None:
    runner = FakeMcuRunner()
    recovery = McuApplicationRecovery(
        marker_path=tmp_path / "mcu-application-recovery-required",
        command_runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert recovery.is_armed() is False
    assert runner.calls == []


@pytest.mark.skipif(
    getattr(os, "getuid", lambda: -1)() != 0,
    reason="deployed lock file is root-owned",
)
def test_both_helper_endpoints_share_one_nonblocking_root_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "privileged"
    runtime.mkdir(mode=0o750)
    lock_path = runtime / "mutation.lock"
    monkeypatch.setattr(privileged_control, "MUTATION_LOCK_PATH", lock_path)

    with privileged_control._exclusive_mutation_lock():
        with pytest.raises(LocalControlActionError) as raised:
            with privileged_control._exclusive_mutation_lock():
                pass

    assert raised.value.code == "HELPER_BUSY"
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert lock_path.stat().st_uid == 0
