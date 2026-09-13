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
    CAMERA_OUTSIDE_SOURCE, CAMERA_INSIDE_SOURCE,
    EDGE_PHOTO_DIR, PHOTO_UPLOAD_POLL_SECONDS,
    PHOTO_GRANT_EXPIRY_SKEW_SECONDS, PHOTO_RETENTION_HOURS,
    TRUSTED_COS_ENVIRONMENT, COS_REQUEST_TIMEOUT_SECONDS,
    REMOTE_SUPPORT_CONTROL_SOCKET,
    DEVICE_CREDENTIALS, BUSINESS_IDENTITY,
    MCU_UPDATE_ENABLED, MCU_REMOTE_UPDATE_CAPABLE,
    MCU_BOOT0_WPI, MCU_RESET_WPI,
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
from business_message_handler import BusinessMessageHandler
from business_outbox_relay import BusinessOutboxRelay
from business_control import (
    build_business_control_service_from_environment,
)
from device_identity import DeviceIdentity
from local_proxy_cloud_transport import LocalProxyCloudTransport
from photo_manager import PhotoManager
from work_manager import CleanUnlockDecisionDeferred, WorkManager
from job_safety import JobSafetyError, build_job_safety_from_environment
from command_processor import CommandProcessor
from edge_boot import boot_sequence, recover_after_online_mcu_hello
from fixed_frame_health_recovery import (
    FixedFrameHealthRecoveryController,
    runtime_uart_state,
)
from fixed_frame_mcu_maintenance import FixedFrameMcuMaintenancePort
from device_entry_url_refresh import DeviceEntryUrlRefreshController
from remote_support_control import (
    RemoteSupportControlClient,
    RemoteSupportStatusBridge,
    RemoteSupportUnavailable,
)
from factory_seal.admission import FactorySealProductionGate
from factory_seal.validation import FactorySealPaths
from trusted_clock import ClockHealthMonitor

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
        # The permanent updater owns the job gate while this process exposes
        # only the bounded MCU observe/quiesce/verify control surface.
        "MCU_UPDATE_MAINTENANCE",
    }
)

CLOUD_TRANSPORT_MODE_ENVIRONMENT = "ECOBIN_CLOUD_TRANSPORT_MODE"
COMMUNICATION_SOCKET_ENVIRONMENT = "ECOBIN_COMMUNICATION_SOCKET"
DEFAULT_COMMUNICATION_SOCKET = "/run/ecobin/communication/control.sock"


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


def _require_stage4_mcu_update_boundary(
    job_safety,
    *,
    legacy_mcu_update_enabled: bool,
) -> None:
    """Never combine the permanent job gate with the old root MCU updater."""

    if job_safety.enabled and legacy_mcu_update_enabled:
        raise RuntimeError(
            "stage-four job safety cannot run with the legacy business-owned "
            "MCU updater; MCU update orchestration has not moved to the "
            "permanent updater"
        )


def _build_cloud_transport(*, job_permit_enforced: bool):
    """Build direct or permanent-agent transport from one explicit mode."""

    mode = os.getenv(CLOUD_TRANSPORT_MODE_ENVIRONMENT, "direct").strip().lower()
    if mode == "direct":
        # The direct transport is deliberately a migration-only dependency.
        # Import it only in the legacy posture so a post-cut-over business
        # release can omit the OneNet client and device-key handling code.
        from direct_onenet_transport import DirectOneNetTransport

        return (
            DirectOneNetTransport(
                product_id=PRODUCT_ID,
                device_name=DEVICE_NAME,
                device_key=DEVICE_KEY,
                mqtt_host=MQTT_HOST,
                mqtt_port=MQTT_PORT,
                clean_session=MQTT_CLEAN_SESSION,
            ),
            False,
        )
    if mode != "local-proxy":
        raise ValueError(
            f"{CLOUD_TRANSPORT_MODE_ENVIRONMENT} must be direct or local-proxy"
        )
    if not job_permit_enforced:
        raise RuntimeError(
            "local cloud proxy requires the permanent job gate before cut-over"
        )
    socket_path = os.getenv(
        COMMUNICATION_SOCKET_ENVIRONMENT,
        DEFAULT_COMMUNICATION_SOCKET,
    ).strip()
    if not socket_path or not Path(socket_path).is_absolute():
        raise ValueError(
            f"{COMMUNICATION_SOCKET_ENVIRONMENT} must be an absolute path"
        )
    return LocalProxyCloudTransport(socket_path), True


class EcoBinEdge:
    """EcoBin 香橙派边缘网关 v2。"""

    def __init__(self):
        self._native_mode = MCU_PROTOCOL_MODE == "uart-v2"
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
        self._runtime_ready = False
        self.clock_monitor = ClockHealthMonitor()
        self.factory_progress = None

        config_validate()
        # Stage-four wiring is installed as a default-off candidate.  When a
        # controlled HIL run explicitly enables it, every new physical job
        # must obtain a permit from the independent updater; an unreadable or
        # still-LOCKED updater therefore fails closed before UART activity.
        self.job_safety = build_job_safety_from_environment()
        _require_stage4_mcu_update_boundary(
            self.job_safety,
            legacy_mcu_update_enabled=MCU_UPDATE_ENABLED,
        )
        self.device_identity = DeviceIdentity(DEVICE_NAME)
        # -- EdgeStore (SQLite) --
        self.store = EdgeStore(EDGE_STORE_PATH)
        self.store.initialize()
        self.factory_seal_authorizer = None
        self.factory_seal_gate = FactorySealProductionGate(
            FactorySealPaths(edge_store=Path(EDGE_STORE_PATH))
        )
        _seed_enrolled_device_entry_url(
            self.store,
            (
                DEVICE_CREDENTIALS.device_entry_url
                if DEVICE_CREDENTIALS is not None
                else BUSINESS_IDENTITY.device_entry_url
                if BUSINESS_IDENTITY is not None
                else None
            ),
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
        ) if not self._native_mode else self._make_native_business()
        maintenance_port = None
        if MCU_PROTOCOL_MODE == "fixed-frame":
            maintenance_port = FixedFrameMcuMaintenancePort(
                self.uart,
                active_work_reader=self.store.get_work_slot,
                shutdown_requested=self._exit_flag.is_set,
                maintenance_authorizer=(
                    self.job_safety.require_mcu_maintenance
                ),
            )
        # -- Selected implementation of the stable cloud boundary --
        self.cloud_transport, cloud_proxy_enabled = _build_cloud_transport(
            job_permit_enforced=self.job_safety.enabled,
        )
        if not cloud_proxy_enabled:
            # Device acceptance, seal authorization and the first-boot
            # progress projection belong to the image-owned factory posture.
            # A replaceable post-seal business package deliberately omits
            # these modules and retains only read-only seal admission.
            from factory_progress import (
                DEFAULT_RUNTIME_PROGRESS_PATH,
                RuntimeProgressWriter,
            )
            from factory_seal.runtime import RuntimeFactorySealAuthorizer

            self.factory_progress = RuntimeProgressWriter(
                os.getenv(
                    "ECOBIN_FACTORY_PROGRESS_PATH",
                    str(DEFAULT_RUNTIME_PROGRESS_PATH),
                )
            )
            self.factory_seal_authorizer = RuntimeFactorySealAuthorizer(
                self.store
            )
        self.business_control = (
            build_business_control_service_from_environment(
                release_version=EDGE_SOFTWARE_VERSION,
                job_permit_enforced=self.job_safety.enabled,
                mcu_maintenance_port=maintenance_port,
                cloud_proxy_ingress=(
                    self.cloud_transport if cloud_proxy_enabled else None
                ),
                enable_cloud_proxy_candidate=cloud_proxy_enabled,
                business_database_path=EDGE_STORE_PATH,
                software_runtime_facts_provider=(
                    self._software_runtime_facts
                ),
                native_fault_status_provider=(
                    self.uart.communication_fault_status
                    if self._native_mode
                    else None
                ),
                native_fault_recovery_handler=(
                    self.uart.confirm_communication_fault_recovered
                    if self._native_mode
                    else None
                ),
            )
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
        self.work = WorkManager(
            self.store,
            self.uart,
            self.device_identity,
            self.photo,
            job_safety=self.job_safety,
        ) if not self._native_mode else self.uart
        if self._native_mode:
            self.work.photo = self.photo
            self.work.reporter.photo = self.photo
        self.acceptance = self._make_device_acceptance_runner(
            cloud_proxy_enabled
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
            # The business-owned updater is another migration-only module.
            # Permanent-updater business releases do not carry these files.
            from mcu_firmware_updater import (
                CosFirmwareDownloader,
                FirmwarePackageCache,
                McuFirmwareUpdater,
                Stm32FlashRunner,
                WiringOpBootControl,
                load_release_public_keys,
            )

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

        # -- Business/cloud bridge (still one process and one connection) --
        self.business_messages = BusinessMessageHandler(
            self.store,
            self.device_identity,
            edge_boot_id=self._edge_boot_id,
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
        self.business_outbox = BusinessOutboxRelay(
            self.store,
            self.cloud_transport,
        )

        # -- Wire stable callbacks --
        self.business_messages.on_command_received = self._on_command
        self.business_messages.on_reliable_event_count_changed = (
            self._request_runtime_snapshot
        )
        self.business_messages.on_outbox_wakeup = (
            self.business_outbox.wake
        )
        self.cloud_transport.on_service_request = (
            self.business_messages.handle_service_request
        )
        self.cloud_transport.on_legacy_command_received = (
            self.business_messages.handle_legacy_command
        )
        self.cloud_transport.on_event_transport_ack = (
            self.business_outbox.handle_transport_ack
        )
        self.cloud_transport.on_event_platform_result = (
            self.business_outbox.handle_platform_result
        )
        self.cloud_transport.on_connected = self._on_cloud_connected
        self.cloud_transport.on_disconnected = self._on_cloud_disconnected
        if not cloud_proxy_enabled:
            self.cloud_transport.on_mqtt_state_observed = (
                self._on_direct_mqtt_state_observed
            )

        # -- Signal handlers --
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _make_device_acceptance_runner(self, cloud_proxy_enabled):
        """Build the local acceptance owner for every direct-cloud UART mode."""
        if cloud_proxy_enabled:
            return None
        from device_acceptance import DeviceAcceptanceRunner

        return DeviceAcceptanceRunner(
            self.store,
            self.uart,
            self.photo,
            self.cos_uploader,
            device_name=DEVICE_NAME,
            mcu_remote_update_capable=MCU_REMOTE_UPDATE_CAPABLE,
            edge_software_version=EDGE_SOFTWARE_VERSION,
            progress_callback=self._report_p8_progress,
        )

    def _on_signal(self, signum, frame):
        logger.info("signal %d, shutting down...", signum)
        self._report_factory_progress(service_state="STOPPING")
        self._exit_flag.set()
        if self.business_control is not None:
            try:
                self.business_control.request_stop()
            except Exception:
                logger.exception(
                    "failed to fence the business local control service"
                )
        try:
            self.cloud_transport.disconnect()
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

    def _on_direct_mqtt_state_observed(
        self,
        session_present: bool,
        reason_code: int,
    ) -> None:
        """Preserve legacy direct-MQTT diagnostics during migration."""

        try:
            self.store.save_mqtt_persistent_state(
                session_present,
                reason_code,
            )
        except Exception:
            logger.exception("failed to persist MQTT diagnostic state")

    def _on_cloud_connected(self):
        try:
            fault = self.store.get_active_edge_fault(
                "NETWORK",
                "NETWORK_CONNECTIVITY",
            )
            if fault is not None:
                self.store.recover_fault_and_create_event(
                    device_name=self.device_identity.device_name,
                    fault_uid=fault["fault_uid"],
                    component="NETWORK",
                    fault_code="NETWORK_CONNECTIVITY",
                    port_no=fault["port_no"],
                    recovery_evidence="MQTT_CONNECTED",
                )
        except Exception:
            logger.exception("failed to persist cloud recovery event")
        self.business_outbox.on_connected()
        values = {"mqtt_state": "CONNECTED"}
        if self._can_clear_runtime_error():
            values["last_error_code"] = None
        self._report_factory_progress(**values)
        if self._runtime_ready:
            self._publish_runtime_snapshot_now(force=True)

    def _on_cloud_disconnected(self):
        self.business_outbox.on_disconnected()
        values = {}
        if self._exit_flag.is_set():
            values["mqtt_state"] = "DISCONNECTED"
            if self._can_clear_runtime_error():
                values["last_error_code"] = None
        else:
            values.update({
                "mqtt_state": "FAILED",
                "last_error_code": "MQTT_DISCONNECTED",
            })
        self._report_factory_progress(**values)

    def _connect_cloud_after_local_boot(self, result: dict) -> dict:
        """Start the selected transport after local safety recovery."""

        if not self.cloud_transport.connect():
            logger.error("BOOT: cloud transport connect failed")
            try:
                self.store.observe_fault_and_create_event(
                    device_name=self.device_identity.device_name,
                    component="NETWORK",
                    fault_code="NETWORK_CONNECTIVITY",
                    severity="WARNING",
                    detail={"reasonCode": "MQTT_CONNECT_FAILED"},
                )
            except Exception:
                logger.exception(
                    "failed to persist cloud connection fault"
                )
            return {
                **result,
                "status": "DEGRADED",
                "reason": "mqtt_connect_failed",
            }

        # Preserve the existing short grace period between subscription and
        # the first runtime snapshot.  Reconnect snapshots remain callback-
        # driven after the runtime has reached READY.
        time.sleep(0.5)
        from edge_boot import _publish_runtime_snapshot

        _publish_runtime_snapshot(
            self.store,
            self.cloud_transport,
            self.device_identity,
            result["mcu_info"],
            result.get("snapshots", []),
        )
        return result

    def _report_p8_progress(
        self,
        phase: str,
        error_code: str | None = None,
    ) -> None:
        values = {"p8_phase": phase}
        if error_code is not None or self.cloud_transport.connected:
            values["last_error_code"] = error_code
        self._report_factory_progress(**values)

    def _report_factory_progress(self, **values) -> None:
        if self.factory_progress is None:
            return
        try:
            self.factory_progress.report(**values)
        except Exception as error:
            logger.error(
                "could not update runtime factory progress: %s",
                type(error).__name__,
            )

    def _can_clear_runtime_error(self) -> bool:
        if self.factory_progress is None:
            return True
        current = self.factory_progress.snapshot()
        return bool(
            current["serviceState"] != "FAILED"
            and current["uartState"] != "FAILED"
            and current["p8Phase"] != "FAILED"
        )

    def _factory_progress_loop(self):
        logger.info("factory progress heartbeat started")
        while not self._exit_flag.wait(5.0):
            business_control = getattr(self, "business_control", None)
            if (
                business_control is not None
                and not business_control.is_running
            ):
                failure = business_control.failure
                logger.critical(
                    "business local control service stopped unexpectedly: %s",
                    (
                        type(failure).__name__
                        if failure is not None
                        else "unknown failure"
                    ),
                )
                self._report_factory_progress(
                    service_state="FAILED",
                    last_error_code="BUSINESS_CONTROL_FAILED",
                )
                self._exit_flag.set()
                try:
                    self.cloud_transport.disconnect()
                except Exception:
                    pass
                break
            self._report_factory_progress(
                uart_state=self._current_uart_progress_state(),
                mqtt_state=(
                    "CONNECTED"
                    if self.cloud_transport.connected
                    else "DISCONNECTED"
                ),
            )
        logger.info("factory progress heartbeat stopped")

    def _current_uart_progress_state(self) -> str:
        if getattr(self, "_native_mode", False):
            return "FAILED" if self.uart.uart_state == "FAULT" else self.uart.uart_state
        if self._uart_recovering.is_set():
            return "RECOVERING"
        if not getattr(self.uart, "is_open", False):
            return "DISCONNECTED"
        if getattr(self.uart, "mcu_session_ready", False):
            return "READY"
        return "STARTING"

    def run(self):
        if getattr(self, "_native_mode", False):
            return self._run_native()
        logger.info("EcoBin Edge v2 starting (boot_id=%d)", self._edge_boot_id)
        self._report_factory_progress(
            service_state="STARTING",
            uart_state="STARTING",
            mqtt_state="CONNECTING",
            p8_phase="IDLE",
            last_error_code=None,
        )
        if self.business_control is not None:
            try:
                self.business_control.start()
            except Exception:
                logger.exception("business local control service failed to start")
                self._report_factory_progress(
                    service_state="FAILED",
                    last_error_code="BUSINESS_CONTROL_FAILED",
                )
                self._shutdown()
                return
        threading.Thread(
            target=self._factory_progress_loop,
            daemon=True,
            name="factory-progress",
        ).start()
        try:
            permanent_mcu_maintenance = (
                self.job_safety.get_mcu_maintenance_status()
            )
        except JobSafetyError:
            logger.exception("permanent MCU maintenance state is unavailable")
            self._report_factory_progress(
                service_state="FAILED",
                last_error_code="MCU_MAINTENANCE_STATE_UNAVAILABLE",
            )
            self._shutdown()
            return
        if permanent_mcu_maintenance is not None:
            self._run_permanent_mcu_maintenance_mode(
                permanent_mcu_maintenance
            )
            return
        # Clock repair starts before business recovery, but clock uncertainty
        # is diagnostic only and never blocks physical work or event creation.
        self._poll_clock_health()
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
                store=self.store,
                uart_link=self.uart,
                device_identity=self.device_identity,
            )
            if result["status"] != "SAFETY_LOCKED":
                result = self._connect_cloud_after_local_boot(result)
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
            self._report_factory_progress(
                service_state="FAILED",
                uart_state="FAILED",
                mqtt_state=(
                    "CONNECTED"
                    if self.cloud_transport.connected
                    else "DISCONNECTED"
                ),
                last_error_code="RUNTIME_BOOT_FAILED",
            )
            self._shutdown()
            return
        if self._exit_flag.is_set():
            self._shutdown()
            return
        logger.info("Boot result: %s", result["status"])
        self._report_factory_progress(
            service_state="RUNNING",
            uart_state=self._current_uart_progress_state(),
            mqtt_state=(
                "CONNECTED"
                if self.cloud_transport.connected
                else "FAILED"
            ),
            last_error_code=(
                None
                if self.cloud_transport.connected
                else "MQTT_CONNECT_FAILED"
            ),
        )

        recovered = self.store.recover_interrupted_commands()
        if any(recovered.values()):
            logger.warning("Recovered interrupted commands: %s", recovered)
        if self.work.reconcile_pending_job_safety_completion():
            logger.info(
                "reconciled a pending permanent job completion before ready"
            )
        if self.work.reconcile_pre_action_job_safety_failure():
            logger.info(
                "closed an interrupted job that had no physical action"
            )
        reconciled_orphans = (
            self.work.reconcile_orphan_granted_job_permits()
        )
        if reconciled_orphans:
            logger.info(
                "reconciled %d grant(s) created before a business slot",
                reconciled_orphans,
            )

        # Type=notify only becomes active after all persistent state recovery
        # and boot safety gates have completed.  A maintenance-locked updater
        # is intentionally ready: its cloud/reporting loops are the recovery
        # path, while EdgeStore continues to block physical work.
        if self._exit_flag.is_set():
            self._shutdown()
            return
        if self.business_control is not None:
            try:
                self.business_control.mark_ready()
            except Exception:
                logger.exception(
                    "business local control service failed before readiness"
                )
                self._report_factory_progress(
                    service_state="FAILED",
                    last_error_code="BUSINESS_CONTROL_FAILED",
                )
                self._shutdown()
                return
        notify_systemd_ready(result["status"])
        self._runtime_ready = True

        # -- Start UART event reader thread --
        threading.Thread(target=self._uart_event_loop, daemon=True, name="uart-evt").start()

        # -- Persistent command and MCU-event consumer --
        threading.Thread(target=self._command_loop, daemon=True, name="cmd-consumer").start()

        # -- Periodic runtime snapshots --
        threading.Thread(target=self._runtime_snapshot_loop, daemon=True, name="rt-snap").start()

        # -- Clock quality / bounded NTP self-repair --
        threading.Thread(target=self._clock_health_loop, daemon=True, name="clock-health").start()

        # -- Selected cloud transport main loop --
        self.cloud_transport.run_forever()
        self._shutdown()

    def _make_native_business(self):
        from native_business_runtime import NativeBusinessRuntime
        return NativeBusinessRuntime(self.store, self.job_safety,
            device_name=DEVICE_NAME,
            connected=lambda: self.cloud_transport.connected)

    def _run_native(self):
        """One foreground UART owner; reuse cloud/photo/management services.

        No legacy boot abort, second UART reader or per-action recovery loop.
        READY below means the service can process management/reporting, not
        that hardware is admitted for a new physical business.
        """
        self._report_factory_progress(service_state="STARTING", uart_state="STARTING")
        try:
            if self.business_control is not None:
                self.business_control.start()
            self.uart.open(port=SERIAL_PORT, baudrate=SERIAL_BAUDRATE)
            # Connect/status/remote-support IPC and cloud submission can wait
            # seconds. They never own the UART and must not stall its owner.
            threading.Thread(target=self._run_native_cloud,
                daemon=True, name="native-cloud").start()
            threading.Thread(target=self._clock_health_loop,
                daemon=True, name="clock-health").start()
            if self.business_control is not None:
                self.business_control.mark_ready()
            self._runtime_ready = True
            notify_systemd_ready("NATIVE_STARTING")
            self._report_factory_progress(service_state="RUNNING")
            self._runtime_snapshot_requested.set()
            threading.Thread(target=self._runtime_snapshot_loop,
                daemon=True, name="runtime-snap").start()
            threading.Thread(target=self._factory_progress_loop,
                daemon=True, name="factory-progress").start()
            threading.Thread(target=self._native_remote_support_loop,
                daemon=True, name="native-support").start()
            previous_status = None
            while not self._exit_flag.is_set():
                try:
                    status = self.work.poll()
                    self.commands.process_next()
                    if status != previous_status:
                        previous_status = status
                        self._request_runtime_snapshot()
                except Exception as error:
                    # Persistence errors must not masquerade as optional camera
                    # failure or permit another START. No retry of an old action.
                    code = getattr(error, "code", "NATIVE_RUNTIME_FAILED")
                    logger.error("native runtime blocked: %s", code)
                    self.store.set_state("native_blocking_fault", code)
                    self._request_runtime_snapshot()
                self._exit_flag.wait(0.05)
        finally:
            self._shutdown()

    def _run_native_cloud(self):
        self.cloud_transport.connect()
        self.cloud_transport.run_forever()

    def _native_remote_support_loop(self):
        while not self._exit_flag.is_set():
            self._poll_remote_support_status()
            self._exit_flag.wait(1.0)

    def _run_permanent_mcu_maintenance_mode(
        self,
        maintenance: dict[str, object],
    ) -> None:
        """Stay alive only as the updater's unprivileged MCU verification arm."""

        if self.business_control is None:
            raise RuntimeError(
                "permanent MCU maintenance requires the business control service"
            )
        update_uid = maintenance["updateUid"]
        logger.warning(
            "starting MCU maintenance-only business mode: update=%s",
            update_uid,
        )
        try:
            self.business_control.mark_ready()
            notify_systemd_ready("MCU_UPDATE_MAINTENANCE")
        except Exception:
            logger.exception("MCU maintenance control surface failed readiness")
            self._shutdown()
            return
        self._runtime_ready = True
        self._report_factory_progress(
            service_state="MAINTENANCE",
            uart_state="STARTING",
            mqtt_state="DISCONNECTED",
            last_error_code=None,
        )
        while not self._exit_flag.wait(0.5):
            if not self.business_control.is_running:
                logger.critical("business control stopped during MCU maintenance")
                break
            try:
                current = self.job_safety.get_mcu_maintenance_status()
            except JobSafetyError:
                logger.exception("lost permanent MCU maintenance state")
                break
            if current is None:
                logger.info(
                    "MCU maintenance released; restarting full business runtime"
                )
                break
            if current["updateUid"] != update_uid:
                logger.critical("MCU maintenance owner changed unexpectedly")
                break
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
        self._report_factory_progress(uart_state="RECOVERING")
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
            values = {"uart_state": "READY"}
            if not self.cloud_transport.connected:
                values["last_error_code"] = "MQTT_DISCONNECTED"
            elif self._can_clear_runtime_error():
                values["last_error_code"] = None
            self._report_factory_progress(**values)
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
                self.cloud_transport.disconnect()
            except Exception:
                pass
            try:
                self.uart.close()
            except Exception:
                pass
            self._report_factory_progress(
                uart_state="FAILED",
                last_error_code="UART_RECOVERY_FAILED",
            )
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
                if self.work.reconcile_pending_physical_action_confirmations():
                    progressed = True
                if self.work.reconcile_pending_job_safety_completion():
                    progressed = True
                if self.work.reconcile_pre_action_job_safety_failure():
                    progressed = True
                if self.work.reconcile_orphan_granted_job_permits():
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
                    except JobSafetyError as error:
                        if error.code == "JOB_GATE_UNAVAILABLE":
                            # The MCU fact is already durable and the action
                            # receipt uses a stable identity.  Keep this inbox
                            # row PENDING so a lost updater response or a brief
                            # socket outage is retried; marking it FAILED would
                            # strand an otherwise valid physical result forever.
                            logger.warning(
                                "permanent action confirmation unavailable; "
                                "MCU event remains pending: boot=%d seq=%d",
                                event["mcu_boot_id"],
                                event["mcu_event_sequence"],
                            )
                            break
                        self.store.mark_mcu_event_failed(
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                            str(error),
                            event["mcu_receive_generation"],
                        )
                        logger.error(
                            "MCU event job-safety validation failed: "
                            "boot=%d seq=%d: %s",
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                            error,
                        )
                        break
                    except CleanUnlockDecisionDeferred:
                        # A durable, still-valid END_CLEAN_BEFORE_UNLOCK is
                        # queued ahead of this unlock decision.  Keep the MCU
                        # measurement pending, then let process_next() below
                        # commit or reject that END command.  The next loop
                        # replays the same fact if END did not become durable.
                        logger.info(
                            "clean unlock decision deferred to pending END: "
                            "boot=%d seq=%d",
                            event["mcu_boot_id"],
                            event["mcu_event_sequence"],
                        )
                        break
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

    def _clock_health_loop(self):
        logger.info("clock health monitor started")
        while not self._exit_flag.wait(60.0):
            try:
                self._poll_clock_health()
            except Exception as error:
                logger.error(
                    "clock health monitor error: %s",
                    type(error).__name__,
                )
        logger.info("clock health monitor stopped")

    def _poll_clock_health(self):
        outcome = self.clock_monitor.poll()
        repair_state = str(outcome["repair_state"])
        previous_repair_state = self.store.get_state(
            "clock_repair_state"
        )
        self.store.set_state("clock_repair_state", repair_state)
        if outcome["warning_due"]:
            self.store.observe_fault_and_create_event(
                device_name=DEVICE_NAME,
                component="CLOCK",
                fault_code="CLOCK_UNSYNCED",
                severity="WARNING",
                detail={
                    "clockQuality": outcome["sample"].quality,
                    "repairState": repair_state,
                },
            )
            logger.warning(
                "clock remained unsynchronised for five minutes: %s",
                repair_state,
            )
            self._request_runtime_snapshot()
        if outcome["recovered"]:
            fault = self.store.get_active_edge_fault(
                "CLOCK", "CLOCK_UNSYNCED"
            )
            if fault is not None:
                self.store.recover_fault_and_create_event(
                    device_name=DEVICE_NAME,
                    fault_uid=fault["fault_uid"],
                    component="CLOCK",
                    fault_code="CLOCK_UNSYNCED",
                    port_no=fault["port_no"],
                    recovery_evidence="NTP_SYNCHRONIZED",
                )
            logger.info("clock synchronization recovered")
            self._request_runtime_snapshot()
        elif previous_repair_state != repair_state:
            self._request_runtime_snapshot()
        return outcome
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
                if not self.cloud_transport.connected:
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
                self.cloud_transport,
                self.device_identity,
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
                    None if compatibility_mode else 2 if getattr(self, "_native_mode", False) else 1
                ),
                "uart_protocol_minor": (
                    None if compatibility_mode else 0
                ),
                "fullness_sensor_kind": (
                    "DIGITAL_INFRARED"
                    if compatibility_mode
                    else "ULTRASONIC"
                ),
                "uart_state": (self.uart.uart_state if getattr(self, "_native_mode", False)
                    else runtime_uart_state(self.store, self.uart)),
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

    def _software_runtime_facts(self):
        """Expose actual read-only MCU/UART facts to the permanent updater."""

        compatibility_mode = bool(
            getattr(self.uart, "compatibility_mode", False)
        )
        raw_capability = (
            0
            if compatibility_mode
            else (getattr(self.uart, "_mcu_capability", None) or 0)
        )
        if (
            isinstance(raw_capability, bool)
            or not isinstance(raw_capability, int)
            or not 0 <= raw_capability <= 0xFFFFFFFFFFFFFFFF
        ):
            raw_capability = 0
        raw_identity = getattr(
            self.uart,
            "verified_firmware_identity",
            None,
        )
        mcu_firmware = None
        if isinstance(raw_identity, dict):
            version = raw_identity.get("firmwareVersion")
            version_code = raw_identity.get("firmwareVersionCode")
            identity_hex = raw_identity.get("firmwareIdentityHex")
            revision = raw_identity.get("fixedFrameRevision")
            if (
                isinstance(version, str)
                and 1 <= len(version) <= 32
                and isinstance(version_code, int)
                and not isinstance(version_code, bool)
                and 1 <= version_code <= 0xFFFFFFFF
                and isinstance(identity_hex, str)
                and len(identity_hex) == 16
                and all(
                    character in "0123456789abcdef"
                    for character in identity_hex
                )
                and isinstance(revision, int)
                and not isinstance(revision, bool)
                and 1 <= revision <= 255
            ):
                mcu_firmware = {
                    "versionName": version,
                    "versionCode": version_code,
                    "identityHex": identity_hex,
                    "fixedFrameRevision": revision,
                }
        return {
            "mcuFirmware": mcu_firmware,
            "uartState": (self.uart.uart_state if getattr(self, "_native_mode", False)
                else runtime_uart_state(self.store, self.uart)),
            "uartProtocol": (
                None
                if compatibility_mode
                else {"major": 2 if getattr(self, "_native_mode", False) else 1, "minor": 0}
            ),
            "capabilityBitmapHex": f"{raw_capability:016x}",
        }

    def _shutdown(self):
        logger.info("shutting down...")
        self._runtime_ready = False
        self._report_factory_progress(service_state="STOPPING")
        self._exit_flag.set()
        if self.business_control is not None:
            try:
                self.business_control.stop()
            except Exception:
                logger.exception(
                    "business local control service did not stop cleanly"
                )
        try:
            self.uart.close()
        except Exception:
            pass
        try:
            self.business_outbox.stop()
        except Exception:
            pass
        try:
            self.cloud_transport.disconnect()
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
        self._report_factory_progress(
            service_state="STOPPED",
            uart_state="DISCONNECTED",
            mqtt_state="DISCONNECTED",
        )
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
    if MCU_PROTOCOL_MODE == "uart-v2":
        raise ValueError("native UART requires the foreground business owner, not the v1 link")
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


def run_gateway(argv=None, *, legacy_factory=None, candidate_runner=None):
    """Choose the recovery candidate explicitly, before building any gateway."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--native-recovery-candidate":
        if candidate_runner is None:
            from native_recovery_entry import main as candidate_runner
        return candidate_runner(args[1:])
    if args:
        import argparse
        argparse.ArgumentParser(prog="main.py", allow_abbrev=False).error("unknown gateway arguments")
    gateway = (legacy_factory or EcoBinEdge)()
    return gateway.run()


if __name__ == "__main__":
    run_gateway()
