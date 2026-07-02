# -*- coding: utf-8 -*-
"""
delivery_handler.py — 投递开门服务处理器。

收到平台 openDeliveryDoor 指令后，执行完整硬件闭环（见 door_flow），
完成后上报 deliveryComplete 事件。
"""

import logging

from door_flow import execute_door_cycle
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
    ):
        self.serial = serial
        self.camera = camera
        self.uploader = uploader
        self.bin_state = bin_state
        self.tm = thing_model
        self.device_name = device_name

    def handle(self, params: dict) -> dict:
        """
        处理平台下发的 openDeliveryDoor 服务调用。

        :param params: {"doorIndex": int, "cosToken": {...}}
        :return: {"accepted": True/False}
        """
        door_index = params.get("doorIndex", 0)
        cos_token = params.get("cosToken") or {}

        logger.info("[投递] 开门 doorIndex=%d", door_index)

        result = execute_door_cycle(
            door_index=door_index,
            cos_token=cos_token,
            serial=self.serial,
            camera=self.camera,
            uploader=self.uploader,
            bin_state=self.bin_state,
            business_prefix="delivery",
            device_name=self.device_name,
        )

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
