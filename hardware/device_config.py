# -*- coding: utf-8 -*-
"""设备侧可持久化配置。"""

from __future__ import annotations

import json
import logging
import os
import threading
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

logger = logging.getLogger("device_config")

DEFAULT_UNIT_PRICE = Decimal("0.5")


class UnitPriceStore:
    """保存 OneNet 下发的单价，并生成 MCU 所需的一位价格数据。"""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._unit_price = self._load()

    @staticmethod
    def normalize(value) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("unitPrice 必须是数字")
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("unitPrice 必须是数字") from exc
        if not price.is_finite() or price < 0 or price >= 1:
            raise ValueError("unitPrice 必须在 [0.0, 1.0) 范围内")
        return price

    @staticmethod
    def protocol_digit(value) -> int:
        price = UnitPriceStore.normalize(value)
        return int((price * 10).to_integral_value(rounding=ROUND_DOWN))

    def get(self) -> float:
        with self._lock:
            return float(self._unit_price)

    def get_protocol_digit(self) -> int:
        with self._lock:
            return self.protocol_digit(self._unit_price)

    def set(self, value) -> float:
        price = self.normalize(value)
        with self._lock:
            self._persist(price)
            self._unit_price = price
            return float(price)

    def _load(self) -> Decimal:
        if not self.path.exists():
            return DEFAULT_UNIT_PRICE
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return self.normalize(data["unitPrice"])
        except Exception as exc:
            logger.warning("设备配置读取失败，使用默认单价 %.1f: %s", DEFAULT_UNIT_PRICE, exc)
            return DEFAULT_UNIT_PRICE

    def _persist(self, price: Decimal) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps({"unitPrice": float(price)}, ensure_ascii=False, indent=2)
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                file.write(payload)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
