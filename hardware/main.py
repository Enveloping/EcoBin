# -*- coding: utf-8 -*-
"""EcoBin 香橙派网关 v2 — SQLite 驱动可靠边缘运行时。

架构变化（相比 v1）：
  - SQLite (WAL) 作为唯一持久化真相源
  - UART 1.0 二进制协议 (CRC/ACK/NACK/HELLO/QUERY_STATE)
  - MQTT QoS 1 + SQLite 驱动事件中继
  - 投递 session 一单 + 边缘本地多轮
  - 清运单次 complete + 锁输出事实 + 人工关门确认
  - 启动恢复: SQLite/MCU 双事实对照
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from config import (
    PRODUCT_ID, DEVICE_NAME, DEVICE_KEY, MQTT_HOST, MQTT_PORT,
    TEST_MODE, SERIAL_PORT, SERIAL_BAUDRATE, UART_PORT_COUNT,
    UART_HIL_REQUIRED_CAPABILITIES, EDGE_STORE_PATH,
    EDGE_BOOT_ID_PATH, EDGE_RUNTIME_SNAPSHOT_INTERVAL_S, DEPLOYMENT_CODE,
    MQTT_CLEAN_SESSION, MCU_PROTOCOL_MODE,
    CAMERA_OUTSIDE_SOURCE, CAMERA_INSIDE_SOURCE, CAMERA_WARMUP_FRAMES,
    EDGE_PHOTO_DIR, PHOTO_UPLOAD_POLL_SECONDS,
    PHOTO_GRANT_EXPIRY_SKEW_SECONDS, PHOTO_RETENTION_HOURS,
    TRUSTED_COS_ENVIRONMENT, COS_REQUEST_TIMEOUT_SECONDS,
    validate as config_validate,
)
from cos_photo_uploader import CosPhotoUploader
from edge_store import EdgeStore
from edge_identity import (
    is_valid_edge_boot_id,
    load_or_generate_edge_boot_id,
    persist_edge_boot_id,
)
from mqtt_client import MqttClient
from photo_manager import PhotoManager
from work_manager import WorkManager
from command_processor import CommandProcessor
from edge_boot import boot_sequence, recover_after_online_mcu_hello

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)-5s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")


class EcoBinEdge:
    """EcoBin 香橙派边缘网关 v2。"""

    def __init__(self):
        self._exit_flag = threading.Event()
        self._uart_recovering = threading.Event()

        config_validate()
        if TEST_MODE:
            logger.info("TEST MODE enabled")

        # -- EdgeStore (SQLite) --
        self.store = EdgeStore(EDGE_STORE_PATH)
        self.store.initialize()

        # -- 读取 boot ID --
        self._edge_boot_id = load_or_generate_edge_boot_id(EDGE_BOOT_ID_PATH)
        stored_boot_id = self.store.get_edge_boot_id()
        if is_valid_edge_boot_id(stored_boot_id):
            self._edge_boot_id = int(stored_boot_id)
            persist_edge_boot_id(EDGE_BOOT_ID_PATH, self._edge_boot_id)
        else:
            self.store.set_edge_boot_id(str(self._edge_boot_id))

        # -- UART Link --
        self.uart = _make_uart_link(
            SERIAL_PORT,
            self._edge_boot_id,
            SERIAL_BAUDRATE,
            UART_PORT_COUNT,
            UART_HIL_REQUIRED_CAPABILITIES,
        )

        # -- MQTT Client --
        self.mqtt = MqttClient(
            product_id=PRODUCT_ID, device_name=DEVICE_NAME,
            device_key=DEVICE_KEY, edge_store=self.store,
            mqtt_host=MQTT_HOST, mqtt_port=MQTT_PORT,
            deployment_code=DEPLOYMENT_CODE, edge_boot_id=self._edge_boot_id,
            clean_session=MQTT_CLEAN_SESSION,
            trusted_cos_environment=TRUSTED_COS_ENVIRONMENT,
        )

        # -- Photo Manager --
        self.photo = PhotoManager(
            self.store,
            photo_dir=EDGE_PHOTO_DIR,
            outside_camera_source=CAMERA_OUTSIDE_SOURCE,
            inside_camera_source=CAMERA_INSIDE_SOURCE,
            camera_warmup_frames=CAMERA_WARMUP_FRAMES,
            deployment_code=DEPLOYMENT_CODE,
            uploader=CosPhotoUploader(
                timeout_seconds=COS_REQUEST_TIMEOUT_SECONDS,
            ),
            upload_poll_seconds=PHOTO_UPLOAD_POLL_SECONDS,
            grant_expiry_skew_seconds=(
                PHOTO_GRANT_EXPIRY_SKEW_SECONDS
            ),
            retention_hours=PHOTO_RETENTION_HOURS,
            simulate_camera=TEST_MODE,
            trusted_cos_environment=TRUSTED_COS_ENVIRONMENT,
        )

        # -- Work Manager --
        self.work = WorkManager(self.store, self.uart, self.mqtt, self.photo)
        self.commands = CommandProcessor(
            self.store,
            self.uart,
            self.work,
            trusted_cos_environment=TRUSTED_COS_ENVIRONMENT,
        )

        # -- Wire callbacks --
        self.mqtt.on_command_received = self._on_command
        self.mqtt.on_confirmation_received = self._on_confirmation

        # -- Signal handlers --
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, signum, frame):
        logger.info("signal %d, shutting down...", signum)
        self._exit_flag.set()
        try:
            self.mqtt.disconnect()
        except Exception:
            pass
        try:
            self.uart.close()
        except Exception:
            pass

    def _on_command(self, cmd_id, cmd_type, payload):
        logger.info("command received: type=%s id=%s", cmd_type, cmd_id)
        self.commands.offer_cos_grant(
            cmd_id,
            payload.get("cosGrant"),
        )
        self.commands.wake()

    def _on_confirmation(self, topic, payload):
        logger.debug("confirmation received: topic=%s", topic)

    def run(self):
        logger.info("EcoBin Edge v2 starting (boot_id=%d)", self._edge_boot_id)

        # -- Boot sequence --
        result = boot_sequence(
            store=self.store, uart_link=self.uart,
            mqtt_client=self.mqtt, work_manager=self.work,
            photo_manager=self.photo, test_mode=TEST_MODE,
        )
        if result["status"] == "SAFETY_LOCKED":
            logger.critical("BOOT FAILED: %s", result.get("reason"))
            self._shutdown()
            return
        if self._exit_flag.is_set():
            self._shutdown()
            return
        logger.info("Boot result: %s", result["status"])

        recovered = self.store.recover_interrupted_commands(
            physical_recovery_required=not getattr(
                self.uart,
                "compatibility_mode",
                False,
            )
        )
        if any(recovered.values()):
            logger.warning("Recovered interrupted commands: %s", recovered)

        # -- Start UART event reader thread --
        threading.Thread(target=self._uart_event_loop, daemon=True, name="uart-evt").start()

        # -- Persistent command and MCU-event consumer --
        threading.Thread(target=self._command_loop, daemon=True, name="cmd-consumer").start()

        # -- Periodic runtime snapshots --
        threading.Thread(target=self._runtime_snapshot_loop, daemon=True, name="rt-snap").start()

        # -- MQTT main loop --
        self.mqtt.loop_forever()
        self._shutdown()

    def _uart_event_loop(self):
        """Persist MCU events before ACK; processing happens on another thread."""
        logger.info("UART event reader started")
        while not self._exit_flag.is_set():
            try:
                frame = self.uart.read_mcu_event(timeout_ms=500)
                if frame:
                    if frame.get("message_name") == "HELLO":
                        self._recover_online_mcu(frame)
                        continue
                    payload = frame.get("payload") or {}
                    result = self.store.receive_mcu_frame(frame)
                    if result in ("ACCEPTED", "DUPLICATE"):
                        self.uart.send_ack(
                            payload["mcuBootId"],
                            frame["tx_sequence"],
                            frame["message_type"],
                            "DUPLICATE_ACCEPTED" if result == "DUPLICATE" else "ACCEPTED",
                        )
                        self.commands.wake()
                    elif result == "CONFLICT":
                        self.uart.send_nack(
                            payload["mcuBootId"],
                            frame["tx_sequence"],
                            frame["message_type"],
                            "IDEMPOTENCY_CONFLICT",
                        )
                        logger.critical(
                            "MCU event identity conflict: generation=%s "
                            "boot=%s seq=%s",
                            self.store.get_mcu_receive_generation(),
                            payload.get("mcuBootId"),
                            payload.get("mcuEventSequence"),
                        )
                    else:
                        logger.error("Rejected MCU frame: %s", result)
            except Exception as e:
                logger.error("uart event loop error: %s", e)
                time.sleep(0.1)
        logger.info("UART event reader stopped")

    def _recover_online_mcu(self, hello_frame):
        self._uart_recovering.set()
        try:
            previous = getattr(self.uart, "_mcu_boot_id", None)
            result = recover_after_online_mcu_hello(
                self.store,
                self.uart,
                hello_frame,
            )
            logger.warning(
                "MCU UART session recovered: previous=%s current=%s "
                "receive_generation=%s",
                previous,
                result["mcu_info"]["mcu_boot_id"],
                result["mcu_info"]["mcu_receive_generation"],
            )
            self.commands.wake()
        except Exception as error:
            logger.critical("online MCU recovery failed: %s", error)
            self.store.record_fault(
                "MCU_INTERNAL",
                2048,
                "BLOCK_DEVICE",
                {"reason": str(error), "phase": "ONLINE_RESTART_RECOVERY"},
            )
            self._exit_flag.set()
            try:
                self.mqtt.disconnect()
            except Exception:
                pass
            try:
                self.uart.close()
            except Exception:
                pass
        finally:
            self._uart_recovering.clear()

    def _command_loop(self):
        logger.info("command consumer started")
        while not self._exit_flag.is_set():
            progressed = False
            try:
                for event in self.store.list_pending_mcu_events(limit=20):
                    try:
                        self.commands.process_mcu_event(event)
                        self.store.mark_mcu_event_processed(
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                            event["mcu_receive_generation"],
                        )
                        progressed = True
                    except Exception as error:
                        self.store.mark_mcu_event_failed(
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                            str(error),
                            event["mcu_receive_generation"],
                        )
                        logger.error(
                            "MCU event processing failed: boot=%d seq=%d: %s",
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                            error,
                        )
                        break
                if (
                    not self._uart_recovering.is_set()
                    and self.commands.process_next()
                ):
                    progressed = True
            except Exception as error:
                logger.error("command consumer error: %s", error)
            if not progressed:
                self.commands.wait(0.5)
        logger.info("command consumer stopped")

    def _runtime_snapshot_loop(self):
        """Publish periodic runtime snapshots."""
        while not self._exit_flag.is_set():
            self._exit_flag.wait(EDGE_RUNTIME_SNAPSHOT_INTERVAL_S)
            if self._exit_flag.is_set():
                break
            try:
                if self.mqtt.connected:
                    from edge_boot import _publish_runtime_snapshot
                    _publish_runtime_snapshot(
                        self.store, self.mqtt,
                        {
                            "mcu_boot_id": (
                                getattr(self.uart, "_mcu_boot_id", None) or 0
                            ),
                            "mcu_capability": (
                                getattr(self.uart, "_mcu_capability", None) or 0
                            ),
                            "mcu_firmware_version": getattr(
                                self.uart,
                                "_mcu_firmware_version",
                                "",
                            ),
                            "uart_protocol_major": (
                                None
                                if getattr(
                                    self.uart,
                                    "compatibility_mode",
                                    False,
                                )
                                else 1
                            ),
                            "uart_protocol_minor": (
                                None
                                if getattr(
                                    self.uart,
                                    "compatibility_mode",
                                    False,
                                )
                                else 0
                            ),
                            "fullness_sensor_kind": (
                                "DIGITAL_INFRARED"
                                if getattr(
                                    self.uart,
                                    "compatibility_mode",
                                    False,
                                )
                                else "ULTRASONIC"
                            ),
                            "compatibility_mode": getattr(
                                self.uart,
                                "compatibility_mode",
                                False,
                            ),
                        },
                        [],
                    )
            except Exception as e:
                logger.error("runtime snapshot error: %s", e)

    def _shutdown(self):
        logger.info("shutting down...")
        self._exit_flag.set()
        try:
            self.uart.close()
        except Exception:
            pass
        try:
            self.mqtt.disconnect()
        except Exception:
            pass
        try:
            self.photo.close()
        except Exception:
            pass
        try:
            self.store.close()
        except Exception:
            pass
        logger.info("shutdown complete")


def _make_uart_link(
    port,
    boot_id,
    baudrate,
    port_count,
    hil_required_capabilities,
):
    """Create the explicitly configured MCU link, using mock in TEST_MODE."""
    if TEST_MODE:
        from test_mode import MockUartLink
        return MockUartLink(port=port, edge_boot_id=boot_id)
    if MCU_PROTOCOL_MODE == "fixed-frame":
        from fixed_frame_mcu_adapter import FixedFrameMcuAdapter
        if port_count != 1:
            logger.warning(
                "fixed-frame MCU protocol exposes one port; "
                "ignoring ECOBIN_UART_PORT_COUNT=%d",
                port_count,
            )
        return FixedFrameMcuAdapter(
            port=port,
            edge_boot_id=boot_id,
            port_count=1,
            baudrate=baudrate,
        )
    from uart_link import UartLink
    kwargs = {
        "port": port,
        "edge_boot_id": boot_id,
        "port_count": port_count,
        "baudrate": baudrate,
    }
    if hil_required_capabilities is not None:
        logger.warning(
            "UART HIL capability override enabled: required=0x%X",
            hil_required_capabilities,
        )
        kwargs["required_capability_bitmap"] = hil_required_capabilities
    return UartLink(**kwargs)


if __name__ == "__main__":
    gateway = EcoBinEdge()
    gateway.run()
