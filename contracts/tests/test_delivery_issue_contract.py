"""Issue-only envelopes are distinct from normal delivery and manual quarantine."""
import sys
import copy
import pytest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from generate_contracts import build_onenet_examples
from contractlib import JsonSchemaSubsetValidator, CONTRACTS_ROOT, load_json
from contractlib import ContractError, payload_sha256
from validate_contracts import _validate_event_semantics


def test_generated_automatic_archive_is_a_distinct_issue_only_event():
    example, _ = build_onenet_examples()["delivery-issue-archived.event.json"]
    assert example["eventType"] == "DELIVERY_ISSUE_ARCHIVED"
    payload = example["payload"]
    assert payload["reason"] == "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
    assert payload["businessValue"] == "NONE"
    assert payload["finalResultAtArchive"] == "ABSENT"
    assert example["eventUid"] == payload["issueUid"]
    assert "operatorConfirmations" not in payload and "deliveryNetWeightGrams" not in payload
    JsonSchemaSubsetValidator().validate(example, CONTRACTS_ROOT / "onenet/events/events.schema.json")
    _validate_event_semantics(example, load_json(CONTRACTS_ROOT / "onenet/thing-model.mapping.yaml"))


@pytest.mark.parametrize("change", ["same_boot", "wrong_target", "wrong_command", "wrong_issue", "wrong_start"])
def test_issue_archive_requires_original_identity_and_positive_reboot_evidence(change):
    event = copy.deepcopy(build_onenet_examples()["delivery-issue-archived.event.json"][0])
    if change == "same_boot":
        event["payload"]["targetMcuBootId"] = event["payload"]["sourceMcuBootId"]
    elif change == "wrong_target":
        event["target"]["uid"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif change == "wrong_command":
        event["commandUid"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif change == "wrong_issue":
        event["eventUid"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    else:
        event["payload"]["originalStartPayloadHex"] = "00" * 60
    event["payloadSha256"] = payload_sha256(event["payload"])
    with pytest.raises(ContractError):
        _validate_event_semantics(event, load_json(CONTRACTS_ROOT / "onenet/thing-model.mapping.yaml"))


def test_issue_evidence_is_bounded_and_explicitly_nonbusiness():
    event, _ = build_onenet_examples()["delivery-issue-evidence-appended.event.json"]
    assert event["eventType"] == "DELIVERY_ISSUE_EVIDENCE_APPENDED"
    payload = event["payload"]
    assert payload["businessValue"] == "NONE"
    assert len(payload["dataHex"]) <= 512
    assert payload["partIndex"] <= payload["partCount"]
    JsonSchemaSubsetValidator().validate(event, CONTRACTS_ROOT / "onenet/events/events.schema.json")
    _validate_event_semantics(event, load_json(CONTRACTS_ROOT / "onenet/thing-model.mapping.yaml"))


@pytest.mark.parametrize("field,value", [("partIndex",2),("partCount",2),("evidenceSizeBytes",256),
    ("dataHex","00"),("evidenceIndex",1),("archiveEvidenceSha256","b"*64)])
def test_issue_evidence_rejects_inconsistent_fragment_metadata(field,value):
    event = copy.deepcopy(build_onenet_examples()["delivery-issue-evidence-appended.event.json"][0])
    event["payload"][field] = value
    event["payloadSha256"] = payload_sha256(event["payload"])
    with pytest.raises(ContractError):
        _validate_event_semantics(event, load_json(CONTRACTS_ROOT / "onenet/thing-model.mapping.yaml"))
