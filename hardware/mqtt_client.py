"""mqtt_client.py —— QoS 1 MQTT 客户端，SQLite 驱动事件中继。

相比旧 mqtt_gateway.py 的关键变化：
  - QoS 1 替代 QoS 0
  - OneNet MQTT 连接默认使用 clean_session=True
  - 事件从 SQLite event_outbox 发送
  - 入站命令持久化到 SQLite command_inbox
  - 启动时从 SQLite 恢复未发送事件
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json as _json
import logging
import threading
import time
from urllib.parse import quote
from typing import Callable, Optional

import paho.mqtt.client as mqtt
from onenet_wire import (
    decode_service_command,
    encode_command_receipt,
    encode_event_post,
    validate_command_envelope,
)

logger = logging.getLogger("mqtt-client")

ONENET_TOKEN_VERSION = "2018-10-31"
ONENET_TOKEN_TTL = 7 * 24 * 3600
ONENET_TOKEN_METHOD = "sha256"

# ── OneNet Token ──

def _build_onenet_token(product_id: str, device_name: str, access_key: str) -> str:
    """生成 OneNet 设备连接 Token（MQTT Password）。"""
    method = ONENET_TOKEN_METHOD
    res = f"products/{product_id}/devices/{device_name}"
    et = str(int(time.time()) + ONENET_TOKEN_TTL)
    for_sign = f"{et}\n{method}\n{res}\n{ONENET_TOKEN_VERSION}"
    key = base64.b64decode(access_key)
    hash_algo = hashlib.sha1 if method == "sha1" else hashlib.sha256
    sign_bytes = hmac.new(key, for_sign.encode("utf-8"), hash_algo).digest()
    sign = base64.b64encode(sign_bytes).decode("utf-8")
    return (
        f"version={ONENET_TOKEN_VERSION}"
        f"&res={quote(res, safe='')}"
        f"&et={et}"
        f"&method={method}"
        f"&sign={quote(sign, safe='')}"
    )

# ── OneNet Topic ──

def _topic_prop_post(pid: str, dn: str) -> str:
    return f"$sys/{pid}/{dn}/thing/property/post"

def _topic_event_post(pid: str, dn: str) -> str:
    return f"$sys/{pid}/{dn}/thing/event/post"


class MqttClient:
    """QoS 1 MQTT 客户端 —— OneNet 连接 + SQLite 驱动可靠事件中继。"""

    def __init__(
        self, product_id: str, device_name: str, device_key: str,
        edge_store, mqtt_host: str = "mqtts.heclouds.com", mqtt_port: int = 1883,
        deployment_code: str = "", edge_boot_id: int = 0,
        clean_session: bool = True,
    ):
        self.product_id = product_id
        self.device_name = device_name
        self.device_key = device_key
        self._store = edge_store
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.deployment_code = deployment_code
        self.edge_boot_id = edge_boot_id
        self.clean_session = clean_session
        self._connected = False
        self._connect_event = threading.Event()
        self._mid_to_event_uid: dict[int, str] = {}
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=device_name,
            clean_session=clean_session,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_publish = self._on_publish
        self.on_command_received: Optional[Callable] = None
        self.on_confirmation_received: Optional[Callable] = None
        self._relay_thread: Optional[threading.Thread] = None
        self._exit_flag = threading.Event()

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        self._connect_event.clear()
        token = _build_onenet_token(self.product_id, self.device_name, self.device_key)
        self.client.username_pw_set(self.product_id, token)
        self.client.connect_async(self.mqtt_host, self.mqtt_port, keepalive=60)
        self.client.loop_start()
        if not self._connect_event.wait(8):
            logger.error("MQTT connect timed out after 8s")
            self.client.loop_stop()
            return False
        self._start_relay_loop()
        return self._connected

    def _real_connect(self) -> None:
        token = _build_onenet_token(self.product_id, self.device_name, self.device_key)
        self.client.username_pw_set(self.product_id, token)
        try:
            self.client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.error("MQTT 连接失败: %s", e)
            self._connect_event.set()

    def disconnect(self) -> None:
        self._exit_flag.set()
        if self._connected:
            self.client.publish(
                _topic_prop_post(self.product_id, self.device_name),
                _json.dumps({"id": "offline", "params": {"online": {"value": False}}}),
                qos=1,
            )
        self.client.disconnect()
        self._connected = False

    def loop_forever(self) -> None:
        if not self._connected:
            self.connect()
        try:
            while not self._exit_flag.is_set():
                self._exit_flag.wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self._exit_flag.set()
            self.client.loop_stop()
            self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        rc_int = self._reason_code_int(reason_code)
        if rc_int == 0:
            self._connected = True
            session_present = self._session_present(flags)
            logger.info("MQTT 已连接: session_present=%s", session_present)
            self._store.save_mqtt_persistent_state(bool(session_present), 0)
            self._subscribe_topics()
            self._publish_online()
            if not session_present:
                self._relay_pending_events()
        else:
            logger.error("MQTT 连接失败: reason_code=%s", reason_code)
            self._connected = False
            try:
                self._store.save_mqtt_persistent_state(False, rc_int)
            except Exception:
                pass
        self._connect_event.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._connected = False
        rc_int = self._reason_code_int(reason_code)
        try:
            self._store.save_mqtt_persistent_state(False, rc_int)
        except Exception:
            pass
        logger.warning("MQTT 断开: reason_code=%s", reason_code)

    def _reason_code_int(self, reason_code) -> int:
        try:
            return int(reason_code)
        except Exception:
            if str(reason_code) == "Success":
                return 0
            return -1

    def _session_present(self, flags) -> bool:
        if isinstance(flags, dict):
            return bool(flags.get("session_present", 0))
        return bool(getattr(flags, "session_present", False))

    def _on_message(self, client, userdata, msg):
        try:
            payload = _json.loads(msg.payload.decode("utf-8"))
            topic = msg.topic
            if "/cmd/request/" in topic:
                self._handle_command(msg.topic, payload)
            elif "/thing/service/" in topic and "/invoke" in topic:
                self._handle_service_call(msg.topic, payload)
            elif "/event/post/reply" in topic or "/cmd/response/" in topic:
                self._handle_confirmation(msg.topic, payload)
        except Exception as e:
            logger.error("消息处理异常: %s", e)

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        logger.debug("PUBACK: mid=%d rc=%s", mid, reason_code)
        event_uid = self._mid_to_event_uid.pop(mid, None)
        if event_uid:
            event = self._store.get_event(event_uid)
            if event and event["event_type"] == "BUSINESS_CONFIRMATION_RECEIPT":
                self._store.mark_control_receipt_published(event_uid)

    def _subscribe_topics(self) -> None:
        pid, dn = self.product_id, self.device_name
        topics = [
            (f"$sys/{pid}/{dn}/cmd/request/+", 1),
            (f"$sys/{pid}/{dn}/thing/service/+/invoke", 1),
            (f"$sys/{pid}/{dn}/thing/property/set", 1),
            (f"$sys/{pid}/{dn}/thing/event/post/reply", 1),
            (f"$sys/{pid}/{dn}/thing/property/post/reply", 1),
            (f"$sys/{pid}/{dn}/cmd/response/+", 1),
        ]
        for topic, qos in topics:
            self.client.subscribe(topic, qos)

    def _publish_online(self) -> None:
        self.publish_property("online", {"value": True, "time": int(time.time() * 1000)})

    def publish_event(self, event_type: str, params: dict) -> Optional[int]:
        pid, dn = self.product_id, self.device_name
        topic = _topic_event_post(pid, dn)
        try:
            payload = encode_event_post(event_type, params)
        except ValueError:
            payload = {"id": params.get("event_uid", str(int(time.time() * 1000))),
                       "version": "1.0", "params": params}
        try:
            info = self.client.publish(topic, _json.dumps(payload), qos=1)
            return info.mid if info.rc == mqtt.MQTT_ERR_SUCCESS else None
        except Exception as e:
            logger.error("事件发布失败: %s", e)
            return None

    def publish_property(self, name: str, value: dict) -> Optional[int]:
        pid, dn = self.product_id, self.device_name
        topic = _topic_prop_post(pid, dn)
        payload = {"id": str(int(time.time() * 1000)), "version": "1.0",
                    "params": {name: value}}
        try:
            info = self.client.publish(topic, _json.dumps(payload), qos=1)
            return info.mid if info.rc == mqtt.MQTT_ERR_SUCCESS else None
        except Exception as e:
            logger.error("属性发布失败: %s", e)
            return None

    def reply_service(self, msg_id: str, svc_id: str, data: dict) -> None:
        pid, dn = self.product_id, self.device_name
        topic = f"$sys/{pid}/{dn}/thing/service/{svc_id}/invoke_reply"
        self.client.publish(
            topic,
            _json.dumps({"id": msg_id, "code": 200, "msg": "accepted", "data": data}),
            qos=1,
        )

    def _handle_command(self, topic: str, payload: dict) -> None:
        cmd_id = payload.get("id", str(int(time.time() * 1000)))
        cmd_type = payload.get("params", {}).get("commandType", "unknown")
        result = self._store.receive_command(cmd_id, cmd_type, payload)
        if result == "ACCEPTED" and self.on_command_received:
            self.on_command_received(cmd_id, cmd_type, payload)

    def _handle_service_call(self, topic: str, payload: dict) -> None:
        svc_id = topic.split("/")[-2] if "/invoke" in topic else ""
        msg_id = payload.get("id", "")
        params = payload.get("params", {})
        command_uid = ""
        try:
            command = decode_service_command(svc_id, params)
            command_uid = command["commandUid"]
            if command.get("deploymentCode") and self.deployment_code:
                if command["deploymentCode"] != self.deployment_code:
                    raise ValueError("deploymentCode mismatch")
            if command["commandType"] == "CONFIRM_EDGE_EVENT":
                result = self._store.receive_business_confirmation_and_create_receipt(
                    command_uid=command_uid,
                    confirmation_payload=command["payload"],
                    deployment_code=self.deployment_code or command.get("deploymentCode", ""),
                )
            else:
                validate_command_envelope(command)
                result = self._store.receive_command(command_uid, command["commandType"], command)
            receipt_state = "DUPLICATE_ACCEPTED" if result == "DUPLICATE" else result
            if receipt_state not in ("ACCEPTED", "DUPLICATE_ACCEPTED"):
                receipt_state = "REJECTED"
            error_code = None
            if result == "CONFLICT":
                error_code = "IDEMPOTENCY_CONFLICT"
            elif result == "REJECTED":
                error_code = "COMMAND_REJECTED"
            self.reply_service(
                msg_id,
                svc_id,
                encode_command_receipt(
                    command_uid,
                    receipt_state,
                    self.edge_boot_id,
                    error_code,
                ),
            )
            if result == "ACCEPTED" and command["commandType"] != "CONFIRM_EDGE_EVENT":
                if self.on_command_received:
                    self.on_command_received(command_uid, command["commandType"], command)
            if result == "ACCEPTED" and command["commandType"] == "CONFIRM_EDGE_EVENT":
                self._relay_pending_events()
        except Exception as e:
            logger.error("服务调用拒绝: %s", e)
            fallback_uid = command_uid or msg_id or str(int(time.time() * 1000))
            self.reply_service(
                msg_id,
                svc_id,
                encode_command_receipt(fallback_uid, "REJECTED", self.edge_boot_id, "BAD_COMMAND"),
            )

    def _handle_confirmation(self, topic: str, payload: dict) -> None:
        if self.on_confirmation_received:
            self.on_confirmation_received(topic, payload)

    def _start_relay_loop(self) -> None:
        if self._relay_thread and self._relay_thread.is_alive():
            return

        def _loop():
            while not self._exit_flag.is_set():
                self._exit_flag.wait(0.5)
                if not self._connected:
                    continue
                self._relay_pending_events()
        self._relay_thread = threading.Thread(target=_loop, daemon=True, name="mqtt-relay")
        self._relay_thread.start()

    def _relay_pending_events(self) -> None:
        events = self._store.list_pending_events(limit=10)
        for evt in events:
            if self._exit_flag.is_set():
                return
            try:
                payload = _json.loads(evt["payload_json"])
                mid = self.publish_event(evt["event_type"], payload)
                if mid:
                    self._mid_to_event_uid[mid] = evt["event_uid"]
                    self._store.mark_event_sending(evt["event_uid"], mid)
                else:
                    self._store.mark_event_pending_retry(evt["event_uid"])
            except Exception as e:
                logger.error("事件中继异常: %s", e)
                self._store.mark_event_pending_retry(evt["event_uid"])
