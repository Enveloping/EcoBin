# -*- coding: utf-8 -*-
"""
mqtt_gateway.py — MQTT 网关：连接 OneNet、鉴权、订阅、发布、消息路由。

从原 main.py 的 SmartBinGateway 中提取 MQTT 通信职责为独立类。
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import quote
from typing import Callable

import paho.mqtt.client as mqtt

from config import TOKEN_VERSION, TOKEN_TTL_SECONDS, TOKEN_METHOD

logger = logging.getLogger("mqtt")


def build_token(product_id: str, dev_name: str, access_key: str) -> str:
    """生成 OneNet 设备连接 Token（用作 MQTT Password）。"""
    method = TOKEN_METHOD
    res = f"products/{product_id}/devices/{dev_name}"
    et = str(int(time.time()) + TOKEN_TTL_SECONDS)
    for_sign = f"{et}\n{method}\n{res}\n{TOKEN_VERSION}"

    key = base64.b64decode(access_key)
    hash_algo = hashlib.sha1 if method == "sha1" else hashlib.sha256
    sign_bytes = hmac.new(key, for_sign.encode("utf-8"), hash_algo).digest()
    sign = base64.b64encode(sign_bytes).decode("utf-8")

    return (
        f"version={TOKEN_VERSION}"
        f"&res={quote(res, safe='')}"
        f"&et={et}"
        f"&method={method}"
        f"&sign={quote(sign, safe='')}"
    )


class MqttGateway:
    """OneNet MQTT 网关 —— 管理 MQTT 连接和消息收发。"""

    def __init__(
        self,
        product_id: str,
        device_name: str,
        device_key: str,
        mqtt_host: str = "studio-mqtt.heclouds.com",
        mqtt_port: int = 1883,
    ):
        self.product_id = product_id
        self.device_name = device_name
        self.device_key = device_key
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self._connected = False

        # ── MQTT 客户端 ──
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=device_name,
            clean_session=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # ── 外部注入回调 ──
        # on_service_call(svc_id, params, msg_id) → reply_data
        self.on_service_call: Callable = None
        # on_property_get(prop_names) → reply_data dict
        self.on_property_get: Callable = None
        # on_property_set(params) → ret_code (200=ok, 100=partial)
        self.on_property_set: Callable = None
        # on_connected callback
        self.on_connected: Callable = None

        self._topic_prefix = f"$sys/{product_id}/{device_name}/thing"

    # ═══════════════════════════════════════════════════════════
    #  连接管理
    # ═══════════════════════════════════════════════════════════

    def connect(self) -> bool:
        """生成 Token 并连接 OneNet MQTT。"""
        token = build_token(self.product_id, self.device_name, self.device_key)
        self.client.username_pw_set(self.product_id, token)
        logger.info("MQTT token 已生成 (有效期%d秒)", TOKEN_TTL_SECONDS)
        try:
            self.client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
            return True
        except Exception as e:
            logger.error("MQTT 连接失败: %s", e)
            return False

    def loop_forever(self):
        """进入 MQTT 事件循环（阻塞）。"""
        self.client.loop_forever()

    def disconnect(self):
        """断开 MQTT 连接。"""
        try:
            self.client.disconnect()
        except Exception:
            pass

    @property
    def connected(self) -> bool:
        return self._connected

    # ═══════════════════════════════════════════════════════════
    #  发布
    # ═══════════════════════════════════════════════════════════

    def _publish(self, topic: str, body: dict):
        payload = json.dumps(body, ensure_ascii=False)
        self.client.publish(topic, payload, qos=0)
        logger.debug("SEND topic=%s payload=%s", topic, payload[:200])

    def post_property(self, prop_data: dict, timeout_ms: int = 5000) -> int:
        """主动上报设备属性到平台。"""
        topic = f"{self._topic_prefix}/property/post"
        self._publish(
            topic,
            {
                "id": str(int(time.time() * 1000)),
                "version": "1.0",
                "params": prop_data,
            },
        )
        return 0

    def post_event(self, event_data: dict, timeout_ms: int = 5000) -> int:
        """主动上报设备事件到平台。"""
        topic = f"{self._topic_prefix}/event/post"
        self._publish(
            topic,
            {
                "id": str(int(time.time() * 1000)),
                "version": "1.0",
                "params": event_data,
            },
        )
        return 0

    def reply(self, action: str, msg_id: str, code: int, data=None):
        """向平台发送通用回复。"""
        reply_action = action + "_reply"
        body = {"id": msg_id, "code": code}
        if data is not None:
            body["data"] = data
        self._publish(f"{self._topic_prefix}{reply_action}", body)

    # ═══════════════════════════════════════════════════════════
    #  MQTT 回调
    # ═══════════════════════════════════════════════════════════

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("MQTT 已连接 OneNet: device=%s", self.device_name)
            topic = f"{self._topic_prefix}/#"
            client.subscribe(topic, qos=0)
            logger.info("已订阅: %s", topic)
            self._connected = True
            if self.on_connected:
                self.on_connected()
        else:
            logger.error(
                "MQTT 连接被拒 reason_code=%s（检查 DEVICE_KEY / Token 是否过期）",
                reason_code,
            )

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("MQTT 正常断开")
        else:
            logger.warning("MQTT 异常断开 reason_code=%s，paho 将自动重连", reason_code)
        self._connected = False

    def _on_message(self, client, userdata, msg):
        """下行消息路由：属性查询/设置 → 服务调用。"""
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            logger.warning("无法解析的下行报文 topic=%s payload=%r", topic, msg.payload)
            return

        prefix = self._topic_prefix
        action = topic[len(prefix) :] if topic.startswith(prefix) else topic
        msg_id = payload.get("id", str(int(time.time() * 1000)))

        logger.info("RECV action=%s id=%s", action, msg_id)

        # ── 属性获取 ──
        if action == "/property/get":
            reply_data = {}
            if self.on_property_get:
                prop_names = payload.get("params", [])
                for name in prop_names:
                    if isinstance(name, str):
                        try:
                            reply_data[name] = self.on_property_get(name)
                        except Exception as e:
                            logger.error("读取属性 %s 失败: %s", name, e)
            self.reply(action, msg_id, 200, reply_data)

        # ── 属性设置 ──
        elif action == "/property/set":
            ret_code = 200
            if self.on_property_set:
                params = payload.get("params", {})
                for name, value in params.items():
                    actual = (
                        value.get("value", value) if isinstance(value, dict) else value
                    )
                    try:
                        self.on_property_set(name, actual)
                    except Exception as e:
                        logger.error("写入属性 %s 失败: %s", name, e)
                        ret_code = 100
            self.reply(action, msg_id, ret_code)

        # ── 服务调用 ──
        elif "/service/" in action and "/invoke" in action:
            parts = action.split("/")
            svc_id = parts[2] if len(parts) > 2 else ""
            params = payload.get("params", {})

            reply_topic = f"{prefix}/service/{svc_id}/invoke_reply"
            if self.on_service_call:
                try:
                    out_data = self.on_service_call(svc_id, params, msg_id)
                    self._publish(
                        reply_topic,
                        {"id": msg_id, "code": 200, "data": out_data},
                    )
                except Exception as e:
                    logger.error("服务 %s 调用失败: %s", svc_id, e)
                    self._publish(reply_topic, {"id": msg_id, "code": 100})
            else:
                self._publish(reply_topic, {"id": msg_id, "code": 404})

        # ── 服务回复（无需处理） ──
        elif "/invoke_reply" in action or "/reply" in action:
            pass
