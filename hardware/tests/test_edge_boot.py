from edge_boot import _publish_runtime_snapshot
from edge_store import EdgeStore


class FakeMqttClient:
    deployment_code = "Dp_demo_01"

    def __init__(self):
        self.published = []

    def publish_event(self, event_type, payload):
        self.published.append((event_type, payload))
        return 1


def test_publish_runtime_snapshot_uses_valid_edge_boot_id_and_event_uid(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_edge_boot_id("123")
    mqtt = FakeMqttClient()

    _publish_runtime_snapshot(
        store,
        mqtt,
        {
            "mcu_boot_id": 456,
            "mcu_firmware_version": "test-fw",
            "mcu_capability": 1,
        },
        [],
    )

    assert len(mqtt.published) == 1
    event_type, payload = mqtt.published[0]
    assert event_type == "DEVICE_RUNTIME_SNAPSHOT"
    assert payload["eventUid"]
    assert payload["payload"]["edgeBootId"] == 123
    assert payload["payload"]["mcuBootId"] == 456
