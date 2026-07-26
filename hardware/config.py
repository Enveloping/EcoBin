# -*- coding: utf-8 -*-
"""
EcoBin 设备配置模块 —— 所有配置从环境变量读取，优先 .env 文件。

使用方法:
    from config import PRODUCT_ID, DEVICE_NAME, DEVICE_KEY
    # 或按需导入单项

配置优先级: .env 文件 > 系统环境变量 > 默认值
复制 .env.example 为 .env 并填入真实凭证即可使用。

环境变量:
    ECOBIN_PRODUCT_ID     — OneNet 产品 ID
    ECOBIN_DEVICE_NAME    — 设备名称 = biz_device.sn
    ECOBIN_DEVICE_KEY     — 设备密钥 Base64（必填，无默认值）
    ECOBIN_MQTT_HOST      — MQTT 服务器地址（默认: mqtts.heclouds.com）
    ECOBIN_MQTT_PORT      — MQTT 端口（默认: 1883）
    ECOBIN_SERIAL_PORT    — 串口设备路径（默认: /dev/ttyS5）
    ECOBIN_SERIAL_BAUDRATE— 串口波特率（默认: 115200）
    ECOBIN_UART_PORT_COUNT— UART 握手端口数（默认: 6）
    ECOBIN_UART_HIL_REQUIRED_CAPABILITIES
                          — 可选 HIL 能力位覆盖；未设置时使用 Registry 基线 0x300
    ECOBIN_DOOR_STATE_TIMEOUT— 等待 MCU 开关盖状态秒数（默认: 5）
    ECOBIN_DELIVERY_WEIGHT_TIMEOUT— 等待投递重量秒数（默认: 120）
    ECOBIN_DEVICE_CONFIG_PATH— 设备持久化配置路径
    ECOBIN_TEST_MODE      — 测试模式开关（true/1/yes 开启，默认: false）
                            开启后所有 MCU/硬件数据均为模拟，无需实际硬件连接
    ECOBIN_DATA_DIR       — 持久数据目录（默认: data/）
    ECOBIN_EDGE_STORE_PATH— SQLite 数据库路径（默认: data/edge.db）
    ECOBIN_EDGE_BOOT_ID   — 边缘启动 ID 持久文件（默认: data/edge-boot-id）
    ECOBIN_DEPLOYMENT_CODE— 后端下发的当前部署编码
"""

import os
import logging

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*a, **kw): pass

# 自动加载项目根目录的 .env 文件（已存在则覆盖系统环境变量）
load_dotenv(override=True)

logger = logging.getLogger("config")

# ── OneNet 设备凭证 ──
PRODUCT_ID = os.getenv("ECOBIN_PRODUCT_ID", "")
DEVICE_NAME = os.getenv("ECOBIN_DEVICE_NAME", "")
DEVICE_KEY = os.getenv("ECOBIN_DEVICE_KEY", "")
DEPLOYMENT_CODE = os.getenv("ECOBIN_DEPLOYMENT_CODE", "")

# ── MQTT ──
MQTT_HOST = os.getenv("ECOBIN_MQTT_HOST", "mqtts.heclouds.com")
MQTT_PORT = int(os.getenv("ECOBIN_MQTT_PORT", "1883"))
MQTT_CLEAN_SESSION = os.getenv("ECOBIN_MQTT_CLEAN_SESSION", "true").lower() in (
    "true",
    "1",
    "yes",
)

# ── Token 参数 ──
TOKEN_VERSION = "2018-10-31"
TOKEN_TTL_SECONDS = 7 * 24 * 3600
TOKEN_METHOD = "sha256"

# ── 串口 ──
SERIAL_PORT = os.getenv("ECOBIN_SERIAL_PORT", "/dev/ttyS5")
SERIAL_BAUDRATE = int(os.getenv("ECOBIN_SERIAL_BAUDRATE", "115200"))
UART_PORT_COUNT = int(os.getenv("ECOBIN_UART_PORT_COUNT", "6"))
_uart_hil_required_capabilities = os.getenv(
    "ECOBIN_UART_HIL_REQUIRED_CAPABILITIES",
    "",
).strip()
UART_HIL_REQUIRED_CAPABILITIES = (
    int(_uart_hil_required_capabilities, 0)
    if _uart_hil_required_capabilities
    else None
)
DOOR_STATE_TIMEOUT = float(os.getenv("ECOBIN_DOOR_STATE_TIMEOUT", "5"))
DELIVERY_WEIGHT_TIMEOUT = float(os.getenv("ECOBIN_DELIVERY_WEIGHT_TIMEOUT", "120"))
_device_config_path = os.getenv("ECOBIN_DEVICE_CONFIG_PATH", "data/device-config.json")
DEVICE_CONFIG_PATH = (
    _device_config_path
    if os.path.isabs(_device_config_path)
    else os.path.join(os.path.dirname(__file__), _device_config_path)
)

# ── 摄像头 ──
CAMERA_OUTSIDE = int(os.getenv("ECOBIN_CAMERA_OUTSIDE", "0"))
CAMERA_INSIDE = int(os.getenv("ECOBIN_CAMERA_INSIDE", "1"))

# ── 测试模式 ──
TEST_MODE = os.getenv("ECOBIN_TEST_MODE", "false").lower() in ("true", "1", "yes")

# ── 边缘持久存储 ──
_data_dir = os.getenv("ECOBIN_DATA_DIR", "data")
_project_root = os.path.dirname(__file__)
DATA_DIR = _data_dir if os.path.isabs(_data_dir) else os.path.join(_project_root, _data_dir)
EDGE_STORE_PATH = os.getenv(
    "ECOBIN_EDGE_STORE_PATH",
    os.path.join(DATA_DIR, "edge.db"),
)
EDGE_BOOT_ID_PATH = os.getenv(
    "ECOBIN_EDGE_BOOT_ID_PATH",
    os.path.join(DATA_DIR, "edge-boot-id"),
)
EDGE_PHOTO_DIR = os.path.join(DATA_DIR, "photos")
EDGE_FAULT_DIR = os.path.join(DATA_DIR, "faults")
EDGE_RUNTIME_SNAPSHOT_INTERVAL_S = int(os.getenv("ECOBIN_RUNTIME_SNAPSHOT_INTERVAL_S", "300"))

# ── 凭证校验 ──
_REQUIRED = ["ECOBIN_PRODUCT_ID", "ECOBIN_DEVICE_NAME", "ECOBIN_DEVICE_KEY"]


def validate():
    """检查必填环境变量是否已设置，缺失则报 error。"""
    for env_var in _REQUIRED:
        if not os.getenv(env_var):
            logger.error(
                "%s 未设置！请复制 .env.example 为 .env 并填入真实凭证", env_var
            )
    # 确保 DATA_DIR 存在
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EDGE_PHOTO_DIR, exist_ok=True)
