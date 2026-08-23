from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol

from .cellular_config import CellularConfigurationError, load_cellular_config
from .cellular_probe import CellularProbe, SysfsUsbNetworkInventory
from .command import CommandRunner
from .model import FactoryTestStatus, FirstBootFacts
from factory.acceptance_config import (
    AcceptanceConfiguration,
    AcceptanceConfigurationError,
    parse_environment_file,
)
from factory.acceptance_hardware import identities_equal
from factory_seal.validation import (
    FactorySealPaths,
    inspect_sealed_authorization,
    valid_passed_factory_report,
)


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MACHINE_ID = re.compile(r"^[0-9a-f]{32}$")
_BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class FactsProvider(Protocol):
    def collect(self) -> FirstBootFacts: ...


@dataclass(frozen=True)
class FirstBootPaths:
    image_release: Path = Path("/etc/ecobin/image-release.json")
    hardware_config: Path = Path("/etc/ecobin/hardware.env")
    machine_id: Path = Path("/etc/machine-id")
    factory_state: Path = Path("/var/lib/ecobin/factory-test/state.json")
    factory_report: Path = Path("/var/lib/ecobin/factory-test/report.json")
    credentials: Path = Path("/etc/ecobin/device-credentials.json")
    handoff_fact: Path = Path("/var/lib/ecobin/first-boot/handoff-safe.json")
    cellular_config: Path = Path("/etc/ecobin/cellular.env")
    sealed: Path = Path("/var/lib/ecobin/first-boot/sealed.json")
    setup_ap_key: Path = Path("/etc/ecobin/setup-ap.key")
    edge_store: Path = Path("/var/lib/ecobin/hardware/edge.db")
    gpio_safe_fact: Path = Path(
        "/run/ecobin/mcu-safe-gpio/boot-safe.json"
    )
    boot_id: Path = Path("/proc/sys/kernel/random/boot_id")


class SystemFactsProvider:
    def __init__(
        self,
        paths: FirstBootPaths = FirstBootPaths(),
        *,
        runner: CommandRunner | None = None,
        inventory: SysfsUsbNetworkInventory | None = None,
    ) -> None:
        self._paths = paths
        self._runner = runner or CommandRunner()
        self._inventory = inventory or SysfsUsbNetworkInventory()

    def collect(self) -> FirstBootFacts:
        release = _read_json(self._paths.image_release)
        release_id = (
            release.get("releaseId", release.get("imageReleaseId"))
            if isinstance(release, dict)
            else None
        )
        release_valid = isinstance(release_id, str) and 1 <= len(release_id) <= 128
        hardware_config_digest: str | None = None
        hardware_config_text = _read_text(self._paths.hardware_config, 64 * 1024)
        if hardware_config_text is not None:
            try:
                hardware_config_digest = AcceptanceConfiguration.from_mapping(
                    parse_environment_file(hardware_config_text)
                ).digest()
            except AcceptanceConfigurationError:
                hardware_config_digest = None
        machine_id = _read_text(self._paths.machine_id, 128)
        machine_id_valid = machine_id is not None and bool(
            _MACHINE_ID.fullmatch(machine_id.strip())
        )
        gpio_ready = _valid_current_boot_gpio_fact(
            self._paths.gpio_safe_fact,
            self._paths.boot_id,
        )
        rootfs_expanded = self._unit_active("ecobin-expand-rootfs.service")
        system_prepared = (
            release_valid
            and hardware_config_digest is not None
            and machine_id_valid
            and gpio_ready
            and rootfs_expanded
        )

        portal_ready = all(
            self._unit_active(unit)
            for unit in (
                "ecobin-factory-egress-lock.service",
                "ecobin-factory-ap.service",
                "ecobin-factory-portal.service",
            )
        )
        factory_status, report_valid, recovery, factory_error = self._factory_facts(
            release_id if isinstance(release_id, str) else "",
            hardware_config_digest,
        )

        seal_fact = inspect_sealed_authorization(
            FactorySealPaths(
                edge_store=self._paths.edge_store,
                sealed=self._paths.sealed,
            )
        )
        sealed_exists = seal_fact.exists
        sealed_valid = seal_fact.valid

        cellular_ready = False
        cellular_active = False
        cellular_error = "NONE"
        factory_passed = (
            factory_status is FactoryTestStatus.PASSED
            and report_valid
            and not recovery
        )
        # Before the isolated P7 report is valid, do not even attempt DNS or
        # HTTPS probes.  The offline nft lock remains the only network fact.
        if factory_passed and (not sealed_exists or sealed_valid):
            try:
                cellular_config = load_cellular_config(self._paths.cellular_config)
                health = CellularProbe(
                    cellular_config,
                    self._inventory,
                    runner=self._runner,
                ).probe()
                cellular_ready = health.ready
                cellular_error = health.error_code
                if health.interface is not None:
                    active = self._runner.run(
                        (
                            "/usr/bin/nmcli",
                            "-g",
                            "GENERAL.CONNECTION",
                            "device",
                            "show",
                            health.interface,
                        ),
                        timeout_seconds=5,
                    )
                    cellular_active = (
                        active.return_code == 0
                        and active.stdout.strip() == cellular_config.connection_id
                    )
            except CellularConfigurationError as error:
                cellular_error = error.code

        time_trusted = self._time_trusted()
        enrollment_complete = self._credentials_valid()
        handoff_document = _read_json(self._paths.handoff_fact)
        current_report = _read_json(self._paths.factory_report)
        handoff_safe = bool(
            enrollment_complete
            and isinstance(handoff_document, dict)
            and set(handoff_document)
            == {
                "schemaVersion",
                "status",
                "imageReleaseId",
                "hardwareConfigDigest",
                "mcuIdentity",
            }
            and handoff_document.get("schemaVersion") == 1
            and handoff_document.get("imageReleaseId") == release_id
            and handoff_document.get("status") == "HANDOFF_SAFE"
            and handoff_document.get("hardwareConfigDigest")
            == hardware_config_digest
            and isinstance(current_report, dict)
            and isinstance(current_report.get("mcuIdentity"), dict)
            and isinstance(handoff_document.get("mcuIdentity"), dict)
            and identities_equal(
                current_report["mcuIdentity"],
                handoff_document["mcuIdentity"],
            )
        )
        sealed_cleanup = bool(
            sealed_valid
            and seal_fact.authorization_state == "SEALED"
            and not _path_entry_exists(self._paths.setup_ap_key)
            and self._unit_inactive("ecobin-factory-ap.service")
            and self._unit_inactive("ecobin-factory-portal.service")
        )

        error_code = (
            seal_fact.status_code
            if sealed_exists and not sealed_valid
            else factory_error
        )
        if error_code == "NONE" and factory_passed and not cellular_ready:
            error_code = cellular_error
        return FirstBootFacts(
            system_prepared=system_prepared,
            factory_portal_ready=portal_ready,
            factory_test_status=factory_status,
            factory_report_valid=report_valid,
            factory_recovery_required=recovery,
            cellular_profile_active=cellular_active,
            uplink_ready=cellular_ready and cellular_active,
            time_trusted=time_trusted,
            enrollment_complete=enrollment_complete,
            handoff_safe=handoff_safe,
            sealed_exists=sealed_exists,
            sealed_valid=sealed_valid,
            sealed_cleanup_complete=sealed_cleanup,
            last_error_code=error_code,
        )

    def _factory_facts(
        self,
        release_id: str,
        hardware_config_digest: str | None,
    ) -> tuple[FactoryTestStatus, bool, bool, str]:
        state = _read_json(self._paths.factory_state)
        report = _read_json(self._paths.factory_report)
        if _path_entry_exists(self._paths.factory_state) and state is None:
            return FactoryTestStatus.RECOVERY_REQUIRED, False, True, "FACTORY_STATE_INVALID"
        if isinstance(state, dict) and (
            state.get("schemaVersion") != 1
            or state.get("status") not in {
                "NOT_RUN",
                "RUNNING",
                "PASSED",
                "FAILED",
                "RECOVERY_REQUIRED",
            }
        ):
            return FactoryTestStatus.RECOVERY_REQUIRED, False, True, "FACTORY_STATE_INVALID"
        if isinstance(state, dict) and (
            state.get("status") == "RECOVERY_REQUIRED"
            or isinstance(state.get("recovery"), dict)
        ):
            return FactoryTestStatus.RECOVERY_REQUIRED, False, True, "FACTORY_RECOVERY_REQUIRED"
        if isinstance(state, dict) and state.get("status") == "RUNNING":
            return FactoryTestStatus.RUNNING, False, False, "NONE"
        if report is None and _path_entry_exists(self._paths.factory_report):
            return FactoryTestStatus.FAILED, False, False, "FACTORY_REPORT_INVALID"
        if report is None:
            return FactoryTestStatus.NOT_RUN, False, False, "NONE"
        if not isinstance(report, dict):
            return FactoryTestStatus.FAILED, False, False, "FACTORY_REPORT_INVALID"
        status_text = report.get("status")
        try:
            status = FactoryTestStatus(status_text)
        except (TypeError, ValueError):
            return FactoryTestStatus.FAILED, False, False, "FACTORY_REPORT_INVALID"
        recovery = (
            report.get("recoveryRequired") is True
            or status is FactoryTestStatus.RECOVERY_REQUIRED
        )
        if recovery:
            return FactoryTestStatus.RECOVERY_REQUIRED, False, True, "FACTORY_RECOVERY_REQUIRED"
        valid_pass = bool(
            status is FactoryTestStatus.PASSED
            and _valid_passed_factory_report(
                report,
                release_id=release_id,
                hardware_config_digest=hardware_config_digest,
            )
        )
        if status is FactoryTestStatus.PASSED and not valid_pass:
            return FactoryTestStatus.FAILED, False, False, "FACTORY_REPORT_INVALID"
        return status, valid_pass, False, "NONE" if status is not FactoryTestStatus.FAILED else "FACTORY_TEST_FAILED"

    def _unit_active(self, unit: str) -> bool:
        result = self._runner.run(
            ("/usr/bin/systemctl", "is-active", "--quiet", unit),
            timeout_seconds=5,
        )
        return result.return_code == 0

    def _unit_inactive(self, unit: str) -> bool:
        result = self._runner.run(
            (
                "/usr/bin/systemctl",
                "show",
                "--property=ActiveState",
                "--value",
                unit,
            ),
            timeout_seconds=5,
        )
        return result.return_code == 0 and result.stdout.strip() == "inactive"

    def _time_trusted(self) -> bool:
        result = self._runner.run(
            (
                "/usr/bin/timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--value",
            ),
            timeout_seconds=5,
        )
        return result.return_code == 0 and result.stdout.strip().lower() == "yes"

    def _credentials_valid(self) -> bool:
        try:
            from device_credentials import load_device_credentials

            return load_device_credentials(self._paths.credentials, required=True) is not None
        except (ImportError, OSError, ValueError):
            return False


def _valid_passed_factory_report(
    report: dict[str, Any],
    *,
    release_id: str,
    hardware_config_digest: str | None,
) -> bool:
    return valid_passed_factory_report(
        report,
        release_id=release_id,
        hardware_config_digest=hardware_config_digest,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    text = _read_text(path, 64 * 1024)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _read_text(path: Path, maximum_bytes: int) -> str | None:
    try:
        info = path.lstat()
        if not info.st_size or info.st_size > maximum_bytes or path.is_symlink():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except OSError:
        return False


def _valid_current_boot_gpio_fact(
    fact_path: Path,
    boot_id_path: Path,
    *,
    expected_uid: int = 0,
    expected_mode: int = 0o600,
) -> bool:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            fact_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or getattr(info, "st_uid", 0) != expected_uid
            or stat.S_IMODE(info.st_mode) != expected_mode
            or info.st_size <= 0
            or info.st_size > 4096
        ):
            return False
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                return False
            chunks.append(chunk)
            remaining -= len(chunk)
        document = json.loads(b"".join(chunks).decode("utf-8"))
        boot_id = boot_id_path.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return bool(
        _BOOT_ID.fullmatch(boot_id)
        and isinstance(document, dict)
        and set(document)
        == {"schemaVersion", "status", "bootId", "boot0", "resetGate"}
        and document.get("schemaVersion") == 1
        and document.get("status") == "SAFE_APPLICATION"
        and document.get("bootId") == boot_id
        and document.get("boot0") == {"wpi": 2, "level": 0}
        and document.get("resetGate") == {"wpi": 5, "level": 0}
    )
