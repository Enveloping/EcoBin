# -*- coding: utf-8 -*-
"""
test_mode.py — 测试模式模拟 MCU 硬件实现。

当 config.TEST_MODE = True 时，本模块提供 MCU 相关硬件的模拟版本：
  - MockSerialBridge  — 模拟串口通信（开门时自动生成随机重量）
  - MockCamera         — 模拟摄像头（生成最小占位 JPEG 文件）

所有模拟组件与真实组件接口完全兼容，可直接替换使用。
无需实际串口设备或摄像头硬件即可运行。

注意：COS 上传和 MQTT 网关不做模拟，始终使用真实实现。
"""

import logging
import os
import random
import threading
import time
from typing import Optional, Callable

from hardware_layer import BinState

logger = logging.getLogger("test_mode")

# ═══════════════════════════════════════════════════════════════
#  最小有效 JPEG（1×1 像素灰度图，约 160 字节）
#  用于模拟摄像头拍照，所有图片查看器均可正常打开
# ═══════════════════════════════════════════════════════════════
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
    b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
    b"\x1c $.\' \"$#\x1c\x1c(7),01444\x1f\'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00{1\xc0q@\xe2\x01\x00\x00\x00\x00"
    b"\x00\x00\x00"
    b"\xff\xd9"
)


# ================================================================
#  MockSerialBridge — 模拟串口通信
# ================================================================
class MockSerialBridge:
    """
    模拟香橙派 ←→ MCU 串口桥接器。

    与真实 SerialBridge 接口完全一致：
      - open() 始终返回 True（无需实际串口设备）
      - send_cmd() 记录日志并返回 True；open 指令会自动生成模拟重量数据
      - recv_loop() 在后台空转（数据由事件驱动，非轮询）
      - 回调属性（on_weight_received 等）与真实类相同

    模拟重量范围：500–5000g（0.5–5kg），随机生成。
    """

    def __init__(self):
        self.serial_port = None
        self._running = False
        self._door_state_version = 0
        self._weight_version = 0
        self._door_is_open = None
        self._latest_weight_grams = None
        self.last_price_digit = None
        self.on_weight_received: Optional[Callable] = None
        self.on_spill_alarm: Optional[Callable] = None
        self.on_smoke_alarm: Optional[Callable] = None

    def open(self, port: str = None) -> bool:
        logger.info("[TEST] 模拟串口已打开 (port=%s)", port or "N/A")
        return True

    def close(self):
        self._running = False
        logger.info("[TEST] 模拟串口已关闭")

    def send_cmd(self, door_index: int, cmd: str) -> bool:
        """
        模拟旧清运门控指令。cmd="open" 时立即向 BinState 注入随机重量，
        供尚未适配新协议的旧清运流程使用。
        """
        logger.info("[TEST] 模拟串口发送: D1,%d,%s,D0", door_index, cmd)
        if cmd == "open":
            weight = random.randint(500, 5000)
            BinState.update_weight(door_index, weight)
            logger.info("[TEST] 模拟重量注入: 舱门%d = %dg (%.2fkg)", door_index, weight, weight / 1000.0)
            if self.on_weight_received:
                self.on_weight_received(door_index, weight)
        return True

    def send_door_control(self, door_index: int, open_door: bool) -> bool:
        """模拟新二进制投递协议，并立即产生动作状态。"""
        if door_index != 1:
            return False
        self._door_is_open = open_door
        self._door_state_version += 1
        logger.info("[TEST] 模拟二进制门控: door=%d open=%s", door_index, open_door)
        if open_door:
            weight = random.randint(500, 5000)
            self._latest_weight_grams = weight
            self._weight_version += 1
            BinState.update_weight(door_index, weight)
            if self.on_weight_received:
                self.on_weight_received(door_index, weight)
        return True

    def send_price_digit(self, digit: int) -> bool:
        if isinstance(digit, bool) or not isinstance(digit, int) or not 0 <= digit <= 9:
            raise ValueError("单价协议数据必须是 0..9 的整数")
        self.last_price_digit = digit
        logger.info("[TEST] 模拟单价同步: BB %02X BB", digit)
        return True

    def event_versions(self) -> tuple[int, int]:
        return self._door_state_version, self._weight_version

    def wait_for_door_state(self, expected_open: bool, after_version: int, timeout_s: float) -> bool:
        return self._door_state_version > after_version and self._door_is_open is expected_open

    def wait_for_weight(self, after_version: int, timeout_s: float):
        if self._weight_version > after_version:
            return self._latest_weight_grams
        return None

    def recv_loop(self):
        """模拟串口接收线程（空转，数据由 send_cmd 事件驱动）。"""
        self._running = True
        logger.info("[TEST] 模拟串口接收线程已启动（事件驱动模式）")
        while self._running:
            time.sleep(1)
        logger.info("[TEST] 模拟串口接收线程已退出")


# ================================================================
#  MockCamera — 模拟双摄像头拍照
# ================================================================
class MockCamera:
    """
    模拟 USB 双摄像头控制器。

    与真实 DualCamera 接口完全一致：
      - capture() 生成最小有效 JPEG 文件（1×1 像素）
      - capture_both() 同时"拍摄"箱外/箱内两张照片
      - 无需 OpenCV、fswebcam 或实际摄像头硬件
    """

    @staticmethod
    def capture(device_id: int, save_path: str) -> bool:
        """生成占位 JPEG 文件到 save_path。"""
        logger.info("[TEST] 模拟摄像头%d 拍照 → %s", device_id, save_path)
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(_MINIMAL_JPEG)
        return True

    @classmethod
    def capture_both(cls, prefix: str) -> tuple:
        """
        同时"拍摄"箱外和箱内照片。
        :return: (outside_path, inside_path)
        """
        outside = f"{prefix}_outside.jpg"
        inside = f"{prefix}_inside.jpg"
        cls.capture(0, outside)
        cls.capture(1, inside)
        return outside, inside
