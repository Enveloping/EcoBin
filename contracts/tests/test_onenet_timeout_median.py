"""Cloud measurement transport preserves a usable, explicitly non-stable median."""
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contracts/tools"))
sys.path.insert(0, str(ROOT / "hardware"))

from contractlib import CONTRACTS_ROOT, ContractError, JsonSchemaSubsetValidator, canonical_json_bytes, load_json
from onenet_wire import encode_event_post


def median_event(file_name="delivery-complete.event.json", slot="finalPostCloseMeasurement"):
    event = load_json(CONTRACTS_ROOT / "examples/onenet" / file_name)
    event["payload"][slot].update(
        status="UNSTABLE", weightValueKind="TIMEOUT_MEDIAN", measurementElapsedMs=5000,
        sampleCount=20, faultCode=None)
    event["payloadSha256"] = hashlib.sha256(canonical_json_bytes(event["payload"])).hexdigest()
    return event


def test_delivery_timeout_median_is_transportable_without_a_fault_or_stable_relabel():
    event = median_event()
    JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")
    post = encode_event_post("DELIVERY_COMPLETE", event)
    value = post["params"]["deliveryComplete"]["value"]
    measurement = value["finalPostCloseMeasurement"]
    assert measurement["weightValueKind"] == 6  # existing five enum codes retain their meanings
    assert measurement["status"] == 2  # UNSTABLE, not STABLE
    assert measurement["weightValueAvailable"] is True
    assert measurement["reportedWeightGrams"] == event["payload"]["finalPostCloseMeasurement"]["reportedWeightGrams"]
    assert measurement["faultCodePresent"] is False
    assert value["manualReviewRequired"] is False


@pytest.mark.parametrize("file_name,slot", [
    ("delivery-complete.event.json", "firstPreOpenMeasurement"),
    ("delivery-complete.event.json", "finalPostCloseMeasurement"),
    ("clean-complete.event.json", "preUnlockMeasurement"),
    ("clean-complete.event.json", "cleanerConfirmedFinalMeasurement"),
])
def test_all_delivery_and_clean_weight_slots_preserve_median_metadata(file_name, slot):
    event = median_event(file_name, slot)
    JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")
    post = encode_event_post(event["eventType"], event)
    value = next(iter(post["params"].values()))["value"][slot]
    assert (value["status"], value["weightValueKind"], value["sampleCount"],
        value["measurementElapsedMs"]) == (2, 6, 20, 5000)
    assert value["faultCodePresent"] is False


@pytest.mark.parametrize("change", [
    {"status": "STABLE"}, {"status": "TIMEOUT"}, {"faultCode": "WEIGHT_UNSTABLE"},
    {"sensorHealth": "UNKNOWN"}, {"weightValueAvailable": False}, {"reportedWeightGrams": None},
    {"sampleCount": 4}, {"sampleCount": 33}, {"sampleCount": True},
    {"measurementElapsedMs": 4999}, {"measurementElapsedMs": 5001},
    {"reportedWeightGrams": -2147483649}, {"reportedWeightGrams": 2147483648},
    {"measurementUid": None}, {"mcuBootId": None}, {"mcuEventSequence": None},
    {"calibrationVersion": 4294967296}, {"mcuEventSequence": 4294967296},
])
def test_median_cannot_hide_missing_data_faults_or_an_incomplete_measurement_window(change):
    event = median_event()
    event["payload"]["finalPostCloseMeasurement"].update(change)
    with pytest.raises(ContractError):
        JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")


def runtime_median_event():
    event = load_json(CONTRACTS_ROOT / "examples/onenet/device-runtime-snapshot.event.json")
    port = event["payload"]["ports"][0]
    port.update(weightMeasurementStatus="UNSTABLE", weightValueKind="TIMEOUT_MEDIAN",
        weightValueAvailable=True, reportedWeightGrams=0, measurementElapsedMs=5000,
        weightSampleCount=5, weightSensorHealth="OK", weightFaultCode=None)
    event["payloadSha256"] = hashlib.sha256(canonical_json_bytes(event["payload"])).hexdigest()
    return event


def test_runtime_snapshot_can_describe_the_same_nonstable_median():
    event = runtime_median_event()
    JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")
    wire = encode_event_post("DEVICE_RUNTIME_SNAPSHOT", event)["params"]["deviceRuntimeSnapshot"]["value"]["ports"][0]
    assert (wire["weightMeasurementStatus"], wire["weightValueKind"], wire["reportedWeightGrams"]) == (2, 6, 0)
    assert wire["weightFaultCodePresent"] is False


@pytest.mark.parametrize("change", [
    {"weightSampleCount": 4}, {"weightSampleCount": 33}, {"measurementElapsedMs": 4999},
    {"weightMeasurementStatus": "STABLE"}, {"weightFaultCode": "WEIGHT_UNSTABLE"},
    {"weightSensorHealth": "UNKNOWN"}, {"reportedWeightGrams": None}, {"weightValueAvailable": False},
    {"weightMeasurementUid": None}, {"weightMcuBootId": None}, {"weightMcuEventSequence": None},
    {"reportedWeightGrams": -2147483649}, {"reportedWeightGrams": 2147483648},
    {"calibrationVersion": 4294967296}, {"weightMcuEventSequence": 4294967296},
])
def test_runtime_median_requires_the_same_measurement_quality_and_identity(change):
    event = runtime_median_event()
    event["payload"]["ports"][0].update(change)
    with pytest.raises(ContractError):
        JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")


@pytest.mark.parametrize("count,grams", [(5, 0), (32, -9), (5, -2147483648), (32, 2147483647)])
def test_median_sample_boundaries_preserve_signed_transport_values_not_calibration(count, grams):
    event = median_event()
    event["payload"]["finalPostCloseMeasurement"].update(sampleCount=count, reportedWeightGrams=grams)
    JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")
    value = encode_event_post(event["eventType"], event)["params"]["deliveryComplete"]["value"]["finalPostCloseMeasurement"]
    assert value["reportedWeightGrams"] == grams and value["reportedWeightGramsPresent"] is True
    assert value["sampleCount"] == count


@pytest.mark.parametrize("kind,code", [("LAST_FOUR_MEAN", 3), ("AVAILABLE_SAMPLES_MEAN", 4)])
def test_old_unstable_means_keep_their_original_kind_and_fault(kind, code):
    event = median_event()
    event["payload"]["finalPostCloseMeasurement"].update(weightValueKind=kind,
        faultCode="WEIGHT_UNSTABLE", measurementElapsedMs=1200, sampleCount=4)
    JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")
    value = encode_event_post(event["eventType"], event)["params"]["deliveryComplete"]["value"]["finalPostCloseMeasurement"]
    assert value["weightValueKind"] == code and value["faultCodePresent"] is True
