"""Exercise the real report across factory, first boot, handoff and seal gates."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from factory.acceptance_config import AcceptanceConfiguration
from factory.acceptance_core import AcceptanceError, FactoryAcceptanceExecutor
from factory.acceptance_storage import AtomicJsonFile
import factory.acceptance_handoff as handoff
from factory_seal.validation import (
    FactorySealPaths, canonical_factory_report_sha256,
    collect_local_factory_facts, valid_device_capabilities,
    valid_passed_factory_report,
)
from first_boot.facts import FirstBootPaths, SystemFactsProvider
from first_boot.model import FactoryTestStatus
from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES
from .test_factory_acceptance_core import _build_executor, _pass_delivery, _set_weight
from .test_factory_acceptance_handoff import _Bootloader, _Mcu
from .test_factory_seal_controller import _report


class NoCommands:
    def run(self, *args, **kwargs):
        raise AssertionError("This test must not invoke any system command")


@pytest.mark.parametrize("reference", [28, 400, 500, 1000])
@pytest.mark.parametrize("update_line", [False, True])
def test_real_generated_report_survives_all_local_consumers(
    tmp_path: Path, monkeypatch, reference: int, update_line: bool,
) -> None:
    executor, model, _serial = _build_executor(tmp_path)
    # _build_executor injects a synthetic capture function, not a real camera.
    executor.cameras.outside_source = "/dev/v4l/by-id/test-outside"
    executor.cameras.inside_source = "/dev/v4l/by-id/test-inside"
    config = replace(AcceptanceConfiguration.from_mapping({}),
                     report_path=str(tmp_path / "acceptance/report.json"))
    with executor:
        executor.begin_run(
            image_release_id="report-contract-1",
            boot_id="11111111-2222-3333-4444-555555555555",
            wall_time_trusted=False, hardware_config_digest=config.digest(),
            mcu_update_line_installed=update_line,
        )
        executor.check_mcu()
        _set_weight(model, 1000)
        executor.capture_empty_weight(reference_weight_grams=reference)
        _set_weight(model, 1000 + reference)
        executor.capture_loaded_weight()
        _set_weight(model, 1000)
        executor.confirm_weight_removed()
        cameras = executor.capture_cameras()
        executor.confirm_cameras(
            review_nonce=cameras["checks"]["cameras"]["reviewNonce"],
            outside_role_confirmed=True, inside_role_confirmed=True,
        )
        if update_line:
            executor.check_upgrade_line()
        _pass_delivery(executor)
        executor.run_action("CLEAN", operator_area_safe_confirmed=True,
                            timeout_ms=100, quiet_ms=0)
        executor.confirm_clean_door_closed(operator_confirmed=True, quiet_ms=0)
        report = executor.finalize(confirm_simulated_peripheral_evidence=True)
    assert report["status"] == "PASSED"
    original_bytes = Path(config.report_path).read_bytes()
    provider = SystemFactsProvider(FirstBootPaths(
        factory_state=tmp_path / "acceptance/state.json",
        factory_report=Path(config.report_path),
    ), runner=NoCommands())
    assert provider._factory_facts("report-contract-1", config.digest()) == (
        FactoryTestStatus.PASSED, True, False, "NONE",
    )
    release = tmp_path / "image-release.json"
    release.write_text(json.dumps({"schemaVersion": 1, "releaseId": "report-contract-1"}))
    seal_paths = FactorySealPaths(
        image_release=release, factory_report=Path(config.report_path),
        sealed=tmp_path / "sealed.json",
    )
    facts = collect_local_factory_facts(seal_paths)
    assert facts.factory_report_sha256 == canonical_factory_report_sha256(report)
    mcu, bootloader = _Mcu(report["mcuIdentity"]), _Bootloader()
    for name, path in {
        "IMAGE_RELEASE_PATH": release,
        "HANDOFF_FACT_PATH": tmp_path / "handoff.json",
        "DEVICE_CAPABILITIES_PATH": tmp_path / "capabilities.json",
        "INSTANCE_LOCK_PATH": tmp_path / "locks/handoff.lock",
        "UART_LOCK_PATH": tmp_path / "locks/handoff-uart.lock",
    }.items():
        monkeypatch.setattr(handoff, name, path)
    monkeypatch.setattr(handoff.FixedFrameAcceptanceMcu, "for_port", lambda *a, **k: mcu)
    monkeypatch.setattr(handoff, "ReadOnlyStm32RomProbe", lambda **k: bootloader)
    monkeypatch.setattr(handoff, "_resolve_business_capability_owner", lambda: None)
    # Neither report validation nor capability validation is mocked.
    assert handoff.run_handoff(config)["status"] == "HANDOFF_SAFE"
    assert bootloader.calls == (
        ["force_application_selection", "boot_application"] if update_line else []
    )
    capabilities = AtomicJsonFile(
        tmp_path / "capabilities.json", file_mode=0o640, chmod_existing_parent=False,
    ).read()
    assert valid_device_capabilities(capabilities, report)
    assert collect_local_factory_facts(seal_paths) == facts
    assert Path(config.report_path).read_bytes() == original_bytes


def _weight_report(reference=500, *, error=0, removal_error=0, sampling=False):
    report = _report("report-contract-1")
    weight = report["checks"]["weight"]
    weight.update(
        targetDeltaGrams=reference, loadedWeightGrams=1000 + reference + error,
        deltaGrams=reference + error, removedWeightGrams=1000 + removal_error,
        resultCode=("WEIGHT_500G_WITHIN_490_510_AND_REMOVED" if reference == 500
                    else "WEIGHT_REFERENCE_WITHIN_TOLERANCE_AND_REMOVED"),
    )
    if sampling:
        weight["sampling"] = {
            stage: {"samplesGrams": [weight[field]] * 3, "readCount": 3,
                    "resultCode": "STABLE_WEIGHT_CAPTURED"}
            for stage, field in (("empty", "emptyWeightGrams"),
                                 ("loaded", "loadedWeightGrams"),
                                 ("removed", "removedWeightGrams"))
        }
    return report


def _accepted(report):
    return valid_passed_factory_report(
        report, release_id="report-contract-1", hardware_config_digest="a" * 64,
    )


@pytest.mark.parametrize("reference", [11, 28, 400, 500, 1000])
@pytest.mark.parametrize("error,expected", [(-11, False), (-10, True), (0, True), (10, True), (11, False)])
def test_report_checks_reference_relative_error(reference, error, expected):
    assert _accepted(_weight_report(reference, error=error)) is expected


@pytest.mark.parametrize("removal_error,expected", [(-11, False), (-10, True), (10, True), (11, False)])
def test_report_requires_return_to_empty(removal_error, expected):
    assert _accepted(_weight_report(400, removal_error=removal_error)) is expected


@pytest.mark.parametrize("override", [
    {"targetDeltaGrams": True}, {"targetDeltaGrams": 500.0},
    {"targetDeltaGrams": 10}, {"targetDeltaGrams": 350001},
    {"targetDeltaGrams": "500"}, {"toleranceGrams": 10.0},
    {"deltaGrams": True}, {"deltaGrams": 501},
    {"emptyWeightGrams": -1}, {"loadedWeightGrams": 350001},
    {"loadedWeightGrams": 28}, {"removedWeightGrams": True},
    {"stableSampleCount": 3.0}, {"sampleIntervalMs": 100.0},
])
def test_report_rejects_invalid_or_inconsistent_weight_facts(override):
    report = _weight_report()
    report["checks"]["weight"].update(override)
    assert not _accepted(report)
    with pytest.raises(AcceptanceError, match="ACCEPTANCE_REPORT_WEIGHT_INVALID"):
        FactoryAcceptanceExecutor._validate_report(report)


def test_500g_object_reported_as_28g_cannot_pass_500g_reference():
    assert not _accepted(_weight_report(500, error=28 - 500))


@pytest.mark.parametrize("case", ["oversized", "wrong_median", "unstable", "missing_stage", "failed_stage"])
def test_passed_report_rejects_invalid_sampling(case):
    report = _weight_report(sampling=True)
    sampling = report["checks"]["weight"]["sampling"]
    if case == "oversized":
        sampling["loaded"]["samplesGrams"] = [1500] * 33
    elif case == "wrong_median":
        sampling["loaded"]["samplesGrams"] = [1490] * 3
    elif case == "unstable":
        sampling["loaded"]["samplesGrams"] = [1490, 1500, 1510]
    elif case == "missing_stage":
        sampling.pop("removed")
    else:
        sampling["removed"]["resultCode"] = "WEIGHT_NOT_STABLE"
    assert not _accepted(report)


def test_legacy_500g_report_without_sampling_still_accepted():
    assert _accepted(_weight_report())


def test_weight_policy_is_in_runtime_and_business_payloads():
    assert "factory_seal/weight_validation.py" in RUNTIME_APP_FILES
    assert "factory_seal/weight_validation.py" in BUSINESS_APP_FILES
