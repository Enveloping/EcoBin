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
    ECOBIN_MCU_PROTOCOL   — MCU 协议模式（默认: fixed-frame；
                            可选 uart-v1，仅保留原 UART 1.0 实现）
    ECOBIN_UART_PORT_COUNT— 设备端口数（fixed-frame 默认: 1；
                            uart-v1 默认: 6）
    ECOBIN_UART_HIL_REQUIRED_CAPABILITIES
                          — 可选 HIL 能力位覆盖；未设置时使用 Registry 基线 0x300
    ECOBIN_DOOR_STATE_TIMEOUT— 等待 MCU 开关盖状态秒数（默认: 5）
    ECOBIN_DELIVERY_WEIGHT_TIMEOUT— 等待投递重量秒数（默认: 120）
    ECOBIN_DEVICE_CONFIG_PATH— 设备持久化配置路径
    ECOBIN_DATA_DIR       — 持久数据目录（默认: data/）
    ECOBIN_EDGE_STORE_PATH— SQLite 数据库路径（默认: data/edge.db）
    ECOBIN_EDGE_BOOT_ID   — 边缘启动 ID 持久文件（默认: data/edge-boot-id）
    ECOBIN_DEPLOYMENT_CODE— 后端下发的当前部署编码
    ECOBIN_COS_REGION     — 当前环境 COS 地域（公开配置）
    ECOBIN_COS_BUCKET_NAME— 当前环境 COS 桶名称（公开配置）
    ECOBIN_COS_BASE_URL   — 当前环境 COS HTTPS 根 URL（公开配置）
    ECOBIN_COS_REQUEST_TIMEOUT_SECONDS
                          — COS SDK 网络超时秒数（默认: 15）
    ECOBIN_CAMERA_OUTSIDE — 外部摄像头 V4L2 稳定路径或 simulated:// 源
                            （当前真机: DECXIN）
    ECOBIN_CAMERA_INSIDE  — 内部摄像头 V4L2 稳定路径或 simulated:// 源
                            （当前真机: icspring）
    ECOBIN_CAMERA_WARMUP_FRAMES
                          — 摄像头打开后读取的预热帧数（默认: 5）
    ECOBIN_PHOTO_UPLOAD_POLL_SECONDS
                          — 照片上传队列轮询秒数（默认: 1）
    ECOBIN_PHOTO_GRANT_EXPIRY_SKEW_SECONDS
                          — STS 到期安全余量秒数（默认: 30）
    ECOBIN_PHOTO_RETENTION_HOURS
                          — 照片失败后永久缺失期限小时数（默认: 72）
"""

import os
import logging
from simulated_camera import is_simulated_camera_source

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*a, **kw): pass

# 自动加载项目根目录的 .env 文件（已存在则覆盖系统环境变量）
load_dotenv(override=True)

logger = logging.getLogger("config")


def _first_environment_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return ""

# ── OneNet 设备凭证 ──
PRODUCT_ID = os.getenv("ECOBIN_PRODUCT_ID", "")
DEVICE_NAME = os.getenv("ECOBIN_DEVICE_NAME", "")
DEVICE_KEY = os.getenv("ECOBIN_DEVICE_KEY", "")
DEPLOYMENT_CODE = os.getenv("ECOBIN_DEPLOYMENT_CODE", "")

# ── COS 可信公开环境 ──
# 同时兼容项目根 .env 使用的 Spring 风格名称；永久密钥不会在设备侧读取。
COS_REGION = _first_environment_value(
    "ECOBIN_COS_REGION",
    "cosRegion",
)
COS_BUCKET_NAME = _first_environment_value(
    "ECOBIN_COS_BUCKET_NAME",
    "cosBucketName",
)
COS_BASE_URL = _first_environment_value(
    "ECOBIN_COS_BASE_URL",
    "cosBaseUrl",
)
COS_REQUEST_TIMEOUT_SECONDS = int(os.getenv(
    "ECOBIN_COS_REQUEST_TIMEOUT_SECONDS",
    "15",
))
TRUSTED_COS_ENVIRONMENT = {
    "bucket": COS_BUCKET_NAME,
    "region": COS_REGION,
    "baseUrl": COS_BASE_URL,
}

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
MCU_PROTOCOL_MODE = os.getenv(
    "ECOBIN_MCU_PROTOCOL",
    "fixed-frame",
).strip().lower()
UART_PORT_COUNT = int(os.getenv(
    "ECOBIN_UART_PORT_COUNT",
    "1" if MCU_PROTOCOL_MODE == "fixed-frame" else "6",
))
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
CAMERA_OUTSIDE_SOURCE = os.getenv(
    "ECOBIN_CAMERA_OUTSIDE",
    (
        "/dev/v4l/by-id/"
        "usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0"
    ),
).strip()
CAMERA_INSIDE_SOURCE = os.getenv(
    "ECOBIN_CAMERA_INSIDE",
    "/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0",
).strip()
CAMERA_WARMUP_FRAMES = int(os.getenv(
    "ECOBIN_CAMERA_WARMUP_FRAMES",
    "5",
))

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
PHOTO_UPLOAD_POLL_SECONDS = float(os.getenv(
    "ECOBIN_PHOTO_UPLOAD_POLL_SECONDS",
    "1",
))
PHOTO_GRANT_EXPIRY_SKEW_SECONDS = int(os.getenv(
    "ECOBIN_PHOTO_GRANT_EXPIRY_SKEW_SECONDS",
    "30",
))
PHOTO_RETENTION_HOURS = int(os.getenv(
    "ECOBIN_PHOTO_RETENTION_HOURS",
    "72",
))

# ── 凭证校验 ──
_REQUIRED = [
    "ECOBIN_PRODUCT_ID",
    "ECOBIN_DEVICE_NAME",
    "ECOBIN_DEVICE_KEY",
    "ECOBIN_DEPLOYMENT_CODE",
]


def validate():
    """检查必填环境变量是否已设置，缺失则报 error。"""
    if MCU_PROTOCOL_MODE not in {"fixed-frame", "uart-v1"}:
        raise ValueError(
            "ECOBIN_MCU_PROTOCOL must be fixed-frame or uart-v1"
        )
    camera_sources = (
        CAMERA_OUTSIDE_SOURCE,
        CAMERA_INSIDE_SOURCE,
    )
    if not all(camera_sources):
        raise ValueError("camera device sources must not be empty")
    if CAMERA_OUTSIDE_SOURCE == CAMERA_INSIDE_SOURCE:
        raise ValueError("outside and inside cameras must be different")
    if not all(
        source.startswith("/dev/v4l/by-id/")
        or is_simulated_camera_source(source)
        for source in camera_sources
    ):
        raise ValueError(
            "camera sources must use stable /dev/v4l/by-id paths "
            "or explicit simulated:// names"
        )
    if CAMERA_WARMUP_FRAMES <= 0:
        raise ValueError("camera warmup frames must be positive")
    if COS_REQUEST_TIMEOUT_SECONDS <= 0:
        raise ValueError("COS request timeout must be positive")
    if PHOTO_UPLOAD_POLL_SECONDS <= 0:
        raise ValueError("photo upload poll interval must be positive")
    if PHOTO_GRANT_EXPIRY_SKEW_SECONDS < 0:
        raise ValueError("photo grant expiry skew must be non-negative")
    if PHOTO_RETENTION_HOURS <= 0:
        raise ValueError("photo retention hours must be positive")
    if not all(TRUSTED_COS_ENVIRONMENT.values()):
        raise ValueError(
            "trusted COS bucket, region and base URL must be configured"
        )
    expected_cos_base_url = (
        f"https://{COS_BUCKET_NAME}.cos.{COS_REGION}.myqcloud.com"
    )
    if COS_BASE_URL != expected_cos_base_url:
        raise ValueError(
            "configured COS base URL differs from bucket and region"
        )
    for env_var in _REQUIRED:
        if not os.getenv(env_var):
            logger.error(
                "%s 未设置！请复制 .env.example 为 .env 并填入真实凭证", env_var
            )
    # 确保 DATA_DIR 存在
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EDGE_PHOTO_DIR, exist_ok=True)
