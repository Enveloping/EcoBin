# -*- coding: utf-8 -*-
"""
智能垃圾桶网关主程序（main.py）
================================
适配平台：香橙派 Zero3 (Orange Pi Zero3)

程序角色：香橙派作为"中继网关"，桥接：
  1. OneNET 平台（MQTT 协议）—— 远端云平台
  2. 垃圾桶 MCU（串口 UART）—— 近端硬件
  3. USB 双摄像头 —— 本地外设

模块架构（重构后）：
  main.py              — 入口，组装所有模块
  config.py            — 环境变量配置
  mqtt_gateway.py      — MQTT 连接/鉴权/收发
  thing_model.py       — 物模型：属性/事件/服务分发
  door_flow.py         — 通用开门闭环
  delivery_handler.py  — 投递开门处理器
  clean_handler.py     — 清运开门处理器
  hardware_layer.py    — 硬件抽象层

使用方法：
  1. 设置环境变量或修改 config.py 默认值
  2. python3 main.py
  3. Ctrl+C 退出
"""

import logging
import signal
import sys
import threading
import time

from config import (
    PRODUCT_ID,
    DEVICE_NAME,
    DEVICE_KEY,
    MQTT_HOST,
    MQTT_PORT,
    TEST_MODE,
    validate as config_validate,
)
from hardware_layer import BinState, SerialBridge, DualCamera, CosUploader, SERIAL_PORT
from mqtt_gateway import MqttGateway
from thing_model import ThingModel
from delivery_handler import DeliveryHandler
from clean_handler import CleanHandler, handle_reboot

if TEST_MODE:
    from test_mode import MockSerialBridge, MockCamera


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-10s] %(levelname)-5s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")


class SmartBinGateway:
    """智能垃圾桶网关 —— 组装所有模块并协调生命周期。"""

    def __init__(self):
        self.exit_flag = threading.Event()

        # ── 配置校验 ──
        config_validate()

        # ── 测试模式 ──
        if TEST_MODE:
            logger.info("=" * 60)
            logger.info("⚠  测试模式已启用 (ECOBIN_TEST_MODE=true)")
            logger.info("   串口 / 摄像头数据均为模拟，MQTT 和 COS 上传保持真实")
            logger.info("=" * 60)

        # ── 硬件层（测试模式下串口和摄像头使用模拟实现） ──
        if TEST_MODE:
            SerialCls = MockSerialBridge
            CameraCls = MockCamera
        else:
            SerialCls = SerialBridge
            CameraCls = DualCamera

        self.serial = SerialCls()
        self.serial.on_weight_received = self._on_weight_from_mcu
        self.serial.on_spill_alarm = self._on_spill_from_mcu
        self.serial.on_smoke_alarm = self._on_smoke_from_mcu

        # ── MQTT 网关 ──
        self.gw = MqttGateway(PRODUCT_ID, DEVICE_NAME, DEVICE_KEY, MQTT_HOST, MQTT_PORT)

        # ── 物模型 ──
        self.tm = ThingModel(self.gw)

        # ── 服务处理器（COS 上传始终使用真实实现） ──
        self.delivery_handler = DeliveryHandler(
            serial=self.serial,
            camera=CameraCls,
            uploader=CosUploader,
            bin_state=BinState,
            thing_model=self.tm,
            device_name=DEVICE_NAME,
        )
        self.clean_handler = CleanHandler(
            serial=self.serial,
            camera=CameraCls,
            uploader=CosUploader,
            bin_state=BinState,
            thing_model=self.tm,
            device_name=DEVICE_NAME,
        )

        # ── 向 MQTT 网关注册回调 ──
        self.gw.on_service_call = self._dispatch_service
        self.gw.on_property_get = self._on_property_get
        self.gw.on_property_set = self._on_property_set
        self.gw.on_connected = self._start_periodic_report

        # ── 注册服务处理器 ──
        self.tm.service_handlers = {
            "openDeliveryDoor": self.delivery_handler.handle,
            "openCleanDoor": self.clean_handler.handle,
            "reboot": handle_reboot,
        }

        # ── 系统信号 ──
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ═══════════════════════════════════════════════════════════
    #  回调：MQTT → ThingModel
    # ═══════════════════════════════════════════════════════════

    def _on_property_get(self, name: str):
        """MQTT 属性查询回调：查 ThingModel 的 prop_read_handlers。"""
        cb = self.tm.prop_read_handlers.get(name)
        if cb:
            return cb()
        return None

    def _on_property_set(self, name: str, value) -> None:
        """MQTT 属性设置回调。"""
        cb = self.tm.prop_write_handlers.get(name)
        if cb:
            cb(value)

    def _dispatch_service(self, svc_id: str, params: dict, msg_id: str) -> dict:
        """MQTT 服务调用回调：分发给 ThingModel.service_handlers。"""
        return self.tm.dispatch_service(svc_id, params, msg_id)

    # ═══════════════════════════════════════════════════════════
    #  回调：串口传感器
    # ═══════════════════════════════════════════════════════════

    def _on_weight_from_mcu(self, door_index: int, weight_grams: int):
        logger.info("[传感器] 舱门%d 重量=%dg", door_index, weight_grams)

    def _on_spill_from_mcu(self, door_index: int):
        logger.info("[传感器] 舱门%d 满溢报警!", door_index)
        self.tm.notify_spill_alarm(door_index, 100)

    def _on_smoke_from_mcu(self, door_index: int):
        logger.info("[传感器] 舱门%d 烟雾报警!", door_index)
        self.tm.notify_smoke_alarm(door_index, 0.0)

    # ═══════════════════════════════════════════════════════════
    #  定时上报
    # ═══════════════════════════════════════════════════════════

    def _start_periodic_report(self):
        """MQTT 连接成功后启动定时属性上报线程（每 30 秒）。"""

        def _report_loop():
            while not self.exit_flag.is_set():
                self.exit_flag.wait(30)
                if self.exit_flag.is_set():
                    break
                try:
                    self.tm.notify_door_states(self.tm._read_door_states())
                    self.tm.notify_online(self.gw.connected)
                    self.tm.notify_rssi(self.tm._read_rssi())
                    self.tm.notify_voltage(self.tm._read_voltage())
                    logger.debug("定时上报完成")
                except Exception as e:
                    logger.error("定时上报失败: %s", e)

        t = threading.Thread(target=_report_loop, daemon=True, name="periodic")
        t.start()

    # ═══════════════════════════════════════════════════════════
    #  生命周期
    # ═══════════════════════════════════════════════════════════

    def _signal_handler(self, signum, frame):
        logger.info("收到退出信号 %d，正在关闭...", signum)
        self.exit_flag.set()
        # 主动断开 MQTT，使 loop_forever() 退出阻塞
        try:
            self.gw.disconnect()
        except Exception:
            pass

    def run(self):
        logger.info("=" * 60)
        logger.info("智能垃圾桶网关启动 (香橙派 Zero3)")
        logger.info("PID=%s  Device=%s", PRODUCT_ID, DEVICE_NAME)
        if TEST_MODE:
            logger.info("测试模式: 串口/摄像头数据均为模拟")
        logger.info("=" * 60)

        # 校验凭证
        if not all([PRODUCT_ID, DEVICE_NAME, DEVICE_KEY]):
            logger.error("PRODUCT_ID / DEVICE_NAME / DEVICE_KEY 不能为空!")
            return

        # 打开串口
        if not self.serial.open(SERIAL_PORT):
            logger.warning("串口 %s 打开失败，将无传感器数据!", SERIAL_PORT)
            logger.warning("继续以纯MQTT模式运行")
        else:
            recv_thread = threading.Thread(
                target=self.serial.recv_loop, daemon=True, name="serial_recv"
            )
            recv_thread.start()
            self.tm.serial = self.serial
            self.tm.exit_flag = self.exit_flag

        # 连接 MQTT
        if not self.gw.connect():
            logger.error("MQTT 连接失败，退出")
            return

        logger.info("进入保活循环 (Ctrl+C 退出)")
        try:
            self.gw.loop_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _shutdown(self):
        logger.info("正在关闭...")
        self.exit_flag.set()
        self.serial.close()
        self.gw.disconnect()
        logger.info("网关已关闭")


if __name__ == "__main__":
    gateway = SmartBinGateway()
    gateway.run()
