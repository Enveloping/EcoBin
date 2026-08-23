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
import hashlib
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config import (
    PRODUCT_ID, DEVICE_NAME, DEVICE_KEY, MQTT_HOST, MQTT_PORT,
    SERIAL_PORT, SERIAL_BAUDRATE, UART_PORT_COUNT,
    UART_HIL_REQUIRED_CAPABILITIES, EDGE_STORE_PATH,
    EDGE_BOOT_ID_PATH, EDGE_RUNTIME_SNAPSHOT_INTERVAL_S,
    EDGE_SOFTWARE_VERSION,
    MQTT_CLEAN_SESSION, MCU_PROTOCOL_MODE, MCU_SIMULATED,
    DEVICE_ENTRY_URL_REFRESH_SECONDS,
    CAMERA_OUTSIDE_SOURCE, CAMERA_INSIDE_SOURCE, CAMERA_WARMUP_FRAMES,
    EDGE_PHOTO_DIR, PHOTO_UPLOAD_POLL_SECONDS,
    PHOTO_GRANT_EXPIRY_SKEW_SECONDS, PHOTO_RETENTION_HOURS,
    TRUSTED_COS_ENVIRONMENT, COS_REQUEST_TIMEOUT_SECONDS,
    REMOTE_SUPPORT_CONTROL_SOCKET,
    DEVICE_CREDENTIALS,
    MCU_UPDATE_ENABLED, MCU_BOOT0_WPI, MCU_RESET_WPI,
    MCU_BOOT0_ACTIVE_LEVEL, MCU_RESET_ACTIVE_LEVEL,
    MCU_HARDWARE_COMPATIBILITY, MCU_SIGNING_PUBLIC_KEYS_DIR,
    MCU_FIRMWARE_CACHE_DIR, STM32FLASH_PATH, GPIO_PATH,
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
from device_acceptance import DeviceAcceptanceRunner
from edge_boot import boot_sequence, recover_after_online_mcu_hello
from fixed_frame_health_recovery import (
    FixedFrameHealthRecoveryController,
    runtime_uart_state,
)
from device_entry_url_refresh import DeviceEntryUrlRefreshController
from remote_support_control import (
    RemoteSupportControlClient,
    RemoteSupportStatusBridge,
    RemoteSupportUnavailable,
)
from mcu_firmware_updater import (
    CosFirmwareDownloader,
    FirmwarePackageCache,
    McuFirmwareUpdater,
    Stm32FlashRunner,
    WiringOpBootControl,
    load_release_public_keys,
)
from factory_seal.runtime import RuntimeFactorySealAuthorizer
from factory_seal.admission import FactorySealProductionGate
from factory_seal.validation import FactorySealPaths

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)-5s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")


_SYSTEMD_READY_BOOT_STATUSES = frozenset(
    {
        "READY",
        "DEGRADED",
        # A retained MCU-update maintenance lock deliberately blocks physical
        # work, but the Edge process must remain available to report and
        # recover that update.  It is therefore process-ready for systemd.
        "MCU_UPDATE_FAILED_LOCKED",
    }
)


def notify_systemd_ready(boot_status: str) -> None:
    """Tell systemd the runtime has passed boot gating and can serve work."""

    if boot_status not in _SYSTEMD_READY_BOOT_STATUSES:
        raise RuntimeError(
            f"refusing systemd readiness for boot status {boot_status!r}"
        )
    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):  # Linux abstract Unix-domain socket
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.sendto(b"READY=1", address)


def _is_mcu_package_retry_wait(update: dict | None) -> bool:
    return bool(
        update is not None
        and not update["package_ready"]
        and update["state"] in {"QUEUED", "PACKAGE_FETCH_FAILED"}
    )


class EcoBinEdge:
    """EcoBin 香橙派边缘网关 v2。"""

    def __init__(self):
        self._exit_flag = threading.Event()
        self._uart_recovering = threading.Event()
        self._runtime_snapshot_requested = threading.Event()
        self._runtime_snapshot_lock = threading.Lock()
        self._runtime_snapshot_schedule_lock = threading.Lock()
        self._last_runtime_snapshot_monotonic = 0.0
        self._last_runtime_snapshot_fingerprint = None
        self._next_runtime_snapshot_monotonic = 0.0
        self._remote_support_bridge_retry_at = 0.0
        self._remote_support_bridge_last_warning_at = 0.0

        config_validate()
        # -- EdgeStore (SQLite) --
        self.store = EdgeStore(EDGE_STORE_PATH)
        self.store.initialize()
        self.factory_seal_authorizer = RuntimeFactorySealAuthorizer(
            self.store
        )
        self.factory_seal_gate = FactorySealProductionGate(
            FactorySealPaths(edge_store=Path(EDGE_STORE_PATH))
        )
        _seed_enrolled_device_entry_url(
            self.store,
            DEVICE_CREDENTIALS.device_entry_url
            if DEVICE_CREDENTIALS is not None
            else None,
        )

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
            MCU_SIMULATED,
            self.store.get_device_entry_url,
        )

        # -- MQTT Client --
        self.mqtt = MqttClient(
            product_id=PRODUCT_ID, device_name=DEVICE_NAME,
            device_key=DEVICE_KEY, edge_store=self.store,
            mqtt_host=MQTT_HOST, mqtt_port=MQTT_PORT,
            edge_boot_id=self._edge_boot_id,
            clean_session=MQTT_CLEAN_SESSION,
            trusted_cos_environment=TRUSTED_COS_ENVIRONMENT,
            unsupported_command_types=(
                {
                    "END_CLEAN_BEFORE_UNLOCK",
                    "RESUME_CLEAN_OPERATION",
                }
                if getattr(
                    self.uart,
                    "compatibility_mode",
                    False,
                )
                else set()
            ),
            factory_seal_gate=self.factory_seal_gate,
        )

        # -- Photo Manager --
        self.cos_uploader = CosPhotoUploader(
            timeout_seconds=COS_REQUEST_TIMEOUT_SECONDS,
        )
        self.photo = PhotoManager(
            self.store,
            photo_dir=EDGE_PHOTO_DIR,
            outside_camera_source=CAMERA_OUTSIDE_SOURCE,
            inside_camera_source=CAMERA_INSIDE_SOURCE,
            camera_warmup_frames=CAMERA_WARMUP_FRAMES,
            device_name=DEVICE_NAME,
            uploader=self.cos_uploader,
            upload_poll_seconds=PHOTO_UPLOAD_POLL_SECONDS,
            grant_expiry_skew_seconds=(
                PHOTO_GRANT_EXPIRY_SKEW_SECONDS
            ),
            retention_hours=PHOTO_RETENTION_HOURS,
            trusted_cos_environment=TRUSTED_COS_ENVIRONMENT,
        )

        # -- Work Manager --
        self.work = WorkManager(self.store, self.uart, self.mqtt, self.photo)
        self.acceptance = DeviceAcceptanceRunner(
            self.store,
            self.uart,
            self.photo,
            self.cos_uploader,
            device_name=DEVICE_NAME,
            edge_software_version=EDGE_SOFTWARE_VERSION,
        )
        self.remote_support = RemoteSupportControlClient(
            REMOTE_SUPPORT_CONTROL_SOCKET,
        )
        self.remote_support_status = RemoteSupportStatusBridge(
            self.remote_support,
            self.store,
        )
        self.mcu_updater = None
        if MCU_UPDATE_ENABLED:
            release_keys = load_release_public_keys(
                Path(MCU_SIGNING_PUBLIC_KEYS_DIR)
            )
            firmware_cache = FirmwarePackageCache(
                Path(MCU_FIRMWARE_CACHE_DIR),
                release_keys,
                MCU_HARDWARE_COMPATIBILITY,
            )
            self.mcu_updater = McuFirmwareUpdater(
                store=self.store,
                uart_link=self.uart,
                package_cache=firmware_cache,
                boot_control=WiringOpBootControl(
                    gpio_path=GPIO_PATH,
                    boot0_wpi=MCU_BOOT0_WPI,
                    reset_wpi=MCU_RESET_WPI,
                    boot0_active_level=MCU_BOOT0_ACTIVE_LEVEL,
                    reset_active_level=MCU_RESET_ACTIVE_LEVEL,
                ),
                flash_runner=Stm32FlashRunner(
                    executable_path=STM32FLASH_PATH,
                    serial_port=SERIAL_PORT,
                ),
                downloader=CosFirmwareDownloader(
                    timeout_seconds=COS_REQUEST_TIMEOUT_SECONDS,
                ),
                enabled=True,
                device_name=DEVICE_NAME,
                factory_seal_gate=self.factory_seal_gate,
            )
        self.commands = CommandProcessor(
            self.store,
            self.uart,
            self.work,
            acceptance_runner=self.acceptance,
            trusted_cos_environment=TRUSTED_COS_ENVIRONMENT,
            remote_support_controller=self.remote_support,
            mcu_firmware_updater=self.mcu_updater,
            factory_seal_authorizer=self.factory_seal_authorizer,
            factory_seal_gate=self.factory_seal_gate,
            device_name=DEVICE_NAME,
        )
        self.fixed_frame_health_recovery = FixedFrameHealthRecoveryController(
            self.store,
            self.uart,
            device_name=DEVICE_NAME,
        )
        self.device_entry_url_refresh = DeviceEntryUrlRefreshController(
            self.store,
            self.uart,
            interval_seconds=DEVICE_ENTRY_URL_REFRESH_SECONDS,
        )

        # -- Wire callbacks --
        self.mqtt.on_command_received = self._on_command
        self.mqtt.on_confirmation_received = self._on_confirmation
        self.mqtt.on_reliable_event_count_changed = (
            self._request_runtime_snapshot
        )

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
        if cmd_type == "PROVIDE_PHOTO_UPLOAD_GRANT":
            return self.commands.accept_photo_upload_grant_now(
                payload
            )
        self.commands.offer_cos_grant(
            cmd_id,
            payload.get("cosGrant"),
        )
        self.commands.wake()
        return True

    def _on_confirmation(self, topic, payload):
        logger.debug("confirmation received: topic=%s", topic)

    def run(self):
        logger.info("EcoBin Edge v2 starting (boot_id=%d)", self._edge_boot_id)
        reconciled_progress = (
            self.store.reconcile_mcu_firmware_progress_events(DEVICE_NAME)
        )
        if reconciled_progress:
            logger.warning(
                "backfilled %d MCU firmware progress event(s) from journal",
                reconciled_progress,
            )
        active_update = self.store.get_active_mcu_firmware_update()
        if (
            active_update is not None
            and active_update["state"] != "FAILED_LOCKED"
            and self.mcu_updater is not None
        ):
            logger.warning(
                "resuming interrupted MCU firmware update before business boot: %s",
                active_update["update_uid"],
            )
            self.mcu_updater.process_active()
            active_update = self.store.get_active_mcu_firmware_update()

        package_retry_wait = _is_mcu_package_retry_wait(active_update)

        # -- Boot sequence --
        # Package acquisition has not touched MCU flash, so UART/session boot
        # is still safe and is required after a process restart. The retained
        # maintenance lock continues to block all physical business work.
        if active_update is None or package_retry_wait:
            # The updater leaves a verified application UART open. The normal
            # boot path owns its own fresh handshake/generation, so close once.
            self.uart.close()
            result = boot_sequence(
                store=self.store, uart_link=self.uart,
                mqtt_client=self.mqtt, work_manager=self.work,
                photo_manager=self.photo,
            )
        else:
            result = {
                "status": "MCU_UPDATE_FAILED_LOCKED",
                "reason": active_update.get("last_error_code")
                or "MCU_UPDATE_DISABLED_OR_INTERRUPTED",
            }
            logger.critical(
                "business boot is blocked by MCU firmware maintenance: "
                "update=%s state=%s reason=%s",
                active_update["update_uid"],
                active_update["state"],
                result["reason"],
            )
        if result["status"] == "SAFETY_LOCKED":
            logger.critical("BOOT FAILED: %s", result.get("reason"))
            self._shutdown()
            return
        if self._exit_flag.is_set():
            self._shutdown()
            return
        logger.info("Boot result: %s", result["status"])
        self.mqtt.on_connected = lambda: (
            self._publish_runtime_snapshot_now(force=True)
        )

        recovered = self.store.recover_interrupted_commands()
        if any(recovered.values()):
            logger.warning("Recovered interrupted commands: %s", recovered)

        # Type=notify only becomes active after all persistent state recovery
        # and boot safety gates have completed.  A maintenance-locked updater
        # is intentionally ready: its cloud/reporting loops are the recovery
        # path, while EdgeStore continues to block physical work.
        if self._exit_flag.is_set():
            self._shutdown()
            return
        notify_systemd_ready(result["status"])

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
            self._request_runtime_snapshot()
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
                active_update = self.store.get_active_mcu_firmware_update()
                if active_update is not None:
                    if (
                        _is_mcu_package_retry_wait(active_update)
                        and self.commands.process_next()
                    ):
                        progressed = True
                        active_update = (
                            self.store.get_active_mcu_firmware_update()
                        )
                    if (
                        active_update is not None
                        and self.mcu_updater is not None
                        and active_update["state"] != "FAILED_LOCKED"
                        and self.mcu_updater.process_active()
                    ):
                        progressed = True
                        self._request_runtime_snapshot()
                    if self.store.get_maintenance_lock() is not None:
                        if self._poll_remote_support_status():
                            progressed = True
                        if not progressed:
                            self.commands.wait(0.5)
                        continue
                if self.work.expire_fixed_frame_work():
                    progressed = True
                if self._poll_remote_support_status():
                    progressed = True
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
                if not self._uart_recovering.is_set():
                    recovery = self.fixed_frame_health_recovery.poll()
                    if recovery["state_changed"]:
                        progressed = True
                    if self.commands.process_next():
                        progressed = True
                    self.device_entry_url_refresh.poll()
            except Exception as error:
                logger.error("command consumer error: %s", error)
            if not progressed:
                self.commands.wait(0.5)
            else:
                self._request_runtime_snapshot()
        logger.info("command consumer stopped")

    def _poll_remote_support_status(self) -> int:
        now = time.monotonic()
        if now < self._remote_support_bridge_retry_at:
            return 0
        try:
            imported = self.remote_support_status.poll_once()
            self._remote_support_bridge_retry_at = 0.0
            return imported
        except RemoteSupportUnavailable:
            self._remote_support_bridge_retry_at = now + 2.0
            if now - self._remote_support_bridge_last_warning_at >= 30.0:
                logger.warning(
                    "independent remote support agent is unavailable"
                )
                self._remote_support_bridge_last_warning_at = now
            return 0
        except Exception as error:
            self._remote_support_bridge_retry_at = now + 5.0
            if now - self._remote_support_bridge_last_warning_at >= 30.0:
                logger.error(
                    "remote support status bridge failed: %s",
                    type(error).__name__,
                )
                self._remote_support_bridge_last_warning_at = now
            return 0

    def _runtime_snapshot_loop(self):
        """Publish state-change snapshots plus a low-frequency fallback."""
        self._reset_runtime_snapshot_fallback()
        while not self._exit_flag.is_set():
            now = time.monotonic()
            next_periodic = self._next_runtime_snapshot_deadline()
            requested = self._runtime_snapshot_requested.wait(
                max(0.0, next_periodic - now)
            )
            if self._exit_flag.is_set():
                break
            try:
                periodic_due = (
                    time.monotonic()
                    >= self._next_runtime_snapshot_deadline()
                )
                while requested and not periodic_due:
                    remaining = max(
                        0.0,
                        5.0 - (
                            time.monotonic()
                            - self._last_runtime_snapshot_monotonic
                        ),
                    )
                    if remaining <= 0:
                        break
                    if self._exit_flag.wait(remaining):
                        break
                    periodic_due = (
                        time.monotonic()
                        >= self._next_runtime_snapshot_deadline()
                    )
                if self._exit_flag.is_set():
                    break
                if requested:
                    self._runtime_snapshot_requested.clear()
                if not requested and not periodic_due:
                    # A reconnect snapshot may have moved the shared fallback
                    # deadline while this thread was waiting on the old one.
                    continue
                if not self.mqtt.connected:
                    if periodic_due:
                        self._retry_runtime_snapshot_after(30.0)
                    continue
                outcome = self._publish_runtime_snapshot_now(
                    force=periodic_due,
                )
                if (
                    not outcome["published"]
                    and not outcome["skipped_unchanged"]
                    and not outcome.get("deferred", False)
                ):
                    self._retry_runtime_snapshot_after(30.0)
            except Exception as e:
                logger.error("runtime snapshot error: %s", e)
                self._retry_runtime_snapshot_after(30.0)

    def _runtime_snapshot_interval_seconds(self):
        applied = self.store.get_latest_applied_configuration()
        if applied:
            device_config = applied.get("payload", {}).get(
                "deviceConfig",
                {},
            )
            interval_ms = device_config.get(
                "edgeHeartbeatIntervalMs",
                3_600_000,
            )
            if (
                isinstance(interval_ms, int)
                and not isinstance(interval_ms, bool)
                and 1 <= interval_ms <= 4_294_967_295
            ):
                return max(600.0, interval_ms / 1000.0)
        return max(600.0, float(EDGE_RUNTIME_SNAPSHOT_INTERVAL_S))

    def _next_runtime_snapshot_deadline(self):
        with self._runtime_snapshot_schedule_lock:
            return self._next_runtime_snapshot_monotonic

    def _reset_runtime_snapshot_fallback(self, now=None):
        if now is None:
            now = time.monotonic()
        with self._runtime_snapshot_schedule_lock:
            self._next_runtime_snapshot_monotonic = (
                now + self._runtime_snapshot_interval_seconds()
            )

    def _retry_runtime_snapshot_after(self, delay_seconds):
        retry_at = time.monotonic() + delay_seconds
        with self._runtime_snapshot_schedule_lock:
            current = self._next_runtime_snapshot_monotonic
            if current <= 0 or retry_at < current:
                self._next_runtime_snapshot_monotonic = retry_at

    def _request_runtime_snapshot(self):
        """Coalesce repeated state changes into at most one snapshot per 5s."""
        self._runtime_snapshot_requested.set()

    def _publish_runtime_snapshot_now(self, force=False):
        from edge_boot import _publish_runtime_snapshot
        with self._runtime_snapshot_lock:
            if (
                not force
                and self._last_runtime_snapshot_monotonic > 0
                and time.monotonic()
                    - self._last_runtime_snapshot_monotonic < 5.0
            ):
                self._runtime_snapshot_requested.set()
                return {
                    "published": False,
                    "skipped_unchanged": False,
                    "deferred": True,
                }
            compatibility_mode = getattr(
                self.uart,
                "compatibility_mode",
                False,
            )
            result = _publish_runtime_snapshot(
                self.store,
                self.mqtt,
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
                "mcu_firmware_identity": getattr(
                    self.uart,
                    "verified_firmware_identity",
                    None,
                ),
                "uart_protocol_major": (
                    None if compatibility_mode else 1
                ),
                "uart_protocol_minor": (
                    None if compatibility_mode else 0
                ),
                "fullness_sensor_kind": (
                    "DIGITAL_INFRARED"
                    if compatibility_mode
                    else "ULTRASONIC"
                ),
                "uart_state": runtime_uart_state(self.store, self.uart),
                "compatibility_mode": compatibility_mode,
                },
                [],
                force=force,
                previous_payload_sha256=(
                    self._last_runtime_snapshot_fingerprint
                ),
            )
            if not result["published"]:
                return result
            now = time.monotonic()
            self._last_runtime_snapshot_fingerprint = result[
                "payload_sha256"
            ]
            self._last_runtime_snapshot_monotonic = now
            self._reset_runtime_snapshot_fallback(now)
            return result

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
    mcu_simulated=False,
    device_entry_url_provider=None,
):
    """Create the explicitly configured MCU link."""
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
            is_simulated=mcu_simulated,
            device_entry_url_provider=device_entry_url_provider,
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


def _seed_enrolled_device_entry_url(store, device_entry_url):
    """Seed the factory QR URL once, before an acceptance command exists."""

    if not device_entry_url or store.get_device_entry_url() is not None:
        return False
    issued_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    store.save_device_entry_url(
        device_entry_url,
        hashlib.sha256(device_entry_url.encode("ascii")).hexdigest(),
        issued_at,
    )
    return True


if __name__ == "__main__":
    gateway = EcoBinEdge()
    gateway.run()
