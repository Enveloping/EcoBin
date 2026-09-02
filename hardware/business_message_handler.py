"""Business admission for commands delivered by a cloud transport.

This module owns the business meaning of OneNet services.  It deliberately
does not know MQTT topics, Paho message identifiers, credentials, connection
state, or how a service reply is transported.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from cloud_transport import CloudServiceRequest, CloudServiceResponse
from device_identity import DeviceIdentity
from factory_seal.admission import FACTORY_NOT_SEALED
from onenet_wire import (
    decode_service_command,
    encode_command_receipt,
    validate_command_envelope,
)


logger = logging.getLogger("business-message-handler")


class BusinessMessageHandler:
    """Validate and durably admit business commands before acknowledging them."""

    def __init__(
        self,
        store,
        device_identity: DeviceIdentity,
        *,
        edge_boot_id: int,
        trusted_cos_environment=None,
        unsupported_command_types=None,
        factory_seal_gate=None,
    ) -> None:
        self._store = store
        self._device_identity = device_identity
        self._edge_boot_id = edge_boot_id
        self._trusted_cos_environment = trusted_cos_environment
        self._unsupported_command_types = frozenset(
            unsupported_command_types or ()
        )
        self._factory_seal_gate = factory_seal_gate

        self.on_command_received: Optional[Callable] = None
        self.on_reliable_event_count_changed: Optional[Callable] = None
        self.on_outbox_wakeup: Optional[Callable] = None

    def handle_legacy_command(self, payload: dict) -> None:
        """Persist a legacy OneNet command before waking its consumer."""

        command_uid = payload.get("id", str(int(time.time() * 1000)))
        command_type = payload.get("params", {}).get(
            "commandType",
            "unknown",
        )
        result = self._store.receive_command(
            command_uid,
            command_type,
            payload,
        )
        if result == "ACCEPTED" and self.on_command_received is not None:
            self.on_command_received(command_uid, command_type, payload)

    def handle_service_request(
        self,
        request: CloudServiceRequest,
    ) -> CloudServiceResponse:
        """Return reply data only after business validation and persistence."""

        service_id = request.service_id
        request_id = request.request_id
        params = request.params
        command_uid = ""
        try:
            if not isinstance(params, dict):
                raise ValueError("service params must be an object")
            command = decode_service_command(service_id, params)
            command_uid = command["commandUid"]
            target_device_name = command.get("targetDeviceName")
            if (
                target_device_name
                and target_device_name
                != self._device_identity.device_name
            ):
                raise ValueError("targetDeviceName mismatch")

            is_factory_seal = (
                command.get("commandType")
                == "AUTHORIZE_FACTORY_SEAL"
            )
            if (
                not is_factory_seal
                or command["commandType"]
                in self._unsupported_command_types
            ):
                validate_command_envelope(
                    command,
                    trusted_environment=(
                        self._trusted_cos_environment
                    ),
                )

            rejection_error = None
            if self._production_command_blocked(
                command["commandType"]
            ):
                rejection_error = FACTORY_NOT_SEALED
                result = self._store.receive_rejected_command(
                    command,
                    rejection_error,
                )
            elif (
                command["commandType"]
                in self._unsupported_command_types
            ):
                rejection_error = "MCU_FEATURE_NOT_SUPPORTED"
                result = self._store.receive_rejected_command(
                    command,
                    rejection_error,
                )
            elif command["commandType"] == "CONFIRM_EDGE_EVENT":
                result = (
                    self._store
                    .receive_business_confirmation_and_create_receipt(
                        device_name=(
                            self._device_identity.device_name
                        ),
                        command=command,
                    )
                )
            elif is_factory_seal:
                # The store selects the trusted receipt instant in the same
                # transaction.  An exact duplicate may reuse that fact after
                # expiresAt; a new expired delivery is rejected before an
                # inbox row exists.
                result = self._store.receive_factory_seal_command(
                    command
                )
            else:
                # Durable command identity, digest and payload must exist
                # before the transport is allowed to reply "accepted".
                result = self._store.receive_command(
                    command_uid,
                    command["commandType"],
                    command,
                )

            factory_seal_requeued = False
            if (
                result == "DUPLICATE"
                and command["commandType"]
                == "AUTHORIZE_FACTORY_SEAL"
            ):
                factory_seal_requeued = (
                    self._store
                    .requeue_failed_factory_seal_command(command_uid)
                )

            clean_end_requeued = False
            if (
                result == "DUPLICATE"
                and command["commandType"]
                == "END_CLEAN_BEFORE_UNLOCK"
            ):
                clean_end_requeued = (
                    self._store
                    .requeue_unknown_end_clean_before_unlock(command)
                )

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

            receipt_state = (
                "DUPLICATE_ACCEPTED"
                if result == "DUPLICATE"
                else result
            )
            if receipt_state not in (
                "ACCEPTED",
                "DUPLICATE_ACCEPTED",
            ):
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

            should_dispatch = result == "ACCEPTED" or (
                result == "DUPLICATE"
                and command["commandType"]
                in {
                    "PROVIDE_PHOTO_UPLOAD_GRANT",
                    "REQUEST_DEVICE_ACCEPTANCE",
                    "START_MCU_FIRMWARE_UPDATE",
                    "AUTHORIZE_FACTORY_SEAL",
                    "END_CLEAN_BEFORE_UNLOCK",
                }
                and (
                    command["commandType"]
                    != "AUTHORIZE_FACTORY_SEAL"
                    or factory_seal_requeued
                )
                and (
                    command["commandType"]
                    != "END_CLEAN_BEFORE_UNLOCK"
                    or clean_end_requeued
                )
            )

            def after_reply() -> None:
                if (
                    should_dispatch
                    and command["commandType"]
                    != "CONFIRM_EDGE_EVENT"
                    and not control_dispatched
                    and self.on_command_received is not None
                ):
                    # Normal commands only wake the durable inbox consumer
                    # after OneNet has been sent the admission receipt.
                    self.on_command_received(
                        command_uid,
                        command["commandType"],
                        command,
                    )
                if (
                    result in ("ACCEPTED", "DUPLICATE")
                    and command["commandType"]
                    == "CONFIRM_EDGE_EVENT"
                ):
                    if result == "ACCEPTED":
                        self._notify_reliable_event_count_changed()
                    self._wake_outbox()

            return CloudServiceResponse(
                data=encode_command_receipt(
                    command_uid,
                    receipt_state,
                    self._edge_boot_id,
                    error_code,
                ),
                after_reply=after_reply,
            )
        except Exception as error:
            logger.error("服务调用拒绝: %s", error)
            fallback_uid = (
                command_uid
                or (
                    request_id
                    if isinstance(request_id, str)
                    else ""
                )
                or str(int(time.time() * 1000))
            )
            return CloudServiceResponse(
                data=encode_command_receipt(
                    fallback_uid,
                    "REJECTED",
                    self._edge_boot_id,
                    "BAD_COMMAND",
                )
            )

    def _production_command_blocked(
        self,
        command_type: object,
    ) -> bool:
        gate = self._factory_seal_gate
        if gate is None:
            return False
        try:
            gate.require_command_allowed(command_type)
        except Exception as error:
            if getattr(error, "code", None) == FACTORY_NOT_SEALED:
                return True
            # An unreadable or otherwise uncertain local seal fact may never
            # be interpreted as permission to execute production work.
            logger.exception(
                "factory seal command admission failed closed"
            )
            return True
        return False

    def _notify_reliable_event_count_changed(self) -> None:
        callback = self.on_reliable_event_count_changed
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.exception("可靠事件待确认数量变化通知失败")

    def _wake_outbox(self) -> None:
        callback = self.on_outbox_wakeup
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.exception("业务确认回执中继唤醒失败")
