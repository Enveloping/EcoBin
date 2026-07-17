import json
import sys
import tempfile
import unittest
from pathlib import Path

HARDWARE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARDWARE_DIR))

from device_config import UnitPriceStore
from thing_model import ThingModel


class FakeDevice:
    _connected = True

    def post_property(self, data, timeout_ms=5000):
        return 0

    def post_event(self, data, timeout_ms=5000):
        return 0


class FakeSerial:
    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.digits = []

    def send_price_digit(self, digit):
        self.digits.append(digit)
        return self.succeeds


class UnitPriceStoreTest(unittest.TestCase):
    def test_default_conversion_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UnitPriceStore(Path(directory) / "config.json")
            self.assertEqual(0.5, store.get())
            self.assertEqual(5, store.get_protocol_digit())
            store.set(0.45)
            self.assertEqual(4, store.get_protocol_digit())

    def test_rejects_values_outside_protocol_range(self):
        for value in (-0.1, 1, 4.3, True, "not-number"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    UnitPriceStore.normalize(value)

    def test_persists_and_restores_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            UnitPriceStore(path).set(0.67)
            restored = UnitPriceStore(path)
            self.assertEqual(0.67, restored.get())
            self.assertEqual(6, restored.get_protocol_digit())
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_corrupt_file_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(0.5, UnitPriceStore(path).get())

    def test_thing_model_persists_then_sends_to_mcu(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = UnitPriceStore(path)
            model = ThingModel(FakeDevice(), store)
            serial = FakeSerial()
            model.serial = serial

            model.prop_write_handlers["unitPrice"](0.45)

            self.assertEqual([4], serial.digits)
            self.assertEqual(0.45, model.prop_read_handlers["unitPrice"]())
            self.assertEqual(0.45, json.loads(path.read_text(encoding="utf-8"))["unitPrice"])

    def test_failed_uart_sync_keeps_persisted_desired_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = UnitPriceStore(path)
            model = ThingModel(FakeDevice(), store)
            model.serial = FakeSerial(succeeds=False)

            with self.assertRaises(RuntimeError):
                model.prop_write_handlers["unitPrice"](0.8)

            self.assertEqual(0.8, UnitPriceStore(path).get())


if __name__ == "__main__":
    unittest.main()
