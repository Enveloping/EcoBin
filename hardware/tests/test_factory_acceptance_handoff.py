from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path

import factory.acceptance_handoff as handoff_module
from factory.acceptance_config import AcceptanceConfiguration
from factory.acceptance_handoff import run_handoff
from factory.acceptance_storage import AtomicJsonFile


class _Mcu:
    def __init__(self, identity: dict[str, object]) -> None:
        self.identity = identity
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def clear_input_for_recovery(self) -> None:
        return None

    def require_business_quiet(self, **_values: object) -> None:
        return None

    def business_input_marker(self) -> int:
        return 0

    def query_identity(self) -> dict[str, object]:
        return {
            "queryStatus": "OK",
            "mode": 1,
            "statusCode": 0,
            "protocolRevision": 2,
            **self.identity,
        }

    def query_self_test(self) -> dict[str, object]:
        return {
            "queryStatus": "OK",
            "communicationHealthy": True,
            "validFlags": 3,
            "weightValid": True,
            "weightGrams": 0,
            "infraredValid": True,
            "infraredBlocked": False,
            "smokeCode": 0,
            "smokeState": "NORMAL",
            "smokeSensorHealth": "OK",
        }


class _Bootloader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def force_application_selection(self) -> None:
        self.calls.append("force_application_selection")

    def boot_application(self) -> None:
        self.calls.append("boot_application")


def test_handoff_without_update_line_never_touches_boot0_or_nrst(
    tmp_path: Path,
    monkeypatch,
) -> None:
    identity = {
        "fixedFrameRevision": 2,
        "firmwareVersion": "factory-sim-1.0.0",
        "firmwareVersionCode": 1,
        "firmwareIdentityHex": "45434f53494d3031",
    }
    report = {
        "schemaVersion": 2,
        "status": "PASSED",
        "mcuIdentity": identity,
        "mcuRemoteUpdateCapable": False,
    }
    report_path = (tmp_path / "report.json").absolute()
    release_path = (tmp_path / "image-release.json").absolute()
    handoff_path = (tmp_path / "handoff.json").absolute()
    capabilities_path = (tmp_path / "device-capabilities.json").absolute()
    AtomicJsonFile(report_path).write(report)
    release_path.write_text(
        json.dumps({"schemaVersion": 1, "releaseId": "release-1"}),
        encoding="utf-8",
    )
    mcu = _Mcu(identity)
    bootloader = _Bootloader()
    monkeypatch.setattr(handoff_module, "IMAGE_RELEASE_PATH", release_path)
    monkeypatch.setattr(handoff_module, "HANDOFF_FACT_PATH", handoff_path)
    monkeypatch.setattr(
        handoff_module,
        "DEVICE_CAPABILITIES_PATH",
        capabilities_path,
    )
    monkeypatch.setattr(
        handoff_module.FixedFrameAcceptanceMcu,
        "for_port",
        lambda *_args, **_kwargs: mcu,
    )
    monkeypatch.setattr(
        handoff_module,
        "ReadOnlyStm32RomProbe",
        lambda **_kwargs: bootloader,
    )
    monkeypatch.setattr(
        handoff_module,
        "AcceptanceLease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        handoff_module,
        "valid_passed_factory_report",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        handoff_module,
        "mcu_remote_update_capability",
        lambda _report: False,
    )
    config = replace(
        AcceptanceConfiguration.from_mapping({}),
        report_path=str(report_path),
    )

    fact = run_handoff(config)

    assert fact["status"] == "HANDOFF_SAFE"
    assert bootloader.calls == []
    capabilities = AtomicJsonFile(
        capabilities_path,
        chmod_existing_parent=False,
    ).read()
    assert capabilities == {
        "schemaVersion": 1,
        "mcuRemoteUpdateCapable": False,
        "factoryReportSha256": handoff_module.canonical_factory_report_sha256(
            report
        ),
    }
