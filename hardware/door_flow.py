# -*- coding: utf-8 -*-
"""
door_flow.py — 投递新协议状态机与旧清运开门闭环。

本模块提供纯函数，不依赖 ThingModel 或 MQTT，方便独立测试。投递已使用
AA/BB/CC/DD 二进制协议；清运暂留旧 D1 文本流程，二者不能视为固件兼容。
"""

import logging
import os
import time
import uuid

from hardware_layer import (
    BinState,
    SerialBridge,
    DualCamera,
    CosUploader,
    PHOTO_DIR,
)

logger = logging.getLogger("door_flow")


def execute_delivery_cycle(
    door_index: int,
    cos_token: dict,
    serial: SerialBridge,
    camera: type,
    uploader: type,
    device_name: str,
    door_state_timeout_s: float,
    weight_timeout_s: float,
) -> dict | None:
    """按新二进制 UART 协议执行单投口投递流程。"""
    if door_index != SerialBridge.SINGLE_DOOR_INDEX:
        logger.error("[delivery] 新协议当前仅支持 doorIndex=1，收到 %s", door_index)
        return None
    if not cos_token.get("tmpSecretId"):
        logger.error("[delivery] cosToken 凭证缺失（tmpSecretId 为空）")
        return None

    open_command_sent = False
    closed_confirmed = False
    try:
        door_version, weight_version = serial.event_versions()
        if not serial.send_door_control(door_index, open_door=True):
            logger.error("[delivery] 串口发送开盖指令失败")
            return None
        open_command_sent = True

        if not serial.wait_for_door_state(True, door_version, door_state_timeout_s):
            logger.error("[delivery] 等待开盖状态超时（%.1fs）", door_state_timeout_s)
            return None

        prefix = os.path.join(PHOTO_DIR, f"door{door_index}_open")
        open_outside_path, open_inside_path = camera.capture_both(prefix)

        weight_grams = serial.wait_for_weight(weight_version, weight_timeout_s)
        if weight_grams is None:
            logger.error("[delivery] 等待本次重量超时（%.1fs）", weight_timeout_s)
            return None

        close_version, _ = serial.event_versions()
        if not serial.send_door_control(door_index, open_door=False):
            logger.error("[delivery] 串口发送关盖指令失败")
            return None
        if not serial.wait_for_door_state(False, close_version, door_state_timeout_s):
            logger.error("[delivery] 等待关盖状态超时（%.1fs）", door_state_timeout_s)
            return None
        closed_confirmed = True

        prefix = os.path.join(PHOTO_DIR, f"door{door_index}_close")
        close_outside_path, close_inside_path = camera.capture_both(prefix)

        creds = uploader.creds_from_cos_token(cos_token)
        upload_prefix = f"{device_name}/delivery/{int(time.time())}-{uuid.uuid4().hex[:8]}"

        def _upload_or_empty(path, slot_name) -> str:
            if path and os.path.exists(path):
                key = f"{upload_prefix}/{slot_name}.jpg"
                return uploader.upload(creds, path, key)
            return ""

        urls = {
            "photoOpenOutside": _upload_or_empty(open_outside_path, "open_outside"),
            "photoOpenInside": _upload_or_empty(open_inside_path, "open_inside"),
            "photoCloseOutside": _upload_or_empty(close_outside_path, "close_outside"),
            "photoCloseInside": _upload_or_empty(close_inside_path, "close_inside"),
        }
        weight = weight_grams / 1000.0
        logger.info("[delivery] 完成 doorIndex=%d weight=%.3fkg", door_index, weight)
        return {"weight": weight, **urls}
    except Exception:
        logger.exception("[delivery] 投递硬件流程异常")
        return None
    finally:
        if open_command_sent and not closed_confirmed:
            if serial.send_door_control(door_index, open_door=False):
                logger.warning("[delivery] 流程失败，已额外发送一次安全关盖指令")
            else:
                logger.error("[delivery] 流程失败，安全关盖指令发送失败")


def execute_door_cycle(
    door_index: int,
    cos_token: dict,
    serial: SerialBridge,
    camera: type,
    uploader: type,
    bin_state: type,
    business_prefix: str,
    device_name: str,
) -> dict | None:
    """
    执行旧清运流程的开门→拍照→等重量→关门→拍照→COS 上传周期。

    注意：该函数仍使用旧 D1 文本协议，未适配新版 MCU 固件；新投递流程使用
    execute_delivery_cycle()。

    :param door_index:       舱门编号（1-6）
    :param cos_token:        平台下发的 COS 临时凭证 dict
    :param serial:           SerialBridge 实例（单元测试可显式传入替身）
    :param camera:           DualCamera 类
    :param uploader:         CosUploader 类
    :param bin_state:        BinState 类
    :param business_prefix:  COS 路径业务前缀（"delivery" / "clean"）
    :param device_name:      设备 SN，COS 路径前缀
    :return:                 {weight, photoOpenOutside, photoOpenInside,
                              photoCloseOutside, photoCloseInside}
                             失败返回 None
    """
    # ====== 前置检查: cosToken 凭证完整性 ======
    if not cos_token.get("tmpSecretId"):
        logger.error("[%s] cosToken 凭证缺失（tmpSecretId 为空）", business_prefix)
        return None

    # ====== Step 1: 串口开门 ======
    if serial:
        if not serial.send_cmd(door_index, "open"):
            logger.error("[%s] 串口发送开门指令失败", business_prefix)
            return None

    # ====== Step 2: 拍开门前照片 ======
    prefix = os.path.join(PHOTO_DIR, f"door{door_index}_open")
    open_outside_path, open_inside_path = camera.capture_both(prefix)

    # ====== Step 3: 等待重量稳定 ======
    weight = wait_for_weight(bin_state, door_index, timeout_s=15.0)

    # ====== Step 4: 串口关门 ======
    if serial:
        serial.send_cmd(door_index, "close")
    time.sleep(1.0)

    # ====== Step 5: 拍关门后照片 ======
    prefix = os.path.join(PHOTO_DIR, f"door{door_index}_close")
    close_outside_path, close_inside_path = camera.capture_both(prefix)

    # ====== Step 6: 上传 4 张照片到 COS ======
    creds = uploader.creds_from_cos_token(cos_token)
    upload_prefix = f"{device_name}/{business_prefix}/{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def _upload_or_empty(path, slot_name) -> str:
        if path and os.path.exists(path):
            key = f"{upload_prefix}/{slot_name}.jpg"
            return uploader.upload(creds, path, key)
        return ""

    urls = {
        "photoOpenOutside": _upload_or_empty(open_outside_path, "open_outside"),
        "photoOpenInside": _upload_or_empty(open_inside_path, "open_inside"),
        "photoCloseOutside": _upload_or_empty(close_outside_path, "close_outside"),
        "photoCloseInside": _upload_or_empty(close_inside_path, "close_inside"),
    }

    logger.info("[%s] 完成 doorIndex=%d weight=%.2fkg", business_prefix, door_index, weight)
    return {"weight": weight, **urls}


def wait_for_weight(bin_state: type, door_index: int, timeout_s: float = 15.0) -> float:
    """
    轮询 BinState 等待重量数据稳定（阻塞调用，最长 timeout_s 秒）。

    稳定判定：连续两次读数差异 < 200g。超时返回最后读数。
    """
    start = time.time()
    last_weight = 0.0
    while time.time() - start < timeout_s:
        state = bin_state.get_door_state(door_index)
        w = state.get("weight", 0.0)
        if w > 0:
            last_weight = w
            time.sleep(1.0)
            state2 = bin_state.get_door_state(door_index)
            w2 = state2.get("weight", 0.0)
            if abs(w2 - w) < 0.2:
                return w2
        time.sleep(0.2)
    logger.warning("[door_flow] 等待重量超时（%.1fs），使用最后读数: %.2fkg", timeout_s, last_weight)
    return last_weight
