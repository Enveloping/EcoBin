"""持久 OneNet 命令消费者：把 MQTT 受理与 MCU 物理执行隔离开。"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional

from onenet_wire import validate_command_envelope
from uart_link import compute_mcu_payload_sha256

logger = logging.getLogger("command-processor")


class CommandProcessor:
    """从 command_inbox 消费命令，避免在 MQTT 网络回调中直接执行物理动作。"""

    def __init__(
        self,
        store,
        uart_link,
        work_manager=None,
        *,
        acceptance_runner=None,
        trusted_cos_environment=None,
        remote_support_controller=None,
        mcu_firmware_updater=None,
    ):
        self._store = store
        self._uart = uart_link
        self._work = work_manager
        self._acceptance = acceptance_runner
        self._trusted_cos_environment = trusted_cos_environment
        self._remote_support = remote_support_controller
        self._mcu_firmware_updater = mcu_firmware_updater
        self._wake_event = threading.Event()
        self._grant_lock = threading.Lock()
        self._volatile_cos_grants: dict[str, dict] = {}

    def offer_cos_grant(
        self,
        command_uid: str,
        grant: Optional[dict],
    ) -> bool:
        """Retain a temporary STS grant only until command execution."""
        if not isinstance(grant, dict):
            return False
        row = self._store.get_command(command_uid)
        if not row:
            return False
        if row["state"] == "COMPLETED":
            if row["command_type"] != "PROVIDE_PHOTO_UPLOAD_GRANT":
                return False
            if not self._store.requeue_completed_photo_grant_command(
                command_uid
            ):
                return False
            row = self._store.get_command(command_uid)
        if row["state"] == "FAILED":
            if row["command_type"] == "START_MCU_FIRMWARE_UPDATE":
                if not self._store.requeue_failed_mcu_firmware_command(
                    command_uid
                ):
                    return False
            else:
                expected_error = (
                    "ACCEPTANCE_GRANT_NOT_AVAILABLE"
                    if row["command_type"]
                    == "REQUEST_DEVICE_ACCEPTANCE"
                    else "PHOTO_GRANT_NOT_AVAILABLE"
                )
                if not self._store.requeue_failed_command(
                    command_uid,
                    expected_error,
                ):
                    return False
        with self._grant_lock:
            self._volatile_cos_grants[command_uid] = {
                **grant,
                "sessionTokenParts": list(
                    grant.get("sessionTokenParts") or []
                ),
            }
        self.wake()
        return True

    def wake(self) -> None:
        self._wake_event.set()

    def accept_photo_upload_grant_now(
        self,
        command: dict,
    ) -> bool:
        """Install execution-only STS data before the service reply."""
        if self._work is None:
            return False
        existing = self._store.get_command(command["commandUid"])
        already_completed = bool(
            existing and existing["state"] == "COMPLETED"
        )
        try:
            result = self._work.accept_photo_upload_grant(command)
            self._store.complete_command(
                command["commandUid"],
                result,
            )
            return True
        except Exception as error:
            if (
                already_completed
                and "PHOTO GRANT REQUEST IS NOT PENDING"
                in str(error).upper()
            ):
                return True
            self._store.fail_command(
                command["commandUid"],
                _error_code(error),
            )
            logger.warning(
                "photo upload grant rejected before reply: %s",
                type(error).__name__,
            )
            return False

    def wait(self, timeout_s: float = 0.5) -> None:
        self._wake_event.wait(timeout_s)
        self._wake_event.clear()

    def process_next(self) -> bool:
        # claim_next_command 先把一条 SQLite 记录置为处理中。进程重启后的恢复逻辑
        # 依据持久状态判断，不依赖 MQTT 回调栈或内存队列是否还存在。
        row = self._store.claim_next_command()
        if not row:
            return False
        command = row["payload"]
        command_uid = row["command_uid"]
        validated = False
        try:
            with self._grant_lock:
                grant = self._volatile_cos_grants.pop(
                    command_uid,
                    None,
                )
            if grant is not None:
                command = {**command, "cosGrant": grant}
            if (
                command.get("commandType")
                in {
                    "PROVIDE_PHOTO_UPLOAD_GRANT",
                    "REQUEST_DEVICE_ACCEPTANCE",
                    "START_MCU_FIRMWARE_UPDATE",
                }
                and not command.get("cosGrant")
            ):
                if (
                    command.get("commandType")
                    == "REQUEST_DEVICE_ACCEPTANCE"
                ):
                    raise ValueError("acceptance grant not available")
                if (
                    command.get("commandType")
                    == "START_MCU_FIRMWARE_UPDATE"
                ):
                    raise ValueError("firmware grant not available")
                raise ValueError("photo grant not available")
            validate_command_envelope(
                command,
                trusted_environment=self._trusted_cos_environment,
            )
            validated = True
            # 校验成功后才按命令类型进入 WorkManager；现场安全、满溢、配置和本地
            # 单作业槽等“此刻事实”由 WorkManager 在写串口前再次判断。
            if command["commandType"] == "APPLY_CONFIGURATION":
                self._apply_configuration(command)
            elif command["commandType"] == "START_DELIVERY_SESSION":
                self._start_delivery_session(command)
            elif command["commandType"] == "START_CLEAN_OPERATION":
                self._start_clean_operation(command)
            elif command["commandType"] == "SAMPLE_FULLNESS":
                self._sample_fullness(command)
            elif command["commandType"] == "MEASURE_EMPTY_BAG_BASELINE":
                self._measure_empty_bag_baseline(command)
            elif command["commandType"] == "END_CLEAN_BEFORE_UNLOCK":
                self._end_clean_before_unlock(command)
            elif command["commandType"] == "RESUME_CLEAN_OPERATION":
                self._resume_clean_operation(command)
            elif (
                command["commandType"]
                == "PROVIDE_PHOTO_UPLOAD_GRANT"
            ):
                self._provide_photo_upload_grant(command)
            elif (
                command["commandType"]
                == "REQUEST_DEVICE_ACCEPTANCE"
            ):
                self._request_device_acceptance(command)
            elif command["commandType"] == "SYNC_DEVICE_ENTRY_URL":
                self._sync_device_entry_url(command)
            elif command["commandType"] == "OPEN_REMOTE_SUPPORT_TUNNEL":
                self._open_remote_support_tunnel(command)
            elif command["commandType"] == "CLOSE_REMOTE_SUPPORT_TUNNEL":
                self._close_remote_support_tunnel(command)
            elif command["commandType"] == "START_MCU_FIRMWARE_UPDATE":
                self._start_mcu_firmware_update(command)
            else:
                self._store.fail_command(command_uid, "COMMAND_NOT_IMPLEMENTED")
                logger.warning(
                    "command type not implemented yet: %s", command["commandType"]
                )
        except Exception as error:
            error_code = _error_code(error)
            if (
                validated
                and command.get("commandType") in {
                    "START_DELIVERY_SESSION",
                    "START_CLEAN_OPERATION",
                    "END_CLEAN_BEFORE_UNLOCK",
                    "RESUME_CLEAN_OPERATION",
                    "SAMPLE_FULLNESS",
                    "MEASURE_EMPTY_BAG_BASELINE",
                }
            ):
                self._store.fail_command_and_observe(
                    command,
                    error_code,
                    stage="FAILED",
                )
            elif command.get("commandType") == "START_MCU_FIRMWARE_UPDATE":
                # Package-acquisition failure atomically changes this command
                # before exposing its retry fact. Do not overwrite a PENDING
                # state if fresh credentials raced with this exception path.
                current = self._store.get_command(command_uid)
                if current and current["state"] == "PROCESSING":
                    self._store.fail_command(command_uid, error_code)
            else:
                self._store.fail_command(command_uid, error_code)
            logger.error("command %s failed: %s", command_uid, error)
        return True

    def _provide_photo_upload_grant(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.accept_photo_upload_grant(command)
        self._store.complete_command(command["commandUid"], result)

    def _request_device_acceptance(self, command: dict) -> None:
        if self._acceptance is None:
            raise RuntimeError("device acceptance runner is required")
        self._persist_and_dispatch_device_entry_url(command)
        self._acceptance.run(command)

    def _sync_device_entry_url(self, command: dict) -> None:
        record = self._persist_and_dispatch_device_entry_url(command)
        self._store.complete_command(
            command["commandUid"],
            {
                "deviceEntryUrlSha256": record[
                    "deviceEntryUrlSha256"
                ],
                "disposition": record["disposition"],
            },
        )

    def _open_remote_support_tunnel(self, command: dict) -> None:
        if self._remote_support is None:
            raise RuntimeError("remote support controller is required")
        payload = command["payload"]
        disposition = self._remote_support.open_session(
            session_uid=payload["sessionUid"],
            command_uid=command["commandUid"],
            device_name=command["targetDeviceName"],
            remote_port=payload["remotePort"],
            expires_at=payload["expiresAt"],
        )
        self._store.complete_command(
            command["commandUid"],
            {
                "sessionUid": payload["sessionUid"],
                "disposition": disposition,
            },
        )

    def _close_remote_support_tunnel(self, command: dict) -> None:
        if self._remote_support is None:
            raise RuntimeError("remote support controller is required")
        session_uid = command["payload"]["sessionUid"]
        disposition = self._remote_support.close_session(
            session_uid=session_uid,
            command_uid=command["commandUid"],
        )
        self._store.complete_command(
            command["commandUid"],
            {
                "sessionUid": session_uid,
                "disposition": disposition,
            },
        )

    def _start_mcu_firmware_update(self, command: dict) -> None:
        if self._mcu_firmware_updater is None:
            raise RuntimeError("MCU firmware updater is not enabled")
        payload = command["payload"]
        queued = self._mcu_firmware_updater.queue_cloud(
            deployment_uid=payload["deploymentUid"],
            command_uid=command["commandUid"],
            object_key=payload["objectKey"],
            package_sha256=payload["packageSha256"],
            package_size=payload["packageSize"],
            cos_grant=command["cosGrant"],
            release_uid=payload["releaseUid"],
            firmware_version=payload["firmwareVersion"],
            firmware_version_code=payload["firmwareVersionCode"],
            firmware_identity_hex=payload["firmwareIdentityHex"],
            requested_reason=payload.get("reason"),
        )
        manifest = queued["manifest"]
        self._store.complete_command(
            command["commandUid"],
            {
                "deploymentUid": queued["deploymentUid"],
                "updateUid": queued["updateUid"],
                "disposition": queued["disposition"],
                "state": queued["state"],
                "firmwareVersion": manifest["firmwareVersion"],
                "firmwareVersionCode": manifest["firmwareVersionCode"],
                "firmwareIdentityHex": manifest["firmwareIdentityHex"],
            },
        )

    def _persist_and_dispatch_device_entry_url(
        self,
        command: dict,
    ) -> dict:
        payload = command["payload"]
        record = self._store.save_device_entry_url(
            payload["deviceEntryUrl"],
            payload["deviceEntryUrlSha256"],
            command["issuedAt"],
        )
        active = self._store.get_device_entry_url()
        if active is None:
            raise RuntimeError("device entry URL was not persisted")
        sender = getattr(self._uart, "send_device_entry_url", None)
        if callable(sender):
            try:
                sender(active["deviceEntryUrl"])
            except Exception as error:
                logger.warning(
                    "device entry URL saved but MCU dispatch failed: %s",
                    error,
                )
        else:
            logger.warning(
                "device entry URL saved; current UART adapter cannot dispatch it"
            )
        return {**active, "disposition": record["disposition"]}

    def _start_delivery_session(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_delivery_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _start_clean_operation(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_clean_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _sample_fullness(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_fullness_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        if result.get("completed_locally"):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _measure_empty_bag_baseline(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_baseline_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        if result.get("completed_locally"):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _end_clean_before_unlock(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.end_clean_before_unlock_command(command)
        self._accept_dispatch_result(command, result)

    def _resume_clean_operation(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.resume_clean_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        if not result.get("already_recovered"):
            self._store.mark_command_waiting_mcu(
                command["commandUid"],
                result["mcu_command_uid"],
                result,
            )

    def _accept_dispatch_result(self, command: dict, result: dict) -> bool:
        if result.get("acked"):
            return True
        error = _symbol(str(result.get("error") or "UART_FAILURE"))
        if error == "TIMEOUT":
            self._store.mark_command_recovery_required(
                command["commandUid"],
                "UART_ACK_RESULT_UNKNOWN",
                result.get("mcu_command_uid"),
                result,
            )
        else:
            self._store.fail_command(command["commandUid"], error)
        return False

    def _apply_configuration(self, command: dict) -> None:
        payload = command["payload"]
        config = payload["config"]
        application_uid = payload["applicationUid"]
        actual_mcu_sha = compute_mcu_payload_sha256(payload)
        if actual_mcu_sha != config["mcuPayloadSha256"]:
            raise ValueError("mcuPayloadSha256 mismatch")

        existing = self._store.get_configuration(application_uid)
        if existing:
            part_uids = existing["part_command_uids"]
        else:
            part_uids = [
                str(uuid.uuid4()) for _ in range(len(payload["ports"]) + 3)
            ]
        saved = self._store.save_configuration_edge(command, part_uids)
        if saved == "OUTDATED":
            raise ValueError("configuration version is older than local version")
        if saved == "CONFLICT":
            raise ValueError("configuration identity conflict")

        if getattr(self._uart, "compatibility_mode", False):
            commit_uid = part_uids[-1]
            applied = self._store.apply_configuration_result({
                "mcuCommandUid": commit_uid,
                "applicationUid": application_uid,
                "status": "APPLIED",
                "configVersion": config["version"],
                "contentSha256": config["contentSha256"],
                "mcuPayloadSha256": config["mcuPayloadSha256"],
                "faultCode": "NONE",
            })
            if applied not in ("ACCEPTED", "DUPLICATE"):
                raise ValueError(
                    f"local configuration result {applied.lower()}"
                )
            self._store.complete_command(
                command["commandUid"],
                {
                    "applicationUid": application_uid,
                    "configVersion": config["version"],
                    "disposition": (
                        "APPLIED"
                        if applied == "ACCEPTED"
                        else "DUPLICATE_APPLIED"
                    ),
                },
            )
            self._store.set_state(
                "mcu_configuration_projection",
                "NOT_SUPPORTED",
            )
            logger.info(
                "configuration applied locally without MCU projection: "
                "app=%s version=%d",
                application_uid,
                config["version"],
            )
            return

        result = self._uart.apply_configuration(command, part_uids)
        if not result["acked"]:
            error_code = str(result.get("error") or "UART_FAILURE")
            failed_part = _last_part_uid(result)
            if error_code == "TIMEOUT":
                self._store.mark_configuration_recovery_required(
                    application_uid, "UART_RESULT_UNKNOWN"
                )
            else:
                self._store.fail_configuration_edge(
                    application_uid,
                    _symbol(error_code),
                    failed_part,
                )
            return

        commit_uid = result["commit_mcu_command_uid"]
        self._store.mark_configuration_waiting_mcu(application_uid, commit_uid)
        self._store.mark_command_waiting_mcu(
            command["commandUid"], commit_uid, result
        )
        logger.info(
            "configuration staged at MCU, waiting result: app=%s version=%d",
            application_uid,
            config["version"],
        )

    def process_mcu_event(self, event: dict) -> None:
        message_name = event["message_name"]
        payload = event["payload"]
        if message_name == "CONFIG_APPLY_RESULT":
            result = self._store.apply_configuration_result(payload)
            if result not in ("ACCEPTED", "DUPLICATE"):
                raise ValueError(f"configuration result {result.lower()}")
        elif self._work is not None:
            self._work.handle_mcu_event({
                "message_name": message_name,
                "message_type": event["message_type"],
                "tx_sequence": event["source_tx_sequence"],
                "mcu_receive_generation": event.get(
                    "mcu_receive_generation",
                    0,
                ),
                "payload": payload,
            })


def _last_part_uid(result: dict) -> Optional[str]:
    parts = result.get("parts") or []
    if not parts:
        return None
    return parts[-1].get("mcu_command_uid")


def _error_code(error: Exception) -> str:
    stable_code = getattr(error, "code", None)
    if stable_code:
        return _symbol(str(stable_code))
    message = str(error).upper()
    if "ACCEPTANCE GRANT NOT AVAILABLE" in message:
        return "ACCEPTANCE_GRANT_NOT_AVAILABLE"
    if "PHOTO GRANT NOT AVAILABLE" in message:
        return "PHOTO_GRANT_NOT_AVAILABLE"
    if "FIRMWARE GRANT NOT AVAILABLE" in message:
        return "FIRMWARE_GRANT_NOT_AVAILABLE"
    if "EXPIRED" in message:
        return "COMMAND_EXPIRED"
    if "MCUPAYLOADSHA256" in message:
        return "MCU_PAYLOAD_DIGEST_MISMATCH"
    if "PAYLOADSHA256" in message:
        return "PAYLOAD_DIGEST_MISMATCH"
    if "UUID" in message:
        return "INVALID_COMMAND_IDENTITY"
    if "OLDER" in message:
        return "CONFIG_VERSION_OUTDATED"
    if "CONFLICT" in message:
        return "IDEMPOTENCY_CONFLICT"
    return "INVALID_COMMAND"


def _symbol(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "_" for character in value.upper()
    ).strip("_")
    return (normalized or "UART_FAILURE")[:64]
