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
from generate_contracts import (  # noqa: E402
    HARDWARE_MCU_UART_GOLDEN_TEST,
    HARDWARE_MCU_UART_HEADER,
    apply_outputs,
    build_outputs,
)
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

    def test_onenet_import_candidate_stays_below_vendor_file_limit(self) -> None:
        candidate = (
            CONTRACTS_ROOT
            / "onenet"
            / "generated"
            / "onenet-thing-model.candidate.json"
        ).read_bytes()
        self.assertLess(len(candidate), 256 * 1024)
        self.assertNotIn(b"\r\n", candidate)

    def test_onenet_import_candidate_enum_descriptions_match_vendor_limits(
        self,
    ) -> None:
        candidate = load_json(
            CONTRACTS_ROOT
            / "onenet"
            / "generated"
            / "onenet-thing-model.candidate.json"
        )
        descriptions: list[str] = []

        def collect(value: object) -> None:
            if isinstance(value, dict):
                data_type = value.get("dataType")
                if (
                    isinstance(data_type, dict)
                    and data_type.get("type") == "enum"
                ):
                    descriptions.extend(data_type["specs"].values())
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(candidate)
        self.assertTrue(descriptions)
        for description in descriptions:
            with self.subTest(description=description):
                self.assertRegex(
                    description,
                    r"\A[A-Za-z0-9_\-\u4e00-\u9fa5]{1,20}\Z",
                )

    def test_hardware_mcu_outputs_are_explicitly_opt_in(self) -> None:
        default_outputs = build_outputs()
        self.assertNotIn(HARDWARE_MCU_UART_HEADER, default_outputs)
        self.assertNotIn(HARDWARE_MCU_UART_GOLDEN_TEST, default_outputs)

        mcu_outputs = build_outputs(include_hardware_mcu=True)
        self.assertIn(HARDWARE_MCU_UART_HEADER, mcu_outputs)
        self.assertIn(HARDWARE_MCU_UART_GOLDEN_TEST, mcu_outputs)

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
                "devicePath": "RUNTIME_CONFIGURED",
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
        self.assertEqual(-500, values["reportedWeightGrams"])
        self.assertEqual(
            bytes.fromhex(vector["payloadHex"]),
            encode_uart_payload(
                self.registry,
                "WORK_POSTCLOSE_WEIGHT_READY",
                values,
            ),
        )

    def test_unstable_weight_preserves_fallback_value(self) -> None:
        vectors = load_json(
            CONTRACTS_ROOT / "examples" / "uart" / "golden-vectors.json"
        )["vectors"]
        vector = next(
            item
            for item in vectors
            if item["name"] == "unstable_weight_keeps_fallback_value"
        )
        values = decode_uart_payload(
            self.registry,
            "WORK_POSTCLOSE_WEIGHT_READY",
            bytes.fromhex(vector["payloadHex"]),
        )
        self.assertEqual("UNSTABLE", values["measurementStatus"])
        self.assertTrue(values["weightValuePresent"])
        self.assertEqual(-480, values["reportedWeightGrams"])
        self.assertEqual("LAST_FOUR_MEAN", values["weightValueKind"])
        self.assertEqual("WEIGHT_UNSTABLE", values["faultCode"])

    def test_registry_has_no_delivery_door_position_claim(self) -> None:
        self.assertNotIn("DeliveryDoorState", self.registry["enums"])
        self.assertNotIn(
            "DELIVERY_DOOR_POSITION_FEEDBACK",
            self.registry["capabilities"],
        )
        messages = {item["name"]: item for item in self.registry["messages"]}
        self.assertNotIn("DELIVERY_DOOR_STATE_CHANGED", messages)
        fields = {
            field["name"]
            for field in self.specs["DELIVERY_DOOR_COMMAND_RESULT"]["fields"]
        }
        self.assertIn("physicalDoorStateBasis", fields)
        self.assertNotIn("actualOutputMs", fields)

    def test_latched_door_output_uses_the_breaking_compact_layout(self) -> None:
        device_fields = {
            field["name"]
            for field in self.specs["CONFIG_DEVICE_BLOCK"]["fields"]
        }
        self.assertNotIn("deliveryDoorOpenCommandSignalMs", device_fields)
        self.assertNotIn("deliveryDoorCloseCommandSignalMs", device_fields)

        expected_lengths = {
            "CONFIG_DEVICE_BLOCK": 163,
            "DELIVERY_DOOR_COMMAND_RESULT": 60,
            "SAFE_CLOSE_RESULT": 43,
            "STATE_SNAPSHOT_PORT": 81,
        }
        for message_name, expected_length in expected_lengths.items():
            with self.subTest(message=message_name):
                spec = self.specs[message_name]
                self.assertEqual(expected_length, spec["minimumPayloadLength"])
                self.assertEqual(expected_length, spec["maximumPayloadLength"])

        for message_name in (
            "DELIVERY_DOOR_COMMAND_RESULT",
            "SAFE_CLOSE_RESULT",
        ):
            result_fields = {
                field["name"] for field in self.specs[message_name]["fields"]
            }
            self.assertNotIn("actualOutputMs", result_fields)
        snapshot_fields = {
            field["name"]
            for field in self.specs["STATE_SNAPSHOT_PORT"]["fields"]
        }
        self.assertNotIn("lastDeliveryDoorActualOutputMs", snapshot_fields)

        output_statuses = self.registry["enums"]["DoorCommandOutputStatus"][
            "values"
        ]
        self.assertEqual(
            2,
            output_statuses["COMMAND_SUPERSEDED_BEFORE_DISPATCH"],
        )
        self.assertNotIn("PARTIAL_OUTPUT_INTERRUPTED", output_statuses)
        self.assertNotIn(
            "DELIVERY_DOOR_OUTPUT_INTERRUPTED",
            self.registry["enums"]["FaultCode"]["values"],
        )

    def test_delivery_door_result_reports_output_not_position(self) -> None:
        values = {
            "mcuBootId": 101,
            "mcuEventSequence": 9,
            "uptimeMs": 1000,
            "mcuCommandUid": "10000000-0000-4000-8000-000000000001",
            "sessionUid": "20000000-0000-4000-8000-000000000001",
            "portNo": 1,
            "roundIndex": 1,
            "command": "CLOSE",
            "outputStatus": "COMMAND_DISPATCHED",
            "physicalDoorStateBasis": "NOT_OBSERVABLE",
            "faultCode": "NONE",
        }
        payload = encode_uart_payload(
            self.registry,
            "DELIVERY_DOOR_COMMAND_RESULT",
            values,
        )
        self.assertEqual(
            values,
            decode_uart_payload(
                self.registry,
                "DELIVERY_DOOR_COMMAND_RESULT",
                payload,
            ),
        )

        rejected_without_fault = copy.deepcopy(values)
        rejected_without_fault["outputStatus"] = "OUTPUT_REJECTED"
        with self.assertRaises(ContractError):
            encode_uart_payload(
                self.registry,
                "DELIVERY_DOOR_COMMAND_RESULT",
                rejected_without_fault,
            )

    def test_v1_baseline_does_not_require_mcu_persistence(self) -> None:
        policy = self.registry["capabilityPolicy"]
        self.assertEqual(0x300, int(policy["requiredMcuMaskHex"], 16))
        self.assertEqual(0x300, int(policy["requiredEdgeMaskHex"], 16))
        for requirements in policy["messageRequirements"].values():
            self.assertNotIn("PERSISTENT_COMMAND_DEDUP", requirements)
            self.assertNotIn("PERSISTENT_CRITICAL_EVENTS", requirements)

    def test_tunable_hil_values_are_bounded_configuration_fields(self) -> None:
        device_fields = {
            field["name"]: field
            for field in self.specs["CONFIG_DEVICE_BLOCK"]["fields"]
        }
        port_fields = {
            field["name"]: field
            for field in self.specs["CONFIG_PORT_BLOCK"]["fields"]
        }
        self.assertEqual(5000, device_fields["cleanSolenoidPulseMs"]["maximum"])
        self.assertIn("默认 1000", device_fields["cleanSolenoidPulseMs"]["notes"])
        self.assertEqual(
            4000,
            port_fields["fullnessDistanceThresholdMm"]["maximum"],
        )
        self.assertIn(
            "候选默认 600",
            port_fields["fullnessDistanceThresholdMm"]["notes"],
        )
        self.assertEqual(
            (30000, 45000),
            (
                device_fields["deliveryDoorTravelWaitMs"]["minimum"],
                device_fields["deliveryDoorTravelWaitMs"]["maximum"],
            ),
        )

    def test_boot_reconciliation_has_both_explicit_branches(self) -> None:
        messages = {item["name"]: item for item in self.registry["messages"]}
        self.assertIn("CONFIRM_NO_ACTIVE_WORK", messages)
        self.assertIn("BOOT_RECONCILIATION_RESULT", messages)
        resume_fields = {
            field["name"]
            for field in self.specs["RESUME_CLEAN_OPERATION"]["fields"]
        }
        self.assertIn("nextCleanActionSequence", resume_fields)
        self.assertNotIn("operationWindowMs", resume_fields)

    def test_shared_uart_vectors(self) -> None:
        summary = ValidationSummary()
        validate_uart_vectors(summary)
        self.assertTrue(summary.checks)


class OneNetSchemaTests(unittest.TestCase):
    def test_onenet_projection_matches_latched_door_contract(self) -> None:
        commands = load_json(
            CONTRACTS_ROOT
            / "onenet"
            / "commands"
            / "commands.schema.json"
        )
        common = load_json(
            CONTRACTS_ROOT / "onenet" / "common.schema.json"
        )
        events = load_json(
            CONTRACTS_ROOT / "onenet" / "events" / "events.schema.json"
        )

        device_config = commands["$defs"]["deviceConfig"]
        for removed in (
            "deliveryDoorOpenCommandSignalMs",
            "deliveryDoorCloseCommandSignalMs",
        ):
            self.assertNotIn(removed, device_config["required"])
            self.assertNotIn(removed, device_config["properties"])

        door_fact = common["$defs"]["deliveryDoorCommandFact"]
        self.assertNotIn("actualOutputMs", door_fact["required"])
        self.assertNotIn("actualOutputMs", door_fact["properties"])
        statuses = set(door_fact["properties"]["outputStatus"]["enum"])
        self.assertIn("COMMAND_SUPERSEDED_BEFORE_DISPATCH", statuses)
        self.assertNotIn("PARTIAL_OUTPUT_INTERRUPTED", statuses)

        runtime_port = events["$defs"]["runtimePort"]
        self.assertNotIn(
            "lastDeliveryDoorActualOutputMs",
            runtime_port["required"],
        )
        self.assertNotIn(
            "lastDeliveryDoorActualOutputMs",
            runtime_port["properties"],
        )

    def test_uart_fault_codes_are_losslessly_representable_in_onenet(self) -> None:
        registry = load_uart_registry()
        common = load_json(
            CONTRACTS_ROOT / "onenet" / "common.schema.json"
        )
        uart_codes = set(registry["enums"]["FaultCode"]["values"]) - {"NONE"}
        measurement_codes = set(
            common["$defs"]["faultCodeSymbol"]["enum"]
        )
        device_codes = set(
            common["$defs"]["deviceFaultCodeSymbol"]["enum"]
        )

        self.assertEqual(uart_codes, measurement_codes)
        self.assertEqual(
            uart_codes
            | {
                "EDGE_STORAGE",
                "CAMERA_CAPTURE",
                "CAMERA_STORAGE",
                "NETWORK_CONNECTIVITY",
                "CLOCK_UNSYNCED",
            },
            device_codes,
        )

    def test_uart_result_enums_are_representable_in_onenet(self) -> None:
        registry_enums = load_uart_registry()["enums"]
        common = load_json(
            CONTRACTS_ROOT / "onenet" / "common.schema.json"
        )
        events = load_json(
            CONTRACTS_ROOT / "onenet" / "events" / "events.schema.json"
        )
        measurement = common["$defs"]["measurementWithQuality"]["properties"]
        runtime = events["$defs"]["runtimePort"]["properties"]
        fault = events["$defs"]["deviceFaultPayload"]["properties"]
        safety = events["$defs"]["safetySensorStateChangedPayload"]["properties"]

        def uart_symbols(enum_name: str) -> set[str]:
            return set(registry_enums[enum_name]["values"])

        exact_pairs = (
            (measurement["status"]["enum"], "MeasurementStatus"),
            (measurement["weightValueKind"]["enum"], "WeightValueKind"),
            (measurement["sensorHealth"]["enum"], "SensorHealth"),
            (
                common["$defs"]["deliveryDoorCommandFact"]["properties"][
                    "command"
                ]["enum"],
                "DeliveryDoorCommand",
            ),
            (
                common["$defs"]["deliveryDoorCommandFact"]["properties"][
                    "outputStatus"
                ]["enum"],
                "DoorCommandOutputStatus",
            ),
            (runtime["cleanLockPowerState"]["enum"], "CleanLockPowerState"),
            (runtime["solenoidHealth"]["enum"], "SolenoidHealth"),
            (runtime["cleanDoorStateBasis"]["enum"], "CleanDoorStateBasis"),
            (runtime["smokeState"]["enum"], "SmokeState"),
            (runtime["smokeSensorHealth"]["enum"], "SensorHealth"),
            (safety["workType"]["enum"], "WorkType"),
            (fault["severity"]["enum"], "FaultSeverity"),
        )
        for onenet_symbols, uart_enum in exact_pairs:
            with self.subTest(enum=uart_enum):
                self.assertEqual(
                    uart_symbols(uart_enum),
                    set(onenet_symbols),
                )

        subset_pairs = (
            (runtime["fullnessSensorKind"]["enum"], "FullnessSensorKind"),
            (runtime["fullnessSensorValue"]["enum"], "FullnessSensorValue"),
            (runtime["fullnessSampleBasis"]["enum"], "FullnessSampleBasis"),
            (fault["component"]["enum"], "FaultComponent"),
        )
        for onenet_symbols, uart_enum in subset_pairs:
            with self.subTest(enum=uart_enum):
                self.assertLessEqual(
                    uart_symbols(uart_enum),
                    set(onenet_symbols),
                )

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
                "ecobin/Dv_0123456789abcdefghijklmn/delivery-session/"
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

    def test_configuration_carries_tunable_hil_candidates(self) -> None:
        command = load_json(
            CONTRACTS_ROOT
            / "examples"
            / "onenet"
            / "apply-configuration.command.json"
        )
        device = command["payload"]["deviceConfig"]
        port = command["payload"]["ports"][0]
        self.assertEqual(1000, device["cleanSolenoidPulseMs"])
        self.assertEqual(600, port["fullnessDistanceThresholdMm"])
        self.assertEqual("ULTRASONIC", port["fullnessSensorKind"])
        self.assertEqual(5, port["fullnessSampleCount"])
        self.assertEqual(3, port["fullnessMinimumValidSampleCount"])

        from contractlib import payload_sha256

        invalid_sample_counts = copy.deepcopy(command)
        invalid_sample_counts["payload"]["ports"][0][
            "fullnessMinimumValidSampleCount"
        ] = 6
        invalid_sample_counts["payloadSha256"] = payload_sha256(
            invalid_sample_counts["payload"]
        )
        with self.assertRaises(ContractError):
            _validate_command_semantics(invalid_sample_counts)

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
