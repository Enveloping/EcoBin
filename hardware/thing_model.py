# -*- coding: utf-8 -*-
"""
thing_model.py — 物模型层：属性读写、事件上报、服务分发。

本类替代原 tm_user.py 中 ThingModelUser 的"翻译层"职责：
  - 平台查询属性 → 从 BinState / 系统读取真实数据
  - 平台下发服务调用 → 分发给对应的 ServiceHandler
  - 业务完成 → 主动上报事件到平台

事件方法（notify_*）和属性读取方法从原 ThingModelUser 迁出。
服务处理的具体逻辑已移入 delivery_handler.py / clean_handler.py，
本类仅持有 service_handlers 注册表并做 dispatch。
"""

import json
import logging
import os
import time
import threading

from hardware_layer import BinState

logger = logging.getLogger("thing_model")


class ThingModel:
    """物模型用户接口 —— 属性/事件/服务分发的薄层。"""

    def __init__(self, device, unit_price_store=None):
        """
        :param device: SmartBinGateway 实例（或兼容的 Mock），提供:
                         - device.post_property()  属性上报 (MQTT)
                         - device.post_event()     事件上报 (MQTT)
                         - device.device_name      设备名称
                         - device._connected       MQTT 连接状态
        """
        self.device = device
        self.serial = None
        self.exit_flag: threading.Event = None
        self.unit_price_store = unit_price_store

        # ── 属性读取处理器 ──
        self.prop_read_handlers = {
            "doorStates": self._read_door_states,
            "fwVersion": self._read_fw_version,
            "online": self._read_online,
            "rssi": self._read_rssi,
            "voltage": self._read_voltage,
        }
        if self.unit_price_store is not None:
            self.prop_read_handlers["unitPrice"] = self._read_unit_price

        # ── 属性写入处理器 ──
        self.prop_write_handlers = {}
        if self.unit_price_store is not None:
            self.prop_write_handlers["unitPrice"] = self._write_unit_price

        # ── 服务调用处理器（由 main.py 注入具体 handler） ──
        self.service_handlers = {}

    # ── 属性列表 / 服务列表 ──

    @staticmethod
    def get_prop_list() -> list:
        return ["doorStates", "fwVersion", "online", "rssi", "voltage", "unitPrice"]

    @staticmethod
    def get_svc_list() -> list:
        return ["openCleanDoor", "openDeliveryDoor", "reboot"]

    # ═══════════════════════════════════════════════════════════
    #  服务分发
    # ═══════════════════════════════════════════════════════════

    def dispatch_service(self, svc_id: str, params: dict, msg_id: str) -> dict:
        """将平台下发的服务调用分发给对应的 handler，返回 reply data。"""
        cb = self.service_handlers.get(svc_id)
        if cb:
            return cb(params)
        return {"accepted": False, "error": f"unknown service: {svc_id}"}

    # ═══════════════════════════════════════════════════════════
    #  属性读取
    # ═══════════════════════════════════════════════════════════

    def _read_door_states(self) -> list:
        states = BinState.get_state()
        for i, s in enumerate(states, start=1):
            s["doorIndex"] = i
        return states

    def _read_unit_price(self) -> float:
        return self.unit_price_store.get()

    def _write_unit_price(self, value) -> None:
        """先持久化 OneNet 期望值，再立即同步给 MCU。"""
        stored = self.unit_price_store.set(value)
        digit = self.unit_price_store.get_protocol_digit()
        if self.serial is None or not self.serial.send_price_digit(digit):
            raise RuntimeError("unitPrice 已保存，但当前未能同步到 MCU")
        logger.info("单价已更新: %.4g 元/kg → MCU 数据位 %d", stored, digit)

    @staticmethod
    def _read_fw_version() -> str:
        return "v2.1.3-zer03"

    def _read_online(self) -> bool:
        return self.device._connected if self.device else True

    @staticmethod
    def _read_rssi() -> int:
        try:
            with open("/proc/net/wireless", "r") as f:
                lines = f.readlines()
                if len(lines) >= 3:
                    parts = lines[2].split()
                    if len(parts) >= 4:
                        return int(float(parts[3]))
        except Exception:
            pass
        return -40

    @staticmethod
    def _read_voltage() -> float:
        return 5.0

    # ═══════════════════════════════════════════════════════════
    #  属性上报
    # ═══════════════════════════════════════════════════════════

    def _prop_notify(self, prop_name: str, value, timeout_ms: int = 5000) -> int:
        prop_data = {
            prop_name: {
                "value": value,
                "time": int(time.time() * 1000),
            }
        }
        return self.device.post_property(prop_data, timeout_ms)

    def notify_door_states(self, door_states: list, timeout_ms: int = 5000) -> int:
        return self._prop_notify("doorStates", door_states, timeout_ms)

    def notify_fw_version(self, val: str, timeout_ms: int = 5000) -> int:
        return self._prop_notify("fwVersion", val, timeout_ms)

    def notify_online(self, val: bool, timeout_ms: int = 5000) -> int:
        return self._prop_notify("online", val, timeout_ms)

    def notify_rssi(self, val: int, timeout_ms: int = 5000) -> int:
        return self._prop_notify("rssi", val, timeout_ms)

    def notify_voltage(self, val: float, timeout_ms: int = 5000) -> int:
        return self._prop_notify("voltage", val, timeout_ms)

    # ═══════════════════════════════════════════════════════════
    #  事件上报
    # ═══════════════════════════════════════════════════════════

    def _event_notify(self, identifier: str, value: dict, timeout_ms: int = 5000) -> int:
        event_data = {
            identifier: {
                "value": value,
                "time": int(time.time() * 1000),
            }
        }
        return self.device.post_event(event_data, timeout_ms)

    def notify_delivery_complete(
        self,
        door_index: int,
        weight: float,
        photo_open_outside: str = "",
        photo_open_inside: str = "",
        photo_close_outside: str = "",
        photo_close_inside: str = "",
        timeout_ms: int = 5000,
    ) -> int:
        """投递完成事件 —— 回传 doorIndex + weight + 4 张照片 URL。"""
        value = {
            "doorIndex": door_index,
            "weight": weight,
        }
        if photo_open_outside:
            value["photoOpenOutside"] = photo_open_outside
        if photo_open_inside:
            value["photoOpenInside"] = photo_open_inside
        if photo_close_outside:
            value["photoCloseOutside"] = photo_close_outside
        if photo_close_inside:
            value["photoCloseInside"] = photo_close_inside
        return self._event_notify("deliveryComplete", value, timeout_ms)

    def notify_clean_gross(
        self,
        clean_order_id: int,
        weight: float,
        photo_open_outside: str = "",
        photo_open_inside: str = "",
        photo_close_outside: str = "",
        photo_close_inside: str = "",
        timeout_ms: int = 5000,
    ) -> int:
        """清运毛重事件 —— 回传 cleanOrderId + 毛重 + 4 张照片 URL。"""
        value = {
            "cleanOrderId": clean_order_id,
            "weight": weight,
        }
        if photo_open_outside:
            value["photoOpenOutside"] = photo_open_outside
        if photo_open_inside:
            value["photoOpenInside"] = photo_open_inside
        if photo_close_outside:
            value["photoCloseOutside"] = photo_close_outside
        if photo_close_inside:
            value["photoCloseInside"] = photo_close_inside
        return self._event_notify("cleanGross", value, timeout_ms)

    def notify_clean_tare(
        self, clean_order_id: int, weight: float, timeout_ms: int = 5000
    ) -> int:
        """清运皮重事件 —— 回传 cleanOrderId + 空袋重量。"""
        return self._event_notify(
            "cleanTare", {"cleanOrderId": clean_order_id, "weight": weight}, timeout_ms
        )

    def notify_smoke_alarm(
        self, door_index: int, temperature: float, timeout_ms: int = 5000
    ) -> int:
        return self._event_notify(
            "smokeAlarm", {"doorIndex": door_index, "temperature": temperature}, timeout_ms
        )

    def notify_spill_alarm(
        self, door_index: int, fullness: int, timeout_ms: int = 5000
    ) -> int:
        return self._event_notify(
            "spillAlarm", {"doorIndex": door_index, "fullness": fullness}, timeout_ms
        )


# ── 独立测试入口 ──
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(name)s] %(message)s")

    BinState.update_overflow(1, 0)
    BinState.update_weight(1, 1500)
    BinState.update_smoke(1, 0)

    class MockDevice:
        _connected = True
        device_name = "test-device"

        def post_property(self, data, timeout_ms=5000):
            logger.info("Mock post_property: %s", json.dumps(data, ensure_ascii=False))
            return 0

        def post_event(self, data, timeout_ms=5000):
            logger.info("Mock post_event: %s", json.dumps(data, ensure_ascii=False))
            return 0

    tm = ThingModel(MockDevice())
    print("=== 属性读取测试 ===")
    print("舱门状态:", json.dumps(tm._read_door_states(), ensure_ascii=False, indent=2))
    print("固件版本:", tm._read_fw_version())
    print("在线状态:", tm._read_online())

    print("\n=== 属性上报测试 ===")
    tm.notify_door_states(tm._read_door_states())
    tm.notify_online(True)

    print("\n=== 事件上报测试 ===")
    tm.notify_delivery_complete(0, 1.5, "https://cos/o.jpg", "https://cos/i.jpg")
    tm.notify_clean_gross(42, 12.5, "https://cos/co.jpg", "https://cos/ci.jpg")
