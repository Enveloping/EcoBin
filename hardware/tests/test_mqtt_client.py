from mqtt_client import MqttClient


class FakeExitEvent:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)

    def set(self):
        self.stopped = True


class FakePahoClient:
    def __init__(self):
        self.loop_stop_calls = 0
        self.disconnect_calls = 0

    def loop_stop(self):
        self.loop_stop_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1


def test_loop_forever_retries_initial_mqtt_connection():
    mqtt = MqttClient.__new__(MqttClient)
    mqtt._connected = False
    mqtt._exit_flag = FakeExitEvent()
    mqtt.client = FakePahoClient()
    attempts = []

    def connect():
        attempts.append(len(attempts) + 1)
        if len(attempts) == 3:
            mqtt._connected = True
            mqtt._exit_flag.set()
            return True
        return False

    mqtt.connect = connect

    mqtt.loop_forever()

    assert attempts == [1, 2, 3]
    assert mqtt._exit_flag.waits == [1, 2]
    assert mqtt.client.loop_stop_calls == 1
    assert mqtt.client.disconnect_calls == 1
