# -*- coding: utf-8 -*-
"""
EcoBin 设备配置模块 —— 所有配置从环境变量读取，带默认 fallback。

使用方法:
    from config import PRODUCT_ID, DEVICE_NAME, DEVICE_KEY
    # 或按需导入单项

环境变量:
    ECOBIN_PRODUCT_ID     — OneNet 产品 ID（默认: Y4o5C0FXYP）
    ECOBIN_DEVICE_NAME    — 设备名称 = biz_device.sn（默认: Smartbin_1）
    ECOBIN_DEVICE_KEY     — 设备密钥 Base64（必填，无默认值）
    ECOBIN_MQTT_HOST      — MQTT 服务器地址（默认: mqtts.heclouds.com）
    ECOBIN_MQTT_PORT      — MQTT 端口（默认: 1883）
    ECOBIN_SERIAL_PORT    — 串口设备路径（默认: /dev/ttyS3）
    ECOBIN_SERIAL_BAUDRATE— 串口波特率（默认: 115200）
"""

import os
import logging

logger = logging.getLogger("config")

# ── OneNet 设备凭证 ──
PRODUCT_ID = os.getenv("ECOBIN_PRODUCT_ID", "Y4o5C0FXYP")
DEVICE_NAME = os.getenv("ECOBIN_DEVICE_NAME", "Smartbin_1")
DEVICE_KEY = os.getenv("ECOBIN_DEVICE_KEY", "")

# ── MQTT ──
MQTT_HOST = os.getenv("ECOBIN_MQTT_HOST", "mqtts.heclouds.com")
MQTT_PORT = int(os.getenv("ECOBIN_MQTT_PORT", "1883"))

# ── Token 参数 ──
TOKEN_VERSION = "2018-10-31"
TOKEN_TTL_SECONDS = 7 * 24 * 3600
TOKEN_METHOD = "sha256"

# ── 串口 ──
SERIAL_PORT = os.getenv("ECOBIN_SERIAL_PORT", "/dev/ttyS3")
SERIAL_BAUDRATE = int(os.getenv("ECOBIN_SERIAL_BAUDRATE", "115200"))

# ── 摄像头 ──
CAMERA_OUTSIDE = int(os.getenv("ECOBIN_CAMERA_OUTSIDE", "0"))
CAMERA_INSIDE = int(os.getenv("ECOBIN_CAMERA_INSIDE", "1"))

# ── 是否为默认凭证（联调/测试环境标记） ──
_DEFAULTS = {
    "ECOBIN_PRODUCT_ID": "Y4o5C0FXYP",
    "ECOBIN_DEVICE_NAME": "Smartbin_1",
    "ECOBIN_DEVICE_KEY": "",
}
_REQUIRED = ["ECOBIN_DEVICE_KEY"]


def validate():
    """检查是否在用默认凭证，打 warning；DEVICE_KEY 缺失则报 error。"""
    for env_var, default_val in _DEFAULTS.items():
        current = os.getenv(env_var, default_val)
        if env_var in _REQUIRED and not current:
            logger.error(
                "%s 未设置！正式运行必须通过环境变量提供", env_var
            )
        elif current == default_val:
            logger.warning(
                "%s 使用默认值（未设置环境变量），正式部署请通过环境变量覆盖",
                env_var,
            )
