"""Querying original work must not synthesize permission to repeat its action."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import CONTRACTS_ROOT, load_uart_registry, encode_uart_payload, decode_uart_payload
from generate_contracts import build_outputs, build_uart_stream_traces, build_uart_vectors


def query_values():
    return {"queryId": 101, "mcuCommandUid": "11111111-1111-4111-8111-111111111111",
            "commandDigestSha256": "ab" * 32, "targetMcuBootId": 42, "commandSequence": 2,
            "workUid": "22222222-2222-4222-8222-222222222222",
            "workType": "DELIVERY_SESSION", "portNo": 1}


class WorkQueryWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_uart_registry()
        cls.codec = types.ModuleType("work_query_candidate")
        exec(compile(build_outputs()[CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"],
                     "work_query_candidate", "exec"), cls.codec.__dict__)

    def test_query_echoes_original_work_and_reports_running_without_result(self):
        values = query_values()
        request = self.codec.encode_payload("QUERY_WORK", values)
        self.assertEqual(86, len(request))
        self.assertEqual(values, decode_uart_payload(self.registry, "QUERY_WORK", request))
        reply = values | {"currentMcuBootId": 42, "status": "RUNNING",
                          "phase": "DELIVERY_OPEN_COUNTDOWN", "resultSequence": 0,
                          "resultDigestSha256": "00" * 32}
        payload = self.codec.encode_payload("WORK_QUERY_REPLY", reply)
        self.assertEqual(132, len(payload))
        self.assertEqual(payload, encode_uart_payload(self.registry, "WORK_QUERY_REPLY", reply))
        self.assertEqual(reply, self.codec.decode_payload("WORK_QUERY_REPLY", payload))
        self.assertFalse(self.codec.MESSAGE_SPECS["QUERY_WORK"]["ackRequired"])

    def test_shared_legal_and_illegal_work_query_traces(self):
        for trace in build_uart_stream_traces(self.registry, build_uart_vectors(self.registry)):
            if not trace["name"].startswith("work_query_"):
                continue
            with self.subTest(trace=trace["name"]):
                parser = self.codec.StreamParser(sender_role=trace["senderRole"])
                frames = []
                for chunk in trace["chunks"]:
                    frames.extend(parser.feed(bytes.fromhex(chunk["hex"]), now_ms=chunk["atMs"]))
                self.assertEqual(trace["expectedMessageNames"], [frame["messageName"] for frame in frames])
                self.assertEqual(trace["expectedDiagnostics"], parser.diagnostics)
