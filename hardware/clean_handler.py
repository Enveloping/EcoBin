# -*- coding: utf-8 -*-
"""
clean_handler.py — 清运开门服务处理器。

收到平台 openCleanDoor 指令后：
  1. 执行完整硬件闭环（开→拍照→等重量→关→拍照→COS 上传）
  2. 上报 cleanGross 事件（含 4 张照片 URL）
  3. 记录 clean_order_id 到 BinState，供后续异步 tare 检测
"""

import logging

from door_flow import execute_door_cycle
from hardware_layer import SerialBridge, DualCamera, CosUploader, BinState

logger = logging.getLogger("clean")


class CleanHandler:
    """清运开门处理器 —— 依赖注入所有硬件组件。"""

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
        处理平台下发的 openCleanDoor 服务调用。

        :param params: {"doorIndex": int, "cleanOrderId": int, "cosToken": {...}}
        :return: {"accepted": True/False}
        """
        door_index = params.get("doorIndex", 0)
        clean_order_id = params.get("cleanOrderId", 0)
        cos_token = params.get("cosToken") or {}

        logger.info(
            "[清运] 开门 doorIndex=%d cleanOrderId=%d", door_index, clean_order_id
        )

        result = execute_door_cycle(
            door_index=door_index,
            cos_token=cos_token,
            serial=self.serial,
            camera=self.camera,
            uploader=self.uploader,
            bin_state=self.bin_state,
            business_prefix="clean",
            device_name=self.device_name,
        )

        if result is None:
            return {"accepted": False}

        # 上报毛重 + 照片 URL
        self.tm.notify_clean_gross(
            clean_order_id=clean_order_id,
            weight=result["weight"],
            photo_open_outside=result.get("photoOpenOutside", ""),
            photo_open_inside=result.get("photoOpenInside", ""),
            photo_close_outside=result.get("photoCloseOutside", ""),
            photo_close_inside=result.get("photoCloseInside", ""),
        )

        # 记 clean_order_id，供后续异步 tare 检测
        BinState.set_clean_order_id(door_index, clean_order_id)

        logger.info(
            "[清运] 完成 doorIndex=%d cleanOrderId=%d weight=%.2fkg",
            door_index,
            clean_order_id,
            result["weight"],
        )
        return {"accepted": True}


# ── reboot handler（远程重启） ──
def handle_reboot(params: dict) -> dict:
    """远程重启（仅记录日志，不实际执行）。"""
    logger.info("[系统] 收到远程重启指令")
    return {"accepted": True}
