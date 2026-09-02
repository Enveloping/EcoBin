#!/usr/bin/env python3
"""Remove per-device state from a copied Orange Pi candidate root filesystem.

This helper never discovers a root filesystem on its own.  The caller must pass
the same non-root directory twice; the privileged loop/mount boundary lives in
``sanitize-candidate.sh``.  Keeping filesystem mutation here makes the policy
testable without attaching a real image.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import stat
import tempfile
import re


BLOCKED_ENABLED_UNITS = {
    "orangepi-resize-filesystem.service",
    "ecobin-business-activation-helper.socket",
    "ecobin-business-permission-preflight.service",
    "ecobin-device-management-preflight.service",
    "ecobin-cellular-uplink.service",
    "ecobin-communication.service",
    "ecobin-enrollment.service",
    "ecobin-factory-ap.service",
    "ecobin-factory-portal.service",
    "ecobin-factory-test.service",
    "ecobin-factory-handoff.service",
    "ecobin-hardware.service",
    "ecobin-mcu-flash-helper.socket",
    "ecobin-remote-support.service",
    "ecobin-runtime.target",
    "ecobin-updater.service",
    "orangepi-zram-config.service",
    "zramswap.service",
    "systemd-zram-setup@zram0.service",
}

BLOCKED_ENABLED_UNIT_PREFIXES = (
    "ecobin-business-activation-helper@",
    "ecobin-mcu-flash-helper@",
)

REMOVE_EXACT_PATHS = (
    "etc/ecobin/device-credentials.json",
    "etc/ecobin/remote-support-credentials.json",
    "var/lib/ecobin/enrollment-state.json",
    "var/lib/ecobin/device-capabilities.json",
    "var/lib/ecobin/first-boot/state.json",
    "var/lib/ecobin/first-boot/sealed.json",
    "var/lib/ecobin/remote-support/state.db",
    "var/lib/ecobin/hardware/edge.db",
    "var/lib/ecobin/communication/communication.db",
    "var/lib/ecobin/business/edge.db",
    "var/lib/ecobin/updater/updater.db",
    "var/lib/dbus/machine-id",
    "var/lib/systemd/random-seed",
    "var/lib/systemd/timesync/clock",
    "var/lib/private/systemd-timesync/clock",
    "var/lib/urandom/random-seed",
    "var/lib/NetworkManager/secret_key",
    "var/lib/NetworkManager/seen-bssids",
    "var/lib/NetworkManager/timestamps",
    "etc/udev/rules.d/70-persistent-net.rules",
    "root/.ssh",
    "home/orangepi/.ssh",
    "root/.bash_history",
    "root/.lesshst",
    "root/.python_history",
    "root/.wget-hsts",
    "home/orangepi/.bash_history",
    "home/orangepi/.lesshst",
    "home/orangepi/.python_history",
    "home/orangepi/.wget-hsts",
    "root/EcoBin/hardware/.env",
    "home/orangepi/EcoBin/hardware/.env",
    "swapfile",
    "etc/dphys-swapfile",
    "etc/systemd/swap.conf",
    "etc/systemd/swap.conf.d",
    "var/lib/dphys-swapfile",
    "etc/systemd/zram-generator.conf",
    "etc/systemd/zram-generator.conf.d",
    "usr/lib/systemd/zram-generator.conf",
    "usr/lib/systemd/zram-generator.conf.d",
    "etc/default/zramswap",
)

CLEAR_DIRECTORY_CONTENTS = (
    "etc/NetworkManager/system-connections",
    "var/lib/chrony",
    "var/lib/NetworkManager",
    "var/lib/dhcp",
    "var/lib/dhcpcd5",
    "var/lib/cloud",
    "var/lib/wpa_supplicant",
    "var/lib/ecobin/hardware",
    "var/lib/ecobin/remote-support",
    "var/lib/ecobin/first-boot",
    "var/lib/ecobin/factory-test",
    "var/lib/ecobin/communication",
    "var/lib/ecobin/business",
    "var/lib/ecobin/updater",
    "root/EcoBin/hardware/data",
    "var/log",
    "var/cache/apt/archives",
    "var/lib/apt/lists",
    "tmp",
    "var/tmp",
)

REMOVE_GLOBS = (
    "etc/ssh/ssh_host_*",
)

SYSTEMD_UNIT_ROOTS = (
    "etc/systemd/system",
    "usr/lib/systemd/system",
    "lib/systemd/system",
)
DISPLAY_MANAGER_CONFIG_FILES = (
    "etc/lightdm/lightdm.conf",
    "etc/gdm3/custom.conf",
    "etc/gdm3/daemon.conf",
    "etc/gdm/custom.conf",
    "etc/sddm.conf",
    "etc/lxdm/lxdm.conf",
    "etc/nodm.conf",
    "etc/default/nodm",
    "etc/sysconfig/displaymanager",
)
DISPLAY_MANAGER_CONFIG_DIRECTORIES = (
    "etc/lightdm/lightdm.conf.d",
    "etc/sddm.conf.d",
)
GETTY_UNIT_NAME = re.compile(r"^(?:serial-)?getty@[^/]*\.service$")
GETTY_DROPIN_NAME = re.compile(r"^(?:serial-)?getty@[^/]*\.service\.d$")
LONG_AUTOLOGIN = re.compile(r"(?<![A-Za-z0-9_-])--autologin(?=[=\s]|$)", re.IGNORECASE)
SHORT_AGETTY_AUTOLOGIN = re.compile(r"(?<!\S)-a(?=\s|$)")


class SanitizationError(RuntimeError):
    """The candidate root does not satisfy a safe mutation precondition."""


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--confirm-root", required=True)
    parser.add_argument("--audit-local-login-only", action="store_true")
    return parser.parse_args()


class RootfsSanitizer:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    def path(self, relative: str) -> pathlib.Path:
        pure = pathlib.PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise SanitizationError(f"unsafe relative policy path: {relative}")
        candidate = self.root.joinpath(*pure.parts)
        self._require_safe_parents(candidate)
        return candidate

    def _require_safe_parents(self, candidate: pathlib.Path) -> None:
        relative = candidate.relative_to(self.root)
        current = self.root
        for part in relative.parts[:-1]:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                return
            if stat.S_ISLNK(mode):
                raise SanitizationError(
                    f"policy path crosses a symbolic link: /{current.relative_to(self.root)}"
                )
            if not stat.S_ISDIR(mode):
                raise SanitizationError(
                    f"policy parent is not a directory: /{current.relative_to(self.root)}"
                )

    def remove(self, relative: str) -> None:
        target = self.path(relative)
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
            shutil.rmtree(target)
        else:
            target.unlink()

    def clear_directory(self, relative: str) -> None:
        target = self.path(relative)
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise SanitizationError(f"cleanup directory is unsafe: /{relative}")
        for child in target.iterdir():
            child_relative = child.relative_to(self.root).as_posix()
            self.remove(child_relative)

    def remove_glob(self, pattern: str) -> None:
        parent_name, name_pattern = pattern.rsplit("/", 1)
        parent = self.path(parent_name)
        try:
            mode = parent.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise SanitizationError(f"glob parent is unsafe: /{parent_name}")
        for target in parent.glob(name_pattern):
            self.remove(target.relative_to(self.root).as_posix())

    def remove_named_entries(self, entry_name: str) -> None:
        if not entry_name or "/" in entry_name or entry_name in (".", ".."):
            raise SanitizationError("unsafe recursive cleanup entry name")
        for current_name, directory_names, file_names in os.walk(
            self.root, topdown=True, followlinks=False
        ):
            current = pathlib.Path(current_name)
            retained_directories: list[str] = []
            for directory_name in directory_names:
                candidate = current / directory_name
                if directory_name == entry_name:
                    self.remove(candidate.relative_to(self.root).as_posix())
                    continue
                if candidate.is_symlink():
                    continue
                retained_directories.append(directory_name)
            directory_names[:] = retained_directories
            for file_name in file_names:
                if file_name == entry_name:
                    candidate = current / file_name
                    self.remove(candidate.relative_to(self.root).as_posix())

    def _read_regular_configuration(self, path: pathlib.Path) -> str:
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_size > 1024 * 1024
        ):
            relative = path.relative_to(self.root).as_posix()
            raise SanitizationError(
                "local-login configuration must be a bounded regular "
                f"single-link file: /{relative}"
            )
        return path.read_text(encoding="utf-8")

    def _systemd_unit_directories(self) -> list[pathlib.Path]:
        directories: list[pathlib.Path] = []
        for relative in SYSTEMD_UNIT_ROOTS:
            if relative == "lib/systemd/system":
                lib = self.root / "lib"
                if lib.is_symlink():
                    target = os.readlink(lib)
                    if target not in ("usr/lib", "/usr/lib"):
                        raise SanitizationError("candidate /lib link is outside the usr-merge policy")
                    continue
            directory = self.path(relative)
            try:
                details = directory.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SanitizationError(
                    f"systemd unit policy directory is unsafe: /{relative}"
                )
            directories.append(directory)
        return directories

    def _getty_configuration_files(self) -> list[pathlib.Path]:
        files: list[pathlib.Path] = []
        seen: set[pathlib.Path] = set()
        for directory in self._systemd_unit_directories():
            for entry in directory.iterdir():
                if GETTY_UNIT_NAME.fullmatch(entry.name):
                    details = entry.lstat()
                    if stat.S_ISLNK(details.st_mode):
                        if os.readlink(entry) == "/dev/null":
                            continue
                        raise SanitizationError(
                            "getty unit symlink is not an exact systemd mask"
                        )
                    if entry not in seen:
                        files.append(entry)
                        seen.add(entry)
                    continue
                if not GETTY_DROPIN_NAME.fullmatch(entry.name):
                    continue
                details = entry.lstat()
                if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                    raise SanitizationError("getty drop-in path is not a regular directory")
                for dropin in entry.iterdir():
                    if dropin.suffix == ".conf" and dropin not in seen:
                        files.append(dropin)
                        seen.add(dropin)
        return files

    def _display_manager_configuration_files(self) -> list[pathlib.Path]:
        files: list[pathlib.Path] = []
        for relative in DISPLAY_MANAGER_CONFIG_FILES:
            candidate = self.path(relative)
            if os.path.lexists(candidate):
                files.append(candidate)
        for relative in DISPLAY_MANAGER_CONFIG_DIRECTORIES:
            directory = self.path(relative)
            try:
                details = directory.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SanitizationError("display-manager configuration directory is unsafe")
            files.extend(
                entry for entry in directory.iterdir() if entry.suffix == ".conf"
            )
        return files

    @staticmethod
    def _systemd_enables_autologin(content: str) -> bool:
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if LONG_AUTOLOGIN.search(line):
                return True
            if re.search(r"(?<![A-Za-z0-9_])agetty(?=[\s;]|$)", line) \
                    and SHORT_AGETTY_AUTOLOGIN.search(line):
                return True
        return False

    @staticmethod
    def _display_manager_enables_autologin(content: str) -> bool:
        section = ""
        true_values = {"1", "true", "yes", "on"}
        identity_keys = {
            "autologin",
            "autologinuser",
            "automaticlogin",
            "displaymanagerautologin",
            "nodmuser",
        }
        enable_keys = {"automaticloginenable", "nodmenabled"}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = re.sub(r"[-_]", "", key.strip().lower())
            normalized_value = value.strip().strip("\"'")
            if normalized_key in enable_keys and normalized_value.lower() in true_values:
                return True
            if normalized_key in identity_keys and normalized_value:
                return True
            if section == "autologin" and normalized_key == "user" and normalized_value:
                return True
        return False

    def remove_getty_autologin_dropins(self) -> None:
        for configuration in self._getty_configuration_files():
            content = self._read_regular_configuration(configuration)
            if not self._systemd_enables_autologin(content):
                continue
            if not GETTY_DROPIN_NAME.fullmatch(configuration.parent.name):
                raise SanitizationError(
                    "base getty unit contains automatic login and cannot be safely rewritten"
                )
            configuration.unlink()

    def audit_no_automatic_local_login(self) -> None:
        for configuration in self._getty_configuration_files():
            content = self._read_regular_configuration(configuration)
            if self._systemd_enables_autologin(content):
                raise SanitizationError(
                    "getty or serial-getty configuration enables automatic login"
                )
        for configuration in self._display_manager_configuration_files():
            content = self._read_regular_configuration(configuration)
            if self._display_manager_enables_autologin(content):
                raise SanitizationError(
                    "display-manager configuration enables automatic login"
                )

    def truncate_machine_id(self) -> None:
        machine_id = self.path("etc/machine-id")
        try:
            machine_id_stat = machine_id.lstat()
        except FileNotFoundError:
            machine_id_mode = 0o444
        else:
            if stat.S_ISLNK(machine_id_stat.st_mode) or not stat.S_ISREG(
                machine_id_stat.st_mode
            ):
                raise SanitizationError("/etc/machine-id must be a regular file")
            machine_id_mode = stat.S_IMODE(machine_id_stat.st_mode)
        machine_id.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            machine_id,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            machine_id_mode,
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, machine_id_mode)
            else:
                os.chmod(machine_id, machine_id_mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def lock_default_passwords(self) -> None:
        shadow = self.path("etc/shadow")
        try:
            shadow_stat = shadow.lstat()
        except FileNotFoundError as error:
            raise SanitizationError("candidate is missing /etc/shadow") from error
        if stat.S_ISLNK(shadow_stat.st_mode) or not stat.S_ISREG(shadow_stat.st_mode):
            raise SanitizationError("candidate /etc/shadow is not a regular file")

        lines = shadow.read_text(encoding="utf-8").splitlines()
        required = {"root", "orangepi"}
        found: set[str] = set()
        rewritten: list[str] = []
        for line in lines:
            fields = line.split(":")
            if len(fields) < 2:
                raise SanitizationError("candidate /etc/shadow has a malformed entry")
            account = fields[0]
            if account in required:
                if account in found:
                    raise SanitizationError("candidate /etc/shadow has a duplicate required account")
                found.add(account)
                fields[1] = "!"
            rewritten.append(":".join(fields))
        if found != required:
            raise SanitizationError("candidate is missing a required local account")

        payload = ("\n".join(rewritten) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".shadow.ecobin.", dir=shadow.parent)
        temporary = pathlib.Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, stat.S_IMODE(shadow_stat.st_mode))
            else:
                os.chmod(temporary, stat.S_IMODE(shadow_stat.st_mode))
            if hasattr(os, "fchown"):
                os.fchown(descriptor, shadow_stat.st_uid, shadow_stat.st_gid)
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, shadow)
            if hasattr(os, "O_DIRECTORY"):
                directory_descriptor = os.open(
                    shadow.parent, os.O_RDONLY | os.O_DIRECTORY
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            if temporary.exists():
                temporary.unlink()

    def remove_blocked_enable_links(self) -> None:
        systemd_root = self.path("etc/systemd/system")
        if not systemd_root.exists():
            return
        if systemd_root.is_symlink() or not systemd_root.is_dir():
            raise SanitizationError("/etc/systemd/system is unsafe")

        for current_name, directory_names, file_names in os.walk(
            systemd_root, topdown=True, followlinks=False
        ):
            current = pathlib.Path(current_name)
            symlink_directories = [
                name for name in directory_names if (current / name).is_symlink()
            ]
            if symlink_directories:
                raise SanitizationError(
                    "/etc/systemd/system contains a symbolic-link directory"
                )
            if not current.name.endswith((".wants", ".requires", ".upholds")):
                continue
            for entry_name in file_names:
                entry = current / entry_name
                mode = entry.lstat().st_mode
                target_name = ""
                if stat.S_ISLNK(mode):
                    target_name = pathlib.PurePosixPath(os.readlink(entry)).name
                names = (entry_name, target_name)
                simulator = any(
                    token in name.lower()
                    for name in names
                    for token in ("simulat", "mock", "fake")
                )
                persistent_swap = any(
                    name.endswith(".swap")
                    or name.startswith("dphys-swapfile")
                    or name.startswith("systemd-swap")
                    for name in names if name
                )
                helper_instance = any(
                    name.startswith(prefix) and name.endswith(".service")
                    for name in names
                    for prefix in BLOCKED_ENABLED_UNIT_PREFIXES
                    if name
                )
                if (
                    any(name in BLOCKED_ENABLED_UNITS for name in names)
                    or helper_instance
                    or simulator
                    or persistent_swap
                ):
                    entry.unlink()

    def remove_fstab_swap_entries(self) -> None:
        fstab = self.path("etc/fstab")
        try:
            details = fstab.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or details.st_size > 1024 * 1024:
            raise SanitizationError("candidate /etc/fstab must be a bounded regular single-link file")
        original = fstab.read_text(encoding="utf-8")
        kept = []
        for raw_line in original.splitlines():
            logical = raw_line.split("#", 1)[0].strip()
            fields = logical.split()
            if len(fields) >= 3 and fields[2].lower() == "swap":
                continue
            kept.append(raw_line)
        payload = ("\n".join(kept) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".fstab.ecobin.", dir=fstab.parent)
        temporary = pathlib.Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, stat.S_IMODE(details.st_mode))
            if hasattr(os, "fchown"):
                os.fchown(descriptor, details.st_uid, details.st_gid)
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = -1
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, fstab)
            if hasattr(os, "O_DIRECTORY"):
                directory_descriptor = os.open(fstab.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    def disable_orangepi_zram(self) -> None:
        target = self.path("etc/default/orangepi-zram-config")
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(target) and target.is_symlink():
            raise SanitizationError("Orange Pi zram configuration is a symbolic link")
        target.write_text("ENABLED=false\nSWAP=false\n", encoding="utf-8", newline="\n")
        os.chmod(target, 0o644)

    def run(self) -> None:
        # Vendor images may carry full Git repositories under /etc and /usr/src.
        # Remove directory, file and symlink forms without following rootfs links.
        self.remove_named_entries(".git")
        for relative in REMOVE_EXACT_PATHS:
            self.remove(relative)
        for pattern in REMOVE_GLOBS:
            self.remove_glob(pattern)
        for relative in CLEAR_DIRECTORY_CONTENTS:
            self.clear_directory(relative)
        self.remove_getty_autologin_dropins()
        self.remove_blocked_enable_links()
        self.remove_fstab_swap_entries()
        self.disable_orangepi_zram()
        self.truncate_machine_id()
        self.lock_default_passwords()
        self.audit_no_automatic_local_login()


def resolve_root(root_argument: str, confirmation_argument: str) -> pathlib.Path:
    supplied = pathlib.Path(root_argument)
    confirmation = pathlib.Path(confirmation_argument)
    if supplied.is_symlink() or confirmation.is_symlink():
        raise SanitizationError("candidate root must not be a symbolic link")
    root = supplied.resolve(strict=True)
    confirmed = confirmation.resolve(strict=True)
    if root != confirmed:
        raise SanitizationError("--root and --confirm-root identify different directories")
    if root == pathlib.Path(root.anchor) or root == pathlib.Path("/"):
        raise SanitizationError("refusing to sanitize a filesystem root")
    if not root.is_dir():
        raise SanitizationError("candidate root is not a directory")
    for required in (root / "etc", root / "var", root / "etc" / "shadow"):
        if not required.exists():
            raise SanitizationError("candidate root is missing required system paths")
    return root


def main() -> int:
    arguments = parse_arguments()
    try:
        root = resolve_root(arguments.root, arguments.confirm_root)
        sanitizer = RootfsSanitizer(root)
        if arguments.audit_local_login_only:
            sanitizer.audit_no_automatic_local_login()
        else:
            sanitizer.run()
    except (OSError, UnicodeError, SanitizationError) as error:
        print(f"candidate-rootfs-sanitization=FAIL: {error}", file=os.sys.stderr)
        return 2
    if arguments.audit_local_login_only:
        print("candidate-local-login-audit=PASS autologin=disabled")
    else:
        print(
            "candidate-rootfs-sanitization=PASS "
            "accounts_locked=root,orangepi autologin=disabled"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
