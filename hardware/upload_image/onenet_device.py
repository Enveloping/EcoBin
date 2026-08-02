# -*- coding: utf-8 -*-
"""
本地模拟一台 EcoBin 设备：连 OneNet 上线 + 跑投递下行闭环。

地基（里程碑1）：通过 MQTT 连上 OneNet 并常驻在线——OneNet 下行服务调用「需要设备在线」
才会把命令真正投递到设备（后端 call-service 返回 code=0 仅表示平台受理转发，不代表已达设备）。

本版（里程碑2）：在「在线」之上跑通**投递**全程——
  后端下发 openDeliveryDoor（带 COS 凭证 cosToken）
    → 设备用凭证把固定测试图 test.jpg 直传 COS（4 张：开门前/关门后 × 箱外/箱内）
    → 设备回 invoke_reply{accepted}
    → 设备发 deliveryComplete 事件，回传 4 个图片 URL + 参数
    → 后端「上传后建单」（投递特性：投递完才建单，归属取活跃会话），原样存 URL、按重量返现。
（清运 openCleanDoor 不在本脚本范围。）

连接四元组（OneJSON 设备接入）：
  Host:Port = studio-mqtt.heclouds.com:1883（明文）
  ClientID  = 设备名（DEVICE_NAME，= biz_device.sn）
  Username  = 产品ID（PRODUCT_ID）
  Password  = 连接 token（build_token，见下）

连接 token 与后端 OneNetTokenGenerator.generate 同算法（HmacSHA256），差别：
  res = products/{pid}/devices/{deviceName}、key 用「设备级 key」、version 用设备版 2018-10-31
  （下行 call-service 用的是产品 access_key + 2022-05-01「平台 API 鉴权」，二者不同，勿混）。

MQTT topic（OneJSON）：
  下行服务调用：$sys/{pid}/{dn}/thing/service/{identifier}/invoke（订阅 thing/service/# 兜全）
  服务回执    ：把收到 topic 尾 /invoke 换成 /invoke_reply 发回
  事件上报    ：$sys/{pid}/{dn}/thing/event/post

⚠ 安全：DEVICE_KEY 是设备密钥，量产固件不得内置可导出的密钥到公开渠道。

依赖：paho-mqtt、cos-python-sdk-v5（uv 已装）；上传逻辑复用 cos_upload.upload。
运行：
    uv run onenet_device.py
"""

import json
import logging
import os
import sys
import time
import uuid

import paho.mqtt.client as mqtt

# 允许从父目录 import hardware_layer 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hardware_layer import CosUploader
from mqtt_gateway import build_token

from cos_upload import upload as cos_put_image   # 复用临时密钥直传逻辑，不重写

# ============================================================
# 配置区
# ============================================================
PRODUCT_ID = "tB6NlBWW0V"        # = .env onenetProductId
DEVICE_NAME = "test-divice-1"    # = biz_device.sn / 控制台设备名（沿用文档里的测试设备）
DEVICE_KEY = ""                  # ← 必填：OneNet 控制台「设备详情页」的设备key（base64 串）

TOKEN_TTL_SECONDS = 7 * 24 * 3600   # token 有效期，模拟设备给长一点
# 设备连接 token 用老版 2018-10-31（res=products/{pid}/devices/{dn}）。
# 注意：后端下行 call-service 用的 2022-05-01 是「平台 API 鉴权」，其 res 只支持
# userid/products/projects，不支持设备级资源，二者不可混用（见 docs/references/安全鉴权.md）。
TOKEN_VERSION = "2018-10-31"

USE_TLS = False                  # 预留：True 时走 8883 + tls_set（需 OneNET CA 证书）
MQTT_HOST = "studio-mqtt.heclouds.com"
MQTT_PORT = 1883                 # 明文 1883；TLS 用 studio-mqtts.heclouds.com:8883

# 投递测试参数
TEST_IMAGE = "test.jpg"          # 固定测试图（hardware/test.jpg），上传到 4 个槽位
TEST_WEIGHT = "1.50"             # 模拟投递重量（kg）

# 物模型服务标识（仅处理投递开门；其余打印跳过）
SVC_OPEN_DELIVERY_DOOR = "openDeliveryDoor"
# deliveryComplete 4 张照片槽位 → 事件字段（key 后缀 ↔ 物模型字段，docs §4.1）
PHOTO_SLOTS = [
    ("open_outside", "photoOpenOutside"),    # 开门前·箱外
    ("open_inside", "photoOpenInside"),      # 开门前·箱内
    ("close_outside", "photoCloseOutside"),  # 关门后·箱外
    ("close_inside", "photoCloseInside"),    # 关门后·箱内
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("onenet_device")


# ============================================================
# topic 工具
# ============================================================
def _topic(suffix: str) -> str:
    """拼 $sys/{pid}/{dn}/<suffix> 完整 topic。"""
    return f"$sys/{PRODUCT_ID}/{DEVICE_NAME}/{suffix}"


SERVICE_WILDCARD = _topic("thing/service/#")   # 订阅所有服务调用下行
EVENT_POST_TOPIC = _topic("thing/event/post")  # 事件上报


def _now_id() -> str:
    """13 位以内消息 id（毫秒时间戳）。"""
    return str(int(time.time() * 1000))


# ============================================================
# 服务调用处理：openDeliveryDoor → 上传 4 张 → 回传 deliveryComplete
# ============================================================
def handle_open_delivery_door(client: mqtt.Client, params: dict) -> None:
    """收到投递开门指令：用 cosToken 凭证直传 test.jpg ×4，再发 deliveryComplete 回传 URL+参数。"""
    door_index = params.get("doorIndex")
    cos = params.get("cosToken") or {}
    if not cos.get("tmpSecretId"):
        raise ValueError("cosToken 缺失或无凭证（后端 isConfigured() 为假？走的占位下发）")

    creds = CosUploader.creds_from_cos_token(cos)
    image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), TEST_IMAGE)
    # 投递照片位置由设备自定。路径规范带业务段以区分投递/清运：{sn}/delivery/{唯一串}/<slot>.jpg
    #（清运对应 {sn}/clean/...，由后端 CosTokenClient.buildPhotoKeys 决定；唯一串避免同设备多单覆盖）
    prefix = f"{DEVICE_NAME}/delivery/{int(time.time())}-{uuid.uuid4().hex[:8]}"

    urls = {}
    for slot, field in PHOTO_SLOTS:
        object_key = f"{prefix}/{slot}.jpg"
        urls[field] = cos_put_image(creds, image_path, object_key)

    value = {
        "doorIndex": door_index,
        "weight": float(TEST_WEIGHT),
        **urls,
    }
    publish_event(client, "deliveryComplete", value)
    logger.info("已发 deliveryComplete：doorIndex=%s weight=%s（4 张照片 URL 已回传）", door_index, TEST_WEIGHT)


def publish_event(client: mqtt.Client, identifier: str, value: dict) -> None:
    """发物模型事件（OneJSON）：$sys/{pid}/{dn}/thing/event/post。"""
    body = {
        "id": _now_id(),
        "version": "1.0",
        "params": {identifier: {"value": value, "time": int(time.time() * 1000)}},
    }
    client.publish(EVENT_POST_TOPIC, json.dumps(body, ensure_ascii=False))


def reply_service(client: mqtt.Client, invoke_topic: str, req_id: str, accepted: bool) -> None:
    """回服务调用受理结果：把 invoke topic 尾 /invoke 换成 /invoke_reply。"""
    reply_topic = invoke_topic[: -len("/invoke")] + "/invoke_reply" if invoke_topic.endswith("/invoke") else invoke_topic + "_reply"
    body = {"id": req_id, "code": 200, "data": {"accepted": accepted}}
    client.publish(reply_topic, json.dumps(body, ensure_ascii=False))


# ============================================================
# MQTT 回调
# ============================================================
def on_connect(client, userdata, flags, reason_code, properties=None):
    """连接结果回调：reason_code 成功即设备已上线，随即订阅下行服务调用。"""
    if reason_code == 0:
        logger.info("已连接 OneNet：device=%s 在线", DEVICE_NAME)
        client.subscribe(SERVICE_WILDCARD)
        logger.info("已订阅下行服务调用：%s", SERVICE_WILDCARD)
    else:
        logger.error("连接被拒：device=%s reason_code=%s（检查 DEVICE_KEY / ClientID / Username / token 是否过期）",
                     DEVICE_NAME, reason_code)


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    """断开回调：非 0 表示异常断开，paho 会按 reconnect 策略重连。"""
    if reason_code == 0:
        logger.info("已断开 OneNet：device=%s", DEVICE_NAME)
    else:
        logger.warning("异常断开：device=%s reason_code=%s，等待自动重连", DEVICE_NAME, reason_code)


def on_message(client, userdata, msg):
    """下行服务调用：解 identifier + params，仅处理 openDeliveryDoor。"""
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        logger.warning("收到无法解析的下行报文 topic=%s payload=%r", topic, msg.payload)
        return

    # identifier 取 topic 倒数第二段：.../thing/service/{identifier}/invoke
    parts = topic.split("/")
    identifier = parts[-2] if topic.endswith("/invoke") and len(parts) >= 2 else "?"
    req_id = payload.get("id", _now_id())
    params = payload.get("params") or {}
    logger.info("收到下行服务调用 identifier=%s id=%s params.keys=%s", identifier, req_id, list(params.keys()))

    if identifier != SVC_OPEN_DELIVERY_DOOR:
        logger.info("非投递开门（identifier=%s），跳过", identifier)
        return

    accepted = True
    try:
        handle_open_delivery_door(client, params)
    except Exception as e:
        accepted = False
        logger.error("处理 openDeliveryDoor 失败：%s", e, exc_info=True)
    finally:
        reply_service(client, topic, req_id, accepted)
        logger.info("已回 invoke_reply：accepted=%s", accepted)


def main():
    token = build_token(PRODUCT_ID, DEVICE_NAME, DEVICE_KEY)
    logger.info("准备连接 OneNet：host=%s:%s clientId=%s username(pid)=%s",
                MQTT_HOST, MQTT_PORT, DEVICE_NAME, PRODUCT_ID)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_NAME)
    client.username_pw_set(PRODUCT_ID, token)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    if USE_TLS:
        # 预留：TLS 需 OneNET 平台 CA 根证书（下载放 hardware/，如 onenet_ca.pem）
        # client.tls_set(ca_certs="onenet_ca.pem")
        raise NotImplementedError("USE_TLS 暂未启用：请下载 OneNET CA 证书并启用 tls_set，再把 Host/Port 改 mqttstls.heclouds.com:8883")

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    logger.info("进入保活循环（Ctrl+C 退出）——paho 自动 PINGREQ 维持在线")
    client.loop_forever()


if __name__ == "__main__":
    main()
