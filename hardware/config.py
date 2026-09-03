# -*- coding: utf-8 -*-
"""
EcoBin 设备配置模块 —— 普通配置读取环境变量，设备密钥读取注册凭证。

使用方法:
    from config import PRODUCT_ID, DEVICE_NAME, DEVICE_KEY
    # 或按需导入单项

OneNet 迁移优先级: 完整旧进程环境三项 > 注册凭证；禁止部分混用。
生产默认只读取 systemd 注入的环境；项目 .env 仅限显式 development 模式。

环境变量:
    ECOBIN_CONFIG_MODE     — production（默认）或 development
    ECOBIN_DOTENV_PATH     — 仅 development 使用的显式 dotenv 路径
    ECOBIN_CLOUD_TRANSPORT_MODE — direct（旧直连）或 local-proxy（永久代理）
    ECOBIN_DEVICE_CREDENTIALS_PATH — 注册后 0600 凭证文件路径
    ECOBIN_BUSINESS_IDENTITY_PATH — 业务进程的 0600 非秘密设备身份
    ECOBIN_PRODUCT_ID     — OneNet 产品 ID
    ECOBIN_DEVICE_NAME    — 设备名称 = biz_device.sn
    ECOBIN_DEVICE_KEY     — 设备密钥 Base64（必填，无默认值）
    ECOBIN_MQTT_HOST      — OneNET Studio MQTT 地址（默认: studio-mqtt.heclouds.com）
    ECOBIN_MQTT_PORT      — MQTT 端口（默认: 1883）
    ECOBIN_SERIAL_PORT    — 串口设备路径（默认: /dev/ttyS5）
    ECOBIN_SERIAL_BAUDRATE— 串口波特率（默认: 115200）
    ECOBIN_MCU_PROTOCOL   — MCU 协议模式（默认: fixed-frame；
                            可选 uart-v1，仅保留原 UART 1.0 实现）
    ECOBIN_MCU_SIMULATED  — 当前串口对端是否为模拟器（默认: false；
                            使用 PTY 模拟器时必须显式设为 true）
    ECOBIN_DEVICE_CAPABILITIES_PATH
                          — 出厂验收写入的设备能力事实文件
    ECOBIN_MCU_UPDATE_ENABLED— 仅 development 模式使用的升级能力开关；
                            production 只信任设备能力事实文件
    ECOBIN_MCU_BOOT0_WPI  — 香橙派连接 MCU BOOT0 的 WiringOP wPi 编号
    ECOBIN_MCU_RESET_WPI  — 香橙派连接 MCU NRST 的 WiringOP wPi 编号
    ECOBIN_MCU_HARDWARE_COMPATIBILITY— 本机主板兼容标识
    ECOBIN_MCU_SIGNING_PUBLIC_KEYS_DIR— Ed25519 发布公钥目录
    ECOBIN_MCU_FIRMWARE_CACHE_DIR— 已验证固件包和镜像缓存目录
    ECOBIN_STM32FLASH_PATH— stm32flash 可执行文件绝对路径
    ECOBIN_GPIO_PATH      — WiringOP gpio 可执行文件绝对路径
    ECOBIN_UART_PORT_COUNT— 设备端口数（fixed-frame 默认: 1；
                            uart-v1 默认: 6）
    ECOBIN_UART_HIL_REQUIRED_CAPABILITIES
                          — 可选 HIL 能力位覆盖；未设置时使用 Registry 基线 0x300
    ECOBIN_DOOR_STATE_TIMEOUT— 等待 MCU 开关盖状态秒数（默认: 5）
    ECOBIN_DELIVERY_WEIGHT_TIMEOUT— 等待投递重量秒数（默认: 120）
    ECOBIN_DEVICE_ENTRY_URL_REFRESH_SECONDS
                          — 固定帧 MCU 二维码 URL 重发周期秒数（默认: 60）
    ECOBIN_DEVICE_CONFIG_PATH— 设备持久化配置路径
    ECOBIN_DATA_DIR       — 持久数据目录（默认: data/）
    ECOBIN_EDGE_STORE_PATH— SQLite 数据库路径（默认: data/edge.db）
    ECOBIN_EDGE_BOOT_ID_PATH
                          — 边缘启动 ID 持久文件（默认: data/edge-boot-id）
    ECOBIN_COS_REGION     — 当前环境 COS 地域（公开配置）
    ECOBIN_COS_BUCKET_NAME— 当前环境 COS 桶名称（公开配置）
    ECOBIN_COS_BASE_URL   — 当前环境 COS HTTPS 根 URL（公开配置）
    ECOBIN_COS_REQUEST_TIMEOUT_SECONDS
                          — COS SDK 网络超时秒数（默认: 15）
    ECOBIN_CAMERA_OUTSIDE — 外部摄像头 V4L2 稳定路径或 simulated:// 源
                            （当前真机: DECXIN）
    ECOBIN_CAMERA_INSIDE  — 内部摄像头 V4L2 稳定路径或 simulated:// 源
                            （当前真机: icspring）
    ECOBIN_PHOTO_UPLOAD_POLL_SECONDS
                          — 照片上传队列轮询秒数（默认: 1）
    ECOBIN_PHOTO_GRANT_EXPIRY_SKEW_SECONDS
                          — STS 到期安全余量秒数（默认: 30）
    ECOBIN_PHOTO_RETENTION_HOURS
                          — 照片失败后永久缺失期限小时数（默认: 72）
"""

import json
import logging
import math
import os
import re
from pathlib import Path
from simulated_camera import is_simulated_camera_source


_CONFIG_DIRECTORY = Path(__file__).resolve().parent


def _configure_environment_source(
    config_directory: str | os.PathLike[str] | None = None,
) -> str:
    """Select the production environment or an explicit development .env.

    Production is deliberately the default.  A release-local ``.env`` would
    make mutable files in ``/opt`` override the systemd EnvironmentFile, so its
    presence is treated as a packaging/configuration error.  Development must
    opt in through the process environment; the opt-in cannot live inside the
    file which has not yet been read.
    """

    mode = os.getenv("ECOBIN_CONFIG_MODE", "production").strip().lower()
    directory = Path(config_directory or _CONFIG_DIRECTORY).resolve()
    release_dotenv = directory / ".env"

    if mode == "production":
        if os.path.lexists(release_dotenv):
            raise RuntimeError(
                "production runtime refuses a code-directory .env; "
                "use /etc/ecobin/hardware.env through systemd"
            )
        return mode

    if mode != "development":
        raise ValueError(
            "ECOBIN_CONFIG_MODE must be production or development"
        )

    dotenv_value = os.getenv("ECOBIN_DOTENV_PATH", "").strip()
    dotenv_path = Path(dotenv_value) if dotenv_value else release_dotenv
    if not dotenv_path.is_absolute():
        dotenv_path = directory / dotenv_path
    dotenv_path = dotenv_path.resolve()
    if not dotenv_path.is_file():
        raise FileNotFoundError(
            f"development dotenv file does not exist: {dotenv_path}"
        )

    try:
        from dotenv import load_dotenv
    except ImportError as error:
        raise RuntimeError(
            "development mode requires the python-dotenv dependency"
        ) from error
    if not load_dotenv(dotenv_path=dotenv_path, override=False):
        raise RuntimeError(f"failed to load development dotenv: {dotenv_path}")
    return mode


CONFIG_MODE = _configure_environment_source()
CLOUD_TRANSPORT_MODE = os.getenv(
    "ECOBIN_CLOUD_TRANSPORT_MODE",
    "direct",
).strip().lower()
if CLOUD_TRANSPORT_MODE not in {"direct", "local-proxy"}:
    raise ValueError(
        "ECOBIN_CLOUD_TRANSPORT_MODE must be direct or local-proxy"
    )

logger = logging.getLogger("config")


def _require_path_under(name: str, value: str, root: str) -> None:
    path = Path(value)
    expected_root = Path(root)
    if not path.is_absolute() or not path.resolve(strict=False).is_relative_to(
        expected_root
    ):
        raise ValueError(f"{name} must be an absolute path under {root}")


def _first_environment_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return ""

# ── 注册后设备凭证 ──
DEVICE_CREDENTIALS_PATH = os.getenv(
    "ECOBIN_DEVICE_CREDENTIALS_PATH",
    "/etc/ecobin/device-credentials.json",
).strip()
BUSINESS_IDENTITY_PATH = os.getenv(
    "ECOBIN_BUSINESS_IDENTITY_PATH",
    "/var/lib/ecobin/business/device-identity.json",
).strip()
BUSINESS_IDENTITY = None
if CLOUD_TRANSPORT_MODE == "local-proxy":
    # Import lazily so historical direct-only runtime inventories remain
    # bootable.  The proxy business process never opens the root enrollment
    # bundle and never receives a OneNet key in memory.
    from business_identity import load_business_identity

    BUSINESS_IDENTITY = load_business_identity(BUSINESS_IDENTITY_PATH)
    DEVICE_CREDENTIALS = None
    PRODUCT_ID = ""
    DEVICE_NAME = BUSINESS_IDENTITY.device_name
    DEVICE_KEY = ""
    _default_mqtt_host = "studio-mqtt.heclouds.com"
    _default_mqtt_port = 1883
else:
    # Device credentials belong only to the legacy direct-OneNet posture.
    # Keeping this import inside that branch lets replaceable proxy-only
    # business packages omit all credential parsing code.
    from device_credentials import (
        credentials_path_from_environment,
        effective_onenet_credentials,
        load_device_credentials,
    )

    DEVICE_CREDENTIALS_PATH = credentials_path_from_environment()
    DEVICE_CREDENTIALS = load_device_credentials(DEVICE_CREDENTIALS_PATH)
    _onenet_credentials = effective_onenet_credentials(DEVICE_CREDENTIALS)
    PRODUCT_ID = _onenet_credentials.product_id
    DEVICE_NAME = _onenet_credentials.device_name
    DEVICE_KEY = _onenet_credentials.device_key
    _default_mqtt_host = _onenet_credentials.mqtt_host
    _default_mqtt_port = _onenet_credentials.mqtt_port

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
MQTT_HOST = os.getenv(
    "ECOBIN_MQTT_HOST",
    _default_mqtt_host,
)
MQTT_PORT = int(os.getenv(
    "ECOBIN_MQTT_PORT",
    str(_default_mqtt_port),
))
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
_mcu_simulated_raw = os.getenv(
    "ECOBIN_MCU_SIMULATED",
    "false",
).strip().lower()
MCU_SIMULATED = _mcu_simulated_raw in {"true", "1", "yes"}
DEVICE_CAPABILITIES_PATH = os.getenv(
    "ECOBIN_DEVICE_CAPABILITIES_PATH",
    "/var/lib/ecobin/device-capabilities.json",
).strip()
DEVICE_CAPABILITIES_SCHEMA_VERSION = 1


def _load_device_capabilities(path_value: str) -> dict:
    """Read the factory capability fact used by the production runtime.

    Unknown fields are rejected so a typo or partially written factory result
    cannot silently enable a physical MCU update path.  First boot owns atomic
    creation of this file; this reader never repairs or rewrites it.
    """

    path = Path(path_value)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(
            f"device capability file does not exist: {path}"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"device capability file is unreadable: {path}"
        ) from error
    required = {
        "schemaVersion",
        "mcuRemoteUpdateCapable",
        "factoryReportSha256",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("device capability fields are invalid")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != DEVICE_CAPABILITIES_SCHEMA_VERSION
    ):
        raise ValueError("device capability schema version is unsupported")
    if not isinstance(document["mcuRemoteUpdateCapable"], bool):
        raise ValueError("mcuRemoteUpdateCapable must be boolean")
    report_sha256 = document["factoryReportSha256"]
    if not isinstance(report_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", report_sha256
    ):
        raise ValueError("factoryReportSha256 is invalid")
    return document


_mcu_update_enabled_raw = os.getenv(
    "ECOBIN_MCU_UPDATE_ENABLED",
    "false",
).strip().lower()
DEVICE_CAPABILITIES = None
DEVICE_CAPABILITIES_ERROR = None
if CONFIG_MODE == "production":
    try:
        DEVICE_CAPABILITIES = _load_device_capabilities(
            DEVICE_CAPABILITIES_PATH
        )
    except ValueError as error:
        # Importing configuration remains side-effect free. validate() fails
        # closed before the runtime opens UART, MQTT, GPIO or firmware files.
        DEVICE_CAPABILITIES_ERROR = str(error)
    MCU_REMOTE_UPDATE_CAPABLE = bool(
        DEVICE_CAPABILITIES
        and DEVICE_CAPABILITIES["mcuRemoteUpdateCapable"]
    )
else:
    MCU_REMOTE_UPDATE_CAPABLE = _mcu_update_enabled_raw in {
        "true", "1", "yes",
    }
# Once the permanent proxy is selected, MCU installation belongs to the
# permanent updater.  The business runtime still reports the board capability
# but must not instantiate its historical root-owned flasher.
MCU_UPDATE_ENABLED = (
    MCU_REMOTE_UPDATE_CAPABLE and CLOUD_TRANSPORT_MODE == "direct"
)
_mcu_boot0_wpi_raw = os.getenv("ECOBIN_MCU_BOOT0_WPI", "2").strip()
_mcu_reset_wpi_raw = os.getenv("ECOBIN_MCU_RESET_WPI", "5").strip()
MCU_BOOT0_WPI = int(_mcu_boot0_wpi_raw) if _mcu_boot0_wpi_raw else None
MCU_RESET_WPI = int(_mcu_reset_wpi_raw) if _mcu_reset_wpi_raw else None
MCU_BOOT0_ACTIVE_LEVEL = int(os.getenv(
    "ECOBIN_MCU_BOOT0_ACTIVE_LEVEL",
    "1",
))
MCU_RESET_ACTIVE_LEVEL = int(os.getenv(
    "ECOBIN_MCU_RESET_ACTIVE_LEVEL",
    "1",
))
MCU_HARDWARE_COMPATIBILITY = os.getenv(
    "ECOBIN_MCU_HARDWARE_COMPATIBILITY",
    "ECOBIN_MAINBOARD_V1.1",
).strip()
MCU_SIGNING_PUBLIC_KEYS_DIR = os.getenv(
    "ECOBIN_MCU_SIGNING_PUBLIC_KEYS_DIR",
    "/etc/ecobin/mcu-release-keys",
).strip()
STM32FLASH_PATH = os.getenv(
    "ECOBIN_STM32FLASH_PATH",
    "/usr/bin/stm32flash",
).strip()
GPIO_PATH = os.getenv(
    "ECOBIN_GPIO_PATH",
    "/usr/bin/gpio",
).strip()
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
DEVICE_ENTRY_URL_REFRESH_SECONDS = float(os.getenv(
    "ECOBIN_DEVICE_ENTRY_URL_REFRESH_SECONDS",
    "60",
))
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
# ── 边缘持久存储 ──
_data_dir = os.getenv("ECOBIN_DATA_DIR", "data")
_project_root = str(_CONFIG_DIRECTORY)
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
_mcu_firmware_cache_dir = os.getenv(
    "ECOBIN_MCU_FIRMWARE_CACHE_DIR",
    os.path.join(DATA_DIR, "mcu-firmware"),
)
MCU_FIRMWARE_CACHE_DIR = (
    _mcu_firmware_cache_dir
    if os.path.isabs(_mcu_firmware_cache_dir)
    else os.path.join(_project_root, _mcu_firmware_cache_dir)
)
REMOTE_SUPPORT_CONTROL_SOCKET = os.getenv(
    "ECOBIN_REMOTE_SUPPORT_SOCKET",
    "/run/ecobin/remote-support/control.sock",
)
# 尚无已应用平台配置时只采用固定的一小时默认值；正式周期来自平台
# applyConfiguration，避免环境变量形成未受平台审计的单设备覆盖。
EDGE_RUNTIME_SNAPSHOT_INTERVAL_S = 3600.0
EDGE_SOFTWARE_VERSION = os.getenv(
    "ECOBIN_EDGE_VERSION",
    "0.1.0",
).strip()
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
def validate():
    """检查必填环境变量是否已设置，缺失则报 error。"""
    if MCU_PROTOCOL_MODE not in {"fixed-frame", "uart-v1"}:
        raise ValueError(
            "ECOBIN_MCU_PROTOCOL must be fixed-frame or uart-v1"
        )
    if _mcu_simulated_raw not in {
        "true", "1", "yes", "false", "0", "no",
    }:
        raise ValueError(
            "ECOBIN_MCU_SIMULATED must be true or false"
        )
    if (
        CONFIG_MODE == "development"
        and _mcu_update_enabled_raw not in {
            "true", "1", "yes", "false", "0", "no",
        }
    ):
        raise ValueError(
            "ECOBIN_MCU_UPDATE_ENABLED must be true or false"
        )
    if MCU_UPDATE_ENABLED:
        if MCU_PROTOCOL_MODE != "fixed-frame":
            raise ValueError(
                "MCU firmware update requires the fixed-frame protocol"
            )
        if MCU_SIMULATED:
            raise ValueError(
                "MCU firmware update is forbidden for a simulated MCU"
            )
        if SERIAL_BAUDRATE != 115200:
            raise ValueError(
                "MCU application UART must use 115200 baud for updates"
            )
        if MCU_BOOT0_WPI is None or MCU_RESET_WPI is None:
            raise ValueError(
                "MCU BOOT0 and NRST WiringOP pins are required"
            )
        if MCU_BOOT0_WPI < 0 or MCU_RESET_WPI < 0:
            raise ValueError("MCU WiringOP pins must be non-negative")
        if MCU_BOOT0_WPI == MCU_RESET_WPI:
            raise ValueError("MCU BOOT0 and NRST pins must be different")
        if MCU_BOOT0_ACTIVE_LEVEL not in {0, 1}:
            raise ValueError("MCU BOOT0 active level must be 0 or 1")
        if MCU_RESET_ACTIVE_LEVEL not in {0, 1}:
            raise ValueError("MCU NRST active level must be 0 or 1")
        if not MCU_HARDWARE_COMPATIBILITY:
            raise ValueError("MCU hardware compatibility must not be empty")
        for name, path in (
            ("MCU signing public-key directory", MCU_SIGNING_PUBLIC_KEYS_DIR),
            ("stm32flash", STM32FLASH_PATH),
            ("WiringOP gpio", GPIO_PATH),
        ):
            if not path or not os.path.isabs(path):
                raise ValueError(f"{name} path must be absolute")
    if (
        not math.isfinite(DEVICE_ENTRY_URL_REFRESH_SECONDS)
        or DEVICE_ENTRY_URL_REFRESH_SECONDS <= 0
    ):
        raise ValueError(
            "device entry URL refresh interval must be positive"
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
    if COS_REQUEST_TIMEOUT_SECONDS <= 0:
        raise ValueError("COS request timeout must be positive")
    if PHOTO_UPLOAD_POLL_SECONDS <= 0:
        raise ValueError("photo upload poll interval must be positive")
    if PHOTO_GRANT_EXPIRY_SKEW_SECONDS < 0:
        raise ValueError("photo grant expiry skew must be non-negative")
    if PHOTO_RETENTION_HOURS <= 0:
        raise ValueError("photo retention hours must be positive")
    if EDGE_RUNTIME_SNAPSHOT_INTERVAL_S <= 0:
        raise ValueError(
            "runtime snapshot interval must be positive"
        )
    if not EDGE_SOFTWARE_VERSION or len(EDGE_SOFTWARE_VERSION) > 64:
        raise ValueError(
            "edge software version must contain 1..64 characters"
        )
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
    required_identity = [("OneNet deviceName", DEVICE_NAME)]
    if CLOUD_TRANSPORT_MODE == "direct":
        required_identity.extend(
            (
                ("OneNet productId", PRODUCT_ID),
                ("OneNet deviceKey", DEVICE_KEY),
            )
        )
    for field, value in required_identity:
        if not value:
            logger.error(
                "%s 未配置；请完成设备注册或提供完整的旧环境凭证",
                field,
            )
    if CONFIG_MODE == "production":
        if DEVICE_CAPABILITIES_ERROR is not None:
            raise ValueError(DEVICE_CAPABILITIES_ERROR)
        if DEVICE_CAPABILITIES is None:
            raise ValueError("device capability fact is unavailable")
        if DEVICE_CAPABILITIES_PATH != (
            "/var/lib/ecobin/device-capabilities.json"
        ):
            raise ValueError(
                "production device capability path must be "
                "/var/lib/ecobin/device-capabilities.json"
            )
        production_uart = {
            "protocol": MCU_PROTOCOL_MODE,
            "simulated": MCU_SIMULATED,
            "serial_port": SERIAL_PORT,
            "baudrate": SERIAL_BAUDRATE,
            "port_count": UART_PORT_COUNT,
        }
        expected_uart = {
            "protocol": "fixed-frame",
            "simulated": False,
            "serial_port": "/dev/ttyS5",
            "baudrate": 115200,
            "port_count": 1,
        }
        if production_uart != expected_uart:
            raise ValueError(
                "production runtime requires the fixed Orange Pi UART5 "
                "boundary (/dev/ttyS5, 115200, fixed-frame, one real port)"
            )
        if MCU_UPDATE_ENABLED:
            production_gpio = (
                MCU_BOOT0_WPI,
                MCU_RESET_WPI,
                MCU_BOOT0_ACTIVE_LEVEL,
                MCU_RESET_ACTIVE_LEVEL,
            )
            if production_gpio != (2, 5, 1, 1):
                raise ValueError(
                    "production update-capable mainboard requires BOOT0 "
                    "wPi 2 active-high and the 2N7002 reset gate on wPi 5 "
                    "active-high"
                )
            production_tools = (
                GPIO_PATH,
                STM32FLASH_PATH,
                MCU_HARDWARE_COMPATIBILITY,
            )
            if production_tools != (
                "/usr/bin/gpio",
                "/usr/bin/stm32flash",
                "ECOBIN_MAINBOARD_V1.1",
            ):
                raise ValueError(
                    "production update-capable mainboard requires the "
                    "locked WiringOP gpio, stm32flash and "
                    "ECOBIN_MAINBOARD_V1.1 identities"
                )
        if UART_HIL_REQUIRED_CAPABILITIES is not None:
            raise ValueError(
                "production runtime forbids a UART HIL capability override"
            )
        if any(is_simulated_camera_source(source) for source in camera_sources):
            raise ValueError(
                "production runtime forbids simulated camera sources"
            )
        if CLOUD_TRANSPORT_MODE == "direct":
            _require_path_under(
                "device credentials path",
                DEVICE_CREDENTIALS_PATH,
                "/etc/ecobin",
            )
        else:
            if any(
                os.getenv(name)
                for name in (
                    "ECOBIN_PRODUCT_ID",
                    "ECOBIN_DEVICE_NAME",
                    "ECOBIN_DEVICE_KEY",
                )
            ):
                raise ValueError(
                    "local proxy business runtime forbids OneNet credential "
                    "environment variables"
                )
            if BUSINESS_IDENTITY_PATH != (
                "/var/lib/ecobin/business/device-identity.json"
            ):
                raise ValueError(
                    "production local proxy requires the fixed business "
                    "identity path"
                )
        _require_path_under(
            "MCU signing public-key directory",
            MCU_SIGNING_PUBLIC_KEYS_DIR,
            "/etc/ecobin",
        )
        persistent_paths = [
            ("data directory", DATA_DIR),
            ("edge store path", EDGE_STORE_PATH),
            ("edge boot ID path", EDGE_BOOT_ID_PATH),
            ("device configuration path", DEVICE_CONFIG_PATH),
        ]
        if MCU_UPDATE_ENABLED:
            persistent_paths.append(
                ("MCU firmware cache directory", MCU_FIRMWARE_CACHE_DIR)
            )
        for name, path in persistent_paths:
            _require_path_under(name, path, "/var/lib/ecobin")
        if CLOUD_TRANSPORT_MODE == "local-proxy" and (
            DATA_DIR != "/var/lib/ecobin/business"
            or EDGE_STORE_PATH != "/var/lib/ecobin/business/edge.db"
            or EDGE_BOOT_ID_PATH
            != "/var/lib/ecobin/business/edge-boot-id"
            or DEVICE_CONFIG_PATH
            != "/var/lib/ecobin/business/device-config.json"
        ):
            raise ValueError(
                "production local proxy requires the fixed business state paths"
            )
        _require_path_under(
            "remote-support control socket",
            REMOTE_SUPPORT_CONTROL_SOCKET,
            "/run/ecobin",
        )
        _require_path_under("serial port", SERIAL_PORT, "/dev")
    # 确保 DATA_DIR 存在
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EDGE_PHOTO_DIR, exist_ok=True)
    if MCU_UPDATE_ENABLED:
        os.makedirs(MCU_FIRMWARE_CACHE_DIR, mode=0o700, exist_ok=True)
