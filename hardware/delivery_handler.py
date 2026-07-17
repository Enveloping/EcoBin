# -*- coding: utf-8 -*-
"""
delivery_handler.py — 投递开门服务处理器。

收到平台 openDeliveryDoor 指令后，执行新版二进制 UART 硬件闭环（见 door_flow），
完成后上报 deliveryComplete 事件。
"""

import logging
import threading

from door_flow import execute_delivery_cycle
from hardware_layer import SerialBridge, DualCamera, CosUploader, BinState

logger = logging.getLogger("delivery")


class DeliveryHandler:
    """投递开门处理器 —— 依赖注入所有硬件组件。"""

    def __init__(
        self,
        serial: SerialBridge,
        camera: type,
        uploader: type,
        bin_state: type,
        thing_model,
        device_name: str,
        door_state_timeout_s: float,
        weight_timeout_s: float,
    ):
        self.serial = serial
        self.camera = camera
        self.uploader = uploader
        self.bin_state = bin_state
        self.tm = thing_model
        self.device_name = device_name
        self.door_state_timeout_s = door_state_timeout_s
        self.weight_timeout_s = weight_timeout_s
        self._cycle_lock = threading.Lock()

    def handle(self, params: dict) -> dict:
        """
        处理平台下发的 openDeliveryDoor 服务调用。

        :param params: {"doorIndex": int, "cosToken": {...}}
        :return: {"accepted": True/False}
        """
        door_index = params.get("doorIndex")
        cos_token = params.get("cosToken") or {}

        if door_index != SerialBridge.SINGLE_DOOR_INDEX:
            logger.error("[投递] 当前仅支持 doorIndex=1，收到 %s", door_index)
            return {"accepted": False}
        if not self._cycle_lock.acquire(blocking=False):
            logger.warning("[投递] 已有投递流程进行中，拒绝重复开盖")
            return {"accepted": False}

        logger.info("[投递] 开门 doorIndex=%d", door_index)
        try:
            result = execute_delivery_cycle(
                door_index=door_index,
                cos_token=cos_token,
                serial=self.serial,
                camera=self.camera,
                uploader=self.uploader,
                device_name=self.device_name,
                door_state_timeout_s=self.door_state_timeout_s,
                weight_timeout_s=self.weight_timeout_s,
            )
        finally:
            self._cycle_lock.release()

        if result is None:
            return {"accepted": False}

        self.tm.notify_delivery_complete(
            door_index=door_index,
            weight=result["weight"],
            photo_open_outside=result.get("photoOpenOutside", ""),
            photo_open_inside=result.get("photoOpenInside", ""),
            photo_close_outside=result.get("photoCloseOutside", ""),
            photo_close_inside=result.get("photoCloseInside", ""),
        )

        logger.info("[投递] 完成 doorIndex=%d weight=%.2fkg", door_index, result["weight"])
        return {"accepted": True}
