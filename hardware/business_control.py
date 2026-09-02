"""Local control surface for the replaceable business runtime.

Stage three ships this adapter with the business release so its protocol can
be exercised under the future ``ecobin-business`` account.  The legacy
runtime does not enable it yet: OneNet still belongs to the existing business
process and the permanent job gate is intentionally deferred to stage four.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    LocalControlAction,
    LocalControlServer,
)


BUSINESS_PROTOCOL_NAME = "ecobin.business.control"
BUSINESS_COMPONENT = "BUSINESS_RUNTIME"


class BusinessControlController:
    """Truthful read-only status for the stage-three bridge boundary."""

    def __init__(
        self,
        release_version: str,
        *,
        utc_now: Callable[[], datetime] | None = None,
        instance_uid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self.release_version = _require_release_version(release_version)
        now = (utc_now or (lambda: datetime.now(timezone.utc)))()
        self.started_at = _format_utc(now)
        instance_uid = instance_uid_factory()
        if not isinstance(instance_uid, uuid.UUID) or instance_uid.version != 4:
            raise ValueError("business runtime instance identity must be UUIDv4")
        self.runtime_instance_uid = str(instance_uid)
        self._status = "STARTING"
        self._lock = threading.Lock()

    def mark_ready(self) -> None:
        with self._lock:
            if self._status == "STOPPING":
                raise RuntimeError("stopping business runtime cannot become ready")
            self._status = "READY"

    def mark_stopping(self) -> None:
        with self._lock:
            self._status = "STOPPING"

    def health(self, _payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            status = self._status
        return {
            "component": BUSINESS_COMPONENT,
            "status": status,
            "runtimeInstanceUid": self.runtime_instance_uid,
            "releaseVersion": self.release_version,
            "startedAt": self.started_at,
            "localProtocolName": BUSINESS_PROTOCOL_NAME,
            "localProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "localProtocolMinor": LOCAL_PROTOCOL_MINOR,
            # These values describe the real migration state.  Shipping the
            # socket adapter must not be confused with completing the OneNet
            # ownership cutover or enabling the stage-four safety gate.
            "managementArchitectureGeneration": "LEGACY_DIRECT",
            "cloudConnectionOwner": "BUSINESS_RUNTIME",
            "jobPermitEnforced": False,
            "maintenanceHandoffEnabled": False,
        }


class BusinessControlService:
    """Lifecycle wrapper used by the future non-root business process."""

    def __init__(
        self,
        controller: BusinessControlController,
        server: LocalControlServer,
    ) -> None:
        self.controller = controller
        self.server = server
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.server.start()
        self._started = True

    def mark_ready(self) -> None:
        if not self._started or not self.server.is_running:
            raise RuntimeError("business local control service is not running")
        self.controller.mark_ready()

    @property
    def is_running(self) -> bool:
        return self._started and self.server.is_running

    @property
    def failure(self) -> BaseException | None:
        return self.server.failure

    def stop(self) -> None:
        self.controller.mark_stopping()
        if self._started:
            self.server.stop()
            self._started = False


def build_business_control_service(
    socket_path: str | Path,
    *,
    release_version: str,
    allowed_uids: Iterable[int],
    socket_gid: int,
    utc_now: Callable[[], datetime] | None = None,
    instance_uid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> BusinessControlService:
    """Build the adapter without resolving accounts or weakening UID checks."""

    action_uids = frozenset(allowed_uids)
    controller = BusinessControlController(
        release_version,
        utc_now=utc_now,
        instance_uid_factory=instance_uid_factory,
    )
    actions = {
        "HEALTH": LocalControlAction(
            controller.health,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
        "GET_STATUS": LocalControlAction(
            controller.health,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
    }
    server = LocalControlServer(
        socket_path,
        protocol_name=BUSINESS_PROTOCOL_NAME,
        actions=actions,
        allowed_uids=action_uids,
        socket_mode=0o660,
        socket_gid=socket_gid,
    )
    return BusinessControlService(controller, server)


def _require_release_version(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError("business release version is invalid")
    return value


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("business runtime clock must be timezone-aware")
    rendered = value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")
