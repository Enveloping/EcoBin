from __future__ import annotations

from pathlib import Path

from edge_store import EdgeStore
from main import _seed_enrolled_device_entry_url


def test_enrollment_url_seeds_fresh_edge_store_but_never_overwrites_sync(tmp_path: Path):
    edge = EdgeStore(str(tmp_path / "edge.db"))
    edge.initialize()

    assert _seed_enrolled_device_entry_url(
        edge,
        "https://www.jinshoubao.com/device-entry/factory",
    ) is True
    assert _seed_enrolled_device_entry_url(
        edge,
        "https://www.jinshoubao.com/device-entry/later-bootstrap",
    ) is False
    assert edge.get_device_entry_url()["deviceEntryUrl"] == (
        "https://www.jinshoubao.com/device-entry/factory"
    )
    edge.close()
