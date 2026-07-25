import os
import tempfile

import pytest

from edge_identity import (
    MAX_EDGE_BOOT_ID,
    is_valid_edge_boot_id,
    load_or_generate_edge_boot_id,
    persist_edge_boot_id,
)


def test_edge_boot_id_range_accepts_onenet_safe_integer_bounds():
    assert is_valid_edge_boot_id(1)
    assert is_valid_edge_boot_id(MAX_EDGE_BOOT_ID)


def test_edge_boot_id_range_rejects_zero_negative_and_too_large_values():
    assert not is_valid_edge_boot_id(0)
    assert not is_valid_edge_boot_id(-1)
    assert not is_valid_edge_boot_id(MAX_EDGE_BOOT_ID + 1)
    assert not is_valid_edge_boot_id("not-a-number")


def test_load_or_generate_replaces_legacy_large_boot_id_file():
    path = os.path.join(tempfile.mkdtemp(), "edge-boot-id")
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(MAX_EDGE_BOOT_ID + 1))

    boot_id = load_or_generate_edge_boot_id(path)

    assert 1 <= boot_id <= MAX_EDGE_BOOT_ID
    with open(path, encoding="utf-8") as f:
        assert int(f.read().strip()) == boot_id


def test_persist_rejects_out_of_range_boot_id():
    path = os.path.join(tempfile.mkdtemp(), "edge-boot-id")

    with pytest.raises(ValueError):
        persist_edge_boot_id(path, MAX_EDGE_BOOT_ID + 1)
