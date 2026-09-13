"""A queried port's controller, sample and configuration facts share one capture."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import CONTRACTS_ROOT, load_uart_registry, encode_uart_payload
from generate_contracts import build_outputs, build_uart_stream_traces, build_uart_vectors


class DeviceFactsWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_uart_registry()
        cls.codec = types.ModuleType("device_facts_candidate")
        source = build_outputs()[CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"]
        exec(compile(source, "device_facts_candidate", "exec"), cls.codec.__dict__)

    def test_facts_capture_preserves_zero_weight_and_distinct_observation_times(self):
        request = {"queryId": 1, "targetMcuBootId": 42, "portNo": 1}
        self.assertEqual(17, len(self.codec.encode_payload("QUERY_DEVICE_FACTS", request)))
        spec = self.codec.MESSAGE_SPECS["DEVICE_FACTS_REPLY"]
        values = {}
        for field in spec["fields"]:
            kind = field["type"]
            values[field["name"]] = ("00" * 32 if kind == "sha256" else
                "00000000-0000-0000-0000-000000000000" if kind == "uuid" else False if kind == "bool" else 0)
        values |= request | {"currentMcuBootId": 42, "status": "AVAILABLE", "capturedUptimeMs": 1200,
            "controlUptimeMs": 1190, "lastDeliveryDoorCommand": "CLOSE", "doorActionActive": True,
            "pb5Active": True, "pinchPaused": True, "scaleReadStatus": "VALID",
            "scaleAttemptSequence": 5, "scaleCapturedUptimeMs": 1000, "scaleWeightGrams": 0}
        payload = self.codec.encode_payload("DEVICE_FACTS_REPLY", values)
        self.assertLessEqual(len(payload), 242)
        self.assertEqual(payload, encode_uart_payload(self.registry, "DEVICE_FACTS_REPLY", values))
        decoded = self.codec.decode_payload("DEVICE_FACTS_REPLY", payload)
        self.assertEqual(0, decoded["scaleWeightGrams"])
        self.assertEqual("VALID", decoded["scaleReadStatus"])
        self.assertEqual(1190, decoded["controlUptimeMs"])
        self.assertFalse(spec["ackRequired"])

    def test_shared_facts_traces_reject_inconsistent_observations(self):
        for trace in build_uart_stream_traces(self.registry, build_uart_vectors(self.registry)):
            if not trace["name"].startswith("device_facts_"):
                continue
            with self.subTest(trace=trace["name"]):
                parser = self.codec.StreamParser(sender_role=trace["senderRole"])
                frames = []
                for chunk in trace["chunks"]:
                    frames.extend(parser.feed(bytes.fromhex(chunk["hex"]), now_ms=chunk["atMs"]))
                self.assertEqual(trace["expectedMessageNames"], [frame["messageName"] for frame in frames])
                self.assertEqual(trace["expectedDiagnostics"], parser.diagnostics)

    def test_every_split_waits_for_complete_facts_frame(self):
        vector = next(item for item in build_uart_vectors(self.registry) if item["name"] == "device_facts_available")
        frame = bytes.fromhex(vector["frameHex"])
        for split in range(1, len(frame)):
            with self.subTest(split=split):
                parser = self.codec.StreamParser(sender_role="MCU")
                self.assertEqual([], parser.feed(frame[:split], now_ms=0))
                complete = parser.feed(frame[split:], now_ms=1)
                self.assertEqual(["DEVICE_FACTS_REPLY"], [item["messageName"] for item in complete])
