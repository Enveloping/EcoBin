"""Run installed factory weight code with synthetic F1 replies, no hardware."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, "/opt/ecobin/factory-test/current/app")
from factory.acceptance_core import AcceptanceError, FactoryAcceptanceExecutor
from factory.acceptance_service import _check_summary
from factory.acceptance_measurements import valid_sampling
from factory_seal.weight_validation import valid_passed_weight_check
from first_boot.factory_flow import _validate_acceptance_projection
from first_boot.facts import FirstBootPaths, SystemFactsProvider
from first_boot.model import FactoryTestStatus
from factory_seal.validation import valid_passed_factory_report
import factory.portal


class WeightProbe(FactoryAcceptanceExecutor):
    def __init__(self):
        self.state = {
            "status": "RUNNING", "phase": "MCU_CHECK_PASSED", "revision": 1,
            "checks": {"mcu": {"status": "PASSED"}},
        }
        self.value = 2000
        self._weight_stable_sample_count = 3
        self._weight_stable_max_spread_grams = 2
        self._weight_sample_interval_ms = 100
        self._weight_sample_timeout_ms = 3000
        self._monotonic = lambda: 0.0
        self._sleeper = lambda seconds: None

    def _require_open(self):
        pass

    def _load_state(self):
        return copy.deepcopy(self.state)

    def _save_state(self, state):
        self.state = copy.deepcopy(state)
        return copy.deepcopy(self.state)

    def _query_self_test(self):
        return {"weightGrams": self.value, "infraredBlocked": False, "smokeCode": 0}


for reference in (28, 400, 500, 1000):
    probe = WeightProbe()
    probe.capture_empty_weight(reference_weight_grams=reference)
    probe.value += reference
    probe.capture_loaded_weight()
    probe.value = 2000
    result = probe.confirm_weight_removed()["checks"]["weight"]
    assert result["status"] == "PASSED"
    assert result["targetDeltaGrams"] == result["deltaGrams"] == reference
    assert valid_sampling(result["sampling"])
    assert _check_summary(result)["sampling"] == result["sampling"]
    report = probe._report_from_state(probe.state, "FAILED", 1000)
    assert report["checks"]["weight"]["targetDeltaGrams"] == reference
    assert valid_passed_weight_check(report["checks"]["weight"])
    invalid = copy.deepcopy(report["checks"]["weight"])
    invalid["deltaGrams"] += 1
    assert not valid_passed_weight_check(invalid)
probe = WeightProbe()
probe.capture_empty_weight(reference_weight_grams=400)
probe.value = 2320
try:
    probe.capture_loaded_weight()
except AcceptanceError as error:
    assert error.code == "WEIGHT_DELTA_OUT_OF_RANGE"
else:
    raise AssertionError("An 80 g deviation must fail")
weight = probe.state["checks"]["weight"]
assert weight["deltaGrams"] == 320
assert weight["sampling"]["loaded"]["samplesGrams"] == [2320] * 3
web = Path("/opt/ecobin/factory-test/current/app/factory/web")
assert 'id="reference-weight-grams"' in (web / "index.html").read_text()
assert 'id="weight-stage-readings"' in (web / "index.html").read_text()
assert "function updateMeasurements(status)" in (web / "app.js").read_text()
assert "referenceWeightGrams: selectedReferenceWeight()" in (web / "app.js").read_text()
print("installed-factory-weight=PASS references=28,400,500,1000 sharedReportWeightGate=true inconsistentDeltaRejected=true failureDelta=320 boundedSamples=true portalImport=true actualHardwareAccess=false")


class NoSystemCommands:
    def run(self, *args, **kwargs):
        raise AssertionError("Image smoke must not execute any system command")


# Replay the eight complete reports actually produced by the frozen source
# suite, against the real installed ARM64 consumer and first-boot error mapping.
# They exist only in the synthetic /tmp bind mount, never in the image bytes.
fixtures = sorted(Path('/tmp/report-fixtures').glob('*.json'))
assert len(fixtures) == 8
seen = set()
for path in fixtures:
    original = path.read_bytes()
    report = json.loads(original)
    release = report['imageReleaseId']
    digest = report['hardwareConfigDigest']
    assert valid_passed_factory_report(report, release_id=release, hardware_config_digest=digest)
    provider = SystemFactsProvider(FirstBootPaths(
        factory_state=Path('/tmp/no-factory-state.json'), factory_report=path,
    ), runner=NoSystemCommands())
    assert provider._factory_facts(release, digest) == (
        FactoryTestStatus.PASSED, True, False, 'NONE',
    )
    seen.add(report['checks']['weight']['targetDeltaGrams'])
    if report['checks']['weight']['targetDeltaGrams'] == 500:
        bad = copy.deepcopy(report)
        weight = bad['checks']['weight']
        weight['loadedWeightGrams'] = weight['emptyWeightGrams'] + 28
        weight['deltaGrams'] = 28
        weight['sampling']['loaded']['samplesGrams'] = [weight['loadedWeightGrams']] * 3
        assert not valid_passed_factory_report(bad, release_id=release, hardware_config_digest=digest)
    assert path.read_bytes() == original
assert seen == {28, 400, 500, 1000}
print('installed-full-report-consumers=PASS completeReports=8 references=28,400,500,1000 firstBootError=NONE true500Observed28Rejected=true')
