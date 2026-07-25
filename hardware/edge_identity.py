"""Edge runtime identity helpers."""
from __future__ import annotations

import os
import secrets
from typing import Any


MAX_EDGE_BOOT_ID = 9_007_199_254_740_991
MIN_EDGE_BOOT_ID = 1


def is_valid_edge_boot_id(value: Any) -> bool:
    try:
        boot_id = int(str(value).strip())
    except Exception:
        return False
    return MIN_EDGE_BOOT_ID <= boot_id <= MAX_EDGE_BOOT_ID


def new_edge_boot_id() -> int:
    return secrets.randbelow(MAX_EDGE_BOOT_ID) + MIN_EDGE_BOOT_ID


def load_or_generate_edge_boot_id(path: str) -> int:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if is_valid_edge_boot_id(raw):
                return int(raw)
    except Exception:
        pass

    boot_id = new_edge_boot_id()
    persist_edge_boot_id(path, boot_id)
    return boot_id


def persist_edge_boot_id(path: str, boot_id: int) -> None:
    if not is_valid_edge_boot_id(boot_id):
        raise ValueError(f"edge_boot_id out of range: {boot_id}")
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(int(boot_id)))
