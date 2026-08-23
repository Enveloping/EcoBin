from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

from first_boot.command import CommandResult
from first_boot.facts import (
    FirstBootPaths,
    SystemFactsProvider,
    _SUPPORTED_FACTORY_STATE_SCHEMA_VERSIONS,
    _valid_current_boot_gpio_fact,
)
from first_boot.model import FactoryTestStatus
from factory.acceptance_config import AcceptanceConfiguration
from factory.acceptance_core import (
    LEGACY_STATE_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
)


class _NoCommands:
    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        return CommandResult(1, "")


class _RecordingCommands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        self.calls.append(tuple(argv))
        return CommandResult(1, "")


class _RootfsExpandedCommands(_RecordingCommands):
    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        self.calls.append(tuple(argv))
        if argv == (
            "/usr/bin/systemctl",
            "is-active",
            "--quiet",
            "ecobin-expand-rootfs.service",
        ):
            return CommandResult(0, "")
        return CommandResult(1, "")


def _paths(tmp_path: Path) -> FirstBootPaths:
    return FirstBootPaths(
        image_release=tmp_path / "image-release.json",
        hardware_config=tmp_path / "hardware.env",
        machine_id=tmp_path / "machine-id",
        factory_state=tmp_path / "factory-state.json",
        factory_report=tmp_path / "factory-report.json",
        credentials=tmp_path / "credentials.json",
        handoff_fact=tmp_path / "handoff.json",
        cellular_config=tmp_path / "cellular.env",
        sealed=tmp_path / "sealed.json",
        setup_ap_key=tmp_path / "setup-ap.key",
        edge_store=tmp_path / "hardware" / "edge.db",
        gpio_safe_fact=tmp_path / "gpio-safe.json",
        boot_id=tmp_path / "boot-id",
    )


def _digest() -> str:
    return AcceptanceConfiguration.from_mapping({}).digest()


def test_safe_gpio_fact_must_match_the_current_kernel_boot(tmp_path: Path) -> None:
    fact = tmp_path / "boot-safe.json"
    boot_id = tmp_path / "boot-id"
    current = "12345678-1234-4234-8234-123456789abc"
    boot_id.write_text(current + "\n", encoding="ascii")
    document = {
        "schemaVersion": 1,
        "status": "SAFE_APPLICATION",
        "bootId": current,
        "boot0": {"wpi": 2, "level": 0},
        "resetGate": {"wpi": 5, "level": 0},
    }
    fact.write_text(json.dumps(document), encoding="utf-8")
    fact.chmod(0o600)
    expected_uid = getattr(os, "getuid", lambda: 0)()
    expected_mode = stat.S_IMODE(fact.stat().st_mode)

    assert _valid_current_boot_gpio_fact(
        fact,
        boot_id,
        expected_uid=expected_uid,
        expected_mode=expected_mode,
    )

    document["bootId"] = "87654321-4321-4321-8321-cba987654321"
    fact.write_text(json.dumps(document), encoding="utf-8")
    fact.chmod(0o600)
    assert not _valid_current_boot_gpio_fact(
        fact,
        boot_id,
        expected_uid=expected_uid,
        expected_mode=expected_mode,
    )


def test_safe_gpio_fact_rejects_wrong_pin_level_and_permissions(
    tmp_path: Path,
) -> None:
    fact = tmp_path / "boot-safe.json"
    boot_id = tmp_path / "boot-id"
    current = "12345678-1234-4234-8234-123456789abc"
    boot_id.write_text(current + "\n", encoding="ascii")
    document = {
        "schemaVersion": 1,
        "status": "SAFE_APPLICATION",
        "bootId": current,
        "boot0": {"wpi": 2, "level": 0},
        "resetGate": {"wpi": 5, "level": 1},
    }
    fact.write_text(json.dumps(document), encoding="utf-8")
    fact.chmod(0o600)
    expected_uid = getattr(os, "getuid", lambda: 0)()
    expected_mode = stat.S_IMODE(fact.stat().st_mode)

    assert not _valid_current_boot_gpio_fact(
        fact,
        boot_id,
        expected_uid=expected_uid,
        expected_mode=expected_mode,
    )

    document["resetGate"] = {"wpi": 5, "level": 0}
    fact.write_text(json.dumps(document), encoding="utf-8")
    fact.chmod(0o644)
    if os.name != "nt":
        assert not _valid_current_boot_gpio_fact(
            fact, boot_id, expected_uid=expected_uid
        )


def test_system_prepared_uses_current_boot_gpio_fact_not_oneshot_active_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    paths.image_release.write_text(
        json.dumps({"releaseId": "release-1"}), encoding="utf-8"
    )
    paths.hardware_config.write_text("# defaults\n", encoding="utf-8")
    paths.machine_id.write_text("a" * 32 + "\n", encoding="ascii")
    runner = _RootfsExpandedCommands()
    monkeypatch.setattr(
        "first_boot.facts._valid_current_boot_gpio_fact",
        lambda _fact, _boot: True,
    )

    facts = SystemFactsProvider(paths, runner=runner).collect()

    assert facts.system_prepared
    assert not any(
        command
        == (
            "/usr/bin/systemctl",
            "is-active",
            "--quiet",
            "ecobin-mcu-safe-gpio.service",
        )
        for command in runner.calls
    )


def _passed_report(release_id: str = "release-1") -> dict[str, object]:
    camera = lambda role, fingerprint: {
        "role": role,
        "sourceKind": "V4L2_BY_ID",
        "sourceFingerprint": fingerprint,
        "captureNonEmpty": True,
        "operatorRoleConfirmed": True,
    }
    return {
        "schemaVersion": 1,
        "status": "PASSED",
        "recoveryRequired": False,
        "imageReleaseId": release_id,
        "hardwareConfigDigest": _digest(),
        "mcuIdentity": {
            "fixedFrameRevision": 2,
            "firmwareVersion": "v1.2.3",
            "firmwareVersionCode": 0x010203,
            "firmwareIdentityHex": "0123456789abcdef",
        },
        "cameraSummary": {
            "status": "PASSED",
            "resultCode": "DUAL_CAMERA_FIXED_ROLES_PASSED",
            "outside": camera("OUTSIDE", "a" * 12),
            "inside": camera("INSIDE", "b" * 12),
        },
        "checks": {
            "mcu": {
                "status": "PASSED",
                "resultCode": "MCU_REVISION_2_AND_F1_HEALTHY",
            },
            "weight": {
                "status": "PASSED",
                "resultCode": "WEIGHT_500G_WITHIN_490_510_AND_REMOVED",
                "emptyWeightGrams": 1000,
                "loadedWeightGrams": 1500,
                "removedWeightGrams": 1000,
                "deltaGrams": 500,
                "targetDeltaGrams": 500,
                "toleranceGrams": 10,
                "stableSampleCount": 3,
                "stableMaxSpreadGrams": 2,
                "sampleIntervalMs": 100,
                "sampleTimeoutMs": 3000,
            },
            "upgradeLine": {
                "status": "PASSED",
                "resultCode": "F2_BOOT0_NRST_ROM_READ_ONLY_AND_APP_RECOVERY_PASSED",
                "romWritePerformed": False,
                "romDeviceId": "0x0410",
            },
            "delivery": {
                "status": "PASSED",
                "resultCode": "DELIVERY_SAFE_VERIFIED",
                "operatorAreaSafeConfirmed": True,
                "preWeightGrams": 1000,
                "postWeightGrams": 1200,
                "weightDeltaGrams": 200,
                "infraredBlocked": False,
            },
            "clean": {
                "status": "PASSED",
                "resultCode": "CLEAN_SAFE_VERIFIED",
                "cleanDoorConfirmed": True,
                "preWeightGrams": 1200,
                "postWeightGrams": 100,
                "weightDeltaGrams": 1100,
                "infraredBlocked": False,
            },
        },
    }


def test_absent_p7_report_is_not_run_and_never_valid(tmp_path: Path) -> None:
    provider = SystemFactsProvider(_paths(tmp_path), runner=_NoCommands())

    status, valid, recovery, error = provider._factory_facts(
        "release-1", _digest()
    )

    assert status is FactoryTestStatus.NOT_RUN
    assert not valid
    assert not recovery
    assert error == "NONE"


def test_valid_passed_report_is_bound_to_current_image(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.factory_report.write_text(
        json.dumps(_passed_report()),
        encoding="utf-8",
    )
    provider = SystemFactsProvider(paths, runner=_NoCommands())

    accepted = provider._factory_facts("release-1", _digest())
    changed_image = provider._factory_facts("release-2", _digest())

    assert accepted == (FactoryTestStatus.PASSED, True, False, "NONE")
    assert changed_image == (
        FactoryTestStatus.FAILED,
        False,
        False,
        "FACTORY_REPORT_INVALID",
    )


def test_current_v2_passed_state_allows_first_boot_to_accept_report(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.image_release.write_text(
        json.dumps({"releaseId": "release-1"}), encoding="utf-8"
    )
    paths.hardware_config.write_text("# defaults\n", encoding="utf-8")
    paths.machine_id.write_text("a" * 32 + "\n", encoding="ascii")
    paths.factory_state.write_text(
        json.dumps(
            {
                "schemaVersion": STATE_SCHEMA_VERSION,
                "status": "PASSED",
            }
        ),
        encoding="utf-8",
    )
    paths.factory_report.write_text(
        json.dumps(_passed_report()),
        encoding="utf-8",
    )

    facts = SystemFactsProvider(paths, runner=_NoCommands()).collect()

    assert STATE_SCHEMA_VERSION == 2
    assert _SUPPORTED_FACTORY_STATE_SCHEMA_VERSIONS == {
        LEGACY_STATE_SCHEMA_VERSION,
        STATE_SCHEMA_VERSION,
    }
    assert facts.factory_test_status is FactoryTestStatus.PASSED
    assert facts.factory_report_valid
    assert not facts.factory_recovery_required
    assert facts.last_error_code != "FACTORY_STATE_INVALID"


def test_unknown_or_non_integer_factory_state_schema_fails_closed(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    for schema_version in (3, True, "2"):
        paths.factory_state.write_text(
            json.dumps(
                {
                    "schemaVersion": schema_version,
                    "status": "PASSED",
                }
            ),
            encoding="utf-8",
        )

        assert SystemFactsProvider(
            paths,
            runner=_NoCommands(),
        )._factory_facts("release-1", _digest()) == (
            FactoryTestStatus.RECOVERY_REQUIRED,
            False,
            True,
            "FACTORY_STATE_INVALID",
        )


def test_passed_report_is_invalid_after_hardware_config_or_mcu_identity_changes(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    report = _passed_report()
    paths.factory_report.write_text(json.dumps(report), encoding="utf-8")
    provider = SystemFactsProvider(paths, runner=_NoCommands())

    changed_config = provider._factory_facts("release-1", "b" * 64)
    report["mcuIdentity"]["fixedFrameRevision"] = 1  # type: ignore[index]
    paths.factory_report.write_text(json.dumps(report), encoding="utf-8")
    changed_mcu = provider._factory_facts("release-1", _digest())

    assert changed_config == (
        FactoryTestStatus.FAILED,
        False,
        False,
        "FACTORY_REPORT_INVALID",
    )
    assert changed_mcu == changed_config


def test_release_id_uses_installed_release_schema_field(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.image_release.write_text(
        json.dumps({"releaseId": "release-1", "version": "1.0.0"}),
        encoding="utf-8",
    )
    paths.hardware_config.write_text("# defaults\n", encoding="utf-8")
    paths.machine_id.write_text("a" * 32 + "\n", encoding="ascii")
    paths.factory_report.write_text(json.dumps(_passed_report()), encoding="utf-8")

    facts = SystemFactsProvider(paths, runner=_NoCommands()).collect()

    assert facts.factory_test_status is FactoryTestStatus.PASSED
    assert facts.factory_report_valid


def test_running_recovery_fact_wins_over_a_stale_pass_report(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.factory_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "RECOVERY_REQUIRED",
                "recovery": {"required": True},
            }
        ),
        encoding="utf-8",
    )
    paths.factory_report.write_text(
        json.dumps(_passed_report()),
        encoding="utf-8",
    )

    result = SystemFactsProvider(paths, runner=_NoCommands())._factory_facts(
        "release-1", _digest()
    )

    assert result == (
        FactoryTestStatus.RECOVERY_REQUIRED,
        False,
        True,
        "FACTORY_RECOVERY_REQUIRED",
    )


def test_corrupt_factory_state_is_recovery_required_not_a_fresh_test(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.factory_state.write_text("{interrupted", encoding="utf-8")

    result = SystemFactsProvider(paths, runner=_NoCommands())._factory_facts(
        "release-1", _digest()
    )

    assert result == (
        FactoryTestStatus.RECOVERY_REQUIRED,
        False,
        True,
        "FACTORY_STATE_INVALID",
    )


def test_corrupt_report_is_invalid_not_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.factory_report.write_text("{interrupted", encoding="utf-8")

    result = SystemFactsProvider(paths, runner=_NoCommands())._factory_facts(
        "release-1", _digest()
    )

    assert result == (
        FactoryTestStatus.FAILED,
        False,
        False,
        "FACTORY_REPORT_INVALID",
    )


def test_no_dns_https_or_nm_probe_runs_before_p7_pass(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.image_release.write_text(
        json.dumps({"imageReleaseId": "release-1"}), encoding="utf-8"
    )
    paths.machine_id.write_text("a" * 32 + "\n", encoding="ascii")
    paths.cellular_config.write_text(
        "\n".join(
            (
                "ECOBIN_CELLULAR_SCHEMA_VERSION=2",
                "ECOBIN_CELLULAR_HIL_APPROVED=true",
                "ECOBIN_CELLULAR_CONNECTION_ID=ecobin-air780e-rndis",
                "ECOBIN_CELLULAR_USB_DRIVER=rndis_host",
                "ECOBIN_CELLULAR_USB_PROFILE=RNDIS",
                "ECOBIN_CELLULAR_AUTO_APN=true",
                "ECOBIN_CELLULAR_PROBE_IPV4=203.0.113.10",
                "ECOBIN_CELLULAR_HTTPS_PROBE_URL=https://probe.example.test/health",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    runner = _RecordingCommands()

    facts = SystemFactsProvider(paths, runner=runner).collect()

    assert facts.factory_test_status is FactoryTestStatus.NOT_RUN
    assert facts.last_error_code == "NONE"
    assert not any(
        command[0] in {"/usr/sbin/ip", "/usr/bin/resolvectl", "/usr/bin/curl", "/usr/bin/nmcli"}
        for command in runner.calls
    )


def test_release_matching_but_unbound_seal_blocks_all_network_probes(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.image_release.write_text(
        json.dumps({"releaseId": "release-1"}), encoding="utf-8"
    )
    paths.hardware_config.write_text("# production defaults\n", encoding="utf-8")
    paths.machine_id.write_text("a" * 32 + "\n", encoding="ascii")
    paths.factory_report.write_text(json.dumps(_passed_report()), encoding="utf-8")
    paths.sealed.parent.mkdir(parents=True, exist_ok=True)
    paths.sealed.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "SEALED",
                "imageReleaseId": "release-1",
            }
        ),
        encoding="utf-8",
    )
    paths.sealed.chmod(0o600)
    paths.cellular_config.write_text(
        "ECOBIN_CELLULAR_SCHEMA_VERSION=2\n",
        encoding="utf-8",
    )
    runner = _RecordingCommands()

    facts = SystemFactsProvider(paths, runner=runner).collect()

    assert facts.sealed_exists
    assert not facts.sealed_valid
    assert facts.last_error_code == "SEALED_FACT_INVALID"
    assert not facts.uplink_ready
    assert not any(
        command[0]
        in {
            "/usr/sbin/ip",
            "/usr/bin/resolvectl",
            "/usr/bin/curl",
            "/usr/bin/nmcli",
        }
        for command in runner.calls
    )


def test_legacy_sealed_row_without_completion_event_remains_pending(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    command_uid = "12345678-1234-4123-8123-123456789abc"
    operator_uid = "abcdef12-3456-4789-8abc-def123456789"
    hardware_sn = "ECOBIN-Z3-000001"
    report_hash = "b" * 64
    binding = "c" * 64
    paths.edge_store.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(paths.edge_store) as database:
        database.execute(
            """CREATE TABLE factory_seal_authorization (
                command_uid TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                acceptance_generation INTEGER NOT NULL,
                authorization_binding_sha256 TEXT NOT NULL,
                image_release_id TEXT NOT NULL,
                hardware_sn TEXT NOT NULL,
                factory_report_sha256 TEXT NOT NULL
            )"""
        )
        database.execute(
            "INSERT INTO factory_seal_authorization VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                command_uid,
                "SEALED",
                5,
                binding,
                "release-1",
                hardware_sn,
                report_hash,
            ),
        )
    paths.sealed.parent.mkdir(parents=True, exist_ok=True)
    paths.sealed.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "SEALED",
                "imageReleaseId": "release-1",
                "hardwareIdentitySha256": hashlib.sha256(
                    hardware_sn.encode("utf-8")
                ).hexdigest(),
                "factoryReportSha256": report_hash,
                "authorizationCommandUid": command_uid,
                "acceptanceGeneration": 5,
                "authorizationBindingSha256": binding,
                "operatorConfirmationUid": operator_uid,
                "sealedAt": "2026-08-22T12:00:00.000Z",
            }
        ),
        encoding="utf-8",
    )
    paths.sealed.chmod(0o600)
    provider = SystemFactsProvider(paths, runner=_NoCommands())

    facts = provider.collect()

    assert facts.sealed_exists
    assert not facts.sealed_valid
    assert facts.last_error_code == "SEALED_CLEANUP_PENDING"
    # A bound marker plus the old SEALED word is no longer production
    # authority; v16 must also prove the atomic completion outbox fact.
    assert not facts.sealed_cleanup_complete


def test_handoff_fact_is_bound_to_current_config_and_p7_mcu_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    report = _passed_report()
    paths.image_release.write_text(
        json.dumps({"releaseId": "release-1"}), encoding="utf-8"
    )
    paths.hardware_config.write_text("# defaults\n", encoding="utf-8")
    paths.machine_id.write_text("a" * 32 + "\n", encoding="ascii")
    paths.factory_report.write_text(json.dumps(report), encoding="utf-8")
    handoff = {
        "schemaVersion": 1,
        "status": "HANDOFF_SAFE",
        "imageReleaseId": "release-1",
        "hardwareConfigDigest": _digest(),
        "mcuIdentity": report["mcuIdentity"],
    }
    paths.handoff_fact.write_text(json.dumps(handoff), encoding="utf-8")
    monkeypatch.setattr(
        "device_credentials.load_device_credentials",
        lambda *_args, **_kwargs: {"hardwareSn": "SN-1"},
    )
    provider = SystemFactsProvider(paths, runner=_NoCommands())

    assert provider.collect().handoff_safe

    handoff["mcuIdentity"] = {
        **report["mcuIdentity"],  # type: ignore[arg-type]
        "firmwareIdentityHex": "fedcba9876543210",
    }
    paths.handoff_fact.write_text(json.dumps(handoff), encoding="utf-8")
    assert not provider.collect().handoff_safe

    handoff["mcuIdentity"] = report["mcuIdentity"]
    handoff["hardwareConfigDigest"] = "f" * 64
    paths.handoff_fact.write_text(json.dumps(handoff), encoding="utf-8")
    assert not provider.collect().handoff_safe
