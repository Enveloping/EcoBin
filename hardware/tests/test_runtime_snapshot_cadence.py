import threading
import time

import main as main_module
from main import EcoBinEdge


class ConfigurationStore:
    def __init__(self, interval_ms=None):
        self.interval_ms = interval_ms

    def get_latest_applied_configuration(self):
        if self.interval_ms is None:
            return None
        return {
            "payload": {
                "deviceConfig": {
                    "edgeHeartbeatIntervalMs": self.interval_ms,
                },
            },
        }


def edge_without_initialization(interval_ms=None):
    edge = EcoBinEdge.__new__(EcoBinEdge)
    edge.store = ConfigurationStore(interval_ms)
    edge._runtime_snapshot_lock = threading.Lock()
    edge._runtime_snapshot_requested = threading.Event()
    edge._last_runtime_snapshot_monotonic = 0.0
    return edge


def test_runtime_snapshot_uses_one_hour_default(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "EDGE_RUNTIME_SNAPSHOT_INTERVAL_S",
        3600.0,
    )

    assert edge_without_initialization()._runtime_snapshot_interval_seconds() == 3600.0


def test_runtime_snapshot_clamps_legacy_configuration_to_ten_minutes():
    edge = edge_without_initialization(300_000)

    assert edge._runtime_snapshot_interval_seconds() == 600.0


def test_state_change_publish_is_deferred_inside_five_second_window():
    edge = edge_without_initialization(3_600_000)
    edge._last_runtime_snapshot_monotonic = time.monotonic()

    result = edge._publish_runtime_snapshot_now(force=False)

    assert result == {
        "published": False,
        "skipped_unchanged": False,
        "deferred": True,
    }
    assert edge._runtime_snapshot_requested.is_set()
