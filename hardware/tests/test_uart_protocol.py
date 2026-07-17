import sys
import unittest
from pathlib import Path

HARDWARE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARDWARE_DIR))

from hardware_layer import BinState, SerialBridge


class FakePort:
    def __init__(self):
        self.is_open = True
        self.writes = []

    def write(self, data):
        self.writes.append(data)


class SerialProtocolTest(unittest.TestCase):
    def setUp(self):
        with BinState.lock:
            BinState.doors = [
                dict(weight=0.0, fullness=0, spill_alarm=False, smoke_alarm=False)
                for _ in range(6)
            ]
        self.bridge = SerialBridge()

    def test_parses_split_concatenated_and_noisy_frames(self):
        alarms = []
        weights = []
        self.bridge.on_spill_alarm = lambda door: alarms.append(("spill", door))
        self.bridge.on_smoke_alarm = lambda door: alarms.append(("smoke", door))
        self.bridge.on_weight_received = lambda door, value: weights.append((door, value))

        self.bridge._feed_received_data(bytes.fromhex("99 88 AA 01"))
        self.assertEqual((0, 0), self.bridge.event_versions())
        self.bridge._feed_received_data(
            bytes.fromhex("AA BB 01 BB CC 01 CC DD 00 01 3C DD")
        )

        self.assertEqual((1, 1), self.bridge.event_versions())
        self.assertEqual([(1, 316)], weights)
        self.assertEqual([("spill", 1), ("smoke", 1)], alarms)
        state = BinState.get_door_state(1)
        self.assertEqual(0.316, state["weight"])
        self.assertEqual(100, state["fullness"])
        self.assertTrue(state["spill_alarm"])
        self.assertTrue(state["smoke_alarm"])

    def test_fixed_weight_length_allows_marker_inside_payload(self):
        self.bridge._feed_received_data(bytes.fromhex("DD DD AA CC DD"))
        self.assertEqual(0xDDAACC / 1000.0, BinState.get_door_state(1)["weight"])

    def test_invalid_flag_is_rejected_without_updating_state(self):
        self.bridge._feed_received_data(bytes.fromhex("AA 02 AA BB 09 BB CC FF CC"))
        self.assertEqual((0, 0), self.bridge.event_versions())
        state = BinState.get_door_state(1)
        self.assertFalse(state["spill_alarm"])
        self.assertFalse(state["smoke_alarm"])

    def test_receive_buffer_is_bounded(self):
        self.bridge._feed_received_data(b"\x01" * (SerialBridge.MAX_RECV_BUFFER + 100))
        self.assertLessEqual(len(self.bridge._recv_buffer), SerialBridge.MAX_RECV_BUFFER)

    def test_writes_exact_binary_frames(self):
        port = FakePort()
        self.bridge.serial_port = port

        self.assertTrue(self.bridge.send_door_control(1, True))
        self.assertTrue(self.bridge.send_door_control(1, False))
        self.assertTrue(self.bridge.send_price_digit(5))
        self.assertFalse(self.bridge.send_door_control(2, True))

        self.assertEqual(
            [bytes.fromhex("AA 00 AA"), bytes.fromhex("AA 01 AA"), bytes.fromhex("BB 05 BB")],
            port.writes,
        )

    def test_wait_requires_a_new_event_version(self):
        self.bridge._feed_received_data(bytes.fromhex("AA 01 AA DD 00 00 64 DD"))
        door_version, weight_version = self.bridge.event_versions()
        self.assertFalse(self.bridge.wait_for_door_state(True, door_version, 0.001))
        self.assertIsNone(self.bridge.wait_for_weight(weight_version, 0.001))


if __name__ == "__main__":
    unittest.main()
