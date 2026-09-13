"""A completed native result is self-contained; UART chunks are not results."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import CONTRACTS_ROOT, load_uart_registry
from generate_contracts import build_outputs
from contractlib import ContractError, compute_uart_result_digest, decode_uart_payload, encode_uart_payload


def result_values():
    values = {
        "mcuBootId": 42, "resultSequence": 3,
        "workUid": "22222222-2222-4222-8222-222222222222",
        "resultDigestSha256": "00" * 32,
        "workType": "DELIVERY_SESSION", "portNo": 1, "configVersion": 7,
        "originCommandUid": "11111111-1111-4111-8111-111111111111",
        "originCommandSequence": 2, "completedUptimeMs": 10000,
        "deliveryRoundCount": 1, "cleanActionSequence": 0,
        "finishReason": "DELIVERY_END", "physicalCloseConfirmed": False,
        "negativeWeightAnomaly": False,
    }
    for prefix, sequence, grams in (("initial", 1, 400), ("final", 2, 1000)):
        values.update({
            prefix + "Kind": "STABLE_MEAN",
            prefix + "MeasurementUid": f"33333333-3333-4333-8333-{sequence:012d}",
            prefix + "SourceMcuBootId": 42, prefix + "McuEventSequence": sequence,
            prefix + "WeightGrams": grams, prefix + "ElapsedMs": 1250,
            prefix + "SampleCount": 5, prefix + "SpanGrams": 20,
            prefix + "CalibrationVersion": 1, prefix + "FaultCode": "NONE",
        })
    return values


class CompleteResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_uart_registry()
        cls.codec = types.ModuleType("result_candidate")
        exec(compile(build_outputs()[CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"],
                     "result_candidate", "exec"), cls.codec.__dict__)

    def test_both_measurements_and_identity_fit_one_frame_and_digest_covers_body(self):
        values = result_values()
        values["resultDigestSha256"] = self.codec.compute_result_digest(values)
        payload = self.codec.encode_payload("WORK_RESULT", values)
        self.assertEqual(199, len(payload))
        frame = self.codec.encode_frame("WORK_RESULT", 1, payload)
        self.assertEqual(213, len(frame))
        parsed = self.codec.decode_frame(frame, sender_role="MCU")
        self.assertEqual(values, self.codec.decode_payload("WORK_RESULT", parsed["payload"]))
        changed = values | {"finalWeightGrams": 999}
        with self.assertRaises(self.codec.ProtocolError):
            self.codec.encode_payload("WORK_RESULT", changed)

    def test_every_possible_uart_chunk_boundary_requires_the_complete_frame(self):
        values = result_values()
        values["resultDigestSha256"] = self.codec.compute_result_digest(values)
        payload = self.codec.encode_payload("WORK_RESULT", values)
        frame = self.codec.encode_frame("WORK_RESULT", 1, payload)
        for split in range(1, len(frame)):
            parser = self.codec.StreamParser(sender_role="MCU")
            self.assertEqual([], parser.feed(frame[:split], now_ms=0))
            self.assertEqual([payload], [item["payload"] for item in parser.feed(frame[split:], now_ms=1)])
        parser = self.codec.StreamParser(sender_role="MCU")
        self.assertEqual([], parser.feed(frame[:100], now_ms=0))
        self.assertEqual([], parser.feed(b"", now_ms=101))
        self.assertEqual([payload, payload], [item["payload"] for item in parser.feed(frame + frame, now_ms=102)])

    def test_explicit_missing_is_distinct_from_successfully_measured_zero(self):
        values = result_values() | {"initialKind": "MCU_RESET_LOST"}
        for field in tuple(values):
            if field.startswith("initial") and field != "initialKind":
                values[field] = ("00000000-0000-0000-0000-000000000000" if field.endswith("Uid") else
                                 "NONE" if field.endswith("FaultCode") else 0)
        values["resultDigestSha256"] = self.codec.compute_result_digest(values)
        payload = self.codec.encode_payload("WORK_RESULT", values)
        self.assertEqual(values, decode_uart_payload(self.registry, "WORK_RESULT", payload))
        values = result_values() | {"initialWeightGrams": 0}
        values["resultDigestSha256"] = self.codec.compute_result_digest(values)
        decoded = self.codec.decode_payload("WORK_RESULT", self.codec.encode_payload("WORK_RESULT", values))
        self.assertEqual("STABLE_MEAN", decoded["initialKind"])
        self.assertEqual(0, decoded["initialWeightGrams"])

    def test_semantically_invalid_results_fail_even_with_recomputed_digest(self):
        changes = [
            {"initialMeasurementUid": "{" + result_values()["finalMeasurementUid"].upper() + "}"},
            {"workType": "NONE"}, {"initialMeasurementUid": result_values()["finalMeasurementUid"]},
            {"finalMcuEventSequence": 1}, {"initialSourceMcuBootId": 43},
            {"initialKind": "NOT_TAKEN"}, {"finalSampleCount": 4},
            {"finalSpanGrams": 101}, {"finalKind": "TIMEOUT_MEDIAN"},
            {"finalKind": "UNAVAILABLE"}, {"physicalCloseConfirmed": True},
            {"deliveryRoundCount": 0}, {"finishReason": "CLEAN_CONFIRMED"},
        ]
        for change in changes:
            for numeric in (False, True):
                values = result_values() | change
                if numeric:
                    for field in self.codec.MESSAGE_SPECS["WORK_RESULT"]["fields"]:
                        if "enum" in field:
                            values[field["name"]] = self.registry["enums"][field["enum"]]["values"][values[field["name"]]]
                values["resultDigestSha256"] = compute_uart_result_digest(self.registry, values)
                with self.subTest(change=change, numeric=numeric):
                    with self.assertRaises(ContractError):
                        encode_uart_payload(self.registry, "WORK_RESULT", values)
                    with self.assertRaises(self.codec.ProtocolError):
                        self.codec.encode_payload("WORK_RESULT", values)

    def test_timeout_median_retains_measurement_provenance_without_using_legacy_mean_kind(self):
        values = result_values() | {"finalKind": "TIMEOUT_MEDIAN", "finalElapsedMs": 5000,
                                  "finalSampleCount": 20, "finalSpanGrams": 1000}
        values["resultDigestSha256"] = compute_uart_result_digest(self.registry, values)
        payload = encode_uart_payload(self.registry, "WORK_RESULT", values)
        self.assertEqual(values, self.codec.decode_payload("WORK_RESULT", payload))
