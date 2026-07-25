"""uart_link.py —— UART 1.0 串行链路层。

封装 ecobin_uart_protocol 生成代码，提供：
  - 串口 I/O（pyserial）
  - 帧编码/解码
  - ACK/NACK 匹配与重试
  - HELLO 双向握手
  - QUERY_STATE 状态收集
  - 命令发送（投递、清运、配置等）

使用方式:
    link = UartLink(port="/dev/ttyS5", edge_boot_id=1, port_count=6)
    link.open()
    mcu_info = link.handshake()
    snapshots = link.query_state(mcu_command_uid)
    link.close()
"""

from __future__ import annotations

import logging
import hashlib
import struct
import threading
import time
import uuid as _uuid
from collections import deque
from typing import Any, Optional

try:
    import serial
except ImportError:
    serial = None

from uart_protocol import (
    StreamParser,
    encode_frame,
    decode_frame,
    encode_payload,
    decode_payload,
    compute_command_digest,
    validate_payload_semantics,
    crc16_ccitt_false,
    MAGIC,
    MAXIMUM_FRAME_LENGTH,
    ACK_REQUIRED,
    MESSAGE_TYPE,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
)

logger = logging.getLogger("uart-link")

# ── 常量 ──
EDGE_CAPABILITY_BITMAP = 0x1FFF  # 全部 13 个能力位
FIRMWARE_IDENTITY = "orangepi"
FIRMWARE_VERSION = "1.0.0-rc.1"

# ── UART Registry 常量 ──
MSG_HELLO = 1
MSG_HELLO_ACK = 2
MSG_ACK = 3
MSG_NACK = 4
MSG_QUERY_STATE = 5
MSG_SAFE_CLOSE = 6


class UartError(Exception):
    """UART 链路错误。"""
    pass


class UartLink:
    """UART 1.0 串行链路。"""

    def __init__(self, port: str, edge_boot_id: int, port_count: int = 6,
                 baudrate: int = 115200, timeout_s: float = 0.5):
        self.port = port
        self.edge_boot_id = edge_boot_id
        self.port_count = port_count
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self._ser: Optional[serial.Serial] = None
        # Bytes read by this object are sent by the MCU.
        self._parser = StreamParser(sender_role="MCU")
        self._io_lock = threading.RLock()
        self._pending_frames: deque[dict] = deque()
        self._tx_sequence = 0
        self._mcu_boot_id: Optional[int] = None
        self._mcu_capability: int = 0
        self._mcu_firmware_version: str = ""

    # ── 生命周期 ──

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(self) -> bool:
        if serial is None:
            logger.error("pyserial not installed, cannot open UART")
            return False
        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout_s,
            )
            logger.info("UART 串口 %s 打开成功", self.port)
            return True
        except serial.SerialException as e:
            logger.error("UART 串口 %s 打开失败: %s", self.port, e)
            return False

    def close(self) -> None:
        with self._io_lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
                self._ser = None
                logger.info("UART 串口已关闭")

    # ── 帧级收发 ──

    def _next_tx_sequence(self) -> int:
        self._tx_sequence = (self._tx_sequence % 4294967295) + 1
        return self._tx_sequence

    def _send_frame(self, message_name: str, payload: Optional[bytes] = None,
                    ack_required: Optional[bool] = None) -> int:
        """发送一帧，返回 tx_sequence。"""
        if payload is None:
            payload = bytes()
        if not self.is_open:
            raise UartError("UART is not open")
        tx_seq = self._next_tx_sequence()
        frame = encode_frame(message_name, tx_seq, payload)
        if ack_required is not None:
            actual = bool(frame[5] & ACK_REQUIRED)
            if actual != ack_required:
                raise UartError(f"{message_name} ACK flag differs from Registry")
        self._write_raw_frame(frame)
        logger.debug("TX frame msg=%s seq=%d len=%d", message_name, tx_seq, len(frame))
        return tx_seq

    def _write_raw_frame(self, frame: bytes) -> None:
        self._ser.write(frame)
        self._ser.flush()

    def _read_serial_frame(self, timeout_ms: int = 500) -> Optional[dict]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            self._ser.timeout = max(0.01, remaining)
            chunk = self._ser.read(min(256, self._ser.in_waiting or 1))
            if not chunk:
                continue
            now_ms = int(time.time() * 1000)
            frames = self._parser.feed(chunk, now_ms=now_ms)
            if frames:
                normalized_frames = []
                for decoded in frames:
                    message_name = decoded["messageName"]
                    normalized_frames.append({
                        "message_name": message_name,
                        "message_type": decoded["messageType"],
                        "flags": decoded["flags"],
                        "tx_sequence": decoded["txSequence"],
                        "payload": decode_payload(message_name, decoded["payload"]),
                    })
                self._pending_frames.extend(normalized_frames[1:])
                return normalized_frames[0]
        return None

    def _read_frame(self, timeout_ms: int = 500) -> Optional[dict]:
        """Read one normalized MCU frame without competing with command ACK waits."""
        with self._io_lock:
            if self._pending_frames:
                return self._pending_frames.popleft()
            if not self.is_open:
                return None
            return self._read_serial_frame(timeout_ms)

    def _send_and_wait_ack(self, message_name: str, payload: bytes,
                           ack_timeout_ms: int = 500,
                           max_retries: int = 3) -> dict:
        """Send once and retransmit the exact same frame only on ACK timeout."""
        with self._io_lock:
            if not self.is_open:
                return {"acked": False, "error": "UART_CLOSED", "fatal": False}
            tx_seq = self._next_tx_sequence()
            raw_frame = encode_frame(message_name, tx_seq, payload)
            if not raw_frame[5] & ACK_REQUIRED:
                raise UartError(f"{message_name} does not require ACK")

            for attempt in range(1, max_retries + 1):
                self._write_raw_frame(raw_frame)
                if attempt > 1:
                    logger.info(
                        "重试原帧 %s seq=%d (attempt %d/%d)",
                        message_name, tx_seq, attempt, max_retries,
                )
                deadline = time.monotonic() + ack_timeout_ms / 1000.0
                while time.monotonic() < deadline:
                    frame = self._pop_matching_response(
                        tx_seq, MESSAGE_TYPE[message_name]
                    )
                    if frame is None:
                        frame = self._read_serial_frame(
                            timeout_ms=max(1, int((deadline - time.monotonic()) * 1000))
                        )
                    if frame is None:
                        break
                    msg_name = frame.get("message_name", "")
                    payload_dict = frame.get("payload", {})
                    if msg_name not in ("ACK", "NACK"):
                        self._pending_frames.append(frame)
                        continue
                    if not self._ack_matches(
                        frame, tx_seq, MESSAGE_TYPE[message_name]
                    ):
                        logger.debug("丢弃不匹配的旧 ACK/NACK: %s", payload_dict)
                        continue
                    if msg_name == "ACK":
                        return {
                            "acked": True,
                            "disposition": payload_dict.get("disposition", "ACCEPTED"),
                            "tx_sequence": tx_seq,
                        }
                    error_name = payload_dict.get("errorCode", "INTERNAL_FAULT")
                    logger.warning("NACK: %s error=%s", message_name, error_name)
                    return {
                        "acked": False,
                        "error": error_name,
                        "fatal": error_name not in ("BUSY",),
                        "tx_sequence": tx_seq,
                    }
                logger.debug("ACK 超时: %s seq=%d", message_name, tx_seq)
        return {"acked": False, "error": "TIMEOUT", "fatal": False}

    def _pop_matching_response(
        self, tx_sequence: int, message_type: int
    ) -> Optional[dict]:
        for index, frame in enumerate(self._pending_frames):
            if (
                frame.get("message_name") in ("ACK", "NACK")
                and self._ack_matches(frame, tx_sequence, message_type)
            ):
                del self._pending_frames[index]
                return frame
        return None

    def _ack_matches(self, frame: dict, tx_sequence: int, message_type: int) -> bool:
        payload = frame.get("payload", {})
        if payload.get("referencedSenderBootId") != self.edge_boot_id:
            return False
        if payload.get("referencedTxSequence") != tx_sequence:
            return False
        if payload.get("referencedMessageType") != message_type:
            return False
        sender_boot_id = payload.get("senderBootId")
        return self._mcu_boot_id is None or sender_boot_id == self._mcu_boot_id

    # ── HELLO 握手 ──

    def handshake(self) -> dict:
        """执行 HELLO 握手。返回 MCU 信息 dict 或抛出 UartError。"""
        if not self.is_open:
            raise UartError("串口未打开")

        # Step 1: 发送 EDGE HELLO
        hello_payload = encode_payload("HELLO", {
            "senderRole": "EDGE",
            "senderBootId": self.edge_boot_id,
            "supportedMajor": PROTOCOL_MAJOR,
            "minimumMinor": 0,
            "maximumMinor": 0,
            "portCount": self.port_count,
            "capabilityBitmap": EDGE_CAPABILITY_BITMAP,
            "maximumFrameLength": MAXIMUM_FRAME_LENGTH,
            "pendingCriticalEventCount": 0,
            "firmwareIdentity": FIRMWARE_IDENTITY,
            "firmwareVersion": FIRMWARE_VERSION,
        })
        self._send_frame("HELLO", hello_payload)
        logger.info("发送 HELLO: bootId=%d ports=%d capability=0x%X",
                     self.edge_boot_id, self.port_count, EDGE_CAPABILITY_BITMAP)

        # Step 2: 等待 MCU HELLO 响应
        mcu_hello = self._read_frame(timeout_ms=3000)
        if mcu_hello is None:
            raise UartError("MCU HELLO 超时（3 秒无响应）")

        mcu_msg = mcu_hello.get("message_name", "")
        if mcu_msg != "HELLO":
            raise UartError(f"期望 HELLO，收到 {mcu_msg}")

        mcu_payload = mcu_hello.get("payload", {})
        if mcu_payload.get("senderRole") != "MCU":
            raise UartError("MCU HELLO senderRole invalid")
        mcu_boot_id = mcu_payload.get("senderBootId", 0)
        mcu_capability = mcu_payload.get("capabilityBitmap", 0)
        unknown_capabilities = mcu_capability & ~EDGE_CAPABILITY_BITMAP
        missing_capabilities = EDGE_CAPABILITY_BITMAP & ~mcu_capability
        negotiated = EDGE_CAPABILITY_BITMAP & mcu_capability

        logger.info("收到 MCU HELLO: bootId=%d ports=%d capability=0x%X negotiated=0x%X",
                     mcu_boot_id, mcu_payload.get("portCount", 0), mcu_capability, negotiated)

        # Step 3: 发送 HELLO_ACK
        status = (
            "ACCEPTED"
            if not unknown_capabilities and not missing_capabilities
            else "INCOMPATIBLE"
        )
        error_code = 0 if status == "ACCEPTED" else 1  # UNSUPPORTED_VERSION
        ack_payload = encode_payload("HELLO_ACK", {
            "responderBootId": self.edge_boot_id,
            "referencedSenderBootId": mcu_boot_id,
            "selectedMajor": PROTOCOL_MAJOR,
            "selectedMinor": 0,
            "status": status,
            "portCount": self.port_count,
            "capabilityBitmap": negotiated,
            "maximumFrameLength": MAXIMUM_FRAME_LENGTH,
            "errorCode": error_code,
        })
        self._send_frame("HELLO_ACK", ack_payload)
        logger.info("发送 HELLO_ACK: status=%s negotiated=0x%X", status, negotiated)

        if status == "INCOMPATIBLE":
            raise UartError(f"MCU 能力不兼容: edge=0x{EDGE_CAPABILITY_BITMAP:X} mcu=0x{mcu_capability:X}")

        # Step 4: 等待 MCU HELLO_ACK
        mcu_hello_ack = self._read_frame(timeout_ms=3000)
        if mcu_hello_ack is None:
            raise UartError("MCU HELLO_ACK 超时")
        ack_msg = mcu_hello_ack.get("message_name", "")
        ack_payload_dict = mcu_hello_ack.get("payload", {})
        if ack_msg == "HELLO_ACK" and ack_payload_dict.get("status") == "ACCEPTED":
            self._mcu_boot_id = mcu_boot_id
            self._mcu_capability = negotiated
            self._mcu_firmware_version = mcu_payload.get("firmwareVersion", "")
            logger.info("HELLO 握手完成: mcuBoodId=%d capability=0x%X",
                         self._mcu_boot_id, self._mcu_capability)
            return {
                "mcu_boot_id": mcu_boot_id,
                "mcu_capability": negotiated,
                "mcu_port_count": mcu_payload.get("portCount", 0),
                "mcu_firmware_identity": mcu_payload.get("firmwareIdentity", ""),
                "mcu_firmware_version": mcu_payload.get("firmwareVersion", ""),
                "mcu_pending_critical_events": mcu_payload.get("pendingCriticalEventCount", 0),
            }
        raise UartError(f"MCU HELLO_ACK 失败: status={ack_payload_dict.get('status')}")

    # ── QUERY_STATE ──

    def query_state(self, on_segment=None) -> list[dict]:
        """发送 QUERY_STATE 并收集完整 STATE_SNAPSHOT。返回 BEGIN/PORT/END 帧列表。"""
        if not self._mcu_boot_id:
            raise UartError("尚未完成 HELLO 握手")

        command_uid = str(_uuid.uuid4())
        values = {
            "mcuCommandUid": command_uid,
            "commandDigestSha256": bytes(32),
            "snapshotUid": str(_uuid.uuid4()),
        }
        values["commandDigestSha256"] = bytes.fromhex(
            compute_command_digest("QUERY_STATE", values)
        )
        payload = encode_payload("QUERY_STATE", values)
        result = self._send_and_wait_ack("QUERY_STATE", payload)
        if not result["acked"]:
            raise UartError(f"QUERY_STATE 失败: {result.get('error', 'unknown')}")

        # 收集分段快照
        segments: list[dict] = []
        timeout_ms = 10000  # 10 秒总超时
        while True:
            frame = self._read_frame(timeout_ms=timeout_ms)
            if frame is None:
                raise UartError("QUERY_STATE 快照收集超时")
            msg_name = frame.get("message_name", "")
            p = frame.get("payload", {})
            if msg_name in ("STATE_SNAPSHOT_BEGIN", "STATE_SNAPSHOT_PORT", "STATE_SNAPSHOT_END"):
                if on_segment is not None:
                    on_segment(frame)
                segments.append(frame)
                if msg_name == "STATE_SNAPSHOT_END":
                    break
            elif msg_name == "ACK":
                continue  # ACK 重发忽略
            else:
                logger.warning("QUERY_STATE 期间收到意外消息: %s", msg_name)
        return segments

    # ── 命令发送 ──

    def apply_configuration(self, command: dict, part_command_uids: list[str]) -> dict:
        """Send BEGIN/device/ports/COMMIT using stable per-part identities."""
        payload = command["payload"]
        config = payload["config"]
        device_config = payload["deviceConfig"]
        ports = payload["ports"]
        part_count = len(ports) + 3
        if len(part_command_uids) != part_count:
            raise ValueError("configuration part command UID count differs")
        expected_mcu_sha = compute_mcu_payload_sha256(payload)
        if expected_mcu_sha != config["mcuPayloadSha256"]:
            raise ValueError("mcuPayloadSha256 mismatch")

        common = {
            "applicationUid": payload["applicationUid"],
            "configVersion": config["version"],
            "contentSha256": config["contentSha256"],
            "mcuPayloadSha256": config["mcuPayloadSha256"],
        }
        segments: list[tuple[str, dict]] = [
            (
                "CONFIG_BEGIN",
                {
                    **common,
                    "partIndex": 1,
                    "partCount": part_count,
                    "expectedPortCount": len(ports),
                },
            ),
            (
                "CONFIG_DEVICE_BLOCK",
                {
                    **common,
                    "partIndex": 2,
                    "partCount": part_count,
                    **device_config,
                },
            ),
        ]
        port_fields = (
            "portNo", "enabled", "unitPriceTenThousandths", "fullnessMode",
            "configuredFullWeightGrams", "fullnessSettleWaitMs",
            "fullnessConfirmationWaitMs", "weightStableWindowMs",
            "weightMaximumFluctuationGrams", "weightRequiredSampleCount",
            "weightMeasurementTimeoutMs", "weightMinimumGrams",
            "weightMaximumGrams", "calibrationVersion",
            "infraredSampleTimeoutMs", "deliveryDoorOperationTimeoutMs",
        )
        for index, port in enumerate(ports, start=3):
            segments.append((
                "CONFIG_PORT_BLOCK",
                {
                    **common,
                    "partIndex": index,
                    "partCount": part_count,
                    **{field: port[field] for field in port_fields},
                },
            ))
        segments.append((
            "CONFIG_COMMIT",
            {
                **common,
                "partIndex": part_count,
                "partCount": part_count,
            },
        ))

        results = []
        for part_uid, (message_name, values) in zip(part_command_uids, segments):
            command_values = {
                "mcuCommandUid": part_uid,
                "commandDigestSha256": bytes(32),
                **values,
            }
            command_values["commandDigestSha256"] = bytes.fromhex(
                compute_command_digest(message_name, command_values)
            )
            result = self._send_and_wait_ack(
                message_name, encode_payload(message_name, command_values)
            )
            results.append({
                "message_name": message_name,
                "mcu_command_uid": part_uid,
                **result,
            })
            if not result["acked"]:
                return {
                    "acked": False,
                    "error": result.get("error", "UART_FAILURE"),
                    "parts": results,
                }
        return {
            "acked": True,
            "parts": results,
            "commit_mcu_command_uid": part_command_uids[-1],
        }

    def send_authorize_delivery_first_open(
        self, session_uid: str, port_no: int,
        preopen_measurement_uid: str,
        parent_start_command_uid: str,
        remaining_ms: int,
    ) -> dict:
        """发送 AUTHORIZE_DELIVERY_FIRST_OPEN。"""
        command_uid = str(_uuid.uuid4())
        values = {
            "mcuCommandUid": _uuid_str_to_bytes(command_uid),
            "commandDigestSha256": bytes(32),  # will be computed
            "sessionUid": _uuid_str_to_bytes(session_uid),
            "portNo": port_no,
            "firstPreOpenMeasurementUid": _uuid_str_to_bytes(preopen_measurement_uid),
            "parentStartCommandUid": _uuid_str_to_bytes(parent_start_command_uid),
            "remainingStartAuthorizationMs": remaining_ms,
        }
        digest = compute_command_digest("AUTHORIZE_DELIVERY_FIRST_OPEN", values)
        values["commandDigestSha256"] = bytes.fromhex(digest)
        payload = encode_payload("AUTHORIZE_DELIVERY_FIRST_OPEN", values)
        return self._send_and_wait_ack("AUTHORIZE_DELIVERY_FIRST_OPEN", payload)

    def send_authorize_delivery_local_continue(
        self, session_uid: str, port_no: int, round_index: int,
        postclose_measurement_uid: str,
    ) -> dict:
        """发送 AUTHORIZE_DELIVERY_LOCAL_CONTINUE。"""
        command_uid = str(_uuid.uuid4())
        values = {
            "mcuCommandUid": _uuid_str_to_bytes(command_uid),
            "commandDigestSha256": bytes(32),
            "sessionUid": _uuid_str_to_bytes(session_uid),
            "portNo": port_no,
            "roundIndex": round_index,
            "postCloseMeasurementUid": _uuid_str_to_bytes(postclose_measurement_uid),
        }
        digest = compute_command_digest("AUTHORIZE_DELIVERY_LOCAL_CONTINUE", values)
        values["commandDigestSha256"] = bytes.fromhex(digest)
        payload = encode_payload("AUTHORIZE_DELIVERY_LOCAL_CONTINUE", values)
        return self._send_and_wait_ack("AUTHORIZE_DELIVERY_LOCAL_CONTINUE", payload)

    def send_unlock_clean_door(
        self, operation_uid: str, port_no: int, action_sequence: int,
        preunlock_measurement_uid: str,
    ) -> dict:
        """发送 UNLOCK_CLEAN_DOOR。"""
        command_uid = str(_uuid.uuid4())
        values = {
            "mcuCommandUid": _uuid_str_to_bytes(command_uid),
            "commandDigestSha256": bytes(32),
            "operationUid": _uuid_str_to_bytes(operation_uid),
            "portNo": port_no,
            "cleanActionSequence": action_sequence,
            "preUnlockMeasurementUid": _uuid_str_to_bytes(preunlock_measurement_uid),
        }
        digest = compute_command_digest("UNLOCK_CLEAN_DOOR", values)
        values["commandDigestSha256"] = bytes.fromhex(digest)
        payload = encode_payload("UNLOCK_CLEAN_DOOR", values)
        return self._send_and_wait_ack("UNLOCK_CLEAN_DOOR", payload)

    def send_clean_finish(self, operation_uid: str, port_no: int,
                          action_sequence: int) -> dict:
        """发送 CLEAN_FINISH_REQUESTED。"""
        command_uid = str(_uuid.uuid4())
        values = {
            "mcuCommandUid": _uuid_str_to_bytes(command_uid),
            "commandDigestSha256": bytes(32),
            "operationUid": _uuid_str_to_bytes(operation_uid),
            "portNo": port_no,
            "cleanActionSequence": action_sequence,
        }
        digest = compute_command_digest("CLEAN_FINISH_REQUESTED", values)
        values["commandDigestSha256"] = bytes.fromhex(digest)
        payload = encode_payload("CLEAN_FINISH_REQUESTED", values)
        return self._send_and_wait_ack("CLEAN_FINISH_REQUESTED", payload)

    def send_safe_close_all(self) -> dict:
        """发送 SAFE_CLOSE（全部投递门）。"""
        command_uid = str(_uuid.uuid4())
        values = {
            "mcuCommandUid": _uuid_str_to_bytes(command_uid),
            "commandDigestSha256": bytes(32),
            "scope": "ALL_DELIVERY_DOORS",
            "portNo": 0,
            "executionDeadlineMs": 5000,
        }
        digest = compute_command_digest("SAFE_CLOSE", values)
        values["commandDigestSha256"] = bytes.fromhex(digest)
        payload = encode_payload("SAFE_CLOSE", values)
        return self._send_and_wait_ack("SAFE_CLOSE", payload)

    # ── 事件接收 ──

    def read_mcu_event(self, timeout_ms: int = 500) -> Optional[dict]:
        """非阻塞读取一个 MCU 事件帧。"""
        if not self.is_open:
            return None
        return self._read_frame(timeout_ms=timeout_ms)

    def send_ack(self, referenced_boot_id: int, referenced_tx_sequence: int,
                 referenced_message_type: int, disposition: str = "ACCEPTED") -> None:
        """发送 ACK。"""
        with self._io_lock:
            payload = encode_payload("ACK", {
                "senderBootId": self.edge_boot_id,
                "referencedSenderBootId": referenced_boot_id,
                "referencedTxSequence": referenced_tx_sequence,
                "referencedMessageType": referenced_message_type,
                "disposition": disposition,
            })
            self._send_frame("ACK", payload)

    def send_nack(self, referenced_boot_id: int, referenced_tx_sequence: int,
                  referenced_message_type: int, error_code: int) -> None:
        """发送 NACK。"""
        with self._io_lock:
            payload = encode_payload("NACK", {
                "senderBootId": self.edge_boot_id,
                "referencedSenderBootId": referenced_boot_id,
                "referencedTxSequence": referenced_tx_sequence,
                "referencedMessageType": referenced_message_type,
                "errorCode": error_code,
            })
            self._send_frame("NACK", payload)


# ── 辅助函数 ──

def _uuid_str_to_bytes(u: str) -> str:
    """Validate a UUID string for the generated encoder."""
    return str(_uuid.UUID(u))


def compute_mcu_payload_sha256(configuration_payload: dict) -> str:
    """Compute the Registry mcuPayloadSha256 over the exact MCU subset."""
    config = configuration_payload["config"]
    device = configuration_payload["deviceConfig"]
    ports = configuration_payload["ports"]
    preimage = bytearray(
        bytes.fromhex("45434f42494e3a554152543a4d43552d434f4e4649473a763100")
    )
    preimage.extend(int(config["version"]).to_bytes(8, "big"))
    preimage.extend(bytes.fromhex(config["contentSha256"]))
    preimage.extend(len(ports).to_bytes(1, "big"))
    preimage.extend(int(device["continueDeliveryWaitMs"]).to_bytes(4, "big"))
    preimage.extend(int(device["negativeWeightThresholdGrams"]).to_bytes(4, "big"))
    preimage.extend(int(device["deliveryAutoCloseMs"]).to_bytes(4, "big"))
    preimage.extend(int(device["weightMeasurementTimeoutMs"]).to_bytes(4, "big"))
    preimage.extend(int(device["cleanSolenoidPulseMs"]).to_bytes(4, "big"))
    preimage.extend((1 if device["smokeMonitoringEnabled"] else 0).to_bytes(1, "big"))
    fullness_modes = {
        "INFRARED_ONLY": 1,
        "WEIGHT_ONLY": 2,
        "INFRARED_OR_WEIGHT": 3,
    }
    for expected_port_no, port in enumerate(ports, start=1):
        if port["portNo"] != expected_port_no:
            raise ValueError("ports must be ordered and contiguous from 1")
        preimage.extend(int(port["portNo"]).to_bytes(1, "big"))
        preimage.extend((1 if port["enabled"] else 0).to_bytes(1, "big"))
        preimage.extend(int(port["unitPriceTenThousandths"]).to_bytes(4, "big"))
        mode = port["fullnessMode"]
        if isinstance(mode, str):
            mode = fullness_modes[mode]
        preimage.extend(int(mode).to_bytes(1, "big"))
        preimage.extend(int(port["configuredFullWeightGrams"]).to_bytes(4, "big"))
        preimage.extend(int(port["fullnessSettleWaitMs"]).to_bytes(4, "big"))
        preimage.extend(int(port["fullnessConfirmationWaitMs"]).to_bytes(4, "big"))
        preimage.extend(int(port["weightStableWindowMs"]).to_bytes(4, "big"))
        preimage.extend(int(port["weightMaximumFluctuationGrams"]).to_bytes(4, "big"))
        preimage.extend(int(port["weightRequiredSampleCount"]).to_bytes(2, "big"))
        preimage.extend(int(port["weightMeasurementTimeoutMs"]).to_bytes(4, "big"))
        preimage.extend(int(port["weightMinimumGrams"]).to_bytes(4, "big", signed=True))
        preimage.extend(int(port["weightMaximumGrams"]).to_bytes(4, "big", signed=True))
        preimage.extend(int(port["calibrationVersion"]).to_bytes(4, "big"))
        preimage.extend(int(port["infraredSampleTimeoutMs"]).to_bytes(4, "big"))
        preimage.extend(int(port["deliveryDoorOperationTimeoutMs"]).to_bytes(4, "big"))
    return hashlib.sha256(preimage).hexdigest()


_NACK_ERROR_NAMES = {
    0: "NONE",
    1: "UNSUPPORTED_VERSION",
    2: "UNSUPPORTED_MESSAGE",
    3: "UNSUPPORTED_FLAGS",
    4: "INVALID_LENGTH",
    5: "INVALID_FIELD",
    6: "EXPIRED",
    7: "BUSY",
    8: "STATE_CONFLICT",
    9: "UNKNOWN_WORK",
    10: "IDEMPOTENCY_CONFLICT",
    11: "SAFETY_BLOCKED",
    12: "INTERNAL_FAULT",
}


def _nack_error_name(code: int) -> str:
    return _NACK_ERROR_NAMES.get(code, f"UNKNOWN({code})")
