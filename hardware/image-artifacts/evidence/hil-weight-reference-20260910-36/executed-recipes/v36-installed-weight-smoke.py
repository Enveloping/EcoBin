"""Run installed factory weight code with synthetic F1 replies, no hardware."""
import copy
from pathlib import Path
import sys

sys.path.insert(0, "/opt/ecobin/factory-test/current/app")
from factory.acceptance_core import AcceptanceError, FactoryAcceptanceExecutor
from factory.acceptance_service import _check_summary
from factory.acceptance_measurements import valid_sampling
from first_boot.factory_flow import _validate_acceptance_projection
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
        self._weight_sample_interval_ms = 0
        self._weight_sample_timeout_ms = 5
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


for reference in (400, 500, 1000):
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
print("installed-factory-weight=PASS references=400,500,1000 failureDelta=320 boundedSamples=true portalImport=true actualHardwareAccess=false")
