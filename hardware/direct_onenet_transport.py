"""Direct OneNet MQTT transport for the single-process migration stage.

Only this adapter knows Paho, OneNet topics, device credentials, QoS and MQTT
packet identifiers.  Business validation and durable outbox state live behind
the stable interfaces in separate modules.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from typing import Callable, Optional
from urllib.parse import quote

import paho.mqtt.client as mqtt

from cloud_transport import (
    CloudEvent,
    CloudEventPlatformResult,
    CloudServiceRequest,
    CloudServiceResponse,
)
from onenet_wire import encode_event_post
from trusted_clock import sample_clock


logger = logging.getLogger("direct-onenet-transport")

ONENET_TOKEN_VERSION = "2018-10-31"
ONENET_TOKEN_TTL = 7 * 24 * 3600
ONENET_TOKEN_METHOD = "sha256"
MAX_INBOUND_MESSAGE_BYTES = 256 * 1024


def build_onenet_token(
    product_id: str,
    device_name: str,
    access_key: str,
) -> str:
    """Build the OneNet device connection token (MQTT password)."""

    method = ONENET_TOKEN_METHOD
    resource = f"products/{product_id}/devices/{device_name}"
    expires_at = str(int(time.time()) + ONENET_TOKEN_TTL)
    signing_input = (
        f"{expires_at}\n{method}\n{resource}\n{ONENET_TOKEN_VERSION}"
    )
    key = base64.b64decode(access_key)
    hash_algorithm = (
        hashlib.sha1 if method == "sha1" else hashlib.sha256
    )
    signature_bytes = hmac.new(
        key,
        signing_input.encode("utf-8"),
        hash_algorithm,
    ).digest()
    signature = base64.b64encode(signature_bytes).decode("utf-8")
    return (
        f"version={ONENET_TOKEN_VERSION}"
        f"&res={quote(resource, safe='')}"
        f"&et={expires_at}"
        f"&method={method}"
        f"&sign={quote(signature, safe='')}"
    )


def _topic_property_post(product_id: str, device_name: str) -> str:
    return f"$sys/{product_id}/{device_name}/thing/property/post"


def _topic_event_post(product_id: str, device_name: str) -> str:
    return f"$sys/{product_id}/{device_name}/thing/event/post"


class DirectOneNetTransport:
    """One direct QoS 1 connection implementing the cloud transport seam."""

    def __init__(
        self,
        product_id: str,
        device_name: str,
        device_key: str,
        *,
        mqtt_host: str = "studio-mqtt.heclouds.com",
        mqtt_port: int = 1883,
        clean_session: bool = True,
    ) -> None:
        self._product_id = product_id
        self._device_name = device_name
        self._device_key = device_key
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._clean_session = clean_session

        self._connected = False
        self._connect_event = threading.Event()
        self._connection_lock = threading.RLock()
        self._publish_lock = threading.Lock()
        self._network_loop_started = False
        self._reconnect_required = False
        self._subscription_mid_to_topic: dict[int, str] = {}
        self._mid_to_event_uid: dict[int, str] = {}
        self._exit_flag = threading.Event()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=device_name,
            clean_session=clean_session,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_publish = self._on_publish
        self.client.on_subscribe = self._on_subscribe
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        self.on_service_request: Optional[Callable] = None
        self.on_legacy_command_received: Optional[Callable] = None
        self.on_event_transport_ack: Optional[Callable] = None
        self.on_event_platform_result: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        # Temporary direct-adapter hook that preserves the two legacy
        # EdgeStore transport diagnostics during the first migration stage.
        # It is deliberately not part of CloudTransport.
        self.on_mqtt_state_observed: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        if self._exit_flag.is_set():
            return False
        if self._connected:
            return True
        self._connect_event.clear()
        with self._connection_lock:
            if self._connected:
                return True
            self._configure_credentials()
            try:
                if not self._network_loop_started:
                    self.client.connect_async(
                        self._mqtt_host,
                        self._mqtt_port,
                        keepalive=60,
                    )
                    self._network_loop_started = True
                    self.client.loop_start()
                elif self._reconnect_required:
                    self._reconnect_required = False
                    self.client.reconnect()
            except Exception as error:
                self._reconnect_required = self._network_loop_started
                logger.error(
                    "MQTT connection attempt failed: %s",
                    error,
                )
                return False
        if not self._connect_event.wait(8):
            logger.error("MQTT connect timed out after 8s")
            return False
        return self._connected

    def disconnect(self) -> None:
        self._exit_flag.set()
        with self._connection_lock:
            self._reconnect_required = False
        if self._connected:
            self.client.publish(
                _topic_property_post(
                    self._product_id,
                    self._device_name,
                ),
                json.dumps(
                    {
                        "id": "offline",
                        "params": {"online": {"value": False}},
                    }
                ),
                qos=1,
            )
        self.client.disconnect()
        self._connected = False

    def run_forever(self) -> None:
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
                        retry_seconds = min(retry_seconds * 2, 30)
                        continue
                if self._exit_flag.is_set():
                    break
                self._exit_flag.wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self._exit_flag.set()
            if self._network_loop_started:
                self.client.loop_stop()
            self.client.disconnect()

    def send_event(self, event: CloudEvent) -> bool:
        """Queue an event without exposing Paho's packet identifier."""

        topic = _topic_event_post(
            self._product_id,
            self._device_name,
        )
        try:
            payload = encode_event_post(
                event.event_type,
                event.params,
            )
            # Paho may enter on_publish on its network thread before
            # publish() returns to this thread.  Holding the same lock used
            # by the callback makes that callback wait until the MID has a
            # stable event UID mapping.
            with self._publish_lock:
                info = self.client.publish(
                    topic,
                    json.dumps(payload),
                    qos=1,
                )
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    return False
                self._mid_to_event_uid[info.mid] = event.event_uid
            # This also covers an already-published MQTTMessageInfo returned
            # by a test adapter or an unusual client implementation.  The
            # mapping pop keeps the callback exactly-once within this process.
            is_published = getattr(info, "is_published", None)
            if callable(is_published) and is_published():
                self._deliver_transport_ack(info.mid)
            return True
        except Exception as error:
            logger.error("事件发布失败: %s", error)
            return False

    def publish_property(
        self,
        name: str,
        value: dict,
    ) -> bool:
        """Publish the transport-owned online/offline property."""

        topic = _topic_property_post(
            self._product_id,
            self._device_name,
        )
        payload = {
            "id": str(int(time.time() * 1000)),
            "version": "1.0",
            "params": {name: value},
        }
        try:
            info = self.client.publish(
                topic,
                json.dumps(payload),
                qos=1,
            )
            return info.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as error:
            logger.error("属性发布失败: %s", error)
            return False

    def _configure_credentials(self) -> None:
        token = build_onenet_token(
            self._product_id,
            self._device_name,
            self._device_key,
        )
        self.client.username_pw_set(self._product_id, token)

    def _on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ) -> None:
        reason_code_int = self._reason_code_int(reason_code)
        if reason_code_int == 0:
            self._connected = True
            with self._connection_lock:
                self._reconnect_required = False
            session_present = self._session_present(flags)
            logger.info(
                "MQTT 已连接: session_present=%s",
                session_present,
            )
            self._notify_mqtt_state_observed(session_present, 0)
            self._subscribe_topics()
            self._publish_online()
            callback = self.on_connected
            if callback is not None:
                try:
                    callback()
                except Exception:
                    logger.exception("MQTT connected callback failed")
        else:
            logger.error(
                "MQTT 连接失败: reason_code=%s",
                reason_code,
            )
            self._connected = False
            with self._connection_lock:
                self._reconnect_required = (
                    not self._exit_flag.is_set()
                )
            self._notify_mqtt_state_observed(False, reason_code_int)
            self._notify_disconnected()
        self._connect_event.set()

    def _on_disconnect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ) -> None:
        self._connected = False
        with self._connection_lock:
            self._reconnect_required = not self._exit_flag.is_set()
        reason_code_int = self._reason_code_int(reason_code)
        logger.warning("MQTT 断开: reason_code=%s", reason_code)
        self._notify_mqtt_state_observed(False, reason_code_int)
        self._notify_disconnected()

    def _notify_disconnected(self) -> None:
        callback = self.on_disconnected
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.exception("MQTT disconnect callback failed")

    def _notify_mqtt_state_observed(
        self,
        session_present: bool,
        reason_code: int,
    ) -> None:
        callback = self.on_mqtt_state_observed
        if callback is None:
            return
        try:
            callback(session_present, reason_code)
        except Exception:
            logger.exception("MQTT diagnostic state callback failed")

    def _on_message(self, client, userdata, message) -> None:
        raw_payload = message.payload
        if len(raw_payload) > MAX_INBOUND_MESSAGE_BYTES:
            logger.error(
                "MQTT message rejected: topic=%s bytes=%d exceeds=%d",
                message.topic,
                len(raw_payload),
                MAX_INBOUND_MESSAGE_BYTES,
            )
            return
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("MQTT payload must be a JSON object")
            topic = message.topic
            logger.info(
                "MQTT message received: topic=%s bytes=%d keys=%s",
                topic,
                len(raw_payload),
                sorted(payload.keys()),
            )
            if "/cmd/request/" in topic:
                callback = self.on_legacy_command_received
                if callback is not None:
                    callback(payload)
            elif "/thing/service/" in topic and "/invoke" in topic:
                self._handle_service_call(topic, payload)
            elif "/thing/event/post/reply" in topic:
                self._handle_event_platform_result(payload)
            elif "/cmd/response/" in topic:
                logger.debug("legacy command response received")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            logger.error("MQTT message rejected: %s", error)
        except Exception:
            logger.exception("MQTT message callback failed")

    def _handle_service_call(
        self,
        topic: str,
        payload: dict,
    ) -> None:
        service_id = topic.split("/")[-2] if "/invoke" in topic else ""
        request_id = payload.get("id", "")
        clock = sample_clock()
        request = CloudServiceRequest(
            delivery_id=str(uuid.uuid4()),
            request_id=request_id,
            service_id=service_id,
            params=payload.get("params", {}),
            received_at=clock.occurred_at,
            clock_quality=clock.quality,
        )
        callback = self.on_service_request
        if callback is None:
            logger.error(
                "business handler unavailable for service=%s",
                service_id,
            )
            self._reply_service_unavailable(request_id, service_id)
            return
        try:
            response = callback(request)
        except Exception:
            # Do not reinterpret a possibly persisted command as BAD_COMMAND.
            # A non-success service reply allows the backend to redeliver the
            # same stable command identity safely.
            logger.exception(
                "business handler failed for service=%s",
                service_id,
            )
            self._reply_service_unavailable(request_id, service_id)
            return
        if not isinstance(response, CloudServiceResponse):
            logger.error("business handler returned an invalid response")
            self._reply_service_unavailable(request_id, service_id)
            return
        self._reply_service(request_id, service_id, response.data)
        if response.after_reply is not None:
            try:
                response.after_reply()
            except Exception:
                logger.exception(
                    "business post-reply notification failed"
                )

    def _handle_event_platform_result(self, payload: dict) -> None:
        transport_id = payload.get("id")
        code = payload.get("code")
        if (
            not isinstance(transport_id, str)
            or not transport_id.isdigit()
            or not 1 <= len(transport_id) <= 13
            or not isinstance(code, int)
            or isinstance(code, bool)
        ):
            logger.warning("ignored malformed OneNet event reply")
            return
        callback = self.on_event_platform_result
        if callback is None:
            return
        try:
            callback(
                CloudEventPlatformResult(
                    edge_event_sequence=int(transport_id),
                    code=code,
                )
            )
        except Exception:
            logger.exception(
                "event platform result callback failed"
            )

    def _reply_service(
        self,
        request_id,
        service_id: str,
        data: dict,
    ) -> None:
        topic = (
            f"$sys/{self._product_id}/{self._device_name}"
            f"/thing/service/{service_id}/invoke_reply"
        )
        self.client.publish(
            topic,
            json.dumps(
                {
                    "id": request_id,
                    "code": 200,
                    "msg": "accepted",
                    "data": data,
                }
            ),
            qos=1,
        )

    def _reply_service_unavailable(
        self,
        request_id,
        service_id: str,
    ) -> None:
        topic = (
            f"$sys/{self._product_id}/{self._device_name}"
            f"/thing/service/{service_id}/invoke_reply"
        )
        self.client.publish(
            topic,
            json.dumps(
                {
                    "id": request_id,
                    "code": 503,
                    "msg": "business handler unavailable",
                    "data": {},
                }
            ),
            qos=1,
        )

    def _on_publish(
        self,
        client,
        userdata,
        mid,
        reason_code,
        properties,
    ) -> None:
        logger.debug("PUBACK: mid=%d rc=%s", mid, reason_code)
        self._deliver_transport_ack(mid)

    def _deliver_transport_ack(self, mid: int) -> None:
        with self._publish_lock:
            event_uid = self._mid_to_event_uid.pop(mid, None)
        if event_uid is None:
            return
        callback = self.on_event_transport_ack
        if callback is None:
            return
        try:
            callback(event_uid)
        except Exception:
            logger.exception(
                "event transport acknowledgement callback failed"
            )

    def _on_subscribe(
        self,
        client,
        userdata,
        mid,
        reason_code_list,
        properties,
    ) -> None:
        topic = self._subscription_mid_to_topic.pop(
            mid,
            "<unknown>",
        )
        reason_codes = list(reason_code_list or ())
        rejected = any(
            self._reason_code_int(reason_code) >= 128
            for reason_code in reason_codes
        )
        if rejected:
            logger.error(
                "MQTT subscription rejected: topic=%s reason_codes=%s",
                topic,
                reason_codes,
            )
        else:
            logger.info(
                "MQTT subscription acknowledged: topic=%s",
                topic,
            )

    def _subscribe_topics(self) -> None:
        product_id = self._product_id
        device_name = self._device_name
        topics = [
            (
                f"$sys/{product_id}/{device_name}/cmd/request/+",
                1,
            ),
            (
                f"$sys/{product_id}/{device_name}/thing/#",
                1,
            ),
            (
                f"$sys/{product_id}/{device_name}/cmd/response/+",
                1,
            ),
        ]
        for topic, qos in topics:
            result, mid = self.client.subscribe(topic, qos)
            if result != mqtt.MQTT_ERR_SUCCESS:
                logger.error(
                    "MQTT subscription could not be queued: "
                    "topic=%s rc=%s",
                    topic,
                    result,
                )
                continue
            self._subscription_mid_to_topic[mid] = topic

    def _publish_online(self) -> None:
        self.publish_property(
            "online",
            {
                "value": True,
                "time": int(time.time() * 1000),
            },
        )

    @staticmethod
    def _reason_code_int(reason_code) -> int:
        try:
            return int(reason_code)
        except Exception:
            if str(reason_code) == "Success":
                return 0
            return -1

    @staticmethod
    def _session_present(flags) -> bool:
        if isinstance(flags, dict):
            return bool(flags.get("session_present", 0))
        return bool(getattr(flags, "session_present", False))
