"""Transport read batching must never decide which complete frames survive."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from contractlib import CONTRACTS_ROOT, UartStreamParser, load_uart_registry  # noqa: E402
from generate_contracts import build_outputs  # noqa: E402


class IncrementalStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_uart_registry()
        cls.codec = types.ModuleType("uart2_stream_candidate")
        source = build_outputs()[CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"]
        exec(compile(source, "uart2_stream_candidate", "exec"), cls.codec.__dict__)

    def parser(self, generated):
        return self.codec.StreamParser(sender_role="MCU") if generated else UartStreamParser(self.registry, sender_role="MCU")

    def reply(self, sequence):
        payload = self.codec.encode_payload("BOOT_PROBE_REPLY", {"probeId": sequence, "mcuBootId": 42})
        return self.codec.encode_frame("BOOT_PROBE_REPLY", sequence, payload)

    def test_large_batch_has_same_complete_frames_as_small_reads(self):
        batch = b"".join(self.reply(sequence) for sequence in range(1, 41))
        self.assertGreater(len(batch), 512)
        for generated in (False, True):
            for chunk_size in (1, 127, 512, len(batch)):
                with self.subTest(generated=generated, chunk_size=chunk_size):
                    parser = self.parser(generated)
                    frames = []
                    for start in range(0, len(batch), chunk_size):
                        frames.extend(parser.feed(batch[start:start + chunk_size], now_ms=0))
                        self.assertLessEqual(parser.buffered_bytes, 512)
                    self.assertEqual(list(range(1, 41)), [frame["txSequence"] for frame in frames])
                    self.assertEqual([], parser.diagnostics)
                    self.assertEqual(0, parser.buffered_bytes)

    def test_large_read_rejects_only_the_bad_frame_and_retains_the_trailing_partial(self):
        bad = self.codec.encode_frame("BOOT_PROBE_REPLY", 999, bytes(16))
        batch = b"".join(self.reply(sequence) for sequence in range(1, 81))
        # Deliberately CRC-correct but probeId=0. Reject the whole frame.
        batch = batch[:600] + bad + batch[600:]
        for generated in (False, True):
            with self.subTest(generated=generated):
                parser = self.parser(generated)
                self.assertEqual([], parser.feed(batch[:13], now_ms=0))
                frames = parser.feed(batch[13:] + self.reply(81)[:17], now_ms=90)
                self.assertEqual(list(range(1, 81)), [frame["txSequence"] for frame in frames])
                self.assertEqual(17, parser.buffered_bytes)
                self.assertEqual([], parser.feed(b"", now_ms=189))
                self.assertEqual(17, parser.buffered_bytes)
                self.assertEqual([], parser.feed(b"", now_ms=190))
                self.assertEqual(0, parser.buffered_bytes)
                self.assertEqual(["SEMANTIC_REJECTED:invalid bootstrap payload", "FRAME_TIMEOUT"], parser.diagnostics)
                self.assertEqual([82], [frame["txSequence"] for frame in parser.feed(self.reply(82), now_ms=191)])


if __name__ == "__main__":
    unittest.main()
