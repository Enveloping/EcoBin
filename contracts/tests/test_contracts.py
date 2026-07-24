from __future__ import annotations

import ast
import copy
import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from contractlib import (  # noqa: E402
    CONTRACTS_ROOT,
    ContractError,
    JsonSchemaSubsetValidator,
    canonical_json_bytes,
    decode_uart_payload,
    encode_uart_payload,
    load_json,
    load_uart_registry,
    onenet_command_canonical_sha256,
    parse_json_text,
    uart_message_specs,
    validate_uart_registry,
)
from generate_contracts import apply_outputs, build_outputs  # noqa: E402
from validate_contracts import (  # noqa: E402
    ValidationSummary,
    _validate_command_semantics,
    _validate_event_semantics,
    validate_generated_c,
    validate_onenet_examples,
    validate_onenet_thing_model,
    validate_onenet_wire_examples,
    validate_sources,
    validate_uart_vectors,
)


class GeneratedArtifactTests(unittest.TestCase):
    def test_generated_outputs_are_current(self) -> None:
        self.assertEqual([], apply_outputs(build_outputs(), check=True))

    def test_generated_python_parses_as_python_311(self) -> None:
        source = (
            CONTRACTS_ROOT
            / "uart"
            / "generated"
            / "python"
            / "ecobin_uart_protocol.py"
        ).read_text(encoding="utf-8")
        ast.parse(source, filename="ecobin_uart_protocol.py", feature_version=(3, 11))

    def test_generated_c_contains_all_registry_messages(self) -> None:
        summary = ValidationSummary()
        validate_generated_c(summary, run_compiler=False)
        self.assertTrue(summary.checks)


class UartRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_uart_registry()
        validate_uart_registry(cls.registry, JsonSchemaSubsetValidator())
        cls.specs = uart_message_specs(cls.registry)

    def test_every_payload_fits_one_frame(self) -> None:
        maximum = self.registry["protocol"]["maximumPayloadLength"]
        for name, spec in self.specs.items():
            with self.subTest(message=name):
                self.assertLessEqual(spec["maximumPayloadLength"], maximum)

    def test_physical_link_is_fixed_at_115200_8n1(self) -> None:
        self.assertEqual(
            {
                "baudRate": 115200,
                "dataBits": 8,
                "parity": "NONE",
                "stopBits": 1,
                "flowControl": "NONE",
                "devicePath": "DEPLOYMENT_CONFIGURED",
            },
            self.registry["physicalLink"],
        )

    def test_cleaning_door_has_no_sensor_or_safe_close_capability(self) -> None:
        capabilities = set(self.registry["capabilities"])
        self.assertNotIn("CLEAN_DOOR_SENSOR", capabilities)
        self.assertNotIn("CLEAN_DOOR_AUTO_CLOSE", capabilities)
        self.assertNotIn("CLEAN_SAFE_CLOSE", capabilities)
        safe_close = next(
            message
            for message in self.registry["messages"]
            if message["name"] == "SAFE_CLOSE"
        )
        self.assertIn("投递门", safe_close["notes"])
        self.assertIn("不适用于清运门", safe_close["notes"])

    def test_negative_weight_round_trips_as_signed_int32(self) -> None:
        vectors = load_json(
            CONTRACTS_ROOT / "examples" / "uart" / "golden-vectors.json"
        )["vectors"]
        vector = next(
            item for item in vectors if item["name"] == "negative_stable_weight"
        )
        values = decode_uart_payload(
            self.registry,
            "WORK_POSTCLOSE_WEIGHT_READY",
            bytes.fromhex(vector["payloadHex"]),
        )
        self.assertEqual(-500, values["stableWeightGrams"])
        self.assertEqual(
            bytes.fromhex(vector["payloadHex"]),
            encode_uart_payload(
                self.registry,
                "WORK_POSTCLOSE_WEIGHT_READY",
                values,
            ),
        )

    def test_shared_uart_vectors(self) -> None:
        summary = ValidationSummary()
        validate_uart_vectors(summary)
        self.assertTrue(summary.checks)


class OneNetSchemaTests(unittest.TestCase):
    def test_sources_and_mapping(self) -> None:
        summary = ValidationSummary()
        validate_sources(summary)
        self.assertGreaterEqual(len(summary.checks), 3)

    def test_examples_and_semantic_rules(self) -> None:
        summary = ValidationSummary()
        validate_onenet_examples(summary)
        self.assertGreaterEqual(len(summary.checks), 2)

    def test_generated_onenet_candidate_respects_vendor_shape(self) -> None:
        summary = ValidationSummary()
        validate_onenet_thing_model(summary)
        validate_onenet_wire_examples(summary)
        self.assertTrue(summary.checks)

    def test_delivery_payload_has_one_session_result_only(self) -> None:
        schema = load_json(
            CONTRACTS_ROOT / "onenet" / "events" / "events.schema.json"
        )
        properties = schema["$defs"]["deliveryCompletePayload"]["properties"]
        self.assertIn("negativeWeightAnomaly", properties)
        self.assertIn("firstPreOpenMeasurement", properties)
        self.assertIn("finalPostCloseMeasurement", properties)
        for forbidden in (
            "cycleUid",
            "rounds",
            "intermediateWeights",
            "negativeWeightTriggerGrams",
        ):
            self.assertNotIn(forbidden, properties)

    def test_photo_events_target_original_work(self) -> None:
        schema = load_json(
            CONTRACTS_ROOT / "onenet" / "events" / "events.schema.json"
        )
        mapping = load_json(
            CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml"
        )
        for definition_name, event_type in (
            ("photoStatusReportedEvent", "PHOTO_STATUS_REPORTED"),
            (
                "photoUploadGrantRequestedEvent",
                "PHOTO_UPLOAD_GRANT_REQUESTED",
            ),
        ):
            with self.subTest(event=event_type):
                properties = schema["$defs"][definition_name]["allOf"][1]["properties"]
                target_types = properties["target"]["allOf"][1]["properties"]["type"][
                    "enum"
                ]
                self.assertEqual(
                    {"DELIVERY_SESSION", "CLEAN_OPERATION"},
                    set(target_types),
                )
                mapping_entry = next(
                    item
                    for item in mapping["events"].values()
                    if item["eventType"] == event_type
                )
                self.assertEqual(set(target_types), set(mapping_entry["targetTypes"]))

    def test_semantic_validator_rejects_photo_target_mismatch(self) -> None:
        mapping = load_json(
            CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml"
        )
        instance = {
            "eventType": "PHOTO_UPLOAD_GRANT_REQUESTED",
            "deliveryClass": "RELIABLE_FACT",
            "target": {
                "type": "DELIVERY_SESSION",
                "uid": "10000000-0000-4000-8000-000000000001",
            },
            "payload": {
                "workType": "CLEAN_OPERATION",
                "workUid": "20000000-0000-4000-8000-000000000001",
            },
            "payloadSha256": "",
        }
        from contractlib import ContractError, payload_sha256

        instance["payloadSha256"] = payload_sha256(instance["payload"])
        with self.assertRaises(ContractError):
            _validate_event_semantics(instance, mapping)

    def test_command_expiry_compares_instants_not_timestamp_text(self) -> None:
        payload = {
            "sessionUid": "10000000-0000-4000-8000-000000000001"
        }
        instance = {
            "commandType": "START_DELIVERY_SESSION",
            "target": {
                "type": "DELIVERY_SESSION",
                "uid": payload["sessionUid"],
            },
            "issuedAt": "2026-07-24T00:00:00Z",
            "expiresAt": "2026-07-24T00:00:00.100Z",
            "payload": payload,
        }
        from contractlib import payload_sha256

        instance["payloadSha256"] = payload_sha256(payload)
        _validate_command_semantics(instance)

    def test_stable_command_digest_excludes_only_attempt_credentials(self) -> None:
        command = load_json(
            CONTRACTS_ROOT
            / "examples"
            / "onenet"
            / "start-delivery-session.command.json"
        )
        base_digest = onenet_command_canonical_sha256(command)
        refreshed = copy.deepcopy(command)
        refreshed["cosGrant"] = {
            "grantUid": "71000000-0000-4000-8000-000000000001",
            "tmpSecretId": "temporary-id",
            "tmpSecretKey": "temporary-secret",
            "sessionTokenParts": ["part"],
            "bucket": "bucket",
            "region": "region",
            "baseUrl": "https://example.invalid",
            "keyPrefix": (
                "ecobin/Dp_demo_01/delivery-session/"
                "30000000-0000-4000-8000-000000000001/"
            ),
            "expiresAt": "2026-07-24T02:00:00.000Z",
        }
        self.assertEqual(base_digest, onenet_command_canonical_sha256(refreshed))
        refreshed["expiresAt"] = "2026-07-24T01:02:00.000Z"
        self.assertNotEqual(base_digest, onenet_command_canonical_sha256(refreshed))

    def test_jcs_profile_rejects_ambiguous_inputs(self) -> None:
        for value in (0.5, 9007199254740992, "\ud800"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ContractError):
                    canonical_json_bytes(value)
        with self.assertRaises(ContractError):
            parse_json_text('{"same":1,"same":2}')

    def test_configuration_semantics_reject_port_gaps_and_bad_weight_range(self) -> None:
        command = load_json(
            CONTRACTS_ROOT
            / "examples"
            / "onenet"
            / "apply-configuration.command.json"
        )
        from contractlib import payload_sha256

        port_gap = copy.deepcopy(command)
        port_gap["payload"]["ports"][1]["portNo"] = 3
        port_gap["payloadSha256"] = payload_sha256(port_gap["payload"])
        with self.assertRaises(ContractError):
            _validate_command_semantics(port_gap)

        bad_range = copy.deepcopy(command)
        bad_range["payload"]["ports"][0]["weightMinimumGrams"] = 100000
        bad_range["payloadSha256"] = payload_sha256(bad_range["payload"])
        with self.assertRaises(ContractError):
            _validate_command_semantics(bad_range)

    def test_delivery_and_clean_weight_relationships_are_enforced(self) -> None:
        mapping = load_json(
            CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml"
        )
        from contractlib import payload_sha256

        delivery = load_json(
            CONTRACTS_ROOT
            / "examples"
            / "onenet"
            / "delivery-complete.event.json"
        )
        delivery["payload"]["deliveryNetWeightGrams"] += 1
        delivery["payloadSha256"] = payload_sha256(delivery["payload"])
        with self.assertRaises(ContractError):
            _validate_event_semantics(delivery, mapping)

        clean = load_json(
            CONTRACTS_ROOT
            / "examples"
            / "onenet"
            / "clean-complete.event.json"
        )
        clean["payload"]["newBaselineWeightGrams"] += 1
        clean["payloadSha256"] = payload_sha256(clean["payload"])
        with self.assertRaises(ContractError):
            _validate_event_semantics(clean, mapping)

    def test_available_photo_url_is_bound_to_work_and_slot(self) -> None:
        mapping = load_json(
            CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml"
        )
        event = load_json(
            CONTRACTS_ROOT
            / "examples"
            / "onenet"
            / "photo-status-reported.event.json"
        )
        from contractlib import payload_sha256

        event["payload"]["photo"]["url"] = event["payload"]["photo"]["url"].replace(
            "/AFTER_INNER/",
            "/BEFORE_INNER/",
        )
        event["payloadSha256"] = payload_sha256(event["payload"])
        with self.assertRaises(ContractError):
            _validate_event_semantics(event, mapping)


if __name__ == "__main__":
    unittest.main()
