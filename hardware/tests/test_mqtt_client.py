import base64

import mqtt_client as mqtt_module
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


class FakeStore:
    def __init__(self):
        self.connection_states = []

    def save_mqtt_persistent_state(self, session_present, reason_code):
        self.connection_states.append((session_present, reason_code))


class LifecyclePahoClient:
    def __init__(self, *, auto_connect=True):
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_publish = None
        self.auto_connect = auto_connect
        self.connect_async_calls = 0
        self.reconnect_calls = 0
        self.loop_start_calls = 0
        self.loop_stop_calls = 0
        self.disconnect_calls = 0
        self.delay = None

    def reconnect_delay_set(self, min_delay, max_delay):
        self.delay = (min_delay, max_delay)

    def username_pw_set(self, username, password):
        assert username == "product"
        assert password

    def connect_async(self, host, port, keepalive):
        self.connect_async_calls += 1

    def reconnect(self):
        self.reconnect_calls += 1
        self.on_connect(
            self,
            None,
            {"session_present": 1},
            0,
            None,
        )

    def loop_start(self):
        self.loop_start_calls += 1
        if self.auto_connect and self.connect_async_calls == 1:
            self.on_connect(
                self,
                None,
                {"session_present": 1},
                0,
                None,
            )

    def loop_stop(self):
        self.loop_stop_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1

    def subscribe(self, topic, qos):
        return (0, 1)

    def publish(self, topic, payload, qos):
        return type("PublishInfo", (), {"mid": 1, "rc": 0})()


class ImmediateTimeoutEvent:
    def clear(self):
        pass

    def wait(self, seconds):
        assert seconds == 8
        return False

    def set(self):
        pass


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


def test_reconnect_reuses_one_paho_network_loop(monkeypatch):
    paho = LifecyclePahoClient()
    monkeypatch.setattr(
        mqtt_module.mqtt,
        "Client",
        lambda *args, **kwargs: paho,
    )
    client = MqttClient(
        product_id="product",
        device_name="device",
        device_key=base64.b64encode(b"device-key").decode(),
        edge_store=FakeStore(),
    )
    client._start_relay_loop = lambda: None
    client._publish_online = lambda: None

    assert client.connect()
    client._on_disconnect(
        paho,
        None,
        None,
        7,
        None,
    )
    assert client.connect()

    assert paho.delay == (1, 30)
    assert paho.connect_async_calls == 1
    assert paho.reconnect_calls == 1
    assert paho.loop_start_calls == 1
    assert paho.loop_stop_calls == 0


def test_connect_timeout_keeps_paho_network_loop_running(monkeypatch):
    paho = LifecyclePahoClient(auto_connect=False)
    monkeypatch.setattr(
        mqtt_module.mqtt,
        "Client",
        lambda *args, **kwargs: paho,
    )
    client = MqttClient(
        product_id="product",
        device_name="device",
        device_key=base64.b64encode(b"device-key").decode(),
        edge_store=FakeStore(),
    )
    client._connect_event = ImmediateTimeoutEvent()

    assert client.connect() is False

    assert client._network_loop_started is True
    assert paho.connect_async_calls == 1
    assert paho.loop_start_calls == 1
    assert paho.loop_stop_calls == 0
