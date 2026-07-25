# -*- coding: utf-8 -*-
"""EcoBin 香橙派网关 v2 — SQLite 驱动可靠边缘运行时。

架构变化（相比 v1）：
  - SQLite (WAL) 作为唯一持久化真相源
  - UART 1.0 二进制协议 (CRC/ACK/NACK/HELLO/QUERY_STATE)
  - MQTT QoS 1 + SQLite 驱动事件中继
  - 投递 session 一单 + 边缘本地多轮
  - 清运单次 complete + 电磁阀通断推定 + 人工关门确认
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
    MQTT_CLEAN_SESSION,
    validate as config_validate,
)
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
from edge_boot import boot_sequence

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
        )

        # -- Photo Manager --
        self.photo = PhotoManager(self.store)

        # -- Work Manager --
        self.work = WorkManager(self.store, self.uart, self.mqtt, self.photo)
        self.commands = CommandProcessor(self.store, self.uart, self.work)

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

        recovered = self.store.recover_interrupted_commands()
        if recovered["configuration_requeued"] or recovered["physical_locked"]:
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
                            "MCU event identity conflict: boot=%s seq=%s",
                            payload.get("mcuBootId"),
                            payload.get("mcuEventSequence"),
                        )
                    else:
                        logger.error("Rejected MCU frame: %s", result)
            except Exception as e:
                logger.error("uart event loop error: %s", e)
                time.sleep(0.1)
        logger.info("UART event reader stopped")

    def _command_loop(self):
        logger.info("command consumer started")
        while not self._exit_flag.is_set():
            progressed = False
            try:
                for event in self.store.list_pending_mcu_events(limit=20):
                    try:
                        self.commands.process_mcu_event(event)
                        self.store.mark_mcu_event_processed(
                            event["mcu_boot_id"], event["mcu_event_sequence"]
                        )
                        progressed = True
                    except Exception as error:
                        self.store.mark_mcu_event_failed(
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                            str(error),
                        )
                        logger.error(
                            "MCU event processing failed: boot=%d seq=%d: %s",
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                            error,
                        )
                        break
                if self.commands.process_next():
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
                        {"mcu_boot_id": self.uart._mcu_boot_id or 0,
                         "mcu_capability": self.uart._mcu_capability or 0,
                         "mcu_firmware_version": getattr(self.uart, "_mcu_firmware_version", "")},
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
    """Create UartLink, using mock in TEST_MODE."""
    if TEST_MODE:
        from test_mode import MockUartLink
        return MockUartLink(port=port, edge_boot_id=boot_id)
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
