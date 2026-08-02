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
        trusted_cos_environment=None,
        unsupported_command_types=None,
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
        self._trusted_cos_environment = trusted_cos_environment
        self._unsupported_command_types = frozenset(
            unsupported_command_types or ()
        )
        self._connected = False
        self._connect_event = threading.Event()
        self._connection_lock = threading.RLock()
        self._network_loop_started = False
        self._reconnect_required = False
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
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.on_command_received: Optional[Callable] = None
        self.on_confirmation_received: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self._relay_thread: Optional[threading.Thread] = None
        self._exit_flag = threading.Event()

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        if self._exit_flag.is_set():
            return False
        if self._connected:
            self._start_relay_loop()
            return True
        self._connect_event.clear()
        with self._connection_lock:
            if self._connected:
                self._start_relay_loop()
                return True
            self._configure_credentials()
            try:
                if not self._network_loop_started:
                    self.client.connect_async(
                        self.mqtt_host,
                        self.mqtt_port,
                        keepalive=60,
                    )
                    self._network_loop_started = True
                    self.client.loop_start()
                elif self._reconnect_required:
                    self._reconnect_required = False
                    self.client.reconnect()
            except Exception as error:
                self._reconnect_required = self._network_loop_started
                logger.error("MQTT connection attempt failed: %s", error)
                return False
        if not self._connect_event.wait(8):
            logger.error("MQTT connect timed out after 8s")
            return False
        if self._connected:
            self._start_relay_loop()
        return self._connected

    def _configure_credentials(self) -> None:
        token = _build_onenet_token(self.product_id, self.device_name, self.device_key)
        self.client.username_pw_set(self.product_id, token)

    def disconnect(self) -> None:
        self._exit_flag.set()
        with self._connection_lock:
            self._reconnect_required = False
        if self._connected:
            self.client.publish(
                _topic_prop_post(self.product_id, self.device_name),
                _json.dumps({"id": "offline", "params": {"online": {"value": False}}}),
                qos=1,
            )
        self.client.disconnect()
        self._connected = False

    def loop_forever(self) -> None:
        retry_seconds = 1
        try:
            while not self._exit_flag.is_set():
                if not self._connected:
                    if self.connect():
                        retry_seconds = 1
                    else:
                        logger.warning(
                            "MQTT will retry connection in %ds",
                            retry_seconds,
                        )
                        self._exit_flag.wait(retry_seconds)
                        retry_seconds = min(
                            retry_seconds * 2,
                            30,
                        )
                        continue
                if self._exit_flag.is_set():
                    break
                self._exit_flag.wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self._exit_flag.set()
            if getattr(self, "_network_loop_started", True):
                self.client.loop_stop()
            self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        rc_int = self._reason_code_int(reason_code)
        if rc_int == 0:
            self._connected = True
            with self._connection_lock:
                self._reconnect_required = False
            session_present = self._session_present(flags)
            logger.info("MQTT 已连接: session_present=%s", session_present)
            self._store.save_mqtt_persistent_state(bool(session_present), 0)
            self._subscribe_topics()
            self._publish_online()
            try:
                fault = self._store.get_active_edge_fault(
                    "NETWORK",
                    "NETWORK_CONNECTIVITY",
                )
                if fault is not None and self.deployment_code:
                    self._store.recover_fault_and_create_event(
                        deployment_code=self.deployment_code,
                        fault_uid=fault["fault_uid"],
                        component="NETWORK",
                        fault_code="NETWORK_CONNECTIVITY",
                        port_no=fault["port_no"],
                        recovery_evidence="MQTT_CONNECTED",
                    )
            except Exception:
                logger.exception(
                    "failed to persist MQTT recovery event"
                )
            if not session_present:
                self._relay_pending_events()
            self._start_relay_loop()
            if self.on_connected is not None:
                try:
                    self.on_connected()
                except Exception:
                    logger.exception(
                        "MQTT reconnect snapshot callback failed"
                    )
        else:
            logger.error("MQTT 连接失败: reason_code=%s", reason_code)
            self._connected = False
            with self._connection_lock:
                self._reconnect_required = not self._exit_flag.is_set()
            try:
                self._store.save_mqtt_persistent_state(False, rc_int)
            except Exception:
                pass
        self._connect_event.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._connected = False
        with self._connection_lock:
            self._reconnect_required = not self._exit_flag.is_set()
        rc_int = self._reason_code_int(reason_code)
        try:
            self._store.save_mqtt_persistent_state(False, rc_int)
            self._store.recover_sending_events()
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
            elif event:
                self._store.mark_event_pending_retry(event_uid)

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
            validate_command_envelope(
                command,
                trusted_environment=(
                    self._trusted_cos_environment
                ),
            )
            rejection_error = None
            if (
                command["commandType"]
                in self._unsupported_command_types
            ):
                rejection_error = "MCU_FEATURE_NOT_SUPPORTED"
                result = self._store.receive_rejected_command(
                    command,
                    rejection_error,
                )
            elif command["commandType"] == "CONFIRM_EDGE_EVENT":
                result = self._store.receive_business_confirmation_and_create_receipt(
                    deployment_code=self.deployment_code or command.get("deploymentCode", ""),
                    command=command,
                )
            else:
                result = self._store.receive_command(command_uid, command["commandType"], command)
            control_dispatched = False
            control_error = None
            if (
                result in ("ACCEPTED", "DUPLICATE")
                and command["commandType"]
                == "PROVIDE_PHOTO_UPLOAD_GRANT"
            ):
                control_dispatched = True
                accepted = bool(
                    self.on_command_received
                    and self.on_command_received(
                        command_uid,
                        command["commandType"],
                        command,
                    )
                )
                if not accepted:
                    result = "REJECTED"
                    control_error = "PHOTO_GRANT_REQUEST_NOT_PENDING"
            receipt_state = "DUPLICATE_ACCEPTED" if result == "DUPLICATE" else result
            if receipt_state not in ("ACCEPTED", "DUPLICATE_ACCEPTED"):
                receipt_state = "REJECTED"
            error_code = None
            if result == "CONFLICT":
                error_code = "IDEMPOTENCY_CONFLICT"
            elif result == "REJECTED":
                error_code = (
                    rejection_error
                    or control_error
                    or "COMMAND_REJECTED"
                )
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
            should_dispatch = result == "ACCEPTED" or (
                result == "DUPLICATE"
                and command["commandType"]
                == "PROVIDE_PHOTO_UPLOAD_GRANT"
            )
            if (
                should_dispatch
                and command["commandType"] != "CONFIRM_EDGE_EVENT"
                and not control_dispatched
            ):
                if self.on_command_received:
                    self.on_command_received(command_uid, command["commandType"], command)
            if (
                result in ("ACCEPTED", "DUPLICATE")
                and command["commandType"] == "CONFIRM_EDGE_EVENT"
            ):
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
        if "/thing/event/post/reply" in topic:
            transport_id = payload.get("id")
            code = payload.get("code")
            if (
                isinstance(transport_id, str)
                and transport_id.isdigit()
                and 1 <= len(transport_id) <= 13
                and isinstance(code, int)
                and not isinstance(code, bool)
            ):
                event_uid = self._store.record_event_platform_reply(
                    int(transport_id),
                    code,
                )
                if event_uid and code not in (0, 200):
                    logger.error(
                        "OneNet event rejected: event=%s code=%d",
                        event_uid,
                        code,
                    )
                elif event_uid:
                    logger.debug(
                        "OneNet event accepted: event=%s",
                        event_uid,
                    )
                elif code not in (0, 200):
                    logger.warning(
                        "OneNet telemetry event rejected: id=%s code=%d",
                        transport_id,
                        code,
                    )
            else:
                logger.warning(
                    "ignored malformed OneNet event reply"
                )
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
