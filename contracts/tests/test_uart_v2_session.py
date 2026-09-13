"""Public wire behavior for command decisions and immutable result handoff."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import (  # noqa: E402
    CONTRACTS_ROOT, ContractError, compute_uart_command_digest,
    decode_uart_payload, encode_uart_payload, load_uart_registry,
)
from generate_contracts import build_outputs, build_uart_stream_traces, build_uart_vectors  # noqa: E402


class SessionWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_uart_registry()
        cls.codec = types.ModuleType("uart2_session_candidate")
        source = build_outputs()[CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"]
        exec(compile(source, "uart2_session_candidate", "exec"), cls.codec.__dict__)

    def command_identity(self):
        command = {
            "mcuCommandUid": "11111111-1111-4111-8111-111111111111",
            "commandDigestSha256": "0" * 64,
            "targetMcuBootId": 42, "commandSequence": 7,
            "scope": "SINGLE_DELIVERY_DOOR", "portNo": 1, "executionDeadlineMs": 5000,
        }
        command["commandDigestSha256"] = compute_uart_command_digest(self.registry, "SAFE_CLOSE", command)
        return {key: command[key] for key in (
            "mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence",
        )}

    def assert_round_trip(self, name, values):
        payload = encode_uart_payload(self.registry, name, values)
        self.assertEqual(payload, self.codec.encode_payload(name, values))
        self.assertEqual(values, decode_uart_payload(self.registry, name, payload))
        self.assertEqual(values, self.codec.decode_payload(name, payload))
        return payload

    def test_decision_echoes_exact_command_identity_not_transport_sequence(self):
        values = self.command_identity() | {
            "currentMcuBootId": 42, "outcome": "ACCEPTED", "errorCode": "NONE",
        }
        payload = self.assert_round_trip("COMMAND_DECISION", values)
        self.assertEqual(71, len(payload))
        for tx_sequence in (1, 999):
            frame = self.codec.encode_frame("COMMAND_DECISION", tx_sequence, payload)
            parsed = self.codec.decode_frame(frame, sender_role="MCU")
            self.assertEqual(values, self.codec.decode_payload("COMMAND_DECISION", parsed["payload"]))

    def test_decision_cannot_claim_acceptance_from_another_boot_or_with_an_error(self):
        identity = self.command_identity()
        for current_boot, outcome, error in (
            (0, "ACCEPTED", "NONE"), (43, "ACCEPTED", "NONE"),
            (42, "ACCEPTED", "BUSY"), (42, "REJECTED", "NONE"),
            (42, "BOOT_MISMATCH", "NONE"), (42, "NOT_SEEN", "NONE"),
            (43, "IDENTITY_CONFLICT", "NONE"),
        ):
            for numeric in (False, True):
                values = identity | {"currentMcuBootId": current_boot, "outcome": outcome, "errorCode": error}
                if numeric:
                    values["outcome"] = self.registry["enums"]["CommandOutcome"]["values"][outcome]
                    values["errorCode"] = self.registry["enums"]["NackError"]["values"][error]
                with self.subTest(values=values):
                    with self.assertRaises(ContractError):
                        encode_uart_payload(self.registry, "COMMAND_DECISION", values)
                    with self.assertRaises(self.codec.ProtocolError):
                        self.codec.encode_payload("COMMAND_DECISION", values)

    def test_query_keeps_original_identity_and_distinguishes_retired_from_not_seen(self):
        identity = self.command_identity()
        request = {"queryId": 99} | identity
        self.assertEqual(68, len(self.assert_round_trip("QUERY_COMMAND", request)))
        for outcome, high_water in (("OLD_DETAILS_UNAVAILABLE", 10), ("NOT_SEEN", 6), ("ACCEPTED", 7)):
            reply = request | {"currentMcuBootId": 42, "outcome": outcome, "errorCode": "NONE", "highestCommandSequence": high_water}
            self.assertEqual(83, len(self.assert_round_trip("COMMAND_QUERY_RESULT", reply)))
        for name in ("QUERY_COMMAND", "COMMAND_QUERY_RESULT"):
            self.assertFalse(self.codec.MESSAGE_SPECS[name]["ackRequired"])

    def test_query_reply_cannot_turn_a_retired_sequence_into_not_seen(self):
        base = {"queryId": 99} | self.command_identity() | {"currentMcuBootId": 42, "errorCode": "NONE"}
        for outcome, high_water in (
            ("NOT_SEEN", 7), ("OLD_DETAILS_UNAVAILABLE", 6),
            ("ACCEPTED", 8), ("IDENTITY_CONFLICT", 6),
        ):
            values = base | {"outcome": outcome, "highestCommandSequence": high_water}
            with self.subTest(outcome=outcome, high_water=high_water):
                with self.assertRaises(ContractError):
                    encode_uart_payload(self.registry, "COMMAND_QUERY_RESULT", values)
                with self.assertRaises(self.codec.ProtocolError):
                    self.codec.encode_payload("COMMAND_QUERY_RESULT", values)
        mismatch = base | {"currentMcuBootId": 0, "outcome": "BOOT_MISMATCH", "highestCommandSequence": 0}
        self.assert_round_trip("COMMAND_QUERY_RESULT", mismatch)
        with self.assertRaises(ContractError):
            encode_uart_payload(self.registry, "COMMAND_QUERY_RESULT", mismatch | {"highestCommandSequence": 1})

    def test_crc_valid_illegal_decision_never_reaches_stream_consumer(self):
        values = self.command_identity() | {"currentMcuBootId": 42, "outcome": "ACCEPTED", "errorCode": "NONE"}
        valid_payload = self.codec.encode_payload("COMMAND_DECISION", values)
        invalid_payload = bytearray(valid_payload)
        invalid_payload[60:68] = bytes(8)
        invalid = self.codec.encode_frame("COMMAND_DECISION", 1, bytes(invalid_payload))
        valid = self.codec.encode_frame("COMMAND_DECISION", 2, valid_payload)
        parser = self.codec.StreamParser(sender_role="MCU")
        frames = parser.feed(invalid + valid, now_ms=0)
        self.assertEqual([2], [frame["txSequence"] for frame in frames])
        self.assertEqual(["SEMANTIC_REJECTED:invalid session payload"], parser.diagnostics)

    def test_saved_receipt_binds_boot_result_work_and_full_digest(self):
        identity = {"mcuBootId": 42, "resultSequence": 3,
                    "workUid": "22222222-2222-4222-8222-222222222222", "resultDigestSha256": "ab" * 32}
        self.assertEqual(60, len(self.assert_round_trip("RESULT_SAVED", identity)))
        reply = identity | {"currentMcuBootId": 42, "status": "RELEASED"}
        self.assertEqual(69, len(self.assert_round_trip("RESULT_SAVED_REPLY", reply)))
        self.assertFalse(self.codec.MESSAGE_SPECS["RESULT_SAVED"]["ackRequired"])

    def test_every_legal_reply_outcome_has_a_shared_three_language_stream_case(self):
        traces = build_uart_stream_traces(self.registry, build_uart_vectors(self.registry))
        names = {trace["name"] for trace in traces}
        for message, enum_name, excluded in (
            ("COMMAND_DECISION", "CommandOutcome", {"NOT_SEEN"}),
            ("COMMAND_QUERY_RESULT", "CommandOutcome", set()),
            ("RESULT_SAVED_REPLY", "ResultSavedStatus", set()),
        ):
            for outcome in self.registry["enums"][enum_name]["values"]:
                if outcome not in excluded:
                    self.assertIn(message.lower() + "_legal_" + outcome.lower(), names)

    def test_saved_reply_cannot_release_a_result_from_another_boot(self):
        base = {"mcuBootId": 42, "resultSequence": 3,
                "workUid": "22222222-2222-4222-8222-222222222222", "resultDigestSha256": "ab" * 32}
        for status, current_boot in (("RELEASED", 0), ("ALREADY_RELEASED", 43), ("BOOT_MISMATCH", 42)):
            for value in (status, self.registry["enums"]["ResultSavedStatus"]["values"][status]):
                payload = base | {"currentMcuBootId": current_boot, "status": value}
                with self.subTest(status=value, current_boot=current_boot):
                    with self.assertRaises(ContractError):
                        encode_uart_payload(self.registry, "RESULT_SAVED_REPLY", payload)
                    with self.assertRaises(self.codec.ProtocolError):
                        self.codec.encode_payload("RESULT_SAVED_REPLY", payload)


if __name__ == "__main__":
    unittest.main()
