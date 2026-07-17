import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HARDWARE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARDWARE_DIR))

import door_flow


class FakeSerial:
    def __init__(self, open_ok=True, weight=316, close_ok=True):
        self.open_ok = open_ok
        self.weight = weight
        self.close_ok = close_ok
        self.door_version = 10
        self.weight_version = 20
        self.commands = []
        self.waited_weight_after = None

    def event_versions(self):
        return self.door_version, self.weight_version

    def send_door_control(self, door_index, open_door):
        self.commands.append((door_index, open_door))
        return True

    def wait_for_door_state(self, expected_open, after_version, timeout_s):
        if expected_open:
            self.door_version += 1
            return self.open_ok
        self.door_version += 1
        return self.close_ok

    def wait_for_weight(self, after_version, timeout_s):
        self.waited_weight_after = after_version
        if self.weight is not None:
            self.weight_version += 1
        return self.weight


class FakeCamera:
    @staticmethod
    def capture_both(prefix):
        outside = Path(f"{prefix}_outside.jpg")
        inside = Path(f"{prefix}_inside.jpg")
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"outside")
        inside.write_bytes(b"inside")
        return str(outside), str(inside)


class FakeUploader:
    @staticmethod
    def creds_from_cos_token(token):
        return token

    @staticmethod
    def upload(creds, path, key):
        return f"https://cos.example/{key}"


class DeliveryFlowTest(unittest.TestCase):
    def run_cycle(self, serial):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(door_flow, "PHOTO_DIR", directory):
                return door_flow.execute_delivery_cycle(
                    door_index=1,
                    cos_token={"tmpSecretId": "id"},
                    serial=serial,
                    camera=FakeCamera,
                    uploader=FakeUploader,
                    device_name="SN-1",
                    door_state_timeout_s=0.01,
                    weight_timeout_s=0.01,
                )

    def test_success_uses_new_weight_after_cycle_baseline(self):
        serial = FakeSerial(weight=316)
        result = self.run_cycle(serial)

        self.assertEqual([(1, True), (1, False)], serial.commands)
        self.assertEqual(20, serial.waited_weight_after)
        self.assertEqual(0.316, result["weight"])
        self.assertTrue(result["photoOpenOutside"].startswith("https://cos.example/"))

    def test_open_state_timeout_sends_one_safety_close(self):
        serial = FakeSerial(open_ok=False)
        self.assertIsNone(self.run_cycle(serial))
        self.assertEqual([(1, True), (1, False)], serial.commands)

    def test_weight_timeout_sends_one_safety_close(self):
        serial = FakeSerial(weight=None)
        self.assertIsNone(self.run_cycle(serial))
        self.assertEqual([(1, True), (1, False)], serial.commands)

    def test_close_state_timeout_sends_an_extra_safety_close(self):
        serial = FakeSerial(close_ok=False)
        self.assertIsNone(self.run_cycle(serial))
        self.assertEqual([(1, True), (1, False), (1, False)], serial.commands)

    def test_rejects_non_single_door_before_writing(self):
        serial = FakeSerial()
        result = door_flow.execute_delivery_cycle(
            door_index=2,
            cos_token={"tmpSecretId": "id"},
            serial=serial,
            camera=FakeCamera,
            uploader=FakeUploader,
            device_name="SN-1",
            door_state_timeout_s=0.01,
            weight_timeout_s=0.01,
        )
        self.assertIsNone(result)
        self.assertEqual([], serial.commands)


if __name__ == "__main__":
    unittest.main()
