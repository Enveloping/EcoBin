"""Failure-state recovery for the legacy fixed-frame MCU sensor query."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence

logger = logging.getLogger("fixed-frame-recovery")

SELF_TEST_TIMEOUT_MS = 3_000
DEFAULT_RETRY_DELAYS_SECONDS = (5.0, 10.0, 20.0, 40.0, 60.0)
UART_COMPONENT = "UART"
UART_FAULT_CODE = "UART_PROTOCOL"
RECOVERY_EVIDENCE = "FIXED_FRAME_SELF_TEST_RETRY_SUCCEEDED"


def _stored_self_test(store) -> dict | None:
    try:
        value = json.loads(
            store.get_state("fixed_frame_latest_self_test_json", "")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def fixed_frame_health_is_complete(store) -> bool:
    """Return whether F1 plus the latest smoke state prove usable sensors."""
    result = _stored_self_test(store)
    if result is None:
        return False
    weight = result.get("weightGrams")
    infrared = result.get("infraredBlocked")
    f1_complete = bool(
        result.get("queryStatus") == "OK"
        and result.get("communicationHealthy") is True
        and result.get("portNo") == 1
        and result.get("validFlags") == 3
        and result.get("weightValid") is True
        and isinstance(weight, int)
        and not isinstance(weight, bool)
        and 0 <= weight <= 350_000
        and result.get("infraredValid") is True
        and isinstance(infrared, bool)
    )
    if not f1_complete:
        return False
    smoke_state = store.get_state(
        "port_1_smoke_state",
        store.get_state("smoke_state", "UNKNOWN"),
    )
    smoke_health = store.get_state(
        "port_1_smoke_sensor_health",
        store.get_state("smoke_sensor_health", "UNKNOWN"),
    )
    return smoke_health == "OK" and smoke_state in {"NORMAL", "ALARM"}


def runtime_uart_state(store, uart_link) -> str:
    """Project the physical link and reliable UART fault into one state."""
    if not getattr(uart_link, "is_open", False):
        return "DISCONNECTED"
    if store.get_active_edge_fault(UART_COMPONENT, UART_FAULT_CODE):
        return "FAULT"
    return "READY"


class FixedFrameHealthRecoveryController:
    """Retry F0 only while fixed-frame health evidence is incomplete.

    ``poll`` is intended to run in the existing command-consumer thread.  It
    therefore never dispatches a physical query concurrently with a command,
    and it refuses to query while the durable work slot is occupied.
    """

    def __init__(
        self,
        store,
        uart_link,
        *,
        device_name: str,
        monotonic: Callable[[], float] = time.monotonic,
        retry_delays_seconds: Sequence[float] = (
            DEFAULT_RETRY_DELAYS_SECONDS
        ),
    ):
        delays = tuple(float(delay) for delay in retry_delays_seconds)
        if not delays or any(delay <= 0 for delay in delays):
            raise ValueError("fixed-frame retry delays must be positive")
        self._store = store
        self._uart = uart_link
        self._device_name = device_name
        self._monotonic = monotonic
        self._delays = delays
        self._delay_index = 0
        self._next_retry_at: float | None = None

    @property
    def next_retry_at(self) -> float | None:
        return self._next_retry_at

    def poll(self) -> dict:
        if not getattr(self._uart, "compatibility_mode", False):
            self._reset_schedule()
            return self._outcome("DISABLED")

        if (
            getattr(self._uart, "is_open", False)
            and fixed_frame_health_is_complete(self._store)
            and self._store.get_active_edge_fault(
                UART_COMPONENT,
                UART_FAULT_CODE,
            ) is None
        ):
            self._reset_schedule()
            return self._outcome("HEALTHY")

        now = self._monotonic()
        if self._next_retry_at is None:
            self._delay_index = 0
            self._next_retry_at = now + self._delays[0]
            logger.info(
                "fixed-frame health incomplete; first F0 retry in %.0fs",
                self._delays[0],
            )
            return self._outcome("ARMED")
        if now < self._next_retry_at:
            return self._outcome("WAITING")
        if self._store.get_work_slot() is not None:
            self._next_retry_at = now + self._delays[0]
            logger.info(
                "fixed-frame F0 retry deferred %.0fs because work is active",
                self._delays[0],
            )
            return self._outcome("BUSY")

        projection_changed = False

        def persist(result: dict) -> bool:
            nonlocal projection_changed
            projection_changed = bool(
                self._store.save_fixed_frame_self_test(result)
            )
            return projection_changed

        logger.info("retrying fixed-frame F0/F1 sensor self-test")
        try:
            result = self._uart.query_self_test(
                timeout_ms=SELF_TEST_TIMEOUT_MS,
                on_result=persist,
                queue_unchanged_safety_event=False,
            )
        except Exception:
            logger.exception("fixed-frame F0/F1 retry failed")
            fault_disposition = self._observe_uart_fault("ADAPTER_ERROR")
            self._schedule_after_failed_attempt(self._monotonic())
            return self._outcome(
                "RETRY_SCHEDULED",
                attempted=True,
                state_changed=fault_disposition == "ACCEPTED",
            )

        communication_healthy = result.get("communicationHealthy") is True
        if communication_healthy:
            fault_disposition = self._recover_uart_fault()
        else:
            fault_disposition = self._observe_uart_fault(
                str(result.get("queryStatus") or "SELF_TEST_FAILED")
            )

        state_changed = bool(
            projection_changed or fault_disposition == "ACCEPTED"
        )
        if fixed_frame_health_is_complete(self._store):
            self._reset_schedule()
            logger.info(
                "fixed-frame F0/F1 retry restored complete sensor health"
            )
            return self._outcome(
                "HEALTHY",
                attempted=True,
                state_changed=state_changed,
            )

        retry_completed_at = self._monotonic()
        self._schedule_after_failed_attempt(retry_completed_at)
        logger.warning(
            "fixed-frame F0/F1 retry still incomplete: query=%s "
            "communication=%s next_retry_in=%.0fs",
            result.get("queryStatus"),
            communication_healthy,
            self._next_retry_at - retry_completed_at,
        )
        return self._outcome(
            "RETRY_SCHEDULED",
            attempted=True,
            state_changed=state_changed,
        )

    def _schedule_after_failed_attempt(self, now: float) -> None:
        self._delay_index = min(
            self._delay_index + 1,
            len(self._delays) - 1,
        )
        self._next_retry_at = now + self._delays[self._delay_index]

    def _reset_schedule(self) -> None:
        self._delay_index = 0
        self._next_retry_at = None

    def _observe_uart_fault(self, reason_code: str) -> str:
        return self._store.observe_fault_and_create_event(
            device_name=self._device_name,
            component=UART_COMPONENT,
            fault_code=UART_FAULT_CODE,
            severity="BLOCK_DEVICE",
            detail={"reasonCode": reason_code},
        )

    def _recover_uart_fault(self) -> str:
        fault = self._store.get_active_edge_fault(
            UART_COMPONENT,
            UART_FAULT_CODE,
        )
        if fault is None:
            return "UNKNOWN"
        return self._store.recover_fault_and_create_event(
            device_name=self._device_name,
            fault_uid=fault["fault_uid"],
            component=UART_COMPONENT,
            fault_code=UART_FAULT_CODE,
            port_no=fault["port_no"],
            recovery_evidence=RECOVERY_EVIDENCE,
        )

    @staticmethod
    def _outcome(
        status: str,
        *,
        attempted: bool = False,
        state_changed: bool = False,
    ) -> dict:
        return {
            "attempted": attempted,
            "state_changed": state_changed,
            "status": status,
        }
